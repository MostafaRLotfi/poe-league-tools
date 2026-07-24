# PoE League Tools

A game assistant built entirely from the one file the developer allows you
to read: no memory access, no injection, no simulated input, just
`Client.txt`. **[Design writeup → kuros.io/work/poe-league-tools](https://kuros.io/work/poe-league-tools/)**

Toolkit for Path of Exile league starts (built for 3.29 *Curse of the
Allflame*, July 24 2026 — the campaign route itself is league-agnostic).
Designed for a **party of 2–6**: paste in everyone's PoB, get per-player
leveling kits, and the overlay guides you through all ten acts while
tracking your mates, your splits, and the loot on your clipboard.

Components:

1. **Campaign overlay** (`overlay/`) — always-on-top guide card, auto-advancing
   through a full 10-act route (187 steps) by watching `Client.txt`. Shows a
   party status row, per-act split timer vs. your PB, XP-penalty warnings,
   and instant TAKE/SKIP/CHECK verdicts on any item you Ctrl+C in game.
2. **Build tools** (`buildgen/`) — PoB codes → leveling sheets with an exact
   passive allocation for each level, overlay gem/passive notes, and a party
   summary (gem links per act + uniques wishlist).
3. **Advisor** (`advisor/`) — LLM pipeline for the 3.29 patch notes:
   `summarize.py` (notes → structured deltas) → `advise.py` (build
   recommendations) → `exposure.py` (per-build nerf report).
4. **Market intelligence** (`market/`) — poe.ninja poller → SQLite →
   deterministic arbitrage scanner (cycles + spreads, anti-price-fixing
   filter) → terminal console with trade journal and PnL calibration.
5. **Tools** (`tools/`) — `simulate_client.py` (fake Client.txt for dev
   without the game), `tradeq.py` (English → trade-site query), `retro.py`
   (post-run analysis), `meta.py` (ninja ladder ranker), `verify_routes_llm.py`
   (wiki-grounded route checker), `check.py` (run every test suite).

## ToS safety (read this once)

This project only ever **reads the `Client.txt` text log**, which GGG
explicitly sanctions — it's how every legitimate leveling tracker works.
It never reads game memory, injects into the process, or sends input.
The market tools follow the same line: they fetch public data politely
(identified User-Agent, 1 request/2 s), **never** send whispers or execute
trades — the console's ceiling is copying whisper text to your clipboard
when you press a key. Party tracking, deaths, and levels are all lines the
game already writes to your own log. Nothing here should ever cross that
line.

## Party setup (start here)

The easiest install is the Windows release:

1. Download and run **`PoE-League-Tools-Setup.exe`**.
2. Launch **PoE League Tools** from the Start menu.
3. Pick **Carry, Aurabot, Banner, or Drugger**, enter the exact in-game
   character name, and click **Use this build**.

The installer is per-user: no Python, terminal, archive extraction, admin
rights, or JSON editing. It immediately starts the overlay after first-run
setup. Settings and run history live in
`%LOCALAPPDATA%\PoE League Tools`, so reinstalling or upgrading keeps them.
Press **F10** in the overlay to change the selected PoB. The Start-menu
shortcut **PoE League Tools - Setup or Change Character** reopens the full
character and Client.txt setup.

The portable ZIP remains available as a fallback. In that version,
double-click `setup_pc.bat`, then `overlay\run_overlay.bat`;
`choose_build.bat` changes the build while the overlay is closed.

To replace or add PoBs later, `python buildgen/party.py --init` remains the
authoring workflow: paste a pobb.in / pastebin / poe.ninja / maxroll link,
then it writes `party.json` and generates per-player
   `<name>_plan.md` + `<name>_notes.json`, `party_summary.md` with
   everyone's gem links per act and a uniques wishlist, and
   `party_bundle.json` — the manifest each PC sets itself up from.
   Each plan reconstructs a connected, deterministic passive click order
   between the PoB author's cumulative tree snapshots. The overlay shows the
   exact passive name at each level, quest-point allocations, Lab choices,
   and authored-tree respec transitions. Because PoB does not store click
   order inside a snapshot, generated plans label this order as derived and
   retain the node ID and PoB tree link for auditing.
   PoBs with act-tagged or level-staged skill sets get build-specific gem
   notes. Known guide PoBs can also match reviewed campaign adapters in
   `data/pob_leveling_adapters.json`; those reminders advance at character
   level milestones. Matched plans include an exact TAKE/BUY/SWAP gem table
   and a build-specific item pickup guide; the generated notes file also
   selects that profile for live Ctrl+C item verdicts. An unknown
   endgame-only PoB falls back to the generic per-class plan in
   `data/leveling_defaults.json` — labelled as generic in plan.md and by the
   doctor.
3. Run the **Build Windows installer** GitHub Actions workflow to produce
   `PoE-League-Tools-Setup.exe`. A `v*` tag also attaches the installer to
   that GitHub Release. On a Windows development machine, the equivalent
   local command is:

   ```powershell
   pip install -r requirements.txt -r requirements-build.txt
   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 `
     -Version 3.29.0
   ```

4. `python tools/make_portable.py` → `dist/poe-league-tools-pc.zip`
   (~92 MB): the whole toolkit **with a private Windows Python and PyQt6
   inside** — friends install nothing. Built from the Mac; needs network
   the first time (python.org + PyPI, cached in `dist/cache/`).
5. Ship the installer. `FRIENDS.md` is the short hand-out;
   (`START_HERE_EASY.md` is the zero-assumed-knowledge setup version,
   and `BEGINNER_LEVELING.md` the from-zero leveling companion, for
   the least experienced player). Keep the portable ZIP for anyone who
   prefers a folder with no installed application.

Solo works too: `python buildgen/pob.py plan <code-or-link>`, empty
party config.

The four supplied 3.29 party builds are already collected in
`buildgen/party.allflame.json`. Generate their current plans and overlay
files with:

```
python buildgen/party.py buildgen/party.allflame.json --out-dir builds/allflame
```

The pickup profiles use the 3.29 socket rules: every gem can be placed in
every socket colour. A matching red/green/blue socket is treated as a
`+10%` gem-quality upgrade, never as a compatibility gate. The Act 1
checklists also use the expanded vendor access to Faster Attacks after
The Caged Brute.

## Overlay quickstart (gaming PC)

Windows, game in **Windowed Fullscreen**, English client.

**Installed release:** open **PoE League Tools** from the Start menu.
First launch performs setup; later launches go straight to the overlay.

**Portable/source version:**

```
setup_pc.bat                 (once — first-run wizard; installs deps only
                              when the folder has no bundled python\)
overlay\run_overlay.bat      (or: .venv\Scripts\python.exe overlay\main.py)
doctor.bat                   (health check — run when anything looks wrong)
```

The .bat files prefer, in order: the portable bundle's `python\`, the
repo `.venv`, a system Python. LLM extras (`anthropic`) need the venv
flavor — the embedded python ships without pip on purpose.

`Client.txt` is auto-detected (common installs, then every Steam library
via `libraryfolders.vdf`, then a per-drive scan — `overlay/find_client.py`);
the wizard persists what it finds, or set `client_txt` in
`overlay/config.json` by hand. Handing the folder to the other players?
Point them at `FRIENDS.md`.

Hotkeys (global on
Windows): **F2/F3** prev/next step · **F4** hide · **F6** click-through,
card and layouts panel together · **F7** layouts panel hide/show ·
**F8** speak the current step · **F9** narration mute ·
**F10** choose another prepared PoB
(F6 is Windows-only — the non-Windows fallback shortcuts could not undo it;
F8/F9 exist only when narration is enabled).

Getting in the way of clicking? **Mouse-wheel** over the card (or the
layouts panel) resizes it, **double-click** collapses the card to just
its header line, **F6** makes everything click-through. Sizes and
positions persist across restarts (`overlay/ui_state.json`,
machine-written — not part of the shared config).

What the card shows: current step checklist + layout/tips + gem notes;
`● Name lvl` party row (● in your area, ⚠ level-gap ≥ `gap_warn`, ☠ deaths,
red flash when someone dies); `⏱ A3 41:22 (-2:10 PB) ⚠ XP -38%` timer/XP
row; and a 6-second color-coded verdict when you Ctrl+C an item in game
(pure local parsing — nothing leaves your machine). Feature flags in
config: `timer`, `item_eval`, `links_best`, `runs_dir`.

**Spoken narration** (off by default — for anyone who finds reading the
card mid-fight hard): set `narration.enabled: true` in
`overlay/config.json` and the overlay **reads each step aloud** as you
enter the zone, using the voice built into Windows (macOS `say` when
developing) — no extra installs, no screenshots, same Client.txt-only
ToS story. It also announces deaths. **F8** repeats the current step,
**F9** mutes. Config knobs: `rate` (-10..10), `volume` (0-100), `tips`
and `layout` (include the tip / layout-hint lines when speaking).

Starting the overlay mid-campaign (or restarting it)? It reads the log
tail and **fast-forwards to where you already are** — zone history plus
your level disambiguates repeated zone names like the act 1/6 towns
(`resume_route: false` to disable; F2/F3 to fine-tune the landing).

Runs are saved to `runs/` on exit; `python tools/retro.py runs/<file>.json`
prints splits vs PB, level curve, deaths — plus three concrete improvements
if the LLM extra is enabled.

## Zone layouts (the "act decoder")

Optional but great: ~470 hand-traced zone layout images from
[Exile-UI](https://github.com/Lailloken/Exile-UI) (MIT). Fetched by
`setup_pc.bat`, or by hand:

```
python tools/fetch_layouts.py
```

Entering a zone pops a panel with every layout the zone can roll
(white = zone outline, green = path to the exit, purple = waypoint).
Glance at your minimap, **left-click** the variant that matches — it
stays pinned (with any deeper-floor images) until the next zone;
**right-click** shows all variants again. The panel reads the area ID
from the same Client.txt line the game already writes, so ToS safety is
unchanged. `layouts.auto_show: false` in the config if you'd rather
summon it with F7 only.

## Updating

Your personal settings never get clobbered by an update: they live in
Installed-app settings live in `%LOCALAPPDATA%\PoE League Tools`.
Portable/source settings remain in `overlay/config.json` and
`overlay/ui_state.json`. Both arrangements keep machine-written state
outside updates.

**If you installed the app:** run the newer installer. It upgrades the
application in place and leaves settings and run history untouched.

**If you cloned with git** (needs [git](https://git-scm.com) installed):

```
git pull
setup_pc.bat
```

`setup_pc.bat` is safe to re-run every time: it upgrades dependencies,
keeps your existing answers in the setup wizard (just press Enter
through it), and updates the zone-layout image pack.

**If you got the portable zip** (no git, no Python install): download
the new `poe-league-tools-pc.zip`, unzip it next to your old folder,
copy your old `overlay\config.json` into the new folder (or skip that
and answer the setup wizard again), run `setup_pc.bat` once, then
delete the old folder.

**Zone-layout images only:** `python tools/fetch_layouts.py --check`
says whether the community pack has new images; re-run
`python tools/fetch_layouts.py` (or `setup_pc.bat`) to update it.

## LLM features (optional)

`pip install anthropic` + set `ANTHROPIC_API_KEY`. Everything degrades
gracefully without it; kill switch `POE_TOOLS_LLM=off`. Usage is metered to
`llm_usage.jsonl` (`python tools/llm_report.py` for spend by feature).

**July 16 (GGG Live) flow:** save the patch notes to a text file, then

```
python advisor/summarize.py notes.txt --out data/3.29/summary.json
python advisor/advise.py --summary data/3.29/summary.json --pob <code> [--pob <code>...]
python advisor/exposure.py <pob-code> --summary data/3.29/summary.json
python market/brief.py watchlist
```

Also available: `python tools/tradeq.py "boots 30 movespeed, life, cold res,
max 5c"` (validated trade-query JSON + site link), and the wiki-grounded
route audit (advisory only):

```
python tools/verify_routes_llm.py all
```

**Crafting copilot:** Ctrl+C an item in game, then

```
Get-Clipboard | python tools/craft_check.py - --level 34   # PC
pbpaste | python tools/craft_check.py - --no-llm           # Mac, digest only
```

prints what the mods are (tiers, prefix/suffix, open slots), what can
still roll on that base at its item level, usable essences, bench crafts
with costs, and — with the LLM — a grounded step-by-step plan. The odds
data is compiled from RePoE (`python tools/refresh_repoe.py`, rerun after
each patch); the model only selects and explains, it never invents
numbers. You execute every step by hand in game, as always.
League-start crafting principles + verified vendor recipes:
`docs/CRAFTING_GUIDELINES.md`.

**High-ticket crafting:** the curated premium-market guides cover Focused
+4 amulets, Helical attribute rings, fractured 35%-effect 12-passive
clusters, +2-arrow Spine Bow inputs, and T1-suppression Necrotic Armour:

```
python -m craft.guides list
python -m craft.guides show focused_plus4_amulet
python -m craft.guides bundle --out high_end_crafting.md
python -m market.base_filters --bankroll-div 100 --out-dir searches
```

The final command writes bankroll-capped, `tools/snipe.py`-compatible
official trade-query JSON for the exact premium bases. Review the generated
`searches/README.md` before arming them; the cap is a risk guard, not a
valuation.

## Market stack

```
python market/daemon.py            # poll poe.ninja → market/market.db
python market/console.py           # ranked opportunities; r/c/j/x/? commands
python market/brief.py daily       # one-page LLM brief (optional)
python -m market.wealth --day 1 --bankroll-c 150 --risk low
                                    # staged craft/flip/investment plan
python tools/pnl.py                # realized vs expected — tune the haircut
python tools/meta.py               # ladder meta ranker (ascendancies/skills)
```

## Second-screen companions (3.29)

Laptop-side tools for playing with the game on the other machine.
All read-only (Client.txt / clipboard paste / official APIs); every
in-game action stays yours.

```
pbpaste | python tools/upgrade_check.py     # Ctrl+C item vs all 4 party PoBs
python tools/chaos_recipe.py --account You --tab 3
                                            # dump-tab sets + shopping list
python tools/filter_update.py --live --filter My.filter
                                            # economy block spliced into filter
python tools/allflame.py report             # voyages/encounters per hour
python tools/allflame.py candidates         # day-1: find the mechanic's log lines
python tools/allflame.py loot 15c "chest"   # log drops → chaos per encounter
```

`upgrade_check` caches decoded party PoBs beside the bundle (offline
after the first run) and prints per-slot deltas + an honest
defensive-stat verdict (it is not a DPS calculator). `chaos_recipe`
needs the `POESESSID` env var (never stored). `filter_update` owns only
a marked block at the top of your filter — regenerate as prices move,
then reload the filter in game yourself. `allflame`'s mechanic patterns
ship as placeholders: run `candidates` after your first session tonight
and paste the real NPC/zone lines into `data/3.29/mechanic_lines.json`.

Scanner finds negative-cycle arbitrage and cross-source spreads over the
latest snapshots, filters price-fixed quotes, and sizes by liquidity and
bankroll (`market/config.json`; league is `Mirage` until 3.29 launches).
`c <row>` copies the next leg's whisper to your clipboard — sending it is
always your keypress in game.

The deterministic [league-start wealth planner](docs/WEALTH_PLAYBOOK.md)
turns league day, bankroll, and risk tolerance into a ranked mix of craft
inventory, targeted crafts, arbitrage, flips, and investments. Every play
includes a live-price check and a stop-loss; it never performs an action.
Its 3.29 plays now include selling double-corruption temples and capped
batches targeting the new charge, reservation, action-speed, maximum-
resistance, gem-level, cast-speed, and explode corruption implicits.

## Developing on a Mac (playing on a PC)

Everything runs on macOS except global hotkeys (window-local fallback) and
the game itself — use the simulator:

```
python3.13 -m venv .venv && .venv/bin/pip install PyQt6      # once
.venv/bin/python tools/check.py                              # all 16 suites
.venv/bin/python overlay/main.py --client /tmp/fake_client.txt
.venv/bin/python tools/simulate_client.py --out /tmp/fake_client.txt \
    walk --route routes/act1.json --party FriendA:Duelist,FriendB:Ranger
```

(`repl` instead of `walk` to type zone/level/join/death events by hand.)
Deploy to the PC: copy the folder — `builds/` included — and run
`setup_pc.bat`; the wizard (`tools/join_party.py`) and the doctor
(`tools/preflight.py`) both run fine on macOS for testing.

Windows release builds are intentionally performed on Windows because
PyInstaller does not cross-build. Use the **Build Windows installer**
workflow, or `packaging\build_windows.ps1` on a Windows machine. The
workflow runs the tests, generates the reviewed build bundle, downloads
the layout pack, freezes the app, runs its packaged self-test, and wraps
it in an Inno Setup installer.

## Route data

`routes/act1.json` … `act10.json` — full guided density (every quest, skill
point, trial, bandit choice, logout-warp tech), `arealvl` per step for XP
warnings. Format in `routes/schema.md`; `tests/test_routes_all.py` walks
all 187 steps end-to-end through the engine.

`tools/crosscheck_routes.py` validates every zone name and area level
against community data vendored from Exile-UI (`data/exileui/`) — it
runs in the test suite, so a typo that would break auto-advance can't
sneak in. `--coverage` also diffs our route against the community
leveling guide (differences are usually deliberate skips).

## Status / roadmap

- [x] Overlay engine + UI, Client.txt watcher, hotkeys, party row
- [x] PoB decode → plans + gem notes; multi-PoB party builder + wishlist
- [x] Acts 1–10 routes (wiki-verified, walked end-to-end in CI)
- [x] Split timers + XP-penalty warnings; run files + retro tool
- [x] Clipboard item evaluator (parser + rules)
- [x] LLM infra + advisor pipeline (awaiting July 16 patch notes)
- [x] Market stack: sources → store → scanner → console → PnL; briefs
- [x] Meta ranker, tradeq, LLM route verifier, Client.txt simulator
- [x] Friend onboarding: party bundle → `setup_pc.bat` wizard →
      `doctor.bat` health check (FRIENDS.md is the hand-out)
- [x] Per-user Windows installer: Start-menu app, first-run PoB picker,
      writable `%LOCALAPPDATA%` state, upgrades and uninstaller
- [x] Zone-layout panel (Exile-UI image pack, F7) · resizable/compact
      overlay · routes cross-checked against community data
- [ ] **Before Jul 20:** Mirage rehearsal — 24 h daemon run, tune the
      price-fixing filter, capture real clipboard fixtures (see DECISIONS.md)
- [ ] **Jul 16:** feed patch notes to the advisor; generate the watchlist
- [x] Second-screen companions: PoB upgrade checker, chaos-recipe
      assistant, economy filter block, Allflame encounter tracker
- [ ] **Jul 24:** set the 3.29 league in `market/config.json`; re-verify
      poe.ninja endpoints + meta.py after launch; calibrate
      `data/3.29/mechanic_lines.json` (`tools/allflame.py candidates`);
      verify the stash endpoint shape (`market/stashapi.py` VERIFY)

## Credits

Zone layout images and campaign reference data come from
[Exile-UI](https://github.com/Lailloken/Exile-UI) by Lailloken (MIT) —
the act-decoder image pack is fetched on demand by
`tools/fetch_layouts.py` (attribution ships alongside it in
`overlay/assets/layouts/ATTRIBUTION.md`), and `data/exileui/` vendors
two of its data tables for offline route validation.
