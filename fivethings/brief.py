"""Turn the shortlist into cards, spending at most N model calls and M minutes.

Two budgets, both hard:
  * a count of notes  -> how many get the model at all
  * a wall clock      -> when time is up, the rest are deferred, not rushed

A card is only trusted if the model can quote the note word for word. If it
cannot, the card is marked `unverified` and the note goes back to the human
queue. That rule exists because a 2B model writes confident prose whether or
not the note says what it claims.
"""
import hashlib
import json
import os
import re
import time

from .engine import EngineError
from .scan import scan

MAX_NOTE_CHARS = 2000
DEFAULT_BUDGET = 5
DEFAULT_MINUTES = 2.0
FIRST_CALL_GUESS = 8.0

PROMPT = """You sort a person's notes. Read one note and return JSON only.

{"what": "under 14 words: what this note is about",
 "next": "under 14 words: the single next action a human should take",
 "quote": "one sentence copied word for word from the note that supports your answer"}

Rules:
- Use only text that appears in the note. Do not guess.
- If the note asks for no action, write "nothing, read it and file it" as next.
- quote must be an exact copy of a sentence in the note.

NOTE:
<<<
__NOTE__
>>>
"""


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


# A 2B model sometimes answers with the field description instead of the field.
# These strings are the ones it has been caught copying back.
ECHO_PHRASES = (
    "what this note is about",
    "under 14 words",
    "the single next action",
    "one sentence copied word for word",
)


def looks_like_echo(value):
    v = _norm(value)
    if len(v) < 3:
        return True
    return any(p in v for p in ECHO_PHRASES)


def quote_gate(quote, text):
    """True only when the quote really occurs in the note."""
    q = _norm(quote)
    if len(q) < 12:
        return False
    return q in _norm(text)


def parse_card(raw):
    """Pull the first JSON object out of whatever the model wrote."""
    if not raw:
        return None
    start = raw.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = raw.find("{", start + 1)
    return None


def build_prompt(text):
    note = text.strip()
    truncated = len(note) > MAX_NOTE_CHARS
    if truncated:
        note = note[:MAX_NOTE_CHARS]
    return PROMPT.replace("__NOTE__", note), truncated


class Cache:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.data = {}

    @staticmethod
    def key(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    def get(self, text):
        return self.data.get(self.key(text))

    def put(self, text, card):
        self.data[self.key(text)] = card
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
        except OSError:
            pass


def make_card(note, engine):
    prompt, truncated = build_prompt(note["text"])
    card = {
        "path": note["path"],
        "score": note["score"],
        "reasons": note["reasons"],
        "truncated": truncated,
        "status": "ok",
    }
    try:
        raw = engine.chat(prompt, max_tokens=220, temperature=0.0)
    except EngineError as exc:
        card.update(status="failed", what="", next="", note=str(exc))
        return card

    parsed = parse_card(raw)
    if not parsed:
        card.update(status="failed", what="", next="", note="the model did not return JSON")
        return card

    card["what"] = str(parsed.get("what", "")).strip()[:160]
    card["next"] = str(parsed.get("next", "")).strip()[:160]
    if looks_like_echo(card["what"]) or looks_like_echo(card["next"]):
        card["status"] = "unverified"
        card["what"] = ""
        card["next"] = ""
        card["quote"] = ""
        card["note"] = "the model echoed the instructions instead of reading the note"
        return card

    quote = str(parsed.get("quote", "")).strip()
    if quote_gate(quote, note["text"]):
        card["quote"] = quote[:240]
    else:
        card["status"] = "unverified"
        card["quote"] = ""
        card["note"] = "quote not found in the note, so the card is not trusted"

    if not card["what"] or not card["next"]:
        card["status"] = "unverified"
        card["note"] = "card came back empty"
    return card


def brief(root, budget=DEFAULT_BUDGET, minutes=DEFAULT_MINUTES, engine=None,
          use_cache=True, now=None):
    started = time.time()
    cands, skipped = scan(root, now=now)
    # chars_in is the whole readable folder, not just the chosen notes: the
    # point of the number is how much text a human is spared, not how much the
    # model was given.
    notes_chars = sum(len(c["text"]) for c in cands)
    cache = Cache(os.path.join(root, ".fivecache.json")) if use_cache else Cache("")
    cards, deferred, chosen = [], [], cands[:budget]
    cached_hits = 0

    last_call = FIRST_CALL_GUESS
    for idx, note in enumerate(chosen):
        elapsed = time.time() - started
        if engine is not None and elapsed + last_call > minutes * 60 and cards:
            for rest in chosen[idx:]:
                deferred.append({"path": rest["path"], "score": rest["score"],
                                 "why": "time budget spent (%.0f s)" % elapsed})
            break

        cached = cache.get(note["text"])
        if cached:
            cards.append(cached)
            cached_hits += 1
            continue

        t0 = time.time()
        card = make_card(note, engine)
        card["seconds"] = round(time.time() - t0, 1)
        last_call = max(1.0, time.time() - t0)
        cache.put(note["text"], card)
        cards.append(card)

    for rest in cands[budget:]:
        deferred.append({"path": rest["path"], "score": rest["score"],
                         "why": "ranked #{n}, below the budget of {b}".format(
                             n=cands.index(rest) + 1, b=budget)})

    report = {
        "notes_found": len(cands),
        "cards": len(cards),
        "deferred": len(deferred),
        "skipped": len(skipped),
        "chars_in": notes_chars,
        "chars_out": sum(len(c.get("what", "")) + len(c.get("next", "")) for c in cards),
        "wall_seconds": round(time.time() - started, 1),
        "status": {},
        "calls": getattr(engine, "calls", 0),
        "cached": cached_hits,
        "prompt_tokens": getattr(engine, "prompt_tokens", 0),
        "completion_tokens": getattr(engine, "completion_tokens", 0),
        "tok_per_second": getattr(engine, "tok_per_second", lambda: 0.0)(),
        "budget": budget,
        "minutes": minutes,
    }
    for card in cards:
        report["status"][card["status"]] = report["status"].get(card["status"], 0) + 1

    return {"cards": cards, "deferred": deferred, "skipped": skipped, "report": report}
