from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskSettings(BaseSettings):
    max_daily_loss: float = 1000.0
    max_drawdown_pct: float = 0.05
    max_order_size_usd: float = 5000.0
    max_positions: int = 5
    max_ticker_exposure_usd: float = 10000.0
    risk_per_trade_pct: float = 0.01
    enable_shorting: bool = False
    default_stop_loss_pct: float = 0.02
    time_of_day_bans: Optional[List[str]] = None

    max_system_bps: int = 500

    model_config = SettingsConfigDict(env_prefix="ORION_RISK_")


class SystemSettings(BaseSettings):
    # API Keys
    uw_api_key: Optional[str] = Field(default=None, validation_alias="UW_API_KEY")
    alpaca_api_key: Optional[str] = Field(default=None, validation_alias="ALPACA_API_KEY")
    alpaca_secret_key: Optional[str] = Field(default=None, validation_alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(default=True, validation_alias="ALPACA_PAPER")

    # Environment
    orion_stage: str = Field(default="paper", validation_alias="ORION_STAGE")
    artifacts_dir: str = Field(default="artifacts", validation_alias="ORION_ARTIFACTS_DIR")
    baseline_solver_id: Optional[str] = Field(default=None, validation_alias="ORION_BASELINE_SOLVER_ID")
    db_echo: bool = Field(default=False, validation_alias="ORION_DB_ECHO")

    # Universe
    universe_ttl_seconds: int = 28800  # 8 hours (Tracks alerts through EOD)
    ingestion_heartbeat_max_age: int = 70
    max_data_lag_seconds: int = 60
    alpaca_lookback_minutes: int = Field(default=15, validation_alias="ALPACA_LOOKBACK_MINUTES")
    uw_fetch_limit: int = 5000
    static_watchlist: List[str] = ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "AMZN", "GOOGL"]
    require_rollups_for_signals_live: bool = True

    model_config = SettingsConfigDict(env_prefix="ORION_")


class MetaSearchSettings(BaseSettings):
    scoring_weights: dict[str, float] = {"sharpe": 0.4, "profit_factor": 0.3, "info_ratio": 0.2, "stability": 0.1}
    model_config = SettingsConfigDict(env_prefix="ORION_META_")


class AgentSettings(BaseSettings):
    model_name: str = "gpt-5.2"
    reasoning_level: str = Field(default="extra_high", validation_alias="ORION_REASONING_LEVEL")
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    model_config = SettingsConfigDict(env_prefix="ORION_AGENT_")


# Singleton Instances
risk_settings = RiskSettings()
system_settings = SystemSettings()
meta_settings = MetaSearchSettings()
agent_settings = AgentSettings()

# Exports for compatibility
STATIC_WATCHLIST = system_settings.static_watchlist
UNIVERSE_TTL_SECONDS = system_settings.universe_ttl_seconds
