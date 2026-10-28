"""Locate, read and (minimally) modify relics inside one decrypted character slot.

Layout (from alfizari/Elden-Ring-Nightreign-Save-Editor, inventory_handler.py / vessel_handler.py):

  0x14                      5120 variable-size "item states"
                              empty/goods 8 B, armor 16 B, relic 80 B, weapon 88 B
  state_end + 0x94          player name (UTF-16LE, <=16 chars)
      name - 64             Sigs   (u32)
      name + 52             Murk   (u32)
  name + 0x5B8              inventory entry count (u32)
  +4                        3065 inventory entries, 14 B each:
                              ga_handle u32 | quantity u32 | acquisition id u32 | favorite u8 | is_new u8
  ...                       hero vessels and saved presets (found by a magic marker);
                            relics referenced there are EQUIPPED
  end - 0x1C                12 pad + 16 MD5 (owned by save_io)

Relic state (80 B): ga_handle | item_id (0x80000000|id) | durability | unk | effect1-3 (+16,+20,+24)
                    | 28 B filler | curse1-3 (+56,+60,+64) | unk | 8 B pad

Removing a relic zeroes its 14 B entry, shrinks its state from 80 B to the 8 B empty record and
re-inserts 72 zero bytes in front of the final 0x1C bytes, so the slot keeps its exact size.
Passive/curse IDs are never written by this module.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

TYPE_MASK = 0xF0000000
ITEM_TYPE_WEAPON = 0x80000000
ITEM_TYPE_ARMOR = 0x90000000
ITEM_TYPE_GOODS = 0xB0000000
ITEM_TYPE_RELIC = 0xC0000000
_STATE_SIZES = {ITEM_TYPE_WEAPON: 88, ITEM_TYPE_ARMOR: 16, ITEM_TYPE_RELIC: 80}

START_OFFSET = 0x14
STATE_SLOT_COUNT = 5120
NAME_GAP = 0x94
SIGS_FROM_NAME = -64
MURK_FROM_NAME = 52
ENTRY_COUNT_FROM_NAME = 0x5B8
ENTRY_SIZE = 14
ENTRY_SLOT_COUNT = 3065
TAIL_SIZE = 0x1C
RELIC_STATE_SIZE = 80
EMPTY_STATE_SIZE = 8
REMOVE_PAD = RELIC_STATE_SIZE - EMPTY_STATE_SIZE  # 72
EMPTY_STATE = bytes.fromhex("00000000FFFFFFFF")
EMPTY_EFFECT = (0, 0xFFFFFFFF)
U32_MAX = 0xFFFFFFFF

_EQUIP_MAGIC = re.compile(re.escape(bytes.fromhex("C2000300002C000003000A0004004600") + bytes.fromhex("64000000")))
_MAX_PRESETS = 100


class LayoutError(Exception):
    """Slot bytes do not look like the layout we know. Nothing should be written."""


class EquipmentError(Exception):
    """Could not determine which relics are equipped. SELL must be refused."""


@dataclass
class Relic:
    ga_handle: int
    item_id: int
    real_id: int
    state_index: int
    state_offset: int
    entry_index: int
    entry_offset: int
    effects: tuple[int, int, int]
    curses: tuple[int, int, int]
    acquisition_id: int
    favorite: bool
    is_new: bool
    equipped_in: set[str] = field(default_factory=set)


class Slot:
    """Parsed, read-only view of one slot payload. Re-create it after mutating the bytes."""

    def __init__(self, data: bytes | bytearray):
        self.data = data
        n = len(data)
        # -- item states
        states: list[tuple[int, int, int]] = []   # (offset, size, ga_handle)
        off = START_OFFSET
        for _ in range(STATE_SLOT_COUNT):
            if off + 8 > n:
                raise LayoutError("Item state table runs past the end of the slot.")
            handle = struct.unpack_from("<I", data, off)[0]
            size = _STATE_SIZES.get(handle & TYPE_MASK, 8) if handle else 8
            if off + size > n:
                raise LayoutError("Item state runs past the end of the slot.")
            states.append((off, size, handle))
            off += size
        self.states = states
        self.state_end = off

        # -- fixed offsets after the state table
        self.name_offset = off + NAME_GAP
        self.murk_offset = self.name_offset + MURK_FROM_NAME
        self.sigs_offset = self.name_offset + SIGS_FROM_NAME
        self.entry_count_offset = self.name_offset + ENTRY_COUNT_FROM_NAME
        self.entries_offset = self.entry_count_offset + 4
        entries_end = self.entries_offset + ENTRY_SLOT_COUNT * ENTRY_SIZE
        if entries_end > n - TAIL_SIZE:
            raise LayoutError("Inventory entry table does not fit in the slot.")

        self.name = self._read_name()
        self.murks = struct.unpack_from("<I", data, self.murk_offset)[0]
        self.stored_entry_count = struct.unpack_from("<I", data, self.entry_count_offset)[0]

        # -- entries + relic linking
        by_handle = {h: i for i, (_, _, h) in enumerate(states) if h}
        self.relics: list[Relic] = []
        self.orphan_relic_entries = 0
        counted = 0
        for i in range(ENTRY_SLOT_COUNT):
            eo = self.entries_offset + i * ENTRY_SIZE
            ga, _qty, acq = struct.unpack_from("<III", data, eo)
            if ga == 0:
                continue
            counted += 1
            if (ga & TYPE_MASK) != ITEM_TYPE_RELIC:
                continue
            si = by_handle.get(ga)
            if si is None or states[si][1] != RELIC_STATE_SIZE:
                self.orphan_relic_entries += 1
                continue
            so = states[si][0]
            item_id = struct.unpack_from("<I", data, so + 4)[0]
            self.relics.append(Relic(
                ga_handle=ga,
                item_id=item_id,
                real_id=item_id & 0x00FFFFFF,
                state_index=si,
                state_offset=so,
                entry_index=i,
                entry_offset=eo,
                effects=struct.unpack_from("<3I", data, so + 16),
                curses=struct.unpack_from("<3I", data, so + 56),
                acquisition_id=acq,
                favorite=bool(data[eo + 12]),
                is_new=bool(data[eo + 13]),
            ))
        self.counted_entries = counted
        self.entry_count_mismatch = counted != self.stored_entry_count

        # -- equipment (vessels + presets)
        self.equipment_error: str | None = None
        try:
            equipped = read_equipment(data)
        except EquipmentError as exc:
            equipped = {}
            self.equipment_error = str(exc)
        self.equipment_known = self.equipment_error is None
        for r in self.relics:
            r.equipped_in = set(equipped.get(r.ga_handle, ()))

    # -- helpers
    def _read_name(self) -> str:
        o = self.name_offset
        end = o + 32
        for cur in range(o, end, 2):
            if self.data[cur:cur + 2] == b"\x00\x00":
                end = cur
                break
        return bytes(self.data[o:end]).decode("utf-16-le", errors="ignore")

    @property
    def equipped_count(self) -> int:
        return sum(1 for r in self.relics if r.equipped_in)

    @property
    def favorite_count(self) -> int:
        return sum(1 for r in self.relics if r.favorite)

    def relic_by_handle(self, ga: int) -> Relic | None:
        return next((r for r in self.relics if r.ga_handle == ga), None)


# ---------------------------------------------------------------------------
# Equipment detection
# ---------------------------------------------------------------------------
def read_equipment(data: bytes | bytearray) -> dict[int, set[str]]:
    """Return {ga_handle: {"vessel", "preset"}} for every relic referenced by a hero vessel or preset.

    Raises EquipmentError when the block cannot be located or looks malformed; callers must then
    treat every relic as protected.
    """
    m = _EQUIP_MAGIC.search(bytes(data))
    if not m:
        raise EquipmentError("Vessel/preset block not found; cannot tell which relics are equipped.")
    out: dict[int, set[str]] = {}

    def add(ga: int, where: str) -> None:
        if ga and (ga & TYPE_MASK) == ITEM_TYPE_RELIC:
            out.setdefault(ga, set()).add(where)

    n = len(data)
    try:
        cur = m.end()
        hero_types: set[int] = set()
        for _ in range(10):                       # 10 heroes x (8 header + 4 universal vessels x 28)
            hero_types.add(data[cur])
            cur += 8
            for _ in range(4):
                cur += 4                          # vessel id
                for ga in struct.unpack_from("<6I", data, cur):
                    add(ga, "vessel")
                cur += 24
        while cur + 4 <= n:                       # per-hero vessels until a zero vessel id
            vid = struct.unpack_from("<I", data, cur)[0]
            cur += 4
            if vid == 0:
                break
            for ga in struct.unpack_from("<6I", data, cur):
                add(ga, "vessel")
            cur += 24
        zero_seen = False
        for _ in range(_MAX_PRESETS):             # presets: 80 bytes each
            h_id = data[cur + 1]
            counter = data[cur + 3]
            if h_id != 0 and h_id not in hero_types:
                raise EquipmentError(f"Unknown hero id {h_id} in a preset; refusing to guess.")
            for ga in struct.unpack_from("<6I", data, cur + 48):
                add(ga, "preset")
            cur += 80
            if counter == 0:
                if zero_seen:
                    break
                zero_seen = True
    except (struct.error, IndexError) as exc:
        raise EquipmentError(f"Vessel/preset block is malformed ({exc}).") from exc
    return out


# ---------------------------------------------------------------------------
# Mutations. All return the number of items changed; the caller re-parses afterwards.
# ---------------------------------------------------------------------------
def set_favorite(data: bytearray, slot: Slot, relics: list[Relic], favorite: bool) -> int:
    """Set the favorite byte. Favoriting also clears is_new, as the game itself does."""
    changed = 0
    for r in relics:
        eo = r.entry_offset
        if struct.unpack_from("<I", data, eo)[0] != r.ga_handle:
            raise LayoutError(f"Entry for relic 0x{r.ga_handle:08X} moved; aborting.")
        if bool(data[eo + 12]) == favorite:
            continue
        data[eo + 12] = 1 if favorite else 0
        if favorite:
            data[eo + 13] = 0
        changed += 1
    return changed


def remove_relics(data: bytearray, slot: Slot, relics: list[Relic]) -> bytearray:
    """Return a NEW slot buffer (same length) with the given relics removed.

    Refuses to touch equipped relics, or anything at all when equipment could not be read.
    """
    if slot.equipment_error:
        raise EquipmentError(slot.equipment_error)
    if not relics:
        return bytearray(data)
    seen: set[int] = set()
    for r in relics:
        if r.equipped_in:
            raise EquipmentError(f"Relic 0x{r.ga_handle:08X} is equipped; refusing to remove it.")
        if r.ga_handle in seen:
            raise LayoutError("Duplicate relic in removal list.")
        seen.add(r.ga_handle)
        s_off, s_size, s_ga = slot.states[r.state_index]
        if s_ga != r.ga_handle or s_size != RELIC_STATE_SIZE or s_off != r.state_offset:
            raise LayoutError(f"State for relic 0x{r.ga_handle:08X} moved; aborting.")
        if struct.unpack_from("<I", data, r.entry_offset)[0] != r.ga_handle:
            raise LayoutError(f"Entry for relic 0x{r.ga_handle:08X} moved; aborting.")

    buf = bytearray(data)
    # 1) entries (original coordinates)
    for r in relics:
        buf[r.entry_offset:r.entry_offset + ENTRY_SIZE] = bytes(ENTRY_SIZE)
    remaining = slot.counted_entries - len(relics)
    struct.pack_into("<I", buf, slot.entry_count_offset, remaining)

    # 2) states: swap each removed 80 B record for the 8 B empty record
    drop = {r.state_index for r in relics}
    parts = [bytes(buf[:START_OFFSET])]
    for i, (off, size, _ga) in enumerate(slot.states):
        parts.append(EMPTY_STATE if i in drop else bytes(buf[off:off + size]))
    parts.append(bytes(buf[slot.state_end:]))
    out = bytearray(b"".join(parts))

    # 3) restore the exact slot size by re-inserting the padding before the 28 byte tail
    pad = REMOVE_PAD * len(relics)
    out = out[:-TAIL_SIZE] + bytes(pad) + out[-TAIL_SIZE:]
    if len(out) != len(data):
        raise LayoutError("Internal error: slot size changed.")
    return out


def credit_murks(data: bytearray, slot: Slot, amount: int) -> int:
    """Add `amount` to the Murk counter (clamped to u32). Returns the new balance."""
    new = min(slot.murks + amount, U32_MAX)
    struct.pack_into("<I", data, slot.murk_offset, new)
    return new


def snapshot(slot: Slot) -> dict[int, tuple[bytes, bytes]]:
    """ga_handle -> (state bytes, entry bytes) for every parsed relic. Used to prove untouched relics are untouched."""
    d = slot.data
    return {
        r.ga_handle: (bytes(d[r.state_offset:r.state_offset + RELIC_STATE_SIZE]),
                      bytes(d[r.entry_offset:r.entry_offset + ENTRY_SIZE]))
        for r in slot.relics
    }
