"""
Price Target Labeling Service.

Tracks option prices over time with comprehensive metrics for ML exit optimization.
"""

import asyncio
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.price_target")

BATCH_SIZE = 50
POLL_INTERVAL_SECONDS = 60


def parse_expiry(expiry_str: Optional[str]) -> Optional[datetime]:
    """Parse expiry string to datetime."""
    if not expiry_str:
        return None
    try:
        return datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def calculate_dte(flow_ts: datetime, expiry: Optional[datetime]) -> Optional[int]:
    """Calculate days to expiry."""
    if not expiry:
        return None
    dte = (expiry.date() - flow_ts.date()).days
    return max(0, dte)


def classify_trade_type(dte: Optional[int]) -> str:
    """Classify trade type based on DTE."""
    if dte is None:
        return "UNKNOWN"
    if dte == 0:
        return "0DTE"
    elif dte <= 3:
        return "SHORT_SWING"
    elif dte <= 14:
        return "SWING"
    return "POSITION"


async def get_entry_signals(limit: int = BATCH_SIZE) -> List[Any]:
    """Get sweep entries that haven't been labeled for price targets yet."""

    async def query(session: Any) -> List[Any]:
        stmt = text(
            """
            SELECT f.*
            FROM silver_uw_flow f
            LEFT JOIN price_target_labels p ON f.event_id = p.event_id
            WHERE p.event_id IS NULL
            AND f.option_chain IS NOT NULL
            AND f.option_price > 0
            AND f.is_sweep = 'true'
            AND f.aggressor = 'ASK'
            AND f.premium_usd >= 50000
            ORDER BY f.flow_ts_utc ASC
            LIMIT :limit
        """
        )
        result = await session.execute(stmt, {"limit": limit})
        return result.fetchall()

    return await db_query(query)


async def get_subsequent_prices(option_chain: str, entry_ts: datetime) -> List[Dict[str, Any]]:
    """Get all subsequent prices for an option chain after entry."""

    async def query(session: Any) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT option_price, flow_ts_utc
            FROM silver_uw_flow
            WHERE option_chain = :option_chain
            AND flow_ts_utc > :entry_ts
            AND option_price > 0
            ORDER BY flow_ts_utc ASC
        """
        )
        result = await session.execute(stmt, {"option_chain": option_chain, "entry_ts": entry_ts})
        return [{"price": row[0], "ts": row[1]} for row in result.fetchall()]

    return await db_query(query)


async def get_opposing_flow(ticker: str, put_call: str, entry_ts: datetime, end_ts: datetime) -> Dict[str, Any]:
    """Get opposing flow during holding period."""
    opposing_type = "P" if put_call == "C" else "C"

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT COUNT(*) as count, COALESCE(SUM(premium_usd), 0) as total_premium
            FROM silver_uw_flow
            WHERE ticker = :ticker
            AND put_call = :opposing_type
            AND flow_ts_utc > :entry_ts
            AND flow_ts_utc <= :end_ts
            AND is_sweep = 'true'
            AND aggressor = 'ASK'
        """
        )
        result = await session.execute(
            stmt,
            {
                "ticker": ticker,
                "opposing_type": opposing_type,
                "entry_ts": entry_ts,
                "end_ts": end_ts,
            },
        )
        row = result.fetchone()
        return {"count": row[0] or 0, "premium": row[1] or 0}

    return await db_query(query)


async def get_gex_at_entry(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get the closest GEX values before entry time."""

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT gex_oi, vex_oi, spot_price
            FROM silver_greek_exposure
            WHERE ticker = :ticker AND ts_utc <= :entry_ts
            ORDER BY ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
        row = result.fetchone()
        return {"gex": row[0], "vex": row[1]} if row else {"gex": None, "vex": None}

    return await db_query(query)


async def get_market_tide_before_entry(entry_ts: datetime, minutes: int = 30) -> Dict[str, Any]:
    """Get market tide sum for the period before entry."""
    start_ts = entry_ts - timedelta(minutes=minutes)

    async def query(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT COALESCE(SUM(net_call_premium), 0), COALESCE(SUM(net_put_premium), 0)
            FROM silver_market_tide
            WHERE ts_utc > :start_ts AND ts_utc <= :entry_ts
        """
        )
        result = await session.execute(stmt, {"start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()
        if row:
            net = float(row[0] or 0) + float(row[1] or 0)
            direction = "BULLISH" if net > 0 else "BEARISH" if net < 0 else "NEUTRAL"
            return {"net_premium": net, "direction": direction}
        return {"net_premium": None, "direction": None}

    return await db_query(query)


async def get_max_pain_distance(ticker: str, expiry_date: Optional[datetime], entry_ts: datetime) -> Optional[float]:
    """Get distance to max pain at entry time."""
    if not expiry_date:
        return None

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT distance_to_max_pain_pct FROM silver_max_pain
            WHERE ticker = :ticker AND expiry = :expiry AND date <= :entry_date
            ORDER BY date DESC LIMIT 1
        """
        )
        result = await session.execute(
            stmt,
            {
                "ticker": ticker,
                "expiry": expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date,
                "entry_date": entry_ts.date(),
            },
        )
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_iv_rank_at_entry(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get IV rank at entry time."""

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT iv_rank FROM silver_iv_rank
            WHERE ticker = :ticker AND ts_utc <= :entry_ts
            ORDER BY ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_regime_at_entry(entry_ts: datetime) -> Dict[str, Any]:
    """Get regime snapshot at entry time from VIX data + market tide."""
    from orion.analysis.regime import MultiAxisRegimeDetector

    detector = MultiAxisRegimeDetector()

    # Get VIX data
    async def query_vix(session: Any) -> Dict[str, Any]:
        stmt = text(
            """
            SELECT vix, vix_1d_change, vix_regime
            FROM silver_vix_data
            WHERE ts_utc <= :entry_ts
            ORDER BY ts_utc DESC LIMIT 1
        """
        )
        result = await session.execute(stmt, {"entry_ts": entry_ts})
        row = result.fetchone()
        return {"vix": row[0], "vix_1d_change": row[1], "vix_regime": row[2]} if row else {}

    # Get market tide sum for risk scoring
    async def query_tide(session: Any) -> Optional[float]:
        start_ts = entry_ts - timedelta(minutes=30)
        stmt = text(
            """
            SELECT COALESCE(SUM(net_call_premium), 0) + COALESCE(SUM(net_put_premium), 0)
            FROM silver_market_tide
            WHERE ts_utc > :start_ts AND ts_utc <= :entry_ts
        """
        )
        result = await session.execute(stmt, {"start_ts": start_ts, "entry_ts": entry_ts})
        row = result.fetchone()
        return float(row[0]) if row and row[0] else None

    vix_data = await db_query(query_vix)
    tide_net = await db_query(query_tide)

    # Detect regime snapshot
    snapshot = detector.detect(
        ts=entry_ts,
        vix=vix_data.get("vix"),
        vix_1d_change=vix_data.get("vix_1d_change"),
        market_tide_net=tide_net,
    )

    return {
        "trend_regime": snapshot.trend.value,
        "vol_regime": snapshot.vol.value,
        "risk_regime": snapshot.risk.value,
        "session_regime": snapshot.session.value,
        "vix_at_entry": snapshot.vix_level,
        "vix_regime": snapshot.vix_regime.value,
    }


def get_price_at_offset(prices: List[Dict[str, Any]], entry_ts: datetime, hours: int) -> Optional[float]:
    """Get price at a specific time offset from entry."""
    target_ts = entry_ts + timedelta(hours=hours)
    closest = None
    min_diff = timedelta(minutes=30)  # Accept within 30 min window

    for p in prices:
        diff = abs(p["ts"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest = p["price"]
    return closest


def calculate_volatility(prices: List[float]) -> Optional[float]:
    """Calculate price volatility (std dev of returns)."""
    if len(prices) < 3:
        return None
    try:
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
        return float(np.std(returns)) if returns else None
    except (ZeroDivisionError, ValueError):
        return None


async def label_entry(entry: Any) -> Optional[Dict[str, Any]]:
    """Label a single entry with comprehensive price target tracking."""
    option_chain = entry.option_chain
    entry_price = entry.option_price
    entry_ts = entry.flow_ts_utc
    ticker = entry.ticker
    put_call = entry.put_call

    if entry_price <= 0:
        return None

    prices = await get_subsequent_prices(option_chain, entry_ts)
    expiry = parse_expiry(entry.expiry)
    dte = calculate_dte(entry_ts, expiry)

    # Base label with nulls
    label = {
        "event_id": entry.event_id,
        "ticker": ticker,
        "option_chain": option_chain,
        "trade_type": classify_trade_type(dte),
        "entry_ts": entry_ts,
        "entry_option_price": entry_price,
        "expiry": expiry,
        "dte": dte,
        "premium_usd": entry.premium_usd,
        "aggressor": entry.aggressor,
        "put_call": put_call,
        "is_sweep": entry.is_sweep == "true" if isinstance(entry.is_sweep, str) else entry.is_sweep,
    }

    if not prices:
        # No subsequent data - still lookup entry features
        gex_data = await get_gex_at_entry(ticker, entry_ts)
        tide_data = await get_market_tide_before_entry(entry_ts, minutes=30)
        max_pain_dist = await get_max_pain_distance(ticker, expiry, entry_ts)
        iv_rank = await get_iv_rank_at_entry(ticker, entry_ts)

        for key in [
            "max_price_reached",
            "max_price_ts",
            "max_return_pct",
            "min_price_reached",
            "min_price_ts",
            "max_drawdown_pct",
            "hit_50_pct_ts",
            "hit_75_pct_ts",
            "hit_100_pct_ts",
            "hit_150_pct_ts",
            "hit_stop_20_pct_ts",
            "first_exit_ts",
            "first_exit_return_pct",
            "time_to_max_seconds",
            "time_to_50_pct_seconds",
            "time_to_stop_seconds",
            "holding_period_seconds",
            "max_drawdown_before_target",
            "min_distance_to_stop_pct",
            "price_volatility",
            "price_at_1h",
            "price_at_2h",
            "price_at_4h",
            "return_at_1h",
            "return_at_2h",
            "return_at_4h",
            "opposing_flow_count",
            "opposing_premium_total",
            "sentiment_shift_ts",
            "optimal_exit_return",
            "optimal_exit_ts",
            "final_return_pct",
        ]:
            label[key] = None
        label["first_exit_type"] = "NONE"
        label["last_tracked_ts"] = entry_ts
        label["gex_at_entry"] = gex_data["gex"]
        label["vex_at_entry"] = gex_data["vex"]
        label["market_tide_30m"] = tide_data["net_premium"]
        label["market_tide_direction"] = tide_data["direction"]
        label["max_pain_distance_pct"] = max_pain_dist
        label["iv_rank_at_entry"] = iv_rank
        label["darkpool_volume_1h"] = None
        # Regime at entry
        regime_data = await get_regime_at_entry(entry_ts)
        label["trend_regime_at_entry"] = regime_data.get("trend_regime")
        label["vol_regime_at_entry"] = regime_data.get("vol_regime")
        label["risk_regime_at_entry"] = regime_data.get("risk_regime")
        label["session_regime_at_entry"] = regime_data.get("session_regime")
        label["vix_at_entry"] = regime_data.get("vix_at_entry")
        label["vix_regime_at_entry"] = regime_data.get("vix_regime")
        return label

    # Track core metrics
    max_price = entry_price
    max_price_ts = entry_ts
    min_price = entry_price
    min_price_ts = entry_ts

    hit_50_ts = None
    hit_75_ts = None
    hit_100_ts = None
    hit_150_ts = None
    hit_stop_ts = None

    first_exit_type = "NONE"
    first_exit_ts = None
    first_exit_return = None

    # Track drawdown before hitting target
    max_drawdown_before_50 = 0.0
    min_distance_to_stop = 20.0  # Start at stop level

    all_prices = [entry_price] + [p["price"] for p in prices]

    for p in prices:
        price = p["price"]
        ts = p["ts"]
        return_pct = ((price - entry_price) / entry_price) * 100

        # Track extremes
        if price > max_price:
            max_price = price
            max_price_ts = ts
        if price < min_price:
            min_price = price
            min_price_ts = ts

        # Track drawdown before 50% target
        if hit_50_ts is None and return_pct < 0:
            max_drawdown_before_50 = min(max_drawdown_before_50, return_pct)

        # Track how close to stop
        if return_pct < 0 and return_pct > -20:
            distance_to_stop = 20.0 + return_pct  # Distance from -20%
            min_distance_to_stop = min(min_distance_to_stop, distance_to_stop)

        # Check targets
        if return_pct >= 50 and hit_50_ts is None:
            hit_50_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_50"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct >= 75 and hit_75_ts is None:
            hit_75_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_75"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct >= 100 and hit_100_ts is None:
            hit_100_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_100"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct >= 150 and hit_150_ts is None:
            hit_150_ts = ts
            if first_exit_ts is None:
                first_exit_type = "TARGET_150"
                first_exit_ts = ts
                first_exit_return = return_pct

        if return_pct <= -20 and hit_stop_ts is None:
            hit_stop_ts = ts
            if first_exit_ts is None:
                first_exit_type = "STOP_20"
                first_exit_ts = ts
                first_exit_return = return_pct

    # Calculate derived metrics
    max_return_pct = ((max_price - entry_price) / entry_price) * 100
    max_drawdown_pct = ((min_price - entry_price) / entry_price) * 100
    last_tracked_ts = prices[-1]["ts"]
    final_return_pct = ((prices[-1]["price"] - entry_price) / entry_price) * 100

    # Timing metrics
    time_to_max = int((max_price_ts - entry_ts).total_seconds()) if max_price_ts != entry_ts else None
    time_to_50 = int((hit_50_ts - entry_ts).total_seconds()) if hit_50_ts else None
    time_to_stop = int((hit_stop_ts - entry_ts).total_seconds()) if hit_stop_ts else None
    holding_period = int((last_tracked_ts - entry_ts).total_seconds())

    # Price at checkpoints
    price_1h = get_price_at_offset(prices, entry_ts, 1)
    price_2h = get_price_at_offset(prices, entry_ts, 2)
    price_4h = get_price_at_offset(prices, entry_ts, 4)

    return_1h = ((price_1h - entry_price) / entry_price * 100) if price_1h else None
    return_2h = ((price_2h - entry_price) / entry_price * 100) if price_2h else None
    return_4h = ((price_4h - entry_price) / entry_price * 100) if price_4h else None

    # Volatility
    volatility = calculate_volatility(all_prices)

    # Opposing flow
    opposing = await get_opposing_flow(ticker, put_call, entry_ts, last_tracked_ts)

    # Build full label
    label.update(
        {
            "max_price_reached": max_price,
            "max_price_ts": max_price_ts,
            "max_return_pct": max_return_pct,
            "min_price_reached": min_price,
            "min_price_ts": min_price_ts,
            "max_drawdown_pct": max_drawdown_pct,
            "hit_50_pct_ts": hit_50_ts,
            "hit_75_pct_ts": hit_75_ts,
            "hit_100_pct_ts": hit_100_ts,
            "hit_150_pct_ts": hit_150_ts,
            "hit_stop_20_pct_ts": hit_stop_ts,
            "first_exit_type": first_exit_type,
            "first_exit_ts": first_exit_ts,
            "first_exit_return_pct": first_exit_return,
            "last_tracked_ts": last_tracked_ts,
            # Timing
            "time_to_max_seconds": time_to_max,
            "time_to_50_pct_seconds": time_to_50,
            "time_to_stop_seconds": time_to_stop,
            "holding_period_seconds": holding_period,
            # Price path
            "max_drawdown_before_target": max_drawdown_before_50 if max_drawdown_before_50 < 0 else None,
            "min_distance_to_stop_pct": min_distance_to_stop if min_distance_to_stop < 20 else None,
            "price_volatility": volatility,
            "price_at_1h": price_1h,
            "price_at_2h": price_2h,
            "price_at_4h": price_4h,
            "return_at_1h": return_1h,
            "return_at_2h": return_2h,
            "return_at_4h": return_4h,
            # Context
            "opposing_flow_count": opposing["count"],
            "opposing_premium_total": opposing["premium"],
            "sentiment_shift_ts": None,
            # Exit quality
            "optimal_exit_return": max_return_pct,
            "optimal_exit_ts": max_price_ts,
            "final_return_pct": final_return_pct,
        }
    )

    # Lookup entry features from feature tables
    gex_data = await get_gex_at_entry(ticker, entry_ts)
    tide_data = await get_market_tide_before_entry(entry_ts, minutes=30)
    max_pain_dist = await get_max_pain_distance(ticker, expiry, entry_ts)
    iv_rank = await get_iv_rank_at_entry(ticker, entry_ts)

    label.update(
        {
            "gex_at_entry": gex_data["gex"],
            "vex_at_entry": gex_data["vex"],
            "market_tide_30m": tide_data["net_premium"],
            "market_tide_direction": tide_data["direction"],
            "max_pain_distance_pct": max_pain_dist,
            "iv_rank_at_entry": iv_rank,
            "darkpool_volume_1h": None,
        }
    )

    # Lookup regime at entry
    regime_data = await get_regime_at_entry(entry_ts)
    label.update(
        {
            "trend_regime_at_entry": regime_data.get("trend_regime"),
            "vol_regime_at_entry": regime_data.get("vol_regime"),
            "risk_regime_at_entry": regime_data.get("risk_regime"),
            "session_regime_at_entry": regime_data.get("session_regime"),
            "vix_at_entry": regime_data.get("vix_at_entry"),
            "vix_regime_at_entry": regime_data.get("vix_regime"),
        }
    )

    return label


async def persist_labels(labels: List[Dict[str, Any]]) -> int:
    """Persist labeled records to database."""
    if not labels:
        return 0

    async def write(session: Any) -> None:
        stmt = text(
            """
            INSERT INTO price_target_labels (
                event_id, ticker, option_chain, trade_type,
                entry_ts, entry_option_price, expiry, dte,
                premium_usd, aggressor, put_call, is_sweep,
                max_price_reached, max_price_ts, max_return_pct,
                min_price_reached, min_price_ts, max_drawdown_pct,
                hit_50_pct_ts, hit_75_pct_ts, hit_100_pct_ts, hit_150_pct_ts,
                hit_stop_20_pct_ts,
                first_exit_type, first_exit_ts, first_exit_return_pct,
                last_tracked_ts,
                time_to_max_seconds, time_to_50_pct_seconds, time_to_stop_seconds, holding_period_seconds,
                max_drawdown_before_target, min_distance_to_stop_pct, price_volatility,
                price_at_1h, price_at_2h, price_at_4h,
                return_at_1h, return_at_2h, return_at_4h,
                opposing_flow_count, opposing_premium_total, sentiment_shift_ts,
                optimal_exit_return, optimal_exit_ts, final_return_pct,
                gex_at_entry, vex_at_entry, market_tide_30m, market_tide_direction,
                max_pain_distance_pct, iv_rank_at_entry, darkpool_volume_1h,
                trend_regime_at_entry, vol_regime_at_entry, risk_regime_at_entry,
                session_regime_at_entry, vix_at_entry, vix_regime_at_entry
            ) VALUES (
                :event_id, :ticker, :option_chain, :trade_type,
                :entry_ts, :entry_option_price, :expiry, :dte,
                :premium_usd, :aggressor, :put_call, :is_sweep,
                :max_price_reached, :max_price_ts, :max_return_pct,
                :min_price_reached, :min_price_ts, :max_drawdown_pct,
                :hit_50_pct_ts, :hit_75_pct_ts, :hit_100_pct_ts, :hit_150_pct_ts,
                :hit_stop_20_pct_ts,
                :first_exit_type, :first_exit_ts, :first_exit_return_pct,
                :last_tracked_ts,
                :time_to_max_seconds, :time_to_50_pct_seconds, :time_to_stop_seconds, :holding_period_seconds,
                :max_drawdown_before_target, :min_distance_to_stop_pct, :price_volatility,
                :price_at_1h, :price_at_2h, :price_at_4h,
                :return_at_1h, :return_at_2h, :return_at_4h,
                :opposing_flow_count, :opposing_premium_total, :sentiment_shift_ts,
                :optimal_exit_return, :optimal_exit_ts, :final_return_pct,
                :gex_at_entry, :vex_at_entry, :market_tide_30m, :market_tide_direction,
                :max_pain_distance_pct, :iv_rank_at_entry, :darkpool_volume_1h,
                :trend_regime_at_entry, :vol_regime_at_entry, :risk_regime_at_entry,
                :session_regime_at_entry, :vix_at_entry, :vix_regime_at_entry
            )
            ON CONFLICT (event_id) DO NOTHING
        """
        )

        for label in labels:
            await session.execute(stmt, label)

    await db_write(write)
    return len(labels)


async def run_labeling_loop(shutdown_event: asyncio.Event) -> None:
    """Main labeling loop."""
    await init_db()

    logger.info("Starting Price Target Labeling Service (v2 - comprehensive metrics)...")

    total_labeled = 0

    while not shutdown_event.is_set():
        try:
            entries = await get_entry_signals(BATCH_SIZE)

            if entries:
                labels = []
                for entry in entries:
                    label = await label_entry(entry)
                    if label:
                        labels.append(label)

                if labels:
                    count = await persist_labels(labels)
                    total_labeled += count

                    hit_50 = sum(1 for label in labels if label.get("hit_50_pct_ts"))
                    stopped = sum(1 for label in labels if label.get("hit_stop_20_pct_ts"))
                    avg_holding = sum(label.get("holding_period_seconds", 0) or 0 for label in labels) / len(labels)

                    logger.info(
                        f"Labeled {count} entries | Total: {total_labeled} | "
                        f"Hit50: {hit_50} | Stopped: {stopped} | AvgHold: {avg_holding/60:.0f}min",
                        extra={
                            "event_type": "BATCH_LABELED",
                            "batch_size": count,
                            "hit_50_pct": hit_50,
                            "stopped_out": stopped,
                            "avg_holding_min": avg_holding / 60,
                        },
                    )
            else:
                logger.debug("No unlabeled entries found, waiting...")

        except Exception as e:
            logger.error(f"Labeling error: {e}", exc_info=True)
            await asyncio.sleep(5)
            continue

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

    logger.info(f"Price Target Labeling stopped. Total: {total_labeled}")


async def main() -> None:
    """Main entry point."""
    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def handle_signal(sig: int) -> None:
        logger.info(f"Received signal {sig}. Shutting down...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    await run_labeling_loop(shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())
