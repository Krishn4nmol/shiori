"""FastAPI dependencies: who is calling, and are they allowed to."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.security import COOKIE_NAME, read_session_token
from app.db import SessionLocal
from app.models import User, UserRole


async def get_current_user(request: Request) -> User:
    """The logged-in user, or 401. Checked on every request, so deactivating a user or
    changing their password takes effect immediately."""
    token = request.cookies.get(COOKIE_NAME)
    claims = read_session_token(token) if token else None
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    async with SessionLocal() as session:
        user = await session.get(User, claims.user_id)
    if user is None or not user.is_active or user.session_version != claims.session_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has ended. Please log in.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can do this.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
