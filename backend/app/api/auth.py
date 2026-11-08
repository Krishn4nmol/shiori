import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUser
from app.auth.rate_limit import login_limiter
from app.auth.security import (
    clear_session_cookie,
    hash_password,
    set_session_cookie,
    verify_password,
)
from app.db import get_session
from app.models import Conversation, Document, User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])
Session = Annotated[AsyncSession, Depends(get_session)]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
Password = Annotated[str, Field(min_length=8, max_length=128)]


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL.match(value) or len(value) > 320:
        raise ValueError("Enter a valid email address.")
    return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: UserRole
    is_active: bool


class Credentials(BaseModel):
    email: str
    password: str = Field(max_length=128)

    _email = field_validator("email")(normalize_email)


class SetupRequest(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=200)
    password: Password

    _email = field_validator("email")(normalize_email)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: Password


class AuthStatus(BaseModel):
    setup_required: bool  # true until the first admin account exists


@router.get("/status", response_model=AuthStatus)
async def auth_status(session: Session) -> AuthStatus:
    count = await session.scalar(select(func.count()).select_from(User))
    return AuthStatus(setup_required=(count or 0) == 0)


@router.post("/setup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def setup_first_admin(body: SetupRequest, session: Session, response: Response) -> User:
    """Create the first admin. Only works while no account exists. Papers and conversations
    created before accounts existed are given to this admin."""
    # Lock the users table so two simultaneous setup requests can't both succeed.
    await session.execute(select(func.pg_advisory_xact_lock(4242)))
    if await session.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup is already done. Please log in.")
    admin = User(
        email=body.email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        role=UserRole.admin,
        is_active=True,
        session_version=0,
    )
    session.add(admin)
    await session.flush()
    await session.execute(
        update(Document).where(Document.uploaded_by_id.is_(None)).values(uploaded_by_id=admin.id)
    )
    await session.execute(
        update(Conversation).where(Conversation.user_id.is_(None)).values(user_id=admin.id)
    )
    await session.commit()
    set_session_cookie(response, admin.id, admin.session_version)
    return admin


@router.post("/login", response_model=UserOut)
async def login(body: Credentials, request: Request, session: Session, response: Response) -> User:
    ip = request.client.host if request.client else "unknown"
    if wait := login_limiter.retry_after(body.email, ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed attempts. Try again in {max(1, wait // 60)} minutes.",
            headers={"Retry-After": str(wait)},
        )
    user = await session.scalar(select(User).where(User.email == body.email))
    if not verify_password(body.password, user.password_hash if user else None) or (
        user is not None and not user.is_active
    ):
        login_limiter.failed(body.email, ip)
        # Same message for unknown email, wrong password and disabled account.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.")
    assert user is not None
    login_limiter.succeeded(body.email, ip)
    set_session_cookie(response, user.id, user.session_version)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange, user: CurrentUser, session: Session, response: Response
) -> None:
    """Change your own password. Logs out your other sessions; this one stays logged in."""
    row = await session.get(User, user.id)
    assert row is not None
    if not verify_password(body.current_password, row.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong.")
    row.password_hash = hash_password(body.new_password)
    row.session_version += 1
    await session.commit()
    set_session_cookie(response, row.id, row.session_version)
