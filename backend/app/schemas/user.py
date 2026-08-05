from datetime import datetime

from pydantic import BaseModel, Field

from app.models import User, UserRole, UserStatus

MAX_EMAIL_LENGTH = 320
MIN_PASSWORD_LENGTH = 8
# bcrypt truncates past 72 bytes; app.core.security rejects rather than truncates, so
# the schema stops it earlier with a field-level 422 instead of a 500.
MAX_PASSWORD_LENGTH = 72


class UserResponse(BaseModel):
    """Explicitly enumerated rather than serialized from the ORM object: ``User``
    carries ``hashed_password``, and a response model built by exclusion would start
    leaking it the moment someone adds a field."""

    id: str
    name: str
    email: str
    role: UserRole
    status: UserStatus
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            status=user.status,
            created_at=user.created_at,
        )


class UserCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # `str`, not EmailStr, matching LoginRequest — this codebase does not carry an
    # email-validator dependency and format strictness here would not match login's.
    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: UserRole
    status: UserStatus = "active"


class UserUpdateRequest(BaseModel):
    """PATCH: every field optional, and an omitted field is left untouched.

    Distinguishing "omitted" from "set to null" matters for `role`/`status`, so callers
    use ``model_fields_set`` rather than a null sentinel.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: str | None = Field(
        default=None, min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )
    role: UserRole | None = None
    status: UserStatus | None = None
