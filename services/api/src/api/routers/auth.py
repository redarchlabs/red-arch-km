"""Authentication routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.auth.dependencies import CurrentUser, get_current_user

router = APIRouter()


@router.get("/me")
async def get_me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, str]:
    """Return the current authenticated user's info."""
    return {
        "sub": user.sub,
        "username": user.username,
        "email": user.email,
    }
