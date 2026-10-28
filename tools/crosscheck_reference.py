"""Optional: compare this project's edits byte-for-byte with the reference editor's own algorithm.

Needs the reference clone at ./reference/Elden-Ring-Nightreign-Save-Editor (git clone it there).
The reference's heavy data-layer modules (pandas, param tables) are stubbed; only its inventory
parsing / remove / favorite code runs. Uses a synthetic slot, so it proves algorithmic agreement,
not agreement with a real save.
"""
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF_SRC = ROOT / "reference" / "Elden-Ring-Nightreign-Save-Editor" / "src"
if not REF_SRC.exists():
    sys.exit("reference clone not found; skipping")

# stub the reference's data layer (not needed for byte-level inventory edits)
for name in ("pandas",):
    sys.modules[name] = types.ModuleType(name)
rc = types.ModuleType("relic_checker")
rc.RelicChecker = type("RelicChecker", (), {})
rc.InvalidReason = type("InvalidReason", (), {"NONE": 0})
rc.is_curse_invalid = lambda *_: False
sys.modules["relic_checker"] = rc
sd = types.ModuleType("source_data_handler")
sd.SourceDataHandler = type("SourceDataHandler", (), {})
sys.modules["source_data_handler"] = sd

sys.path.insert(0, str(REF_SRC))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import globals as ref_globals                    # noqa: E402  (the reference's module of that name)
import inventory_handler as ref_inv              # noqa: E402
import relic_parser as rp                        # noqa: E402
from make_test_save import make_save             # noqa: E402
from save_io import SaveFile                     # noqa: E402


def reference_inventory(data):
    ref_globals.data = bytearray(data)
    ref_inv.InventoryHandler._instance = None
    ref_inv.InventoryHandler._initialized = False
    h = ref_inv.InventoryHandler()
    h.parse()
    h.reset_equipped_records()
    return h


def main():
    raw, _ = make_save(180, seed=11)
    slot_data = SaveFile(None, raw).slot_data(0)
    slot = rp.Slot(slot_data)
    rng = random.Random(4)
    unequipped = [r for r in slot.relics if not r.equipped_in]
    victims = rng.sample(unequipped, 40)

    # --- removal
    h = reference_inventory(slot_data)
    for r in victims:
        h.remove_relic_from_inventory(r.ga_handle)
    ref_after = bytes(ref_globals.data)
    mine = bytes(rp.remove_relics(bytearray(slot_data), slot, victims))
    print("remove: lengths", len(ref_after), len(mine), "identical:", ref_after == mine)
    ok = ref_after == mine

    # --- favorite / unfavorite
    favs = rng.sample(slot.relics, 30)
    h = reference_inventory(slot_data)
    for r in favs:
        if not h.relics[r.ga_handle].is_favorite:
            h.toggle_favorite_mark(r.ga_handle)
    ref_fav = bytes(ref_globals.data)
    buf = bytearray(slot_data)
    rp.set_favorite(buf, slot, favs, True)
    print("favorite: identical:", ref_fav == bytes(buf))
    ok = ok and ref_fav == bytes(buf)
    sys.exit(0 if ok else 1)


main()
