"""
UW Feature Enrichment Service.

Periodically fetches GEX, Market Tide, Max Pain, IV Rank for tracked tickers.
Runs as a background service to populate feature tables for ML.
"""

import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import Any, List

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from orion.analysis.regime import MultiAxisRegimeDetector
from orion.clients.heber_reader import HeberReader
from orion.config import system_settings
from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector
from orion.connectors.uw_iv_rank_connector import UWIVRankConnector
from orion.connectors.uw_market_tide_connector import UWMarketTideConnector
from orion.connectors.uw_max_pain_connector import UWMaxPainConnector
from orion.connectors.vix_proxy_connector import VIXProxyConnector
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.feature_enrichment")

# Poll intervals
MARKET_TIDE_INTERVAL = 300  # Every 5 minutes (reduced from 60s to save API calls)
GREEK_EXPOSURE_INTERVAL = 300  # Every 5 minutes
MAX_PAIN_INTERVAL = 3600  # Every hour
IV_RANK_INTERVAL = 900  # Every 15 minutes
REGIME_SNAPSHOT_INTERVAL = 300  # Every 5 minutes
VIX_DATA_INTERVAL = 3600  # Every hour (VIX is daily-level data)
DEFAULT_ZERO_WRITE_WARN_STREAK = 3
DEFAULT_LOOP_SLEEP_SECONDS = 30.0
DEFAULT_LOOP_ERROR_WARN_STREAK = 3
DEFAULT_NON_HEBER_WARN_STREAK = 3
STATIC_TICKER_FALLBACK = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMD", "META", "AMZN", "GOOG", "MSFT"]
_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}

_heber_reader = HeberReader()
_recent_regime_snapshots: list[dict[str, Any]] = []


def _gateway_runtime_contract() -> tuple[str, str]:
    gateway_url = (system_settings.data_gateway_url or "").strip()
    if not gateway_url:
        raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")

    gateway_api_key = (system_settings.data_gateway_api_key or "").strip()
    if not gateway_api_key:
        raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")

    return gateway_url.rstrip("/"), gateway_api_key


def _extract_top_tickers_from_flow_df(flow_df: pd.DataFrame, limit: int) -> List[str]:
    if flow_df.empty:
        return []

    ticker_col = None
    for candidate in ("ticker", "symbol", "underlying"):
        if candidate in flow_df.columns:
            ticker_col = candidate
            break

    if ticker_col is None:
        return []

    ts_col = None
    for candidate in ("flow_ts_utc", "ts_event", "timestamp", "created_at"):
        if candidate in flow_df.columns:
            ts_col = candidate
            break

    if ts_col is not None:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)
        ts = pd.to_datetime(flow_df[ts_col], utc=True, errors="coerce")
        flow_df = flow_df.loc[ts >= cutoff]

    tickers = flow_df[ticker_col].dropna().astype(str).str.upper().str.strip().replace("", pd.NA).dropna()
    if tickers.empty:
        return []

    counts = tickers.value_counts()
    return counts.head(limit).index.tolist()


def _prefer_heber_context_reads() -> bool:
    raw = os.getenv("ORION_FEATURE_ENRICHMENT_PREFER_HEBER_CONTEXT", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _first_existing_column(df: pd.DataFrame, candidates: List[str]) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def _coerce_time_series(df: pd.DataFrame) -> pd.Series:
    ts_col = _first_existing_column(df, ["ts_event", "ts_utc", "bar_start_ts", "bar_start_ts_utc", "timestamp"])
    if ts_col is None:
        return pd.Series(index=df.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(df[ts_col], utc=True, errors="coerce")


def _get_latest_market_tide_from_heber() -> float | None:
    try:
        now = datetime.now(timezone.utc)
        tide_df = _heber_reader.read_market_tide(
            asof_time=now,
            start_time=now - pd.Timedelta(days=2),
        )
    except Exception:
        logger.debug("Heber market tide read failed, falling back to local DB", exc_info=True)
        return None

    if tide_df.empty:
        return None

    tide_df = tide_df.copy()
    tide_df["_ts"] = _coerce_time_series(tide_df)
    if tide_df["_ts"].notna().any():
        tide_df = tide_df.dropna(subset=["_ts"]).sort_values("_ts", ascending=False)
    if tide_df.empty:
        return None

    latest = tide_df.iloc[0]
    net_col = _first_existing_column(tide_df, ["net_premium", "market_tide_net"])
    if net_col is not None:
        value = pd.to_numeric(pd.Series([latest.get(net_col)]), errors="coerce").iloc[0]
        if pd.notna(value):
            return float(value)

    call_col = _first_existing_column(tide_df, ["net_call_premium", "call_premium"])
    put_col = _first_existing_column(tide_df, ["net_put_premium", "put_premium"])
    if call_col is None or put_col is None:
        return None

    call_value = pd.to_numeric(pd.Series([latest.get(call_col)]), errors="coerce").iloc[0]
    put_value = pd.to_numeric(pd.Series([latest.get(put_col)]), errors="coerce").iloc[0]
    if pd.isna(call_value) or pd.isna(put_value):
        return None
    return float(call_value - put_value)


def _map_vix_proxy_to_regime(vix_proxy: float) -> str:
    if vix_proxy > 30:
        return "EXTREME"
    if vix_proxy > 20:
        return "ELEVATED"
    if vix_proxy > 12:
        return "NORMAL"
    return "LOW"


def _get_latest_vix_data_from_heber() -> dict[str, float | str | None] | None:
    try:
        now = datetime.now(timezone.utc)
        bars_df = _heber_reader.read_bars(
            symbols=["VIXY"],
            asof_time=now,
            start_time=now - pd.Timedelta(days=3),
        )
    except Exception:
        logger.debug("Heber VIXY bars read failed, falling back to local DB", exc_info=True)
        return None

    if bars_df.empty:
        return None

    bars_df = bars_df.copy()
    symbol_col = _first_existing_column(bars_df, ["symbol", "ticker", "underlying", "instrument_key"])
    if symbol_col is not None:
        symbols = bars_df[symbol_col].astype(str).str.upper().str.split(":").str[-1]
        bars_df = bars_df.loc[symbols == "VIXY"]
    if bars_df.empty:
        return None

    close_col = _first_existing_column(bars_df, ["close", "c"])
    if close_col is None:
        return None

    bars_df["_ts"] = _coerce_time_series(bars_df)
    bars_df["_close"] = pd.to_numeric(bars_df[close_col], errors="coerce")
    bars_df = bars_df.dropna(subset=["_ts", "_close"]).sort_values("_ts")
    if bars_df.empty:
        return None

    latest = bars_df.iloc[-1]
    latest_close = float(latest["_close"])
    target_prior_ts = latest["_ts"] - pd.Timedelta(days=1)
    prior_rows = bars_df.loc[bars_df["_ts"] <= target_prior_ts]
    if prior_rows.empty:
        vix_1d_change = 0.0
    else:
        prior_close = float(prior_rows.iloc[-1]["_close"])
        vix_1d_change = ((latest_close - prior_close) / prior_close) * 100 if prior_close > 0 else 0.0

    return {
        "vix": latest_close,
        "vvix": None,
        "vix_1d_change": vix_1d_change,
        "vix_regime": _map_vix_proxy_to_regime(latest_close),
    }


def _get_spy_cumulative_return_from_heber() -> float | None:
    try:
        now = datetime.now(timezone.utc)
        bars_df = _heber_reader.read_bars(
            symbols=["SPY"],
            asof_time=now,
            start_time=now - pd.Timedelta(days=2),
        )
    except Exception:
        logger.debug("Heber SPY bars read failed, falling back to local DB", exc_info=True)
        return None

    if bars_df.empty:
        return None

    bars_df = bars_df.copy()
    symbol_col = _first_existing_column(bars_df, ["symbol", "ticker", "underlying", "instrument_key"])
    if symbol_col is not None:
        symbols = bars_df[symbol_col].astype(str).str.upper().str.split(":").str[-1]
        bars_df = bars_df.loc[symbols == "SPY"]
    if bars_df.empty:
        return None

    close_col = _first_existing_column(bars_df, ["close", "c"])
    if close_col is None:
        return None

    bars_df["_ts"] = _coerce_time_series(bars_df)
    if bars_df["_ts"].notna().any():
        bars_df = bars_df.dropna(subset=["_ts"]).sort_values("_ts", ascending=False)

    bars_df["_close"] = pd.to_numeric(bars_df[close_col], errors="coerce")
    bars_df = bars_df.dropna(subset=["_close"]).head(20)
    if bars_df.empty:
        return None
    if len(bars_df) < 2:
        return 0.0

    latest_close = float(bars_df.iloc[0]["_close"])
    oldest_close = float(bars_df.iloc[-1]["_close"])
    if oldest_close == 0:
        return 0.0
    return (latest_close - oldest_close) / oldest_close


def _zero_write_warn_streak_threshold() -> int:
    raw = os.getenv(
        "ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK",
        str(DEFAULT_ZERO_WRITE_WARN_STREAK),
    ).strip()
    try:
        value = int(raw)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except Exception:
        logger.warning(
            "Invalid ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK; using default",
            extra={
                "event": "feature_enrichment_zero_write_warn_streak_invalid",
                "value": raw,
                "default": DEFAULT_ZERO_WRITE_WARN_STREAK,
            },
        )
        return DEFAULT_ZERO_WRITE_WARN_STREAK


def _loop_sleep_seconds() -> float:
    raw = os.getenv(
        "ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS",
        str(DEFAULT_LOOP_SLEEP_SECONDS),
    ).strip()
    try:
        value = float(raw)
        if value <= 0:
            raise ValueError("must be > 0")
        return value
    except Exception:
        logger.warning(
            "Invalid ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS; using default",
            extra={
                "event": "feature_enrichment_loop_sleep_seconds_invalid",
                "value": raw,
                "default": DEFAULT_LOOP_SLEEP_SECONDS,
            },
        )
        return DEFAULT_LOOP_SLEEP_SECONDS


def _loop_error_warn_streak_threshold() -> int:
    raw = os.getenv(
        "ORION_FEATURE_ENRICHMENT_LOOP_ERROR_WARN_STREAK",
        str(DEFAULT_LOOP_ERROR_WARN_STREAK),
    ).strip()
    try:
        value = int(raw)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except Exception:
        logger.warning(
            "Invalid ORION_FEATURE_ENRICHMENT_LOOP_ERROR_WARN_STREAK; using default",
            extra={
                "event": "feature_enrichment_loop_error_warn_streak_invalid",
                "value": raw,
                "default": DEFAULT_LOOP_ERROR_WARN_STREAK,
            },
        )
        return DEFAULT_LOOP_ERROR_WARN_STREAK


def _note_loop_error(
    consecutive_error_streak: int,
    warn_streak: int,
    error: Exception,
) -> int:
    streak = consecutive_error_streak + 1
    if streak >= warn_streak:
        logger.warning(
            "Feature enrichment loop has consecutive cycle errors",
            extra={
                "event": "feature_enrichment_loop_error_streak",
                "streak": streak,
                "warn_streak": warn_streak,
                "error": str(error),
            },
        )
    return streak


def _non_heber_warn_streak_threshold() -> int:
    raw = os.getenv(
        "ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK",
        str(DEFAULT_NON_HEBER_WARN_STREAK),
    ).strip()
    try:
        value = int(raw)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except Exception:
        logger.warning(
            "Invalid ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK; using default",
            extra={
                "event": "feature_enrichment_non_heber_warn_streak_invalid",
                "value": raw,
                "default": DEFAULT_NON_HEBER_WARN_STREAK,
            },
        )
        return DEFAULT_NON_HEBER_WARN_STREAK


def _note_ticker_source_streak(
    source: str,
    non_heber_streak: int,
    warn_streak: int,
    tickers_count: int,
) -> int:
    if source == "heber":
        return 0

    streak = non_heber_streak + 1
    if streak >= warn_streak:
        logger.warning(
            "Ticker discovery has consecutive non-Heber source cycles",
            extra={
                "event": "feature_enrichment_non_heber_streak",
                "source": source,
                "streak": streak,
                "warn_streak": warn_streak,
                "tickers_count": tickers_count,
            },
        )
    return streak


def _note_fetch_count(
    feed_name: str,
    count: int,
    zero_write_streaks: dict[str, int],
    warn_streak: int,
    tickers_count: int | None = None,
) -> None:
    if count > 0:
        zero_write_streaks[feed_name] = 0
        return

    streak = zero_write_streaks.get(feed_name, 0) + 1
    zero_write_streaks[feed_name] = streak
    if streak >= warn_streak:
        logger.warning(
            "Feature enrichment feed has consecutive zero-write cycles",
            extra={
                "event": "feature_enrichment_zero_write_streak",
                "feed": feed_name,
                "count": count,
                "streak": streak,
                "warn_streak": warn_streak,
                "tickers_count": tickers_count,
            },
        )


def _log_ticker_source_transition(source: str, previous_source: str | None, tickers_count: int) -> str:
    if source == previous_source:
        return source

    extra = {
        "event": "feature_enrichment_ticker_source_changed",
        "previous_source": previous_source,
        "source": source,
        "tickers_count": tickers_count,
    }
    if source == "heber":
        logger.info("Ticker discovery source switched", extra=extra)
    else:
        logger.warning("Ticker discovery source switched away from Heber", extra=extra)
    return source


async def get_active_tickers_with_source(limit: int = 20) -> tuple[List[str], str]:
    """Get tickers with recent flow activity and the source used."""
    try:
        now_utc = datetime.now(timezone.utc)
        flow_df = _heber_reader.read_flow(
            asof_time=now_utc,
            start_time=now_utc - pd.Timedelta(days=2),
        )
        tickers = _extract_top_tickers_from_flow_df(flow_df, limit=limit)
        if tickers:
            return tickers, "heber"
    except Exception:
        logger.debug("Heber flow ticker discovery failed; using static fallback", exc_info=True)

    return STATIC_TICKER_FALLBACK[:limit], "static_fallback"


async def get_active_tickers(limit: int = 20) -> List[str]:
    """Get tickers with recent flow activity (Heber first, DB fallback)."""
    tickers, _source = await get_active_tickers_with_source(limit=limit)
    return tickers


async def get_latest_vix_data() -> dict:
    """Get latest VIX context, preferring Heber VIXY proxy bars."""
    if _prefer_heber_context_reads():
        heber_vix = _get_latest_vix_data_from_heber()
        if heber_vix is not None:
            return heber_vix

    return {}


async def get_latest_market_tide() -> float | None:
    """Get latest market tide net premium (calls - puts)."""
    if _prefer_heber_context_reads():
        heber_net = _get_latest_market_tide_from_heber()
        if heber_net is not None:
            return heber_net

    return None


async def get_spy_cumulative_return() -> float:
    """Get SPY cumulative return over past 20 bars (approximate trend)."""
    if _prefer_heber_context_reads():
        heber_return = _get_spy_cumulative_return_from_heber()
        if heber_return is not None:
            return heber_return

    return 0.0


async def persist_regime_snapshot(
    ts: datetime,
    snapshot: Any,
    ticker: str = "SPY",
) -> None:
    """Persist regime snapshot in memory while centralized sinks are externalized."""
    import json

    record = {
        "ts_utc": ts,
        "ticker": ticker,
        "trend_regime": snapshot.trend.value if snapshot.trend else None,
        "vol_regime": snapshot.vol.value if snapshot.vol else None,
        "risk_regime": snapshot.risk.value if snapshot.risk else None,
        "session_regime": snapshot.session.value if snapshot.session else None,
        "vix_regime": snapshot.vix_regime.value if snapshot.vix_regime else None,
        "vix_level": snapshot.vix_level,
        "realized_vol": snapshot.realized_vol,
        "trend_strength": snapshot.trend_strength,
        "risk_score": snapshot.risk_score,
        "confidence_json": json.dumps(snapshot.confidence) if snapshot.confidence else None,
    }

    _recent_regime_snapshots.append(record)
    if len(_recent_regime_snapshots) > 2000:
        del _recent_regime_snapshots[:1000]


async def run_feature_loop(shutdown_event: asyncio.Event) -> None:
    """Main feature enrichment loop."""
    gateway_url, gateway_api_key = _gateway_runtime_contract()
    zero_write_warn_streak = _zero_write_warn_streak_threshold()
    loop_sleep_seconds = _loop_sleep_seconds()
    loop_error_warn_streak = _loop_error_warn_streak_threshold()
    non_heber_warn_streak = _non_heber_warn_streak_threshold()
    await init_db()

    # Initialize connectors (now using Data Gateway)
    greek_connector = UWGreekExposureConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
    tide_connector = UWMarketTideConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
    max_pain_connector = UWMaxPainConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
    iv_connector = UWIVRankConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
    regime_detector = MultiAxisRegimeDetector()
    vix_connector = VIXProxyConnector()  # Uses VIXY bars from silver_alpaca_bars

    last_tide = datetime.min.replace(tzinfo=timezone.utc)
    last_greek = datetime.min.replace(tzinfo=timezone.utc)
    last_max_pain = datetime.min.replace(tzinfo=timezone.utc)
    last_iv = datetime.min.replace(tzinfo=timezone.utc)
    last_regime = datetime.min.replace(tzinfo=timezone.utc)
    last_vix = datetime.min.replace(tzinfo=timezone.utc)
    last_ticker_source: str | None = None
    zero_write_streaks: dict[str, int] = {}
    loop_error_streak = 0
    non_heber_streak = 0

    logger.info("Feature Enrichment Service started")

    while not shutdown_event.is_set():
        try:
            now = datetime.now(timezone.utc)
            tickers, ticker_source = await get_active_tickers_with_source()
            last_ticker_source = _log_ticker_source_transition(
                source=ticker_source,
                previous_source=last_ticker_source,
                tickers_count=len(tickers),
            )
            non_heber_streak = _note_ticker_source_streak(
                source=ticker_source,
                non_heber_streak=non_heber_streak,
                warn_streak=non_heber_warn_streak,
                tickers_count=len(tickers),
            )

            # Market Tide - every minute
            if (now - last_tide).total_seconds() >= MARKET_TIDE_INTERVAL:
                count = await tide_connector.fetch_and_store()
                logger.info(f"Market Tide: stored {count} ticks")
                _note_fetch_count("market_tide", count, zero_write_streaks, zero_write_warn_streak)
                last_tide = now

            # Greek Exposure - every 5 minutes
            if (now - last_greek).total_seconds() >= GREEK_EXPOSURE_INTERVAL:
                count = await greek_connector.fetch_and_store(tickers)
                logger.info(f"Greek Exposure: stored {count} records for {len(tickers)} tickers")
                _note_fetch_count(
                    "greek_exposure",
                    count,
                    zero_write_streaks,
                    zero_write_warn_streak,
                    tickers_count=len(tickers),
                )
                last_greek = now

            # Max Pain - every hour
            if (now - last_max_pain).total_seconds() >= MAX_PAIN_INTERVAL:
                count = await max_pain_connector.fetch_and_store(tickers)
                logger.info(f"Max Pain: stored {count} records")
                _note_fetch_count(
                    "max_pain",
                    count,
                    zero_write_streaks,
                    zero_write_warn_streak,
                    tickers_count=len(tickers),
                )
                last_max_pain = now

            # IV Rank - every 15 minutes
            if (now - last_iv).total_seconds() >= IV_RANK_INTERVAL:
                count = await iv_connector.fetch_and_store(tickers)
                logger.info(f"IV Rank: stored {count} records")
                _note_fetch_count(
                    "iv_rank",
                    count,
                    zero_write_streaks,
                    zero_write_warn_streak,
                    tickers_count=len(tickers),
                )
                last_iv = now

            # VIX Data - every hour
            if (now - last_vix).total_seconds() >= VIX_DATA_INTERVAL:
                try:
                    count = await vix_connector.fetch_and_store()
                    logger.info(f"VIX Proxy: stored {count} records")
                    _note_fetch_count("vix_proxy", count, zero_write_streaks, zero_write_warn_streak)
                    last_vix = now
                except Exception as e:
                    logger.error(f"VIX proxy fetch error: {e}", exc_info=True)

            # Regime Snapshot - every 5 minutes
            if (now - last_regime).total_seconds() >= REGIME_SNAPSHOT_INTERVAL:
                try:
                    vix_data = await get_latest_vix_data()
                    market_tide_net = await get_latest_market_tide()
                    cum_ret = await get_spy_cumulative_return()

                    snapshot = regime_detector.detect(
                        ts=now,
                        cum_ret=cum_ret,
                        realized_vol=0.015,  # Default; could compute from bars
                        vix=vix_data.get("vix"),
                        vix_1d_change=vix_data.get("vix_1d_change"),
                        market_tide_net=market_tide_net,
                    )

                    await persist_regime_snapshot(now, snapshot)
                    logger.info(
                        f"Regime Snapshot: trend={snapshot.trend.value}, "
                        f"vol={snapshot.vol.value}, risk={snapshot.risk.value}, "
                        f"session={snapshot.session.value}, vix={snapshot.vix_regime.value}"
                    )
                    last_regime = now
                except Exception as e:
                    logger.error(f"Regime snapshot error: {e}", exc_info=True)
            loop_error_streak = 0

        except Exception as e:
            logger.error(f"Feature enrichment error: {e}", exc_info=True)
            loop_error_streak = _note_loop_error(
                consecutive_error_streak=loop_error_streak,
                warn_streak=loop_error_warn_streak,
                error=e,
            )

        # Wait before next iteration
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=loop_sleep_seconds)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("Feature Enrichment Service stopped")


async def main() -> None:
    """Main entry point."""
    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def handle_signal(sig: int) -> None:
        logger.info(f"Received signal {sig}. Shutting down...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    await run_feature_loop(shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())
