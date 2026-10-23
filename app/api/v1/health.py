import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import Settings, get_settings
from app.database import get_database, ping_database

router = APIRouter(tags=["health"])


@router.get("/health", summary="Application liveness")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@router.get("/health/ready", summary="Readiness including MongoDB connectivity")
async def readiness(
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        await asyncio.wait_for(ping_database(database), timeout=3)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MongoDB is not reachable.") from exc
    return {"status": "ready", "database": database.name}
