from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.db import database_is_up

router = APIRouter(tags=["system"])


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok", "unavailable"]


@router.get("/health", response_model=Health)
async def health() -> Health:
    db_ok = await database_is_up()
    return Health(status="ok", version=__version__, database="ok" if db_ok else "unavailable")
