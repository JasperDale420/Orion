"""
Position Monitor.

Monitors open positions and triggers exits based on ML exit classifier
and rule-based exit signals.
"""

import asyncio
import resource
import sys
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

from orion.ml.exit_classifier import (
    BucketExitClassifier,
    ExitFeatures,
    ExitPrediction,
    get_exit_classifier,
)
from orion.shared.db_utils import db_query
from orion.shared.liveness import publish_liveness
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.execution.position_monitor")

# Liveness cadence budget for the position-monitor loop (default 60s tick); the
# dead-man watchdog alerts if no successful check lands within 300s.
POSITION_MONITOR_LIVENESS_CADENCE_BUDGET_SECONDS = 300

# OCC option-symbol pattern: ROOT (1-6 alphanumeric) + YYMMDD + C/P + 8-digit strike.
# Example: QQQ260522P00721000, GDX260618C00086000.
# OCC symbol detection now lives in `orion.execution.attribution`
# (shared so `execution_engine.py` can use it without circular
# imports). Re-exported here under the old private name so the rest
# of this module's call sites continue to work.
from orion.execution.attribution import is_occ_option_symbol as _is_occ_option_symbol  # noqa: E402
from orion.execution.persistence import (  # noqa: E402
    load_position_running_stats,
    upsert_position_running_stats,
)


def _dte_from_occ_symbol(symbol: str) -> int | None:
    """Calendar DTE (from today, UTC) parsed from an OCC symbol's expiry."""
    from orion.shared.utils import parse_occ_symbol

    expiry_str = parse_occ_symbol(symbol).get("expiry")
    if not isinstance(expiry_str, str):
        return None
    try:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (expiry - datetime.now(UTC).date()).days


def _expiry_from_occ_symbol(symbol: str | None) -> datetime | None:
    """Expiry (tz-aware UTC) parsed from an OCC symbol, or None for equity.

    The expiry is encoded in the contract symbol, so it is always derivable
    without a database round-trip. `TimeToExpiryRule` silently no-ops on a
    missing `expiry_date`, and the entry-context join has fallbacks that omit it
    (no matching decision, fetch error, timeout) — so relying on the DB alone
    left positions with no time-stop at all, riding to expiry where Alpaca
    auto-exercises the ITM ones into equity Orion never opened.
    """
    from orion.shared.utils import parse_occ_symbol

    if not symbol:
        return None
    expiry_str = parse_occ_symbol(symbol).get("expiry")
    if not isinstance(expiry_str, str):
        return None
    try:
        parsed = datetime.strptime(expiry_str, "%Y-%m-%d")
    except ValueError:
        return None
    # MIDNIGHT UTC, matching how `candidate_trades.expiration_date` is stored,
    # so the fallback and the DB path produce identical exit timing. End-of-day
    # would push the rule's 24h-multiple comparison past the intended session:
    # a Friday expiry with min_dte=2 would only reach the threshold at 19:59 ET
    # Wednesday — after the close — leaving expiry day as the next chance.
    return parsed.replace(tzinfo=UTC)


def _bucket_from_occ_symbol(symbol: str) -> str | None:
    """Bucket derived from the OCC symbol's embedded expiry (current DTE).

    Current DTE biases an aged position toward the TIGHTER bucket, which
    fails safe: exits fire earlier and the 0DTE hard flatten still arms.
    """
    dte = _dte_from_occ_symbol(symbol)
    if dte is None:
        return None
    from orion.execution.exit_fallback_rules import bucket_for_dte

    return bucket_for_dte(dte)


async def _run_coroutine_in_thread(coro: Coroutine[Any, Any, Any]) -> Any:
    """Await ``coro`` on a private event loop inside a worker thread.

    For work that is nominally async but whose implementation blocks on
    synchronous I/O. Running it on its own loop keeps the caller's loop free to
    service timers and other coroutines, which is what makes an enclosing
    timeout enforceable at all.
    """
    return await asyncio.to_thread(asyncio.run, coro)


def _process_rss_mb() -> float:
    """Resident-set high-water mark for this process, in MiB.

    ``ru_maxrss`` is bytes on macOS and kibibytes on Linux.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(peak / divisor, 1)


def _is_exercise_residue_candidate(symbol: str | None, orion_option_underlyings: set[str]) -> bool:
    """True when an unattributed EQUITY position looks like Orion's own exercise.

    Alpaca auto-exercises ITM long options at expiry, producing an equity
    position with no order and no fill — so it never appears in
    `_fetch_orion_attributed_tickers` and Orion cannot see, size against, or
    exit it (the exit path is options-only).

    Attribution works off Orion's own option history rather than the fills
    ledger, because the fill record for the originating contract may be missing
    (that is exactly how BABA went unnoticed). The shared account makes this a
    judgement call, so it only ever produces an ALERT — never silent adoption of
    a position that might belong to a sibling system.
    """
    if not symbol or _is_occ_option_symbol(symbol):
        return False
    return symbol in orion_option_underlyings


async def _fetch_orion_option_underlyings() -> set[str]:
    """Underlyings Orion has ever traded options on, from its own candidates."""
    from sqlalchemy import select

    from orion.shared.db_utils import db_query
    from orion.storage.models_gold import CandidateTrade

    async def _read(session: Any) -> list[Any]:
        stmt = select(CandidateTrade.ticker).where(CandidateTrade.option_symbol.is_not(None)).distinct()
        return list((await session.execute(stmt)).scalars().all())

    try:
        return {t for t in await db_query(_read) if t}
    except Exception as exc:
        # Non-fatal: this only drives an advisory alert.
        logger.warning(f"Could not load Orion option underlyings for exercise-residue check: {exc}")
        return set()


async def _fetch_orion_attributed_tickers() -> set[str]:
    """Return the set of distinct broker symbols Orion has actually filled.

    Sourced from the ``fills`` table (NOT ``orders``). For options,
    ``fills.ticker`` stores the FULL OCC contract symbol
    (e.g. ``AAPL260529P00315000``) — so the returned set lets the
    adapter do per-contract attribution. For equity, ``fills.ticker``
    is the underlying (e.g. ``AAPL``), which is also what the broker
    reports as the position symbol. Both cases match directly without
    any derivation.

    Per-contract attribution is what closes codex review 2026-05-26's
    CRITICAL finding on commit 39174f8: the previous implementation
    queried ``orders.ticker`` (which stores the UNDERLYING for both
    equity and options) and admitted ANY broker option whose
    underlying ever appeared in Orion's order history. With sibling
    systems trading on the shared Alpaca account, that meant
    "Orion bought AAPL puts last week" allowed a sibling's
    AAPL CALL today (different OCC contract) to be classified as
    Orion-owned — and the position monitor would route it to
    ``close_position``, closing a position Orion doesn't own.

    Mirrors ``ExecutionEngine._fetch_orion_tickers``. Keeping the
    implementation here as well (rather than importing from
    execution_engine) avoids a circular import.

    Returns an empty set if the query fails; callers MUST treat that
    as "no orion attribution data" rather than "no positions to filter
    out" — the latter would silently absorb other systems' positions
    back into Orion's tracker.
    """
    from sqlalchemy import select

    from orion.execution.attribution import orion_order_id_sql_pattern
    from orion.shared.db_utils import db_query
    from orion.storage.models_execution import FillRecord

    async def query_tickers(session: Any) -> set[str]:
        stmt = select(FillRecord.ticker).where(FillRecord.client_order_id.like(orion_order_id_sql_pattern())).distinct()
        result = await session.execute(stmt)
        return {row[0] for row in result.all()}

    return await db_query(query_tickers)


class GatewayPositionAdapter:
    """Adapts GatewayTradingClient.get_positions() dicts to the attribute-based
    interface expected by PositionMonitor.sync_positions().

    sync_positions expects a connector with ``get_all_positions()`` returning
    objects that have ``.symbol``, ``.current_price``, ``.avg_entry_price``,
    ``.qty``, and ``.unrealized_plpc`` attributes.

    SHARED-ACCOUNT FILTER — observed live 2026-05-21 — the Alpaca paper
    account is shared by multiple Empire systems (3Roses, Cerberus, Kairos,
    Orbit, WhaleHunter, Orion). Without filtering at this layer, the
    position-monitor tracker absorbed every broker position
    (30 of 30 entries were ghosts; ExecutionEngine separately reported
    "open_positions=0 skipped_non_orion=38" using its own filter pattern).
    `refresh()` now consults the orders table for distinct
    `orion_`-prefixed `client_order_id` tickers and drops any broker
    position whose symbol isn't in that set. Default-deny on DB failure.
    """

    def __init__(self, gateway_client: Any) -> None:
        self._client = gateway_client
        self._cached_positions: list[SimpleNamespace] = []
        # Symbols already alerted on as likely exercise residue, so a standing
        # position doesn't page every refresh.
        self._residue_alerted: set[str] = set()

    def get_all_positions(self) -> list[SimpleNamespace]:
        """Synchronous wrapper — the actual fetch happens in `refresh()`,
        which the monitor loop awaits before calling sync_positions."""
        return self._cached_positions

    async def refresh(self) -> None:
        """Fetch positions from Gateway, filter to Orion-attributed only,
        and cache as SimpleNamespace objects.

        Default-deny semantics:
          - DB query raises → cache empty list (NEVER fall back to
            unfiltered raw positions).
          - No orion-prefixed orders in DB → cache empty list.
          - Gateway returns positions for tickers we don't have orders
            for → those positions are excluded.

        This is the same shape `ExecutionEngine._sync_risk_from_gateway`
        uses for its risk-side filter, just at the position-monitor
        layer too.
        """
        raw = await self._client.get_positions()

        try:
            orion_tickers = await _fetch_orion_attributed_tickers()
        except Exception as exc:
            logger.error(
                "Failed to fetch Orion-owned tickers for shared-account "
                "position filtering; defaulting to EMPTY position list to "
                "avoid absorbing other systems' positions",
                extra={
                    "event_type": "POSITION_ADAPTER_FILTER_FAILED",
                    "error": str(exc),
                    "raw_broker_position_count": len(raw),
                },
            )
            self._cached_positions = []
            return

        filtered: list[SimpleNamespace] = []
        unattributed: list[dict[str, Any]] = []
        for p in raw:
            symbol = p.get("symbol", "")
            # `orion_tickers` now comes from `fills.ticker`, which stores
            # the FULL OCC contract for options (e.g. AAPL260529P00315000)
            # and the underlying for equity (e.g. AAPL). Both match the
            # broker's position symbol verbatim, so a direct membership
            # check is correct AND safe — a sibling system's AAPL option
            # on a different contract won't be in the set even if Orion
            # has traded other AAPL contracts. Codex review 2026-05-26.
            if symbol in orion_tickers:
                filtered.append(
                    SimpleNamespace(
                        symbol=symbol,
                        current_price=p.get("current_price", 0),
                        avg_entry_price=p.get("avg_entry_price", 0),
                        qty=p.get("qty", 0),
                        unrealized_plpc=p.get("unrealized_plpc", 0),
                    )
                )
            else:
                unattributed.append(p)

        await self._alert_exercise_residue(unattributed)
        self._cached_positions = filtered

        if len(filtered) != len(raw):
            logger.info(
                f"PositionAdapter filtered {len(raw) - len(filtered)} non-Orion "
                f"positions from shared account; kept {len(filtered)} Orion-attributed",
                extra={
                    "event_type": "POSITION_ADAPTER_FILTERED",
                    "raw_count": len(raw),
                    "kept_count": len(filtered),
                    "skipped_count": len(raw) - len(filtered),
                },
            )

    async def _alert_exercise_residue(self, unattributed: list[dict[str, Any]]) -> None:
        """Alert on equity that looks like Orion's own auto-exercised option.

        Advisory only — it never adopts the position, because on a shared
        account the evidence is circumstantial. Alerts once per symbol per
        process so a standing position does not page every 60s.
        """
        equities = [p for p in unattributed if not _is_occ_option_symbol(p.get("symbol", ""))]
        # Release the dedup entry once a position is gone, so a LATER exercise
        # on the same underlying alerts again instead of being suppressed for
        # the life of the process (and so a false positive can't mask a real
        # residue later).
        present = {p.get("symbol", "") for p in equities}
        self._residue_alerted &= present
        if not equities:
            return
        candidates = [p for p in equities if p.get("symbol") not in self._residue_alerted]
        if not candidates:
            return

        underlyings = await _fetch_orion_option_underlyings()
        for p in candidates:
            symbol = p.get("symbol", "")
            if not _is_exercise_residue_candidate(symbol, underlyings):
                continue
            self._residue_alerted.add(symbol)
            # Deliberately does NOT instruct a close. The account is shared, and
            # Orion's own order/decision records for an exercised contract may
            # be missing (BABA had 11 candidates but 0 EXECUTE rows), so this
            # cannot be strengthened into proof of ownership from local data —
            # it points the operator at the authoritative check instead.
            logger.critical(
                f"UNATTRIBUTED EQUITY {symbol}: Orion has traded options on this underlying and cannot "
                "see, size against, or exit an equity position (its exit path is options-only). This may "
                "be its own ITM contract auto-exercised at expiry, or a sibling system's position. "
                "VERIFY before acting: GET /api/v1/alpaca/account/activities?activity_types=OPEXC (and "
                "OPASN) and match the contract's underlying, quantity (contracts x 100) and strike+premium "
                "against this position's avg_entry_price.",
                extra={
                    "event_type": "EXERCISE_RESIDUE_DETECTED",
                    "symbol": symbol,
                    "qty": p.get("qty"),
                    "market_value": p.get("market_value"),
                    "avg_entry_price": p.get("avg_entry_price"),
                },
            )


@dataclass
class TrackedPosition:
    """Position with tracking metadata for exit decisions."""

    symbol: str
    qty: float
    entry_price: float
    current_price: float
    unrealized_pnl_pct: float
    entry_time: datetime
    bucket: str  # 0DTE, SHORT_SWING, SWING, POSITION
    direction: str = "LONG"  # "LONG" or "SHORT"

    # Tracking state
    max_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    # Entry context (from original trade)
    premium_usd: float | None = None
    dte_at_entry: int | None = None
    is_sweep: bool = False
    iv_rank_at_entry: float | None = None
    vix_at_entry: float | None = None
    gex_at_entry: float | None = None
    market_tide_30m: float | None = None

    # Additional
    decision_id: str | None = None
    option_symbol: str | None = None  # For options positions

    # Optional — populated by sync_positions when the parent decision/order
    # carries it. TimeToExpiryRule no-ops when None (graceful), so it's
    # safe to leave unset on positions where we don't have the data.
    expiry_date: datetime | None = None


class PositionMonitor:
    """
    Monitors open positions and evaluates exit signals.

    Uses:
    - ML exit classifier (bucket-specific)
    - Heuristic fallbacks
    - Position tracking (max return, max drawdown)
    """

    def __init__(
        self,
        execution_engine: Any | None = None,
        position_manager: Any | None = None,
    ) -> None:
        self.exit_classifier: BucketExitClassifier = get_exit_classifier()
        self.tracked_positions: dict[str, TrackedPosition] = {}
        self._last_check_time: datetime | None = None
        self._execution_engine = execution_engine
        self._position_manager = position_manager
        # Per-symbol consecutive failed-close counter. Bounds retries so a
        # single stuck position can't fire an unbounded stream of close
        # attempts every cycle (2026-05-29: ~3,235 rejected order-creates/day).
        # Paired with a per-symbol last-failure timestamp so abandonment is
        # time-bounded, not permanent (RCA 2026-06-05: a +320% MU winner was
        # stranded unclosable after 5 wash-trade rejections exhausted the
        # counter and nothing ever reset it short of a process restart).
        self._consecutive_close_failures: dict[str, int] = {}
        self._close_failure_ts: dict[str, float] = {}
        # Per-symbol entry-context cache. Populated lazily on first
        # _fetch_entry_context call and reused for the lifetime of the
        # PositionMonitor — entry-time market context (IV rank, VIX,
        # GEX, market tide, premium, DTE-at-entry, …) is immutable
        # post-entry, so a session-lifetime cache is correct.
        self._entry_context_cache: dict[str, dict[str, Any]] = {}
        # In-flight background resolutions, one task per symbol. Entry-context
        # enrichment reaches multi-second Heber parquet scans, so it resolves
        # off the cycle's critical path: sync_positions waits only up to the
        # budget below and never cancels the work, so a slow resolution lands
        # in the cache for a later cycle instead of being discarded and redone.
        self._entry_context_tasks: dict[str, asyncio.Task[None]] = {}
        # Per-symbol retry gate for resolutions that failed outright.
        self._entry_context_retry_at: dict[str, float] = {}
        self._entry_context_backoff_seconds: dict[str, float] = {}
        # Symbols whose resolved context has been written onto their
        # TrackedPosition, so a late-arriving context is applied exactly once.
        self._entry_context_applied: set[str] = set()
        # Bumped whenever a position is dropped, so a resolution still running
        # for the closed position cannot cache or apply onto a later reopen.
        self._entry_context_generation: dict[str, int] = {}
        # Enrichment scans are memory-heavy (a single 370-day bars read peaks
        # above 7 GB), so only one runs at a time regardless of position count.
        self._entry_context_semaphore = asyncio.Semaphore(self._ENTRY_CONTEXT_MAX_CONCURRENCY)

    # How long a sync_positions cycle will wait for pending entry-context
    # resolutions before falling back to the contract-derived bucket. This
    # bounds the CYCLE, not the work: overrunning resolutions keep running.
    _ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 2.0
    # Concurrent entry-context resolutions.
    _ENTRY_CONTEXT_MAX_CONCURRENCY = 1
    # Exponential backoff bounds for a symbol whose resolution raised.
    _ENTRY_CONTEXT_RETRY_BASE_SECONDS = 30.0
    _ENTRY_CONTEXT_RETRY_MAX_SECONDS = 300.0

    @staticmethod
    def _apply_entry_context(pos: TrackedPosition, entry_context: dict[str, Any]) -> None:
        """Populate the fields of `pos` that come from its entry context.

        Runs at construction and again when the context arrives on a later
        cycle (the first fetch timed out or failed and was deliberately not
        cached). `entry_time` is only overwritten by a real decision
        timestamp — a context without one keeps the existing approximation.
        `expiry_date` falls back to the contract symbol's own encoded expiry:
        the entry-context join returns a context WITHOUT it on three paths
        (no matching decision, fetch error, timeout), and a None here disarms
        the time-stop entirely — the position then rides to expiry and any
        ITM contract is auto-exercised into equity.
        """
        entry_time = entry_context.get("entry_time")
        if entry_time is not None:
            pos.entry_time = entry_time
        pos.bucket = entry_context.get("bucket", "SWING")
        pos.direction = entry_context.get("direction", "LONG")
        pos.premium_usd = entry_context.get("premium_usd")
        pos.dte_at_entry = entry_context.get("dte")
        pos.is_sweep = entry_context.get("is_sweep", False)
        pos.iv_rank_at_entry = entry_context.get("iv_rank_at_entry")
        pos.vix_at_entry = entry_context.get("vix_at_entry")
        pos.gex_at_entry = entry_context.get("gex_at_entry")
        pos.market_tide_30m = entry_context.get("market_tide_30m")
        pos.decision_id = entry_context.get("decision_id")
        pos.option_symbol = entry_context.get("option_symbol")
        pos.expiry_date = entry_context.get("expiry_date") or _expiry_from_occ_symbol(pos.symbol)

    async def sync_positions(self, connector: Any) -> list[TrackedPosition]:
        """
        Sync tracked positions with broker positions.

        Fetches current positions from Alpaca and updates tracking state.
        """
        try:
            broker_positions = connector.get_all_positions()
        except Exception as e:
            logger.error(f"Failed to fetch broker positions: {e}")
            return list(self.tracked_positions.values())

        # Get current symbols
        broker_symbols = {p.symbol for p in broker_positions}
        tracked_symbols = set(self.tracked_positions.keys())

        # Remove closed positions
        for symbol in tracked_symbols - broker_symbols:
            logger.info(
                f"Position closed externally: {symbol}",
                extra={"event": "position_closed", "symbol": symbol},
            )
            del self.tracked_positions[symbol]
            # Drop cached entry-context so a close+reopen of the same symbol
            # in the same process doesn't reuse a stale decision_id / context.
            self._discard_entry_context(symbol)

        # Resolve entry-context for every symbol without a cached context. On a
        # cold container with ~200 positions, resolving these inline in the
        # construction loop would block startup for tens of seconds before the
        # first exit evaluation, so resolution runs in the background and this
        # cycle waits only up to the budget. Covers new positions and tracked
        # positions whose earlier resolution failed — those are deliberately
        # left uncached so the real context is retried rather than frozen at
        # the fallback for the life of the process (July 2026: 0DTE contracts
        # ran SWING parameters that way).
        uncached = [p.symbol for p in broker_positions if p.symbol not in self._entry_context_cache]
        # This cycle's fallback for symbols still awaiting resolution. Local,
        # never cached: the bucket comes from the contract's own expiry so a
        # 0DTE gets 0DTE cadence, stops, and flatten even before the join
        # resolves.
        timed_out: dict[str, dict[str, Any]] = {}
        if uncached:
            await self._await_entry_contexts(uncached)
            for sym in uncached:
                if sym in self._entry_context_cache:
                    continue
                fallback_bucket = _bucket_from_occ_symbol(sym) or "SWING"
                logger.warning(
                    f"Entry-context fetch timed out for {sym}; using bucket {fallback_bucket} "
                    f"from the contract symbol this cycle while resolution continues",
                    extra={"event": "entry_context_timeout", "symbol": sym, "bucket": fallback_bucket},
                )
                timed_out[sym] = {"bucket": fallback_bucket}

        # Tracked positions whose context has resolved but has not been applied
        # yet. Resolution runs in the background and usually lands after the
        # cycle that created the position, so "resolved" cannot be inferred
        # from this cycle's uncached list — it has to be tracked explicitly, or
        # a late-arriving context is silently never applied.
        pending_apply = {
            symbol
            for symbol in broker_symbols
            if symbol in self.tracked_positions
            and symbol in self._entry_context_cache
            and symbol not in self._entry_context_applied
        }

        # Update existing or add new positions
        for bp in broker_positions:
            symbol = bp.symbol
            current_price = float(bp.current_price)
            entry_price = float(bp.avg_entry_price)
            qty = float(bp.qty)
            unrealized_pnl_pct = float(bp.unrealized_plpc) * 100  # Convert to percentage

            if symbol in self.tracked_positions:
                # Update existing
                pos = self.tracked_positions[symbol]
                pos.current_price = current_price
                pos.unrealized_pnl_pct = unrealized_pnl_pct

                # Entry context that arrived on retry (see the pre-fetch above)
                # is applied in place, so bucket-specific exits and the real
                # entry time take effect without disturbing the running envelope.
                if symbol in pending_apply:
                    self._apply_entry_context(pos, self._entry_context_cache[symbol])
                    self._entry_context_applied.add(symbol)
                    logger.info(
                        f"Entry context resolved late for {symbol}: bucket={pos.bucket}",
                        extra={
                            "event": "entry_context_resolved_late",
                            "symbol": symbol,
                            "bucket": pos.bucket,
                            "decision_id": pos.decision_id,
                        },
                    )

                # Update tracking metrics — record whether we observed a
                # new peak or trough so we can avoid the DB write on
                # uneventful ticks (each cycle covers ~50 positions; 12
                # cycles/min = 600 writes/min if we upserted on every
                # tick. Most ticks are inside the running envelope and
                # don't change anything; only the peak/trough deserves
                # a durable update).
                running_changed = False
                if unrealized_pnl_pct > pos.max_return_pct:
                    pos.max_return_pct = unrealized_pnl_pct
                    running_changed = True
                if unrealized_pnl_pct < pos.max_drawdown_pct:
                    pos.max_drawdown_pct = unrealized_pnl_pct
                    running_changed = True
                if running_changed:
                    # Phase 3 of exit-pipeline RCA: persist so the ML
                    # branch sees a non-zero MFE/MAE on restart-loaded
                    # positions, instead of a fresh `max(0, unrealized)`
                    # seed.
                    await upsert_position_running_stats(
                        symbol=symbol,
                        max_return_pct=pos.max_return_pct,
                        max_drawdown_pct=pos.max_drawdown_pct,
                    )
            else:
                # New position — context was pre-fetched above and is in
                # cache (a real row or a default; _fetch_entry_context
                # short-circuits on cache hit), or is this cycle's uncached
                # timeout fallback.
                entry_context = timed_out.get(symbol) or await self._fetch_entry_context(symbol)

                # Phase 3 of exit-pipeline RCA: rehydrate running-window
                # stats from the durable `position_running_stats` table
                # if a row exists. Falls back to the original
                # `max(0, unrealized) / min(0, unrealized)` seed when
                # no row is present (genuinely-new position or first
                # ever sync_positions cycle).
                persisted = await load_position_running_stats(symbol)
                if persisted is not None:
                    persisted_max, persisted_drawdown = persisted
                    # Defensive: the current tick might have moved the
                    # peak/trough past the persisted values. Take the
                    # more-extreme of (persisted, current) so a hot
                    # restart-during-spike doesn't shrink the envelope.
                    seeded_max = max(persisted_max, unrealized_pnl_pct, 0)
                    seeded_drawdown = min(persisted_drawdown, unrealized_pnl_pct, 0)
                else:
                    seeded_max = max(0, unrealized_pnl_pct)
                    seeded_drawdown = min(0, unrealized_pnl_pct)

                # entry_time starts as now() and is replaced by the decision
                # row's real timestamp when the context has one — approximating
                # every legacy position's entry as "now" after a restart biased
                # the ML `time_held_hours` feature toward "hold longer".
                pos = TrackedPosition(
                    symbol=symbol,
                    qty=qty,
                    entry_price=entry_price,
                    current_price=current_price,
                    unrealized_pnl_pct=unrealized_pnl_pct,
                    entry_time=datetime.now(UTC),
                    bucket="SWING",
                    max_return_pct=seeded_max,
                    max_drawdown_pct=seeded_drawdown,
                )
                self._apply_entry_context(pos, entry_context)
                # Only a resolved context counts as applied; the contract-derived
                # fallback must stay eligible for late application.
                if symbol in self._entry_context_cache:
                    self._entry_context_applied.add(symbol)
                self.tracked_positions[symbol] = pos
                # Initial upsert so the row exists for subsequent
                # rehydration (e.g. if the container restarts before
                # the next peak/trough event).
                await upsert_position_running_stats(
                    symbol=symbol,
                    max_return_pct=pos.max_return_pct,
                    max_drawdown_pct=pos.max_drawdown_pct,
                )

                logger.info(
                    f"New position tracked: {symbol} @ {entry_price}",
                    extra={
                        "event": "position_tracked",
                        "symbol": symbol,
                        "bucket": pos.bucket,
                        "decision_id": pos.decision_id,
                        "has_entry_context": any(
                            v is not None
                            for v in (
                                pos.iv_rank_at_entry,
                                pos.vix_at_entry,
                                pos.gex_at_entry,
                                pos.market_tide_30m,
                            )
                        ),
                    },
                )

        return list(self.tracked_positions.values())

    async def _await_entry_contexts(self, symbols: list[str]) -> None:
        """Start background entry-context resolution and wait out the budget.

        Waiting is deliberately non-destructive: ``asyncio.wait`` returns when
        the budget expires but leaves unfinished resolutions running, so the
        work completes once and is cached instead of being cancelled and
        repeated on every cycle. Callers treat a symbol that is still missing
        from the cache as unresolved for this cycle and fall back to the
        bucket encoded in the contract symbol.
        """
        pending = [task for task in (self._ensure_entry_context_task(s) for s in symbols) if task is not None]
        if not pending:
            return
        await asyncio.wait(pending, timeout=self._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS)

    def _ensure_entry_context_task(self, symbol: str) -> asyncio.Task[None] | None:
        """Return the in-flight resolution for ``symbol``, starting one if due.

        Returns None while a previously failed symbol is inside its backoff
        window, so a symbol that cannot resolve costs one attempt per backoff
        interval rather than one per cycle.
        """
        existing = self._entry_context_tasks.get(symbol)
        if existing is not None:
            return existing
        retry_at = self._entry_context_retry_at.get(symbol)
        if retry_at is not None and time.monotonic() < retry_at:
            return None
        generation = self._entry_context_generation.get(symbol, 0)
        task = asyncio.create_task(self._resolve_entry_context(symbol, generation))
        self._entry_context_tasks[symbol] = task
        return task

    async def _resolve_entry_context(self, symbol: str, generation: int) -> None:
        """Resolve and cache one symbol's entry context, serialised and bounded.

        Runs under a semaphore because enrichment reaches Heber parquet scans
        whose peak memory is measured in gigabytes; a failure schedules an
        exponentially backed-off retry rather than looping hot.

        ``generation`` pins the result to the position that requested it. The
        permit is held until the worker thread genuinely finishes, so a symbol
        that closes mid-scan cannot hand the slot to a second scan while the
        first is still resident; a result that arrives for a superseded
        generation is discarded instead of being cached against the reopen.
        """
        try:
            async with self._entry_context_semaphore:
                if generation != self._entry_context_generation.get(symbol, 0):
                    return
                await self._fetch_entry_context(symbol)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"Entry-context resolution failed for {symbol}: {e}",
                extra={"event": "entry_context_resolution_failed", "symbol": symbol, "error": str(e)},
            )
        finally:
            # Only clear our own entry: a close+reopen of the same symbol may
            # already have registered a replacement task.
            if self._entry_context_tasks.get(symbol) is asyncio.current_task():
                self._entry_context_tasks.pop(symbol, None)

        if generation != self._entry_context_generation.get(symbol, 0):
            # The position closed while this was resolving. Drop anything the
            # fetch cached so a reopen re-resolves against its own decision.
            self._entry_context_cache.pop(symbol, None)
            return

        if symbol in self._entry_context_cache:
            self._entry_context_backoff_seconds.pop(symbol, None)
            self._entry_context_retry_at.pop(symbol, None)
            return

        previous = self._entry_context_backoff_seconds.get(symbol, 0.0)
        backoff = min(
            previous * 2 if previous else self._ENTRY_CONTEXT_RETRY_BASE_SECONDS,
            self._ENTRY_CONTEXT_RETRY_MAX_SECONDS,
        )
        self._entry_context_backoff_seconds[symbol] = backoff
        self._entry_context_retry_at[symbol] = time.monotonic() + backoff

    def _discard_entry_context(self, symbol: str) -> None:
        """Drop all entry-context state for a symbol that is no longer held.

        An in-flight resolution is deliberately NOT cancelled and NOT removed
        from the task map. Cancelling would unwind the semaphore and hand the
        single scan permit to another symbol while the abandoned worker thread
        keeps scanning — ``asyncio.to_thread`` work cannot be interrupted — so
        close/reopen churn could stack the very multi-gigabyte scans the permit
        exists to serialise. The resolution instead drains on its own, and the
        generation bump below makes its result inapplicable.
        """
        self._entry_context_generation[symbol] = self._entry_context_generation.get(symbol, 0) + 1
        self._entry_context_cache.pop(symbol, None)
        self._entry_context_retry_at.pop(symbol, None)
        self._entry_context_backoff_seconds.pop(symbol, None)
        self._entry_context_applied.discard(symbol)

    async def _fetch_entry_context(self, symbol: str) -> dict[str, Any]:
        """
        Fetch entry context for a position symbol.

        Joins orders → strategy_decisions → candidate_trades to recover the
        originating Orion decision (filtering by the ``orion_`` client_order_id
        prefix to ignore positions opened by other systems on the shared
        Alpaca account). From that decision row we pull:

          - decision_id, entry_time, direction, premium_usd, dte, bucket,
            option_symbol, expiry_date — all directly queryable from the join.
          - is_sweep, event_id — extracted from ``candidate_trades.evidence``.
          - iv_rank_at_entry, vix_at_entry, gex_at_entry, market_tide_30m —
            fetched from the same flow_enricher pipeline the ML scorer uses,
            so train/inference parity is preserved.

        Results are cached per-symbol on the PositionMonitor instance for the
        lifetime of the session — entry-time context is immutable post-entry,
        so the cache never needs invalidation. Positions opened by other
        systems on the shared account fall through to the default ``{"bucket":
        "SWING"}`` payload (no Orion decision rows match), and the exit
        classifier / fallback rules already handle None fields gracefully.
        """
        if symbol in self._entry_context_cache:
            return self._entry_context_cache[symbol]

        # Match by option_symbol when the position symbol looks like an OCC
        # contract (e.g. QQQ260522P00721000); otherwise match by underlying
        # ticker. Alpaca returns option positions keyed by OCC, equity
        # positions keyed by ticker.
        is_option = _is_occ_option_symbol(symbol)
        join_clause = "ct.option_symbol = :symbol" if is_option else "ct.ticker = :symbol"

        # DTE is computed in Python from expiration_date - decision_ts to
        # stay portable across PostgreSQL (production) and SQLite (tests).
        query = f"""
            SELECT
                sd.decision_id,
                sd.timestamp_utc as decision_ts,
                ct.ticker,
                ct.option_symbol,
                ct.expiration_date,
                ct.option_type,
                ct.premium,
                ct.direction,
                ct.evidence
            FROM strategy_decisions sd
            JOIN candidate_trades ct ON sd.candidate_id = ct.candidate_id
            LEFT JOIN orders o ON o.decision_id = sd.decision_id
            WHERE {join_clause}
            AND sd.decision = 'EXECUTE'
            -- o.id IS NULL is the LEFT-JOIN miss sentinel (no orders row at
            -- all for this decision, e.g. legacy pre-attribution rows). We
            -- intentionally do NOT use `client_order_id IS NULL` here because
            -- that would also match orders rows another system inserted with
            -- a null client_order_id, falsely attributing them to Orion.
            AND (o.client_order_id LIKE 'orion_%' OR o.id IS NULL)
            ORDER BY sd.timestamp_utc DESC
            LIMIT 1
        """

        row: dict[str, Any] | None = None
        try:

            async def run_query(session: Any) -> dict[str, Any] | None:
                from sqlalchemy import text

                result = await session.execute(text(query), {"symbol": symbol})
                row_ = result.mappings().first()
                return dict(row_) if row_ else None

            row = await db_query(run_query)
        except Exception as e:
            # Transient DB failure — do NOT cache the fallback (a cached
            # wrong bucket would permanently disable bucket-specific exits
            # like the 0DTE hard flatten); retry on the next sync cycle.
            logger.warning(f"Failed to fetch entry context for {symbol}: {e}")
            return {"bucket": _bucket_from_occ_symbol(symbol) or "SWING"}

        if not row:
            # No matching Orion decision — either a non-Orion position on the
            # shared account, or a legacy position pre-attribution. Derive the
            # bucket from the OCC symbol's embedded expiry when possible, and
            # cache so we don't re-hit the DB every 60s for the same symbol.
            default = {"bucket": _bucket_from_occ_symbol(symbol) or "SWING"}
            self._entry_context_cache[symbol] = default
            return default

        # Compute DTE in Python (portable across Postgres/SQLite). Falls back
        # to candidate.evidence["dte"] when expiration_date is missing.
        def _coerce_dt(val: Any) -> datetime | None:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                try:
                    from dateutil.parser import parse as _parse  # type: ignore[import-untyped,unused-ignore]

                    return _parse(val)
                except Exception:
                    return None
            return None

        dte: int | None = None
        expiration_date = _coerce_dt(row.get("expiration_date"))
        decision_ts_for_dte = _coerce_dt(row.get("decision_ts"))
        if expiration_date is not None and decision_ts_for_dte is not None:
            try:
                # Calendar-day DTE: expiration_date is stored as midnight UTC,
                # so timestamp subtraction truncates a 1-DTE entered intraday
                # to 0 days (mislabelling it 0DTE). Date arithmetic is exact.
                dte = max((expiration_date.date() - decision_ts_for_dte.date()).days, 0)
            except Exception:
                dte = None

        if dte is None:
            try:
                evidence_raw = row.get("evidence") or {}
                if isinstance(evidence_raw, str):
                    import json as _json

                    evidence_raw = _json.loads(evidence_raw)
                if isinstance(evidence_raw, dict) and evidence_raw.get("dte") is not None:
                    dte = int(evidence_raw["dte"])
            except Exception:
                dte = None

        if dte is None:
            # Last resort: the OCC symbol embeds the expiry — current DTE
            # biases toward the TIGHTER bucket for an aged position, which
            # fails safe (earlier exits, and the 0DTE flatten still arms).
            dte = _dte_from_occ_symbol(symbol)

        if dte is None:
            logger.warning(
                f"DTE unknown for {symbol}; defaulting bucket to SWING — "
                f"bucket-specific exits (0DTE flatten) may not arm",
                extra={"event": "dte_fallback_used", "symbol": symbol},
            )
            dte = 7

        from orion.execution.exit_fallback_rules import bucket_for_dte

        bucket = bucket_for_dte(dte)

        # The decision row has the real entry timestamp; use it instead of
        # now() so ML exit features (time_held_hours) are correct after a
        # restart. Fall back to None so the caller can decide.
        decision_ts = decision_ts_for_dte  # already coerced to datetime above
        from orion.shared.utils import ensure_utc as _ensure_utc

        entry_time = _ensure_utc(decision_ts) if decision_ts is not None else None

        evidence = row.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                import json as _json

                evidence = _json.loads(evidence)
            except Exception:
                evidence = {}

        is_sweep = bool(evidence.get("is_sweep")) if isinstance(evidence, dict) else False
        event_id = evidence.get("event_id") if isinstance(evidence, dict) else None
        if not event_id and isinstance(evidence, dict):
            ids = evidence.get("event_ids") or []
            if isinstance(ids, list) and ids:
                event_id = ids[0]

        put_call_raw = row.get("option_type") or (evidence.get("put_call") if isinstance(evidence, dict) else None)
        put_call = "C"
        if put_call_raw:
            normalized = str(put_call_raw).upper()
            put_call = "C" if normalized in {"C", "CALL"} else ("P" if normalized in {"P", "PUT"} else "C")

        ticker = row.get("ticker") or symbol

        premium_usd = row.get("premium")
        if (premium_usd in (None, 0)) and isinstance(evidence, dict):
            premium_usd = evidence.get("premium_usd") or evidence.get("premium") or premium_usd

        # Enrich with iv_rank / vix / gex / market_tide via the same path
        # the ML scorer uses, so position-monitor features stay aligned
        # with training. Failures here are non-fatal: we still return the
        # DB-derived context and let the exit classifier / fallback rules
        # cope with None enrichment fields (they already do).
        enrichment: dict[str, Any] = {}
        if entry_time is not None:
            try:
                from orion.ml.flow_enricher import enrich_flow_for_scoring

                expiry_str = None
                if expiration_date is not None:
                    try:
                        expiry_str = expiration_date.date().isoformat()
                    except AttributeError:
                        expiry_str = str(expiration_date)

                # Offloaded to a worker thread: the enricher fans out to
                # HeberReader, whose parquet scans are synchronous and can run
                # for over a minute (a 370-day bars read measured 80s). Awaited
                # inline they block the whole monitor loop for that duration —
                # exit evaluation included — and make the fetch budget above
                # unenforceable, because a blocked loop cannot fire its timers.
                enrichment = await _run_coroutine_in_thread(
                    enrich_flow_for_scoring(
                        ticker=ticker,
                        entry_ts=entry_time,
                        put_call=put_call,
                        dte=dte,
                        premium_usd=float(premium_usd) if premium_usd is not None else None,
                        event_id=event_id,
                        option_chain=row.get("option_symbol"),
                        aggressor=evidence.get("aggressor") if isinstance(evidence, dict) else None,
                        is_sweep=is_sweep,
                        expiry=expiry_str,
                    )
                )
            except Exception as e:
                logger.warning(
                    f"Failed to enrich entry context for {symbol}: {e}",
                    extra={"event": "entry_context_enrichment_failed", "symbol": symbol, "error": str(e)},
                )
                enrichment = {}

        context = {
            "decision_id": row.get("decision_id"),
            "option_symbol": row.get("option_symbol"),
            "premium_usd": premium_usd,
            "dte": dte,
            "bucket": bucket,
            "direction": row.get("direction", "LONG"),
            "entry_time": entry_time,
            "expiry_date": expiration_date,
            "is_sweep": is_sweep,
            "iv_rank_at_entry": enrichment.get("iv_rank_at_entry"),
            "vix_at_entry": enrichment.get("vix_at_entry"),
            "gex_at_entry": enrichment.get("gex_at_entry"),
            "market_tide_30m": enrichment.get("market_tide_30m"),
        }

        self._entry_context_cache[symbol] = context
        return context

    def evaluate_exits(self) -> list[tuple[TrackedPosition, ExitPrediction]]:
        """Evaluate exit signals for all tracked positions.

        Returns list of (position, prediction) tuples for positions that
        should be exited.

        Fallback rules (profit / time-to-expiry / drawdown) are evaluated
        FIRST and short-circuit the ML classifier when they fire. This
        keeps the deterministic safety net in place even when the
        classifier is degraded or returns low-confidence predictions
        (see FOLLOWUPS.md #0).

        The classifier is consulted only for buckets that have a trained
        exit model loaded. Without one, the per-bucket barriers are the
        entire exit policy: the classifier's built-in heuristic uses
        tighter, undocumented thresholds (SWING stop -20% vs the -40%
        barrier) and would otherwise pre-empt every documented barrier.
        A barrier evaluation that raises is retried with the unoverridden
        bucket defaults; if that also raises, the classifier is consulted
        as the last resort for that position (never "no policy").
        """
        from orion.config import system_settings
        from orion.core.market_schedule import resolve_session_close
        from orion.execution.exit_fallback_rules import (
            evaluate_fallback_rules,
            resolve_exit_params,
        )

        exit_signals: list[tuple[TrackedPosition, ExitPrediction]] = []

        # One calendar lookup per cycle, shared by every position: the 0DTE
        # flatten deadline is min(configured cutoff, session close - buffer),
        # so a 13:00 ET half day flattens before expiry instead of never.
        session_close = resolve_session_close()

        for symbol, pos in self.tracked_positions.items():
            # Fallback rules first — they're cheap and deterministic.
            # Wrap in try/except so a rule that raises (a malformed
            # ORION_EXIT_BUCKET_OVERRIDES entry, schema drift on
            # expiry_date, etc.) doesn't kill the whole evaluate loop and
            # starve every remaining position this cycle. On failure the
            # evaluation is retried with the unoverridden bucket defaults so
            # the position is never left with no policy; only if that also
            # raises does the classifier become the last resort below.
            policy_evaluated = True
            try:
                fallback = evaluate_fallback_rules(
                    pos,
                    params=resolve_exit_params(pos.bucket, system_settings.exit_bucket_overrides),
                    session_close=session_close,
                )
            except Exception as exc:
                logger.error(
                    f"Exit fallback evaluation raised for {symbol}: {exc}; retrying with default barriers",
                    extra={
                        "event": "exit_fallback_error",
                        "symbol": symbol,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                try:
                    fallback = evaluate_fallback_rules(
                        pos,
                        params=resolve_exit_params(pos.bucket),
                        session_close=session_close,
                    )
                except Exception as retry_exc:
                    logger.critical(
                        f"EXIT_POLICY_EVALUATION_FAILED for {symbol} ({pos.bucket}): default barriers also "
                        f"raised: {retry_exc}; consulting the exit classifier as last resort",
                        extra={
                            "event": "exit_policy_evaluation_failed",
                            "event_type": "EXIT_POLICY_EVALUATION_FAILED",
                            "ticker": symbol,
                            "bucket": pos.bucket,
                            "error": str(retry_exc),
                        },
                        exc_info=True,
                    )
                    fallback = None
                    policy_evaluated = False

            if fallback is not None:
                logger.info(
                    f"Exit fallback fired for {symbol}: {fallback.reason}",
                    extra={
                        "event": "exit_signal_fallback",
                        "symbol": symbol,
                        "rule_id": fallback.rule_id,
                        "urgency": fallback.urgency,
                        "pnl_pct": pos.unrealized_pnl_pct,
                        "bucket": pos.bucket,
                    },
                )
                # Wrap the ExitSignal as ExitPrediction-compatible so the
                # downstream execute_exits path doesn't need to branch.
                # Duck-typed as ExitPrediction: consumers only read .should_exit,
                # .confidence, .reasoning, and (optionally) .rule_id, which
                # carries the rule's own identity through to exit_decisions.
                prediction = cast(
                    "ExitPrediction",
                    SimpleNamespace(
                        should_exit=True,
                        confidence=fallback.confidence,
                        reasoning=fallback.reason,
                        rule_id=fallback.rule_id,
                    ),
                )
                exit_signals.append((pos, prediction))
                continue

            # No trained exit model for this bucket: the barriers above are
            # the whole policy, so the classifier (and its heuristic) is
            # not consulted — but only when the barriers actually evaluated.
            # If they raised on both attempts, the classifier is the last
            # resort rather than leaving the position with no exit policy.
            if policy_evaluated and pos.bucket not in self.exit_classifier.models:
                continue

            # ML classifier path — unchanged from before.
            time_held = datetime.now(UTC) - pos.entry_time
            time_held_hours = time_held.total_seconds() / 3600

            features = ExitFeatures(
                current_return_pct=pos.unrealized_pnl_pct,
                time_held_hours=time_held_hours,
                max_return_so_far=pos.max_return_pct,
                max_drawdown_so_far=pos.max_drawdown_pct,
                premium_usd=pos.premium_usd or 0,
                dte_at_entry=pos.dte_at_entry or 7,
                is_sweep=pos.is_sweep,
                bucket=pos.bucket,
                iv_rank_at_entry=pos.iv_rank_at_entry,
                vix_at_entry=pos.vix_at_entry,
                gex_at_entry=pos.gex_at_entry,
                market_tide_30m=pos.market_tide_30m,
            )

            prediction = self.exit_classifier.predict(features)

            if prediction.should_exit:
                logger.info(
                    f"Exit signal for {symbol}: {prediction.reasoning}",
                    extra={
                        "event": "exit_signal",
                        "symbol": symbol,
                        "confidence": prediction.confidence,
                        "pnl_pct": pos.unrealized_pnl_pct,
                        "bucket": pos.bucket,
                    },
                )
                exit_signals.append((pos, prediction))

        return exit_signals

    # After this many consecutive failed closes, a symbol is abandoned (with a
    # CRITICAL alert) so a single stuck position can't hammer the Gateway every
    # cycle. Abandonment is NOT permanent: after the cooldown below the symbol
    # gets another attempt, because the cause is often transient (a stale mark,
    # a day-trading-buying-power wall, a sibling's resting order on the shared
    # account). Permanent abandonment stranded a +320% MU winner on 2026-06-05.
    _MAX_CONSECUTIVE_CLOSE_FAILURES = 5
    _CLOSE_ABANDON_COOLDOWN_SECONDS = 600.0
    # An expiry flatten has a hard deadline — the session close — and the whole
    # window between the flatten cutoff and that close is only 15 minutes. The
    # standard 10-minute cooldown would eat almost all of it and let an ITM
    # contract reach expiry, so a flatten waits a cycle or two, not ten minutes.
    _FLATTEN_ABANDON_COOLDOWN_SECONDS = 60.0

    def _now(self) -> float:
        """Monotonic clock for cooldown math (patchable in tests)."""
        return time.monotonic()

    def _close_attempts_exhausted(self, symbol: str, *, expiry_deadline: bool = False) -> bool:
        count = self._consecutive_close_failures.get(symbol, 0)
        if count < self._MAX_CONSECUTIVE_CLOSE_FAILURES:
            return False
        # Abandoned — but give it another chance once the cooldown elapses so a
        # transient cause can clear instead of stranding the position forever.
        cooldown = self._FLATTEN_ABANDON_COOLDOWN_SECONDS if expiry_deadline else self._CLOSE_ABANDON_COOLDOWN_SECONDS
        last = self._close_failure_ts.get(symbol)
        if last is not None and (self._now() - last) >= cooldown:
            self._consecutive_close_failures.pop(symbol, None)
            self._close_failure_ts.pop(symbol, None)
            logger.warning(
                f"Re-attempting close for {symbol} after abandon cooldown elapsed",
                extra={
                    "event": "close_abandon_cooldown_elapsed",
                    "event_type": "CLOSE_ABANDON_RETRY",
                    "symbol": symbol,
                },
            )
            return False
        return True

    def _record_close_result(self, symbol: str, success: bool) -> None:
        """Track consecutive close failures per symbol; reset on success."""
        if success:
            self._consecutive_close_failures.pop(symbol, None)
            self._close_failure_ts.pop(symbol, None)
            return
        count = self._consecutive_close_failures.get(symbol, 0) + 1
        self._consecutive_close_failures[symbol] = count
        self._close_failure_ts[symbol] = self._now()
        if count == self._MAX_CONSECUTIVE_CLOSE_FAILURES:
            logger.critical(
                f"Abandoning close for {symbol} after {count} consecutive failures — "
                f"will retry after the abandon cooldown "
                f"({int(self._CLOSE_ABANDON_COOLDOWN_SECONDS)}s, or "
                f"{int(self._FLATTEN_ABANDON_COOLDOWN_SECONDS)}s for an expiry flatten); "
                f"manual review recommended (position may be stuck/unclosable)",
                extra={
                    "event": "close_abandoned",
                    "event_type": "CLOSE_ABANDONED",
                    "symbol": symbol,
                    "failures": count,
                },
            )

    async def execute_exits(
        self,
        connector: Any,
        exit_signals: list[tuple[TrackedPosition, ExitPrediction]],
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Execute exit orders for positions with exit signals.

        Routes through ExecutionEngine.close_position() when available to ensure
        circuit breaker, rate limiter, risk manager, and order persistence are applied.
        Falls back to direct connector call only when no engine is configured.

        Uses PositionManager._closing_symbols guard to prevent duplicate close orders
        from both the ML exit classifier and rule-based exit logic.

        Args:
            connector: AlpacaTradingConnector
            exit_signals: List of (position, prediction) tuples
            dry_run: If True, log but don't execute

        Returns:
            List of execution results
        """
        from orion.execution.exit_fallback_rules import racing_expiry
        from orion.ml.performance_tracker import log_exit_prediction, log_outcome

        results = []

        for pos, prediction in exit_signals:
            result = {
                "symbol": pos.symbol,
                "position_type": "option" if pos.option_symbol else "equity",
                "pnl_pct": pos.unrealized_pnl_pct,
                "confidence": prediction.confidence,
                "reasoning": prediction.reasoning,
                "executed": False,
                "order_id": None,
                "error": None,
            }

            # Log exit prediction for performance tracking
            try:
                await log_exit_prediction(
                    symbol=pos.symbol,
                    option_chain=pos.option_symbol or "",
                    bucket=pos.bucket,
                    prediction_score=prediction.confidence,
                    position_id=pos.decision_id,
                )
            except Exception as e:
                logger.debug(f"Failed to log exit prediction: {e}")

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would close: {pos.symbol} @ {pos.unrealized_pnl_pct:.1f}%",
                    extra={"event": "dry_run_exit", "symbol": pos.symbol},
                )
                result["executed"] = False
                result["error"] = "dry_run"
            else:
                # Check closing guard — prevent duplicate close orders
                if self._position_manager and self._position_manager.is_closing(pos.symbol):
                    logger.warning(
                        f"ML exit skipped: {pos.symbol} already has a close in progress",
                        extra={"event": "ml_exit_duplicate_blocked", "symbol": pos.symbol},
                    )
                    result["error"] = "close_already_in_progress"
                    results.append(result)
                    continue

                # Stop hammering a position that repeatedly fails to close —
                # except one expiring today, which runs out of session rather
                # than out of attempts. Keyed off the position, not the winning
                # rule: a post-cutoff 0DTE that exits on stop-loss or profit
                # target is racing the same expiry as an explicit flatten.
                if self._close_attempts_exhausted(pos.symbol, expiry_deadline=racing_expiry(pos)):
                    result["error"] = "close_abandoned_after_repeated_failures"
                    results.append(result)
                    continue

                # Mark symbol as closing before submitting order
                if self._position_manager:
                    if not self._position_manager.mark_closing(pos.symbol):
                        result["error"] = "close_already_in_progress"
                        results.append(result)
                        continue

                try:
                    closed = False

                    if self._execution_engine is not None:
                        # Route through ExecutionEngine for circuit breaker, rate limiter,
                        # risk manager, and order persistence
                        from types import SimpleNamespace

                        # A fallback rule's prediction carries its own rule_id,
                        # which is what exit_decisions records; a classifier
                        # prediction has none and keeps the ml_exit label.
                        # Urgency is always IMMEDIATE regardless of the rule:
                        # close_position keys equity market-vs-limit routing
                        # on it, and a monitor exit is meant to close now.
                        exit_signal = SimpleNamespace(
                            rule_id=getattr(prediction, "rule_id", None) or f"ml_exit_{pos.bucket}",
                            reason=prediction.reasoning,
                            urgency="IMMEDIATE",
                            confidence=prediction.confidence,
                            details={"bucket": pos.bucket, "pnl_pct": pos.unrealized_pnl_pct},
                        )

                        # `use_market_order=False` for the options path —
                        # close_position routes options through limit orders
                        # regardless of urgency, because Alpaca rejects
                        # options market orders outside RTH (42210000).
                        # Pass `current_price` (the mark sync_positions
                        # maintains on `TrackedPosition`) so the limit can
                        # be derived without a separate quote lookup.
                        closed = await self._execution_engine.close_position(
                            ticker=pos.symbol,
                            qty=pos.qty,
                            exit_signal=exit_signal,
                            direction=pos.direction,
                            use_market_order=False,
                            current_price=pos.current_price,
                        )
                    else:
                        # Fallback: direct connector call (legacy path, no safety guards)
                        logger.warning(
                            f"No ExecutionEngine configured — closing {pos.symbol} directly via connector",
                            extra={"event": "direct_close_fallback", "symbol": pos.symbol},
                        )
                        close_symbol = pos.option_symbol or pos.symbol
                        order = connector.close_position(close_symbol)
                        closed = order is not None

                    if closed:
                        result["executed"] = True
                        logger.info(
                            f"Exit executed via engine: {pos.symbol}",
                            extra={"event": "exit_executed", "symbol": pos.symbol},
                        )

                        # Log outcome for performance tracking
                        hit_target = pos.unrealized_pnl_pct >= 50
                        hit_stop = pos.unrealized_pnl_pct <= -20
                        try:
                            await log_outcome(
                                position_id=pos.decision_id or pos.symbol,
                                actual_return_pct=pos.unrealized_pnl_pct,
                                hit_target=hit_target,
                                hit_stop=hit_stop,
                            )
                        except Exception as e:
                            logger.debug(f"Failed to log outcome: {e}")

                except Exception as e:
                    logger.error(f"Exit execution failed for {pos.symbol}: {e}")
                    result["error"] = str(e)
                finally:
                    # Always release the closing guard
                    if self._position_manager:
                        self._position_manager.unmark_closing(pos.symbol)

                # Bound retries: track consecutive failures so a stuck position
                # is eventually abandoned instead of hammering every cycle.
                self._record_close_result(pos.symbol, bool(result["executed"]))

            results.append(result)

        return results

    async def _reprotect_unprotected_positions(self) -> None:
        """Re-place missing protective bracket legs once per cycle.

        Reads the risk manager's in-memory unprotected registry (populated
        by ExecutionEngine when entry-time bracket placement failed) and asks
        the execution engine to re-attempt protection for each entry that maps
        to a currently-tracked position. On success the engine clears the
        registry entry and emits a recovery alert. Best-effort: any per-symbol
        failure is logged and skipped, never blocking the rest of the cycle.
        """
        engine = self._execution_engine
        if engine is None:
            return
        risk_manager = getattr(engine, "risk_manager", None)
        get_unprotected = getattr(risk_manager, "get_unprotected", None)
        if not callable(get_unprotected):
            return

        unprotected = get_unprotected()
        if not unprotected:
            return

        # An entry that no longer maps to a live position is stale: the
        # position closed (exit/manual flatten) before re-protection landed.
        # Clearing it prevents a later reopen of the same OCC contract from
        # inheriting the old entry and getting spurious bracket orders. The
        # grace window covers the entry-fill race (brackets are attempted at
        # submit time, before the fill lands in tracked_positions).
        stale_grace = timedelta(minutes=30)
        now = datetime.now(UTC)

        for option_symbol in list(unprotected.keys()):
            pos = self.tracked_positions.get(option_symbol)
            entry = unprotected[option_symbol]
            if pos is None:
                marked_at = entry.get("marked_at_utc")
                if isinstance(marked_at, datetime) and now - marked_at > stale_grace:
                    clear = getattr(risk_manager, "clear_unprotected", None)
                    if callable(clear):
                        clear(option_symbol)
                    logger.info(
                        "unprotected_entry_stale_cleared",
                        extra={"option_symbol": option_symbol, "marked_at_utc": str(marked_at)},
                    )
                # Within the grace window: broker sync may not have surfaced
                # the fill yet — leave it registered for a later cycle.
                continue
            ticker = entry.get("ticker") or option_symbol
            try:
                # Orion positions are always long options (entries are BUYs);
                # reprotect_position hardcodes the side accordingly and places
                # only the legs recorded missing at registration time.
                await engine.reprotect_position(
                    ticker=ticker,
                    option_symbol=option_symbol,
                    entry_price=pos.entry_price,
                    qty=int(abs(pos.qty)),
                    missing_legs=entry.get("missing_legs"),
                )
            except Exception as exc:
                logger.error(
                    "reprotect_attempt_failed",
                    extra={"option_symbol": option_symbol, "error": str(exc)},
                )

    async def run_check(
        self,
        connector: Any,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Run a full position check cycle.

        1. Sync positions with broker
        2. Evaluate exit signals
        3. Execute exits

        Returns summary of the check.
        """
        self._last_check_time = datetime.now(UTC)

        # Sync positions
        positions = await self.sync_positions(connector)

        # Re-protection runs FIRST — before exit evaluation — so a position
        # left naked by a failed entry-time bracket regains its stop/take-
        # profit as early as possible in the cycle. dry_run skips it (no
        # order submission). Runs even with no exit signals.
        if not dry_run:
            await self._reprotect_unprotected_positions()

        if not positions:
            return {
                "timestamp": self._last_check_time.isoformat(),
                "positions_checked": 0,
                "exit_signals": 0,
                "exits_executed": 0,
                "rss_mb": _process_rss_mb(),
            }

        # Evaluate exits
        exit_signals = self.evaluate_exits()

        # Execute exits
        results = []
        if exit_signals:
            results = await self.execute_exits(connector, exit_signals, dry_run=dry_run)

        summary = {
            "timestamp": self._last_check_time.isoformat(),
            "positions_checked": len(positions),
            "exit_signals": len(exit_signals),
            "exits_executed": sum(1 for r in results if r.get("executed")),
            "exits": results,
            # Cheap growth guard: the 2026-08-17 leak was only visible because
            # someone happened to look at `top`. Peak RSS on every cycle line
            # makes the same growth obvious from the structured logs alone.
            "rss_mb": _process_rss_mb(),
        }

        logger.info(
            f"Position check complete: {summary['positions_checked']} positions, "
            f"{summary['exit_signals']} signals, {summary['exits_executed']} executed",
            extra={"event": "position_check_complete", **summary},
        )

        return summary


# Background monitoring loop
async def run_position_monitor_loop(
    check_interval_seconds: int = 60,
    dry_run: bool = False,
    execution_engine: Any | None = None,
    position_manager: Any | None = None,
    gateway_client: Any | None = None,
) -> None:
    """
    Run continuous position monitoring loop.

    Syncs positions via GatewayTradingClient and routes exits through
    ExecutionEngine.close_position() for full safety-guard coverage
    (circuit breaker, rate limiter, risk manager, order persistence).

    Args:
        check_interval_seconds: Seconds between position checks
        dry_run: If True, log but don't execute exits
        execution_engine: ExecutionEngine instance for safe order routing
        position_manager: PositionManager instance for closing-guard coordination
        gateway_client: GatewayTradingClient for position sync
    """
    if gateway_client is None:
        from orion.clients.gateway_trading_client import get_gateway_trading_client

        gateway_client = get_gateway_trading_client()

    adapter = GatewayPositionAdapter(gateway_client)
    monitor = get_position_monitor(
        execution_engine=execution_engine,
        position_manager=position_manager,
    )

    logger.info(
        "Position monitor loop started",
        extra={
            "event": "monitor_started",
            "interval_seconds": check_interval_seconds,
            "dry_run": dry_run,
            "has_execution_engine": execution_engine is not None,
        },
    )

    while True:
        try:
            # Refresh positions from Gateway before each check
            await adapter.refresh()
            summary = await monitor.run_check(adapter, dry_run=dry_run)
            logger.debug(
                "Position monitor cycle complete",
                extra={"event": "monitor_cycle", **summary},
            )
            # Liveness: one publish per successful check (swallows its own errors).
            await publish_liveness(
                "position_monitor",
                cadence_budget_seconds=POSITION_MONITOR_LIVENESS_CADENCE_BUDGET_SECONDS,
            )
        except Exception as e:
            logger.error(f"Position monitor error: {e}", exc_info=True)

        # 0DTE positions decay fast enough that a 60s cadence gives away
        # real money between checks — tighten to 30s while any are open.
        interval = check_interval_seconds
        if any(p.bucket == "0DTE" for p in monitor.tracked_positions.values()):
            interval = min(check_interval_seconds, 30)
        await asyncio.sleep(interval)


# Singleton
_position_monitor: PositionMonitor | None = None


def get_position_monitor(
    execution_engine: Any | None = None,
    position_manager: Any | None = None,
) -> PositionMonitor:
    """Get or create position monitor singleton."""
    global _position_monitor
    if _position_monitor is None:
        _position_monitor = PositionMonitor(
            execution_engine=execution_engine,
            position_manager=position_manager,
        )
    return _position_monitor
