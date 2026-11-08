"""Admin-only user management. Users are deactivated, never deleted, so their papers and
history stay attributable."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Password, UserOut, normalize_email
from app.auth.deps import AdminUser, require_admin
from app.auth.security import hash_password
from app.db import get_session
from app.models import User, UserRole

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]


class UserCreate(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=200)
    password: Password  # temporary password; the user can change it after logging in
    role: UserRole = UserRole.member

    _email = field_validator("email")(normalize_email)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: UserRole | None = None
    is_active: bool | None = None
    password: Password | None = None  # reset to a new temporary password


@router.get("", response_model=list[UserOut])
async def list_users(session: Session) -> list[User]:
    return list(await session.scalars(select(User).order_by(User.created_at)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, session: Session) -> User:
    user = User(
        email=body.email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        session_version=0,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email exists.") from err
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: AdminUser, session: Session
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")

    removes_admin = (body.role == UserRole.member or body.is_active is False) and (
        user.role == UserRole.admin and user.is_active
    )
    if removes_admin:
        if user.id == admin.id:
            raise HTTPException(400, "You can't remove your own admin access.")
        admins = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.admin, User.is_active.is_(True))
        )
        if (admins or 0) <= 1:
            raise HTTPException(400, "There must be at least one active admin.")

    if body.name is not None:
        user.name = body.name.strip()
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None and body.is_active != user.is_active:
        user.is_active = body.is_active
        user.session_version += 1  # deactivation logs them out everywhere
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        user.session_version += 1
    await session.commit()
    await session.refresh(user)
    return user
