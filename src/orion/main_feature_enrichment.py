"""UW Feature Enrichment Service.

Periodically fetches GEX, Market Tide, Max Pain, IV Rank for tracked tickers.
Runs as a background service to populate feature tables for ML.
"""

import asyncio
import os
import signal
from datetime import UTC, datetime
from functools import partial

from dotenv import load_dotenv

load_dotenv()

from orion.analysis.regime import MultiAxisRegimeDetector
from orion.config import system_settings
from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector
from orion.connectors.uw_iv_rank_connector import UWIVRankConnector
from orion.connectors.uw_market_tide_connector import UWMarketTideConnector
from orion.connectors.uw_max_pain_connector import UWMaxPainConnector
from orion.connectors.vix_proxy_connector import VIXProxyConnector
from orion.enrichment.heber_context import (
    get_active_tickers_with_source,
    get_latest_market_tide,
    get_latest_vix_data,
    get_spy_cumulative_return,
    persist_regime_snapshot,
)
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


# Re-export for backward compatibility (other modules and tests import from here)
from orion.enrichment.heber_context import (  # noqa: E402, F401
    STATIC_TICKER_FALLBACK,
    _coerce_time_series,
    _extract_tickers_from_bars,
    _extract_top_tickers_from_flow_df,
    _get_latest_market_tide_from_heber,
    _get_latest_vix_data_from_heber,
    _get_spy_cumulative_return_from_heber,
    _map_vix_proxy_to_regime,
    _prefer_heber_context_reads,
    _try_vix_proxy_from_heber,
    get_active_tickers,
    get_active_tickers_with_source as _get_active_tickers_with_source_orig,
)


def _gateway_fetch_enabled() -> bool:
    raw = os.getenv("ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH", "0").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def _gateway_runtime_contract() -> tuple[str, str]:
    gateway_url = (system_settings.data_gateway_url or "").strip()
    if not gateway_url:
        raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")

    gateway_api_key = (system_settings.data_gateway_api_key or "").strip()
    if not gateway_api_key:
        raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")

    return gateway_url.rstrip("/"), gateway_api_key


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


async def run_feature_loop(shutdown_event: asyncio.Event) -> None:
    """Main feature enrichment loop."""
    gateway_fetch_enabled = _gateway_fetch_enabled()
    zero_write_warn_streak = _zero_write_warn_streak_threshold()
    loop_sleep_seconds = _loop_sleep_seconds()
    loop_error_warn_streak = _loop_error_warn_streak_threshold()
    non_heber_warn_streak = _non_heber_warn_streak_threshold()
    await init_db()

    greek_connector: UWGreekExposureConnector | None = None
    tide_connector: UWMarketTideConnector | None = None
    max_pain_connector: UWMaxPainConnector | None = None
    iv_connector: UWIVRankConnector | None = None
    if gateway_fetch_enabled:
        gateway_url, gateway_api_key = _gateway_runtime_contract()
        greek_connector = UWGreekExposureConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
        tide_connector = UWMarketTideConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
        max_pain_connector = UWMaxPainConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
        iv_connector = UWIVRankConnector(gateway_url=gateway_url, gateway_key=gateway_api_key)
    else:
        logger.info(
            "Feature enrichment gateway polling disabled; relying on Data-Gateway -> Heber feeds",
            extra={"event": "feature_enrichment_gateway_fetch_disabled"},
        )
    regime_detector = MultiAxisRegimeDetector()
    vix_connector = VIXProxyConnector()  # Uses VIXY bars from Heber bars feed

    last_tide = datetime.min.replace(tzinfo=UTC)
    last_greek = datetime.min.replace(tzinfo=UTC)
    last_max_pain = datetime.min.replace(tzinfo=UTC)
    last_iv = datetime.min.replace(tzinfo=UTC)
    last_regime = datetime.min.replace(tzinfo=UTC)
    last_vix = datetime.min.replace(tzinfo=UTC)
    last_ticker_source: str | None = None
    zero_write_streaks: dict[str, int] = {}
    loop_error_streak = 0
    non_heber_streak = 0

    logger.info("Feature Enrichment Service started")

    while not shutdown_event.is_set():
        try:
            now = datetime.now(UTC)
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

            # Market Tide
            if (
                gateway_fetch_enabled
                and tide_connector is not None
                and (now - last_tide).total_seconds() >= MARKET_TIDE_INTERVAL
            ):
                count = await tide_connector.fetch_and_store()
                logger.info(f"Market Tide: stored {count} ticks")
                _note_fetch_count("market_tide", count, zero_write_streaks, zero_write_warn_streak)
                last_tide = now

            # Greek Exposure
            if (
                gateway_fetch_enabled
                and greek_connector is not None
                and (now - last_greek).total_seconds() >= GREEK_EXPOSURE_INTERVAL
            ):
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

            # Max Pain
            if (
                gateway_fetch_enabled
                and max_pain_connector is not None
                and (now - last_max_pain).total_seconds() >= MAX_PAIN_INTERVAL
            ):
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

            # IV Rank
            if (
                gateway_fetch_enabled
                and iv_connector is not None
                and (now - last_iv).total_seconds() >= IV_RANK_INTERVAL
            ):
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

            # VIX Data
            if (now - last_vix).total_seconds() >= VIX_DATA_INTERVAL:
                try:
                    count = await vix_connector.fetch_and_store()
                    logger.info(f"VIX Proxy: stored {count} records")
                    _note_fetch_count("vix_proxy", count, zero_write_streaks, zero_write_warn_streak)
                    last_vix = now
                except Exception as e:
                    logger.error(f"VIX proxy fetch error: {e}", exc_info=True)

            # Regime Snapshot
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
        except TimeoutError:
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
        loop.add_signal_handler(sig, partial(handle_signal, sig))

    await run_feature_loop(shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())
