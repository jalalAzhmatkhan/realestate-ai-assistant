from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import UserRole, UserStatus

ClientType = Literal["api", "browser"]


class LoginRequest(BaseModel):
    # `str`, not EmailStr: a malformed address must fail the same way an unknown one
    # does (401 invalid_credentials), not with a 422 that reveals the format was the
    # only thing wrong.
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    # Chosen explicitly by the caller rather than inferred from Origin/User-Agent —
    # an implicit rule would be spoofable and invisible in the OpenAPI schema.
    client_type: ClientType = "api"


class UserPublic(BaseModel):
    id: str
    name: str
    email: str
    role: UserRole


class LoginResponse(BaseModel):
    # Null in browser mode by design: returning both the httpOnly cookie and the raw
    # token would put a Bearer credential into JavaScript memory, which is exactly
    # what httpOnly exists to prevent. Kept in the schema so both modes share one
    # response model.
    access_token: str | None
    token_type: Literal["bearer"] | None
    expires_in: int
    csrf_token: str | None
    user: UserPublic


class SessionInfo(BaseModel):
    auth_method: Literal["cookie", "bearer"]
    expires_at: datetime
    csrf_token: str | None


class MeResponse(BaseModel):
    id: str
    name: str
    email: str
    role: UserRole
    status: UserStatus
    session: SessionInfo
