"""Rule/preset engine: decides which relics a preset matches and what a dry-run would change.

Nothing here touches save bytes. `build_plan` is pure and is what the dry-run dialog shows.
"""
from __future__ import annotations

import copy
import re
from functools import lru_cache
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from game_data import GameData, RelicInfo, TIERS, norm

ACTIONS = ("SELL", "FAVORITE", "UNFAVORITE")
MATCH_MODES = ("ANY_X_AND_ANY_Y", "ANY_X_OR_ANY_Y", "ANY_X", "NONE_OF_X",
               "ANY_X_AND_ANY_Y_AND_ANY_Z", "ANY_X_OR_ANY_Y_OR_ANY_Z", "ANY_X_AND_ANY_Y_NO_Z")
Z_MODES = ("ANY_X_AND_ANY_Y_AND_ANY_Z", "ANY_X_OR_ANY_Y_OR_ANY_Z", "ANY_X_AND_ANY_Y_NO_Z")
COMBINE = ("AND", "OR")
GROUP_NAMES = ("X", "Y", "Z", "D")      # D = demerits (curse slots only)
DEEP_MODES = ("any", "only", "exclude")
COLOR_NAMES = ("Red", "Blue", "Yellow", "Green", "White")


def default_preset(name: str = "New preset") -> dict:
    return {
        "preset_name": name,
        "filters": {
            "combination_query": {"enabled": False, "group_x_affixes": [], "group_y_affixes": [], "group_z_affixes": [], "group_d_affixes": [], "min_counts": {},
                                  "expression": "",
                                  "match_mode": "ANY_X_AND_ANY_Y"},
            "attributes": {"tiers": [], "colors": [], "deep": "any"},
        },
        "target_action": "FAVORITE",
    }


def normalize_preset(p: dict) -> dict:
    """Fill defaults and clamp enums so a hand-edited preset can never crash the engine.

    Old presets (separate demerit rule + "combine") are converted: the demerit list becomes Group D, its
    threshold becomes D's minimum count, and the logic expression joins the two with the old AND/OR.
    """
    base = default_preset(str(p.get("preset_name") or "Unnamed"))
    f = p.get("filters") or {}
    cq, at = (f.get("combination_query") or {}), (f.get("attributes") or {})
    bd = f.get("bad_demerit_rule") or {}
    base["target_action"] = p.get("target_action") if p.get("target_action") in ACTIONS else "FAVORITE"
    q = base["filters"]["combination_query"]
    q["enabled"] = bool(cq.get("enabled", False))
    for g in GROUP_NAMES:
        q[f"group_{g.lower()}_affixes"] = [str(x) for x in cq.get(f"group_{g.lower()}_affixes", [])]
    q["expression"] = str(cq.get("expression") or "").strip()
    q["match_mode"] = cq.get("match_mode") if cq.get("match_mode") in MATCH_MODES else "ANY_X_AND_ANY_Y"
    mc = cq.get("min_counts") if isinstance(cq.get("min_counts"), dict) else {}
    for g in GROUP_NAMES:
        try:
            q["min_counts"][g] = max(1, min(6, int(mc.get(g, 1))))
        except (TypeError, ValueError):
            q["min_counts"][g] = 1
    if bd and "group_d_affixes" not in cq:                       # migrate a pre-Group-D preset
        q["group_d_affixes"] = [str(x) for x in bd.get("bad_effects_list", [])]
        try:
            q["min_counts"]["D"] = max(1, min(6, int(bd.get("demerit_count_threshold", 1))))
        except (TypeError, ValueError):
            pass
        if bd.get("enabled"):
            if q["enabled"]:
                join = "OR" if p.get("combine") == "OR" else "AND"
                q["expression"] = f"({effective_expression(q)}) {join} D"
            else:
                q["expression"], q["enabled"] = "D", True
    a = base["filters"]["attributes"]
    a["tiers"] = [t for t in (str(x).lower() for x in at.get("tiers", [])) if t in TIERS]
    a["colors"] = [c for c in (str(x).title() for x in at.get("colors", [])) if c in COLOR_NAMES]
    a["deep"] = at.get("deep") if at.get("deep") in DEEP_MODES else "any"
    return base


# -- preset files ------------------------------------------------------------
def load_presets(path: Path) -> tuple[list[dict], str | None]:
    """Returns (presets, warning). A corrupt file is moved aside, never overwritten silently."""
    path = Path(path)
    if not path.exists():
        return [], None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("presets", [raw]) if isinstance(raw, dict) else raw
        return [normalize_preset(p) for p in items if isinstance(p, dict)], None
    except (OSError, ValueError, AttributeError) as exc:
        aside = path.with_name(f"{path.stem}.corrupt_{time.strftime('%Y%m%d_%H%M%S')}{path.suffix}")
        try:
            shutil.move(str(path), str(aside))
        except OSError:
            aside = path
        return [], f"Could not read {path.name} ({exc}); kept a copy as {aside.name}."


def save_presets(path: Path, presets: list[dict]) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"presets": presets}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# -- matching ----------------------------------------------------------------
def _keys(info: RelicInfo) -> set[str]:
    names = {norm(n) for n in info.effects + info.curses}
    ids = {str(i) for i in info.effect_ids + info.curse_ids}
    return names | ids


LEGACY_EXPR = {
    "ANY_X_AND_ANY_Y": "X AND Y", "ANY_X_OR_ANY_Y": "X OR Y", "ANY_X": "X", "NONE_OF_X": "NOT X",
    "ANY_X_AND_ANY_Y_AND_ANY_Z": "X AND Y AND Z", "ANY_X_OR_ANY_Y_OR_ANY_Z": "X OR Y OR Z",
    "ANY_X_AND_ANY_Y_NO_Z": "X AND Y AND NOT Z",
}


def effective_expression(q: dict) -> str:
    """The logic expression in force: the user's own, or the one a legacy match_mode stands for."""
    return (q.get("expression") or "").strip() or LEGACY_EXPR.get(q.get("match_mode"), "X AND Y")


def _parse(text: str):
    """Expression -> AST. Grammar: or := and (OR and)*; and := not (AND not)*; not := NOT not | ( or ) | group."""
    toks = re.findall(r"\(|\)|[^\s()]+", text)
    if not toks:
        raise ValueError("the logic expression is empty")
    pos = 0

    def peek():
        return toks[pos].upper() if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1].upper()

    def p_or():
        node = p_and()
        while peek() == "OR":
            take()
            node = ("or", node, p_and())
        return node

    def p_and():
        node = p_not()
        while peek() == "AND":
            take()
            node = ("and", node, p_not())
        return node

    def p_not():
        t = peek()
        if t == "NOT":
            take()
            return ("not", p_not())
        if t == "(":
            take()
            node = p_or()
            if peek() != ")":
                raise ValueError("missing closing parenthesis")
            take()
            return node
        if t in GROUP_NAMES:
            take()
            return ("g", t)
        raise ValueError(f"unexpected {toks[pos]!r}" if t is not None else "the expression ends too early")

    node = p_or()
    if pos != len(toks):
        raise ValueError(f"unexpected {toks[pos]!r}")
    return node


def _dnf(node, neg=False):
    """OR of ANDs of (group, negated) literals, via De Morgan."""
    kind = node[0]
    if kind == "g":
        return [[(node[1], neg)]]
    if kind == "not":
        return _dnf(node[1], not neg)
    a, b = _dnf(node[1], neg), _dnf(node[2], neg)
    conj = (kind == "and") != neg                      # NOT(a AND b) == NOT a OR NOT b, and vice versa
    out = [ca + cb for ca in a for cb in b] if conj else a + b
    if len(out) > 64:
        raise ValueError("the expression is too complicated")
    return out


@lru_cache(maxsize=256)
def compile_expression(text: str):
    """Returns the DNF of `text`; raises ValueError with a readable reason."""
    return tuple(tuple(c) for c in _dnf(_parse(text)))


def _slots(info: RelicInfo) -> list[tuple[set[str], bool]]:
    """(key-set of normalised name + id, is_curse) per effect/curse slot of the relic."""
    names, ids = info.effects + info.curses, list(info.effect_ids + info.curse_ids)
    ids += [None] * (len(names) - len(ids))
    return [({norm(n)} | ({str(i)} if i is not None else set()), k >= len(info.effects))
            for k, (n, i) in enumerate(zip(names, ids))]


def _eligible(slot, group: str, keys: set[str]) -> bool:
    return bool(slot[0] & keys) and (group != "D" or slot[1])


def _distinct_cover(slots, needs: list[tuple[str, set[str]]]) -> bool:
    """True if every entry of `needs` can be given its OWN slot (a relic can't hold one effect twice, so an
    effect listed in two AND-ed groups must not count for both)."""
    def go(i: int, used: frozenset) -> bool:
        if i == len(needs):
            return True
        g, keys = needs[i]
        return any(k not in used and _eligible(slots[k], g, keys) and go(i + 1, used | {k}) for k in range(len(slots)))
    return go(0, frozenset())


def _eval_combination(info: RelicInfo, q: dict, groups: dict[str, set[str]]) -> bool:
    try:
        dnf = compile_expression(effective_expression(q))
    except ValueError:
        return False                                   # a broken expression matches nothing (the dry run says why)
    mins = q.get("min_counts") or {}
    slots = _slots(info)
    for conj in dnf:
        pos = [(g, groups[g]) for g, n in conj if not n]
        neg = [g for g, n in conj if n]
        if any(not groups[g] for g in neg):            # NOT of an empty list would match everything: refuse
            continue
        if any(sum(_eligible(sl, g, groups[g]) for sl in slots) >= mins.get(g, 1) for g in neg):
            continue
        needs = [(g, keys) for g, keys in pos for _ in range(mins.get(g, 1))]
        if needs and _distinct_cover(slots, needs):
            return True
        if not needs and neg:
            return True
    return False


def _eval_attributes(info: RelicInfo, a: dict) -> bool:
    if a["tiers"] and info.tier not in a["tiers"]:
        return False
    if a["colors"] and info.color not in a["colors"]:
        return False
    if a["deep"] == "only" and not info.deep:
        return False
    if a["deep"] == "exclude" and info.deep:
        return False
    return True


def attributes_active(a: dict) -> bool:
    return bool(a["tiers"] or a["colors"] or a["deep"] != "any")


@dataclass
class Matcher:
    preset: dict
    groups: dict
    warnings: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        f = self.preset["filters"]
        return not (f["combination_query"]["enabled"] or attributes_active(f["attributes"]))

    def matches(self, info: RelicInfo) -> bool:
        f = self.preset["filters"]
        if self.is_empty():
            return False                                   # never "match everything" by accident
        if not _eval_attributes(info, f["attributes"]):
            return False
        if f["combination_query"]["enabled"]:
            return _eval_combination(info, f["combination_query"], self.groups)
        return True                                        # attributes only


def make_matcher(preset: dict, gd: GameData) -> Matcher:
    p = normalize_preset(preset)
    q = p["filters"]["combination_query"]
    groups, unknown = {}, {}
    for g in GROUP_NAMES:
        groups[g], unknown[g] = gd.resolve_tokens(q[f"group_{g.lower()}_affixes"])
    m = Matcher(p, groups)
    if q["enabled"]:
        try:
            dnf = compile_expression(effective_expression(q))
        except ValueError as exc:
            m.warnings.append(f"Logic expression is invalid ({exc}); the combination query matches nothing.")
            dnf = ()
        used = {g for c in dnf for g, _ in c}
        for g in GROUP_NAMES:
            if g in used and not groups[g]:
                neg_only = all(n for c in dnf for gg, n in c if gg == g)
                m.warnings.append(f"Group {g} is used in the logic but is empty"
                                  + ("; a NOT of an empty group matches nothing." if neg_only else "."))
        shared = set()
        for c in dnf:
            P = [g for g, n in c if not n]
            for i, g1 in enumerate(P):
                for g2 in P[i + 1:]:
                    if g1 != g2 and groups[g1] & groups[g2]:
                        shared.add(tuple(sorted((g1, g2))))
        for g1, g2 in sorted(shared):
            m.warnings.append(f"Groups {g1} and {g2} share effects; a relic can't hold one effect twice, so each AND "
                              "side must be satisfied by a different effect.")
    for g in GROUP_NAMES:
        unk = unknown[g]
        if unk:
            m.warnings.append(f"Group {g}: not a known relic effect: " + ", ".join(unk[:6]) + ("..." if len(unk) > 6 else ""))
    if m.is_empty():
        m.warnings.append("No filter is enabled, so nothing will match.")
    return m


# -- plan (dry run) ----------------------------------------------------------
@dataclass
class Plan:
    action: str
    scanned: int
    equipped_count: int
    favorite_count: int
    candidates: list[RelicInfo]
    skipped: list[tuple[RelicInfo, str]]
    matched_total: int
    payout: int
    tier_counts: dict[str, int]
    murk_current: int
    murk_new: int
    credit_murk: bool
    sell_values_verified: bool
    warnings: list[str]
    blocked: str | None = None      # reason the plan cannot be applied at all

    @property
    def can_apply(self) -> bool:
        return not self.blocked and bool(self.candidates)


def build_plan(infos: list[RelicInfo], preset: dict, gd: GameData, murk_current: int, *,
               equipment_known: bool, allow_favorites: bool = False, credit_murk: bool = True,
               extra_warnings: list[str] | None = None) -> Plan:
    matcher = make_matcher(preset, gd)
    action = matcher.preset["target_action"]
    warnings = list(extra_warnings or []) + matcher.warnings
    candidates: list[RelicInfo] = []
    skipped: list[tuple[RelicInfo, str]] = []
    matched = 0
    for info in infos:
        if not matcher.matches(info):
            continue
        matched += 1
        reason = None
        if action == "SELL":
            if info.equipped is None or info.equipped:
                reason = "equipped" if info.equipped else "equipment unknown"
            elif info.unique:
                reason = "unique relic (protected)"
            elif not info.salable:
                reason = "not sellable (game flag)"
            elif info.tier is None or gd.sell_value(info.tier) is None:
                reason = "no known sell tier"
            elif info.favorite and not allow_favorites:
                reason = "favorite (locked)"
        elif action == "FAVORITE" and info.favorite:
            reason = "already favorite"
        elif action == "UNFAVORITE" and not info.favorite:
            reason = "not a favorite"
        if reason:
            skipped.append((info, reason))
        else:
            candidates.append(info)
    payout = 0
    tiers: dict[str, int] = {}
    blocked = None
    if action == "SELL":
        for info in candidates:
            payout += gd.sell_value(info.tier) or 0
            tiers[info.tier] = tiers.get(info.tier, 0) + 1
        if not equipment_known:
            blocked = "Equipped relics could not be determined for this slot, so selling is disabled."
        if not gd.sell_values_verified and candidates:
            warnings.append("Sell values are unverified placeholders; compare with the in-game prices first.")
    murk_new = min(murk_current + payout, 0xFFFFFFFF) if (action == "SELL" and credit_murk) else murk_current
    return Plan(
        action=action, scanned=len(infos),
        equipped_count=sum(1 for i in infos if i.equipped),
        favorite_count=sum(1 for i in infos if i.favorite),
        candidates=candidates, skipped=skipped, matched_total=matched,
        payout=payout, tier_counts=tiers, murk_current=murk_current, murk_new=murk_new,
        credit_murk=credit_murk, sell_values_verified=gd.sell_values_verified,
        warnings=warnings, blocked=blocked,
    )
