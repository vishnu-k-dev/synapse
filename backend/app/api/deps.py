from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    settings = get_settings()
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return x_api_key
