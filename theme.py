"""Dark, gold-trimmed look inspired by the game's menus. Pure colours and fonts: no game art is used or shipped."""
from __future__ import annotations

BG = "#0e1015"        # window
PANEL = "#161a22"     # frames
PANEL2 = "#1d222d"    # inputs, rows
LINE = "#3a3526"      # borders
GOLD = "#c9a96b"
GOLD_HI = "#e8cf94"
TEXT = "#e4dfd0"
MUTED = "#8b8676"
DANGER = "#d9605a"
SELL_ROW = "#3b1d22"
FAV_ROW = "#1f3a2b"
EQUIP = "#8db4e6"
SELECT = "#4a3f22"

SERIF = ("Georgia", "Palatino Linotype", "Times New Roman", "DejaVu Serif")
SANS = ("Segoe UI", "Helvetica", "DejaVu Sans")


def _family(root, names):
    import tkinter.font as tkfont
    have = {f.casefold() for f in tkfont.families(root)}
    return next((n for n in names if n.casefold() in have), names[-1])


def apply(root) -> dict:
    """Style ttk + classic tk widgets on `root`. Returns fonts for callers that want them."""
    from tkinter import ttk
    import tkinter.font as tkfont
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    serif, sans = _family(root, SERIF), _family(root, SANS)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=sans, size=10)
        except Exception:
            pass
    heading = (serif, 10, "bold")
    root.configure(bg=BG)
    root.option_add("*Toplevel.background", BG)
    root.option_add("*Text.background", PANEL2)
    root.option_add("*Text.foreground", TEXT)
    root.option_add("*Text.insertBackground", GOLD)
    root.option_add("*Text.selectBackground", SELECT)
    root.option_add("*Text.selectForeground", GOLD_HI)
    root.option_add("*Text.relief", "flat")
    root.option_add("*Text.highlightThickness", 1)
    root.option_add("*Text.highlightBackground", LINE)
    root.option_add("*Text.highlightColor", GOLD)
    root.option_add("*Canvas.background", BG)
    root.option_add("*Menu.background", PANEL)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", SELECT)
    root.option_add("*Menu.activeForeground", GOLD_HI)
    root.option_add("*TCombobox*Listbox.background", PANEL2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", GOLD_HI)

    st.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL2, bordercolor=LINE,
                 lightcolor=LINE, darkcolor=LINE, troughcolor=PANEL, focuscolor=GOLD, insertcolor=GOLD)
    st.configure("TFrame", background=BG)
    st.configure("TLabel", background=BG, foreground=TEXT)
    st.configure("Title.TLabel", font=(serif, 17, "bold"), foreground=GOLD, background=BG)
    st.configure("Sub.TLabel", foreground=MUTED, background=BG)
    st.configure("Danger.TLabel", foreground=DANGER, background=BG)
    st.configure("TLabelframe", background=BG, bordercolor=LINE, relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=BG, foreground=GOLD, font=heading)
    # frames/labels placed inside a labelframe should blend with it
    st.configure("Card.TFrame", background=PANEL)
    st.configure("TButton", background=PANEL2, foreground=GOLD, bordercolor=LINE, padding=(10, 4), relief="flat")
    st.map("TButton", background=[("active", SELECT), ("disabled", PANEL)], foreground=[("active", GOLD_HI), ("disabled", MUTED)],
           bordercolor=[("focus", GOLD), ("active", GOLD)])
    st.configure("Accent.TButton", background=SELECT, foreground=GOLD_HI, bordercolor=GOLD, font=(serif, 10, "bold"), padding=(14, 6))
    st.map("Accent.TButton", background=[("active", "#6a5a2e"), ("disabled", PANEL)], foreground=[("disabled", MUTED)])
    st.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT, bordercolor=LINE, insertcolor=GOLD)
    st.map("TEntry", bordercolor=[("focus", GOLD)])
    st.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT, arrowcolor=GOLD, bordercolor=LINE)
    st.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", TEXT)], bordercolor=[("focus", GOLD)])
    st.configure("TSpinbox", fieldbackground=PANEL2, foreground=TEXT, arrowcolor=GOLD, bordercolor=LINE)
    for w in ("TCheckbutton", "TRadiobutton"):
        # clam draws the tick/dot with indicatorforeground (black by default = invisible on a dark box)
        st.configure(w, background=BG, foreground=TEXT, indicatorbackground=PANEL2, indicatorforeground=GOLD_HI,
                     upperbordercolor=GOLD, lowerbordercolor=GOLD, indicatormargin=(2, 2, 6, 2))
        st.map(w, indicatorbackground=[("pressed", SELECT), ("selected", SELECT), ("!selected", PANEL2)],
               indicatorforeground=[("disabled", MUTED), ("selected", GOLD_HI)],
               upperbordercolor=[("active", GOLD_HI)], lowerbordercolor=[("active", GOLD_HI)],
               background=[("active", BG)], foreground=[("active", GOLD_HI)])
    st.configure("Treeview", background=PANEL2, fieldbackground=PANEL2, foreground=TEXT, bordercolor=LINE, rowheight=22)
    st.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", GOLD_HI)])
    st.configure("Treeview.Heading", background=PANEL, foreground=GOLD, font=heading, relief="flat", bordercolor=LINE)
    st.map("Treeview.Heading", background=[("active", SELECT)])
    for o in ("Vertical", "Horizontal"):
        st.configure(f"{o}.TScrollbar", background=PANEL2, troughcolor=PANEL, bordercolor=LINE, arrowcolor=GOLD)
        st.map(f"{o}.TScrollbar", background=[("active", SELECT)])
    st.configure("TNotebook", background=BG, bordercolor=LINE, tabmargins=(2, 4, 2, 0))
    st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(12, 4), bordercolor=LINE)
    st.map("TNotebook.Tab", background=[("selected", SELECT), ("active", PANEL2)], foreground=[("selected", GOLD_HI), ("active", GOLD)])
    st.configure("TPanedwindow", background=BG)
    st.configure("Sash", background=LINE, sashthickness=5)
    st.configure("TSeparator", background=LINE)
    st.configure("Status.TLabel", background=PANEL, foreground=MUTED, padding=(8, 3))
    return {"serif": serif, "sans": sans}
