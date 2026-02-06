from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

from pathlib import Path
from typing import List, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskSettings(BaseSettings):
    max_daily_loss: float = 1000.0
    max_drawdown_pct: float = 0.05
    max_order_size_pct: float = 0.05  # 5% of account equity
    max_positions: int = 5
    max_ticker_exposure_pct: float = 0.10  # 10% of account equity
    risk_per_trade_pct: float = 0.01
    enable_shorting: bool = False
    default_stop_loss_pct: float = 0.02
    time_of_day_bans: Optional[List[str]] = None

    max_system_bps: int = 500

    # Options-specific settings
    max_option_premium_pct: float = 0.02  # Max 2% of equity per option trade
    min_dte: int = 3  # Minimum days to expiration
    max_option_positions: int = 3  # Max simultaneous option positions

    # Portfolio-level Greeks limits (options risk)
    max_portfolio_delta: float = 500.0  # Absolute delta exposure limit
    max_portfolio_gamma: float = 100.0  # Absolute gamma exposure limit
    max_portfolio_vega: float = 200.0  # Absolute vega exposure limit (IV crush protection)
    max_position_delta: float = 100.0  # Per-position delta limit
    max_position_vega: float = 50.0  # Per-position vega limit
    enable_greeks_checks: bool = True  # Toggle Greeks checks

    # Sector concentration limits
    max_sector_exposure_pct: float = 0.30  # Max 30% of portfolio in one sector
    enable_sector_checks: bool = True  # Toggle sector concentration checks

    # 0DTE time-of-day wind-down
    zero_dte_cutoff_minutes: int = 60  # Stop new 0DTE entries X minutes before close
    zero_dte_reduce_size_after_minutes: int = 120  # Reduce size after X minutes before close
    zero_dte_reduced_size_pct: float = 0.50  # Size reduction factor (50% of normal)
    enable_zero_dte_winddown: bool = True  # Toggle 0DTE wind-down

    # Correlation-aware position sizing
    correlation_size_scaling: bool = False  # Disabled by default for safe rollout
    correlation_lookback_days: int = 30  # Days of price history for correlation
    correlation_threshold: float = 0.70  # Correlation above this triggers penalty
    correlation_penalty_factor: float = 0.30  # Size multiplier at max correlation
    min_bars_for_correlation: int = 20  # Skip adjustment if insufficient data

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
    orion_use_gateway: bool = Field(default=True, validation_alias="ORION_USE_GATEWAY")

    # Centralized Gateway + Heber integration settings
    data_gateway_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices("DATA_GATEWAY_URL", "GATEWAY_URL"),
    )
    data_gateway_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("DATA_GATEWAY_API_KEY", "GATEWAY_API_KEY"),
    )
    heber_catalog_url: str = Field(
        default="http://localhost:8085/api/v1",
        validation_alias="HEBER_CATALOG_URL",
    )
    heber_data_root: Path = Field(
        default=Path("/Volumes/heber/data"),
        validation_alias="HEBER_DATA_ROOT",
    )

    # Universe
    universe_ttl_seconds: int = 28800  # 8 hours (Tracks alerts through EOD)
    ingestion_heartbeat_max_age: int = 70
    max_data_lag_seconds: int = 90  # Alpaca 1m bars are naturally 60-80s behind
    alpaca_lookback_minutes: int = Field(default=15, validation_alias="ALPACA_LOOKBACK_MINUTES")
    uw_fetch_limit: int = 5000
    static_watchlist: List[str] = ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "AMZN", "GOOGL", "VIXY"]
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
