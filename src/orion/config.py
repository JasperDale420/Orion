from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

import uuid
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskSettings(BaseSettings):
    max_daily_loss: float = 1000.0
    max_drawdown_pct: float = 0.05
    max_order_size_pct: float = 0.05  # 5% of account equity
    # Legacy compatibility field used by older tests/callers.
    max_order_size_usd: float | None = None
    max_positions: int = 5
    max_ticker_exposure_pct: float = 0.10  # 10% of account equity
    # Legacy compatibility field used by older tests/callers.
    max_ticker_exposure_usd: float | None = None
    risk_per_trade_pct: float = 0.01
    enable_shorting: bool = False
    default_stop_loss_pct: float = 0.02
    time_of_day_bans: list[str] | None = None

    max_system_bps: int = 500

    # Options-specific settings
    max_option_premium_pct: float = 0.02  # Max 2% of equity per option trade
    min_dte: int = 1  # Minimum days to expiration (0-DTE blocked; 1-DTE+ allowed)
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

    # Bracket orders (stop-loss / take-profit at broker level)
    enable_bracket_orders: bool = False  # Off by default for safe paper rollout

    model_config = SettingsConfigDict(env_prefix="ORION_RISK_")


class HeuristicWeights(BaseSettings):
    """Configurable weights for the heuristic scorer fallback.

    Each weight represents the score increment applied when a specific
    condition is met. The base score starts at ``base_score`` and weights
    are added/subtracted according to flow characteristics.
    """

    base_score: float = 0.30  # Starting score for every flow
    premium_500k_plus: float = 0.25  # Premium ≥ $500k
    premium_100k_plus: float = 0.15  # Premium ≥ $100k
    premium_50k_plus: float = 0.10  # Premium ≥ $50k
    premium_25k_plus: float = 0.05  # Premium ≥ $25k
    sweep_bonus: float = 0.15  # Flow is a sweep order
    ask_side_bonus: float = 0.10  # Aggressor aligned with direction
    vol_oi_high: float = 0.10  # Volume/OI > 2.0
    vol_oi_moderate: float = 0.05  # Volume/OI > 1.0
    low_premium_penalty: float = -0.20  # Premium < $10k (noise filter)

    model_config = SettingsConfigDict(env_prefix="ORION_HEURISTIC_")


class SystemSettings(BaseSettings):
    # API Keys
    uw_api_key: str | None = Field(default=None, validation_alias="UW_API_KEY")
    alpaca_api_key: str | None = Field(default=None, validation_alias="ALPACA_API_KEY")
    alpaca_secret_key: str | None = Field(default=None, validation_alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(default=True, validation_alias="ALPACA_PAPER")

    # Environment
    orion_stage: str = Field(default="paper", validation_alias="ORION_STAGE")
    artifacts_dir: str = Field(default="artifacts", validation_alias="ORION_ARTIFACTS_DIR")
    baseline_solver_id: str | None = Field(default=None, validation_alias="ORION_BASELINE_SOLVER_ID")
    db_url: str = Field(
        default="postgresql+asyncpg://orion@localhost:5432/orion_db",
        validation_alias=AliasChoices("DB_URL", "ORION_DB_URL"),
    )
    db_echo: bool = Field(default=False, validation_alias="ORION_DB_ECHO")
    orion_use_gateway: bool = Field(default=True, validation_alias="ORION_USE_GATEWAY")
    ml_prefilter_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        validation_alias="ORION_ML_PREFILTER_THRESHOLD",
    )
    heuristic_cap_live: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        validation_alias="ORION_HEURISTIC_CAP_LIVE",
    )
    ensemble_consensus_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        validation_alias="ORION_ENSEMBLE_CONSENSUS_THRESHOLD",
    )

    # Centralized Gateway + Heber integration settings
    data_gateway_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices("DATA_GATEWAY_URL", "GATEWAY_URL"),
    )
    data_gateway_api_key: str | None = Field(
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
    # Belt-and-suspenders alongside the overnight DB-heartbeat update in
    # ingestion.service._check_overnight_sleep. 600s tolerates the longer
    # cycle times we see in practice (rollups can run 30-60s on busy days).
    ingestion_heartbeat_max_age: int = 600
    max_data_lag_seconds: int = 600  # Pre-market/post-market data can lag 300s+
    alpaca_lookback_minutes: int = Field(default=15, validation_alias="ALPACA_LOOKBACK_MINUTES")
    uw_fetch_limit: int = 5000
    uw_base_url: str = Field(default="https://api.unusualwhales.com", validation_alias="UW_BASE_URL")
    static_watchlist: list[str] = ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "AMZN", "GOOGL", "VIXY"]
    require_rollups_for_signals_live: bool = True

    # Runtime / Observability
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()), validation_alias="ORION_RUN_ID")
    log_format: str = Field(default="json", validation_alias="ORION_LOG_FORMAT")
    api_key: str | None = Field(default=None, validation_alias="ORION_API_KEY")
    metrics_enabled: bool = Field(default=False, validation_alias="ORION_METRICS_ENABLED")
    metrics_port: int = Field(default=8000, validation_alias="ORION_METRICS_PORT")

    # ML / Models
    # /app/models exists inside Docker (volume mount); outside Docker fall back
    # to <project-root>/models so that locally-trained models are picked up
    # without requiring ORION_MODEL_DIR to be set in every env.
    model_dir: Path = Field(
        default=Path("/app/models") if Path("/app/models").exists() else Path(__file__).resolve().parents[2] / "models",
        validation_alias="ORION_MODEL_DIR",
    )
    max_model_age_days: int = Field(default=14, validation_alias="ORION_MAX_MODEL_AGE_DAYS")
    ml_stale_model_policy: str = Field(
        default="warn",
        validation_alias="ORION_ML_STALE_MODEL_POLICY",
        description="Policy when models exceed max_model_age_days: "
        "'skip' = reject stale models (original behavior), "
        "'warn' = load stale models with warning, "
        "'bypass' = skip ML scoring entirely and pass candidates through",
    )
    # --- Exit fallback rules (deterministic, independent of exit classifier) ---
    # Profit-target exit: close when position return crosses this threshold.
    # 1.00 = +100% on the option premium. Conservative because options can
    # continue running; 1.50 (i.e. +150%) is also reasonable. Set to 0 to disable.
    exit_fallback_profit_target_pct: float = Field(
        default=1.00,
        validation_alias="ORION_EXIT_FALLBACK_PROFIT_TARGET_PCT",
    )
    # Time-to-expiry exit: close when DTE drops below this. Prevents pin risk
    # and theta wipeout on the last day. 1 = exit at T-1. Set to 0 to disable.
    exit_fallback_min_dte: int = Field(
        default=1,
        validation_alias="ORION_EXIT_FALLBACK_MIN_DTE",
    )
    # Drawdown exit: close when position has retraced this far from its peak.
    # 0.50 = if max_return_so_far was +200% and current is +100%, that's a 50%
    # retracement → exit. Protects unrealized gains. Set to 0 to disable.
    exit_fallback_max_drawdown_from_peak_pct: float = Field(
        default=0.50,
        validation_alias="ORION_EXIT_FALLBACK_MAX_DRAWDOWN_FROM_PEAK_PCT",
    )
    proposals_dir: str = Field(default="proposals", validation_alias="ORION_PROPOSALS_DIR")

    # SQLite tuning
    sqlite_lock_retry_attempts: int = Field(default=2, ge=0, validation_alias="ORION_SQLITE_LOCK_RETRY_ATTEMPTS")
    sqlite_lock_retry_base_delay_seconds: float = Field(
        default=0.05, ge=0.0, validation_alias="ORION_SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS"
    )
    sqlite_lock_retry_max_delay_seconds: float = Field(
        default=0.5, ge=0.0, validation_alias="ORION_SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS"
    )

    # Heber preference flags
    validate_features_prefer_heber: bool = Field(default=True, validation_alias="ORION_VALIDATE_FEATURES_PREFER_HEBER")
    data_quality_checker_prefer_heber: bool = Field(
        default=True, validation_alias="ORION_DATA_QUALITY_CHECKER_PREFER_HEBER"
    )
    reconcile_backfill_prefer_heber: bool = Field(
        default=True, validation_alias="ORION_RECONCILE_BACKFILL_PREFER_HEBER"
    )
    execution_prefer_heber_recent_flow: bool = Field(
        default=True, validation_alias="ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW"
    )
    feature_enrichment_prefer_heber_context: bool = Field(
        default=True, validation_alias="ORION_FEATURE_ENRICHMENT_PREFER_HEBER_CONTEXT"
    )
    feature_enrichment_enable_gateway_fetch: bool = Field(
        default=False, validation_alias="ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH"
    )

    # Circuit breaker
    reset_circuit_breaker_on_start: bool = Field(default=False, validation_alias="ORION_RESET_CIRCUIT_BREAKER_ON_START")
    trip_circuit_breaker_on_lag: bool = Field(default=False, validation_alias="ORION_TRIP_CIRCUIT_BREAKER_ON_LAG")
    # Master kill switches for both breakers. When false, the breaker still
    # records events (so logs show what would have tripped) but trading is
    # never blocked by it. Intended for forward-testing windows where a
    # spurious trip is more costly than a real broker-error event.
    circuit_breaker_enabled: bool = Field(default=True, validation_alias="ORION_CIRCUIT_BREAKER_ENABLED")
    global_circuit_breaker_enabled: bool = Field(default=True, validation_alias="ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED")
    # Per-process broker-result error-rate breaker (in ExecutionEngine).
    # The previous deque[bool] with maxlen=20 had no time component: a single
    # failure stayed in the deque all day during low-volume periods. The
    # time-windowed version (failures within last N seconds) plus a minimum
    # sample count avoids both the "stuck breaker" and the "first-failure
    # after restart trips it" failure modes.
    circuit_breaker_error_rate: float = Field(default=0.03, validation_alias="ORION_CIRCUIT_BREAKER_ERROR_RATE")
    circuit_breaker_window_seconds: float = Field(
        default=300.0, validation_alias="ORION_CIRCUIT_BREAKER_WINDOW_SECONDS"
    )
    circuit_breaker_min_samples: int = Field(default=5, validation_alias="ORION_CIRCUIT_BREAKER_MIN_SAMPLES")

    # Client URLs
    trading_rag_url: str = Field(default="http://localhost:8005", validation_alias="TRADING_RAG_URL")
    trading_rag_api_key: str | None = Field(default=None, validation_alias="TRADING_RAG_API_KEY")
    orion_api_url: str = Field(default="http://localhost:8000", validation_alias="ORION_API_URL")

    # System Monitor
    monitor_lag_threshold: int = Field(default=300, validation_alias="MONITOR_LAG_THRESHOLD")
    monitor_dlq_lookback: int = Field(default=5, validation_alias="MONITOR_DLQ_LOOKBACK")

    # RAG / Embeddings
    ollama_embedding_model: str = Field(default="nomic-embed-text", validation_alias="OLLAMA_EMBEDDING_MODEL")
    ollama_base_url: str = Field(default="http://host.docker.internal:11434", validation_alias="OLLAMA_BASE_URL")

    model_config = SettingsConfigDict(env_prefix="ORION_")


class MetaSearchSettings(BaseSettings):
    scoring_weights: dict[str, float] = {"sharpe": 0.4, "profit_factor": 0.3, "info_ratio": 0.2, "stability": 0.1}
    model_config = SettingsConfigDict(env_prefix="ORION_META_")


class AgentSettings(BaseSettings):
    model_name: str = "glm-5.1"
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    ai_gateway_url: str = Field(default="http://localhost:8002/v1", validation_alias="ORION_AI_GATEWAY_URL")
    ai_gateway_key: str = Field(default="empire-ai-gateway-key", validation_alias="ORION_AI_GATEWAY_KEY")
    model_config = SettingsConfigDict(env_prefix="ORION_AGENT_")


# Singleton Instances
risk_settings = RiskSettings()
system_settings = SystemSettings()
meta_settings = MetaSearchSettings()
agent_settings = AgentSettings()
heuristic_weights = HeuristicWeights()

# Exports for compatibility
STATIC_WATCHLIST = system_settings.static_watchlist
UNIVERSE_TTL_SECONDS = system_settings.universe_ttl_seconds
