"""``/api/v1/users`` — admin-only user administration.

Denial here is ``403 forbidden``, not the ``404`` used for out-of-scope individual
records elsewhere: an ``agent`` or ``client`` may not touch this endpoint *at all*, and
that fact is not worth hiding. Only the per-record lookups below fall back to ``404``.

Users are disabled, never deleted — a deletion would orphan their bookings — so there is
no ``DELETE`` route; ``PATCH {"status": "disabled"}`` is the deactivation path.
"""

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, or_, select

from app.api.deps import DbSession, enforce_csrf, require_role
from app.api.pagination import Page, PageParamsDep, paginate
from app.core.exceptions import EmailAlreadyExistsError, UserNotFoundError
from app.core.security import hash_password
from app.models import User, UserRole, UserStatus
from app.schemas.user import UserCreateRequest, UserResponse, UserUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(enforce_csrf)],
)

USER_SORT_COLUMNS = {
    "name": col(User.name),
    "email": col(User.email),
    "role": col(User.role),
    "created_at": col(User.created_at),
}
DEFAULT_USER_SORT = "name"

AdminUser = Annotated[User, Depends(require_role("admin"))]


def _normalize_email(email: str) -> str:
    """Lowercased and stripped, matching how ``POST /auth/login`` looks an address up —
    otherwise a user created as ``A@b.test`` could never log in."""
    return email.strip().lower()


def _assert_email_free(db: Session, email: str, *, exclude_user_id: str | None = None) -> None:
    statement = select(User).where(col(User.email) == email)
    if exclude_user_id is not None:
        statement = statement.where(col(User.id) != exclude_user_id)
    if db.exec(statement).first() is not None:
        raise EmailAlreadyExistsError()


@router.get("", response_model=Page[UserResponse])
def list_users(
    admin: AdminUser,
    db: DbSession,
    params: PageParamsDep,
    q: Annotated[str | None, Query(description="Free text over name and email")] = None,
    role: Annotated[list[UserRole] | None, Query(description="Repeatable")] = None,
    status: Annotated[list[UserStatus] | None, Query(description="Repeatable")] = None,
) -> Page[UserResponse]:
    """List platform users."""
    statement = select(User)
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(col(User.name).ilike(pattern), col(User.email).ilike(pattern))
        )
    if role:
        statement = statement.where(col(User.role).in_(role))
    if status:
        statement = statement.where(col(User.status).in_(status))

    page = paginate(
        db,
        statement,
        params,
        sort_columns=USER_SORT_COLUMNS,
        default_sort=DEFAULT_USER_SORT,
        item_factory=UserResponse.from_user,
    )
    logger.info("users_listed", extra={"user_id": admin.id, "total": page.total})
    return page


@router.post("", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreateRequest, admin: AdminUser, db: DbSession) -> UserResponse:
    """Create a user. The password is hashed here and never echoed back."""
    email = _normalize_email(payload.email)
    _assert_email_free(db, email)

    user = User(
        id=f"u-{uuid4().hex[:12]}",
        name=payload.name,
        email=email,
        role=payload.role,
        status=payload.status,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(
        "user_created",
        extra={"user_id": admin.id, "created_user_id": user.id, "created_role": user.role},
    )
    return UserResponse.from_user(user)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str, payload: UserUpdateRequest, admin: AdminUser, db: DbSession
) -> UserResponse:
    """Partially update a user: rename, change role, enable/disable, or reset password.

    An omitted field is left untouched — ``model_fields_set`` distinguishes that from an
    explicit value, so a PATCH carrying only ``status`` cannot blank out the name.
    """
    user = db.get(User, user_id)
    if user is None:
        raise UserNotFoundError()

    fields = payload.model_fields_set
    if "email" in fields and payload.email is not None:
        email = _normalize_email(payload.email)
        _assert_email_free(db, email, exclude_user_id=user.id)
        user.email = email
    if "name" in fields and payload.name is not None:
        user.name = payload.name
    if "role" in fields and payload.role is not None:
        user.role = payload.role
    if "status" in fields and payload.status is not None:
        user.status = payload.status
    if "password" in fields and payload.password is not None:
        user.hashed_password = hash_password(payload.password)

    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(
        "user_updated",
        extra={
            "user_id": admin.id,
            "updated_user_id": user.id,
            # Never the values, so a password reset cannot reach a log.
            "updated_fields": sorted(fields),
        },
    )
    return UserResponse.from_user(user)
