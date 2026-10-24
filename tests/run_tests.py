"""Plain-assert test run: no pytest, no deps.

    python tests/run_tests.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fivethings import brief as brief_mod
from fivethings.brief import brief, build_prompt, make_card, parse_card, quote_gate
from fivethings.scan import score_note, scan

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLE = os.path.join(ROOT, "sample", "notes")

PASSED = []
FAILED = []


def check(name):
    def deco(fn):
        try:
            fn()
            PASSED.append(name)
        except AssertionError as exc:
            FAILED.append((name, str(exc) or "assertion failed"))
        return fn
    return deco


class FakeEngine:
    """Answers with whatever the test hands it, and counts the calls."""

    def __init__(self, answer=None, delay=0.0):
        self.answer = answer or '{"what":"a note","next":"do the thing","quote":"%s"}'
        self.delay = delay
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.seconds = 0.0

    def chat(self, prompt, max_tokens=200, temperature=0.0):
        self.calls += 1
        self.prompt_tokens += 100
        self.completion_tokens += 20
        self.seconds += self.delay
        if self.delay:
            time.sleep(self.delay)
        return self.answer

    def tok_per_second(self):
        return 0.0


@check("scan finds the sample notes")
def _():
    cands, skipped = scan(SAMPLE)
    assert len(cands) >= 10, "expected at least 10 candidates, got %d" % len(cands)
    sentinel = [s for s in skipped if s["path"] == "already-done.md"]
    assert sentinel, "the note marked done must be skipped"
    assert "marked done" in sentinel[0]["why"]


@check("empty and near-empty notes do not reach the top")
def _():
    cands, _ = scan(SAMPLE)
    paths = [c["path"] for c in cands[:5]]
    assert "tiny.txt" not in paths, "a 14 byte note should not outrank real ones"


@check("recent notes with open markers outrank stale ones")
def _():
    cands, _ = scan(SAMPLE)
    order = {c["path"]: c["score"] for c in cands}
    assert order["client-call-2026-10-23.md"] > order["project-ideas-2024.md"]
    assert order["!renewal-domains.md"] > order["wifi-and-codes.md"]


@check("a near due date adds points and says so")
def _():
    now = time.mktime((2026, 10, 24, 12, 0, 0, 0, 0, -1))
    text = "DUE 2026-10-25: pay before the late fee."
    score, reasons = score_note(text, 1.0, len(text), now)
    assert any("within a week" in r for r in reasons), reasons
    far = "DUE 2027-06-01: nothing soon."
    s2, r2 = score_note(far, 1.0, len(far), now)
    assert not any("within a week" in r for r in r2), r2
    assert score > s2


@check("quote gate accepts a real sentence")
def _():
    note = "The backups ran on the 19th and the restore test has still not been done."
    assert quote_gate("The backups ran on the 19th and the restore test has still not been done.", note)
    assert quote_gate("the backups ran on the 19th  and the restore test has still not been done", note)


@check("quote gate rejects a paraphrase")
def _():
    note = "The backups ran on the 19th and the restore test has still not been done."
    assert not quote_gate("The backups ran last week and nobody tested a restore.", note)
    assert not quote_gate("ok", note)


@check("card parser survives prose around the JSON")
def _():
    raw = 'Sure! Here it is:\n{"what":"a","next":"b","quote":"c"}\nHope that helps.'
    got = parse_card(raw)
    assert got and got["what"] == "a" and got["next"] == "b"


@check("card parser returns None on junk")
def _():
    assert parse_card("I cannot help with that.") is None
    assert parse_card("") is None
    assert parse_card('{"what": "broken"') is None


@check("a card whose quote is not in the note is marked unverified")
def _():
    note = {"path": "x.md", "score": 1.0, "reasons": [], "text": "Buy oat milk on the way home."}
    engine = FakeEngine('{"what":"shopping","next":"buy milk","quote":"the shop closes at nine"}')
    card = make_card(note, engine)
    assert card["status"] == "unverified", card
    assert card["quote"] == ""
    assert "not trusted" in card["note"]


@check("a card with a real quote is trusted")
def _():
    note = {"path": "x.md", "score": 1.0, "reasons": [], "text": "Buy oat milk on the way home."}
    engine = FakeEngine('{"what":"shopping","next":"buy milk","quote":"Buy oat milk on the way home."}')
    card = make_card(note, engine)
    assert card["status"] == "ok", card
    assert card["quote"]


@check("the model answering with garbage does not invent a card")
def _():
    note = {"path": "x.md", "score": 1.0, "reasons": [], "text": "Buy oat milk on the way home."}
    card = make_card(note, FakeEngine("no json here"))
    assert card["status"] == "failed"
    assert card["what"] == "" and card["next"] == ""


@check("budget caps the number of model calls")
def _():
    engine = FakeEngine('{"what":"w","next":"n","quote":"x"}')
    result = brief(SAMPLE, budget=3, minutes=10, engine=engine, use_cache=False)
    assert engine.calls == 3, "expected 3 calls, got %d" % engine.calls
    assert len(result["cards"]) == 3
    assert result["deferred"], "the rest must be listed, not hidden"


@check("deferred entries say why they were left out")
def _():
    engine = FakeEngine('{"what":"w","next":"n","quote":"x"}')
    result = brief(SAMPLE, budget=2, minutes=10, engine=engine, use_cache=False)
    assert all(d["why"] for d in result["deferred"])
    assert any("budget" in d["why"] for d in result["deferred"])


@check("the clock budget stops the run instead of rushing it")
def _():
    engine = FakeEngine('{"what":"w","next":"n","quote":"x"}', delay=0.6)
    result = brief(SAMPLE, budget=10, minutes=0.02, engine=engine, use_cache=False)
    assert len(result["cards"]) < 10
    assert any("time budget" in d["why"] for d in result["deferred"])


def clean_cache():
    path = os.path.join(SAMPLE, ".fivecache.json")
    if os.path.exists(path):
        os.remove(path)


@check("a second run reads the cache and does not call the model")
def _():
    clean_cache()
    engine = FakeEngine('{"what":"w","next":"n","quote":"x"}')
    brief(SAMPLE, budget=2, minutes=10, engine=engine, use_cache=True)
    first = engine.calls
    engine2 = FakeEngine('{"what":"w","next":"n","quote":"x"}')
    brief(SAMPLE, budget=2, minutes=10, engine=engine2, use_cache=True)
    assert first == 2 and engine2.calls == 0, (first, engine2.calls)
    clean_cache()


@check("the prompt tells the model not to guess")
def _():
    prompt, truncated = build_prompt("short note")
    assert "Do not guess" in prompt
    assert truncated is False
    long_prompt, truncated_long = build_prompt("x" * 5000)
    assert truncated_long is True
    assert len(long_prompt) < 4000


@check("the report counts what a human has to read")
def _():
    engine = FakeEngine('{"what":"w"*20,"next":"n"*20,"quote":"x"}')
    result = brief(SAMPLE, budget=3, minutes=10, engine=engine, use_cache=False)
    rep = result["report"]
    assert rep["chars_in"] > 0
    assert rep["chars_out"] >= 0
    assert rep["calls"] == 3
    assert rep["notes_found"] >= rep["cards"]


@check("the model echoing the schema is not a card")
def _():
    note = {"path": "x.md", "score": 1.0, "reasons": [], "text": "Buy oat milk on the way home."}
    echo = FakeEngine('{"what":"what this note is about","next":"the single next action","quote":"Buy oat milk on the way home."}')
    card = make_card(note, echo)
    assert card["status"] == "unverified", card
    assert "echoed the instructions" in card["note"]


@check("the report counts the whole folder, not only the chosen notes")
def _():
    engine = FakeEngine('{"what":"w","next":"n","quote":"x"}')
    result = brief(SAMPLE, budget=1, minutes=10, engine=engine, use_cache=False)
    cands, _ = scan(SAMPLE)
    assert result["report"]["chars_in"] == sum(len(c["text"]) for c in cands)
    assert result["report"]["chars_in"] > 3000


@check("cached cards are counted separately from model calls")
def _():
    engine = FakeEngine('{"what":"w","next":"n","quote":"x"}')
    brief(SAMPLE, budget=2, minutes=10, engine=engine, use_cache=True)
    engine2 = FakeEngine('{"what":"w","next":"n","quote":"x"}')
    again = brief(SAMPLE, budget=2, minutes=10, engine=engine2, use_cache=True)
    assert again["report"]["calls"] == 0
    assert again["report"]["cached"] == 2
    cache_path = os.path.join(SAMPLE, ".fivecache.json")
    if os.path.exists(cache_path):
        os.remove(cache_path)


def main():
    print("%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for name, why in FAILED:
        print("  FAIL %s: %s" % (name, why))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
