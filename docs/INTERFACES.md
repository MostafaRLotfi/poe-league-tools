# Interfaces & Conventions — contract for parallel module work

This file is the coordination contract used while building out the toolkit.
Every module builder MUST follow it. When a module and this file disagree,
this file wins; propose changes here rather than silently diverging.

## Invariants (never violate)

1. **Client.txt is read-only.** No game memory, no process injection, no
   synthesized input into the game or any website. No automated messaging,
   whispering, or trade execution — a human performs every send and trade.
2. **Dependencies:** Python 3.10+ stdlib only, with two exceptions:
   PyQt6 (only in `overlay/` UI files) and the `anthropic` SDK (only inside
   `llm/client.py`, behind a guarded import — see below).
3. **Rate limits:** any HTTP client honors `Retry-After`/429, sends
   User-Agent `poe-league-tools/1.0 (contact: cyrus@hadavi.net)`, global
   concurrency 1, hard floor 1 request / 2 seconds regardless of headers.
4. **LLM access only via `llm.client.LLM`.** Every LLM feature must degrade
   gracefully (documented per feature) when LLM is unavailable.
5. **Nothing blocking on the overlay's 300 ms poll path** — no network, no
   LLM calls on that path; hand off to threads/queues.

## Test conventions

- Assert-style headless scripts like `tests/test_core.py`: plain asserts,
  `print("ALL TESTS PASSED")` at the end, runnable via
  `.venv/bin/python tests/test_<name>.py`.
- Same `sys.path` bootstrap pattern as `tests/test_core.py`.
- No network and no Qt imports in tests. Network code is tested against
  fixtures under `tests/fixtures_<name>/`.
- One test file per module, named in the ownership table below.

## File ownership (during the parallel build)

| Owner | Files |
|---|---|
| routes workstream | `routes/act2..10.json`, `routes/act1.json` (arealvl only), `routes/schema.md`, `tests/test_routes_all.py`, `tests/test_core.py` |
| llm-client | `llm/client.py`, `llm/config.json`, `tools/llm_report.py`, `tests/test_llm.py` |
| item-eval | `overlay/itemtext.py`, `overlay/item_rules.py`, `data/resist_budget.json`, `tests/fixtures_items/`, `tests/test_items.py` |
| market-sources | `market/sources.py`, `tests/fixtures_market/`, `tests/test_sources.py` |
| market-store | `market/store.py`, `market/daemon.py`, `market/config.json`, `tests/test_store.py` |
| market-scanner | `market/scanner.py`, `tests/test_scanner.py` |
| market-console | `market/console.py`, `tools/pnl.py`, `tests/test_console.py` |
| run-tracker | `overlay/run_tracker.py`, `tests/test_tracker.py` |
| advisor | `advisor/*.py`, `buildgen/pob.py` (extend only), `buildgen/party.py` (extend only), `tests/test_advisor.py` |
| retro | `tools/retro.py`, `tests/test_retro.py` |
| tradeq | `tools/tradeq.py`, `data/trade_stats.json`, `tests/test_tradeq.py` |
| brief | `market/brief.py`, `market/prompts.py`, `tests/test_brief.py` |
| route-verifier-llm | `tools/verify_routes_llm.py`, `data/wiki_cache/`, `tests/test_verify_routes.py` |
| meta-ranker | `tools/meta.py`, `tests/test_meta.py` |
| craft | `craft/*.py`, `tools/refresh_repoe.py`, `tools/craft_check.py`, `data/repoe_craft.json`, `data/craft_recipes.json`, `tests/fixtures_craft/`, `tests/test_craft.py` |
| layouts | `overlay/layout_index.py`, `overlay/layout_panel.py`, `overlay/ui_state.py`, `tools/fetch_layouts.py`, `tools/crosscheck_routes.py`, `data/exileui/`, `tests/test_layouts.py` |
| integration (later) | `overlay/main.py`, `overlay/overlay_window.py`, `overlay/config.json`, `README.md`, `tools/check.py`, `requirements.txt`, `setup_pc.bat` |
| upgrade-checker | `buildgen/gear.py`, `tools/upgrade_check.py`, `tests/fixtures_gear/`, `tests/test_gear.py` |
| chaos-recipe | `market/stashapi.py`, `tools/chaos_recipe.py`, `tests/fixtures_stash/`, `tests/test_chaos_recipe.py` |
| filtergen | `filtergen/*.py`, `tools/filter_update.py`, `data/filter_tiers.json`, `tests/fixtures_filtergen/`, `tests/test_filtergen.py` |
| mechanic-tracker | `overlay/mechanic_tracker.py`, `tools/allflame.py`, `data/3.29/mechanic_lines.json`, `tests/fixtures_mechanic/`, `tests/test_mechanic_tracker.py` |

Do not create or edit files outside your row. `data/` files not listed are
free for their owner. Directories are created implicitly by writing files.

## `llm/client.py` — shared LLM wrapper (the one API everyone codes against)

```python
from llm.client import LLM, LLMDisabled, LLMError

llm = LLM("standard")                      # tier: "fast" | "standard" | "deep"
text = llm.complete(
    system="...",                          # system prompt (str)
    messages=[{"role": "user", "content": "..."}],   # or a plain str shortcut
    max_tokens=1024,
    feature="item_eval",                   # usage-meter tag, required
    json_schema=None,                      # if given -> returns validated dict
)
```

- Tiers map to model IDs in `llm/config.json`; defaults:
  `fast` = `claude-haiku-4-5`, `standard` = `claude-sonnet-5`,
  `deep` = `claude-opus-4-8`.
- Implementation: official `anthropic` Python SDK behind a guarded import.
  `client.messages.create(model=..., max_tokens=..., system=..., messages=...)`.
  Read text from blocks with `block.type == "text"`. Check
  `response.stop_reason == "refusal"` → raise `LLMError`.
- `json_schema` given → pass `output_config={"format": {"type": "json_schema",
  "schema": ...}}`, `json.loads` the text, minimal structural validation
  (required keys / types), one reprompt-with-error retry, then raise `LLMError`.
- **Kill switch / degrade:** raise `LLMDisabled` (subclass of `RuntimeError`)
  when `POE_TOOLS_LLM=off`, the `anthropic` package is missing, or no
  `ANTHROPIC_API_KEY`. Callers MUST catch `LLMDisabled` and degrade.
- Transient errors: rely on SDK retries (`max_retries=3`); after that raise
  `LLMError`.
- **Usage meter:** append one JSON line per call to `llm_usage.jsonl` (repo
  root): `{"ts", "feature", "tier", "model", "in_tokens", "out_tokens"}`.
  `tools/llm_report.py` prints spend by feature (tokens and est. $ using
  prices in `llm/config.json`).

## Data formats

### Route step (routes/actN.json)

```json
{"zone": "The Coast", "kind": "travel|kill|town|trial",
 "do": ["..."], "layout": "optional", "tip": "optional", "arealvl": 2}
```

`zone` must exactly match the "You have entered X." Client.txt line.
`arealvl` = the zone's monster level (omit for towns if unknown).
Optional side areas go inside a `do` item of an adjacent required step —
never their own step (auto-advance lookahead is 4).

### Client.txt events (overlay/client_watcher.py — stable, do not change)

`("zone", name)`, `("level", (name, cls, lvl))`, `("join", name)`,
`("leave", name)`, `("slain", name)`.

### Run file (runs/run_<startts>.json; PB at runs/pb.json)

```json
{"league": "3.29", "character": "Name", "class": "Witch",
 "started": "2026-07-24T20:00:00", "ended": null,
 "splits": [{"act": 1, "t": 2710, "level": 12}],
 "levels": [{"level": 2, "t": 95}],
 "deaths": [{"t": 3100, "who": "Name"}]}
```

`t` = seconds since run start. `overlay/run_tracker.py` owns writing;
`tools/retro.py` consumes. Tracker is pure logic: it takes events + a
monotonic clock callable, returns display strings; file IO isolated in
small save/load helpers.

### Market DB (market/market.db, SQLite, gitignored)

```sql
CREATE TABLE snapshots(ts TEXT, source TEXT, league TEXT, item TEXT,
  buy REAL, sell REAL, buy_vol REAL, sell_vol REAL, raw TEXT,
  PRIMARY KEY(ts, source, item));
CREATE TABLE opportunities(id TEXT PRIMARY KEY, ts TEXT, kind TEXT, path TEXT,
  margin_pct REAL, est_profit_c REAL, liq_score REAL, confidence TEXT, flags TEXT);
CREATE TABLE executions(id TEXT PRIMARY KEY, opp_id TEXT, ts TEXT,
  legs TEXT, realized_profit_c REAL, minutes REAL, notes TEXT,
  expected_profit_c REAL, kind TEXT);
```

`snapshots.buy_vol`/`sell_vol` are **as published by the source**: listing
counts for poe.ninja currency/stash rows, chaos-denominated volume for
exchange rows (`volumePrimaryValue` present in `raw`). The scanner
normalizes both to chaos depth (count × unit price) before any gating,
sizing or ranking. On **pair rows** (`item` contains `->`) `sell_vol` is
already chaos-denominated depth.

`executions.expected_profit_c`/`kind` snapshot the opportunity **as seen
at journal time**: opportunity ids are stable per path and rescans
INSERT-OR-REPLACE `est_profit_c`, so PnL calibration must not read the
live opportunities row (tools/pnl.py falls back to the join only for
legacy rows without the snapshot columns).

### Opportunity object (scanner output; console + brief consume)

```json
{"id": "...", "kind": "cycle|spread", "path": ["chaos->divine", "..."],
 "margin_pct": 6.2, "est_profit_c": 140, "est_profit_per_hour": 900,
 "liq_score": 0.7, "confidence": "high|low", "flags": ["price_fixing_suspect"],
 "actions": [{"type": "whisper", "text": "..."} , {"type": "exchange", "instruction": "..."}]}
```

Scanner parameters live in `market/config.json`:
`{"league": "...", "bankroll_c": 2000, "haircut": 0.04, "min_margin_pct": 5,
  "min_vol": 20, "poll_currency_s": 300, "poll_items_s": 1800}`.

### Item evaluator

`overlay/itemtext.py: parse(text) -> dict | None` for the game's Ctrl+C
format (blocks split by `--------` lines). Parsed dict includes at least:
`item_class, rarity, name, base, ilvl, sockets, links, mods (list[str])`,
and derived `props`: `life, movespeed, res: {fire, cold, lightning, chaos}`.
`overlay/item_rules.py: evaluate(parsed, ctx) -> (verdict, reason)` where
verdict ∈ `"TAKE" | "SKIP" | "CHECK"` and ctx =
`{"level": int, "act": int, "links_best": int, "build": dict|None}`.
Per-act resist budget table in `data/resist_budget.json`. Pure stdlib, no
Qt — the clipboard hook is wired by integration, not here.

### Crafting copilot (craft/, addendum 5G)

Dataset `data/repoe_craft.json` compiled by `tools/refresh_repoe.py` from
https://repoe-fork.github.io/ (static JSON, no auth; refetch after each
game patch — format documented in the tool's docstring).

`craft.pool.CraftData.load(path=None)`; `match_item(parsed)` identifies a
parsed item's mod lines (tier per trade convention, prefix/suffix, origin
roll/essence/special/bench/implicit) and estimates open affix slots;
`pool(base, ilvl)` = rollable ladders (domain- and tag-filtered, first-
matching-spawn-weight semantics); `essences_for(cls, ilvl)`,
`bench_for(cls)`. `craft.copilot.advise(parsed, ctx, data=None,
recipes=None, llm_factory=None) -> {digest, text, plan, llm_note}` —
deterministic digest always; LLM (standard tier, feature `craft_copilot`,
schema-validated plan) degrades to digest-only on LLMDisabled/LLMError.
All numbers come from the dataset; the LLM only selects and explains.
CLI: `tools/craft_check.py`. Recipes: `data/craft_recipes.json`
(authored; `craft.recipes.applicable(recipes, cls, level)`).

`itemtext.parse` additionally returns `mod_tags`: the parenthetical tag
per mod line ("implicit", "crafted", "fractured", ...; "" = explicit),
aligned with `mods`. Existing consumers of `mods` are unaffected.

### Live-search monitor (market/livesearch.py + tools/snipe.py)

Official trade-site live search -> Alert objects -> human action. NEVER
buys, whispers, or touches the game client (invariant: every trade is a
human action; with Merchant's Tabs the human step is one buy click).
`SearchSpec(search_id, label)`; `LiveSearchMonitor(specs, league,
session_id, on_alert, connector=None, fetcher=None, sleep=...)` — one
thread per search, reconnect with 2→60 s doubling backoff, per-search
dedupe (bounded 4096 ids), fetches share a global 1 s floor and honor
Retry-After / X-Rate-Limit-* (market/ratelimit.py). `create_search(query,
league) -> id` (unauthenticated POST, same endpoint as tradeq --post).
Auth: POESESSID env var only, never persisted. Transport: optional
`websocket-client`; without it (or without POESESSID) everything raises
`LiveSearchUnavailable` with instructions — callers degrade. CLI:
`tools/snipe.py --search-id ID | --query file.json [--open]
[--copy-whisper] [--log x.jsonl] [--probe]`; --probe prints the first raw
WS frame per search (rehearsal verification). VERIFY at rehearsal: WS
message shapes, ping cadence, fetch-response Merchant's-Tab/buyout
marker, live rate-limit headers. Tests inject connector/fetcher/urlopen;
offline always.

### Watchlist (market/watchlist.json)

`[{"item": "...", "reason": "...", "source": "...", "expected_window": "..."}]`
Every entry cites `data/3.29/summary.json` or is tagged `"source": "assumption"`.

### Patch-note summary (data/3.29/summary.json — advisor owns the writer)

`{"patch": "3.29", "items": [{"id": "...", "kind": "skill|support|unique|base|keystone|mechanic",
  "change": "...", "direction": "buff|nerf|neutral", "quote": "...", "source": "..."}]}`
Produced by `advisor/summarize.py` from pasted patch notes (July 16).
Consumers must degrade when the file is absent.

### Upgrade checker (buildgen/gear.py + tools/upgrade_check.py)

`gear.parse_pob_item(text)` parses PoB `<Item>` text blocks into dicts
shape-compatible with `itemtext.parse` output (plus `implicit_mods` and
an `estimated` flag when `(lo-hi)` ranges were resolved via `{range:X}`
or midpoint). `gear.slot_map(root)` maps the ACTIVE `<ItemSet>` (matched
by **id**, not document order — live-verified against the party's pobb.in
exports) to parsed items; `gear.member_builds(bundle)` resolves each
member's PoB and caches decoded XML as `<Player>.pobxml` beside the
bundle so later runs are offline (`--refresh` refetches). `compare()`
diffs life/res/movespeed/links + mod gains/losses; its verdict score is
a defensive-stat heuristic, NOT a PoB DPS calculation. Optional LLM
one-liner (feature `upgrade_check`, standard tier) degrades silently.

### Stash API + chaos recipe (market/stashapi.py + tools/chaos_recipe.py)

`fetch_stash_tab(account, league, tab_index)` GETs the legacy
`character-window/get-stash-items` endpoint with `Cookie: POESESSID`
(env var only, never persisted; missing -> `StashUnavailable` with
instructions, mirroring livesearch). Endpoint shape is community-
documented — VERIFY at 3.29 launch. Reuses the global
`RateLimitedFetcher` gate; `get_json` grew an optional `extra_headers`
kwarg (merged into request headers, never overriding the User-Agent).
Recipe math is pure: rare + ilvl 60–74, slot classification via
repoe base->class lookup with keyword fallback, sets need
body/helm/gloves/boots/belt/amulet/2 rings + one 2H (incl. bow) or two
1H-equivalents (shield counts); unid full set = 2c, mixed = 1c.

### Economy filter block (filtergen/ + tools/filter_update.py)

`filtergen.economy` turns snapshot rows into a marked first-match-wins
block spliced at the TOP of a loot filter: tiers from
`data/filter_tiers.json` (authored `min_chaos` thresholds + style
lines), `BaseType ==` exact-match rules (div cards additionally carry
`Class == "Divination Cards"`), links-suffixed and unique-overview rows
excluded. Markers:
`# >>> poe-league-tools economy block — generated <ts> — league <name>`
… `# <<< poe-league-tools economy block`. `splice()` replaces an
existing region byte-preserving and idempotently; no markers ->
refusal unless `--install`. Price sources: `--offline-rows` >
`--live` (NinjaClient) > newest market.db rows. The tool only writes
filter files; in-game reload is always manual.

### Mechanic tracker (overlay/mechanic_tracker.py + tools/allflame.py)

Pure logic, clock-free (time parsed from Client.txt line timestamps);
config `data/3.29/mechanic_lines.json`:
`{league, voyage_zone_patterns, npc_line_patterns:
[{id, pattern, kind}], town_zones, notes}` — mechanic patterns ship as
VERIFY placeholders until launch-night calibration via
`tools/allflame.py candidates` (ranks field NPC dialogue, suppresses
vendor chatter). Encounters dedupe per (pattern id, zone visit);
active time excludes towns/hideouts. Manual loot log:
`runs/allflame_loot.jsonl` rows `{ts, chaos, raw, note}` via
`tools/allflame.py loot 15c <note>` (`Ndiv` needs `--div-rate`).

## Reserved overlay/config.json keys (wired by integration)

`item_eval` (bool), `timer` (bool), `runs_dir` (str).
(`llm_ask` was reserved here during the build but never implemented —
no code reads it; setting it does nothing.)
Modules must NOT read overlay/config.json directly — accept parameters.

## Network fixture policy

Live fetches during the build are allowed for endpoint verification and
fixture capture only (poe.ninja, official trade stats endpoint, poewiki) —
under the rate rules in invariant 3. Tests never hit the network. Anything
that couldn't be live-verified is flagged with a `VERIFY:` comment.
