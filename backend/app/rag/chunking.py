"""Split markdown runbooks into heading-based chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class MarkdownChunk:
    title: str
    source: str
    section: str
    text: str


def _default_title(source: str) -> str:
    stem = Path(source).stem.replace("-", " ").replace("_", " ").strip()
    return stem.title() if stem else "Untitled"


def chunk_markdown(text: str, source: str = "unknown") -> list[MarkdownChunk]:
    """Chunk markdown by ATX headings, keeping each heading with its body.

    The document title is the first H1 if one exists, otherwise a title
    derived from the source filename. Files with no headings become a
    single chunk. Empty sections are skipped.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    title = _default_title(source)
    for raw in lines:
        match = HEADING_RE.match(raw.strip())
        if match and len(match.group(1)) == 1:
            heading = match.group(2).strip()
            if heading:
                title = heading
            break

    sections: list[tuple[str, list[str]]] = []
    current_heading = title
    current_lines: list[str] = []

    def _has_body(lines: list[str]) -> bool:
        # A section is empty if it is only a heading (plus blank lines).
        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                continue
            if HEADING_RE.match(raw) or HEADING_RE.match(stripped):
                continue
            return True
        return False

    def _flush() -> None:
        if current_lines and _has_body(current_lines):
            sections.append((current_heading, list(current_lines)))

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            _flush()
            heading = match.group(2).strip()
            current_heading = heading or current_heading
            current_lines = [line]
        else:
            current_lines.append(line)
    _flush()

    if not sections:
        body = normalized.strip()
        if not body:
            return []
        return [MarkdownChunk(title=title, source=source, section=title, text=body)]

    chunks: list[MarkdownChunk] = []
    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        chunks.append(MarkdownChunk(title=title, source=source, section=heading, text=body))
    return chunks


def chunk_runbook_dir(directory: Path) -> list[MarkdownChunk]:
    """Chunk every ``*.md`` file in ``directory`` (non-recursive)."""
    chunks: list[MarkdownChunk] = []
    if not directory.is_dir():
        return chunks
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        chunks.extend(chunk_markdown(text, source=path.name))
    return chunks
