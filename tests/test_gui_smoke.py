"""Drives the real Tk GUI through load -> dry run -> confirm -> apply on a temporary synthetic save.

Skipped automatically when Tk or a display is unavailable. Message boxes are stubbed so nothing blocks.
"""
import shutil
import sys
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import app as A                                  # noqa: E402
from make_test_save import make_save             # noqa: E402


@pytest.fixture()
def gui(tmp_path, monkeypatch):
    try:
        probe = tkinter.Tk()
        probe.destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    shutil.copytree(ROOT / "data", tmp_path / "data")
    raw, _ = make_save(120, seed=5)
    save = tmp_path / "NR0000.sl2"
    save.write_bytes(raw)
    monkeypatch.setattr(A, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(A, "PRESETS_PATH", tmp_path / "data" / "rules_presets.json")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path / "data")
    calls = []
    for n in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(A.messagebox, n, lambda *a, _n=n, **k: calls.append(_n))
    monkeypatch.setattr(A.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(A, "default_save_candidates", lambda: [])    # never touch a real save in tests
    app = A.App(None)
    app.load_file(str(save))
    app.update()
    yield app, save, raw, calls
    app.destroy()


def _dialog(app):
    return [w for w in app.winfo_children() if isinstance(w, A.PlanDialog)][-1]


def test_dry_run_writes_nothing_and_apply_needs_confirmation(gui):
    app, save, raw, calls = gui
    assert app.slot is not None and len(app.tree.get_children()) == 120
    app.v_action.set("SELL")
    app.v_cq.set(False)
    for t in ("delicate", "polished", "grand"):
        app.v_tier[t].set(True)
    app.preview()
    app.update()
    dlg = _dialog(app)
    assert app.plan.candidates and dlg.btn.instate(["disabled"])
    assert save.read_bytes() == raw and not (save.parent / "backups").exists()

    plan = app.plan
    dlg.v_ok.set(True)
    dlg._sync()
    dlg._apply()
    app.update()
    assert calls == ["showinfo"]
    assert (next((save.parent / "backups").glob("NR0000_backup_*.sl2"))).read_bytes() == raw
    assert app.slot.murks == plan.murk_new and len(app.tree.get_children()) == 120 - len(plan.candidates)


def test_blocked_plan_cannot_be_applied(gui):
    app, *_ = gui
    app.slot.equipment_error = "forced"
    app.slot.equipment_known = False
    app.infos = [app.gd.describe(r, False) for r in app.slot.relics]
    app.v_action.set("SELL")
    app.v_cq.set(False)
    app.v_tier["grand"].set(True)
    app.preview()
    app.update()
    dlg = _dialog(app)
    assert app.plan.blocked and dlg.btn.instate(["disabled"])


def test_picker_groups_and_group_z(gui):
    app, *_ = gui
    app.pick_effects(app.t_z, None)
    pk = [w for w in app.winfo_children() if isinstance(w, A.EffectPicker)][-1]
    groups = pk.tree.get_children()
    assert len(groups) > 10
    pk.tree.selection_set(groups[0])
    pk.add()
    assert len(A._lines(app.t_z)) > 3                      # a group row adds all its effects
    pk.v.set("poise +1")
    pk.update()
    leaf = pk.tree.get_children(pk.tree.get_children()[0])[0]
    pk.tree.selection_set(leaf)
    assert pk.selected_names() == ["Poise +1"]
    assert "group_z_affixes" in app.read_form()["filters"]["combination_query"]
    pk.destroy()


def test_logic_box(gui):
    app, *_ = gui
    app.v_expr.set("(X OR Y) AND NOT Z")
    app.update()
    assert app.read_form()["filters"]["combination_query"]["expression"] == "(X OR Y) AND NOT Z"
    assert app.lbl_expr.cget("text") == ""
    app.v_expr.set("X AND (")
    app.update()
    assert "problem" in app.lbl_expr.cget("text")
