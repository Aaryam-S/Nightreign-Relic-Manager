"""Nightreign Relic Manager: batch favorite / unfavorite / sell relics, with a dry-run review.

  python app.py                      open the GUI (auto-loads the newest save under %APPDATA%\\Nightreign)
  python app.py <NR0000.sl2>         open the GUI on a specific file
  python app.py --inspect <file>     READ-ONLY text summary; add --dump relics.json to export every relic

The GUI never writes anything until you press Apply in the dry-run dialog and confirm.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import filter_engine as fe
import theme
import relic_parser as rp
from game_data import GameData, RelicInfo
from operations import ApplyError, apply_plan, game_running
from save_io import CHARACTER_SLOT_COUNT, SaveFile, SaveFormatError, default_save_candidates

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:                                  # headless / no Tk: --inspect still works
    tk = None

# When frozen into an .exe (build_exe.bat), keep data/ and backups/ NEXT TO the exe, not inside its bundle.
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
BACKUPS_DIR = APP_DIR / "backups"
PRESETS_PATH = DATA_DIR / "rules_presets.json"

SAFETY_TEXT = (
    "Only edit saves you are willing to risk.\n\n"
    "- Close the game first. Never edit while it is running.\n"
    "- Keep your own manual copy of the save too; automatic backups go to the 'backups' folder next to this app.\n"
    "- Editing a save can get an account flagged online. Use offline / non-ranked saves.\n"
    "- A game update can change the save layout. If this tool reports a checksum or layout problem, stop and do not write."
)


# ---------------------------------------------------------------------------
# helpers shared by CLI and GUI
# ---------------------------------------------------------------------------
def relic_record(info: RelicInfo) -> dict:
    r = info.relic
    return {
        "ga_handle": f"0x{r.ga_handle:08X}", "relic_id": r.real_id, "name": info.name, "tier": info.tier,
        "color": info.color, "deep": info.deep, "salable": info.salable,
        "effects": [{"id": i, "name": n} for i, n in zip(info.effect_ids, info.effects)],
        "curses": [{"id": i, "name": n} for i, n in zip(info.curse_ids, info.curses)],
        "favorite": info.favorite, "equipped": info.equipped, "equipped_in": sorted(r.equipped_in),
    }


def inspect(path: str, dump: str | None, only_slot: int | None) -> int:
    gd = GameData(DATA_DIR)
    sf = SaveFile.load(path)
    print(f"{path}\n  {len(sf.entries)} container entries, {len(sf.raw):,} bytes, sha256 {sf.sha256[:16]}...")
    active = sf.active_slots()
    out: dict[str, list] = {}
    for i in range(CHARACTER_SLOT_COUNT):
        if not active[i] or (only_slot is not None and i != only_slot):
            continue
        ck = "ok" if sf.checksum_ok(i) else "MISMATCH (do not edit)"
        try:
            s = rp.Slot(sf.slot_data(i))
        except rp.LayoutError as exc:
            print(f"  slot {i}: layout not recognised: {exc}; checksum {ck}")
            continue
        eq = f"{s.equipped_count} equipped" if s.equipment_known else f"UNKNOWN ({s.equipment_error})"
        print(f"  slot {i}: {s.name!r}  Murk {s.murks:,}  relics {len(s.relics)}  favorites {s.favorite_count}  "
              f"equipment: {eq}  checksum {ck}")
        if s.orphan_relic_entries or s.entry_count_mismatch:
            print(f"           warnings: orphan relic entries={s.orphan_relic_entries}, "
                  f"entry count stored {s.stored_entry_count} vs counted {s.counted_entries}")
        out[str(i)] = [relic_record(gd.describe(r, s.equipment_known)) for r in s.relics]
    if dump:
        Path(dump).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote {sum(len(v) for v in out.values())} relics to {dump}")
    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
_Base = tk.Tk if tk else object
COLS = (("name", "Relic", 210), ("tier", "Tier", 70), ("color", "Color", 60), ("deep", "Deep", 45),
        ("effects", "Effects", 330), ("curses", "Curses", 230), ("fav", "Fav", 40), ("equipped", "Equipped", 80),
        ("sell", "Sellable", 65))


def _lines(widget) -> list[str]:
    return [ln.strip() for ln in widget.get("1.0", "end").splitlines() if ln.strip()]


def _set_lines(widget, items: list[str]) -> None:
    widget.delete("1.0", "end")
    widget.insert("1.0", "\n".join(items))


class ScrollFrame(ttk.Frame if tk else object):
    """A vertically scrollable container; put children in `.inner`."""

    def __init__(self, parent, width: int = 380):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, width=width)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _unbind_wheel(self, _e):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _wheel(self, e):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")


class App(_Base):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.title("Nightreign Relic Manager")
        self.geometry("1320x780")
        self.minsize(1050, 620)
        self.fonts = theme.apply(self)
        self.gd = GameData(DATA_DIR)
        self.presets, warn = fe.load_presets(PRESETS_PATH)
        if not self.presets:
            self.presets = [fe.default_preset("New preset")]
        self.sf: SaveFile | None = None
        self.path: Path | None = None
        self.slots: dict[int, rp.Slot | str] = {}
        self.slot_index: int | None = None
        self.slot: rp.Slot | None = None
        self.infos: list[RelicInfo] = []
        self.plan: fe.Plan | None = None
        self._sort: tuple[str | None, bool] = (None, False)

        self._build_menu()
        self.v_status = tk.StringVar(value=warn or "Open a save file to begin.")
        ttk.Label(self, textvariable=self.v_status, anchor="w", style="Status.TLabel").pack(side="bottom", fill="x")
        hdr = ttk.Frame(self)
        hdr.pack(side="top", fill="x", padx=12, pady=(10, 0))
        ttk.Label(hdr, text="NIGHTREIGN  \u2022  RELIC MANAGER", style="Title.TLabel").pack(side="left")
        ttk.Label(hdr, text="sort, favorite and sell with a dry run first", style="Sub.TLabel").pack(side="left", padx=14, pady=(6, 0))
        tk.Frame(self, height=1, bg=theme.GOLD).pack(side="top", fill="x", padx=12, pady=(6, 4))
        self._build_actions()
        self._build_top()
        self._build_body()
        self._load_preset_into_form(self.presets[0])

        path = initial_path or (str(default_save_candidates()[0]) if default_save_candidates() else None)
        if path:
            self.v_path.set(path)
            self.after(50, lambda: self.load_file(path))

    def report_callback_exception(self, exc, val, tb):
        import traceback
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            (APP_DIR / "crash.log").write_text(text, encoding="utf-8")
        except OSError:
            pass
        messagebox.showerror("Something went wrong", f"{val}\n\nDetails were saved to crash.log next to the app.\n"
                                                    "Nothing is written to your save unless an apply finished.")

    # ---- construction ------------------------------------------------------
    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=False)
        f.add_command(label="Open save...", command=self.browse)
        f.add_command(label="Reload", command=lambda: self.load_file(self.v_path.get()))
        f.add_command(label="Export relic list (JSON)...", command=self.export_json)
        f.add_separator()
        f.add_command(label="Quit", command=self.destroy)
        m.add_cascade(label="File", menu=f)
        s = tk.Menu(m, tearoff=False)
        s.add_command(label="Sell values...", command=self.edit_sell_values)
        m.add_cascade(label="Settings", menu=s)
        h = tk.Menu(m, tearoff=False)
        h.add_command(label="Safety notes", command=lambda: messagebox.showinfo("Safety notes", SAFETY_TEXT))
        m.add_cascade(label="Help", menu=h)
        self.config(menu=m)

    def _build_top(self):
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Save file:").grid(row=0, column=0, sticky="w")
        self.v_path = tk.StringVar()
        ttk.Entry(top, textvariable=self.v_path).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse...", command=self.browse).grid(row=0, column=2)
        ttk.Button(top, text="Load (read-only)", command=lambda: self.load_file(self.v_path.get())).grid(row=0, column=3, padx=(6, 0))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Character:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.v_slot = tk.StringVar()
        self.cb_slot = ttk.Combobox(top, textvariable=self.v_slot, state="readonly", width=44)
        self.cb_slot.grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        self.cb_slot.bind("<<ComboboxSelected>>", lambda e: self.select_slot())
        self.v_summary = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.v_summary, font=("TkDefaultFont", 10, "bold")).grid(row=1, column=2, columnspan=2, sticky="w", pady=(6, 0))
        self.v_warn = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.v_warn, style="Danger.TLabel", wraplength=1200, justify="left").grid(row=2, column=0, columnspan=4, sticky="w")

    def _build_body(self):
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=8, pady=4)
        left = ScrollFrame(pw, width=390)
        right = ttk.Frame(pw, padding=4)
        pw.add(left, weight=0)
        pw.add(right, weight=1)
        self._build_rules(left.inner)
        self._build_table(right)

    def _build_actions(self):
        """Always-visible bottom bar: options + the dry-run button."""
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(side="bottom", fill="x")
        self.v_allow_fav = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Allow selling favorites", variable=self.v_allow_fav).pack(side="left")
        self.v_credit = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Credit Murk for sold relics", variable=self.v_credit).pack(side="left", padx=(14, 0))
        ttk.Button(bar, text="Preview changes (dry run)...", style="Accent.TButton", command=self.preview).pack(side="right")
        ttk.Label(bar, text="Nothing is written until you confirm in the preview.").pack(side="right", padx=12)

    def _build_rules(self, parent):
        # preset bar
        bar = ttk.LabelFrame(parent, text="Rule preset", padding=6)
        bar.pack(fill="x")
        self.v_pname = tk.StringVar()
        self.cb_preset = ttk.Combobox(bar, textvariable=self.v_pname, width=30)
        self.cb_preset.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.cb_preset.bind("<<ComboboxSelected>>", lambda e: self._on_preset_selected())
        ttk.Button(bar, text="Save", command=self.save_preset).grid(row=1, column=0, pady=(4, 0), sticky="w")
        ttk.Button(bar, text="Delete", command=self.delete_preset).grid(row=1, column=1, pady=(4, 0), sticky="w")
        ttk.Button(bar, text="Clear", command=lambda: self._load_preset_into_form(fe.default_preset("New preset"))).grid(row=1, column=2, pady=(4, 0), sticky="w")
        bar.columnconfigure(0, weight=1)
        self._refresh_preset_list()

        # action
        act = ttk.LabelFrame(parent, text="When a relic matches", padding=6)
        act.pack(fill="x", pady=(6, 0))
        self.v_action = tk.StringVar(value="SELL")
        for i, (val, txt) in enumerate((("SELL", "Sell"), ("FAVORITE", "Favorite"), ("UNFAVORITE", "Unfavorite"))):
            ttk.Radiobutton(act, text=txt, value=val, variable=self.v_action).grid(row=0, column=i, padx=(0, 10))
        # combination query
        cq = ttk.LabelFrame(parent, text="Rule: effect groups + logic (effect names, one per line)", padding=6)
        cq.pack(fill="x", pady=(6, 0))
        self.v_cq = tk.BooleanVar()
        ttk.Checkbutton(cq, text="Enabled", variable=self.v_cq).grid(row=0, column=0, sticky="w")
        self.v_expr = tk.StringVar(value="X AND Y")
        self.cb_tpl = ttk.Combobox(cq, values=("X AND Y", "X OR Y", "X", "NOT X", "X AND Y AND Z", "X OR Y OR Z",
                                               "X AND Y AND NOT Z", "(X OR Y) AND Z", "X AND (Y OR Z)", "D", "X AND NOT D"),
                                   width=20, state="readonly")
        self.cb_tpl.set("Insert example logic...")
        self.cb_tpl.grid(row=0, column=1, columnspan=2, sticky="e")
        self.cb_tpl.bind("<<ComboboxSelected>>", lambda e: self.v_expr.set(self.cb_tpl.get()))
        ttk.Label(cq, text="Logic").grid(row=1, column=0, sticky="w")
        ttk.Entry(cq, textvariable=self.v_expr).grid(row=1, column=1, columnspan=2, sticky="ew")
        self.lbl_expr = ttk.Label(cq, text="", style="Danger.TLabel", wraplength=330)
        self.lbl_expr.grid(row=2, column=0, columnspan=3, sticky="w")
        self.v_expr.trace_add("write", lambda *a: self._check_expr())
        ttk.Label(cq, wraplength=330, style="Sub.TLabel",
                  text="Combine groups with AND, OR, NOT and ( ). Example: (X OR Y) AND NOT D").grid(row=3, column=0, columnspan=3, sticky="w")
        self.v_min = {}
        for r0, name in ((4, "X"), (6, "Y"), (8, "Z"), (10, "D")):
            t = f"t_{name.lower()}"
            ttk.Label(cq, text="Group D (demerits / curses)" if name == "D" else f"Group {name}").grid(row=r0, column=0, sticky="w")
            if name == "D":                       # only demerits use a "match at least N" count
                f0 = ttk.Frame(cq)
                f0.grid(row=r0, column=1, sticky="e")
                ttk.Label(f0, text="match at least").pack(side="left")
                self.v_min[name] = tk.IntVar(value=1)
                ttk.Spinbox(f0, from_=1, to=6, width=3, textvariable=self.v_min[name]).pack(side="left", padx=4)
            ttk.Button(cq, text="Pick...", width=6,
                       command=lambda t=t, d=(True if name == "D" else None): self.pick_effects(getattr(self, t), d)).grid(row=r0, column=2, sticky="e")
            w = tk.Text(cq, height=3, width=40, wrap="none")
            w.grid(row=r0 + 1, column=0, columnspan=3, sticky="ew")
            setattr(self, t, w)
        cq.columnconfigure(1, weight=1)

        # attributes
        at = ttk.LabelFrame(parent, text="Limit to (leave empty = any)", padding=6)
        at.pack(fill="x", pady=(6, 0))
        self.v_tier = {t: tk.BooleanVar() for t in ("delicate", "polished", "grand")}
        for i, t in enumerate(self.v_tier):
            ttk.Checkbutton(at, text=t.title(), variable=self.v_tier[t]).grid(row=0, column=i, sticky="w")
        self.v_color = {c: tk.BooleanVar() for c in fe.COLOR_NAMES[:4]}
        for i, c in enumerate(self.v_color):
            ttk.Checkbutton(at, text=c, variable=self.v_color[c]).grid(row=1, column=i, sticky="w")
        ttk.Label(at, text="Deep relics:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.v_deep = tk.StringVar(value="any")
        ttk.Combobox(at, textvariable=self.v_deep, values=fe.DEEP_MODES, state="readonly", width=8).grid(row=2, column=1, pady=(4, 0), sticky="w")

    def _build_table(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        ttk.Label(bar, text="Find:").pack(side="left")
        self.v_find = tk.StringVar()
        self.v_find.trace_add("write", lambda *a: self.refresh_table())
        ttk.Entry(bar, textvariable=self.v_find, width=34).pack(side="left", padx=4)
        self.v_count = tk.StringVar()
        ttk.Label(bar, textvariable=self.v_count).pack(side="right")
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(4, 0))
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in COLS], show="headings", selectmode="extended")
        for key, title, width in COLS:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, stretch=key in ("effects", "curses"), anchor="w")
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("cand_sell", background=theme.SELL_ROW)
        self.tree.tag_configure("cand_fav", background=theme.FAV_ROW)
        self.tree.tag_configure("skip", foreground=theme.MUTED)
        self.tree.tag_configure("equipped", foreground=theme.EQUIP)

    # ---- loading -----------------------------------------------------------
    def set_status(self, msg: str):
        self.v_status.set(msg)

    def browse(self):
        p = filedialog.askopenfilename(title="Open Nightreign save", filetypes=[("Save files", "*.sl2 *.co2"), ("All files", "*")])
        if p:
            self.v_path.set(p)
            self.load_file(p)

    def load_file(self, path: str, keep_slot: int | None = None):
        path = (path or "").strip().strip('"')
        if not path:
            return
        try:
            sf = SaveFile.load(path)
            active = sf.active_slots()
        except (OSError, SaveFormatError) as exc:
            messagebox.showerror("Cannot open save", str(exc))
            return
        self.sf, self.path = sf, Path(path)
        self.slots = {}
        for i in range(CHARACTER_SLOT_COUNT):
            if not active[i]:
                continue
            try:
                self.slots[i] = rp.Slot(sf.slot_data(i))
            except rp.LayoutError as exc:
                self.slots[i] = str(exc)
        labels = []
        for i, s in self.slots.items():
            labels.append(f"Slot {i}: {s.name or '(no name)'}  -  {len(s.relics)} relics" if isinstance(s, rp.Slot) else f"Slot {i}: unreadable")
        self.cb_slot["values"] = labels
        readable = [i for i, s in self.slots.items() if isinstance(s, rp.Slot)]
        if not readable:
            self.v_slot.set("")
            self.slot = None
            self.infos = []
            self.refresh_table()
            self.v_summary.set("")
            self.v_warn.set("No readable character slot found. The save layout may have changed; do not edit this file.")
            return
        pick = keep_slot if keep_slot in readable else readable[0]
        self.v_slot.set(labels[list(self.slots).index(pick)])
        self.select_slot(pick)
        self.set_status(f"Loaded {self.path.name} (read-only until you apply a plan).")

    def select_slot(self, index: int | None = None):
        if index is None:
            sel = self.cb_slot.current()
            index = list(self.slots)[sel] if sel >= 0 else None
        s = self.slots.get(index)
        if not isinstance(s, rp.Slot):
            self.v_warn.set(f"Slot {index} could not be parsed: {s}")
            return
        self.slot_index, self.slot, self.plan = index, s, None
        self.infos = [self.gd.describe(r, s.equipment_known) for r in s.relics]
        eq = f"{s.equipped_count} equipped" if s.equipment_known else "equipment UNKNOWN"
        self.v_summary.set(f"{s.name}   |   Murk {s.murks:,}   |   {len(s.relics)} relics ({eq}, {s.favorite_count} favorites)")
        warns = []
        if not self.sf.checksum_ok(index):
            warns.append("This slot's checksum does not verify; writing is disabled.")
        if not s.equipment_known:
            warns.append(f"{s.equipment_error} Selling is disabled for this slot.")
        if s.orphan_relic_entries:
            warns.append(f"{s.orphan_relic_entries} relic entries have no matching record and are ignored.")
        if s.entry_count_mismatch:
            warns.append(f"Inventory count mismatch (stored {s.stored_entry_count}, counted {s.counted_entries}).")
        self.v_warn.set("  ".join(warns))
        self.refresh_table()

    # ---- table -------------------------------------------------------------
    def _row(self, info: RelicInfo) -> tuple:
        sellable = "unique" if info.unique else ("yes" if (info.salable and info.tier) else "no")
        eq = "?" if info.equipped is None else (info.equipped_where if info.equipped else "")
        return (info.name, (info.tier or "").title(), info.color, "yes" if info.deep else "",
                " | ".join(info.effects), " | ".join(info.curses), "★" if info.favorite else "", eq, sellable)

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        q = self.v_find.get().strip().casefold()
        cand = {i.relic.ga_handle for i in (self.plan.candidates if self.plan else [])}
        skip = {i.relic.ga_handle for i, _ in (self.plan.skipped if self.plan else [])}
        rows = []
        for info in self.infos:
            vals = self._row(info)
            if q and q not in " ".join(vals).casefold():
                continue
            tags = []
            ga = info.relic.ga_handle
            if ga in cand:
                tags.append("cand_sell" if self.plan.action == "SELL" else "cand_fav")
            elif ga in skip:
                tags.append("skip")
            if info.equipped:
                tags.append("equipped")
            rows.append((vals, f"{ga:08X}", tuple(tags)))
        key, rev = self._sort
        if key:
            idx = [c[0] for c in COLS].index(key)
            rows.sort(key=lambda r: r[0][idx].casefold(), reverse=rev)
        for vals, iid, tags in rows:
            self.tree.insert("", "end", iid=iid, values=vals, tags=tags)
        extra = f"   (highlighted: {len(cand)} will change)" if self.plan else ""
        self.v_count.set(f"{len(rows)} of {len(self.infos)} relics shown{extra}")

    def sort_by(self, key: str):
        self._sort = (key, (not self._sort[1]) if self._sort[0] == key else False)
        self.refresh_table()

    def export_json(self):
        if not self.slot:
            return
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="relics.json")
        if p:
            Path(p).write_text(json.dumps([relic_record(i) for i in self.infos], indent=2, ensure_ascii=False), encoding="utf-8")
            self.set_status(f"Exported {len(self.infos)} relics to {p}")

    # ---- presets -----------------------------------------------------------
    def _refresh_preset_list(self):
        self.cb_preset["values"] = [p["preset_name"] for p in self.presets]

    def _on_preset_selected(self):
        name = self.v_pname.get()
        p = next((p for p in self.presets if p["preset_name"] == name), None)
        if p:
            self._load_preset_into_form(p)

    def _load_preset_into_form(self, p: dict):
        p = fe.normalize_preset(p)
        f = p["filters"]
        self.v_pname.set(p["preset_name"])
        self.v_action.set(p["target_action"])
        self.v_cq.set(f["combination_query"]["enabled"])
        self.v_expr.set(fe.effective_expression(f["combination_query"]))
        _set_lines(self.t_x, f["combination_query"]["group_x_affixes"])
        _set_lines(self.t_y, f["combination_query"]["group_y_affixes"])
        _set_lines(self.t_z, f["combination_query"]["group_z_affixes"])
        _set_lines(self.t_d, f["combination_query"]["group_d_affixes"])
        for g, v in self.v_min.items():
            v.set(f["combination_query"]["min_counts"].get(g, 1))
        for t, v in self.v_tier.items():
            v.set(t in f["attributes"]["tiers"])
        for c, v in self.v_color.items():
            v.set(c in f["attributes"]["colors"])
        self.v_deep.set(f["attributes"]["deep"])
        self.plan = None
        self.refresh_table()

    def read_form(self) -> dict:
        mins = {}
        for g, v in self.v_min.items():
            try:
                mins[g] = int(v.get())
            except (tk.TclError, ValueError):
                mins[g] = 1
        return fe.normalize_preset({
            "preset_name": self.v_pname.get().strip() or "Unnamed",
            "target_action": self.v_action.get(),
            "filters": {
                "combination_query": {"enabled": self.v_cq.get(), "group_x_affixes": _lines(self.t_x),
                                      "group_y_affixes": _lines(self.t_y), "group_z_affixes": _lines(self.t_z),
                                      "group_d_affixes": _lines(self.t_d), "min_counts": mins,
                                      "expression": self.v_expr.get().strip()},
                "attributes": {"tiers": [t for t, v in self.v_tier.items() if v.get()],
                               "colors": [c for c, v in self.v_color.items() if v.get()],
                               "deep": self.v_deep.get()},
            },
        })

    def save_preset(self):
        p = self.read_form()
        for i, old in enumerate(self.presets):
            if old["preset_name"] == p["preset_name"]:
                if not messagebox.askyesno("Overwrite preset", f"Replace the existing preset '{p['preset_name']}'?"):
                    return
                self.presets[i] = p
                break
        else:
            self.presets.append(p)
        try:
            fe.save_presets(PRESETS_PATH, self.presets)
        except OSError as exc:
            messagebox.showerror("Could not save presets", str(exc))
            return
        self._refresh_preset_list()
        self.set_status(f"Saved preset '{p['preset_name']}'.")

    def delete_preset(self):
        name = self.v_pname.get()
        if name not in [p["preset_name"] for p in self.presets]:
            return
        if not messagebox.askyesno("Delete preset", f"Delete preset '{name}'?"):
            return
        self.presets = [p for p in self.presets if p["preset_name"] != name]
        fe.save_presets(PRESETS_PATH, self.presets)
        self._refresh_preset_list()
        self._load_preset_into_form(self.presets[0] if self.presets else fe.default_preset("New preset"))

    def _check_expr(self):
        try:
            fe.compile_expression(self.v_expr.get().strip())
            self.lbl_expr.configure(text="")
        except ValueError as exc:
            self.lbl_expr.configure(text=f"Logic problem: {exc}")

    def pick_effects(self, target, debuff):
        seen: set[str] = set()
        for i in self.infos:
            if debuff is not True:
                seen.update(i.effects)
            if debuff is not False:
                seen.update(i.curses)
        EffectPicker(self, target, self.gd.effect_catalog(debuff, found_on_relics=sorted(seen)))

    def edit_sell_values(self):
        SellValuesDialog(self)

    # ---- dry run / apply -----------------------------------------------------
    def preview(self):
        if not self.slot:
            messagebox.showinfo("No save loaded", "Load a save file first.")
            return
        plan = fe.build_plan(self.infos, self.read_form(), self.gd, self.slot.murks,
                             equipment_known=self.slot.equipment_known,
                             allow_favorites=self.v_allow_fav.get(), credit_murk=self.v_credit.get())
        self.plan = plan
        self.refresh_table()
        PlanDialog(self, plan)

    def apply(self, plan: fe.Plan) -> bool:
        if game_running() and not messagebox.askyesno(
                "Game appears to be running",
                "Nightreign seems to be running. Editing a save while the game is open can corrupt it.\n\nApply anyway?"):
            return False
        try:
            res = apply_plan(self.path, self.slot_index, self.sf.sha256, plan, BACKUPS_DIR)
        except ApplyError as exc:
            messagebox.showerror("Nothing was changed" if "restored" not in str(exc) else "Write failed", str(exc))
            return False
        keep = self.slot_index
        self.plan = None
        self.load_file(str(self.path), keep_slot=keep)
        verb = {"SELL": "sold", "FAVORITE": "favorited", "UNFAVORITE": "unfavorited"}[plan.action]
        extra = f"\nMurk: {res.murk_before:,} -> {res.murk_after:,}" if plan.action == "SELL" and plan.credit_murk else ""
        messagebox.showinfo("Done", f"{res.changed} relics {verb}.{extra}\n\nBackup saved to:\n{res.backup}")
        self.set_status(f"Applied. Backup: {res.backup}")
        return True


class PlanDialog(tk.Toplevel if tk else object):
    def __init__(self, app: App, plan: fe.Plan):
        super().__init__(app)
        self.app, self.plan = app, plan
        self.title("Dry run: review before anything is written")
        self.geometry("900x640")
        self.transient(app)
        self.grab_set()
        pad = {"padx": 10}

        head = ttk.Frame(self, padding=10)
        head.pack(fill="x")
        lines = [f"Relics scanned: {plan.scanned:,}",
                 f"Protected (equipped): {plan.equipped_count:,}",
                 f"Favorites: {plan.favorite_count:,}",
                 f"Matched by rule: {plan.matched_total:,}   (skipped: {len(plan.skipped):,})"]
        for i, t in enumerate(lines):
            ttk.Label(head, text=t).grid(row=i, column=0, sticky="w")
        big = ("TkDefaultFont", 11, "bold")
        if plan.action == "SELL":
            ttk.Label(head, text=f"Relics to be sold: {len(plan.candidates):,}", font=big).grid(row=0, column=1, sticky="w", padx=40)
            est = "" if plan.sell_values_verified else "  (estimated)"
            ttk.Label(head, text=f"Total Murk payout: +{plan.payout:,} Murk{est}", font=big).grid(row=1, column=1, sticky="w", padx=40)
            bal = f"Murk balance: {plan.murk_current:,} -> {plan.murk_new:,}" if plan.credit_murk else f"Murk balance: {plan.murk_current:,} (not credited)"
            ttk.Label(head, text=bal, font=big).grid(row=2, column=1, sticky="w", padx=40)
            tiers = ", ".join(f"{n} {t}" for t, n in sorted(plan.tier_counts.items())) or "-"
            ttk.Label(head, text=f"By tier: {tiers}").grid(row=3, column=1, sticky="w", padx=40)
        else:
            verb = "favorited" if plan.action == "FAVORITE" else "unfavorited"
            ttk.Label(head, text=f"Relics to be {verb}: {len(plan.candidates):,}", font=big).grid(row=0, column=1, sticky="w", padx=40)

        msgs = ([plan.blocked] if plan.blocked else []) + plan.warnings
        if msgs:
            ttk.Label(self, text="\n".join(msgs), style="Danger.TLabel", wraplength=860, justify="left").pack(fill="x", **pad)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=6)
        self._list(nb, f"Will change ({len(plan.candidates)})", [(i, "") for i in plan.candidates], reason=False)
        self._list(nb, f"Skipped ({len(plan.skipped)})", plan.skipped, reason=True)

        foot = ttk.Frame(self, padding=10)
        foot.pack(fill="x")
        self.v_ok = tk.BooleanVar(value=False)
        who = app.slot.name if app.slot else "this character"
        cb = ttk.Checkbutton(foot, variable=self.v_ok, command=self._sync,
                             text=f"I closed the game and want to write these changes to {who}'s save (a backup is made first).")
        cb.grid(row=0, column=0, columnspan=3, sticky="w")
        self.btn = ttk.Button(foot, text="Apply changes", style="Accent.TButton", command=self._apply)
        self.btn.grid(row=1, column=1, sticky="e", pady=(8, 0))
        ttk.Button(foot, text="Cancel", command=self.destroy).grid(row=1, column=2, sticky="e", padx=(6, 0), pady=(8, 0))
        foot.columnconfigure(0, weight=1)
        if not plan.can_apply:
            cb.state(["disabled"])
        self._sync()

    def _list(self, nb, title, items, reason):
        f = ttk.Frame(nb)
        nb.add(f, text=title)
        cols = ("name", "tier", "effects", "curses") + (("reason",) if reason else ())
        t = ttk.Treeview(f, columns=cols, show="headings")
        widths = {"name": 190, "tier": 65, "effects": 300, "curses": 200, "reason": 140}
        for c in cols:
            t.heading(c, text=c.title())
            t.column(c, width=widths[c], stretch=c in ("effects", "curses"))
        for info, why in items:
            row = [info.name, (info.tier or "").title(), " | ".join(info.effects), " | ".join(info.curses)]
            t.insert("", "end", values=row + ([why] if reason else []))
        ys = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=ys.set)
        t.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")

    def _sync(self):
        self.btn.state(["!disabled"] if (self.plan.can_apply and self.v_ok.get()) else ["disabled"])

    def _apply(self):
        if self.app.apply(self.plan):
            self.destroy()


class EffectPicker(tk.Toplevel if tk else object):
    """Grouped effect chooser. Pick a group row to add everything in it; type to filter."""

    def __init__(self, app: App, target, catalog: list[tuple[str, list[str]]]):
        super().__init__(app)
        self.target, self.catalog = target, catalog
        self.title("Pick effects")
        self.geometry("620x600")
        self.transient(app)
        self.v = tk.StringVar()
        self.v.trace_add("write", lambda *a: self._fill())
        ttk.Entry(self, textvariable=self.v).pack(fill="x", padx=8, pady=(8, 4))
        fr = ttk.Frame(self)
        fr.pack(fill="both", expand=True, padx=8)
        self.tree = ttk.Treeview(fr, show="tree", selectmode="extended")
        ys = ttk.Scrollbar(fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.bind("<Double-Button-1>", lambda e: self.after_idle(self.add))
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Add selected", command=self.add).pack(side="left")
        ttk.Label(bar, text="  (a group row adds all of its effects)").pack(side="left")
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self._fill()

    def _fill(self):
        q = self.v.get().strip().casefold()
        self.tree.delete(*self.tree.get_children())
        for gi, (group, names) in enumerate(self.catalog):
            hit = [n for n in names if q in n.casefold()]
            if not hit:
                continue
            node = self.tree.insert("", "end", iid=f"g{gi}", text=f"{group}  ({len(hit)})", open=bool(q))
            for n in hit:
                self.tree.insert(node, "end", text=n, values=(n,))

    def selected_names(self) -> list[str]:
        out: list[str] = []
        for iid in self.tree.selection():
            kids = self.tree.get_children(iid)
            for k in (kids or (iid,)):
                v = self.tree.item(k, "values")
                if v and v[0] not in out:
                    out.append(str(v[0]))
        return out

    def add(self):
        have = _lines(self.target)
        for n in self.selected_names():
            if n not in have:
                have.append(n)
        _set_lines(self.target, have)


class SellValuesDialog(tk.Toplevel if tk else object):
    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("Sell values")
        self.transient(app)
        self.grab_set()
        ttk.Label(self, wraplength=360, justify="left", text=(
            "Murk paid per sold relic, by tier. The reference data has no sell prices, so the defaults are "
            "unverified placeholders. Check the real prices at the Relic Rites in-game, enter them here, "
            "then tick 'verified'.")).grid(row=0, column=0, columnspan=2, padx=10, pady=10)
        self.vars = {}
        for i, t in enumerate(("delicate", "polished", "grand")):
            ttk.Label(self, text=t.title()).grid(row=i + 1, column=0, sticky="w", padx=10)
            self.vars[t] = tk.IntVar(value=app.gd.sell_values.get(t, 0))
            ttk.Spinbox(self, from_=0, to=1000000, textvariable=self.vars[t], width=10).grid(row=i + 1, column=1, sticky="w")
        self.ver = tk.BooleanVar(value=app.gd.sell_values_verified)
        ttk.Checkbutton(self, text="These match the in-game prices (verified)", variable=self.ver).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=8)
        bar = ttk.Frame(self)
        bar.grid(row=5, column=0, columnspan=2, pady=(0, 10))
        ttk.Button(bar, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def save(self):
        gd = self.app.gd
        try:
            data = gd.load_params()
            data["sell_values"] = {t: max(0, int(v.get())) for t, v in self.vars.items()}
            data["sell_values_verified"] = bool(self.ver.get())
            gd.params_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        gd.sell_values = {k: int(v) for k, v in data["sell_values"].items()}
        gd.sell_values_verified = data["sell_values_verified"]
        self.app.plan = None
        self.app.refresh_table()
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nightreign Relic Manager")
    ap.add_argument("save", nargs="?", help="path to NR0000.sl2 / .co2")
    ap.add_argument("--inspect", metavar="FILE", help="read-only text summary, no GUI")
    ap.add_argument("--dump", metavar="JSON", help="with --inspect: write every relic to this JSON file")
    ap.add_argument("--slot", type=int, help="with --inspect: only this character slot")
    a = ap.parse_args(argv)
    if a.inspect:
        try:
            return inspect(a.inspect, a.dump, a.slot)
        except (OSError, SaveFormatError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if tk is None:
        print("Tkinter is not available in this Python. Install the 'tk' package (or use --inspect).", file=sys.stderr)
        return 1
    try:
        app = App(a.save)
        app.mainloop()
    except Exception:                                  # the launcher uses pythonw (no console), so keep a record
        import traceback
        (APP_DIR / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror("Relic Manager crashed", f"Details were written to {APP_DIR / 'crash.log'}")
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
