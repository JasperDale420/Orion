"""Shared configuration base for all Empire services.

Usage:
    from empire_core.config import EmpireBaseSettings

    class CerberusSettings(EmpireBaseSettings):
        strategy_count: int = 10
        scan_interval: int = 60

    settings = CerberusSettings()
    print(settings.gateway_url)  # reads EMPIRE_GATEWAY_URL or DATA_GATEWAY_URL or GATEWAY_URL
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class EmpireBaseSettings(BaseSettings):
    """Base settings inherited by all Empire services.

    Env var lookup chains allow each repo's existing env vars to keep working.
    """

    model_config = {"env_file": ".env", "extra": "ignore"}

    gateway_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices(
            "EMPIRE_GATEWAY_URL",
            "DATA_GATEWAY_URL",
            "GATEWAY_URL",
            "CERBERUS_GATEWAY_URL",
            "DATA_INGESTION_URL",
        ),
    )

    redis_url: str = Field(
        default="redis://localhost:6379",
        validation_alias=AliasChoices(
            "EMPIRE_REDIS_URL",
            "REDIS_URL",
        ),
    )

    gateway_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "EMPIRE_GATEWAY_KEY",
            "GATEWAY_KEY",
            "X_GATEWAY_KEY",
        ),
    )

    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices(
            "EMPIRE_LOG_LEVEL",
            "LOG_LEVEL",
        ),
    )
