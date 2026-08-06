"""``python -m app.rag.reindex`` — idempotent (re)build of ``faq_embeddings``.

Algorithm (``Documentation/audits/2026-08-06-pgvector-migration-contract.md`` decision
1(d)): for each ``seed_data/faq.json`` entry, ``content_hash = sha256(document)``. A row
already present for ``(faq_id, embedding_model)`` whose ``content_hash`` **and**
``dimensions`` both already match is left alone — no embedding call for it at all.
Everything else is (re-)embedded, in one batch, and upserted in place (looked up first,
then updated-or-inserted through the ORM identity map — never delete-then-insert, which
would leave the table momentarily empty for a concurrent reader). Once the loop is done,
rows for this ``embedding_model`` whose ``faq_id`` no longer appears in ``faq.json`` are
deleted.

``dimensions`` is never hardcoded per model: for a row that needs (re-)embedding, it is
measured from that call's actual output width. For a row that is a *skip candidate*
(content_hash matches), it is checked against the width already recorded on this
``embedding_model``'s other existing rows — so a plain, fully-up-to-date reindex issues
**zero** embedding calls, while a genuine dimension drift (the same ``embedding_model``
name now producing a different-width vector) still gets caught and re-embedded rather
than silently indexed with the wrong ``dimensions`` bookkeeping.

A zero-norm embedding is rejected outright, naming the offending ``faq_id`` — never
stored. Under pgvector, ``<=>`` against a zero vector is ``NaN``, which would poison
``ORDER BY`` for every future query rather than just scoring that one row badly (the
pre-migration NumPy index's weaker, and now retired, tolerance).

Also invoked (same routine, same idempotency) from ``app/main.py``'s lifespan when
``RAG_INDEX_ON_STARTUP`` is set (the default), immediately after ``seed_if_empty``, so a
fresh ``docker compose up`` yields a working FAQ path with no manual step.
"""

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass

from sqlalchemy import Engine, delete
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.db.session import build_engine
from app.models.faq_embedding import FaqEmbedding
from app.models.types import utcnow
from app.rag.embeddings import build_embedding_model
from app.rag.index import FaqEntry, load_faq_entries

logger = logging.getLogger(__name__)


class ZeroNormEmbeddingError(RuntimeError):
    """A ``faq.json`` entry embedded to a zero (or ~zero) vector.

    Never stored: see ``app/rag/reindex.py``'s module docstring for why. Failing loudly,
    naming the offending ``faq_id``, is the point — a silent skip or a stored zero row
    would surface only as a mysteriously broken ranking, far from this call site.
    """


def _content_hash(document: str) -> str:
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _norm(vector: list[float]) -> float:
    return sum(component * component for component in vector) ** 0.5


@dataclass(frozen=True)
class ReindexReport:
    embedded: int
    skipped: int
    deleted: int

    @property
    def total_considered(self) -> int:
        return self.embedded + self.skipped


async def reindex(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    embedding_model=None,
    force: bool = False,
) -> ReindexReport:
    """Idempotent by default; ``force=True`` re-embeds every row unconditionally.

    ``engine``/``embedding_model`` are accepted so a caller that already has them (e.g.
    ``app/main.py``'s lifespan, reusing ``app.state.engine`` and the shared, expensive
    ``EmbeddingModel``) doesn't pay for a second copy of either. The CLI entrypoint below
    builds and disposes its own.
    """
    settings = settings or get_settings()
    owns_engine = engine is None
    engine = engine or build_engine(settings)
    embedding_model = embedding_model or build_embedding_model(settings)
    embedding_model_name = settings.embedding_model

    try:
        entries = load_faq_entries(settings.seed_data_dir)

        with Session(engine) as session:
            existing = {
                row.faq_id: row
                for row in session.exec(
                    select(FaqEmbedding).where(
                        FaqEmbedding.embedding_model == embedding_model_name
                    )
                )
            }
            # A width already recorded for this embedding_model, used to catch a
            # dimension drift on a *skip candidate* without needing to embed anything to
            # discover it fresh (see module docstring).
            reference_dims = next(iter(existing.values())).dimensions if existing else None

            to_embed: list[FaqEntry] = []
            for entry in entries:
                row = existing.get(entry.id)
                content_hash = _content_hash(entry.document)
                unchanged = (
                    row is not None
                    and row.content_hash == content_hash
                    and (reference_dims is None or row.dimensions == reference_dims)
                )
                if not force and unchanged:
                    continue
                to_embed.append(entry)

            embedded_count = 0
            if to_embed:
                result = await embedding_model.embed(
                    [entry.document for entry in to_embed], input_type="document"
                )
                vectors = result.embeddings
                now = utcnow()

                for entry, vector in zip(to_embed, vectors, strict=True):
                    norm = _norm(vector)
                    if norm == 0.0:
                        raise ZeroNormEmbeddingError(
                            f"faq_id={entry.id!r} embedded to a zero-norm vector under "
                            f"embedding_model={embedding_model_name!r}; refusing to "
                            "index it (cosine distance against a zero vector is "
                            "undefined under pgvector's `<=>` and would poison ranking)."
                        )

                    row = existing.get(entry.id)
                    if row is None:
                        row = FaqEmbedding(faq_id=entry.id, embedding_model=embedding_model_name)
                        existing[entry.id] = row
                    row.dimensions = len(vector)
                    row.content_hash = _content_hash(entry.document)
                    row.document = entry.document
                    row.question = entry.question
                    row.answer = entry.answer
                    row.category = entry.category
                    row.embedding = list(vector)
                    row.indexed_at = now
                    session.add(row)
                    embedded_count += 1

                session.flush()

            live_ids = {entry.id for entry in entries}
            stale_ids = set(existing) - live_ids
            deleted_count = 0
            if stale_ids:
                session.execute(
                    delete(FaqEmbedding).where(
                        FaqEmbedding.embedding_model == embedding_model_name,
                        FaqEmbedding.faq_id.in_(stale_ids),
                    )
                )
                deleted_count = len(stale_ids)

            session.commit()
    finally:
        if owns_engine:
            engine.dispose()

    report = ReindexReport(
        embedded=embedded_count,
        skipped=len(entries) - embedded_count,
        deleted=deleted_count,
    )
    logger.info(
        "faq_reindexed",
        extra={
            "embedding_model": embedding_model_name,
            "embedded": report.embedded,
            "skipped": report.skipped,
            "deleted": report.deleted,
            "force": force,
        },
    )
    return report


def main(argv: list[str] | None = None) -> int:
    import asyncio

    parser = argparse.ArgumentParser(
        description="(Re)build faq_embeddings from seed_data/faq.json."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed every row, ignoring content_hash/dimensions (default: idempotent).",
    )
    args = parser.parse_args(argv)

    report = asyncio.run(reindex(force=args.force))
    print(
        f"embedded={report.embedded} skipped={report.skipped} deleted={report.deleted}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
