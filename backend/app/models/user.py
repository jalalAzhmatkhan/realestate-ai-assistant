from datetime import datetime
from typing import Literal

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime, utcnow

UserRole = Literal["admin", "agent", "client"]
UserStatus = Literal["active", "disabled"]


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(primary_key=True)
    name: str
    email: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    role: UserRole = Field(sa_column=Column(String, nullable=False, index=True))
    hashed_password: str
    # Users are disabled, never deleted — deletion would orphan their bookings.
    status: UserStatus = Field(
        default="active", sa_column=Column(String, nullable=False, index=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False)
    )
