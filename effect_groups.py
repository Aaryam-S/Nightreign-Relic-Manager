"""Group relic effects by type for the effect picker.

The game data has no explicit category column, so this classifies by effect name. Rules run in order and
the first match wins. Inside a group, effects keep the game's own text-ID order (which follows the game's
internal effect table). If an effect lands in the wrong group, fix it here: one rule or one name per line.
"""
from __future__ import annotations

import re

CLASSES = ("Wylder", "Guardian", "Ironeye", "Duchess", "Raider", "Revenant", "Recluse", "Executor", "Scholar", "Undertaker")

GROUP_ORDER = (
    "Attributes & Max HP/FP/Stamina",
    "Attack Power & Damage",
    "Damage Negation",
    "Status Resistance",
    "Poise",
    "Guarding",
    "Sorceries & Incantations",
    "Skills & Arts",
    "HP / FP / Stamina Recovery",
    "Status Ailments",
    "Armament-specific",
    "Starting Items & Armament",
    "Dormant Power",
    "Team",
    "Expedition & Environment",
    *(f"Character: {c}" for c in CLASSES),
    "Character-specific (other)",
    "Demerits (curses)",
    "Other",
)
FOUND_ON_RELICS = "On your relics but not in the standard relic tables"

_WEAPONS = (r"daggers?|straight swords?|greatswords?|colossal swords?|curved swords?|curved greatswords?|katana|"
            r"twinblades?|thrusting swords?|heavy thrusting swords?|axes|axe|greataxes?|hammers?|great hammers?|"
            r"flails?|colossal weapons?|spears?|great spears?|halberds?|reapers?|whips?|fists?|claws?|bows?|"
            r"light bows?|greatbows?|crossbows?|ballistas?|glintstone staffs?|staffs?|sacred seals?|seals?|"
            r"torch(?:es)?|small shields?|medium shields?|greatshields?|shields?")

# (group, regex) evaluated in order against the lower-cased name. Class tags and demerits are handled before this.
_RULES: list[tuple[str, re.Pattern]] = [(g, re.compile(p)) for g, p in (
    ("Attributes & Max HP/FP/Stamina", r"^(vigor|mind|endurance|strength|dexterity|intelligence|faith|arcane) \+\d|^increased maximum (hp|fp|stamina)"),
    ("Starting Items & Armament", r"at start of expedition|^starting armament|^changes compatible armament"),
    ("Dormant Power", r"^dormant power"),
    ("Armament-specific", rf"^improved ({_WEAPONS}) attack power$|(hp|fp) restoration upon ({_WEAPONS}) attacks|with 3\+ .* equipped"),
    ("Damage Negation", r"negation|negate damage|nullify attacks"),
    ("Status Resistance", r"resistance"),
    ("Poise", r"poise"),
    ("Guarding", r"guard(?! breaking)"),
    ("Sorceries & Incantations", r"sorcer|incantation|spell|glintstone|glintblade|carian|dragon communion|stonedigger|perfuming"),
    ("Skills & Arts", r"art gauge|character skill|ultimate art|skill fp cost|reduced skill|fp consumption"),
    ("HP / FP / Stamina Recovery", r"^partial hp restoration|stamina recovery speed"),
    ("Status Ailments", r"^attacks inflict|^gesture"),
    ("Attack Power & Damage", r"attack power|attack up|attack follows|follows charge|guard breaking|critical|stance-breaking|projectile|damage|"
                              r"improved (charge|jump|dash|rolling|chain|melee|ranged|roar)|initial standard attack|attacks? .*when|affinity attack"),
    ("Team", r"allies|family"),
    ("Expedition & Environment", r"evergaol|great enemy|sorcerer's rise|night invader|expedition|treasure|\bmap\b|rune|item discovery|discount|totem stela"),
    ("Status Ailments", r"inflict|buildup|frostbite|poison|scarlet rot|\brot\b|blood loss|madness|sleep|death blight|mist|surge|wraith|lava|lightning|shockwave|pillars|glintblades|spirits|vicinity"),
    ("HP / FP / Stamina Recovery", r"restor|recover|heal|flask|bolus"),
)]


def classify(name: str, debuff: bool = False) -> str:
    n = name.lower()
    if debuff:
        return "Demerits (curses)"
    if n.startswith("["):
        cls = name[1:name.find("]")] if "]" in name else ""
        return f"Character: {cls}" if cls in CLASSES else "Character-specific (other)"
    for group, rx in _RULES:
        if rx.search(n):
            return group
    return "Other"
