from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Dialect, TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator[datetime]):
    """Persists every datetime as naive UTC and hands it back timezone-aware.

    SQLite has no timezone-aware column type: SQLAlchemy renders an aware datetime
    into a naive string and drops the offset, so the seeded ``+07:00`` viewing slots
    would read back as if they were UTC — a seven-hour error, not a formatting one.
    Normalizing on the way in and re-attaching UTC on the way out makes SQLite and
    Postgres behave identically and keeps every comparison aware-vs-aware.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
