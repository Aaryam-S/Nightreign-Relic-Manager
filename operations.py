"""Apply a dry-run Plan to a save file, safely. No GUI code here so it can be tested headlessly.

Order of operations (every step can abort, and nothing is written before step 6):
  1 re-read the file and make sure it is byte-identical to what the user reviewed
  2 original slot checksum must verify (otherwise our idea of the layout is wrong)
  3 re-locate every candidate relic in the fresh parse
  4 build the edited slot in memory
  5 re-parse the edited slot and prove only the intended changes happened
  6 timestamped backup -> atomic write -> read back and compare -> auto-restore on any problem
"""
from __future__ import annotations

import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import relic_parser as rp
from filter_engine import Plan
from save_io import SaveFile, atomic_write, checksum_ok, make_backup


class ApplyError(Exception):
    pass


@dataclass
class ApplyResult:
    backup: Path
    changed: int
    murk_before: int
    murk_after: int


def game_running() -> bool:
    """Best effort (Windows only): is a process with 'nightreign' in its name running?"""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "nightreign" in out.lower()


def _sigs(slot: rp.Slot) -> int:
    return struct.unpack_from("<I", slot.data, slot.sigs_offset)[0]


def verify_edit(before: rp.Slot, after: rp.Slot, action: str, targets: set[int], expected_murk: int) -> None:
    """Raise ApplyError unless `after` differs from `before` only as intended."""
    if len(after.data) != len(before.data):
        raise ApplyError("Slot size changed.")
    if after.name != before.name or _sigs(after) != _sigs(before):
        raise ApplyError("Name or Sigs changed unexpectedly.")
    if after.equipment_error or after.equipped_count != before.equipped_count:
        raise ApplyError("Equipment data changed unexpectedly.")
    b_by = {r.ga_handle: r for r in before.relics}
    a_by = {r.ga_handle: r for r in after.relics}
    if action == "SELL":
        if set(a_by) != set(b_by) - targets:
            raise ApplyError("Relic set after removal is not what was planned.")
        if after.counted_entries != before.counted_entries - len(targets) or after.stored_entry_count != after.counted_entries:
            raise ApplyError("Inventory entry count is inconsistent after removal.")
        sb, sa = rp.snapshot(before), rp.snapshot(after)
        for ga in a_by:
            if sa[ga] != sb[ga]:
                raise ApplyError(f"Untouched relic 0x{ga:08X} was modified.")
    else:
        if set(a_by) != set(b_by):
            raise ApplyError("Relic set changed during a favorite edit.")
        want = action == "FAVORITE"
        for ga, r in a_by.items():
            if ga in targets and r.favorite != want:
                raise ApplyError(f"Relic 0x{ga:08X} did not take the new favorite state.")
            if ga not in targets and r.favorite != b_by[ga].favorite:
                raise ApplyError(f"Relic 0x{ga:08X} changed but was not selected.")
        allowed = set(range(len(before.data) - rp.TAIL_SIZE, len(before.data)))   # re-signed checksum tail
        for ga in targets:
            eo = b_by[ga].entry_offset
            allowed |= {eo + 12, eo + 13}
        diff = [i for i in range(len(before.data)) if before.data[i] != after.data[i]]
        stray = [i for i in diff if i not in allowed]
        if stray:
            raise ApplyError(f"{len(stray)} unrelated byte(s) changed (first at 0x{stray[0]:X}).")
    if after.murks != expected_murk:
        raise ApplyError(f"Murk is {after.murks}, expected {expected_murk}.")


def apply_plan(path: str | Path, slot_index: int, loaded_sha256: str, plan: Plan,
               backups_dir: Path) -> ApplyResult:
    path = Path(path)
    if plan.blocked:
        raise ApplyError(plan.blocked)
    if not plan.candidates:
        raise ApplyError("Nothing to apply.")

    sf = SaveFile.load(path)                                            # 1
    if sf.sha256 != loaded_sha256:
        raise ApplyError("The save file changed on disk since it was loaded. Reload it and run the preview again.")
    if not sf.checksum_ok(slot_index):                                  # 2
        raise ApplyError("The slot's original checksum does not verify; refusing to edit it.")
    before = rp.Slot(sf.slot_data(slot_index))
    if plan.action == "SELL" and not before.equipment_known:
        raise ApplyError(before.equipment_error or "Equipment unknown.")

    by_ga = {r.ga_handle: r for r in before.relics}                     # 3
    try:
        targets = [by_ga[i.relic.ga_handle] for i in plan.candidates]
    except KeyError as exc:
        raise ApplyError(f"Relic 0x{exc.args[0]:08X} is no longer in the save; reload.") from None
    target_set = {r.ga_handle for r in targets}

    new = bytearray(before.data)                                        # 4
    murk_before = before.murks
    expected_murk = murk_before
    try:
        if plan.action == "SELL":
            if plan.credit_murk:
                expected_murk = min(murk_before + plan.payout, rp.U32_MAX)
            new = rp.remove_relics(new, before, targets)
            if plan.credit_murk:
                rp.credit_murks(new, rp.Slot(new), plan.payout)
        else:
            rp.set_favorite(new, before, targets, plan.action == "FAVORITE")
        after = rp.Slot(new)                                            # 5
        verify_edit(before, after, plan.action, target_set, expected_murk)
    except (rp.LayoutError, rp.EquipmentError) as exc:
        raise ApplyError(str(exc)) from exc

    sf.replace_slot(slot_index, new)
    out = sf.to_bytes()
    check = SaveFile(None, out)                                         # prove the bytes we will write decode correctly
    if not checksum_ok(check.payloads[slot_index]):
        raise ApplyError("Re-encoded slot failed its own checksum.")
    for i, p in check.payloads.items():
        if i != slot_index and p != sf.payloads[i]:
            raise ApplyError(f"Slot {i} changed but was not edited.")
    verify_edit(before, rp.Slot(check.payloads[slot_index]), plan.action, target_set, expected_murk)

    backup = make_backup(path, backups_dir)                             # 6
    original = path.read_bytes()
    try:
        atomic_write(path, out)
        if path.read_bytes() != out:
            raise OSError("Read-back of the written file did not match.")
    except Exception as exc:
        try:
            atomic_write(path, original)
        except OSError:
            raise ApplyError(f"Write failed ({exc}) AND restoring failed. Restore manually from {backup}.") from exc
        raise ApplyError(f"Write failed ({exc}); the original file was restored. Backup: {backup}") from exc
    return ApplyResult(backup=backup, changed=len(targets), murk_before=murk_before, murk_after=expected_murk)
