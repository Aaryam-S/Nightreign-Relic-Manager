# Nightreign Relic Manager

<img width="988" height="620" alt="image" src="https://github.com/user-attachments/assets/21a8b8ad-0488-4d2b-b992-bc8648fb1887" />

Batch **favorite / unfavorite / sell** relics in an Elden Ring: Nightreign **PC** save, with a dry-run review before anything is written.
It never edits, rerolls or creates relic effects and never spawns items. It only flips favorite flags, removes the relics you selected,
and (optionally) credits the Murk for them.

> **Risk:** editing saves can get an online account flagged. Use offline saves, close the game first, and keep your own
> copy of the save. The app also makes a backup before every write.

## Quick start (Windows)

1. Install Python 3 from python.org (tick **Add python.exe to PATH** and keep **tcl/tk** ticked).
2. Double-click **`run_windows.bat`**. The first run sets itself up (a private `.venv` and one dependency); later runs just open the app.
   `run_windows.bat shortcut` adds a desktop shortcut. If the app closes unexpectedly, see `crash.log` next to it.
3. The newest save in `%APPDATA%\Nightreign\<SteamID>\NR0000.sl2` loads automatically (read-only until you apply a plan).

Other options: `python app.py [save]` · `python app.py --inspect NR0000.sl2 [--dump relics.json]` (read-only text summary) ·
`build_exe.bat` (optional standalone `.exe`; keep its `data` folder next to it). Needs `pip install -r requirements.txt` if run manually.

## Building a rule

A rule is effect groups plus a logic line, optionally narrowed by tier / color / deep, plus an action (Sell, Favorite, Unfavorite).
Save it as a **preset** (stored in `data/rules_presets.json`, created on first save; none are shipped), choose it, press **Preview changes**, review.

* **Groups:** **X, Y, Z** hold any effects; **D** holds demerits (curse slots only) and has a **match at least N** box.
  A group is true when the relic has any of its effects (D: at least N).
* **Logic:** combine groups with `AND`, `OR`, `NOT` and parentheses, e.g. `(X OR Y) AND NOT D`. `NOT` binds tightest, then `AND`, then `OR`.
  `D` = relics with N+ of these curses · `X AND Y` = something from both lists · `X AND NOT D` = good effect, none of these curses.
* **AND needs different effects on each side.** A relic can't hold one effect twice, so an effect in two AND-ed groups can't satisfy both
  (the dry run warns about overlaps), and D's "match at least 2" needs two distinct curse slots.
* **Limit to** tier, color, deep relics is always ANDed on top.
* **Safe defaults:** an empty rule, a malformed logic line, or a `NOT` of an empty group matches nothing, never everything.

**Choosing effects:** type names (one per line, case-insensitive; numeric IDs also work) or use **Pick...**. The picker lists only effects that
can roll on relics (519 names, 24 curses), grouped like the in-game list as far as the data allows; a group row adds all its effects.
The game data has no category column, so the grouping is a name-based classification in `effect_groups.py` (one regex per line; fix
misplaced effects there). Item/weapon/talisman passives that share the game's table are excluded, and effects found on your own relics but
missing from the standard tables appear in a last group.

## Protections

* **Equipped relics are never sold** (referenced by any hero vessel or saved preset). If that data can't be read, selling is disabled.
* **Unique relics are never sold** (boss-reward, Collector Signboard, Night of the Fathom etc.; IDs 1000-2100 and 10000-19999), even where the game data says sellable.
* Also skipped, with a reason shown: not sellable per the game flag, favorites (unless "Allow selling favorites"), relics without a Delicate/Polished/Grand tier.
* **Write pipeline:** re-read and refuse if the file changed, verify the slot's checksum, rebuild in memory, prove only the intended changes happened
  (survivors byte-identical, equipped set unchanged, counts and Murk as planned), re-encode and re-verify, timestamped backup in `backups/`,
  atomic replace, read back and compare, **auto-restore** on any mismatch. Only the edited slot is re-encrypted.

## Limits

* PC saves only (`.sl2` / `.co2`). No PlayStation.
* **Sell values are unverified placeholders** (10 / 20 / 50 Murk; the game data has none). The dry run says "(estimated)" until you enter the real
  prices under Settings -> Sell values. Selling may also update counters this tool can't see.
* Tier comes from the relic's name; deep relics use the same values. Tier-less relics are never sold.
* The save format can change with any patch. Development used a **synthetic** save built from the reference editor's documented layout
  (container crypto cross-checked against it); no real save was available, which is why the `--inspect` check above matters.
* The Windows launcher scripts were written without a Windows machine. If one misbehaves, run `python app.py`.

## Development

`python -m pytest tests` (GUI tests skip without a display) covers the crypto, parsing, every edit and its verification, rule logic,
grouping, the picker and a full load -> dry run -> apply flow on a synthetic save (`tests/make_test_save.py`).
`python tools/crosscheck_reference.py` runs the reference editor's own code (needs a clone in `reference/`) and compares bytes.

Files: `app.py` GUI · `theme.py` colours/fonts · `filter_engine.py` rules, presets, plan · `effect_groups.py` picker grouping ·
`game_data.py` names/tiers/flags · `relic_parser.py` slot layout and edits · `operations.py` safe apply · `save_io.py` BND4/AES/MD5, backup ·
`data/` game tables + `relic_params.json` (sell values).

## Credits and license

MIT (see `LICENSE`). 
Thanks to [alfizari/Elden-Ring-Nightreign-Save-Editor](https://github.com/alfizari/Elden-Ring-Nightreign-Save-Editor) 
for save format and game table knowledge.

Unofficial fan tool, not affiliated with or endorsed by FromSoftware or Bandai Namco. Game data belongs to its owners.
