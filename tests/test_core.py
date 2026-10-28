import hashlib
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import filter_engine as fe                      # noqa: E402
import operations as ops                        # noqa: E402
import relic_parser as rp                       # noqa: E402
from game_data import GameData                  # noqa: E402
from make_test_save import make_save            # noqa: E402
from save_io import SaveFile, checksum_ok       # noqa: E402

GD = GameData(ROOT / "data")


@pytest.fixture()
def save(tmp_path):
    raw, meta = make_save(150, seed=7)
    p = tmp_path / "NR0000.sl2"
    p.write_bytes(raw)
    return p, meta, raw


def infos_for(slot):
    return [GD.describe(r, slot.equipment_known) for r in slot.relics]


def slot0(path):
    sf = SaveFile.load(path)
    return sf, rp.Slot(sf.slot_data(0))


def sell_preset(**attrs):
    p = fe.default_preset("t")
    p["target_action"] = "SELL"
    p["filters"]["attributes"].update({"tiers": ["delicate", "polished", "grand"], **attrs})
    return p


# -- container ----------------------------------------------------------------
def test_container_roundtrip_is_byte_identical_when_untouched(save):
    path, _, raw = save
    sf = SaveFile.load(path)
    assert sf.to_bytes() == raw
    assert all(sf.checksum_ok(i) for i in range(11))
    assert sf.active_slots() == (True, False, True) + (False,) * 7


def test_replacing_a_slot_keeps_other_slots_ciphertext_and_size(save):
    path, _, raw = save
    sf = SaveFile.load(path)
    new = sf.slot_data(0)
    new[0x20] ^= 0xFF
    sf.replace_slot(0, new)
    out = sf.to_bytes()
    assert len(out) == len(raw)
    e1, e0 = sf.entries[1], sf.entries[0]
    assert out[e1.data_offset:e1.data_offset + e1.size] == raw[e1.data_offset:e1.data_offset + e1.size]
    chk = SaveFile(None, out)
    assert checksum_ok(chk.payloads[0]) and chk.payloads[0][0x20] == new[0x20]


def test_not_a_save_is_rejected():
    from save_io import SaveFormatError
    with pytest.raises(SaveFormatError):
        SaveFile(None, b"nope" * 100)


# -- parsing ------------------------------------------------------------------
def test_slot_parse_matches_generator(save):
    path, meta, _ = save
    _, slot = slot0(path)
    assert slot.name == meta[0]["name"]
    assert slot.murks == meta[0]["murk"]
    assert {r.ga_handle for r in slot.relics} == set(meta[0]["relics"])
    assert slot.orphan_relic_entries == 0 and not slot.entry_count_mismatch
    assert slot.equipment_known
    assert {r.ga_handle for r in slot.relics if r.equipped_in} == meta[0]["equipped"]
    assert any(r.favorite for r in slot.relics)


def test_equipment_unknown_when_marker_missing(save):
    path, _, _ = save
    _, slot = slot0(path)
    data = bytearray(slot.data)
    m = rp._EQUIP_MAGIC.search(bytes(data))
    data[m.start()] ^= 0xFF
    bad = rp.Slot(data)
    assert not bad.equipment_known and "not found" in bad.equipment_error
    plan = fe.build_plan(infos_for(bad), sell_preset(), GD, bad.murks, equipment_known=False)
    assert plan.blocked and not plan.can_apply


# -- filters -------------------------------------------------------------------
def _info(effects=(), curses=(), tier="delicate", color="Red", deep=False, fav=False, equipped=False, salable=True):
    ids = {e.name: e.id for e in GD.effects.values()}
    r = rp.Relic(0xC0000001, 0x80000064, 100, 0, 0, 0, 0,
                 tuple([ids[n] for n in effects] + [0xFFFFFFFF] * (3 - len(effects))),
                 tuple([ids[n] for n in curses] + [0xFFFFFFFF] * (3 - len(curses))), 1, fav, False)
    i = GD.describe(r, True)
    i.tier, i.color, i.deep, i.equipped, i.salable = tier, color, deep, equipped, salable
    return i


def test_any_x_and_any_y():
    p = fe.default_preset()
    p["filters"]["combination_query"].update(enabled=True, group_x_affixes=["Poise +1"],
                                             group_y_affixes=["Increased Maximum Stamina"])
    m = fe.make_matcher(p, GD)
    assert m.matches(_info(["Poise +1", "Increased Maximum Stamina"]))
    assert not m.matches(_info(["Poise +1"]))
    assert not m.matches(_info(["Increased Maximum Stamina"]))


def _demerit_preset(bad, n, expr="D", **extra):
    p = fe.default_preset()
    p["filters"]["combination_query"].update(enabled=True, group_d_affixes=bad, expression=expr,
                                             min_counts={"D": n}, **extra)
    return p


def test_demerit_group_threshold_counts_occurrences():
    m = fe.make_matcher(_demerit_preset(["Reduced Maximum HP", "reduced maximum fp"], 2), GD)
    assert m.matches(_info(curses=["Reduced Maximum HP", "Reduced Maximum FP"]))
    assert not m.matches(_info(curses=["Reduced Maximum HP"]))
    assert not m.matches(_info(curses=["Reduced Maximum HP", "Reduced Rune Acquisition"]))
    assert not m.matches(_info(["Reduced Maximum HP"]))           # group D only looks at curse slots


def test_demerit_group_combines_with_other_groups_in_logic():
    p = _demerit_preset(["Reduced Maximum HP"], 1, "X AND D", group_x_affixes=["Poise +1"])
    both, only_x = _info(["Poise +1"], ["Reduced Maximum HP"]), _info(["Poise +1"])
    assert fe.make_matcher(p, GD).matches(both) and not fe.make_matcher(p, GD).matches(only_x)
    p["filters"]["combination_query"]["expression"] = "X OR D"
    assert fe.make_matcher(p, GD).matches(only_x)
    p["filters"]["combination_query"]["expression"] = "X AND NOT D"
    assert fe.make_matcher(p, GD).matches(only_x) and not fe.make_matcher(p, GD).matches(both)


def test_old_presets_with_separate_demerit_rule_are_migrated():
    old = {"preset_name": "old", "combine": "OR", "target_action": "SELL", "filters": {
        "combination_query": {"enabled": True, "group_x_affixes": ["Poise +1"], "match_mode": "ANY_X"},
        "bad_demerit_rule": {"enabled": True, "bad_effects_list": ["Reduced Maximum HP"], "demerit_count_threshold": 1}}}
    p = fe.normalize_preset(old)
    assert "bad_demerit_rule" not in p["filters"] and "combine" not in p
    assert fe.effective_expression(p["filters"]["combination_query"]) == "(X) OR D"
    m = fe.make_matcher(old, GD)
    assert m.matches(_info(["Poise +1"])) and m.matches(_info(curses=["Reduced Maximum HP"]))
    assert fe.normalize_preset(p) == p                             # idempotent


def test_empty_preset_matches_nothing_and_unknown_names_warn():
    m = fe.make_matcher(fe.default_preset(), GD)
    assert m.is_empty() and not m.matches(_info(["Poise +1"]))
    p = fe.default_preset()
    p["filters"]["combination_query"].update(enabled=True, expression="D", group_d_affixes=["Stamina Recovery -15%"])
    assert any("not a known relic effect" in w for w in fe.make_matcher(p, GD).warnings)


def test_sell_protections_in_plan(save):
    path, meta, _ = save
    _, slot = slot0(path)
    plan = fe.build_plan(infos_for(slot), sell_preset(), GD, slot.murks, equipment_known=True)
    cand = {c.relic.ga_handle for c in plan.candidates}
    assert cand and not (cand & meta[0]["equipped"])
    assert all(not c.favorite and c.salable and c.tier for c in plan.candidates)
    assert plan.payout == sum(GD.sell_value(c.tier) for c in plan.candidates)
    assert plan.murk_new == plan.murk_current + plan.payout
    reasons = {r for _, r in plan.skipped}
    assert "equipped" in reasons and "favorite (locked)" in reasons
    plan2 = fe.build_plan(infos_for(slot), sell_preset(), GD, slot.murks, equipment_known=True, allow_favorites=True)
    assert any(c.favorite for c in plan2.candidates)


def test_preset_file_roundtrip_and_corruption(tmp_path):
    p = tmp_path / "rules.json"
    fe.save_presets(p, [fe.default_preset("A")])
    loaded, warn = fe.load_presets(p)
    assert warn is None and loaded[0]["preset_name"] == "A"
    p.write_text("{not json")
    loaded, warn = fe.load_presets(p)
    assert loaded == [] and warn and not p.exists() and list(tmp_path.glob("rules.corrupt_*"))
    assert fe.load_presets(tmp_path / "missing.json") == ([], None)       # no presets are shipped; a fresh install starts empty


# -- relic-effect list and unique relics --------------------------------------
def test_picker_lists_only_relic_effects():
    relic_names = set(GD.all_effect_names(None))
    all_names = {e.name for e in GD.effects.values()}
    assert relic_names < all_names and len(relic_names) == 519
    assert "Add Fire to Weapon" in all_names and "Add Fire to Weapon" not in relic_names   # item passive
    assert set(GD.all_effect_names(True)) <= relic_names and "Reduced Flask HP Restoration" in GD.all_effect_names(True)
    # a name that only item passives have is flagged as unknown for relic rules
    assert GD.resolve_tokens(["Add Fire to Weapon"])[1] == ["Add Fire to Weapon"]
    assert GD.resolve_tokens(["Poise +1", "Increased Maximum Stamina"])[1] == []


def norm_name(s):
    from game_data import norm
    return norm(s)


def test_unique_relics_are_never_sold():
    from game_data import is_unique_relic
    fathom = next(r for r in GD.relics.values() if r.name == "Night of the Fathom")
    assert fathom.unique and is_unique_relic(2030) and is_unique_relic(10000) and not is_unique_relic(102)
    sellable_unique = [r for r in GD.relics.values() if r.unique and r.salable]
    assert sellable_unique, "data has unique relics the game flags sellable; they must still be protected"
    for rd in (sellable_unique[0], fathom):
        info = _info()
        info.unique, info.salable, info.tier, info.name = True, True, "grand", rd.name
        p = sell_preset()
        plan = fe.build_plan([info], p, GD, 0, equipment_known=True)
        assert not plan.candidates and plan.skipped[0][1] == "unique relic (protected)"
    normal = _info(); normal.tier = "grand"
    assert fe.build_plan([normal], sell_preset(), GD, 0, equipment_known=True).candidates


# -- applying -----------------------------------------------------------------
def test_apply_favorite_changes_only_entry_flags(save, tmp_path):
    path, _, raw = save
    sf, slot = slot0(path)
    p = fe.default_preset()
    p["target_action"] = "FAVORITE"
    p["filters"]["attributes"]["tiers"] = ["grand"]
    plan = fe.build_plan(infos_for(slot), p, GD, slot.murks, equipment_known=True)
    assert plan.candidates
    res = ops.apply_plan(path, 0, sf.sha256, plan, tmp_path / "backups")
    assert res.backup.read_bytes() == raw and res.backup.name.startswith("NR0000_backup_")
    sf2, after = slot0(path)
    assert sf2.checksum_ok(0) and len(path.read_bytes()) == len(raw)
    want = {c.relic.ga_handle for c in plan.candidates}
    assert all(r.favorite for r in after.relics if r.ga_handle in want)
    assert after.murks == slot.murks
    for i in (1, 2, 10):                                    # untouched entries keep their exact ciphertext
        e = sf2.entries[i]
        assert path.read_bytes()[e.data_offset:e.data_offset + e.size] == raw[e.data_offset:e.data_offset + e.size]
    # unfavorite everything we just locked, restoring original flags
    p["target_action"] = "UNFAVORITE"
    plan2 = fe.build_plan(infos_for(after), p, GD, after.murks, equipment_known=True)
    ops.apply_plan(path, 0, sf2.sha256, plan2, tmp_path / "backups")
    final = slot0(path)[1]
    assert not any(r.favorite for r in final.relics if GD.relic_def(r.real_id).tier == "grand")


def test_apply_sell_end_to_end(save, tmp_path):
    path, meta, raw = save
    sf, slot = slot0(path)
    before_snap = rp.snapshot(slot)
    plan = fe.build_plan(infos_for(slot), sell_preset(), GD, slot.murks, equipment_known=True)
    sold = {c.relic.ga_handle for c in plan.candidates}
    assert 0 < len(sold) < len(slot.relics)
    res = ops.apply_plan(path, 0, sf.sha256, plan, tmp_path / "backups")
    assert res.murk_after == slot.murks + plan.payout and res.backup.read_bytes() == raw

    sf2, after = slot0(path)
    assert len(path.read_bytes()) == len(raw) and sf2.checksum_ok(0)
    assert after.murks == slot.murks + plan.payout
    kept = {r.ga_handle for r in after.relics}
    assert kept == set(meta[0]["relics"]) - sold
    assert meta[0]["equipped"] <= kept                       # every equipped relic survived
    assert after.equipped_count == slot.equipped_count
    after_snap = rp.snapshot(after)
    assert all(after_snap[g] == before_snap[g] for g in kept)   # survivors byte-identical
    assert after.stored_entry_count == after.counted_entries == slot.counted_entries - len(sold)
    assert sf2.slot_data(2) == SaveFile(None, raw).slot_data(2)  # other character untouched
    # removed records collapsed to the 8-byte empty state
    n_empty = lambda s: sum(1 for _, size, ga in s.states if ga == 0 and size == 8)    # noqa: E731
    assert n_empty(after) == n_empty(slot) + len(sold)

    # running the same preset again has nothing left to do
    plan_again = fe.build_plan(infos_for(after), sell_preset(), GD, after.murks, equipment_known=True)
    assert not (sold & {c.relic.ga_handle for c in plan_again.candidates})


def test_sell_refuses_equipped_even_if_forced(save):
    path, _, _ = save
    sf, slot = slot0(path)
    eq = [r for r in slot.relics if r.equipped_in][:1]
    with pytest.raises(rp.EquipmentError):
        rp.remove_relics(bytearray(slot.data), slot, eq)


def test_apply_aborts_if_file_changed_since_load(save, tmp_path):
    path, _, _ = save
    sf, slot = slot0(path)
    plan = fe.build_plan(infos_for(slot), sell_preset(), GD, slot.murks, equipment_known=True)
    path.write_bytes(path.read_bytes() + b"")                # same bytes: fine
    other = bytearray(path.read_bytes()); other[-1] ^= 1; path.write_bytes(other)
    with pytest.raises(ops.ApplyError, match="changed on disk"):
        ops.apply_plan(path, 0, sf.sha256, plan, tmp_path / "b")
    assert not (tmp_path / "b").exists()


def test_apply_refuses_when_original_checksum_is_bad(save, tmp_path):
    path, _, raw = save
    sf, slot = slot0(path)
    plan = fe.build_plan(infos_for(slot), sell_preset(), GD, slot.murks, equipment_known=True)
    # rebuild a file whose slot 0 has a wrong MD5 (as if our layout assumptions were off)
    bad = SaveFile(None, raw)
    p = bad.slot_data(0); p[-20] ^= 0xFF                     # inside the 16-byte MD5 field
    bad.payloads[0] = p                                      # bypass patching on purpose
    from save_io import _encrypt
    e = bad.entries[0]
    out = bytearray(raw); out[e.data_offset:e.data_offset + e.size] = _encrypt(p)
    path.write_bytes(out)
    sha = hashlib.sha256(out).hexdigest()
    with pytest.raises(ops.ApplyError, match="checksum"):
        ops.apply_plan(path, 0, sha, plan, tmp_path / "b")


def test_failed_write_restores_original(save, tmp_path, monkeypatch):
    path, _, raw = save
    sf, slot = slot0(path)
    plan = fe.build_plan(infos_for(slot), sell_preset(), GD, slot.murks, equipment_known=True)
    real = ops.atomic_write
    calls = []

    def flaky(p, data):
        calls.append(1)
        if len(calls) == 1:
            Path(p).write_bytes(b"garbage")                  # simulate a torn write
            raise OSError("disk full")
        real(p, data)

    monkeypatch.setattr(ops, "atomic_write", flaky)
    with pytest.raises(ops.ApplyError, match="restored"):
        ops.apply_plan(path, 0, sf.sha256, plan, tmp_path / "b")
    assert path.read_bytes() == raw


def test_murk_clamps_to_u32(save):
    path, _, _ = save
    _, slot = slot0(path)
    d = bytearray(slot.data)
    struct.pack_into("<I", d, slot.murk_offset, 0xFFFFFFF0)
    s2 = rp.Slot(d)
    assert rp.credit_murks(d, s2, 100) == 0xFFFFFFFF


# -- grouping and Group Z -----------------------------------------------------
def test_catalog_groups_every_effect_once():
    cat = GD.effect_catalog(None)
    flat = [n for _, names in cat for n in names]
    assert len(flat) == len(set(flat)) == 519
    d = dict(cat)
    assert "Poise +1" in d["Poise"] and "Reduced Flask HP Restoration" in d["Demerits (curses)"]
    assert "Lightning Attack Power Up +3" in d["Attack Power & Damage"]
    assert "Improved Magic Damage Negation +1" in d["Damage Negation"]
    for bad in ("Dmg negation upon landing charge attacks", "Taking damage boosts negation"):
        assert not any(bad.casefold() in n.casefold() for n in flat)
    assert not d.get("Other") or len(d["Other"]) < 5


def test_catalog_found_on_relics_fallback():
    cat = GD.effect_catalog(None, found_on_relics=["Totally Custom Effect", "Poise +1"])
    assert cat[-1][1] == ["Totally Custom Effect"]


def test_group_z_modes():
    info = _info()
    info.effects, info.curses = ["Poise +1", "Vigor +1", "Mind +1"], []
    def run(mode, x, y, z):
        q = {"enabled": True, "group_x_affixes": x, "group_y_affixes": y, "group_z_affixes": z, "match_mode": mode}
        p = fe.normalize_preset({"filters": {"combination_query": q}})
        return fe.make_matcher(p, GD), p
    for mode, z, expect in (("ANY_X_AND_ANY_Y_AND_ANY_Z", ["Mind +1"], True),
                            ("ANY_X_AND_ANY_Y_AND_ANY_Z", ["Vigor +1"], False),
                            ("ANY_X_AND_ANY_Y_AND_ANY_Z", ["Arcane +1"], False),
                            ("ANY_X_OR_ANY_Y_OR_ANY_Z", ["Arcane +1"], True),
                            ("ANY_X_AND_ANY_Y_NO_Z", ["Mind +1"], False),
                            ("ANY_X_AND_ANY_Y_NO_Z", ["Arcane +1"], True)):
        m, _ = run(mode, ["Poise +1"], ["Vigor +1"], z)
        assert m.matches(info) is expect, (mode, z)
    m, _ = run("ANY_X_AND_ANY_Y_AND_ANY_Z", ["Poise +1"], ["Vigor +1"], [])
    assert any("Group Z" in w for w in m.warnings)


def test_and_needs_distinct_effects():
    info = _info()
    info.effects, info.effect_ids, info.curses, info.curse_ids = ["Poise +1"], [101], [], []
    def match(mode, x, y, z=()):
        q = {"enabled": True, "group_x_affixes": x, "group_y_affixes": y, "group_z_affixes": list(z), "match_mode": mode}
        m = fe.make_matcher(fe.normalize_preset({"filters": {"combination_query": q}}), GD)
        return m.matches(info), m.warnings
    ok, warn = match("ANY_X_AND_ANY_Y", ["Poise +1", "Poise +2"], ["Poise +1", "Poise +3"])
    assert ok is False and any("share effects" in w for w in warn)      # one effect can't fill both sides
    info.effects, info.effect_ids = ["Poise +1", "Poise +3"], [101, 103]
    assert match("ANY_X_AND_ANY_Y", ["Poise +1", "Poise +2"], ["Poise +1", "Poise +3"])[0] is True
    assert match("ANY_X_AND_ANY_Y_AND_ANY_Z", ["Poise +1"], ["Poise +3"], ["Poise +1"])[0] is False


def test_logic_expressions():
    info = _info()
    info.effects, info.effect_ids, info.curses, info.curse_ids = ["Poise +1", "Vigor +1"], [101, 102], [], []
    def run(expr, x=(), y=(), z=()):
        q = {"enabled": True, "group_x_affixes": list(x), "group_y_affixes": list(y), "group_z_affixes": list(z), "expression": expr}
        m = fe.make_matcher(fe.normalize_preset({"filters": {"combination_query": q}}), GD)
        return m.matches(info), m.warnings
    P, V, M, A = ["Poise +1"], ["Vigor +1"], ["Mind +1"], ["Arcane +1"]
    assert run("(X OR Y) AND Z", M, A, V)[0] is False            # neither X nor Y present
    assert run("(X OR Y) AND Z", P, A, V)[0] is True
    assert run("X AND (Y OR Z)", P, M, V)[0] is True
    assert run("X AND NOT (Y OR Z)", P, M, V)[0] is False        # De Morgan: Z present
    assert run("X AND NOT (Y OR Z)", P, M, M)[0] is True
    assert run("NOT X OR Y", M, V)[0] is True and run("not x", P)[0] is False
    assert run("NOT X", [])[0] is False                           # NOT of an empty group matches nothing
    ok, warn = run("X AND (Y", P, V)
    assert ok is False and any("invalid" in w for w in warn)
    assert run("X Y", P, V)[0] is False and run("", P, V)[0] is True   # empty -> legacy default "X AND Y"
    # AND inside parentheses still needs different effects
    assert run("(X AND Y) OR Z", P, P, M)[0] is False
