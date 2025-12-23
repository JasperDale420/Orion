import os

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None, alias="x-api-key")) -> None:
    expected = os.getenv("ORION_API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="ORION_API_KEY is not configured")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
