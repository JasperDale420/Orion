"""Unit tests for orion.main_feature_enrichment module.

Tests cover pure functions, env var parsing, DataFrame extraction logic,
streak/warning helpers, and async wrappers with mocked dependencies.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# _gateway_fetch_enabled
# ---------------------------------------------------------------------------
class TestGatewayFetchEnabled:
    """Tests for _gateway_fetch_enabled() env var parsing."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "env_value,expected",
        [
            ("1", True),
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("yes", True),
            ("YES", True),
            ("on", True),
            ("ON", True),
            ("y", True),
            ("Y", True),
            ("0", False),
            ("false", False),
            ("no", False),
            ("off", False),
            ("n", False),
            ("", False),
            ("random", False),
        ],
    )
    def test_truthy_and_falsy_values(self, env_value, expected, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", env_value)
        from orion.main_feature_enrichment import _gateway_fetch_enabled

        assert _gateway_fetch_enabled() is expected

    @pytest.mark.unit
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", raising=False)
        from orion.main_feature_enrichment import _gateway_fetch_enabled

        assert _gateway_fetch_enabled() is False

    @pytest.mark.unit
    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", "  true  ")
        from orion.main_feature_enrichment import _gateway_fetch_enabled

        assert _gateway_fetch_enabled() is True


# ---------------------------------------------------------------------------
# _prefer_heber_context_reads
# ---------------------------------------------------------------------------
class TestPreferHeberContextReads:
    """Tests for _prefer_heber_context_reads() config-driven toggling."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "config_value,expected",
        [
            (True, True),
            (False, False),
        ],
    )
    def test_truthy_and_falsy_values(self, config_value, expected, monkeypatch):
        from orion.config import system_settings

        monkeypatch.setattr(system_settings, "feature_enrichment_prefer_heber_context", config_value)
        from orion.main_feature_enrichment import _prefer_heber_context_reads

        assert _prefer_heber_context_reads() is expected

    @pytest.mark.unit
    def test_default_is_true(self):
        from orion.main_feature_enrichment import _prefer_heber_context_reads

        # Default is True (field default in SystemSettings)
        assert _prefer_heber_context_reads() is True


# ---------------------------------------------------------------------------
# _gateway_runtime_contract
# ---------------------------------------------------------------------------
class TestGatewayRuntimeContract:
    """Tests for _gateway_runtime_contract() validation."""

    @pytest.mark.unit
    def test_returns_tuple_with_valid_settings(self):
        mock_settings = MagicMock()
        mock_settings.data_gateway_url = "http://localhost:8080/"
        mock_settings.data_gateway_api_key = "test-key-123"  # pragma: allowlist secret

        with patch("orion.main_feature_enrichment.system_settings", mock_settings):
            from orion.main_feature_enrichment import _gateway_runtime_contract

            url, key = _gateway_runtime_contract()
            assert url == "http://localhost:8080"  # trailing slash stripped
            assert key == "test-key-123"

    @pytest.mark.unit
    def test_raises_when_url_is_empty(self):
        mock_settings = MagicMock()
        mock_settings.data_gateway_url = ""
        mock_settings.data_gateway_api_key = "key"  # pragma: allowlist secret

        with patch("orion.main_feature_enrichment.system_settings", mock_settings):
            from orion.main_feature_enrichment import _gateway_runtime_contract

            with pytest.raises(ValueError, match="DATA_GATEWAY_URL"):
                _gateway_runtime_contract()

    @pytest.mark.unit
    def test_raises_when_url_is_none(self):
        mock_settings = MagicMock()
        mock_settings.data_gateway_url = None
        mock_settings.data_gateway_api_key = "key"  # pragma: allowlist secret

        with patch("orion.main_feature_enrichment.system_settings", mock_settings):
            from orion.main_feature_enrichment import _gateway_runtime_contract

            with pytest.raises(ValueError, match="DATA_GATEWAY_URL"):
                _gateway_runtime_contract()

    @pytest.mark.unit
    def test_raises_when_api_key_is_empty(self):
        mock_settings = MagicMock()
        mock_settings.data_gateway_url = "http://gw:8080"
        mock_settings.data_gateway_api_key = "  "

        with patch("orion.main_feature_enrichment.system_settings", mock_settings):
            from orion.main_feature_enrichment import _gateway_runtime_contract

            with pytest.raises(ValueError, match="DATA_GATEWAY_API_KEY"):
                _gateway_runtime_contract()

    @pytest.mark.unit
    def test_raises_when_api_key_is_none(self):
        mock_settings = MagicMock()
        mock_settings.data_gateway_url = "http://gw:8080"
        mock_settings.data_gateway_api_key = None

        with patch("orion.main_feature_enrichment.system_settings", mock_settings):
            from orion.main_feature_enrichment import _gateway_runtime_contract

            with pytest.raises(ValueError, match="DATA_GATEWAY_API_KEY"):
                _gateway_runtime_contract()


# ---------------------------------------------------------------------------
# _extract_top_tickers_from_flow_df
# ---------------------------------------------------------------------------
class TestExtractTopTickersFromFlowDf:
    """Tests for _extract_top_tickers_from_flow_df()."""

    @pytest.mark.unit
    def test_empty_dataframe_returns_empty_list(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame()
        assert _extract_top_tickers_from_flow_df(df, limit=10) == []

    @pytest.mark.unit
    def test_missing_ticker_column_returns_empty_list(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame({"price": [100, 200], "volume": [1000, 2000]})
        assert _extract_top_tickers_from_flow_df(df, limit=10) == []

    @pytest.mark.unit
    def test_extracts_tickers_from_ticker_column(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame(
            {
                "ticker": ["AAPL", "AAPL", "NVDA", "TSLA", "NVDA", "NVDA"],
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=2)
        assert result == ["NVDA", "AAPL"]  # sorted by count desc

    @pytest.mark.unit
    def test_extracts_from_symbol_column(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame(
            {
                "symbol": ["MSFT", "MSFT", "GOOG"],
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=5)
        assert "MSFT" in result
        assert "GOOG" in result

    @pytest.mark.unit
    def test_extracts_from_underlying_column(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame(
            {
                "underlying": ["AMD", "AMD"],
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=5)
        assert result == ["AMD"]

    @pytest.mark.unit
    def test_respects_limit(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame(
            {
                "ticker": ["A", "B", "C", "D", "E"] * 3,
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=2)
        assert len(result) == 2

    @pytest.mark.unit
    def test_uppercases_tickers(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame(
            {
                "ticker": ["aapl", "Aapl", "AAPL"],
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=5)
        assert result == ["AAPL"]

    @pytest.mark.unit
    def test_drops_na_and_empty_tickers(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame(
            {
                "ticker": [None, "", "  ", "NVDA", "NVDA"],
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=5)
        assert result == ["NVDA"]

    @pytest.mark.unit
    def test_filters_by_timestamp_when_present(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        now = pd.Timestamp.now(tz="UTC")
        recent = now - pd.Timedelta(hours=6)
        old = now - pd.Timedelta(days=5)

        df = pd.DataFrame(
            {
                "ticker": ["RECENT", "RECENT", "OLD", "OLD", "OLD"],
                "flow_ts_utc": [recent, recent, old, old, old],
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=5)
        # OLD should be filtered out (>1 day ago)
        assert result == ["RECENT"]

    @pytest.mark.unit
    def test_no_timestamp_column_skips_time_filter(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        df = pd.DataFrame(
            {
                "ticker": ["AAPL", "AAPL", "TSLA"],
            }
        )
        # No ts column -- should still work
        result = _extract_top_tickers_from_flow_df(df, limit=5)
        assert len(result) == 2
        assert result[0] == "AAPL"

    @pytest.mark.unit
    def test_all_tickers_filtered_returns_empty_list(self):
        from orion.main_feature_enrichment import _extract_top_tickers_from_flow_df

        old = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=10)
        df = pd.DataFrame(
            {
                "ticker": ["STALE", "STALE"],
                "flow_ts_utc": [old, old],
            }
        )
        result = _extract_top_tickers_from_flow_df(df, limit=5)
        assert result == []


# ---------------------------------------------------------------------------
# _map_vix_proxy_to_regime
# ---------------------------------------------------------------------------
class TestMapVixProxyToRegime:
    """Tests for _map_vix_proxy_to_regime()."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "vix,expected",
        [
            (35.0, "EXTREME"),
            (30.1, "EXTREME"),
            (25.0, "ELEVATED"),
            (20.1, "ELEVATED"),
            (15.0, "NORMAL"),
            (12.1, "NORMAL"),
            (12.0, "LOW"),
            (10.0, "LOW"),
            (0.0, "LOW"),
        ],
    )
    def test_regime_boundaries(self, vix, expected):
        from orion.main_feature_enrichment import _map_vix_proxy_to_regime

        assert _map_vix_proxy_to_regime(vix) == expected


# ---------------------------------------------------------------------------
# _zero_write_warn_streak_threshold
# ---------------------------------------------------------------------------
class TestZeroWriteWarnStreakThreshold:
    """Tests for _zero_write_warn_streak_threshold() env var parsing."""

    @pytest.mark.unit
    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK", raising=False)
        from orion.main_feature_enrichment import (
            DEFAULT_ZERO_WRITE_WARN_STREAK,
            _zero_write_warn_streak_threshold,
        )

        assert _zero_write_warn_streak_threshold() == DEFAULT_ZERO_WRITE_WARN_STREAK

    @pytest.mark.unit
    def test_valid_integer(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK", "5")
        from orion.main_feature_enrichment import _zero_write_warn_streak_threshold

        assert _zero_write_warn_streak_threshold() == 5

    @pytest.mark.unit
    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK", "abc")
        from orion.main_feature_enrichment import (
            DEFAULT_ZERO_WRITE_WARN_STREAK,
            _zero_write_warn_streak_threshold,
        )

        assert _zero_write_warn_streak_threshold() == DEFAULT_ZERO_WRITE_WARN_STREAK

    @pytest.mark.unit
    def test_zero_value_returns_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK", "0")
        from orion.main_feature_enrichment import (
            DEFAULT_ZERO_WRITE_WARN_STREAK,
            _zero_write_warn_streak_threshold,
        )

        assert _zero_write_warn_streak_threshold() == DEFAULT_ZERO_WRITE_WARN_STREAK

    @pytest.mark.unit
    def test_negative_value_returns_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK", "-1")
        from orion.main_feature_enrichment import (
            DEFAULT_ZERO_WRITE_WARN_STREAK,
            _zero_write_warn_streak_threshold,
        )

        assert _zero_write_warn_streak_threshold() == DEFAULT_ZERO_WRITE_WARN_STREAK


# ---------------------------------------------------------------------------
# _loop_sleep_seconds
# ---------------------------------------------------------------------------
class TestLoopSleepSeconds:
    """Tests for _loop_sleep_seconds() env var parsing."""

    @pytest.mark.unit
    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS", raising=False)
        from orion.main_feature_enrichment import (
            DEFAULT_LOOP_SLEEP_SECONDS,
            _loop_sleep_seconds,
        )

        assert _loop_sleep_seconds() == DEFAULT_LOOP_SLEEP_SECONDS

    @pytest.mark.unit
    def test_valid_float(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS", "10.5")
        from orion.main_feature_enrichment import _loop_sleep_seconds

        assert _loop_sleep_seconds() == 10.5

    @pytest.mark.unit
    def test_zero_returns_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS", "0")
        from orion.main_feature_enrichment import (
            DEFAULT_LOOP_SLEEP_SECONDS,
            _loop_sleep_seconds,
        )

        assert _loop_sleep_seconds() == DEFAULT_LOOP_SLEEP_SECONDS

    @pytest.mark.unit
    def test_negative_returns_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS", "-5")
        from orion.main_feature_enrichment import (
            DEFAULT_LOOP_SLEEP_SECONDS,
            _loop_sleep_seconds,
        )

        assert _loop_sleep_seconds() == DEFAULT_LOOP_SLEEP_SECONDS

    @pytest.mark.unit
    def test_non_numeric_returns_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS", "bad")
        from orion.main_feature_enrichment import (
            DEFAULT_LOOP_SLEEP_SECONDS,
            _loop_sleep_seconds,
        )

        assert _loop_sleep_seconds() == DEFAULT_LOOP_SLEEP_SECONDS


# ---------------------------------------------------------------------------
# _loop_error_warn_streak_threshold
# ---------------------------------------------------------------------------
class TestLoopErrorWarnStreakThreshold:
    """Tests for _loop_error_warn_streak_threshold() env var parsing."""

    @pytest.mark.unit
    def test_valid_integer(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_LOOP_ERROR_WARN_STREAK", "7")
        from orion.main_feature_enrichment import _loop_error_warn_streak_threshold

        assert _loop_error_warn_streak_threshold() == 7

    @pytest.mark.unit
    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_LOOP_ERROR_WARN_STREAK", "xyz")
        from orion.main_feature_enrichment import (
            DEFAULT_LOOP_ERROR_WARN_STREAK,
            _loop_error_warn_streak_threshold,
        )

        assert _loop_error_warn_streak_threshold() == DEFAULT_LOOP_ERROR_WARN_STREAK


# ---------------------------------------------------------------------------
# _non_heber_warn_streak_threshold
# ---------------------------------------------------------------------------
class TestNonHeberWarnStreakThreshold:
    """Tests for _non_heber_warn_streak_threshold() env var parsing."""

    @pytest.mark.unit
    def test_valid_integer(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK", "10")
        from orion.main_feature_enrichment import _non_heber_warn_streak_threshold

        assert _non_heber_warn_streak_threshold() == 10

    @pytest.mark.unit
    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK", "abc")
        from orion.main_feature_enrichment import (
            DEFAULT_NON_HEBER_WARN_STREAK,
            _non_heber_warn_streak_threshold,
        )

        assert _non_heber_warn_streak_threshold() == DEFAULT_NON_HEBER_WARN_STREAK

    @pytest.mark.unit
    def test_zero_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK", "0")
        from orion.main_feature_enrichment import (
            DEFAULT_NON_HEBER_WARN_STREAK,
            _non_heber_warn_streak_threshold,
        )

        assert _non_heber_warn_streak_threshold() == DEFAULT_NON_HEBER_WARN_STREAK


# ---------------------------------------------------------------------------
# _note_loop_error
# ---------------------------------------------------------------------------
class TestNoteLoopError:
    """Tests for _note_loop_error() streak tracking."""

    @pytest.mark.unit
    def test_increments_streak(self):
        from orion.main_feature_enrichment import _note_loop_error

        result = _note_loop_error(consecutive_error_streak=2, warn_streak=5, error=RuntimeError("boom"))
        assert result == 3

    @pytest.mark.unit
    def test_returns_one_from_zero(self):
        from orion.main_feature_enrichment import _note_loop_error

        result = _note_loop_error(consecutive_error_streak=0, warn_streak=5, error=RuntimeError("boom"))
        assert result == 1

    @pytest.mark.unit
    def test_warns_at_threshold(self):
        from orion.main_feature_enrichment import _note_loop_error

        # Should log a warning when streak >= warn_streak
        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            _note_loop_error(consecutive_error_streak=4, warn_streak=5, error=RuntimeError("boom"))
            mock_logger.warning.assert_called_once()

    @pytest.mark.unit
    def test_no_warn_below_threshold(self):
        from orion.main_feature_enrichment import _note_loop_error

        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            _note_loop_error(consecutive_error_streak=1, warn_streak=5, error=RuntimeError("boom"))
            mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# _note_ticker_source_streak
# ---------------------------------------------------------------------------
class TestNoteTickerSourceStreak:
    """Tests for _note_ticker_source_streak()."""

    @pytest.mark.unit
    def test_heber_source_resets_streak(self):
        from orion.main_feature_enrichment import _note_ticker_source_streak

        result = _note_ticker_source_streak(source="heber", non_heber_streak=5, warn_streak=3, tickers_count=10)
        assert result == 0

    @pytest.mark.unit
    def test_non_heber_increments_streak(self):
        from orion.main_feature_enrichment import _note_ticker_source_streak

        result = _note_ticker_source_streak(
            source="static_fallback", non_heber_streak=2, warn_streak=5, tickers_count=10
        )
        assert result == 3

    @pytest.mark.unit
    def test_warns_at_threshold(self):
        from orion.main_feature_enrichment import _note_ticker_source_streak

        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            _note_ticker_source_streak(source="static_fallback", non_heber_streak=4, warn_streak=5, tickers_count=10)
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _note_fetch_count
# ---------------------------------------------------------------------------
class TestNoteFetchCount:
    """Tests for _note_fetch_count() zero-write streak tracking."""

    @pytest.mark.unit
    def test_positive_count_resets_streak(self):
        from orion.main_feature_enrichment import _note_fetch_count

        streaks: dict[str, int] = {"market_tide": 5}
        _note_fetch_count("market_tide", count=3, zero_write_streaks=streaks, warn_streak=3)
        assert streaks["market_tide"] == 0

    @pytest.mark.unit
    def test_zero_count_increments_streak(self):
        from orion.main_feature_enrichment import _note_fetch_count

        streaks: dict[str, int] = {"greek_exposure": 1}
        _note_fetch_count("greek_exposure", count=0, zero_write_streaks=streaks, warn_streak=5)
        assert streaks["greek_exposure"] == 2

    @pytest.mark.unit
    def test_zero_count_creates_new_streak(self):
        from orion.main_feature_enrichment import _note_fetch_count

        streaks: dict[str, int] = {}
        _note_fetch_count("iv_rank", count=0, zero_write_streaks=streaks, warn_streak=3)
        assert streaks["iv_rank"] == 1

    @pytest.mark.unit
    def test_warns_at_threshold(self):
        from orion.main_feature_enrichment import _note_fetch_count

        streaks: dict[str, int] = {"market_tide": 2}
        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            _note_fetch_count("market_tide", count=0, zero_write_streaks=streaks, warn_streak=3)
            mock_logger.warning.assert_called_once()

    @pytest.mark.unit
    def test_no_warn_below_threshold(self):
        from orion.main_feature_enrichment import _note_fetch_count

        streaks: dict[str, int] = {"market_tide": 0}
        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            _note_fetch_count("market_tide", count=0, zero_write_streaks=streaks, warn_streak=5)
            mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# _log_ticker_source_transition
# ---------------------------------------------------------------------------
class TestLogTickerSourceTransition:
    """Tests for _log_ticker_source_transition()."""

    @pytest.mark.unit
    def test_same_source_returns_source_no_log(self):
        from orion.main_feature_enrichment import _log_ticker_source_transition

        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            result = _log_ticker_source_transition("heber", "heber", 10)
            assert result == "heber"
            mock_logger.info.assert_not_called()
            mock_logger.warning.assert_not_called()

    @pytest.mark.unit
    def test_transition_to_heber_logs_info(self):
        from orion.main_feature_enrichment import _log_ticker_source_transition

        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            result = _log_ticker_source_transition("heber", "static_fallback", 10)
            assert result == "heber"
            mock_logger.info.assert_called_once()

    @pytest.mark.unit
    def test_transition_away_from_heber_logs_warning(self):
        from orion.main_feature_enrichment import _log_ticker_source_transition

        with patch("orion.main_feature_enrichment.logger") as mock_logger:
            result = _log_ticker_source_transition("static_fallback", "heber", 10)
            assert result == "static_fallback"
            mock_logger.warning.assert_called_once()

    @pytest.mark.unit
    def test_transition_from_none(self):
        from orion.main_feature_enrichment import _log_ticker_source_transition

        # First time source is set -- previous is None, so it's a transition
        result = _log_ticker_source_transition("heber", None, 5)
        assert result == "heber"


# ---------------------------------------------------------------------------
# _coerce_time_series
# ---------------------------------------------------------------------------
class TestCoerceTimeSeries:
    """Tests for _coerce_time_series()."""

    @pytest.mark.unit
    def test_coerces_ts_event_column(self):
        from orion.main_feature_enrichment import _coerce_time_series

        now_str = "2025-03-10T12:00:00Z"
        df = pd.DataFrame({"ts_event": [now_str], "value": [42]})
        result = _coerce_time_series(df)
        assert pd.notna(result.iloc[0])

    @pytest.mark.unit
    def test_coerces_timestamp_column(self):
        from orion.main_feature_enrichment import _coerce_time_series

        df = pd.DataFrame({"timestamp": ["2025-01-01T00:00:00Z"]})
        result = _coerce_time_series(df)
        assert pd.notna(result.iloc[0])

    @pytest.mark.unit
    def test_no_known_column_returns_nat(self):
        from orion.main_feature_enrichment import _coerce_time_series

        df = pd.DataFrame({"foo": [1, 2, 3]})
        result = _coerce_time_series(df)
        assert result.isna().all()

    @pytest.mark.unit
    def test_invalid_dates_become_nat(self):
        from orion.main_feature_enrichment import _coerce_time_series

        df = pd.DataFrame({"ts_event": ["not-a-date", "also-bad"]})
        result = _coerce_time_series(df)
        assert result.isna().all()


# ---------------------------------------------------------------------------
# get_active_tickers_with_source (async)
# ---------------------------------------------------------------------------
class TestGetActiveTickersWithSource:
    """Tests for get_active_tickers_with_source() async function."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_heber_tickers_when_available(self):
        from orion.main_feature_enrichment import get_active_tickers_with_source

        now = pd.Timestamp.now(tz="UTC")
        mock_flow_df = pd.DataFrame(
            {
                "ticker": ["AAPL", "AAPL", "NVDA"],
                "flow_ts_utc": [now, now, now],
            }
        )

        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_flow.return_value = mock_flow_df
            tickers, source = await get_active_tickers_with_source(limit=5)

        assert source == "heber"
        assert "AAPL" in tickers

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_falls_back_to_static_on_heber_failure(self):
        from orion.main_feature_enrichment import STATIC_TICKER_FALLBACK, get_active_tickers_with_source

        with (
            patch("orion.enrichment.heber_context._heber_reader") as mock_reader,
            patch("orion.enrichment.heber_context._extract_tickers_from_bars", return_value=[]),
        ):
            mock_reader.read_flow.side_effect = RuntimeError("Heber down")
            tickers, source = await get_active_tickers_with_source(limit=5)

        assert source == "static_fallback"
        assert tickers == STATIC_TICKER_FALLBACK[:5]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_falls_back_when_flow_df_empty(self):
        from orion.main_feature_enrichment import STATIC_TICKER_FALLBACK, get_active_tickers_with_source

        with (
            patch("orion.enrichment.heber_context._heber_reader") as mock_reader,
            patch("orion.enrichment.heber_context._extract_tickers_from_bars", return_value=[]),
        ):
            mock_reader.read_flow.return_value = pd.DataFrame()
            tickers, source = await get_active_tickers_with_source(limit=3)

        assert source == "static_fallback"
        assert len(tickers) == 3
        assert tickers == STATIC_TICKER_FALLBACK[:3]


# ---------------------------------------------------------------------------
# get_active_tickers (async)
# ---------------------------------------------------------------------------
class TestGetActiveTickers:
    """Tests for get_active_tickers() convenience wrapper."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_ticker_list_only(self):
        from orion.main_feature_enrichment import get_active_tickers

        with patch("orion.enrichment.heber_context.get_active_tickers_with_source", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = (["SPY", "QQQ"], "heber")
            tickers = await get_active_tickers(limit=10)

        assert tickers == ["SPY", "QQQ"]


# ---------------------------------------------------------------------------
# get_latest_vix_data (async)
# ---------------------------------------------------------------------------
class TestGetLatestVixData:
    """Tests for get_latest_vix_data() async function."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_heber_data_when_available(self):
        from orion.main_feature_enrichment import get_latest_vix_data

        vix_data = {"vix": 18.5, "vvix": None, "vix_1d_change": -1.2, "vix_regime": "NORMAL"}

        with (
            patch("orion.enrichment.heber_context._prefer_heber_context_reads", return_value=True),
            patch("orion.enrichment.heber_context._get_latest_vix_data_from_heber", return_value=vix_data),
        ):
            result = await get_latest_vix_data()

        assert result["vix"] == 18.5
        assert result["vix_regime"] == "NORMAL"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_heber_unavailable(self):
        from orion.main_feature_enrichment import get_latest_vix_data

        with (
            patch("orion.enrichment.heber_context._prefer_heber_context_reads", return_value=True),
            patch("orion.enrichment.heber_context._get_latest_vix_data_from_heber", return_value=None),
        ):
            result = await get_latest_vix_data()

        assert result == {}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_heber_not_preferred(self):
        from orion.main_feature_enrichment import get_latest_vix_data

        with patch("orion.enrichment.heber_context._prefer_heber_context_reads", return_value=False):
            result = await get_latest_vix_data()

        assert result == {}


# ---------------------------------------------------------------------------
# get_latest_market_tide (async)
# ---------------------------------------------------------------------------
class TestGetLatestMarketTide:
    """Tests for get_latest_market_tide() async function."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_heber_value(self):
        from orion.main_feature_enrichment import get_latest_market_tide

        with (
            patch("orion.enrichment.heber_context._prefer_heber_context_reads", return_value=True),
            patch("orion.enrichment.heber_context._get_latest_market_tide_from_heber", return_value=12345.67),
        ):
            result = await get_latest_market_tide()

        assert result == 12345.67

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_none_when_not_preferred(self):
        from orion.main_feature_enrichment import get_latest_market_tide

        with patch("orion.enrichment.heber_context._prefer_heber_context_reads", return_value=False):
            result = await get_latest_market_tide()

        assert result is None


# ---------------------------------------------------------------------------
# get_spy_cumulative_return (async)
# ---------------------------------------------------------------------------
class TestGetSpyCumulativeReturn:
    """Tests for get_spy_cumulative_return() async function."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_heber_value(self):
        from orion.main_feature_enrichment import get_spy_cumulative_return

        with (
            patch("orion.enrichment.heber_context._prefer_heber_context_reads", return_value=True),
            patch("orion.enrichment.heber_context._get_spy_cumulative_return_from_heber", return_value=0.025),
        ):
            result = await get_spy_cumulative_return()

        assert result == 0.025

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_zero_when_heber_unavailable(self):
        from orion.main_feature_enrichment import get_spy_cumulative_return

        with (
            patch("orion.enrichment.heber_context._prefer_heber_context_reads", return_value=True),
            patch("orion.enrichment.heber_context._get_spy_cumulative_return_from_heber", return_value=None),
        ):
            result = await get_spy_cumulative_return()

        assert result == 0.0


# ---------------------------------------------------------------------------
# persist_regime_snapshot (async)
# ---------------------------------------------------------------------------
class TestPersistRegimeSnapshot:
    """Tests for persist_regime_snapshot() in-memory persistence."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_appends_snapshot_to_in_memory_list(self):
        from orion.enrichment.heber_context import _recent_regime_snapshots
        from orion.main_feature_enrichment import persist_regime_snapshot

        # Clear any existing state
        _recent_regime_snapshots.clear()

        mock_snapshot = MagicMock()
        mock_snapshot.trend.value = "BULLISH"
        mock_snapshot.vol.value = "LOW"
        mock_snapshot.risk.value = "LOW"
        mock_snapshot.session.value = "REGULAR"
        mock_snapshot.vix_regime.value = "NORMAL"
        mock_snapshot.vix_level = 15.0
        mock_snapshot.realized_vol = 0.012
        mock_snapshot.trend_strength = 0.8
        mock_snapshot.risk_score = 0.2
        mock_snapshot.confidence = {"trend": 0.9}

        ts = datetime.now(UTC)
        await persist_regime_snapshot(ts, mock_snapshot, ticker="QQQ")

        assert len(_recent_regime_snapshots) == 1
        record = _recent_regime_snapshots[0]
        assert record["ticker"] == "QQQ"
        assert record["trend_regime"] == "BULLISH"
        assert record["vix_level"] == 15.0

        _recent_regime_snapshots.clear()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_trims_list_beyond_2000(self):
        from orion.enrichment.heber_context import _recent_regime_snapshots
        from orion.main_feature_enrichment import persist_regime_snapshot

        _recent_regime_snapshots.clear()

        mock_snapshot = MagicMock()
        mock_snapshot.trend.value = "BEARISH"
        mock_snapshot.vol.value = "HIGH"
        mock_snapshot.risk.value = "HIGH"
        mock_snapshot.session.value = "CLOSED"
        mock_snapshot.vix_regime.value = "EXTREME"
        mock_snapshot.vix_level = 40.0
        mock_snapshot.realized_vol = 0.05
        mock_snapshot.trend_strength = 0.3
        mock_snapshot.risk_score = 0.9
        mock_snapshot.confidence = None

        # Fill to 2000
        for _ in range(2000):
            _recent_regime_snapshots.append({"dummy": True})

        # Adding one more should trim
        ts = datetime.now(UTC)
        await persist_regime_snapshot(ts, mock_snapshot)

        assert len(_recent_regime_snapshots) <= 1001  # 2001 -> del [:1000] = 1001

        _recent_regime_snapshots.clear()


# ---------------------------------------------------------------------------
# _get_latest_market_tide_from_heber
# ---------------------------------------------------------------------------
class TestGetLatestMarketTideFromHeber:
    """Tests for _get_latest_market_tide_from_heber()."""

    @pytest.mark.unit
    def test_returns_none_on_exception(self):
        from orion.main_feature_enrichment import _get_latest_market_tide_from_heber

        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_market_tide.side_effect = RuntimeError("fail")
            result = _get_latest_market_tide_from_heber()
            assert result is None

    @pytest.mark.unit
    def test_returns_none_on_empty_df(self):
        from orion.main_feature_enrichment import _get_latest_market_tide_from_heber

        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_market_tide.return_value = pd.DataFrame()
            result = _get_latest_market_tide_from_heber()
            assert result is None

    @pytest.mark.unit
    def test_returns_net_premium_value(self):
        from orion.main_feature_enrichment import _get_latest_market_tide_from_heber

        now = pd.Timestamp.now(tz="UTC")
        df = pd.DataFrame(
            {
                "net_premium": [50000.0],
                "ts_event": [now.isoformat()],
            }
        )
        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_market_tide.return_value = df
            result = _get_latest_market_tide_from_heber()
            assert result == 50000.0

    @pytest.mark.unit
    def test_computes_from_call_put_premium(self):
        from orion.main_feature_enrichment import _get_latest_market_tide_from_heber

        now = pd.Timestamp.now(tz="UTC")
        df = pd.DataFrame(
            {
                "net_call_premium": [100000.0],
                "net_put_premium": [30000.0],
                "ts_event": [now.isoformat()],
            }
        )
        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_market_tide.return_value = df
            result = _get_latest_market_tide_from_heber()
            assert result == 70000.0


# ---------------------------------------------------------------------------
# _get_spy_cumulative_return_from_heber
# ---------------------------------------------------------------------------
class TestGetSpyCumulativeReturnFromHeber:
    """Tests for _get_spy_cumulative_return_from_heber()."""

    @pytest.mark.unit
    def test_returns_none_on_exception(self):
        from orion.main_feature_enrichment import _get_spy_cumulative_return_from_heber

        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_bars.side_effect = RuntimeError("fail")
            result = _get_spy_cumulative_return_from_heber()
            assert result is None

    @pytest.mark.unit
    def test_returns_none_on_empty_df(self):
        from orion.main_feature_enrichment import _get_spy_cumulative_return_from_heber

        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_bars.return_value = pd.DataFrame()
            result = _get_spy_cumulative_return_from_heber()
            assert result is None

    @pytest.mark.unit
    def test_returns_zero_for_single_bar(self):
        from orion.main_feature_enrichment import _get_spy_cumulative_return_from_heber

        now = pd.Timestamp.now(tz="UTC")
        df = pd.DataFrame(
            {
                "symbol": ["SPY"],
                "close": [500.0],
                "ts_event": [now.isoformat()],
            }
        )
        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_bars.return_value = df
            result = _get_spy_cumulative_return_from_heber()
            assert result == 0.0

    @pytest.mark.unit
    def test_computes_cumulative_return(self):
        from orion.main_feature_enrichment import _get_spy_cumulative_return_from_heber

        base = pd.Timestamp.now(tz="UTC")
        df = pd.DataFrame(
            {
                "symbol": ["SPY", "SPY"],
                "close": [510.0, 500.0],
                "ts_event": [(base - pd.Timedelta(hours=1)).isoformat(), (base - pd.Timedelta(hours=2)).isoformat()],
            }
        )
        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_bars.return_value = df
            result = _get_spy_cumulative_return_from_heber()
            # latest=510 / oldest=500 -> (510-500)/500 = 0.02
            assert result is not None
            assert abs(result - 0.02) < 1e-9

    @pytest.mark.unit
    def test_returns_zero_for_zero_oldest_close(self):
        from orion.main_feature_enrichment import _get_spy_cumulative_return_from_heber

        base = pd.Timestamp.now(tz="UTC")
        df = pd.DataFrame(
            {
                "symbol": ["SPY", "SPY"],
                "close": [510.0, 0.0],
                "ts_event": [(base - pd.Timedelta(hours=1)).isoformat(), (base - pd.Timedelta(hours=2)).isoformat()],
            }
        )
        with patch("orion.enrichment.heber_context._heber_reader") as mock_reader:
            mock_reader.read_bars.return_value = df
            result = _get_spy_cumulative_return_from_heber()
            assert result == 0.0


# ---------------------------------------------------------------------------
# run_feature_loop (single-iteration smoke test)
# ---------------------------------------------------------------------------
class TestRunFeatureLoop:
    """Smoke test for run_feature_loop() with mocked deps -- exercises one iteration."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_single_iteration_then_shutdown(self):
        from orion.main_feature_enrichment import run_feature_loop

        shutdown_event = asyncio.Event()

        # Set up patches to skip all real work
        with (
            patch("orion.main_feature_enrichment._gateway_fetch_enabled", return_value=False),
            patch("orion.main_feature_enrichment._zero_write_warn_streak_threshold", return_value=3),
            patch("orion.main_feature_enrichment._loop_sleep_seconds", return_value=0.01),
            patch("orion.main_feature_enrichment._loop_error_warn_streak_threshold", return_value=3),
            patch("orion.main_feature_enrichment._non_heber_warn_streak_threshold", return_value=3),
            patch("orion.main_feature_enrichment.init_db", new_callable=AsyncMock),
            patch(
                "orion.main_feature_enrichment.get_active_tickers_with_source",
                new_callable=AsyncMock,
                return_value=(["SPY"], "static_fallback"),
            ),
            patch("orion.main_feature_enrichment.VIXProxyConnector") as mock_vix_cls,
            patch("orion.main_feature_enrichment.MultiAxisRegimeDetector") as mock_regime_cls,
            patch("orion.main_feature_enrichment.get_latest_vix_data", new_callable=AsyncMock, return_value={}),
            patch("orion.main_feature_enrichment.get_latest_market_tide", new_callable=AsyncMock, return_value=None),
            patch("orion.main_feature_enrichment.get_spy_cumulative_return", new_callable=AsyncMock, return_value=0.0),
            patch("orion.main_feature_enrichment.persist_regime_snapshot", new_callable=AsyncMock),
        ):
            mock_vix = MagicMock()
            mock_vix.fetch_and_store = AsyncMock(return_value=0)
            mock_vix_cls.return_value = mock_vix

            mock_regime = MagicMock()
            mock_snapshot = MagicMock()
            mock_snapshot.trend.value = "NEUTRAL"
            mock_snapshot.vol.value = "NORMAL"
            mock_snapshot.risk.value = "LOW"
            mock_snapshot.session.value = "REGULAR"
            mock_snapshot.vix_regime.value = "NORMAL"
            mock_regime.detect.return_value = mock_snapshot
            mock_regime_cls.return_value = mock_regime

            # Signal shutdown after a very short delay so it exits
            async def set_shutdown():
                await asyncio.sleep(0.05)
                shutdown_event.set()

            task = asyncio.create_task(set_shutdown())
            await run_feature_loop(shutdown_event)
            await task  # ensure clean teardown
