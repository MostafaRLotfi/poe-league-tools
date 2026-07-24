"""Market data sources: poe.ninja economy API + official trade API helpers.

Everything in this module is *read-only public data* fetched over HTTP with
an identifying User-Agent and a hard rate floor (1 request / 2 s, global
concurrency 1, Retry-After/429 honored).  Nothing here sends messages,
whispers, or trades - see PLAN_ADDENDUM_LLM_MARKET.md section 4.0.

Endpoint verification (all GETs below live-verified on 2026-07-07):

* poe.ninja moved its data API under ``/poe1/``.  The legacy paths that
  older tooling documents - ``/api/data/index-state``,
  ``/api/data/currencyoverview`` and ``/api/data/itemoverview`` - now
  return HTTP 404.

* ``GET https://poe.ninja/poe1/api/data/index-state`` ->
  ``{"economyLeagues": [{"name","url","displayName"}, ...],
     "oldEconomyLeagues": [...], "snapshotVersions": [...],
     "buildLeagues": [...], "oldBuildLeagues": [...]}``.
  economyLeagues found on 2026-07-07 (in order): **Ancestors** (current
  challenge league), **Mirage** (event league, dying before the 3.29
  launch on Jul 24), Hardcore Ancestors, Hardcore Mirage, Standard,
  Hardcore.  ``discover_league()`` returned ``"Ancestors"``.

* ``GET https://poe.ninja/poe1/api/economy/stash/current/currency/overview
  ?league=<Name>&type=Currency|Fragment`` ->
  ``{"lines": [...], "currencyDetails": [...]}``.  Each line:
  ``currencyTypeName``, optional ``pay`` / ``receive`` blocks,
  ``chaosEquivalent``, ``detailsId``, sparklines.  Semantics (confirmed
  against live Divine Orb data, pay 0.002369 / receive 500.0 /
  chaosEquivalent 443):
    - ``receive``: pay_currency_id=1 (chaos), get_currency_id=<line
      currency>; ``value`` = chaos paid per 1 unit -> our **buy** price.
    - ``pay``: pay_currency_id=<line currency>, get_currency_id=1;
      ``value`` = units paid per 1 chaos -> our **sell** price is
      ``1 / value`` (chaos received selling 1 unit).
    - ``listing_count`` on each side -> buy_vol / sell_vol.

* ``GET https://poe.ninja/poe1/api/economy/exchange/current/overview
  ?league=<Name>&type=Scarab|Essence|DivinationCard`` ->
  ``{"core": {"items","rates","primary":"chaos","secondary":"divine"},
     "lines": [{"id","primaryValue","volumePrimaryValue",
                "maxVolumeCurrency","maxVolumeRate","sparkline"}, ...],
     "items": [{"id","name","image","category","detailsId"}, ...]}``.
  These three types 404 on the item overview endpoint - they are
  "exchange view" types on the site.  ``primaryValue`` is the aggregate
  chaos value per unit (single price; used for both buy and sell) and
  ``volumePrimaryValue`` is chaos-denominated volume.

* ``GET https://poe.ninja/poe1/api/economy/stash/current/item/overview
  ?league=<Name>&type=UniqueWeapon`` -> ``{"lines": [...]}`` with the
  classic itemoverview line shape: ``name``, ``baseType``, ``links``,
  ``chaosValue``, ``divineValue``, ``count``, ``listingCount``,
  ``detailsId``, modifier lists, sparkline.

* The ``league`` query parameter is the case-sensitive league *name*
  from index-state (e.g. ``Ancestors``); the path segment ``current``
  is a literal (the site pins historic snapshots by replacing it with a
  version string such as ``2002-20260707-55341``).

* ``GET https://www.pathofexile.com/api/trade/data/stats`` ->
  ``{"result": [{"id","label","entries":[{"id","text","type"}, ...]}]}``
  (13 groups, e.g. pseudo/explicit/implicit; ~1.8 MB).

Import-safe: no network, no side effects at import time.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
MIN_INTERVAL_S = 2.0          # hard floor: 1 request / 2 s, regardless of headers
DEFAULT_TIMEOUT_S = 15.0
MAX_RETRIES_429 = 3

NINJA_API = "https://poe.ninja/poe1/api"
SOURCE_NINJA = "poe.ninja"
TRADE_STATS_URL = "https://www.pathofexile.com/api/trade/data/stats"


class SourceError(RuntimeError):
    """Any failure fetching or decoding a market data source."""


# --------------------------------------------------------------- rate gate
# Module-level so *every* fetcher in the process shares the same 2 s floor
# (invariant 3: global concurrency 1).  Timestamps come from whichever
# monotonic clock the fetcher was built with; tests reset via
# reset_rate_gate() and inject a fake clock.
_GATE: dict = {"last": None}
_LOCK = threading.Lock()


def reset_rate_gate() -> None:
    """Forget the last-request timestamp (test isolation helper)."""
    _GATE["last"] = None


def _retry_after_seconds(value) -> float:
    """Parse a Retry-After header: seconds or HTTP-date; floor on garbage."""
    if value is None or str(value).strip() == "":
        return MIN_INTERVAL_S
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(str(value))
        return max((dt - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return MIN_INTERVAL_S


def utc_now_iso() -> str:
    """Snapshot timestamp: ISO-8601 UTC to the second, e.g. 2026-07-07T20:00:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RateLimitedFetcher:
    """GET JSON with the mandatory User-Agent and the global 2 s floor.

    * Honors 429 + Retry-After: sleeps (at least the floor) and retries,
      up to ``max_retries`` times, then raises SourceError.
    * Any other HTTP error, network error, timeout, or JSON decode
      failure raises SourceError.
    * ``clock`` (monotonic), ``sleep`` and ``opener`` are injectable for
      offline tests.  The opener must behave like
      ``urllib.request.build_opener()``: ``open(request, timeout=...)``
      returning a response context manager with ``.read()``, and raising
      ``urllib.error.HTTPError`` for non-2xx statuses.
    """

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_S,
                 max_retries: int = MAX_RETRIES_429,
                 user_agent: str = USER_AGENT,
                 clock=time.monotonic, sleep=time.sleep, opener=None):
        self._timeout = timeout
        self._max_retries = max_retries
        self._user_agent = user_agent
        self._clock = clock
        self._sleep = sleep
        self._opener = opener if opener is not None else urllib.request.build_opener()

    def _throttle(self) -> None:
        last = _GATE["last"]
        now = self._clock()
        if last is not None:
            wait = MIN_INTERVAL_S - (now - last)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        _GATE["last"] = now

    def get_json(self, url: str, extra_headers: dict | None = None):
        """GET ``url`` and return the decoded JSON payload.

        ``extra_headers`` (optional) are merged into the request headers
        (e.g. a session Cookie); they never override the User-Agent.
        """
        retries = 0
        with _LOCK:                       # global concurrency 1
            while True:
                self._throttle()
                headers = dict(extra_headers) if extra_headers else {}
                headers.update({
                    "User-Agent": self._user_agent,
                    "Accept": "application/json",
                })
                request = urllib.request.Request(url, headers=headers)
                try:
                    with self._opener.open(request, timeout=self._timeout) as resp:
                        body = resp.read()
                except urllib.error.HTTPError as exc:
                    if exc.code == 429 and retries < self._max_retries:
                        retries += 1
                        pause = max(_retry_after_seconds(
                            exc.headers.get("Retry-After") if exc.headers else None),
                            MIN_INTERVAL_S)
                        self._sleep(pause)
                        continue
                    raise SourceError(f"GET {url} failed: HTTP {exc.code}") from exc
                except urllib.error.URLError as exc:
                    raise SourceError(f"GET {url} failed: {exc.reason}") from exc
                except (TimeoutError, OSError) as exc:
                    raise SourceError(f"GET {url} failed: {exc}") from exc
                try:
                    return json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise SourceError(f"GET {url}: response is not JSON") from exc


# ------------------------------------------------------------- normalizers
# Snapshot row shape == the snapshots table in docs/INTERFACES.md:
# {ts, source, league, item, buy, sell, buy_vol, sell_vol, raw}
# buy  = chaos to buy 1 unit; sell = chaos received selling 1 unit.

def _row(ts, league, item, buy, sell, buy_vol, sell_vol, raw_line) -> dict:
    return {
        "ts": ts, "source": SOURCE_NINJA, "league": league, "item": item,
        "buy": buy, "sell": sell, "buy_vol": buy_vol, "sell_vol": sell_vol,
        "raw": json.dumps(raw_line, separators=(",", ":"), sort_keys=True),
    }


def normalize_currency(payload: dict, league: str, ts: str) -> list[dict]:
    """currencyoverview lines -> snapshot rows (see module docstring).

    receive.value -> buy, 1/pay.value -> sell, listing counts -> volumes.
    Lines missing a direction get None for that side.
    """
    rows = []
    for line in payload.get("lines", []):
        name = line.get("currencyTypeName")
        if not name:
            continue
        receive = line.get("receive") or {}
        pay = line.get("pay") or {}
        buy = receive.get("value")
        buy_vol = receive.get("listing_count")
        pay_value = pay.get("value")
        sell = (1.0 / pay_value) if pay_value else None
        sell_vol = pay.get("listing_count") if pay_value else None
        rows.append(_row(
            ts, league, name,
            float(buy) if buy is not None else None,
            sell,
            float(buy_vol) if buy_vol is not None else None,
            float(sell_vol) if sell_vol is not None else None,
            line))
    return rows


def normalize_exchange(payload: dict, league: str, ts: str) -> list[dict]:
    """exchange overview lines -> snapshot rows.

    The exchange endpoint publishes one aggregate ``primaryValue`` (chaos
    per unit), so buy == sell; ``volumePrimaryValue`` (chaos-denominated
    volume) fills both volume columns.  Item names come from the
    payload's ``items`` id -> name map.
    """
    names = {i.get("id"): i.get("name") for i in payload.get("items", [])}
    rows = []
    for line in payload.get("lines", []):
        line_id = line.get("id")
        name = names.get(line_id) or line_id
        if not name:
            continue
        value = line.get("primaryValue")
        value = float(value) if value is not None else None
        vol = line.get("volumePrimaryValue")
        vol = float(vol) if vol is not None else None
        rows.append(_row(ts, league, name, value, value, vol, vol, line))
    return rows


def normalize_items(payload: dict, league: str, ts: str) -> list[dict]:
    """item overview lines -> snapshot rows.

    One aggregate ``chaosValue`` per line, so buy == sell;
    ``listingCount`` fills both volume columns.  Linked variants are
    disambiguated as "<name> <links>L" so they do not collide with the
    base listing under the (ts, source, item) primary key.
    """
    rows = []
    for line in payload.get("lines", []):
        name = line.get("name")
        if not name:
            continue
        links = line.get("links")
        if links:
            name = f"{name} {links}L"
        value = line.get("chaosValue")
        value = float(value) if value is not None else None
        vol = line.get("listingCount")
        vol = float(vol) if vol is not None else None
        rows.append(_row(ts, league, name, value, value, vol, vol, line))
    return rows


# ------------------------------------------------------------ poe.ninja
class NinjaClient:
    """Fetch + normalize poe.ninja economy overviews for one league."""

    CURRENCY_TYPES = ("Currency", "Fragment")
    # Types served by the exchange endpoint (item/overview 404s for these):
    EXCHANGE_ITEM_TYPES = ("Scarab", "Essence", "DivinationCard")
    # Types served by the stash item overview endpoint:
    STASH_ITEM_TYPES = ("UniqueWeapon",)
    ITEM_TYPES = EXCHANGE_ITEM_TYPES + STASH_ITEM_TYPES

    def __init__(self, league: str, fetcher: RateLimitedFetcher | None = None):
        self.league = league
        self.fetcher = fetcher if fetcher is not None else RateLimitedFetcher()

    # --- URLs -----------------------------------------------------------
    def _url(self, path: str, type_: str) -> str:
        query = urllib.parse.urlencode(
            {"league": self.league, "type": type_},
            quote_via=urllib.parse.quote)
        return f"{NINJA_API}{path}?{query}"

    def currency_overview_url(self, type_: str = "Currency") -> str:
        return self._url("/economy/stash/current/currency/overview", type_)

    def item_overview_url(self, type_: str) -> str:
        if type_ in self.EXCHANGE_ITEM_TYPES:
            return self._url("/economy/exchange/current/overview", type_)
        return self._url("/economy/stash/current/item/overview", type_)

    # --- raw fetches ----------------------------------------------------
    def fetch_currency_overview(self, type_: str = "Currency") -> dict:
        payload = self.fetcher.get_json(self.currency_overview_url(type_))
        if not isinstance(payload, dict) or "lines" not in payload:
            raise SourceError(f"currency overview ({type_}): unexpected shape")
        return payload

    def fetch_item_overview(self, type_: str) -> dict:
        payload = self.fetcher.get_json(self.item_overview_url(type_))
        if not isinstance(payload, dict) or "lines" not in payload:
            raise SourceError(f"item overview ({type_}): unexpected shape")
        return payload

    # --- snapshot rows --------------------------------------------------
    def snapshot_currency(self, type_: str = "Currency",
                          ts: str | None = None) -> list[dict]:
        payload = self.fetch_currency_overview(type_)
        return normalize_currency(payload, self.league, ts or utc_now_iso())

    def snapshot_items(self, type_: str, ts: str | None = None) -> list[dict]:
        payload = self.fetch_item_overview(type_)
        normalize = (normalize_exchange if type_ in self.EXCHANGE_ITEM_TYPES
                     else normalize_items)
        return normalize(payload, self.league, ts or utc_now_iso())


def fetch_index_state(fetcher: RateLimitedFetcher | None = None) -> dict:
    """GET the poe.ninja index-state document (league catalog)."""
    fetcher = fetcher if fetcher is not None else RateLimitedFetcher()
    payload = fetcher.get_json(f"{NINJA_API}/data/index-state")
    if not isinstance(payload, dict) or "economyLeagues" not in payload:
        raise SourceError("index-state: unexpected shape")
    return payload


def discover_league(fetcher: RateLimitedFetcher | None = None,
                    index_state: dict | None = None) -> str:
    """Return the current temp challenge league name (e.g. "Ancestors").

    Picks the first economyLeagues entry that is not Hardcore / Standard /
    SSF / Ruthless; falls back to the first entry.
    """
    payload = index_state if index_state is not None else fetch_index_state(fetcher)
    leagues = payload.get("economyLeagues") or []
    skip = ("Hardcore", "Standard", "SSF", "Ruthless")
    for entry in leagues:
        name = entry.get("name", "")
        if name and not any(tok in name for tok in skip):
            return name
    if leagues and leagues[0].get("name"):
        return leagues[0]["name"]
    raise SourceError("index-state: no economy leagues found")


# ------------------------------------------------------- official trade API
def fetch_stats(fetcher: RateLimitedFetcher | None = None) -> dict:
    """GET the public trade stats catalog (live-verified 2026-07-07).

    Returns the raw ``{"result": [{"id","label","entries": [...]}, ...]}``
    document; consumers (tools/tradeq.py) cache it to data/trade_stats.json.
    """
    fetcher = fetcher if fetcher is not None else RateLimitedFetcher()
    payload = fetcher.get_json(TRADE_STATS_URL)
    if not isinstance(payload, dict) or "result" not in payload:
        raise SourceError("trade stats: unexpected shape")
    return payload


def fetch_bulk_exchange_quote(league: str, have: str, want: str,
                              fetcher: RateLimitedFetcher | None = None):
    """DISABLED stub: direction-specific bulk-exchange quotes. Never sends.

    This would be a *read-only search* (quotes only - whispering and
    trading always stay human), but it is a POST endpoint governed by the
    trade site's X-Rate-Limit-* token buckets, which need dedicated
    budget handling before we dare poll it.  Deliberately disabled until
    the Mirage rehearsal proves out a token-bucket implementation.

    VERIFY: shape below is from community documentation of the trade
    site's network calls, NOT live-verified (we did not POST):

        POST https://www.pathofexile.com/api/trade/exchange/{league}
        Content-Type: application/json
        {"query": {"status": {"option": "online"},
                   "have": ["chaos"], "want": ["divine"],
                   "minimum": 1},
         "sort": {"have": "asc"}, "engine": "new"}

        -> {"id": "<search-id>", "complexity": null, "total": <n>,
            "result": {"<listing-id>": {"id": "...", "item": null,
              "listing": {"indexed": "...", "account": {...},
                "offers": [{"exchange": {"currency": "chaos",
                                         "amount": <pay>},
                            "item": {"currency": "divine",
                                     "amount": <get>,
                                     "stock": <n>}}]}}, ...}}

    Rate headers observed on GETs to the same host: none on
    /api/trade/data/stats; the search/exchange endpoints are documented
    to return X-Rate-Limit-Ip / X-Rate-Limit-Ip-State and Retry-After
    on 429 (VERIFY against live headers during the rehearsal).
    """
    raise NotImplementedError(
        "bulk-exchange quotes are disabled: POST rate budgets not yet "
        "verified (see docstring); use poe.ninja snapshots instead")
