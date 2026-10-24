# five-things

Five cards from a folder of notes, written by a model that runs on your own
machine. One command, one screen, and the rest of the pile is listed as
deliberately skipped instead of quietly summarised.

This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19).

## The problem it removes

A notes folder grows faster than anyone reads it. On a Saturday morning you
open it, see forty files, and close it again. The work you actually wanted was
to find the five that matter this week.

`five-things` makes that decision in two steps:

1. A deterministic scan scores every note on cheap signals: how recently it was
   touched, whether it still has `TODO` / `WAITING` / `DECIDE` markers, how many
   sections it has, whether it names a date in the next seven days.
2. Only the top N go to a local open-weight model, which writes one card each:
   what the note is, the single next action, and a sentence copied word for
   word out of the note as proof.

Everything below the line is printed as **not today**, with the reason it lost.
Nothing is silently dropped.

## Why it is built this way

The model is the expensive part. Not in money, in seconds: a 2B model on a
laptop CPU is slow enough that you feel every call. So the tool spends the model
only where a rule cannot help, and it caps that spend twice:

- `--budget 5` notes may call the model at all
- `--minutes 2` is a wall clock cap for the whole run; when it is gone the rest
  are deferred, not rushed

Both caps are tested. `tests/run_tests.py` has a test that breaks the clock on
purpose and checks that the run stops early instead of pretending it finished.

## The rule that matters

A 2B model will write a confident sentence whether or not the note supports it.
So every card must return a `quote`: an exact sentence from the note. The tool
checks that quote against the source text, ignoring whitespace and case. If the
quote is not there, the card is marked `unverified` and the note stays in your
queue, because a wrong summary you trust is worse than no summary.

That check runs on every card, and its failures are counted in the run report.

## Install

No dependencies beyond Python 3.9+. The model is served by
[llama.cpp](https://github.com/ggml-org/llama.cpp); the tool talks to its
OpenAI-compatible endpoint.

```bash
git clone https://github.com/dekita-dev/five-things.git
cd five-things
export FIVE_THINGS_LLAMA_BIN=/path/to/llama-server      # Windows: set FIVE_THINGS_LLAMA_BIN=...
export FIVE_THINGS_MODEL=/path/to/gemma-2-2b-it-Q4_K_M.gguf
python -m fivethings start          # boots llama-server on port 8791
python -m fivethings doctor         # endpoint, binary, model, reachable
python -m fivethings brief ~/notes  # today's five
```

Already running a server somewhere? Skip `start` and pass `--url http://127.0.0.1:8080`.

## What a run looks like

Real output, 14 note sample folder, Gemma 2 2B Q4_K_M on a laptop CPU:

```
1. !renewal-domains.md
   what : Domain renewals
   next : decide whether the .dev one is worth another year.
   proof: The two domains expire on 2026-10-30.
   signals: pinned by filename, touched in the last 3 days, open markers: DECIDE, TODO

3. client-call-2026-10-23.md  [unverified]
   what : -
   next : -
   why  : the model echoed the instructions instead of reading the note
   signals: touched today, open markers: DECIDE, NEXT

not today (8)
  - sourdough-timer.md      <ranked #6, below the budget of 5>
  - project-ideas-2024.md   <ranked #11, below the budget of 5>

filtered out (1)
  - already-done.md  <marked done>

run: 13 notes, 5 cards, 5 model calls (0 from cache), 1100+299 tokens, 394.9s, 0.8 tok/s
read: 3250 chars in the folder, 292 chars in your cards (9.0% of the folder)
card status: {"ok": 4, "unverified": 1}
```

Second run on the same folder, with the cache warm: `0 model calls, 0.0s`.

## Tests

```bash
python tests/run_tests.py
```

20 assertions, no pytest, no network. They cover the scan rules, the quote gate
in both directions, a model that answers with garbage, a model that echoes the
schema back, both budgets, the cache, and the prompt text itself.

## Sample folder

`sample/notes/` is invented for the demo, including two awkward cases: a note
that tries to inject instructions ("ignore previous instructions") and a note
already marked done. Run `python sample/make_sample.py` to rebuild it with
spread out modification dates.

## Limits, stated plainly

- Text files only: `.md`, `.markdown`, `.txt`. No PDFs, no images, no OCR.
- The ranking is rules, not taste. It will miss the important note that has no
  marker and no recent edit. The deferred list exists so you can spot that.
- A 2B model writes flat prose. The cards are for triage, not for reading
  instead of the note.
- English only in the sample; the prompt is not tuned for other languages.

## License

MIT. The model weights are not included; Gemma 2 is distributed under the
[Gemma Terms of Use](https://ai.google.dev/gemma/terms).
