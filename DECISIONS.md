# DECISIONS

Running record of decisions made while building the toolkit (per the plan's
DECISIONS.md convention). Newest at the bottom.

## 2026-07-23 Windows installer

- **Ship an installer EXE, not a PyInstaller one-file application.**
  PyInstaller builds a one-folder GUI app; Inno Setup compresses that folder
  into the single file friends download. Runtime data remains inspectable
  and stable instead of being unpacked into a temporary directory.
- **Per-user, non-admin install.** Program files go under
  `%LOCALAPPDATA%\Programs\PoE League Tools`; writable config, UI state,
  runs, and crash logs go under `%LOCALAPPDATA%\PoE League Tools`.
  Upgrades and uninstalls do not erase player data.
- **Build on Windows.** The GitHub Actions release job prepares the four
  reviewed PoBs and layout pack, runs all tests, freezes the GUI, executes a
  packaged-asset self-test, and builds `PoE-League-Tools-Setup.exe`.
  The portable embedded-Python ZIP remains supported as a fallback.

## Pre-made / confirmed during the 2026-07-07 build

- **Dependencies:** Python stdlib only, plus PyQt6 (overlay UI) and the
  optional `anthropic` package (LLM features only; everything degrades
  without it, kill switch `POE_TOOLS_LLM=off`).
- **LLM model tiers** (`llm/config.json`): fast = `claude-haiku-4-5`,
  standard = `claude-sonnet-5`, deep = `claude-opus-4-8`.
- **Trade/HTTP etiquette:** User-Agent `poe-league-tools/1.0 (contact:
  cyrus@hadavi.net)`, global concurrency 1, hard floor 1 request/2 s,
  Retry-After honored. Whisper text: the API-provided template verbatim,
  copied to the local clipboard on an explicit keypress only — nothing is
  ever sent automatically (GGG ToS line, see README).
- **Market storage:** SQLite (`market/market.db`, gitignored). Console is
  terminal-only (no curses — Windows-first plain ANSI).
- **Scanner starting parameters:** haircut 4 %, min margin 5 %, bankroll
  fraction 20 %, min volume 20 — tuned via the trade journal
  (`tools/pnl.py`), not vibes.
- **Rehearsal league:** `Mirage` (live-verified event league) in
  `market/config.json`; switch to the 3.29 league name at launch (Jul 24).
- **poe.ninja API:** legacy paths 404 as of 2026-07-07; everything lives
  under `poe.ninja/poe1/api/...` (economy overviews + protobuf builds API).
  Re-verify after the 3.29 launch.
- **Route files:** one `actN.json` per act, numeric-sorted by the engine
  (act10 after act9, not after act1). `arealvl` = zone monster level on
  every non-town step; drives the overlay XP-penalty warning.
- **XP-penalty formula** implemented from poewiki (safe zone
  `3 + floor(L/16)`, `((L+5)/(L+5+d^2.5))^1.5`, plus the 95+ table).
- **Scanner seam:** currency-overview rows are converted to directed pair
  rows via `scanner.pair_rows_from_currency`; derived cross-source 2-cycles
  are suppressed (the spread detector reports those), same-source crossed
  books are kept.
- **Reconstructed plan docs:** the original IMPLEMENTATION_PLAN.md was never
  in this repo; `docs/INTERFACES.md` now serves as the working contract.

## 2026-07-07 review sweep (post-build fixes)

- **Retry-After is honored in full everywhere:** `tools/meta.py` and
  `tools/verify_routes_llm.py` now parse both the seconds and HTTP-date
  forms, uncapped (previously capped at 120 s / misparsed dates as 5 s),
  mirroring `market/sources.py`. `tools/tradeq.py --post` persists its
  last-request timestamp and any 429/token-bucket deadline to
  `data/tradeq_state.json` so the budget survives across processes, and
  reads `X-Rate-Limit-*` headers.
- **Scanner volumes are chaos-normalized:** single-item listing counts are
  multiplied by unit price before gating/sizing (exchange rows already
  chaos); pair-row `sell_vol` is defined as chaos depth. `min_vol` is now
  a chaos-depth gate — retune it during the Mirage rehearsal (20 c is
  permissive).
- **Scanner correctness:** the fixer filter degrades quotes still below
  the band after the k-drop (never HIGH confidence on a bait price);
  cycle detection branches per cycle edge instead of greedy worst-edge
  removal (suppressed/over-cap cycles no longer mask reportable ones);
  3+-leg cycles keep forward and reverse as distinct trades.
- **Trade journal snapshots expectations:** `executions` gained
  `expected_profit_c`/`kind` columns filled at journal time; tools/pnl.py
  prefers them over the (rescanned) live opportunities row and no longer
  reports a locked DB as "no executions journaled yet".
- **Run tracker:** pb.json is only written by runs that reached act 10;
  the clock re-anchors on the first zone (login-queue idle excluded);
  the overlay autosaves the in-progress run every 30 s and primes the
  character level from the Client.txt tail on restart.
- **Client.txt parsing:** system lines are matched from line start (chat
  can no longer spoof events by embedding "] : "); guild tags
  (`<TAG> Name ...`) are accepted; partial (mid-flush) lines are buffered
  until their newline arrives.
- **simulate_client.py** refuses `--out` paths that look like a real
  Client.txt (override: `--i-know-what-im-doing`) and defaults to the
  system temp dir (Windows-safe).
- **Windows text encodings:** clip.exe gets UTF-16 (BOM) payloads;
  meta/verify_routes reconfigure stdout to UTF-8 for redirected output.
- **Clickthrough (F6) is Windows-only:** the non-Windows shortcut
  fallback cannot undo input transparency, so main.py no longer binds it
  there.

## 2026-07-07 friend onboarding sweep

- **Nobody hand-edits JSON to join.** `buildgen/party.py` now also writes
  `builds/party_bundle.json` (league + members: player/class/ascendancy +
  notes/plan *basenames* — the bundle travels with its folder, so paths
  from the generating machine would be wrong on every PC).
  `tools/join_party.py` (chained from `setup_pc.bat`, re-run-safe) reads
  it, asks "who are you?", finds Client.txt, and writes that machine's
  `overlay/config.json` — preserving unrelated tweaks, backing up a
  corrupt config to `.bak` instead of discarding it.
- **The old printed paste-block was broken by design** for the actual
  deploy flow (generate on the Mac → play on PCs): it embedded
  `os.path.abspath` of the notes file on the *generating* machine. Kept
  as a hand-editor fallback, now overlay-relative with forward slashes.
- **Client.txt discovery** (`overlay/find_client.py`, injectable-FS,
  shared by overlay/wizard/doctor): configured path → common installs →
  every Steam library out of `libraryfolders.vdf` (registry `SteamPath`
  + default dirs; read-only file parse) → per-drive layout scan.
- **Doctor** (`tools/preflight.py`, `doctor.bat`): OK/INFO/WARN/FAIL
  rows with a fix per line; exit 1 on FAIL. Catches the *silent* failure
  modes: `me` misspelt vs the bundle, `build_notes` pointing nowhere
  (previously skipped without a word — now also warned at overlay start),
  stale `Client.txt` from an abandoned install, a log tail with zero
  parseable lines (non-English client), typo'd config keys, duplicate
  hotkeys. Never crashes: every check is wrapped.
- **Failures must be loud where the friend is looking:** malformed
  config.json → plain-language error + exit 2 (no traceback);
  `run_overlay.bat` prints a doctor hint on non-zero exit; a missing
  Client.txt is parked on the overlay card itself (party row — no party
  events can arrive to repaint it while the log is missing).
- **`FRIENDS.md`** is the hand-out: two double-clicks, a symptom table,
  and the ToS answer up front. Ship the folder zipped (`builds/` is
  gitignored, so a clone would lack the bundle).
- **Generic plans verified against the live 3.28 meta (2026-07-08).**
  All seven `data/leveling_defaults.json` entries were rewritten to
  match Maxroll's class leveling guides (each page "Reviewed for PoE
  3.28 Mirage", Mar 2026) with every `@level` claim checked against
  poewiki's cargo DB (two API queries, repo User-Agent). Notable
  corrections over the first-draft classics: Ancestral Call removed
  from Ground Slam/Cleave lines (slams aren't strikes — it never
  worked); Templar now opens Holy Strike + Hallow (new 3.28 holy
  gems); Witch/Templar/Shadow use the ignite-prolif Armageddon Brand +
  Cremation route (not Controlled Destruction); Duelist/Scion open
  Spectral Throw into Sunder @12 with Close Combat @18; Ranger gets
  Momentum/LMP + Manaforged Arrows @28 tech. Re-verify after the
  Jul 16 patch notes (3.29 may shuffle gems again).
- **Bare PoBs get a generic class leveling plan.** A guide-site export
  with only an "Endgame" skill set used to produce zero gem notes
  (empty ⚙ row). `pob.make_plan` now falls back to
  `data/leveling_defaults.json` — authored per class (7 × acts 1-10,
  ≤110 chars/line for the card), league-agnostic classics, freely
  editable. Fallback fires only on ZERO act-tagged notes (a partial
  PoB is respected as authored); entries carry `"source": "generic"`
  (the overlay ignores extra keys) so plan.md and the doctor label
  them. Build-specific notes remain the upgrade path: title the PoB
  sets "Act N ..." and re-run party.py.
- **Zero-install PC bundle** (`tools/make_portable.py` →
  `dist/poe-league-tools-pc.zip`, ~92 MB): python.org's *Windows
  embeddable* CPython + the PyQt6 win_amd64 wheels unzipped straight
  into its site-packages (wheels are install-by-extract; PyQt6 has no
  post-install steps). Friends need no Python, no admin, no internet.
  Kept as the Mac-buildable portable fallback because PyInstaller/Nuitka
  cannot cross-build; the embeddable approach is pure downloads + file
  assembly, so the Mac builds it (network at build time only, cached under
  `dist/cache/`). The primary installer is now built by Windows CI.
  - The `._pth` file takes FULL control of the embedded interpreter's
    `sys.path` (no script-dir insertion), so it lists every package dir
    the repo imports by-same-directory: `..`, `..\overlay`,
    `..\buildgen`, `..\market`, `..\advisor`. Site stays disabled.
  - `EMBED_VERSION` pinned (3.13.14, verified live 2026-07-07); its
    major.minor must match pip's `--python-version` because
    `PyQt6_sip` wheels are cpXY-specific (handled in the tool).
  - Known limit: no pip inside the bundle, so LLM extras need the
    .venv flavor (or a future `--with-llm` that vendors `anthropic`+
    deps the same way). The .bat files prefer `python\` → `.venv` →
    system Python, so both flavors coexist.
  - Removed a stray empty `{overlay,routes,buildgen,tests}` dir at the
    repo root (2026-07-06 brace-expansion accident) that leaked into
    the first bundle build.

## 2026-07-08 field feedback (first friend test on Mirage)

- **Route fast-forward on startup** (requested: "fast forward to where
  my progress was"). `RouteEngine.fast_forward(zones, known_level)`
  takes the log tail's zone history (`client_watcher.recent_zones`,
  4 MB tail) and reconciles two estimates: replaying the full history
  through `on_zone` (exact when the log covers the run) and the last
  history zone that names a route step (rescues fresh installs,
  rotated logs, alt-character pollution). With a known level, the
  estimate whose area level fits better wins — that's what tells the
  act 1 from the act 6 "Lioneye's Watch". Startup-only: mid-play the
  lookahead window stays the teleport guard. `resume_route: true`
  config key (default on), F2/F3 to nudge a wrong landing.
- Friend verdict otherwise: runs fine, not intrusive; card is not
  clickable by design (hotkeys only, F6 = click-through).

## 2026-07-11 crafting copilot (addendum 5G, task 29)

- **Data source: the RePoE fork** (repoe-fork.github.io) — static JSON
  extracted from game files, the same substrate Craft of Exile uses.
  Craft of Exile itself has no API; scraping its internal files was
  rejected (unlicensed, fragile, redundant). `tools/refresh_repoe.py`
  compiles 4 raw files (~25 MB) into `data/repoe_craft.json` (2.1 MB,
  committed) — mods already carry rendered English templates, so
  stat_translations isn't needed. Currently 3.28.0.14.3 data; **rerun
  after the 3.29 patch drops** (added to the launch checklist below).
- **Division of labor:** every number (tiers, ranges, level gates, spawn
  weights, essence ilvl caps, bench costs) is computed deterministically
  in `craft/pool.py`; the LLM (standard tier, schema-validated) only
  selects among methods present in the digest + the 12 authored recipes
  (`data/craft_recipes.json`) and explains. Prompt forbids invented
  numbers and anything automated.
- **Matcher subtleties encoded in tests:** mods only roll in their own
  domain (without the base-domain filter, flask mods "spawned" on wands
  via their `default` tag); spawn weights are first-match-wins over the
  mod's ordered tag list; hybrid mods collapse consecutive lines into one
  affix; `itemtext.parse` grew a backwards-compatible `mod_tags` key so
  implicits/enchants don't count toward affix slots and `(crafted)` lines
  resolve against the bench table. Affix counts are labeled an estimate
  whenever any line stayed unidentified/ambiguous.
- **Fixture caveat:** the authored `magic_wand.txt` has a lone
  "5% increased Light Radius" line — the real mod is a two-line hybrid
  (radius + accuracy), so it can't match. Real Ctrl+C captures during the
  Mirage rehearsal (owner checklist item 3) should also feed
  `tests/fixtures_craft/` expectations.
- **Overlay hotkey wiring deferred** to an integration pass: the copilot
  is CLI-first (`tools/craft_check.py`, stdin/clipboard pipe); wiring it
  to the Qt clipboard signal must keep the LLM call off the 300 ms path
  (worker thread, like the item evaluator's planned LLM path).

## 2026-07-11 crafting recipe research sweep (Reddit + poewiki)

- **Sources:** Reddit consensus harvested via Tavily search (reddit.com
  blocks direct crawling; Tavily's cached index works — extract does not).
  Every mechanic then verified against poewiki's MediaWiki API with the
  repo User-Agent before entering the data files. Rule of the sweep:
  *Reddit is consensus, the wiki is truth.*
- **Correction found:** the authored `plus1_caster_weapon` recipe used the
  outdated ruby/topaz/sapphire-ring + alteration form. Current recipe
  (verified): NORMAL rune dagger/sceptre/staff/wand + 2+ gems totaling
  40% quality with the matching damage tag (warstaff/generic dagger
  excluded). Also added its minion-helmet variant.
- **New recipes:** `minion_plus1_helmet`, `orb_of_binding` (Harbinger-
  sourced caveat; 4-socket bases from ilvl 25), `fractured_base`,
  `vendor_shopping` (restock on level-up; linked-RGB → Chromatic,
  6-socket → 7 Jeweller's); `phys_weapon_rustic` gained exact ranges
  (magic sash 40–49%, rare 50–64%).
- **New layer: `data/craft_guidelines.json`** — 11 general principles
  (open-affix-first, life+res rule, essence-over-chaos, stop-loss,
  craft-vs-buy, weapon-first, base/ilvl, magic-is-fine, currency
  scarcity order, don't-outcraft-uniques, fractured-forever). Included
  in every LLM payload (`digest["guidelines"]`); human-readable copy in
  `docs/CRAFTING_GUIDELINES.md`. Not rendered in the CLI digest to keep
  it scannable.

## 2026-07-12 order-of-operations layer (craft copilot)

- Recipes alone don't make plans — sequencing does. New
  `data/craft_order.json`: 7 canonical phases (base → quality → sockets/
  links/colors → mods → regal/scour → bench → corrupt) plus 8 hard
  sequencing rules the LLM must never violate (quality-before-fusings and
  jeweller/fusing quality effects verified verbatim on poewiki; rarity-
  sensitive vendor recipes before currency; rerolls delete bench crafts
  so bench goes last; corruption is final). Prompt rule 6 enforces it and
  tells the model to plan from the item's *current* state rather than
  scouring back to the textbook sequence. CLI digest prints the one-line
  phase chain; the full rationale lives in docs/CRAFTING_GUIDELINES.md.

## 2026-07-12 method-selection matrix (craft copilot)

- Third static layer, `data/craft_methods.json`: when essence vs fossil
  vs beastcraft vs harvest vs unveil vs Rog vs eldritch is the right
  tool. Three parts: `choose` (need-shaped one-liners: one mod → essence;
  themed combo → fossils; fix half → harvest keep-affixes; gamble →
  imprint first; free gear → Rog), `stages` (campaign / early maps / 75+,
  with `from_level` thresholds and explicit skip-lists — no hoarding for
  mechanics that don't exist yet), `methods` (per-method what/where/
  use_when/avoid_when). Availability claims verified on poewiki: Harvest
  is maps-only (Sacred Grove portals in maps); Delve opens Act 4 at
  level 14 via Niko, so fossils ARE campaign-reachable. Prompt rule 2
  widened accordingly (it previously forbade anything beyond recipes/
  essences/bench, which would have banned fossils outright); it now also
  enforces stage gating. CLI digest prints the ctx-level's stage line.
  Recombinators entry carries a `VERIFY:` for 3.29 availability.

## Owner inputs still needed (from the plan §7 + build findings)

1. **Starting bankroll** (chaos/div) for opportunity sizing once 3.29
   stabilizes — placeholder `bankroll_c: 2000` in `market/config.json`.
2. **Party character names** at league start: rerun `buildgen/party.py`
   with the real PoBs, re-ship `builds/`, everyone re-runs
   `setup_pc.bat` (it re-asks against the fresh bundle).
3. **Mirage rehearsal checklist (before Jul 20):** run the market daemon
   ≥ 24 h against Mirage; capture 5 real Ctrl+C item samples into
   `tests/fixtures_items/` (replacing authored ones); die once on purpose
   and confirm the "has been slain" line matches the watcher; tune the
   price-fixing filter constants.
   *Added 2026-07-16 — live-search monitor rehearsal:* run
   `tools/snipe.py --probe` with a real POESESSID against a Mirage
   search, then a short armed session, and resolve the VERIFY notes in
   `market/livesearch.py` (WS message shapes, ping cadence, Merchant's
   Tab buyout marker in fetch responses, live rate-limit headers).
   Context: PoE1 has had async buyout via Merchant's Tabs since 3.27 —
   mispriced buyout listings are real opportunities far more often than
   whisper-era price fixing; re-check the price-fixing filter split
   (buyout vs whisper listings) accordingly. Design decision: the
   monitor alerts and (optionally) opens the results page / copies the
   whisper — the buy click or whisper send is ALWAYS the human. Needs
   optional `websocket-client` (commented in requirements.txt) and the
   POESESSID env var; both degrade with instructions.
4. **July 16 (GGG Live):** paste the 3.29 patch notes through
   `advisor/summarize.py` → `data/3.29/summary.json`, then run
   `advisor/advise.py` + `advisor/exposure.py` on the party's PoBs and
   `market/brief.py watchlist`.
   *2026-07-16 status:* raw notes saved to `data/3.29/patchnotes.txt`;
   `summary.json` (146 items, quotes verified verbatim) and
   `market/watchlist.json` (13 entries, citations validated) were authored
   directly by Claude because no `ANTHROPIC_API_KEY` is set on this machine —
   re-running `advisor/summarize.py` with a key is optional, not required.
   `advise.py`/`exposure.py` still pending: blocked on real party PoBs (item 2).
   League name is **Curse of the Allflame**.
   ⚠ 3.29 makes Ctrl+C ALWAYS copy the advanced description format
   ("Copying an item's text now always copies the advanced description
   format") — Mirage-rehearsal fixtures (item 3) must be captured with
   Ctrl+Alt+C (advanced copy) so they match post-patch clipboard output;
   `overlay/itemtext.py` already skips `{`/`(` info lines but re-verify
   verdicts against advanced-format samples at launch.
5. **At launch (Jul 24):** set the 3.29 league name in `market/config.json`,
   re-verify poe.ninja endpoints and `tools/meta.py` protobuf schema, and
   rerun `tools/refresh_repoe.py` once the fork publishes 3.29 data (check
   the version line the tool prints).

## 2026-07-24 second-screen companions (launch day, 4 parallel workstreams)

Four laptop-side tools built the morning of the 3.29 launch, same
parallel-ownership model as the original build (rows + data formats
added to docs/INTERFACES.md). All are CLI-first, stdlib-only, offline-
tested (34/34 suites), and keep every game/trade action human.

- **Upgrade checker** (`tools/upgrade_check.py`): Ctrl+C item vs the
  equivalent slot in all four party PoBs. Live pobb.in exports were
  fetched during the build (network-fixture policy), which caught two
  would-be bugs: `activeItemSet` matches ItemSet **id**, not document
  order (real export order: 2,3,4,8,6,1,7,5), and influence markers
  ("Hunter Item") sit before `Implicits: N` — naive counting spilled
  eldritch implicits into the explicit-mod diff. Decoded PoB XML is
  cached as `<Player>.pobxml` beside the bundle; verdict is an honest
  defensive-stat heuristic, not a DPS calc.
- **Chaos-recipe assistant** (`tools/chaos_recipe.py`): legacy
  session-cookie stash endpoint over the shared 2 s rate gate
  (`RateLimitedFetcher.get_json` gained backwards-compatible
  `extra_headers`). POESESSID env-only, never persisted, degrade path
  prints cookie instructions. OAuth was rejected for now: the legacy
  endpoint matches the existing livesearch auth posture and needs no
  app registration. Endpoint shape VERIFY at launch.
- **Filter feedback loop** (`tools/filter_update.py`): marked economy
  block spliced at the top of the user's filter (first-match-wins), so
  we never regenerate or fork their base filter — NeverSink stays
  theirs, we own 40 lines between two markers. Uniques excluded v1
  (filters match BaseType, not unique names). Reload stays manual.
- **Allflame tracker** (`tools/allflame.py`): mechanic zone/NPC
  patterns are UNKNOWABLE pre-launch, so the config ships VERIFY
  placeholders plus a `candidates` calibration command (ranks non-town
  NPC dialogue by frequency, suppresses speakers heard more in town).
  Loot is logged manually (`loot 15c "sunken chest"`) — Client.txt has
  no drop data, and we don't pretend otherwise.

## 2026-07-21 spoken narration (overlay)

- **Narration is TTS over existing Client.txt data — no screenshots.**
  "Recognize the area and say what to do" needs nothing visual: the zone
  line in Client.txt + the route step the engine is already on IS the
  area recognition. `overlay/narrator.py` speaks the step (zone, do-list,
  layout hint, tip) on every real step advance, on F2/F3, and at startup;
  deaths are announced too. ToS posture unchanged (invariant 1).
- **Voices: OS-built-in only** (invariant 2 — no new deps). Windows: one
  persistent PowerShell child running System.Speech (per-utterance spawn
  costs ~1 s; the child loops on stdin and echoes `##DONE##` after each
  utterance). macOS dev: `say`. Elsewhere: silent no-op.
- **Latest-wins, interrupting:** a daemon worker thread + single pending
  slot; zoning again drops the stale step and kills the in-flight
  utterance (the voice must never narrate a zone you already left).
  Nothing blocks the poll path (invariant 5).
- **Step-advance detection stays out of `dispatch_events`:** its op list
  is a pinned contract (test_integration asserts exact ops); the tick
  compares `engine.i` before/after instead — also naturally skips the
  level-up `("refresh",)` which must not re-read the card.
- Off by default (`narration.enabled` in overlay/config.json); F8 repeat,
  F9 mute. Flip-on instructions for the beginner in START_HERE_EASY.md.
- **Deferred: periodic-screenshot LLM coaching.** Rejected for now:
  screen capture needs a non-stdlib dep (mss/Pillow), breaks the "reads
  Client.txt only" one-liner we tell friends, costs real money on a
  timer, and ~everything a screenshot shows (zone, level, deaths, gear
  via Ctrl+C) is already available as text. If coaching is wanted, build
  it as an on-demand hotkey using text state via `llm.client` first.
