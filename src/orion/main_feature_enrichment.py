"""
UW Feature Enrichment Service.

Periodically fetches GEX, Market Tide, Max Pain, IV Rank for tracked tickers.
Runs as a background service to populate feature tables for ML.
"""

import asyncio
import signal
from datetime import datetime, timezone
from typing import Any, List

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.analysis.regime import MultiAxisRegimeDetector
from orion.clients.heber_reader import HeberReader
from orion.config import system_settings
from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector
from orion.connectors.uw_iv_rank_connector import UWIVRankConnector
from orion.connectors.uw_market_tide_connector import UWMarketTideConnector
from orion.connectors.uw_max_pain_connector import UWMaxPainConnector
from orion.connectors.vix_proxy_connector import VIXProxyConnector
from orion.shared.db_utils import db_query, db_write
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

_heber_reader = HeberReader()


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


async def get_active_tickers(limit: int = 20) -> List[str]:
    """Get tickers with recent flow activity (Heber first, DB fallback)."""
    try:
        now_utc = datetime.now(timezone.utc)
        flow_df = _heber_reader.read_flow(
            asof_time=now_utc,
            start_time=now_utc - pd.Timedelta(days=2),
        )
        tickers = _extract_top_tickers_from_flow_df(flow_df, limit=limit)
        if tickers:
            return tickers
    except Exception:
        logger.debug("Heber flow ticker discovery failed, falling back to local DB", exc_info=True)

    async def query(session: Any) -> List[str]:
        stmt = text(
            """
            SELECT ticker
            FROM silver_uw_flow
            WHERE flow_ts_utc > NOW() - INTERVAL '1 day'
            AND ticker IS NOT NULL
            GROUP BY ticker
            ORDER BY COUNT(*) DESC
            LIMIT :limit
        """
        )
        result = await session.execute(stmt, {"limit": limit})
        return [row[0] for row in result.fetchall()]

    try:
        return await db_query(query)
    except Exception:
        # Fallback to common tickers
        return ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMD", "META", "AMZN", "GOOG", "MSFT"]


async def get_latest_vix_data() -> dict:
    """Get latest VIX data from silver_vix_data."""

    async def query(session: Any) -> dict:
        stmt = text(
            """
            SELECT vix, vvix, vix_1d_change, vix_regime
            FROM silver_vix_data
            ORDER BY ts_utc DESC
            LIMIT 1
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        if row:
            return {
                "vix": row[0],
                "vvix": row[1],
                "vix_1d_change": row[2],
                "vix_regime": row[3],
            }
        return {}

    try:
        return await db_query(query)
    except Exception:
        return {}


async def get_latest_market_tide() -> float | None:
    """Get latest market tide net premium (calls - puts)."""

    async def query(session: Any) -> float | None:
        stmt = text(
            """
            SELECT net_call_premium - net_put_premium as net_premium
            FROM silver_market_tide
            ORDER BY ts_utc DESC
            LIMIT 1
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None

    try:
        return await db_query(query)
    except Exception:
        return None


async def get_spy_cumulative_return() -> float:
    """Get SPY cumulative return over past 20 bars (approximate trend)."""

    async def query(session: Any) -> float:
        stmt = text(
            """
            SELECT
                (LAST_VALUE(close) OVER (ORDER BY bar_start_ts_utc) -
                 FIRST_VALUE(close) OVER (ORDER BY bar_start_ts_utc)) /
                NULLIF(FIRST_VALUE(close) OVER (ORDER BY bar_start_ts_utc), 0) as cum_return
            FROM silver_alpaca_bars
            WHERE ticker = 'SPY'
            ORDER BY bar_start_ts_utc DESC
            LIMIT 20
        """
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        return float(row[0]) if row and row[0] else 0.0

    try:
        return await db_query(query)
    except Exception:
        return 0.0


async def persist_regime_snapshot(
    ts: datetime,
    snapshot: Any,
    ticker: str = "SPY",
) -> None:
    """Persist regime snapshot to silver_regime_history."""
    import json

    async def write(session: Any) -> None:
        stmt = text(
            """
            INSERT INTO silver_regime_history (
                ts_utc, ticker, trend_regime, vol_regime, risk_regime,
                session_regime, vix_regime, vix_level, realized_vol,
                trend_strength, risk_score, confidence_json
            ) VALUES (
                :ts_utc, :ticker, :trend_regime, :vol_regime, :risk_regime,
                :session_regime, :vix_regime, :vix_level, :realized_vol,
                :trend_strength, :risk_score, :confidence_json
            )
        """
        )
        await session.execute(
            stmt,
            {
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
            },
        )

    await db_write(write)


async def run_feature_loop(shutdown_event: asyncio.Event) -> None:
    """Main feature enrichment loop."""
    gateway_url, gateway_api_key = _gateway_runtime_contract()
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

    logger.info("Feature Enrichment Service started")

    while not shutdown_event.is_set():
        try:
            now = datetime.now(timezone.utc)
            tickers = await get_active_tickers()

            # Market Tide - every minute
            if (now - last_tide).total_seconds() >= MARKET_TIDE_INTERVAL:
                count = await tide_connector.fetch_and_store()
                logger.info(f"Market Tide: stored {count} ticks")
                last_tide = now

            # Greek Exposure - every 5 minutes
            if (now - last_greek).total_seconds() >= GREEK_EXPOSURE_INTERVAL:
                count = await greek_connector.fetch_and_store(tickers)
                logger.info(f"Greek Exposure: stored {count} records for {len(tickers)} tickers")
                last_greek = now

            # Max Pain - every hour
            if (now - last_max_pain).total_seconds() >= MAX_PAIN_INTERVAL:
                count = await max_pain_connector.fetch_and_store(tickers)
                logger.info(f"Max Pain: stored {count} records")
                last_max_pain = now

            # IV Rank - every 15 minutes
            if (now - last_iv).total_seconds() >= IV_RANK_INTERVAL:
                count = await iv_connector.fetch_and_store(tickers)
                logger.info(f"IV Rank: stored {count} records")
                last_iv = now

            # VIX Data - every hour
            if (now - last_vix).total_seconds() >= VIX_DATA_INTERVAL:
                try:
                    count = await vix_connector.fetch_and_store()
                    logger.info(f"VIX Proxy: stored {count} records")
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

        except Exception as e:
            logger.error(f"Feature enrichment error: {e}", exc_info=True)

        # Wait before next iteration
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=30)
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
