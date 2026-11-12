from app.config import Settings
from app.ingest import ingest_runbooks
from app.rag.embeddings import HashEmbedder
from app.rag.store import KnowledgeStore


def test_ingest_runbooks_upserts_chunks(tmp_path):
    (tmp_path / "crashloopbackoff.md").write_text(
        "# CrashLoopBackOff\n\n## Causes\nThe process exits with code 1.\n\n## Fixes\nFix the command.\n",
        encoding="utf-8",
    )
    (tmp_path / "oomkilled.md").write_text(
        "# OOMKilled\n\n## Causes\nThe memory limit is too low.\n",
        encoding="utf-8",
    )
    settings = Settings(qdrant_url=":memory:", embedding_backend="fake")
    store = KnowledgeStore(settings=settings, embedder=HashEmbedder())
    result = ingest_runbooks(tmp_path, store=store, settings=settings)
    assert result["files"] == 2
    assert result["chunks"] >= 3

    hits = store.search(store.runbooks_collection, "CrashLoopBackOff exit code 1", top_k=3)
    assert hits
    assert any("crashloopbackoff.md" in hit.source for hit in hits)
    assert all(hit.title for hit in hits)
    assert all(hit.section for hit in hits)


def test_reingest_is_idempotent(tmp_path):
    (tmp_path / "only.md").write_text("# Only\n\nIntro.\n\n## Section\nStable body.\n", encoding="utf-8")
    settings = Settings(qdrant_url=":memory:", embedding_backend="fake")
    store = KnowledgeStore(settings=settings, embedder=HashEmbedder())
    first = ingest_runbooks(tmp_path, store=store, settings=settings)
    second = ingest_runbooks(tmp_path, store=store, settings=settings)
    assert first["chunks"] == second["chunks"] == 2  # title + section? title heading + Section
    hits = store.search(store.runbooks_collection, "Stable body", top_k=5)
    # Re-upsert must not duplicate the same source/section.
    sections = [(h.source, h.section) for h in hits if h.source == "only.md"]
    assert len(sections) == len(set(sections))
