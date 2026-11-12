from app.rag.chunking import chunk_markdown, chunk_runbook_dir


def test_chunk_markdown_splits_on_headings():
    text = """# CrashLoopBackOff

Intro paragraph about crash loops.

## Symptoms

The pod restart count climbs.

## Fixes

Correct the container command.
"""
    chunks = chunk_markdown(text, source="crashloopbackoff.md")
    assert [c.section for c in chunks] == ["CrashLoopBackOff", "Symptoms", "Fixes"]
    assert all(c.title == "CrashLoopBackOff" for c in chunks)
    assert all(c.source == "crashloopbackoff.md" for c in chunks)
    symptoms = next(c for c in chunks if c.section == "Symptoms")
    assert "restart count climbs" in symptoms.text
    assert symptoms.text.startswith("## Symptoms")


def test_chunk_markdown_without_headings_is_one_chunk():
    chunks = chunk_markdown("just a paragraph of notes\n\nmore notes", source="notes.md")
    assert len(chunks) == 1
    assert chunks[0].title == "Notes"
    assert chunks[0].section == "Notes"
    assert "just a paragraph" in chunks[0].text


def test_chunk_markdown_skips_empty_sections_and_blank_docs():
    text = """# Title

Intro under the title.

## Empty

## Real

Something useful.
"""
    chunks = chunk_markdown(text, source="t.md")
    assert [c.section for c in chunks] == ["Title", "Real"]
    assert "Intro under the title" in chunks[0].text
    assert chunk_markdown("   \n\n", source="empty.md") == []


def test_chunk_runbook_dir(tmp_path):
    (tmp_path / "one.md").write_text("# One\n\n## Causes\nA cause.\n", encoding="utf-8")
    (tmp_path / "two.md").write_text("# Two\n\nBody only.\n", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("not markdown", encoding="utf-8")
    chunks = chunk_runbook_dir(tmp_path)
    sources = {c.source for c in chunks}
    assert sources == {"one.md", "two.md"}
    assert chunk_runbook_dir(tmp_path / "missing") == []
