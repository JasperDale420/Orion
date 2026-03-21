"""Feature configuration constants for ML pattern mining.

All feature column lists, equity Gold dataset mappings, categorical columns,
and trade bucket configurations live here. These are used by training_data,
model_training, and the pattern_miner orchestrator.
"""

# Feature configuration - ENTRY-TIME ONLY (no outcome leakage)
# These features are known at trade entry and don't reveal the outcome
FEATURE_COLUMNS = [
    # Alert-level features (25 original)
    "strike",
    "days_to_expiry",
    "premium",
    "volume",
    "open_interest",
    "volume_oi_ratio",
    "spot_price",
    "contract_price",
    "moneyness",
    "log_moneyness",
    "delta",
    "gamma",
    "theta",
    "vega",
    "iv",
    "underlying_30d_return",
    "underlying_5d_return",
    "underlying_1d_return",
    "realized_vol_20d",
    "iv_rank",
    "hour_of_day",
    "minute_of_hour",
    "day_of_week",
    "minutes_since_open",
    "minutes_to_close",
    # Equity-level Gold features (18 new — asof-joined from Heber Gold)
    # Momentum (from momentum_features Gold dataset)
    "momentum_1d",
    "momentum_5d",
    "momentum_10d",
    "momentum_20d",
    "rsi_14",
    "rsi_28",
    "macd",
    "macd_signal",
    # Volatility (from volatility_features Gold dataset)
    "vol_5d",
    "vol_20d",
    "vol_ratio_5_20",
    "atr_14",
    "bb_width_20",
    "price_zscore_20d",
    # Flow (from flow_features Gold dataset)
    "total_premium_24h",
    "call_put_premium_ratio",
    "net_premium_24h",
    "sweep_count_24h",
    "net_bull_premium_lr",
    "sweep_volume_share",
    # Market regime (from market_regime_features Gold dataset — market-level, broadcast to all tickers)
    "dispersion",
    "vol_of_vol",
    "breadth_proxy",
    "yield_curve_slope",
    # Derived features (computed at runtime from existing features)
    "iv_vs_realized",
    "vega_theta_ratio",
    "gamma_delta_ratio",
    "dollar_gamma",
    "theta_premium_ratio",
    # Flow normalization (from flow_normalization_features Gold dataset)
    "adv_premium_20d",
    "adv_volume_20d",
    "adv_oi_20d",
    # Runtime-derived from flow normalization
    "premium_vs_adv",
    "volume_vs_adoi",
    "relative_oi_buildup",
    # IV surface (from iv_surface_features Gold dataset)
    "put_call_iv_skew",
    "term_structure_slope",
    "iv_change_1d",
    # Ticker base rates (from ticker_base_rates Gold dataset)
    "ticker_win_rate_90d",
    "ticker_alert_frequency",
    "ticker_flow_predictability",
    # Flow context (from flow_context_features Gold dataset — per-alert)
    "same_ticker_alerts_1h",
    "directional_agreement_4h",
    "repeat_ticker_days_5d",
    # GEX regime (from gex_regime_features Gold dataset — market-level)
    "net_gex",
    "gex_regime",
    "gex_flip_distance",
    # Flow toxicity (from flow_toxicity_features Gold dataset)
    "flow_toxicity_1d",
    "toxicity_acceleration",
    # OI momentum (from oi_momentum_features Gold dataset)
    "oi_buildup_ratio",
    "new_position_signal",
    "oi_change_momentum_5d",
    # Market tide context (from market_tide_context_features Gold dataset — market-level)
    "market_sentiment_score",
    "market_premium_momentum",
    # Darkpool confirmation (from darkpool_features Gold dataset)
    "darkpool_notional_1d",
    "darkpool_premium_ratio",
    "darkpool_activity_zscore",
    # Straddle momentum (from straddle_momentum_features Gold dataset)
    "straddle_return_1m",
    "straddle_return_3m",
    # Trend scanning labels/features (from trend_scan_features Gold dataset)
    "trend_scan_horizon",
    "trend_scan_t_value",
    # Sector flow context (from sector_flow_features Gold dataset — per-alert)
    "sector_flow_alignment",
    "sector_call_put_ratio",
    # New runtime derived features
    "ask_side_dominance",
    "aggressor_conviction",
    "max_pain_distance",
    "days_to_nearest_opex",
]

# Equity-level feature names grouped by source Gold dataset
EQUITY_MOMENTUM_FEATURES = [
    "momentum_1d",
    "momentum_5d",
    "momentum_10d",
    "momentum_20d",
    "rsi_14",
    "rsi_28",
    "macd",
    "macd_signal",
]
EQUITY_VOLATILITY_FEATURES = [
    "vol_5d",
    "vol_20d",
    "vol_ratio_5_20",
    "atr_14",
    "bb_width_20",
    "price_zscore_20d",
]
EQUITY_FLOW_FEATURES = [
    "total_premium_24h",
    "call_put_premium_ratio",
    "net_premium_24h",
    "sweep_count_24h",
    "net_bull_premium_lr",
    "sweep_volume_share",
]
EQUITY_REGIME_FEATURES: list[str] = [
    "dispersion",
    "vol_of_vol",
    "breadth_proxy",
    "yield_curve_slope",
]
EQUITY_FLOW_NORM_FEATURES = ["adv_premium_20d", "adv_volume_20d", "adv_oi_20d"]
EQUITY_IV_SURFACE_FEATURES = ["put_call_iv_skew", "term_structure_slope", "iv_change_1d"]
EQUITY_TICKER_RATES_FEATURES = ["ticker_win_rate_90d", "ticker_alert_frequency", "ticker_flow_predictability"]
ALERT_FLOW_CONTEXT_FEATURES = ["same_ticker_alerts_1h", "directional_agreement_4h", "repeat_ticker_days_5d"]
EQUITY_GEX_REGIME_FEATURES = ["net_gex", "gex_regime", "gex_flip_distance"]
EQUITY_FLOW_TOXICITY_FEATURES = ["flow_toxicity_1d", "toxicity_acceleration"]
EQUITY_OI_MOMENTUM_FEATURES = ["oi_buildup_ratio", "new_position_signal", "oi_change_momentum_5d"]
EQUITY_MARKET_TIDE_FEATURES = ["market_sentiment_score", "market_premium_momentum"]
EQUITY_DARKPOOL_FEATURES = ["darkpool_notional_1d", "darkpool_premium_ratio", "darkpool_activity_zscore"]
EQUITY_STRADDLE_FEATURES = ["straddle_return_1m", "straddle_return_3m"]
EQUITY_TREND_SCAN_FEATURES = ["trend_scan_horizon", "trend_scan_t_value"]
ALERT_SECTOR_FLOW_FEATURES = ["sector_flow_alignment", "sector_call_put_ratio"]

EQUITY_GOLD_DATASETS: dict[str, list[str]] = {
    "momentum_features": EQUITY_MOMENTUM_FEATURES,
    "volatility_features": EQUITY_VOLATILITY_FEATURES,
    "flow_features": EQUITY_FLOW_FEATURES,
    "market_regime_features": EQUITY_REGIME_FEATURES,
    "flow_normalization_features": EQUITY_FLOW_NORM_FEATURES,
    "iv_surface_features": EQUITY_IV_SURFACE_FEATURES,
    "ticker_base_rates": EQUITY_TICKER_RATES_FEATURES,
    "gex_regime_features": EQUITY_GEX_REGIME_FEATURES,
    "flow_toxicity_features": EQUITY_FLOW_TOXICITY_FEATURES,
    "oi_momentum_features": EQUITY_OI_MOMENTUM_FEATURES,
    "market_tide_context_features": EQUITY_MARKET_TIDE_FEATURES,
    "darkpool_features": EQUITY_DARKPOOL_FEATURES,
    "straddle_momentum_features": EQUITY_STRADDLE_FEATURES,
    "trend_scan_features": EQUITY_TREND_SCAN_FEATURES,
}
ALL_EQUITY_FEATURE_COLUMNS = (
    EQUITY_MOMENTUM_FEATURES
    + EQUITY_VOLATILITY_FEATURES
    + EQUITY_FLOW_FEATURES
    + EQUITY_REGIME_FEATURES
    + EQUITY_FLOW_NORM_FEATURES
    + EQUITY_IV_SURFACE_FEATURES
    + EQUITY_TICKER_RATES_FEATURES
    + EQUITY_GEX_REGIME_FEATURES
    + EQUITY_FLOW_TOXICITY_FEATURES
    + EQUITY_OI_MOMENTUM_FEATURES
    + EQUITY_MARKET_TIDE_FEATURES
    + EQUITY_DARKPOOL_FEATURES
    + EQUITY_STRADDLE_FEATURES
    + EQUITY_TREND_SCAN_FEATURES
)

CATEGORICAL_COLUMNS = [
    "put_call",
    "alert_type",
    "side",
    "aggressor",
    "is_bullish",
    "is_bearish",
    "is_sweep",
    "is_block",
    "is_unusual",
]

# Target definitions - 4 targets for diverse signal dimensions
# Note: quick_winner has bucket-specific thresholds defined in TRADE_BUCKET_CONFIGS
TARGETS = {
    "hit_target_50": """
        CASE WHEN hit_50_pct_ts IS NOT NULL
             AND (hit_stop_20_pct_ts IS NULL OR hit_50_pct_ts < hit_stop_20_pct_ts)
        THEN 1 ELSE 0 END
    """,
    "avoid_stop": """
        CASE WHEN hit_stop_20_pct_ts IS NULL THEN 1 ELSE 0 END
    """,
    "hit_target_100": """
        CASE WHEN hit_100_pct_ts IS NOT NULL
             AND (hit_stop_20_pct_ts IS NULL OR hit_100_pct_ts < hit_stop_20_pct_ts)
        THEN 1 ELSE 0 END
    """,
}

# Trade bucket configurations with bucket-specific lookback windows
TRADE_BUCKET_CONFIGS = {
    "0DTE": {
        "filter": "trade_type = '0DTE'",
        "window_days": 10,
        "min_samples": 50,
        "quick_winner_seconds": 3600,
        "description": "Same-day expiry options",
    },
    "SHORT_SWING": {
        "filter": "trade_type = 'SHORT_SWING'",
        "window_days": 20,
        "min_samples": 50,
        "quick_winner_seconds": 14400,
        "description": "1-3 day expiry options",
    },
    "SWING": {
        "filter": "trade_type = 'SWING'",
        "window_days": 45,
        "min_samples": 30,
        "quick_winner_seconds": 86400,
        "description": "3-14 day expiry options",
    },
    "POSITION": {
        "filter": "trade_type = 'POSITION'",
        "window_days": 90,
        "min_samples": 20,
        "quick_winner_seconds": 259200,
        "description": "14+ day expiry options",
    },
}


def get_quick_winner_target(seconds_threshold: int) -> str:
    """Generate quick_winner target SQL with bucket-specific time threshold."""
    return f"""
        CASE WHEN hit_50_pct_ts IS NOT NULL
             AND time_to_50_pct_seconds IS NOT NULL
             AND time_to_50_pct_seconds < {seconds_threshold}
             AND (hit_stop_20_pct_ts IS NULL OR hit_50_pct_ts < hit_stop_20_pct_ts)
        THEN 1 ELSE 0 END
    """
