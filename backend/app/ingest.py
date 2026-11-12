"""Ingest markdown runbooks into Qdrant.

Usage (from the backend directory, with the venv active):

    python -m app.ingest
    python -m app.ingest --dir ../knowledge/runbooks
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.rag.chunking import chunk_runbook_dir
from app.rag.embeddings import build_embedder
from app.rag.store import KIND_RUNBOOK, KnowledgeStore, KnowledgeUnavailableError

logger = logging.getLogger(__name__)


def default_knowledge_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    if settings.knowledge_dir:
        return Path(settings.knowledge_dir).expanduser().resolve()
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "knowledge" / "runbooks",  # repo root from backend/app
        Path("/knowledge/runbooks"),
        Path.cwd() / "knowledge" / "runbooks",
        Path.cwd().parent / "knowledge" / "runbooks",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[0]


def ingest_runbooks(
    directory: Path,
    store: KnowledgeStore | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    settings = settings or get_settings()
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Runbook directory does not exist: {directory}")

    chunks = chunk_runbook_dir(directory)
    files = list(directory.glob("*.md"))
    if not chunks:
        logger.warning("No markdown chunks found in %s", directory)
        return {"files": len(files), "chunks": 0}

    if store is None:
        embedder = build_embedder(settings)
        store = KnowledgeStore(settings=settings, embedder=embedder)
    ids = store.upsert_chunks(store.runbooks_collection, chunks, kind=KIND_RUNBOOK)
    logger.info(
        "Ingested %s chunks from %s files in %s into collection %s",
        len(ids),
        len(files),
        directory,
        store.runbooks_collection,
    )
    return {"files": len(files), "chunks": len(ids)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Kube Copilot runbooks into Qdrant.")
    parser.add_argument(
        "--dir",
        dest="directory",
        default=None,
        help="Directory of markdown runbooks (default: knowledge/runbooks).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    directory = Path(args.directory) if args.directory else default_knowledge_dir(settings)
    try:
        result = ingest_runbooks(directory, settings=settings)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KnowledgeUnavailableError as exc:
        print(
            f"error: {exc}\n"
            "Start Qdrant with `docker compose up -d qdrant`, or set QDRANT_URL=:memory: "
            "or QDRANT_PATH=.qdrant for a local store.",
            file=sys.stderr,
        )
        return 1
    print(f"Ingested {result['chunks']} chunks from {result['files']} runbooks in {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
