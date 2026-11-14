"""Shrink container logs to a character budget without losing the evidence.

Groq's free tier allows 8K tokens per minute, and one diagnose with two
200-line logs of nginx probe hits went over it (HTTP 413). Instead of a blind
tail, this keeps:

- the first few lines (startup banner, config echo),
- every distinct error-like line (first and last occurrence of each pattern),
  with a few lines of context after a traceback/panic,
- as much of the tail as still fits,

collapses repeated lines (same text once timestamps, numbers and IPs are
masked) into one line with a count, and marks every gap.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_MASKS = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\[\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\]"), "<ts>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{8,}\b"), "<hex>"),
    (re.compile(r"\d+(?:\.\d+)?"), "#"),
]
IMPORTANT = re.compile(
    r"error|\berr\b|fatal|panic|exception|traceback|fail|refused|denied|unauthori[sz]ed|forbidden|killed|"
    r"\boom\b|out of memory|timed? ?out|cannot|can't|unable|invalid|no such|not found|segfault|sigsegv|"
    r"\bexit(ed)?\b|exit (code|status)|\bwarn|emerg|crit",
    re.IGNORECASE,
)
_CONTEXT_AFTER = re.compile(r"traceback|panic|goroutine|exception", re.IGNORECASE)
HEAD_LINES = 4


def pattern(line: str) -> str:
    out = line.strip()
    for rx, repl in _MASKS:
        out = rx.sub(repl, out)
    return out


@dataclass
class _Entry:
    index: int  # first original line index
    line: str
    pattern: str
    run: int = 1  # consecutive repeats collapsed into this entry
    last: str = ""  # text of the last repeat


def _render(entry: _Entry, total: Counter) -> str:
    text = entry.line
    if entry.run > 1:
        last = entry.last if len(entry.last) <= 240 else entry.last[:239] + "…"
        text += f"\n  [x{entry.run} consecutive similar lines; last one:]\n{last}"
    elif total[entry.pattern] > 1:
        text += f"  [pattern seen {total[entry.pattern]}x in this log]"
    return text


def compact_logs(text: str | None, max_chars: int = 3000) -> str:
    """Return `text` unchanged when it fits, otherwise a compacted version within ~max_chars."""
    if not text or len(text) <= max_chars:
        return text or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    total = Counter(pattern(ln) for ln in lines)

    entries: list[_Entry] = []
    for i, ln in enumerate(lines):
        p = pattern(ln)
        if entries and entries[-1].pattern == p:
            entries[-1].run += 1
            entries[-1].last = ln
            continue
        entries.append(_Entry(i, ln, p))

    rendered = [_render(e, total) for e in entries]
    if sum(len(r) + 1 for r in rendered) <= max_chars:
        return "(log compacted: repeated lines collapsed)\n" + "\n".join(rendered)

    keep: set[int] = set(range(min(HEAD_LINES, len(entries))))
    # Distinct error-like patterns: first and last occurrence (+ context after tracebacks/panics).
    first_seen: dict[str, int] = {}
    last_seen: dict[str, int] = {}
    for idx, e in enumerate(entries):
        if IMPORTANT.search(e.line):
            first_seen.setdefault(e.pattern, idx)
            last_seen[e.pattern] = idx
    for idx in sorted(set(first_seen.values()) | set(last_seen.values())):
        keep.add(idx)
        if _CONTEXT_AFTER.search(entries[idx].line):
            keep.update(range(idx + 1, min(idx + 6, len(entries))))

    def size(indices: set[int]) -> int:
        return sum(len(rendered[i]) + 1 for i in indices) + 40 * len(indices) // 4

    # Too many distinct errors: keep the ones nearest the end.
    if size(keep) > max_chars:
        trimmed: set[int] = set()
        for idx in sorted(keep, reverse=True):
            if size(trimmed | {idx}) > max_chars:
                break
            trimmed.add(idx)
        keep = trimmed
    # Fill the rest of the budget with the tail.
    for idx in range(len(entries) - 1, -1, -1):
        if idx in keep:
            continue
        if size(keep | {idx}) > max_chars:
            break
        keep.add(idx)

    out = [f"(log compacted to fit the prompt budget: {len(lines)} lines -> {len(keep)} shown; "
           "repeats collapsed, error lines and the tail kept)"]
    prev = -1
    for idx in sorted(keep):
        gap = entries[idx].index - (entries[prev].index + entries[prev].run if prev >= 0 else 0)
        if gap > 0:
            out.append(f"... {gap} line(s) omitted ...")
        out.append(rendered[idx])
        prev = idx
    tail_gap = len(lines) - (entries[prev].index + entries[prev].run) if prev >= 0 else 0
    if tail_gap > 0:
        out.append(f"... {tail_gap} line(s) omitted ...")
    return "\n".join(out)
