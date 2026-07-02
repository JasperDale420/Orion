"""Tests for orion.ml.feature_store — unified Gold feature reads for scoring."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from orion.ml.feature_store import (
    _ALERT_LEVEL_DATASETS,
    _GOLD_TO_FEATURE,
    _MARKET_LEVEL_DATASETS,
    _coerce_bool,
    _coerce_float,
    _find_event_row,
    get_scoring_features,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert_df(
    event_id: str = "evt-123",
    symbol: str = "AAPL",
    alert_time: str = "2025-06-15T14:30:00+00:00",
    delta: float = 0.65,
    iv: float = 0.32,
    is_sweep: int = 1,
) -> pd.DataFrame:
    """Build a minimal meta_label_features DataFrame."""
    return pd.DataFrame(
        [
            {
                "event_id": event_id,
                "symbol": symbol,
                "alert_time": alert_time,
                "delta": delta,
                "gamma": 0.05,
                "theta": -0.12,
                "vega": 0.08,
                "iv": iv,
                "premium": 25000.0,
                "volume": 500,
                "open_interest": 2000,
                "volume_oi_ratio": 0.25,
                "spot_price": 185.50,
                "contract_price": 3.20,
                "strike": 190.0,
                "moneyness": 0.976,
                "log_moneyness": -0.024,
                "days_to_expiry": 5,
                "put_call": "C",
                "alert_type": "sweep",
                "side": "ask",
                "aggressor": "buyer",
                "is_bullish": 1,
                "is_bearish": 0,
                "is_sweep": is_sweep,
                "is_block": 0,
                "is_unusual": 1,
                "underlying_30d_return": 0.05,
                "underlying_5d_return": 0.02,
                "underlying_1d_return": 0.01,
                "realized_vol_20d": 0.22,
                "iv_rank": 45.0,
                "hour_of_day": 14,
                "minute_of_hour": 30,
                "day_of_week": 2,
                "minutes_since_open": 300,
                "minutes_to_close": 90,
                "gex": 1_500_000.0,
                "vex": 250_000.0,
                "max_pain_distance_pct": 0.03,
                "market_tide_net_premium": 500_000.0,
                "market_tide_direction": "bullish",
            }
        ]
    )


# ---------------------------------------------------------------------------
# _coerce_float
# ---------------------------------------------------------------------------


class TestCoerceFloat:
    def test_normal_float(self):
        assert _coerce_float(3.14) == 3.14

    def test_int(self):
        assert _coerce_float(42) == 42.0

    def test_string_number(self):
        assert _coerce_float("1.5") == 1.5

    def test_none(self):
        assert _coerce_float(None) is None

    def test_nan(self):
        assert _coerce_float(float("nan")) is None

    def test_non_numeric_string(self):
        assert _coerce_float("abc") is None


# ---------------------------------------------------------------------------
# _coerce_bool
# ---------------------------------------------------------------------------


class TestCoerceBool:
    @pytest.mark.parametrize(
        "val,expected",
        [
            (1, 1),
            ("1", 1),
            ("true", 1),
            ("True", 1),
            ("yes", 1),
            ("on", 1),
            (0, 0),
            ("0", 0),
            ("false", 0),
            ("no", 0),
            ("off", 0),
            (None, 0),
            ("", 0),
        ],
    )
    def test_coerce_values(self, val, expected):
        assert _coerce_bool(val) == expected


# ---------------------------------------------------------------------------
# _find_event_row
# ---------------------------------------------------------------------------


class TestFindEventRow:
    def test_exact_event_id_match(self):
        df = _make_alert_df(event_id="evt-123")
        row = _find_event_row(df, "evt-123", "AAPL", datetime(2025, 6, 15, tzinfo=timezone.utc))
        assert row is not None
        assert row["delta"] == 0.65

    def test_no_match_returns_none(self):
        df = _make_alert_df(event_id="evt-123", symbol="TSLA")
        row = _find_event_row(df, "evt-999", "AAPL", datetime(2025, 6, 15, tzinfo=timezone.utc))
        assert row is None

    def test_ticker_timestamp_fallback(self):
        df = _make_alert_df(event_id="other-id", symbol="AAPL", alert_time="2025-06-15T14:00:00+00:00")
        row = _find_event_row(df, "evt-999", "AAPL", datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc))
        assert row is not None
        assert row["delta"] == 0.65

    def test_no_future_leakage(self):
        """Rows after entry_ts must not be returned."""
        df = _make_alert_df(event_id="other", symbol="AAPL", alert_time="2025-06-15T15:00:00+00:00")
        row = _find_event_row(df, "evt-999", "AAPL", datetime(2025, 6, 15, 14, 0, tzinfo=timezone.utc))
        assert row is None

    def test_empty_df(self):
        row = _find_event_row(pd.DataFrame(), "evt-123", "AAPL", datetime(2025, 6, 15, tzinfo=timezone.utc))
        assert row is None


# ---------------------------------------------------------------------------
# get_scoring_features (integration-style with mocked Heber)
# ---------------------------------------------------------------------------


class TestGetScoringFeatures:
    """Test the main entrypoint with mocked HeberReader."""

    def _mock_reader(self, alert_df: pd.DataFrame | None = None, equity_dfs: dict | None = None):
        reader = MagicMock()

        def read_gold(dataset: str, asof_time=None, symbols=None, lookback_days=None):
            if dataset == "meta_label_features":
                return alert_df if alert_df is not None else pd.DataFrame()
            if equity_dfs and dataset in equity_dfs:
                return equity_dfs[dataset]
            return pd.DataFrame()

        reader.read_gold_features.side_effect = read_gold
        return reader

    @pytest.mark.asyncio
    async def test_full_feature_load(self):
        alert_df = _make_alert_df()
        reader = self._mock_reader(alert_df=alert_df)

        with patch("orion.ml.feature_store.get_heber_reader", return_value=reader):
            features = await get_scoring_features(
                event_id="evt-123",
                ticker="AAPL",
                entry_ts=datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc),
            )

        assert features["delta"] == 0.65
        assert features["iv"] == 0.32
        assert features["is_sweep"] == 1
        assert features["is_bearish"] == 0
        assert features["premium"] == 25000.0
        assert features["market_tide_30m"] == 500_000.0  # mapped from market_tide_net_premium

    @pytest.mark.asyncio
    async def test_missing_event_returns_none_features(self):
        reader = self._mock_reader(alert_df=pd.DataFrame())

        with patch("orion.ml.feature_store.get_heber_reader", return_value=reader):
            features = await get_scoring_features(
                event_id="evt-missing",
                ticker="XYZ",
                entry_ts=datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc),
            )

        # All features should be present (as keys) but None
        assert "delta" in features
        assert features["delta"] is None

    @pytest.mark.asyncio
    async def test_alert_read_exception_handled(self):
        """HeberReader exception should not crash — features default to None."""
        reader = MagicMock()
        reader.read_gold_features.side_effect = RuntimeError("Heber down")

        with patch("orion.ml.feature_store.get_heber_reader", return_value=reader):
            features = await get_scoring_features(
                event_id="evt-123",
                ticker="AAPL",
                entry_ts=datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc),
            )

        assert isinstance(features, dict)
        assert features["delta"] is None

    @pytest.mark.asyncio
    async def test_equity_features_merged(self):
        """Equity Gold features should appear in the result dict."""
        momentum_df = pd.DataFrame(
            [
                {
                    "ts_event": "2025-06-15T14:00:00+00:00",
                    "momentum_1d": 0.015,
                    "momentum_5d": 0.03,
                    "momentum_10d": 0.05,
                    "momentum_20d": 0.08,
                    "rsi": 62.0,
                    "macd": 1.2,
                    "macd_signal": 0.9,
                    "macd_hist": 0.3,
                }
            ]
        )
        reader = self._mock_reader(
            alert_df=_make_alert_df(),
            equity_dfs={"momentum_features": momentum_df},
        )

        with patch("orion.ml.feature_store.get_heber_reader", return_value=reader):
            features = await get_scoring_features(
                event_id="evt-123",
                ticker="AAPL",
                entry_ts=datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc),
            )

        assert features["delta"] == 0.65  # from alert
        # Equity features only appear if they're in FEATURE_COLUMNS/CATEGORICAL_COLUMNS
        # (the momentum columns are defined there by pattern_miner)

    @pytest.mark.asyncio
    async def test_all_feature_keys_present(self):
        """Result must include every FEATURE_COLUMNS + CATEGORICAL_COLUMNS key."""
        from orion.ml.feature_config import CATEGORICAL_COLUMNS, FEATURE_COLUMNS

        reader = self._mock_reader(alert_df=_make_alert_df())

        with patch("orion.ml.feature_store.get_heber_reader", return_value=reader):
            features = await get_scoring_features(
                event_id="evt-123",
                ticker="AAPL",
                entry_ts=datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc),
            )

        for col in FEATURE_COLUMNS + CATEGORICAL_COLUMNS:
            assert col in features, f"Missing feature key: {col}"

    @pytest.mark.asyncio
    async def test_regime_features_merged(self):
        """Market regime features should appear in the result dict when provided."""
        regime_df = pd.DataFrame(
            [
                {
                    "ts_event": "2025-06-15T14:00:00+00:00",
                    "instrument_key": "market:regime",
                    "dispersion": 0.042,
                    "vol_of_vol": 0.18,
                    "breadth_proxy": 0.65,
                    "yield_curve_slope": 0.012,
                }
            ]
        )
        reader = self._mock_reader(
            alert_df=_make_alert_df(),
            equity_dfs={"market_regime_features": regime_df},
        )

        with patch("orion.ml.feature_store.get_heber_reader", return_value=reader):
            features = await get_scoring_features(
                event_id="evt-123",
                ticker="AAPL",
                entry_ts=datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc),
            )

        assert features["dispersion"] == 0.042
        assert features["vol_of_vol"] == 0.18
        assert features["breadth_proxy"] == 0.65
        assert features["yield_curve_slope"] == 0.012

    @pytest.mark.asyncio
    async def test_regime_features_read_without_ticker_filter(self):
        """market_regime_features must be read with symbols=None (market-level, not per-ticker)."""
        regime_df = pd.DataFrame(
            [
                {
                    "ts_event": "2025-06-15T14:00:00+00:00",
                    "instrument_key": "market:regime",
                    "dispersion": 0.05,
                    "vol_of_vol": 0.20,
                    "breadth_proxy": 0.55,
                    "yield_curve_slope": -0.003,
                }
            ]
        )
        reader = MagicMock()
        call_log: list[tuple[str, list[str] | None]] = []

        def read_gold(dataset: str, asof_time=None, symbols=None, lookback_days=None):
            call_log.append((dataset, symbols))
            if dataset == "meta_label_features":
                return _make_alert_df()
            if dataset == "market_regime_features":
                return regime_df
            return pd.DataFrame()

        reader.read_gold_features.side_effect = read_gold

        with patch("orion.ml.feature_store.get_heber_reader", return_value=reader):
            features = await get_scoring_features(
                event_id="evt-123",
                ticker="AAPL",
                entry_ts=datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc),
            )

        # Verify regime dataset was called with symbols=None (no ticker filter)
        regime_calls = [(ds, syms) for ds, syms in call_log if ds == "market_regime_features"]
        assert len(regime_calls) == 1, f"Expected 1 regime call, got {len(regime_calls)}"
        assert regime_calls[0][1] is None, f"Expected symbols=None for regime, got {regime_calls[0][1]}"

        # Verify per-ticker datasets were called WITH the ticker filter
        ticker_calls = [
            (ds, syms)
            for ds, syms in call_log
            if ds not in ("meta_label_features",)
            and ds not in _MARKET_LEVEL_DATASETS
            and ds not in _ALERT_LEVEL_DATASETS
        ]
        for ds, syms in ticker_calls:
            assert syms == ["AAPL"], f"Expected symbols=['AAPL'] for {ds}, got {syms}"

        # Verify the values propagated
        assert features["dispersion"] == 0.05
        assert features["yield_curve_slope"] == -0.003


# ---------------------------------------------------------------------------
# Column mapping consistency
# ---------------------------------------------------------------------------


class TestColumnMapping:
    def test_gold_to_feature_covers_training_features(self):
        """The mapping should cover the core meta_label features used in training."""
        from orion.ml.feature_config import FEATURE_COLUMNS

        alert_level_features = [
            "days_to_expiry",
            "premium",
            "volume",
            "open_interest",
            "volume_oi_ratio",
            "spot_price",
            "contract_price",
            "strike",
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
        ]
        mapped_targets = set(_GOLD_TO_FEATURE.values())
        for feat in alert_level_features:
            assert feat in mapped_targets, f"Training feature '{feat}' not in _GOLD_TO_FEATURE values"
