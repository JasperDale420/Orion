"""UW Feature Enrichment Service.

Periodically fetches GEX, Market Tide, Max Pain, IV Rank for tracked tickers.
Runs as a background service to populate feature tables for ML.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

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
    persist_discovery_status,
    persist_regime_snapshot,
)
from orion.shared.async_main import run_service
from orion.shared.liveness import publish_liveness
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.feature_enrichment")

# Liveness cadence budget. The loop ticks every ~30s but its heaviest sub-poll
# (regime/connector intervals) is up to 15 min; 900s gives the dead-man watchdog
# headroom over the longest legitimate gap between successful cycles.
LIVENESS_CADENCE_BUDGET_SECONDS = 900

# Poll intervals
MARKET_TIDE_INTERVAL = 300  # Every 5 minutes (reduced from 60s to save API calls)
GREEK_EXPOSURE_INTERVAL = 300  # Every 5 minutes
MAX_PAIN_INTERVAL = 3600  # Every hour
IV_RANK_INTERVAL = 900  # Every 15 minutes
REGIME_SNAPSHOT_INTERVAL = 300  # Every 5 minutes
VIX_DATA_INTERVAL = 3600  # Every hour (VIX is daily-level data)
TICKER_DISCOVERY_INTERVAL = 300  # Refresh ticker list every 5 minutes
# Note: ticker discovery scans 2 days of UW flow parquet (~GB range); running it
# every loop iteration causes OOM crash-loops.
DEFAULT_ZERO_WRITE_WARN_STREAK = 3
DEFAULT_LOOP_SLEEP_SECONDS = 30.0
DEFAULT_LOOP_ERROR_WARN_STREAK = 3
DEFAULT_NON_HEBER_WARN_STREAK = 3

# Market-hours gate for UW connector polls. UW's spot-exposures /
# market-tide / max-pain endpoints return empty payloads outside
# regular+extended trading hours, and the VIX-proxy bars only stream
# when VIXY is trading. Polling outside this window burns API budget
# and triggers `feature_enrichment_zero_write_streak` warnings on
# expected-empty data. Matches the gate already used in
# main_data_quality.py.
ET_TZ = ZoneInfo("America/New_York")
MARKET_HOURS_GATE_FEEDS = ("market_tide", "greek_exposure", "max_pain", "iv_rank", "vix_proxy")
MARKET_HOURS_START_HOUR = 7  # 7 AM ET — pre-market open
MARKET_HOURS_END_HOUR = 20  # 8 PM ET — post-market close


def _is_extended_market_hours(now_utc: datetime | None = None) -> bool:
    """Return True if `now_utc` falls in extended trading hours (Mon-Fri,
    07:00-20:00 ET). Outside this window UW endpoints return empty.
    """
    now = (now_utc or datetime.now(UTC)).astimezone(ET_TZ)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    return MARKET_HOURS_START_HOUR <= now.hour < MARKET_HOURS_END_HOUR


_T = int | float


def _parse_env_threshold(env_key: str, default: _T, type_fn: type[_T], *, min_val: _T | None = None) -> _T:
    """Parse a numeric env var with bounds checking, logging a warning on failure."""
    raw = os.getenv(env_key, str(default)).strip()
    try:
        value = type_fn(raw)
        if min_val is not None and value < min_val:
            raise ValueError(f"must be >= {min_val}")
        return value
    except Exception:
        logger.warning(
            f"Invalid {env_key}; using default",
            extra={"event": f"{env_key.lower()}_invalid", "value": raw, "default": default},
        )
        return default


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


_GATEWAY_FETCH_ENV = "ORION_FEATURE_ENRICHMENT_ENABLE_GATEWAY_FETCH"
_GATEWAY_FETCH_FALSE_VALUES = {"0", "false", "no", "off", "n"}


def _gateway_fetch_enabled() -> bool:
    raw = os.getenv(_GATEWAY_FETCH_ENV)
    if raw is None:
        return system_settings.feature_enrichment_enable_gateway_fetch
    return raw.lower() not in _GATEWAY_FETCH_FALSE_VALUES


def _gateway_runtime_contract() -> tuple[str, str]:
    gateway_url = (system_settings.data_gateway_url or "").strip()
    if not gateway_url:
        raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")

    gateway_api_key = (system_settings.data_gateway_api_key or "").strip()
    if not gateway_api_key:
        raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")

    return gateway_url.rstrip("/"), gateway_api_key


def _zero_write_warn_streak_threshold() -> int:
    return cast(
        int,
        _parse_env_threshold(
            "ORION_FEATURE_ENRICHMENT_ZERO_WRITE_WARN_STREAK", DEFAULT_ZERO_WRITE_WARN_STREAK, int, min_val=1
        ),
    )


def _loop_sleep_seconds() -> float:
    return _parse_env_threshold(
        "ORION_FEATURE_ENRICHMENT_LOOP_SLEEP_SECONDS", DEFAULT_LOOP_SLEEP_SECONDS, float, min_val=0.01
    )


def _loop_error_warn_streak_threshold() -> int:
    return cast(
        int,
        _parse_env_threshold(
            "ORION_FEATURE_ENRICHMENT_LOOP_ERROR_WARN_STREAK", DEFAULT_LOOP_ERROR_WARN_STREAK, int, min_val=1
        ),
    )


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
    return cast(
        int,
        _parse_env_threshold(
            "ORION_FEATURE_ENRICHMENT_NON_HEBER_WARN_STREAK", DEFAULT_NON_HEBER_WARN_STREAK, int, min_val=1
        ),
    )


def _note_ticker_source_streak(
    source: str,
    non_heber_streak: int,
    warn_streak: int,
    tickers_count: int,
) -> int:
    # Per docs/rca/feature_enrichment_crash_loop.md (2026-04-22), bronze_db
    # is the canonical primary ticker-discovery source; "heber" is now a
    # fallback. Both are healthy outcomes — only "static_fallback" or an
    # unknown source indicates real degradation, so reset the streak for
    # the recognized data-backed sources.
    if source in ("bronze_db", "heber"):
        return 0

    streak = non_heber_streak + 1
    if streak >= warn_streak:
        logger.warning(
            "Ticker discovery fell back to static list",
            extra={
                "event": "feature_enrichment_static_fallback_streak",
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

    # Stagger initial-fire timing so the UW connectors don't fan out in one
    # asyncio.gather at startup (200+ concurrent HTTP calls = memory spike that
    # previously OOM-killed this container). Lighter HTTP-backed connectors
    # (tide/greek/iv) fire first. Heavy Heber-backed connectors (max_pain,
    # vix_proxy — both read parquet bars) wait for their natural interval so
    # they don't compound memory pressure while Python is still warming up.
    _now_init = datetime.now(UTC)
    last_tide = _now_init - timedelta(seconds=MARKET_TIDE_INTERVAL - 5)
    last_greek = _now_init - timedelta(seconds=GREEK_EXPOSURE_INTERVAL - 75)
    last_iv = _now_init - timedelta(seconds=IV_RANK_INTERVAL - 145)
    last_regime = _now_init - timedelta(seconds=REGIME_SNAPSHOT_INTERVAL - 45)
    last_max_pain = _now_init  # fires at +MAX_PAIN_INTERVAL (1h)
    last_vix = _now_init  # fires at +VIX_DATA_INTERVAL (1h)
    last_ticker_refresh = datetime.min.replace(tzinfo=UTC)
    last_ticker_source: str | None = None
    tickers: list[str] = []
    ticker_source: str = "unset"
    zero_write_streaks: dict[str, int] = {}
    loop_error_streak = 0
    non_heber_streak = 0

    logger.info("Feature Enrichment Service started")

    while not shutdown_event.is_set():
        try:
            now = datetime.now(UTC)

            # Ticker discovery scans 2 days of flow parquet (~GB-scale reads).
            # Refresh only every TICKER_DISCOVERY_INTERVAL instead of every loop.
            if (now - last_ticker_refresh).total_seconds() >= TICKER_DISCOVERY_INTERVAL:
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
                # Surface degradation to ExecutionEngine via SystemStatus so a
                # stale-discovery state hard-blocks new trades; persisted every
                # cycle (including healthy ones) so last_updated_utc doubles
                # as a liveness signal.
                await persist_discovery_status(
                    source=ticker_source,
                    streak=non_heber_streak,
                    warn_streak=non_heber_warn_streak,
                )
                last_ticker_refresh = now

            # --- UW Connector fetches (parallelized via asyncio.gather) ---
            # Skip outside extended trading hours (Mon-Fri 07:00-20:00 ET).
            # UW endpoints return empty payloads at all other times, which
            # otherwise trigger feature_enrichment_zero_write_streak warnings
            # and burn API budget. Reset gated-feed streaks on entry to off-
            # hours so the streak doesn't carry over to the next session.
            in_market_hours = _is_extended_market_hours(now)
            if not in_market_hours:
                for feed in MARKET_HOURS_GATE_FEEDS:
                    zero_write_streaks[feed] = 0
                if not getattr(run_feature_loop, "_off_hours_logged", False):
                    logger.info(
                        "Skipping UW connector polls outside extended market hours",
                        extra={
                            "event": "feature_enrichment_off_hours_skip",
                            "feeds": list(MARKET_HOURS_GATE_FEEDS),
                            "et_hour": datetime.now(ET_TZ).hour,
                            "weekday": datetime.now(ET_TZ).weekday(),
                        },
                    )
                    run_feature_loop._off_hours_logged = True  # type: ignore[attr-defined]
            else:
                run_feature_loop._off_hours_logged = False  # type: ignore[attr-defined]

            if gateway_fetch_enabled and in_market_hours:
                uw_tasks: list[Any] = []  # list of coroutines for asyncio.gather
                uw_task_meta: list[dict[str, Any]] = []  # name, feed_name, has_tickers

                if tide_connector is not None and (now - last_tide).total_seconds() >= MARKET_TIDE_INTERVAL:
                    uw_tasks.append(tide_connector.fetch_and_store())
                    uw_task_meta.append({"name": "Market Tide", "feed": "market_tide", "has_tickers": False})

                if greek_connector is not None and (now - last_greek).total_seconds() >= GREEK_EXPOSURE_INTERVAL:
                    uw_tasks.append(greek_connector.fetch_and_store(tickers))
                    uw_task_meta.append({"name": "Greek Exposure", "feed": "greek_exposure", "has_tickers": True})

                if max_pain_connector is not None and (now - last_max_pain).total_seconds() >= MAX_PAIN_INTERVAL:
                    uw_tasks.append(max_pain_connector.fetch_and_store(tickers))
                    uw_task_meta.append({"name": "Max Pain", "feed": "max_pain", "has_tickers": True})

                if iv_connector is not None and (now - last_iv).total_seconds() >= IV_RANK_INTERVAL:
                    uw_tasks.append(iv_connector.fetch_and_store(tickers))
                    uw_task_meta.append({"name": "IV Rank", "feed": "iv_rank", "has_tickers": True})

                if uw_tasks:
                    results = await asyncio.gather(*uw_tasks, return_exceptions=True)

                    succeeded_feeds: set[str] = set()
                    for i, result in enumerate(results):
                        meta = uw_task_meta[i]
                        if isinstance(result, BaseException):
                            logger.error(
                                "uw_connector_failed",
                                connector=meta["name"],
                                feed=meta["feed"],
                                error=str(result),
                                exc_info=result,
                            )
                            continue

                        succeeded_feeds.add(meta["feed"])
                        count = cast(int, result)
                        if meta["has_tickers"]:
                            logger.info(f"{meta['name']}: stored {count} records for {len(tickers)} tickers")
                            _note_fetch_count(
                                meta["feed"],
                                count,
                                zero_write_streaks,
                                zero_write_warn_streak,
                                tickers_count=len(tickers),
                            )
                        else:
                            logger.info(f"{meta['name']}: stored {count} ticks")
                            _note_fetch_count(meta["feed"], count, zero_write_streaks, zero_write_warn_streak)

                    # Only advance timestamps for connectors that succeeded —
                    # failed connectors should retry on the next loop iteration
                    if "market_tide" in succeeded_feeds:
                        last_tide = now
                    if "greek_exposure" in succeeded_feeds:
                        last_greek = now
                    if "max_pain" in succeeded_feeds:
                        last_max_pain = now
                    if "iv_rank" in succeeded_feeds:
                        last_iv = now

            # VIX Data — gated on extended market hours (VIXY only streams
            # bars when the underlying ETF is trading).
            if in_market_hours and (now - last_vix).total_seconds() >= VIX_DATA_INTERVAL:
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

            # Liveness: one publish per successful cycle (swallows its own errors).
            await publish_liveness("feature_enrichment", cadence_budget_seconds=LIVENESS_CADENCE_BUDGET_SECONDS)

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


if __name__ == "__main__":
    # run_feature_loop() calls init_db() itself, so skip the helper's init.
    # The helper installs SIGINT/SIGTERM handlers, logs any unhandled crash as
    # CRITICAL, and exits non-zero so OOM/restart loops stay visible.
    run_service("orion.feature_enrichment", run_feature_loop, init_database=False)
