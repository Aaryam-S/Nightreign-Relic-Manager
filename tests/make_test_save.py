"""Build a SYNTHETIC Nightreign-style save for testing and GUI demos.

It follows the layout documented in relic_parser.py, so it proves the code is self-consistent,
NOT that the layout matches a real save. Real-save confidence comes from running
`python app.py --inspect <your NR0000.sl2>` (read-only) and checking name/Murk/relic count.

Test helper (not part of the app). usage: python tests/make_test_save.py out.sl2 [--relics 150] [--seed 1]
"""
from __future__ import annotations

import argparse
import os
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import relic_parser as rp                       # noqa: E402
from game_data import GameData                  # noqa: E402
from save_io import (BND4_ENTRY_MAGIC, BND4_HEADER_LEN, BND4_ENTRY_HEADER_LEN,  # noqa: E402
                     _encrypt, patch_checksum)

EQUIP_MAGIC = bytes.fromhex("C2000300002C000003000A0004004600") + bytes.fromhex("64000000")
FILLER = bytes([0xFF] * 8 + [0, 0, 0, 0xFF] + [0] * 8 + [0xFF] * 8)   # 28 bytes, as in the reference editor


def relic_state(ga: int, real_id: int, effects: list[int], curses: list[int]) -> bytes:
    b = bytearray(80)
    item_id = 0x80000000 | real_id
    struct.pack_into("<IIII", b, 0, ga, item_id, item_id, 0xFFFFFFFF)
    for i in range(3):
        struct.pack_into("<I", b, 16 + 4 * i, effects[i] if i < len(effects) else 0xFFFFFFFF)
        struct.pack_into("<I", b, 56 + 4 * i, curses[i] if i < len(curses) else 0xFFFFFFFF)
    b[28:56] = FILLER
    struct.pack_into("<I", b, 68, 0xFFFFFFFF)
    return bytes(b)


def build_slot(rng: random.Random, gd: GameData, n_relics: int, name: str, murk: int):
    sale = [r for r in gd.relics.values() if r.salable and r.tier]
    nontier = [r for r in gd.relics.values() if r.salable and not r.tier]
    unsalable = [r for r in gd.relics.values() if not r.salable]
    good = sorted(i for i in gd.relic_effect_ids if not gd.effects[i].debuff)
    bad = sorted(i for i in gd.relic_effect_ids if gd.effects[i].debuff)

    states: list[bytes] = [rp.EMPTY_STATE] * 84
    entries: list[tuple[int, int, int, int, int]] = []     # ga, qty, acq, fav, new
    relic_gas: list[int] = []
    acq = 1
    inst = 0x800054
    # a few non-relic items so the variable-size walk is exercised
    for k in range(6):
        states.append(bytes([k + 1, 0, 0, 0x80]) + struct.pack("<I", 1000 + k) + bytes(80))
        entries.append((0x80000000 | (k + 1), 1, acq, 0, 0)); acq += 1
    for k in range(6):
        states.append(struct.pack("<II", 0x90000000 | (k + 1), 2000 + k) + bytes(8))
        entries.append((0x90000000 | (k + 1), 1, acq, 0, 0)); acq += 1
    entries.append((0xB00003E9, 3, acq, 0, 0)); acq += 1          # a goods entry (flask)

    meta_tiers = {}
    for _ in range(n_relics):
        inst += 1
        roll = rng.random()
        rd = rng.choice(sale) if roll < 0.85 else (rng.choice(nontier) if roll < 0.93 else rng.choice(unsalable))
        ga = 0xC0000000 | inst
        n_eff = rng.choice([1, 2, 3, 3])
        effs = rng.sample(good, n_eff)
        curses = rng.sample(bad, rng.choice([0, 0, 1, 2, 3]))
        states.append(relic_state(ga, rd.id, effs, curses))
        fav = 1 if rng.random() < 0.08 else 0
        entries.append((ga, 1, acq, fav, 0 if fav else int(rng.random() < 0.5))); acq += 1
        relic_gas.append(ga)
        meta_tiers[ga] = rd.tier
    if len(states) > rp.STATE_SLOT_COUNT:
        raise SystemExit("too many items for one slot")
    states += [rp.EMPTY_STATE] * (rp.STATE_SLOT_COUNT - len(states))

    # equipped: first few relics -> hero vessels / presets
    equipped_vessel = relic_gas[:6]
    equipped_preset = relic_gas[6:9]
    rng.shuffle(entries)

    body = bytearray(rp.START_OFFSET)
    body += b"".join(states)
    assert len(body) == rp.START_OFFSET + sum(len(s) for s in states)
    gap = bytearray(rp.NAME_GAP)
    struct.pack_into("<I", gap, rp.NAME_GAP + rp.SIGS_FROM_NAME, 77)           # sigs (name - 64)
    body += gap
    name_off = len(body)
    body += bytearray(rp.ENTRY_COUNT_FROM_NAME)                                # name .. entry count
    enc = name.encode("utf-16-le")
    body[name_off:name_off + len(enc)] = enc
    struct.pack_into("<I", body, name_off + rp.MURK_FROM_NAME, murk)
    body += struct.pack("<I", len(entries))
    ent = bytearray(rp.ENTRY_SLOT_COUNT * rp.ENTRY_SIZE)
    for i, (ga, qty, aq, fav, new) in enumerate(entries):
        struct.pack_into("<IIIBB", ent, i * rp.ENTRY_SIZE, ga, qty, aq, fav, new)
    body += ent

    # vessel / preset block
    body += EQUIP_MAGIC
    it = iter(equipped_vessel)
    for hero in range(1, 11):
        body += struct.pack("<BBxxI", hero, 0, 9600 + hero)
        for v in range(4):
            relics = [next(it, 0) if (hero == 1 and v == 0) else 0 for _ in range(6)]
            body += struct.pack("<I6I", 9600 + hero * 10 + v, *relics)
    body += struct.pack("<I6I", 9700, equipped_vessel[0], 0, 0, 0, 0, 0)       # a hero vessel
    body += struct.pack("<I", 0)                                               # vessel list terminator
    for p in range(100):
        rec = bytearray(80)
        if p < 3:
            rec[1] = 1                                                          # hero
            rec[3] = p + 1                                                      # counter
            nm = f"Preset{p}".encode("utf-16-le")
            rec[4:4 + len(nm)] = nm
            struct.pack_into("<I", rec, 44, 9601)
            if p < len(equipped_preset):
                struct.pack_into("<I", rec, 48, equipped_preset[p])
        body += rec
    body += bytes(2048)                                                         # padding area
    body += bytes(rp.TAIL_SIZE)                                                 # 12 pad + md5 (patched on save)
    while len(body) % 16:
        body.insert(-rp.TAIL_SIZE, 0)
    patch_checksum(body)
    return bytearray(body), relic_gas, equipped_vessel + equipped_preset


def build_regulation(flags: list[int]) -> bytearray:
    b = bytearray(1024)
    b[100:110] = bytes(flags)
    b[161:168] = b"'\x00\x00FACE"          # magic start = 161; flags at 161 - 61 = 100
    patch_checksum(b)
    return b


def build_dummy() -> bytearray:
    b = bytearray(256)
    patch_checksum(b)
    return b


def build_bnd4(payloads: list[bytes | bytearray]) -> bytes:
    n = len(payloads)
    header = bytearray(BND4_HEADER_LEN + BND4_ENTRY_HEADER_LEN * n)
    header[0:4] = b"BND4"
    struct.pack_into("<i", header, 12, n)
    blobs = [_encrypt(p) for p in payloads]
    off = len(header)
    for i, blob in enumerate(blobs):
        pos = BND4_HEADER_LEN + BND4_ENTRY_HEADER_LEN * i
        header[pos:pos + 8] = BND4_ENTRY_MAGIC
        struct.pack_into("<iiiii", header, pos + 8, len(blob), 0, off, 0, 0)
        off += len(blob)
    return bytes(header) + b"".join(blobs)


def make_save(n_relics: int = 150, seed: int = 1):
    rng = random.Random(seed)
    gd = GameData(ROOT / "data")
    s0, gas0, eq0 = build_slot(rng, gd, n_relics, "TestRunner", 12345)
    s2, gas2, eq2 = build_slot(rng, gd, max(20, n_relics // 3), "SecondChar", 999)
    payloads = [s0, build_dummy(), s2] + [build_dummy() for _ in range(7)] + [build_regulation([1, 0, 1] + [0] * 7)]
    meta = {0: {"relics": gas0, "equipped": set(eq0), "murk": 12345, "name": "TestRunner"},
            2: {"relics": gas2, "equipped": set(eq2), "murk": 999, "name": "SecondChar"}}
    return build_bnd4(payloads), meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--relics", type=int, default=150)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    data, _ = make_save(a.relics, a.seed)
    Path(a.out).write_bytes(data)
    print(f"wrote synthetic save {a.out} ({len(data)} bytes, {a.relics} relics in slot 0)")
