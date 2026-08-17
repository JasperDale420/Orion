from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

import uuid
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
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
    # Orion's allocated slice of the shared Alpaca paper account. The account
    # (~$1M) is shared across multiple systems, so Gateway reports the full
    # pooled equity. Sizing (max premium/order %) must compute off Orion's
    # slice, not the pool — uncapped seeding was the root of the 5/26
    # over-exposure. Caps the seeded equity baseline; None = no cap.
    allocated_equity: float | None = 100_000.0
    enable_shorting: bool = False
    default_stop_loss_pct: float = 0.02
    time_of_day_bans: list[str] | None = None

    max_system_bps: int = 500

    # Options-specific settings
    max_option_premium_pct: float = 0.02  # Max 2% of equity per option trade
    min_dte: int = 1  # Minimum days to expiration (0-DTE blocked; 1-DTE+ allowed)
    max_option_positions: int = 15  # Max simultaneous option positions (hard backstop over bucket caps)

    # Sample-size sizing: a fixed premium debit per trade gives every closed
    # trade uniform weight, so per-bucket expectancy is a clean per-trade
    # average. 0 disables (falls back to solver risk_per_trade_bps sizing).
    fixed_premium_per_trade: float = 500.0
    max_contracts_per_trade: int = 5
    # Per-bucket concurrent position caps — without these, the highest-volume
    # rule would hog every slot and the other buckets would never build a
    # sample. Counted from open trade-journal rows; max_option_positions
    # (broker-synced, includes pending) remains the hard backstop.
    option_bucket_caps: dict[str, int] = Field(
        default_factory=lambda: {"0DTE": 4, "SHORT_SWING": 6, "SWING": 8, "POSITION": 2}
    )
    max_positions_per_underlying: int = 2
    max_positions_per_index_underlying: int = 3  # SPY/QQQ/IWM get one extra slot

    # Contract liquidity gate (checked at order time from the live chain).
    # A zero-bid or wide-spread contract can't be exited at anything near its
    # mark — dust entries strand until expiry. 0 disables either check.
    min_option_mid: float = 0.20  # Reject contracts with mid below this
    max_option_spread_pct: float = 0.25  # Fallback spread cap for unknown buckets
    # Per-bucket spread caps: profit targets must be multiples of round-trip
    # spread cost, so faster buckets need tighter spreads.
    option_bucket_spread_caps: dict[str, float] = Field(
        default_factory=lambda: {"0DTE": 0.10, "SHORT_SWING": 0.15, "SWING": 0.15, "POSITION": 0.15}
    )

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


# Recommended environment variable names for the alias pairs below:
#   ORION_DB_URL            (alias: DB_URL)
#   DATA_GATEWAY_URL        (alias: GATEWAY_URL)
#   DATA_GATEWAY_API_KEY    (alias: GATEWAY_API_KEY)
#   DISCORD_WEBHOOK_URL     (alias: ORION_DISCORD_WEBHOOK_URL)
# Prefer the recommended name in new config. PRECEDENCE WARNING: when BOTH
# names of a pair are set, pydantic's AliasChoices order decides — the
# FIRST-listed alias on each field wins, which is DB_URL (not ORION_DB_URL),
# DATA_GATEWAY_URL, DATA_GATEWAY_API_KEY, and ORION_DISCORD_WEBHOOK_URL (not
# DISCORD_WEBHOOK_URL). Set only one name per pair. The alias orders are
# load-bearing for deployed .env files; do not reorder or remove them.
class SystemSettings(BaseSettings):
    # API Keys
    uw_api_key: str | None = Field(default=None, validation_alias="UW_API_KEY")
    alpaca_api_key: str | None = Field(default=None, validation_alias="ALPACA_API_KEY")
    alpaca_secret_key: str | None = Field(default=None, validation_alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(default=True, validation_alias="ALPACA_PAPER")

    # --- Dedicated Alpaca account scaffolding (DORMANT; A4 of the 2026-06-11
    # redesign plan). These configure a future direct-to-Alpaca broker path for
    # a dedicated paper account, replacing the shared-account attribution
    # gymnastics. They are deliberately ORION_-prefixed so they never collide
    # with the shared-account ALPACA_API_KEY / ALPACA_SECRET_KEY above. Nothing
    # reads these yet — the direct broker client is a separate future task. See
    # docs/configuration-guide.md "Enabling a dedicated Alpaca account".
    orion_alpaca_api_key: str | None = Field(default=None, validation_alias="ORION_ALPACA_API_KEY")
    orion_alpaca_secret_key: str | None = Field(default=None, validation_alias="ORION_ALPACA_SECRET_KEY")
    orion_alpaca_paper: bool = Field(default=True, validation_alias="ORION_ALPACA_PAPER")
    # Broker routing mode. "gateway" (default) routes orders through
    # Data-Gateway → shared Alpaca account (today's behavior). "direct" is
    # scaffolded but NOT implemented: it raises at settings load so a half-mode
    # can never silently start. Flip to "direct" only after the direct client
    # exists and a dedicated key is set (see the doc section above).
    broker_mode: str = Field(default="gateway", validation_alias="ORION_BROKER_MODE")

    @field_validator("broker_mode")
    @classmethod
    def _validate_broker_mode(cls, value: str) -> str:
        # COERCE, never raise (adversarial-review finding): SystemSettings is
        # instantiated at module import by every service INCLUDING the dead-man
        # watchdog, so a raising validator turns a stray .env value into a
        # fleet-wide boot kill switch with no surviving alerter. Given this
        # repo's silent-stall history, log loudly and fall back to gateway.
        if value == "direct":
            import logging

            logging.getLogger("orion.config").critical(
                "ORION_BROKER_MODE=direct is scaffolded but not implemented — "
                "falling back to 'gateway'. See docs/configuration-guide.md "
                "'Enabling a dedicated Alpaca account'."
            )
            return "gateway"
        if value not in {"gateway"}:
            import logging

            logging.getLogger("orion.config").critical(
                "Unknown ORION_BROKER_MODE %r — falling back to 'gateway' (must be 'gateway' or 'direct').",
                value,
            )
            return "gateway"
        return value

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
    discord_webhook_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ORION_DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"),
    )
    heber_catalog_url: str = Field(
        default="http://localhost:8085/api/v1",
        validation_alias="HEBER_CATALOG_URL",
    )
    heber_data_root: Path = Field(
        default=Path("/Volumes/heber/data"),
        validation_alias="HEBER_DATA_ROOT",
    )
    # Live ML scoring needs only the most recent Gold feature row per symbol
    # as-of decision time, so it scans just the last N days of dt= partitions
    # rather than the full (unbounded) dataset history. Bounds per-candidate
    # read latency that was aging candidates past max_data_lag_seconds. A row
    # last computed longer ago than this is treated as missing (scorer defaults
    # to None). Training/backfill callers pass no lookback and read full history.
    gold_feature_lookback_days: int = Field(
        default=7,
        validation_alias="ORION_GOLD_FEATURE_LOOKBACK_DAYS",
    )

    # Universe
    universe_ttl_seconds: int = 28800  # 8 hours (Tracks alerts through EOD)
    # Belt-and-suspenders alongside the overnight DB-heartbeat update in
    # ingestion.service._check_overnight_sleep. 600s tolerates the longer
    # cycle times we see in practice (rollups can run 30-60s on busy days).
    ingestion_heartbeat_max_age: int = 600
    max_data_lag_seconds: int = 600  # Pre-market/post-market data can lag 300s+
    # Per-bucket signal-age budgets (seconds) — override the flat
    # max_data_lag_seconds at preflight. A 2-minute-old 0DTE momentum signal
    # is dead; a 15-minute-old multi-day swing thesis is fine. JSON env, e.g.
    # ORION_BUCKET_SIGNAL_AGE_BUDGETS='{"0DTE": 90}'.
    bucket_signal_age_budgets: dict[str, int] = Field(
        default_factory=lambda: {"0DTE": 120, "SHORT_SWING": 300, "SWING": 900, "POSITION": 900},
        validation_alias="ORION_BUCKET_SIGNAL_AGE_BUDGETS",
    )
    # Liquid-underlying allowlist for the bucket entry rules — the cheapest
    # option-liquidity proxy (no chain data needed at signal time). Index ETFs
    # + megacaps/high-option-volume names. JSON-array env override.
    liquid_universe: list[str] = Field(
        default_factory=lambda: [
            # Index / sector ETFs
            "SPY",
            "QQQ",
            "IWM",
            "DIA",
            "TLT",
            "GLD",
            "SLV",
            "XLF",
            "XLE",
            "SMH",
            # Megacap tech
            "AAPL",
            "MSFT",
            "NVDA",
            "AMZN",
            "GOOGL",
            "GOOG",
            "META",
            "TSLA",
            "AVGO",
            "AMD",
            "INTC",
            "MU",
            "QCOM",
            "TSM",
            "ORCL",
            "CRM",
            "ADBE",
            "NFLX",
            "NOW",
            # High-option-volume names
            "PLTR",
            "COIN",
            "MSTR",
            "HOOD",
            "SOFI",
            "UBER",
            "ABNB",
            "SHOP",
            "SNOW",
            "CRWD",
            "PANW",
            "SQ",
            "PYPL",
            "DELL",
            "SMCI",
            "ARM",
            "MRVL",
            "IREN",
            # Financials / industrials / energy
            "BAC",
            "JPM",
            "GS",
            "MS",
            "C",
            "WFC",
            "XOM",
            "CVX",
            "OXY",
            "BA",
            "CAT",
            "GE",
            "F",
            "GM",
            # Consumer / healthcare
            "LLY",
            "UNH",
            "PFE",
            "MRNA",
            "WMT",
            "COST",
            "TGT",
            "HD",
            "DIS",
            "SBUX",
            "NKE",
            "MCD",
        ],
        validation_alias="ORION_LIQUID_UNIVERSE",
    )
    # First flow poll on startup looks back this many minutes from `now` to
    # seed `_last_flow_poll_ts` (was a hardcoded 15-minute literal).
    initial_flow_lookback_minutes: int = Field(
        default=15,
        validation_alias="ORION_INITIAL_FLOW_LOOKBACK_MINUTES",
    )
    # Each periodic flow poll rewinds the watermark by this overlap before
    # reading, so a Heber outage that recovers replays the gap window instead
    # of jumping the watermark to `now` and silently dropping it. Re-delivered
    # events are absorbed by the DeduplicationEngine + bronze ON CONFLICT; the
    # born-stale drop in _poll_heber_flow still discards anything past the
    # data-lag budget, so the overlap only resurfaces recent unseen events.
    flow_poll_overlap_seconds: int = Field(
        default=120,
        validation_alias="ORION_FLOW_POLL_OVERLAP_SECONDS",
    )
    # UW flow delivery path (redesign R1 / B1). Default `poll` in code is
    # today's exact Heber-Silver poll behavior; deploy sets `shadow`.
    #   poll   — Heber-Silver poll only (unchanged).
    #   shadow — consume push (Gateway WS) AND poll; log per-cycle parity to
    #            flow_push_parity. Dedup collapses the overlap so the pipeline
    #            sees each event once (no double candidates).
    #   push   — push primary; poll retained as the degrade/replay gap-filler.
    flow_source: Literal["poll", "shadow", "push"] = Field(
        default="poll",
        validation_alias="ORION_FLOW_SOURCE",
    )
    alpaca_lookback_minutes: int = Field(default=15, validation_alias="ALPACA_LOOKBACK_MINUTES")
    uw_fetch_limit: int = 5000
    uw_base_url: str = Field(default="https://api.unusualwhales.com", validation_alias="UW_BASE_URL")
    static_watchlist: list[str] = ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "AMZN", "GOOGL", "VIXY"]
    require_rollups_for_signals_live: bool = True
    # Self-healing universe (2026-06-01 near-outage). A DB-down/sparse start
    # previously pinned the WS subscription to the static watchlist for the
    # whole session with no recovery. Re-hydrate from candidate_trades on this
    # interval so a degraded start self-broadens within minutes. 0 disables.
    universe_rehydrate_interval_seconds: int = Field(
        default=300,
        validation_alias="ORION_UNIVERSE_REHYDRATE_INTERVAL_SECONDS",
    )
    # Breadth alarm: page when subscribed Alpaca bar breadth collapses to
    # ~static-watchlist size during market hours (06-01 was 314 -> 11). Sits
    # above the 11-ticker static floor and below normal 100s+ breadth.
    universe_breadth_min_tickers: int = Field(
        default=30,
        validation_alias="ORION_UNIVERSE_BREADTH_MIN_TICKERS",
    )

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
    # --- Exit rules (deterministic, per-bucket — the primary exit policy) ---
    # Defaults live in execution/exit_fallback_rules.py:DEFAULT_BUCKET_PARAMS
    # (profit target / stop loss / time stops / drawdown per bucket). This JSON
    # env var overrides individual fields per bucket, e.g.
    # ORION_EXIT_BUCKET_OVERRIDES='{"0DTE": {"profit_target_pct": 0.5}}'
    exit_bucket_overrides: dict[str, dict[str, float | int | str]] = Field(
        default_factory=dict,
        validation_alias="ORION_EXIT_BUCKET_OVERRIDES",
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
    trip_circuit_breaker_on_lag: bool = Field(default=False, validation_alias="ORION_TRIP_CIRCUIT_BREAKER_ON_LAG")
    # Master kill switch for the per-process broker-error breaker. When false,
    # it still records events (so logs show what would have tripped) but
    # trading is never blocked by it. Intended for forward-testing windows
    # where a spurious trip is more costly than a real broker-error event.
    # The GLOBAL breaker has no such switch: it is the drawdown/daily-loss
    # kill switch and the operator's emergency stop, and an OPEN row always
    # halts new entries.
    circuit_breaker_enabled: bool = Field(default=True, validation_alias="ORION_CIRCUIT_BREAKER_ENABLED")
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
    orion_api_url: str = Field(default="http://localhost:8000", validation_alias="ORION_API_URL")

    # System Monitor
    monitor_lag_threshold: int = Field(default=300, validation_alias="MONITOR_LAG_THRESHOLD")
    monitor_dlq_lookback: int = Field(default=5, validation_alias="MONITOR_DLQ_LOOKBACK")

    model_config = SettingsConfigDict(env_prefix="ORION_")


def warn_on_default_dev_credentials(system: "SystemSettings") -> list[str]:
    """Log a WARNING when known default dev credentials are still active.

    Returns the list of field names found using a default dev credential so
    callers/tests can assert on it. Silent (no warning) when none are active.
    """
    from orion.shared.logger import setup_struct_logger

    flagged: list[str] = []
    if system.data_gateway_api_key == "gw_orion_trading_key_55555":  # pragma: allowlist secret
        flagged.append("data_gateway_api_key")

    if flagged:
        log = setup_struct_logger("orion.config")
        for field in flagged:
            log.warning("default_dev_credential_in_use", field=field)
    return flagged


# Singleton Instances
risk_settings = RiskSettings()
system_settings = SystemSettings()
heuristic_weights = HeuristicWeights()

warn_on_default_dev_credentials(system_settings)

# Exports for compatibility
STATIC_WATCHLIST = system_settings.static_watchlist
UNIVERSE_TTL_SECONDS = system_settings.universe_ttl_seconds
