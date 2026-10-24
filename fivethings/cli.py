"""Command line entry point."""
import argparse
import io
import json
import sys

from .brief import brief
from .engine import EngineError, LocalEngine, find_binary, find_model, spawn, wait_ready


def _safe_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def render(result):
    out = io.StringIO()
    cards = result["cards"]
    report = result["report"]
    for i, card in enumerate(cards, 1):
        tag = "" if card["status"] == "ok" else "  [%s]" % card["status"]
        out.write("%d. %s%s\n" % (i, card["path"], tag))
        if card["status"] in ("ok", "unverified"):
            out.write("   what : %s\n" % (card.get("what") or "-"))
            out.write("   next : %s\n" % (card.get("next") or "-"))
            if card.get("quote"):
                out.write("   proof: %s\n" % card["quote"])
            if card.get("note"):
                out.write("   why  : %s\n" % card["note"])
        else:
            out.write("   unread: %s\n" % card.get("note", "model error"))
        out.write("   signals: %s\n" % ", ".join(card["reasons"][:3]))
        out.write("\n")

    if result["deferred"]:
        out.write("not today (%d)\n" % len(result["deferred"]))
        for d in result["deferred"]:
            out.write("  - %s  <%s>\n" % (d["path"], d["why"]))
        out.write("\n")

    if result["skipped"]:
        out.write("filtered out (%d)\n" % len(result["skipped"]))
        for s in result["skipped"]:
            out.write("  - %s  <%s>\n" % (s["path"], s["why"]))
        out.write("\n")

    out.write("run: %d notes, %d cards, %d model calls (%d from cache), %d+%d tokens, %.1fs, %.1f tok/s\n" % (
        report["notes_found"], report["cards"], report["calls"], report["cached"],
        report["prompt_tokens"], report["completion_tokens"],
        report["wall_seconds"], report["tok_per_second"]))
    out.write("read: %d chars in the folder, %d chars in your cards (%s)\n" % (
        report["chars_in"], report["chars_out"],
        "%.1f%% of the folder" % (100.0 * report["chars_out"] / report["chars_in"])
        if report["chars_in"] else "n/a"))
    if report["status"]:
        out.write("card status: %s\n" % json.dumps(report["status"]))
    return out.getvalue()


def cmd_brief(args):
    engine = LocalEngine(args.url)
    if not engine.healthy():
        raise SystemExit("no model at %s. Start one first (see README) or pass --url." % args.url)
    result = brief(args.path, budget=args.budget, minutes=args.minutes,
                   engine=engine, use_cache=not args.no_cache)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


def cmd_doctor(args):
    engine = LocalEngine(args.url)
    print("endpoint : %s" % engine.url)
    print("reachable: %s" % ("yes" if engine.healthy() else "no"))
    print("binary   : %s" % (find_binary() or "not found"))
    print("model    : %s" % (find_model() or "not set (FIVE_THINGS_MODEL)"))
    return 0 if engine.healthy() else 1


def cmd_start(args):
    try:
        proc = spawn(model=args.model, port=args.port)
    except EngineError as exc:
        raise SystemExit(str(exc))
    print("llama-server started, pid %d, waiting for /health ..." % proc.pid)
    ok = wait_ready("http://127.0.0.1:%d" % args.port, seconds=args.wait)
    print("ready: %s (stop it with: taskkill /PID %d /F)" % ("yes" if ok else "no", proc.pid))
    return 0 if ok else 1


def main(argv=None):
    _safe_stdout()
    ap = argparse.ArgumentParser(prog="five-things",
                                 description="Five cards from a folder of notes, made by a local model.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("brief", help="build today's five cards")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--budget", type=int, default=5, help="how many notes may use the model")
    p.add_argument("--minutes", type=float, default=2.0, help="wall clock cap for the whole run")
    p.add_argument("--url", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_brief)

    d = sub.add_parser("doctor", help="is the local model there?")
    d.add_argument("--url", default=None)
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("start", help="launch llama-server for this model")
    s.add_argument("--model", default=None)
    s.add_argument("--port", type=int, default=8791)
    s.add_argument("--wait", type=int, default=90)
    s.set_defaults(func=cmd_start)

    args = ap.parse_args(argv)
    return args.func(args)
