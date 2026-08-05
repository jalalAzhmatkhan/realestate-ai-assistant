"""Startup seed loader.

Loads ``backend/seed_data/*.json`` into an empty database so the app is usable
without a manual seeding step. No-ops on a non-empty database regardless of
``SEED_ON_STARTUP``; the flag is the explicit kill switch on top of that.

``faq.json`` is deliberately absent from the load order — it feeds the in-process
RAG index (``app/rag``), not a table.
"""

import json
import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, select

from app.core.config import Settings
from app.models import AvailabilitySlot, Booking, Property, User

logger = logging.getLogger(__name__)

# Dependency order: a booking references a slot, a property and two users.
SEED_FILES: tuple[tuple[str, type[SQLModel], dict[str, Callable[[str], Any]]], ...] = (
    ("users.json", User, {"created_at": datetime.fromisoformat}),
    ("properties.json", Property, {"listed_date": date.fromisoformat}),
    ("agent_availability.json", AvailabilitySlot, {"slot_time": datetime.fromisoformat}),
    (
        "viewings.json",
        Booking,
        {"slot_time": datetime.fromisoformat, "created_at": datetime.fromisoformat},
    ),
)


def seed_if_empty(engine: Engine, settings: Settings) -> None:
    if not settings.seed_on_startup:
        logger.info("seed_skipped", extra={"reason": "seed_on_startup_disabled"})
        return

    seed_dir = Path(settings.seed_data_dir)
    with Session(engine) as session:
        if session.exec(select(User.id).limit(1)).first() is not None:
            logger.info("seed_skipped", extra={"reason": "database_not_empty"})
            return

        loaded: dict[str, int] = {}
        for filename, model, converters in SEED_FILES:
            rows = _read_rows(seed_dir / filename)
            for row in rows:
                session.add(model(**_convert(row, converters)))
            # SQLAlchemy derives flush order from relationship() declarations, which
            # these models deliberately do not have, so it would otherwise pick its
            # own inter-table order and trip the foreign keys. One flush per file
            # pins the dependency order; the whole load is still one transaction.
            session.flush()
            loaded[filename] = len(rows)

        session.commit()

    logger.info("seed_loaded", extra={"seed_dir": str(seed_dir), "rows": loaded})


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _convert(row: dict[str, Any], converters: dict[str, Callable[[str], Any]]) -> dict[str, Any]:
    """Table-mode SQLModel skips validation, so an ISO string handed to a datetime
    column would be persisted verbatim. Parse the temporal fields explicitly."""
    return {
        key: converters[key](value) if key in converters else value
        for key, value in row.items()
    }
