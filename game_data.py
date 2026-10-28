"""Static game data: relic names/tiers/colors/salability and effect names/debuff flags.

Source tables are the param CSVs and text XMLs shipped with the MIT-licensed
alfizari/Elden-Ring-Nightreign-Save-Editor (copied to data/game/).
Sell values are NOT in those tables; they live in data/relic_params.json and are user-editable.
"""
from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from relic_parser import EMPTY_EFFECT, Relic

COLORS = {0: "Red", 1: "Blue", 2: "Yellow", 3: "Green", 4: "White"}
TIERS = ("delicate", "polished", "grand")
_TIER_RE = re.compile(r"^(?:deep\s+)?(delicate|polished|grand)\b", re.I)


# Unique relics: boss rewards like "Night of the Fathom", Small Jar Bazaar / Collector Signboard relics, etc.
# Same ID ranges the reference editor uses (globals.py UNIQUENESS_IDS). Never sold by this tool.
UNIQUE_RANGES = ((1000, 2100), (10000, 19999))
# Ordinary relics: store scenes, boss-reward scenes and deep relics (the reference editor's "safe" groups).
# Relic IDs outside these and the unique ranges (e.g. the "Antique" placeholder rows 1-6, "illegal" 20000+)
# are NOT real relics, so the effect tables they use are not relic effects.
SAFE_RANGES = ((100, 299), (1000000, 1009999), (2000000, 2019999))


def _in_ranges(rid: int, ranges) -> bool:
    return any(a <= rid <= b for a, b in ranges)


def _rollable(row: dict) -> bool:
    """Reference editor's rule: final chance weight is positive (the DLC weight overrides the base one unless -1)."""
    w, d = int(row["chanceWeight"]), int(row["chanceWeight_dlc"])
    return d > 0 or (d == -1 and w > 0)


def is_unique_relic(real_id: int) -> bool:
    return any(a <= real_id <= b for a, b in UNIQUE_RANGES)


def norm(s: str) -> str:
    return " ".join(str(s).casefold().split())


@dataclass(frozen=True)
class RelicDef:
    id: int
    name: str
    color: str
    deep: bool
    salable: bool
    tier: str | None
    known: bool = True
    unique: bool = False


@dataclass(frozen=True)
class EffectDef:
    id: int
    name: str
    debuff: bool
    text_id: int = 0          # the game's own ordering key


@dataclass
class RelicInfo:
    """A parsed relic joined with its static data; this is what filters operate on."""
    relic: Relic
    name: str
    tier: str | None
    color: str
    deep: bool
    salable: bool
    known: bool
    effect_ids: list[int]
    curse_ids: list[int]
    effects: list[str]
    curses: list[str]
    favorite: bool
    equipped: bool | None        # None = unknown (equipment block unreadable)
    equipped_where: str
    unique: bool = False


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _read_text_xml(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    if not path.exists():
        return out
    for el in ET.parse(path).getroot().iter("text"):
        txt = " ".join((el.text or "").split())
        if txt and txt != "%null%":
            out[int(el.get("id"))] = txt
    return out


class GameData:
    def __init__(self, data_dir: Path, language: str = "en_US"):
        self.data_dir = Path(data_dir)
        game = self.data_dir / "game"
        text = game / "Text" / language
        if not text.exists():
            text = game / "Text" / "en_US"

        relic_names = _read_text_xml(text / "AntiqueName.fmg.xml")
        for k, v in _read_text_xml(text / "AntiqueName_dlc01.fmg.xml").items():
            relic_names.setdefault(k, v)
        effect_names = _read_text_xml(text / "AttachEffectName.fmg.xml")
        for k, v in _read_text_xml(text / "AttachEffectName_dlc01.fmg.xml").items():
            effect_names.setdefault(k, v)

        self.effects: dict[int, EffectDef] = {}
        for row in _read_csv(game / "Param" / "AttachEffectParam.csv"):
            eid = int(row["ID"])
            name = effect_names.get(int(row["attachTextId"]), "")
            if name:
                self.effects[eid] = EffectDef(eid, name, row["isDebuff"] == "1", int(row["attachTextId"]))

        self.relics: dict[int, RelicDef] = {}
        pos_tables: set[int] = set()
        curse_tables: set[int] = set()
        for row in _read_csv(game / "Param" / "EquipParamAntique.csv"):
            rid = int(row["ID"])
            name = relic_names.get(rid, "")
            m = _TIER_RE.match(name)
            self.relics[rid] = RelicDef(
                id=rid,
                name=name or f"Relic #{rid}",
                color=COLORS.get(int(row["relicColor"]), "Red"),
                deep=row["isDeepRelic"] == "1",
                salable=row["isSalable"] == "1",
                tier=m.group(1).lower() if m else None,
                unique=is_unique_relic(rid),
            )
            if _in_ranges(rid, SAFE_RANGES) or _in_ranges(rid, UNIQUE_RANGES):
                pos_tables.update(int(row[f"attachEffectTableId_{i}"]) for i in (1, 2, 3))
                curse_tables.update(int(row[f"attachEffectTableId_curse{i}"]) for i in (1, 2, 3))

        # Effects that can actually appear on a real relic: rollable rows of the roll tables used by real relics.
        # The effect param table also holds weapon / talisman / item passives; those are NOT relic effects.
        self.relic_effect_ids: set[int] = set()
        for row in _read_csv(game / "Param" / "AttachEffectTableParam.csv"):
            tid, eid = int(row["ID"]), int(row["attachEffectId"])
            if eid >= 0 and (tid in pos_tables or tid in curse_tables) and _rollable(row):
                self.relic_effect_ids.add(eid)
        self.relic_effect_ids &= self.effects.keys()

        self.name_to_ids: dict[str, set[int]] = {}      # all effects: matching a relic's own effects must always work
        for e in self.effects.values():
            self.name_to_ids.setdefault(norm(e.name), set()).add(e.id)
        self.relic_effect_names: set[str] = {norm(self.effects[i].name) for i in self.relic_effect_ids}

        params = self.load_params()
        self.sell_values: dict[str, int] = {k.lower(): int(v) for k, v in params.get("sell_values", {}).items()}
        self.sell_values_verified: bool = bool(params.get("sell_values_verified", False))

    # -- user params -----------------------------------------------------
    @property
    def params_path(self) -> Path:
        return self.data_dir / "relic_params.json"

    def load_params(self) -> dict:
        try:
            return json.loads(self.params_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"sell_values": {"delicate": 10, "polished": 20, "grand": 50}, "sell_values_verified": False}

    # -- lookups ---------------------------------------------------------
    def relic_def(self, real_id: int) -> RelicDef:
        d = self.relics.get(real_id)
        return d if d else RelicDef(real_id, f"Unknown relic #{real_id}", "Red", False, False, None,
                                    known=False, unique=is_unique_relic(real_id))

    def effect_name(self, eid: int) -> str:
        if eid in EMPTY_EFFECT:
            return ""
        e = self.effects.get(eid)
        return e.name if e else f"Unknown effect #{eid}"

    def all_effect_names(self, debuff: bool | None = None) -> list[str]:
        """Relic effect names only (item/weapon passives excluded), for pickers."""
        names = {self.effects[i].name for i in self.relic_effect_ids
                 if debuff is None or self.effects[i].debuff == debuff}
        return sorted(names, key=str.casefold)

    def effect_catalog(self, debuff: bool | None = None, found_on_relics=()) -> list[tuple[str, list[str]]]:
        """[(group, [effect names...])] in picker order. Names inside a group follow the game's own text-ID order.

        `found_on_relics`: effect names present on the loaded save's relics; any that are not in the standard
        tables are listed under their own group, since they evidently are relic effects.
        """
        from effect_groups import FOUND_ON_RELICS, GROUP_ORDER, classify
        best: dict[str, tuple[int, bool]] = {}
        for i in self.relic_effect_ids:
            e = self.effects[i]
            if debuff is not None and e.debuff != debuff:
                continue
            if e.name not in best or e.text_id < best[e.name][0]:
                best[e.name] = (e.text_id, e.debuff)
        groups: dict[str, list[tuple[int, str]]] = {}
        for name, (tid, deb) in best.items():
            groups.setdefault(classify(name, deb), []).append((tid, name))
        out = [(g, [n for _, n in sorted(groups[g], key=lambda t: (t[0], t[1].casefold()))])
               for g in GROUP_ORDER if g in groups]
        known = {norm(n) for n in best}
        extra = sorted({n for n in found_on_relics if n and norm(n) not in known and not n.startswith("Unknown effect")},
                       key=str.casefold)
        if extra:
            out.append((FOUND_ON_RELICS, extra))
        return out

    def sell_value(self, tier: str | None) -> int | None:
        return self.sell_values.get(tier) if tier else None

    # -- joining ---------------------------------------------------------
    def describe(self, relic: Relic, equipment_known: bool) -> RelicInfo:
        d = self.relic_def(relic.real_id)
        eff = [e for e in relic.effects if e not in EMPTY_EFFECT]
        cur = [c for c in relic.curses if c not in EMPTY_EFFECT]
        return RelicInfo(
            relic=relic, name=d.name, tier=d.tier, color=d.color, deep=d.deep,
            salable=d.salable, known=d.known,
            effect_ids=eff, curse_ids=cur,
            effects=[self.effect_name(e) for e in eff],
            curses=[self.effect_name(c) for c in cur],
            favorite=relic.favorite,
            equipped=(bool(relic.equipped_in) if equipment_known else None),
            equipped_where="+".join(sorted(relic.equipped_in)),
            unique=d.unique,
        )

    def resolve_tokens(self, tokens: list[str]) -> tuple[set[str], list[str]]:
        """Normalise user-entered effect names / numeric IDs. Returns (match keys, unknown tokens)."""
        keys: set[str] = set()
        unknown: list[str] = []
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            if t.isdigit():
                keys.add(t)
                if int(t) not in self.relic_effect_ids:
                    unknown.append(t)
            else:
                keys.add(norm(t))
                if norm(t) not in self.relic_effect_names:
                    unknown.append(t)
        return keys, unknown
