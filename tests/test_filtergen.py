"""Headless tests: economy loot-filter block generation (filtergen/)."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]

from filtergen import economy                  # noqa: E402
import filter_update                           # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures_filtergen")
ROWS_PATH = os.path.join(FIXTURES, "rows.json")
BASE_FILTER_PATH = os.path.join(FIXTURES, "base_filter.filter")

with open(ROWS_PATH, encoding="utf-8") as f:
    ROWS = json.load(f)
TIERS = economy.load_tiers(os.path.join(ROOT, "data", "filter_tiers.json"))

# ------------------------------------------------- shipped tiers file sanity
assert [t["id"] for t in TIERS] == ["top", "high", "notable"]
assert [t["min_chaos"] for t in TIERS] == [100, 20, 5]
assert all(t["style"] for t in TIERS), "every tier ships style lines"

# --------------------------------------------------------------- price rule
assert economy.price_of({"buy": 505.0, "sell": 500.0}) == 500.0
assert economy.price_of({"buy": 60.0, "sell": None}) == 60.0, \
    "price falls back to buy when sell is missing"
assert economy.price_of({"buy": None, "sell": None}) is None

# ---------------------------------------------------------- tier assignment
assignments = economy.assign_tiers(ROWS, TIERS)
assert assignments == {
    "top": ["Divine Orb", "Sacred Orb"],
    "high": ["Awakened Sextant", "Fracturing Orb", "Tempering Orb",
             "The Enlightened", "Titanic Scarab"],
    "notable": ["Deafening Essence of Contempt"],
}, f"unexpected assignment: {assignments}"
# encoded in that exactness:
#  - "Whispering Essence of Woe" (1c) rejected below every tier
#  - "Shavronne's Wrappings 6L" links row excluded
#  - "Bottled Faith" (stash item-overview / unique row) excluded
#  - "Blessing of Chayula" (null price) excluded
#  - "chaos->divine" pair row excluded
#  - duplicate "Sacred Orb" (25c + 150c sources) deduped, highest tier wins

cards = economy.card_basetypes(ROWS)
assert cards == {"The Enlightened"}, f"card detection: {cards}"

# min-vol gating: listing counts are chaos-normalized (count x price),
# exchange volumes are already chaos. At 100c depth:
#   Tempering Orb (2 listings x 30c = 60c) and Awakened Sextant (unknown
#   volume) drop; everything else has >= 100c depth and stays.
gated = economy.assign_tiers(ROWS, TIERS, min_vol_chaos=100)
assert gated == {
    "top": ["Divine Orb", "Sacred Orb"],
    "high": ["Fracturing Orb", "The Enlightened", "Titanic Scarab"],
    "notable": ["Deafening Essence of Contempt"],
}, f"unexpected gated assignment: {gated}"

# empty input still yields a complete (empty) tier map
assert economy.assign_tiers([], TIERS) == \
    {"top": [], "high": [], "notable": []}

# ---------------------------------------------------------------- rendering
TS = "2026-07-24T12:00:00Z"
block = economy.render_block(assignments, TIERS, "TestLeague", TS,
                             cards=cards)
lines = block.splitlines()
assert lines[0] == ("# >>> poe-league-tools economy block — generated "
                    "2026-07-24T12:00:00Z — league TestLeague")
assert lines[1] == ("# >>> do not edit inside this block; regenerate with "
                    "tools/filter_update.py")
assert lines[-1] == "# <<< poe-league-tools economy block"
assert block.endswith("# <<< poe-league-tools economy block\n")

# exact rules: quoted, exact-match operator, cards split out with Class
assert '    BaseType == "Divine Orb" "Sacred Orb"' in lines
assert ('    BaseType == "Awakened Sextant" "Fracturing Orb" '
        '"Tempering Orb" "Titanic Scarab"') in lines
assert ('Show\n'
        '    Class == "Divination Cards"\n'
        '    BaseType == "The Enlightened"\n') in block
assert '    BaseType == "Deafening Essence of Contempt"' in lines
assert block.count("Show") == 4, \
    "exactly: top bases, high bases, high cards, notable bases"

# styles are attached per tier, verbatim from data/filter_tiers.json
top_rule_at = block.index('"Divine Orb"')
high_rule_at = block.index('"Titanic Scarab"')
notable_rule_at = block.index('"Deafening Essence')
assert top_rule_at < high_rule_at < notable_rule_at, \
    "tiers render top-first (loudest first)"
assert block.index("    PlayAlertSound 6 300") < high_rule_at
assert "    PlayEffect Blue Temp" in lines

# names excluded from assignment never leak into the block
for absent in ("Shavronne", "Bottled Faith", "6L", "Whispering",
               "chaos->divine", "Blessing of Chayula"):
    assert absent not in block, f"{absent!r} must not appear in the block"

# a card-only tier still renders (no empty base rule emitted)
card_only = economy.render_block(
    {"top": [], "high": ["The Enlightened"], "notable": []},
    TIERS, "L", TS, cards={"The Enlightened"})
assert card_only.count("Show") == 1 and "Divination Cards" in card_only

# ----------------------------------------------------------------- splicing
with open(BASE_FILTER_PATH, encoding="utf-8", newline="") as f:
    base_filter = f.read()

spliced = economy.splice(base_filter, block)
assert "Stale Orb" not in spliced, "old block content is replaced"
assert block in spliced
prefix = base_filter[:base_filter.index("# >>> poe-league-tools")]
suffix = base_filter[base_filter.index("# <<< poe-league-tools economy block")
                     + len("# <<< poe-league-tools economy block\n"):]
assert spliced == prefix + block + suffix, \
    "everything outside the markers is preserved byte-for-byte"
assert spliced.startswith("# My league filter")
assert spliced.endswith("    SetFontSize 18\n")

# idempotent: splicing the same block again changes nothing
assert economy.splice(spliced, block) == spliced

# no markers -> ValueError unless install=True (which prepends block + blank)
plain = "Show\n    Class \"Currency\"\n"
try:
    economy.splice(plain, block)
    raise AssertionError("splice without markers must raise ValueError")
except ValueError as exc:
    assert "--install" in str(exc)
installed = economy.splice(plain, block, install=True)
assert installed == block + "\n" + plain
assert economy.splice(installed, block) == installed, \
    "install then re-splice is idempotent"

# damaged region: begin marker without end marker -> ValueError
try:
    economy.splice("# >>> poe-league-tools economy block — x\nShow\n", block)
    raise AssertionError("missing end marker must raise ValueError")
except ValueError as exc:
    assert "end marker" in str(exc)

# CRLF filters: surrounding bytes (incl. \r\n) survive the splice
crlf = ("Hide\r\n\tBaseType \"Wisdom\"\r\n"
        "# >>> poe-league-tools economy block — old\r\n"
        "junk\r\n"
        "# <<< poe-league-tools economy block\r\n"
        "Show\r\n")
crlf_out = economy.splice(crlf, block)
assert crlf_out.startswith("Hide\r\n\tBaseType \"Wisdom\"\r\n")
assert crlf_out.endswith(block + "Show\r\n")

# ------------------------------------------------------------------- CLI
tmp = tempfile.mkdtemp(prefix="poe_filtergen_test_")
try:
    def run_cli(argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = filter_update.main(argv)
        return rc, out.getvalue()

    # standalone block file from offline rows, end to end
    out_path = os.path.join(tmp, "economy_block.filter")
    rc, printed = run_cli(["--offline-rows", ROWS_PATH, "--league",
                           "TestLeague", "--out", out_path])
    assert rc == 0, printed
    with open(out_path, encoding="utf-8", newline="") as f:
        written = f.read()
    assert written.startswith("# >>> poe-league-tools economy block")
    assert written.endswith("# <<< poe-league-tools economy block\n")
    assert "league TestLeague" in written.splitlines()[0]
    assert '    BaseType == "Divine Orb" "Sacred Orb"' in written
    assert ('    Class == "Divination Cards"\n'
            '    BaseType == "The Enlightened"') in written
    assert "2 top / 5 high / 1 notable" in printed
    assert "Reload Filter" in printed and "manual" in printed.lower(), \
        "the manual in-game reload reminder must be printed"

    # --min-vol drops illiquid lines end to end
    rc, printed = run_cli(["--offline-rows", ROWS_PATH, "--league",
                           "TestLeague", "--out", out_path,
                           "--min-vol", "100"])
    assert rc == 0 and "Tempering Orb" not in open(
        out_path, encoding="utf-8").read()
    assert "2 top / 3 high / 1 notable" in printed

    # splice mode: backup written, block replaced, rest preserved
    target = os.path.join(tmp, "my.filter")
    shutil.copyfile(BASE_FILTER_PATH, target)
    rc, printed = run_cli(["--offline-rows", ROWS_PATH, "--league",
                           "TestLeague", "--filter", target])
    assert rc == 0, printed
    with open(target + ".bak", encoding="utf-8", newline="") as f:
        assert f.read() == base_filter, ".bak is the pre-splice original"
    with open(target, encoding="utf-8", newline="") as f:
        merged = f.read()
    assert "Stale Orb" not in merged and "Sacred Orb" in merged
    assert merged.startswith("# My league filter")
    assert merged.endswith("    SetFontSize 18\n")

    # refusal: no markers and no --install leaves the filter untouched
    bare = os.path.join(tmp, "bare.filter")
    with open(bare, "w", encoding="utf-8", newline="") as f:
        f.write(plain)
    rc, printed = run_cli(["--offline-rows", ROWS_PATH, "--league",
                           "TestLeague", "--filter", bare])
    assert rc == 2 and "--install" in printed
    assert open(bare, encoding="utf-8", newline="").read() == plain

    # ...and --install performs the first-time top insertion
    rc, printed = run_cli(["--offline-rows", ROWS_PATH, "--league",
                           "TestLeague", "--filter", bare, "--install"])
    assert rc == 0, printed
    installed_cli = open(bare, encoding="utf-8", newline="").read()
    assert installed_cli.startswith("# >>> poe-league-tools economy block")
    assert installed_cli.endswith(plain), "base filter follows the block"

    # default source: latest rows per item from a market.db via market.store
    from market.store import Store
    db_path = os.path.join(tmp, "market.db")
    with Store(db_path) as store:
        assert store.insert_snapshots(ROWS) == len(ROWS)
    rc, printed = run_cli(["--db", db_path, "--league", "TestLeague",
                           "--out", out_path])
    assert rc == 0, printed
    db_written = open(out_path, encoding="utf-8").read()
    assert '"Divine Orb" "Sacred Orb"' in db_written, \
        "DB rows (incl. cross-source dedupe) drive the block"
    assert "Divination Cards" in db_written

    # DB rows for another league are ignored (with a helpful failure)
    rc, printed = run_cli(["--db", db_path, "--league", "OtherLeague",
                           "--out", out_path])
    assert rc == 2 and "OtherLeague" in printed

    # missing DB: instruct to use --live instead of inventing prices
    rc, printed = run_cli(["--db", os.path.join(tmp, "nope.db"),
                           "--league", "TestLeague", "--out", out_path])
    assert rc == 2 and "--live" in printed
    assert not os.path.exists(os.path.join(tmp, "nope.db")), \
        "the tool must not create a market DB as a side effect"
finally:
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print(f"  sample block: {len(block.splitlines())} lines, "
      f"{sum(len(v) for v in assignments.values())} basetypes, "
      f"{len(cards)} card(s)")
