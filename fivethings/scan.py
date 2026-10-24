"""Deterministic first pass: rank notes by cheap signals, no model involved.

The model is the expensive part of this tool, so the decision of *who gets the
model* is made here, with rules you can read and test.
"""
import os
import re
import time

TEXT_EXT = (".md", ".markdown", ".txt")
DONE_MARK = "<!-- five:done -->"
MAX_BYTES = 400_000

MARKER_RE = re.compile(r"\b(TODO|FIXME|NEXT|DUE|DECIDE|BLOCKED|WAITING)\b", re.I)
DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
H2_RE = re.compile(r"^##\s+", re.M)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def note_age_days(mtime, now=None):
    now = time.time() if now is None else now
    return max(0.0, (now - mtime) / 86400.0)


def score_note(text, age_days, size_bytes, now=None):
    """Return (score, reasons). Every point added is explainable."""
    now = now or time.time()
    reasons = []
    score = 0.0

    if age_days <= 1:
        score += 12
        reasons.append("touched today")
    elif age_days <= 3:
        score += 9
        reasons.append("touched in the last 3 days")
    elif age_days <= 14:
        score += 5
        reasons.append("touched in the last 2 weeks")
    else:
        score += max(0.0, 5.0 - (age_days - 14) / 10.0)
        reasons.append("stale (%d days)" % int(age_days))

    markers = MARKER_RE.findall(text)
    if markers:
        pts = min(18.0, 6.0 * len(markers))
        score += pts
        uniq = sorted({m.upper() for m in markers})
        reasons.append("open markers: %s" % ", ".join(uniq))

    if H2_RE.search(text):
        sections = len(H2_RE.findall(text))
        score += min(6.0, 1.5 * sections)
        reasons.append("%d sections" % sections)

    for m in DATE_RE.finditer(text):
        try:
            due = time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               23, 59, 0, 0, 0, -1))
        except (ValueError, OverflowError):
            continue
        delta = (due - now) / 86400.0
        if -1 <= delta <= 7:
            score += 10
            reasons.append("dated %s (within a week)" % m.group(0))
            break

    body = text.strip()
    if len(body) < 120:
        score -= 6
        reasons.append("almost empty")
    if size_bytes > 120_000:
        score -= 4
        reasons.append("very large")

    return round(score, 2), reasons


def scan(root, now=None):
    """Return (candidates, skipped). Candidates are dicts sorted by score desc."""
    now = now or time.time()
    cands, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not name.lower().endswith(TEXT_EXT):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            try:
                st = os.stat(path)
                if st.st_size > MAX_BYTES:
                    skipped.append({"path": rel, "why": "larger than %d KB" % (MAX_BYTES // 1000)})
                    continue
                text = _read(path)
            except OSError as exc:
                skipped.append({"path": rel, "why": "unreadable: %s" % exc})
                continue

            if DONE_MARK in text:
                skipped.append({"path": rel, "why": "marked done"})
                continue
            if not text.strip():
                skipped.append({"path": rel, "why": "empty file"})
                continue

            bonus_from_name = 3.0 if name.lower().startswith("!") else 0.0
            score, reasons = score_note(text, note_age_days(st.st_mtime, now), st.st_size, now)
            score += bonus_from_name
            if bonus_from_name:
                reasons.insert(0, "pinned by filename")

            cands.append({
                "path": rel,
                "score": score,
                "reasons": reasons,
                "text": text,
                "size": st.st_size,
                "age_days": round(note_age_days(st.st_mtime, now), 1),
            })

    cands.sort(key=lambda c: (-c["score"], c["path"]))
    return cands, skipped
