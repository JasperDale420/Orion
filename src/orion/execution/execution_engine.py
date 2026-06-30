import asyncio
import math
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select

from orion.config import risk_settings, system_settings
from orion.core.enums import DecisionStatus, OrderSide
from orion.execution.attribution import (
    ORDER_ID_PREFIX,
    is_occ_option_symbol,
    mint_orion_order_id,
    orion_order_id_sql_pattern,
)
from orion.execution.fill_processor import FillProcessor, maybe_snapshot_positions
from orion.execution.persistence import (
    has_processed_fill_for_order,
    persist_exit_decision,
    persist_exit_order_rejection,
    persist_order_finalize,
    persist_order_status_update,
    persist_pending_order,
)
from orion.execution.rate_limiter import get_order_rate_limiter
from orion.shared.alerts import send_discord_alert
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = setup_struct_logger(__name__)

# `ORDER_ID_PREFIX` is re-exported from orion.execution.attribution so
# existing imports (`from orion.execution.execution_engine import ORDER_ID_PREFIX`)
# continue to work. New code should import directly from `attribution`.
__all__ = ["ExecutionEngine", "ORDER_ID_PREFIX", "async_session_factory"]

# Per-service lease — a soft single-process guard backed by SystemStatus.
# Each service that creates an ExecutionEngine and wants single-instance
# enforcement calls `engine.acquire_service_lease("<service-id>")` before
# `engine.initialize()`. The actual implementation lives in
# `orion.core.service_lease`; these methods are thin wrappers that retain
# the engine instance state (`_lease_service_id`, `_lease_run_id`) so
# `renew_service_lease()` can be called with no arguments from the
# execution main loop. New code outside of ExecutionEngine should call
# the free functions in `orion.core.service_lease` directly.
#
# Constants are re-exported below so existing tests that import them
# from this module continue to work.
from orion.core.service_lease import (
    SERVICE_LEASE_KEY_PREFIX,
    SERVICE_LEASE_STALE_SECONDS,
    acquire_service_lease as _acquire_service_lease,
    renew_service_lease as _renew_service_lease,
)

__all__ += ["SERVICE_LEASE_KEY_PREFIX", "SERVICE_LEASE_STALE_SECONDS"]


class _SkipLeg(Exception):
    """Internal sentinel: a bracket leg was intentionally not placed."""


@dataclass
class _CancelState:
    """Per-order backoff state for the stale-entry-cancel sweep.

    Stops the self-inflicted 429 storm: a rejected cancel used to be re-issued
    every 5s forever. We now back off (transient) or give up (permanent) per
    order, keyed by broker_order_id on the engine instance.
    """

    attempts: int = 0
    next_eligible: float = 0.0  # time.monotonic() value before which we skip
    last_code: int | None = None
    gave_up: bool = False
    alerted: bool = False


# Permanent (non-retryable) Gateway/Alpaca markers for a rejected cancel. If any
# appears in the error/detail/code body, the order can never be cancelled via
# this path, so we give up immediately rather than backing off forever.
_CANCEL_PERMANENT_MARKERS: tuple[str, ...] = (
    "gw-e2009",
    "trading capability required",
)


def _cancel_backoff_jitter() -> float:
    """Random 0–1s jitter added to each backoff window so many stale orders that
    failed in the same sweep don't all retry on the same later tick (a fresh
    thundering herd). Isolated as a function so tests can monkeypatch it to 0."""
    return random.uniform(0.0, 1.0)


def _is_permanent_cancel_rejection(result: dict[str, Any]) -> bool:
    """True ONLY when a rejected cancel can never succeed — a known permanent
    Gateway/Alpaca marker (GW-E2009 / "trading capability required") in the
    error / detail / code body.

    Everything else — a 429, a 5xx, a generic 4xx, a timeout, an unknown shape —
    is treated as TRANSIENT: it still gives up after ``_CANCEL_MAX_ATTEMPTS``
    backed-off attempts, but is never stranded after a SINGLE attempt. Defaulting
    unknown rejections to transient is the safe bias: wrongly retrying a truly
    permanent reject wastes a few bounded calls, whereas wrongly declaring a
    transient reject permanent strands the order's day-trading buying power for
    the whole session after one attempt (a generic 408/409 can be retryable).
    Permanence is asserted only from explicit, known broker codes.
    """
    blob = f"{result.get('detail') or ''} {result.get('error') or ''} {result.get('code') or ''}".lower()
    return any(marker in blob for marker in _CANCEL_PERMANENT_MARKERS)


def _is_trading_capability_rejection_text(error: str) -> bool:
    """True when Gateway says this client key cannot mutate trading state."""
    return any(marker in error.lower() for marker in _CANCEL_PERMANENT_MARKERS)


# Alpaca rejects a cancel on a done order with `order is already in "<state>"
# state` (wrapped as GW-E8001 / code 42210000). poll_fills' 200-row status
# window can age an Orion order out before it sees the fill, so the sweep keeps
# cancelling an order the broker already closed — the 2026-06-22 storm where 182
# already-FILLED orders each gave up and paged a false "reserving DTBP" alert.
#
# The reject reaches us as the gateway's RAW response body (`exc.response.text`),
# where Alpaca's message is double-JSON-escaped, so the quotes around the state
# arrive as `\"`/`\\\"`, not a bare `"`. `\W+` matches any run of those quote /
# backslash / space chars between the words so the match is escaping-agnostic.
_CANCEL_ALREADY_TERMINAL_RE = re.compile(r"already in\W+(filled|canceled|cancelled|expired|rejected)\W+state")


def _parse_already_terminal_state(result: dict[str, Any]) -> str | None:
    """Return the broker's terminal state when a cancel was rejected because the
    order is ALREADY in it (filled/canceled/expired/rejected), else None.

    This is a state-desync to RECONCILE (flip the row terminal, stop sweeping),
    not a cancel failure to retry-and-page. ``cancelled`` is normalised to
    ``canceled`` to match the OrderRecord status vocabulary used elsewhere here.
    """
    blob = f"{result.get('detail') or ''} {result.get('error') or ''}".lower()
    match = _CANCEL_ALREADY_TERMINAL_RE.search(blob)
    if match is None:
        return None
    state = match.group(1)
    return "canceled" if state == "cancelled" else state


# Gateway 404 code for a cancel/get of an order whose client_order_id lacks the
# per-client `c-<client>-` ownership prefix the Gateway added on 2026-05-20.
# Orion orders placed BEFORE that date reached Alpaca as raw `orion_<uuid>`, so
# the Gateway's ownership guard now fail-closes their cancel with 404 GW-E4404.
# Such an order can NEVER be cancelled through the Gateway, so the sweep
# reconciles its orphaned row out instead of looping (the 2026-06-22..24
# GW-A4001/GW-E4404 retry flood — 1,164 warnings — was this case misclassified
# as a transient reject and re-attempted across sweeps and restarts).
_CANCEL_LEGACY_UNOWNED_MARKER = "gw-e4404"


def _is_legacy_unowned_cancel_rejection(result: dict[str, Any]) -> bool:
    """True ONLY when a cancel was rejected 404 GW-E4404 — a legacy pre-2026-05-20
    order the Gateway can't confirm Orion owns.

    Like ``_parse_already_terminal_state`` this is a state to RECONCILE (the order
    is unreachable through the Gateway forever), not a failure to retry-and-page.
    Scoped to the exact Gateway code, NOT a bare 404: another 404 on the cancel
    path may be legitimately retryable, so only the never-cancellable
    legacy-unowned case is reconciled out. Only called from the stale-entry
    cancel sweep, so the match is inherently cancel-path scoped.
    """
    blob = f"{result.get('detail') or ''} {result.get('error') or ''} {result.get('code') or ''}".lower()
    return _CANCEL_LEGACY_UNOWNED_MARKER in blob


def classify_close_failure(result: dict[str, Any]) -> Literal["confirmed_rejection", "ambiguous"]:
    """Classify a failed close-order Gateway response for escalation routing.

    Only a CONFIRMED broker rejection (HTTP 4xx, EXCLUDING 429) means the limit
    definitively did not rest, so escalating to a native flatten is safe.
    Anything else — a 429, a 5xx, a sub-400 code, a missing/None status_code, or
    a non-int shape — is AMBIGUOUS: the limit may have been accepted and be
    resting, so we must defer (never escalate), or a double-close could re-open
    a naked-short hole (the reverted-a388337 class of bug).

    A 429 is a TRANSIENT rate-limit (the self-inflicted storm), not a real
    rejection: the limit may rest fine once the storm clears. Escalating a 429
    to a NATIVE flatten would blind the daily-loss/drawdown kill switch (native
    closes aren't Orion-attributed), so 429 must defer-and-retry, never escalate.

    ``bool`` is an ``int`` subclass in Python, but a boolean status_code is a
    shape error, not a real HTTP status — treated as ambiguous and logged.
    """
    status_code = result.get("status_code")

    # bool is a subclass of int; reject it before the int check so True/False
    # can't masquerade as HTTP 1/0. A boolean here is an unexpected shape.
    if isinstance(status_code, bool):
        logger.warning(
            "close_failure_unclassified",
            reason="status_code is bool",
            status_code=status_code,
        )
        return "ambiguous"

    if isinstance(status_code, int):
        # 429 (rate limit) is transient — defer, never escalate to native.
        if status_code == 429:
            return "ambiguous"
        if 400 <= status_code < 500:
            return "confirmed_rejection"
        # 5xx, or any sub-400 code — server-side / transport-ambiguous.
        return "ambiguous"

    # Missing key or explicit None: GatewayTradingClient returns {"error": ...}
    # with no status_code on timeout / transport error — genuinely ambiguous,
    # no warning needed (this is the expected shape for those cases).
    if status_code is None:
        return "ambiguous"

    # Any other non-int shape (str, float, list, …) is unexpected — log it so
    # a Gateway error-shape change surfaces instead of silently misclassifying.
    logger.warning(
        "close_failure_unclassified",
        reason="status_code is non-int, non-None",
        status_code_type=type(status_code).__name__,
        status_code=str(status_code)[:80],
    )
    return "ambiguous"


def round_to_options_tick(price: float) -> float:
    """Round a price to Alpaca's options tick increment.

    Alpaca rejects options orders whose limit_price doesn't match the
    minimum tick:
      - price >= $3.00 -> $0.10 increments
      - price <  $3.00 -> $0.05 increments

    Sub-penny prices (e.g., a mid of $0.605, or float-precision artefacts
    like 5.789999999999999) produce 422 Unprocessable Entity at the
    broker, which we observed as `broker_order_id IS NULL` rows in the
    `orders` table. Rounding here keeps internal sizing math and the
    submitted price consistent (errors stay below one tick per contract).
    """
    if price <= 0:
        return 0.0
    tick = 0.10 if price >= 3.0 else 0.05
    return round(round(price / tick) * tick, 2)


def _extract_contract_greeks(contract: dict[str, Any]) -> dict[str, float] | None:
    """Pull per-contract (per-share) greeks off a Gateway chain contract.

    Returns delta/gamma/theta/vega, or None if delta/gamma/vega are missing or
    unparseable (theta is optional — tracking only). A real 0.0 is present data,
    not missing. Decimal/str/None are coerced the same way as bid/ask above.
    """

    def _coerce(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    delta = _coerce(contract.get("delta"))
    gamma = _coerce(contract.get("gamma"))
    vega = _coerce(contract.get("vega"))
    if delta is None or gamma is None or vega is None:
        return None
    theta = _coerce(contract.get("theta")) or 0.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def _project_position_greeks(contract_greeks: dict[str, float] | None, num_contracts: int) -> dict[str, float] | None:
    """Scale per-share contract greeks to share-equivalent position greeks:
    per-share greek × 100 shares/contract × num_contracts."""
    if contract_greeks is None:
        return None
    mult = 100.0 * num_contracts
    return {
        "delta": contract_greeks["delta"] * mult,
        "gamma": contract_greeks["gamma"] * mult,
        "theta": contract_greeks["theta"] * mult,
        "vega": contract_greeks["vega"] * mult,
    }


def _greeks_gate_blocks_on_missing(greeks_enabled: bool) -> bool:
    """Fail-safe policy when per-contract greeks are unavailable.

    Block only in real-money stages (anything other than paper/test) and only
    when greek checks are enabled. paper/test fail open (the caller logs a WARN
    and skips the greek gate). Operators can disable the gate entirely with
    ORION_RISK_ENABLE_GREEKS_CHECKS=False.
    """
    if not greeks_enabled:
        return False
    return system_settings.orion_stage.lower() not in ("paper", "test")


class ExecutionEngine:
    """
    Translates Agent decisions into broker orders.

    Trading is routed through the Data Gateway which proxies to Alpaca.
    Options-only: candidates without an option_symbol are rejected.

    Paper mode is controlled by the Data Gateway's Alpaca configuration.
    """

    def __init__(self) -> None:
        from orion.execution.risk.manager import RiskManager

        self.risk_manager = RiskManager()

        # Data Gateway trading client (lazy-initialized via _get_gateway_client)
        self._gateway_client: Any = None
        self._gateway_available = False
        # Lazy: set on first _check_gateway_available / market-open check.
        self._gateway_check_ts: datetime | None = None
        self._market_schedule: Any = None

        # Time-windowed broker-result history for the per-process circuit
        # breaker. Each entry is (monotonic_ts, success). Bounded by the
        # configured window, not by entry count — see _check_circuit_breaker
        # and _prune_order_history.
        self.order_history: deque[tuple[float, bool]] = deque()
        self.last_positions_snapshot_ts: datetime | None = None
        self._last_fill_poll_ts: datetime | None = None
        self._last_order_poll_ts: datetime | None = None
        self._last_position_sync_ts: datetime | None = None

        # Per-order backoff/give-up state for the stale-entry-cancel sweep,
        # keyed by broker_order_id. Pruned each sweep to orders still stale.
        self._cancel_attempts: dict[str, _CancelState] = {}

        # TTL cache for _check_system_health (avoids N identical DB queries per cycle)
        self._health_cache: tuple[bool, float] | None = None
        self._health_cache_ttl: float = 10.0

        # Per-service single-instance lease (None until acquire_service_lease is called).
        self._lease_service_id: str | None = None
        self._lease_run_id: str | None = None

        # Empire-core ledger for EmpireUI trade recording
        self._ledger = None
        try:
            from empire_core.ledger import LedgerWriter
            from orion.core.ledger_adapter import OrionLedgerAdapter

            writer = LedgerWriter(db_path="ledger.db", system="orion")
            self._ledger = OrionLedgerAdapter(writer)
            logger.info("Ledger adapter initialized")
        except Exception as exc:
            logger.warning("Ledger adapter init failed (non-fatal)", error=str(exc))

        # Fill processor (handles partial fills, persistence)
        self._fill_processor = FillProcessor(ledger=self._ledger)

    # ── Service lease (single-process enforcement) ───────────────────────
    #
    # Thin wrappers over `orion.core.service_lease` that retain the lease
    # identity on the engine instance so `renew_service_lease()` can be
    # called argument-less from the main execution loop. New callers
    # outside ExecutionEngine should use the free functions directly.

    @staticmethod
    def _service_lease_key(service_id: str) -> str:
        return f"{SERVICE_LEASE_KEY_PREFIX}{service_id}"

    async def acquire_service_lease(self, service_id: str) -> None:
        """Acquire a single-instance lease for ``service_id``.

        Delegates to ``orion.core.service_lease.acquire_service_lease``
        and stashes the returned run_id on the instance so subsequent
        ``renew_service_lease()`` calls don't need it passed in.

        Raises RuntimeError if another fresh lease is owned by a
        different run_id; see the free function for the full semantics.
        """
        run_id = await _acquire_service_lease(service_id)
        self._lease_service_id = service_id
        self._lease_run_id = run_id

    async def renew_service_lease(self) -> None:
        """Refresh the lease's ``last_updated_utc`` so other processes treat it as live.

        No-op if ``acquire_service_lease`` was never called. Errors are
        logged but do not propagate — a transient DB blip should not
        abort the main execution loop.
        """
        if not self._lease_service_id or not self._lease_run_id:
            return
        await _renew_service_lease(self._lease_service_id, self._lease_run_id)

    def _get_gateway_client(self) -> Any:
        """Lazy-initialize Gateway trading client singleton."""
        if self._gateway_client is None:
            from orion.clients.gateway_trading_client import get_gateway_trading_client

            self._gateway_client = get_gateway_trading_client()
        return self._gateway_client

    # Close is a critical path — a transient Gateway blip must not abandon an
    # exit for a full monitor cycle. The close path retries a fresh
    # (cache-bypassing) availability probe a few times before giving up.
    _CLOSE_GATEWAY_RETRY_ATTEMPTS: int = 3
    _CLOSE_GATEWAY_RETRY_BACKOFF_SECONDS: float = 1.0

    async def _check_gateway_available(self, force: bool = False) -> bool:
        """Check if Data Gateway is reachable. Caches result for 60s unless
        `force` is set (the critical close path bypasses a possibly-stale
        cached blip)."""
        if not force and hasattr(self, "_gateway_check_ts") and self._gateway_check_ts:
            elapsed = (datetime.now(UTC) - self._gateway_check_ts).total_seconds()
            if elapsed < 60:
                return self._gateway_available

        try:
            client = self._get_gateway_client()
            result = await client.get_clock()
            self._gateway_available = "error" not in result
        except Exception:
            logger.error("Gateway availability check failed", exc_info=True)
            self._gateway_available = False

        self._gateway_check_ts = datetime.now(UTC)

        if not self._gateway_available:
            logger.warning(
                "Data Gateway unavailable; execution engine in degraded mode",
                extra={"event_type": "GATEWAY_UNAVAILABLE"},
            )
        else:
            logger.info(
                "Data Gateway connection verified",
                extra={"event_type": "GATEWAY_AVAILABLE"},
            )

        return self._gateway_available

    async def _gateway_available_for_close(self, ticker: str) -> bool:
        """Critical-path availability check for closes: retry a fresh probe
        with backoff before giving up so a transient blip doesn't abandon an
        exit for a full monitor cycle."""
        for attempt in range(1, self._CLOSE_GATEWAY_RETRY_ATTEMPTS + 1):
            if await self._check_gateway_available(force=True):
                return True
            if attempt < self._CLOSE_GATEWAY_RETRY_ATTEMPTS:
                logger.warning(
                    "close_gateway_unavailable_retrying",
                    extra={
                        "event_type": "CLOSE_GATEWAY_RETRY",
                        "ticker": ticker,
                        "attempt": attempt,
                        "max_attempts": self._CLOSE_GATEWAY_RETRY_ATTEMPTS,
                    },
                )
                await asyncio.sleep(self._CLOSE_GATEWAY_RETRY_BACKOFF_SECONDS)
        return False

    async def _live_position_qty(self, ticker: str) -> float | None:
        """Signed live broker qty for `ticker`: 0.0 if flat, None if it could
        not be determined (caller must fail safe and NOT submit a close —
        submitting against an unknown position risks opening a naked short)."""
        try:
            pos = await self._get_gateway_client().get_position(ticker)
        except Exception as e:
            logger.warning(
                f"Live position fetch failed for {ticker}: {e}",
                extra={"event_type": "LIVE_POSITION_FETCH_ERROR", "ticker": ticker, "error": str(e)},
            )
            return None
        if not isinstance(pos, dict):
            return None
        if "error" in pos:
            return 0.0  # broker reports no such position → flat, nothing to reduce
        raw = pos.get("qty")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    _ACCOUNT_CACHE_TTL_SECONDS = 30.0

    async def _get_cached_account(self) -> dict[str, Any]:
        """Account snapshot from the Gateway, cached for a short TTL so the
        per-order buying-power gate doesn't add a round-trip to every order."""
        now = time.monotonic()
        ts = getattr(self, "_account_cache_ts", None)
        cached = getattr(self, "_account_cache", None)
        if cached is not None and ts is not None and (now - ts) < self._ACCOUNT_CACHE_TTL_SECONDS:
            return cached
        acct = await self._get_gateway_client().get_account()
        self._account_cache = acct
        self._account_cache_ts = now
        return acct

    _DTBP_BACKOFF_SECONDS = 120.0
    _TRADING_CAPABILITY_BACKOFF_SECONDS = 3600.0

    def _in_dtbp_backoff(self) -> bool:
        """True while inside the cooldown set after a CONFIRMED broker DTBP
        rejection (40310000), so Orion stops submitting opening orders after the
        first confirmed wall instead of hammering — even when the proactive
        account check fails open on a degraded/malformed read (adversarial
        review)."""
        return time.monotonic() < getattr(self, "_dtbp_backoff_until", 0.0)

    def _note_dtbp_rejection(self) -> None:
        """Arm the DTBP backoff after a broker 40310000 rejection."""
        self._dtbp_backoff_until = time.monotonic() + self._DTBP_BACKOFF_SECONDS

    def _in_trading_capability_backoff(self) -> bool:
        """True while the Gateway key is known unable to submit/cancel orders."""
        return time.monotonic() < getattr(self, "_trading_capability_backoff_until", 0.0)

    def _note_trading_capability_rejection(self) -> None:
        """Arm the Gateway trading-capability backoff after GW-E2009."""
        self._trading_capability_backoff_until = time.monotonic() + self._TRADING_CAPABILITY_BACKOFF_SECONDS

    async def _has_daytrading_buying_power(self, estimated_cost: float) -> bool:
        """True unless we can positively read that the shared account's
        day-trading buying power can't cover ``estimated_cost``.

        Fails OPEN (returns True) on any read/parse problem — a missing field or
        a transient account-read error must never block trading. This only
        exists to stop Orion hammering the Gateway with opening orders Alpaca
        will reject 40310000 when the shared paper account's day-trading buying
        power is exhausted (2026-06-02: 193 rejected buys in one day).
        """
        try:
            acct = await self._get_cached_account()
        except Exception:
            return True
        if not isinstance(acct, dict) or "error" in acct:
            return True
        raw = acct.get("daytrading_buying_power")
        if raw is None:
            return True  # field absent → don't gate
        try:
            dtbp = float(raw)
        except (TypeError, ValueError):
            return True
        return dtbp >= estimated_cost

    async def initialize(self) -> None:
        """
        Loads the last 20 execution attempts and initializes RiskManager state.
        """
        if hasattr(self.risk_manager, "initialize"):
            await self.risk_manager.initialize()

        await self._sync_risk_from_gateway()

        # NOTE: We deliberately do NOT seed `order_history` from past
        # `strategy_decisions` rows. `executed_successfully=FALSE` in that
        # table covers every pre-flight rejection (DTE gate, data-lag gate,
        # health gate, risk rejection, shorting disabled, etc.) — not just
        # broker-submission failures, which is what `_check_circuit_breaker`
        # actually wants to monitor. Backfilling created a self-reinforcing
        # poison pill: yesterday's session had 104 EXECUTEs all rejected at
        # pre-flight (system-status STALE bug), which on restart filled the
        # 20-slot deque with [False]*20, immediately tripped the 3% error-
        # rate breaker, and prevented any new orders from being attempted —
        # which then prevented any True from landing to clear the history.
        # The runtime tracking at _record_result() (lines 601 / 639 / 784 /
        # 792) only logs *actual* broker round-trips, which is what the
        # breaker is meant to measure. Start the deque empty.
        logger.info(
            "ExecutionEngine initialized",
            extra={"event_type": "EXECUTION_INIT", "loaded_history_count": 0},
        )

    async def _fetch_orion_tickers(self) -> set[str] | None:
        """Return the set of broker symbols Orion has actually filled.

        Sourced from ``fills`` (not ``orders``) so that for options the
        set contains full OCC contract symbols, enabling per-contract
        attribution against the broker's position symbols. See
        ``position_monitor._fetch_orion_attributed_tickers`` for the
        full rationale (codex review 2026-05-26 CRITICAL on commit
        39174f8).
        """
        from orion.storage.models_execution import FillRecord

        async def query_tickers(session: Any) -> set[str]:
            stmt = (
                select(FillRecord.ticker)
                .where(FillRecord.client_order_id.like(orion_order_id_sql_pattern()))
                .distinct()
            )
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}

        try:
            return await db_query(query_tickers)
        except Exception as exc:
            logger.error(
                "Failed to fetch Orion-owned tickers for shared-account position filtering",
                extra={"event_type": "ORION_TICKER_LOOKUP_FAILED", "error": str(exc)},
                exc_info=True,
            )
            return None

    async def _compute_cost_basis_from_fills(self) -> dict[str, dict[str, float]]:
        """Return Orion-only per-position cost basis by replaying FillRecord.

        On the shared Alpaca paper account, the broker's ``avg_entry_price``
        is blended across all systems holding the same instrument.  This method
        re-derives ``avg_entry`` purely from fills with an ``orion_``-prefixed
        ``client_order_id``, using the same weighted-average logic as
        ``RiskManager.process_fill``, so restarts always get an uncontaminated
        cost basis.

        Returns ``{symbol: {"qty": float, "avg_entry": float}}`` for every
        symbol with a non-zero computed quantity.  Returns an empty dict on
        any DB failure (caller falls back to broker value).
        """
        from orion.storage.models_execution import FillRecord

        async def query_fills(session: Any) -> list[FillRecord]:
            order_ts = func.coalesce(FillRecord.filled_at_utc, FillRecord.created_at_utc)
            stmt = (
                select(FillRecord)
                .where(FillRecord.client_order_id.like(orion_order_id_sql_pattern()))
                # Secondary sort by id for determinism when two fills share a timestamp
                # (possible when partial fills are upserted — filled_at_utc reflects the
                # first partial, not the final fill event).
                .order_by(order_ts, FillRecord.id)
            )
            result = await session.execute(stmt)
            return result.scalars().all()

        try:
            fills = await db_query(query_fills)
        except Exception as exc:
            logger.error(
                "Failed to compute cost basis from fills; will fall back to broker avg_entry_price",
                extra={"event_type": "COST_BASIS_COMPUTE_FAILED", "error": str(exc)},
                exc_info=True,
            )
            return {}

        positions: dict[str, dict[str, float]] = {}
        for fill in fills:
            ticker = fill.ticker
            qty = float(fill.filled_qty or 0)
            price = float(fill.filled_avg_price or 0)
            side = (fill.side or "").lower()

            if qty <= 0 or price <= 0:
                continue

            sign = 1 if side == "buy" else -1
            # abs() mirrors process_fill's own defensive coding (line 682 in manager.py);
            # guards against any broker-side event that delivers a signed filled_qty.
            signed_qty = abs(qty) * sign

            current = positions.get(ticker, {"qty": 0.0, "avg_entry": 0.0})
            old_qty = current["qty"]
            old_entry = current["avg_entry"]
            new_qty = old_qty + signed_qty
            is_closing = (old_qty > 0 and signed_qty < 0) or (old_qty < 0 and signed_qty > 0)

            if not is_closing:
                total_val = (old_qty * old_entry) + (signed_qty * price)
                new_avg = total_val / new_qty if abs(new_qty) > 1e-9 else 0.0
                positions[ticker] = {"qty": new_qty, "avg_entry": new_avg}
            elif abs(signed_qty) > abs(old_qty):
                # Overshoot / flip
                positions[ticker] = {"qty": new_qty, "avg_entry": price}
            elif math.isclose(new_qty, 0, abs_tol=1e-9):
                positions[ticker] = {"qty": 0.0, "avg_entry": 0.0}
            else:
                positions[ticker] = {"qty": new_qty, "avg_entry": old_entry}

        return positions

    async def _sync_risk_from_gateway(self) -> None:
        """Sync risk manager state from Data Gateway (account + positions).

        Only positions for tickers that Orion has traded are loaded into the
        risk manager.  The Alpaca paper account is shared by multiple systems
        via Data-Gateway, so we must filter to avoid counting other systems'
        positions in Orion's risk calculations.
        """
        # Prune stale pending orders (DB-only, gateway-independent). Expired
        # day orders never fire a fill, so they linger as phantom pending
        # exposure until restart unless swept at runtime (RCA 2026-06-05).
        prune = getattr(self.risk_manager, "prune_stale_pending_orders", None)
        if prune is not None:
            try:
                await prune()
            except Exception as e:
                logger.debug(f"pending order prune skipped: {e}")

        if not await self._check_gateway_available():
            logger.info(
                "Skipping Gateway risk sync; server unavailable. Using persisted risk state.",
                extra={"event_type": "GATEWAY_RISK_SYNC_SKIPPED"},
            )
            return

        try:
            client = self._get_gateway_client()

            account = await client.get_account()
            if "error" not in account:
                equity = float(account.get("equity", 0) or 0)
                float(account.get("last_equity", 0) or account.get("equity", 0) or 0)

                if equity > 0:
                    # The paper Alpaca account is shared with
                    # 3Roses/Cerberus/Kairos/Orbit/WhaleHunter, so the
                    # account-wide `equity` and `last_equity` include
                    # their P&L. Overwriting Orion-only running totals
                    # with that pool falsely trips Orion's drawdown /
                    # daily-loss kill switches when other systems lose.
                    #
                    # Seed-once pattern (mirrors current_daily_loss /
                    # peak_equity): take the first Gateway equity as the
                    # Orion baseline; afterwards `current_equity` only
                    # moves from Orion-attributed fills via
                    # `update_post_fill` (manager.py line 590), and
                    # `peak_equity` is bumped by
                    # `_evaluate_drawdown_kill_switch` when
                    # current_equity rises above it.
                    # Seed the Orion equity baseline ONCE, capped to the
                    # allocated slice (RiskManager.seed_equity_baseline). The
                    # shared account reports pooled equity across all systems;
                    # peak seeds to the same capped baseline so drawdown starts
                    # at 0% (using max(equity, last_equity) historically pulled
                    # a stale cross-system high and instantly tripped drawdown).
                    self.risk_manager.seed_equity_baseline(equity)

                    # Daily loss is Orion-attributed only: driven by update_post_fill
                    # from Orion-owned fills (client_order_id prefix "orion_"), not
                    # by account-wide equity delta. We DO NOT overwrite
                    # self.risk_manager.current_daily_loss here.

                    logger.info(
                        "Risk state synced from Gateway account",
                        extra={
                            "event_type": "GATEWAY_RISK_SYNC",
                            "equity": equity,
                            "peak_equity": self.risk_manager.peak_equity,
                            "daily_loss": self.risk_manager.current_daily_loss,
                        },
                    )

            # Filter positions to Orion-owned tickers only
            orion_tickers = await self._fetch_orion_tickers()
            if orion_tickers is None:
                logger.error(
                    "Skipping Gateway position sync because Orion-owned tickers could not be determined",
                    extra={"event_type": "GATEWAY_POSITIONS_SYNC_ABORTED"},
                )
                return

            positions = await client.get_positions()
            # Derive Orion-only cost basis from fills before clearing the
            # positions dict.  The broker's avg_entry_price is blended across
            # all systems on the shared paper account; using it after restart
            # corrupts realized-PnL and kill-switch accounting.
            cost_basis = await self._compute_cost_basis_from_fills()
            # CRITICAL: This block ALWAYS runs, including when `positions` is
            # an empty list. The previous `if positions:` guard meant that
            # when the broker returned 0 orion-attributable positions, the
            # risk_manager's `open_positions` count stayed at whatever was
            # loaded from `risk_state.open_positions_count` — observed live
            # on 2026-05-21 stuck at 5 ghost positions, causing every
            # EXECUTE to fail preflight with "RISK REJECT: Max Positions 5
            # Reached" despite the broker reporting zero. The risk
            # manager's view of "how many positions am I holding" MUST be
            # ground-truthed against the broker on every sync.
            self.risk_manager.positions = {}
            self.risk_manager.ticker_exposures = {}
            skipped = 0
            for p in positions:
                symbol = p.get("symbol", "")
                # Only load positions Orion has actually filled. Empty
                # orion_tickers means Orion owns no positions at all —
                # skip everything (None is the error sentinel, handled
                # above).
                #
                # `orion_tickers` is sourced from `fills.ticker`, which
                # stores the full OCC contract for options and the
                # underlying for equity — both match the broker's symbol
                # verbatim. Per-contract attribution, fixes codex review
                # 2026-05-26 CRITICAL on commit 39174f8 (underlying-only
                # matching let sibling systems' same-underlying options
                # leak into Orion's risk and exit pipelines).
                if symbol not in orion_tickers:
                    skipped += 1
                    continue

                qty = float(p.get("qty", 0) or 0)
                # Use fill-derived cost basis; fall back to broker value only
                # when no orion fills exist for this symbol.
                # Prefer fill-derived cost basis (Orion-only, uncontaminated).
                # Falls back to broker avg_entry_price when:
                #   (a) no orion fills exist for this symbol yet, OR
                #   (b) replay yields avg_entry=0.0 (position closed in FillRecord but
                #       broker still shows it — likely a timing gap or another system's
                #       position leaking through _fetch_orion_tickers; using 0.0 entry
                #       would make PnL worse, so broker value is the lesser evil here).
                avg_entry = cost_basis.get(symbol, {}).get("avg_entry") or float(p.get("avg_entry_price", 0) or 0)
                market_value = float(p.get("market_value", 0) or 0)

                self.risk_manager.positions[symbol] = {"qty": qty, "avg_entry": avg_entry}
                self.risk_manager.ticker_exposures[symbol] = abs(market_value)

            self.risk_manager.open_positions = len(
                [p for p in self.risk_manager.positions.values() if abs(p["qty"]) > 1e-9]
            )
            logger.info(
                "Positions synced from Gateway (Orion-only)",
                extra={
                    "event_type": "GATEWAY_POSITIONS_SYNC",
                    "open_positions": self.risk_manager.open_positions,
                    "skipped_non_orion": skipped,
                    "total_account_positions": len(positions),
                },
            )

            if hasattr(self.risk_manager, "evaluate_drawdown_kill_switch"):
                await self.risk_manager.evaluate_drawdown_kill_switch()

        except Exception as e:
            logger.error(
                "Failed to sync risk state from Gateway",
                extra={"event_type": "GATEWAY_RISK_SYNC_ERROR", "error": str(e)},
                exc_info=True,
            )

    # ── Order execution ──────────────────────────────────────────────────

    async def execute_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        if not await self._check_gateway_available():
            logger.warning(
                "Data Gateway unavailable. Order logged but not submitted.",
                extra={
                    "event_type": "EXECUTION_NOOP",
                    "ticker": candidate.ticker,
                    "decision": decision.decision,
                },
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Data Gateway Unavailable"
            return

        action = decision.decision.upper()
        if action != "EXECUTE":
            logger.info("decision_skipped", ticker=candidate.ticker, action=action)
            return

        if isinstance(candidate.direction, str):
            candidate.direction = candidate.direction.upper()

        if not candidate.option_symbol:
            logger.warning(
                "execution_rejected_no_option_symbol",
                ticker=candidate.ticker,
                reason="Options only — no option_symbol on candidate",
            )
            decision.executed_successfully = DecisionStatus.SKIPPED
            decision.reason = "Options only"
            return

        await self._execute_options_order(decision, candidate)

    async def _remove_pending_order_compat(self, order_id: str | None) -> None:
        """Support both sync and async remove_pending_order implementations."""
        if not order_id or not hasattr(self.risk_manager, "remove_pending_order"):
            return
        maybe_result = self.risk_manager.remove_pending_order(order_id)
        if asyncio.iscoroutine(maybe_result):
            await maybe_result

    async def _execute_options_order(self, decision: StrategyDecision, candidate: CandidateTrade) -> None:
        """Execute an options order via Data Gateway."""

        if not await self._pre_flight_checks(decision, candidate):
            return

        dte: int | None = None
        if candidate.expiration_date:
            now = datetime.now(UTC)
            dte = (candidate.expiration_date - now).days
            if dte < risk_settings.min_dte:
                logger.warning(
                    "options_blocked_dte_low", dte=dte, min_dte=risk_settings.min_dte, ticker=candidate.ticker
                )
                decision.executed_successfully = DecisionStatus.FALSE
                decision.reason = f"DTE Too Low ({dte} days)"
                return

        # Always fetch live option chain for current pricing — candidate.premium
        # is signal-time data and may be stale. Live mid/ask is needed for
        # accurate order sizing, risk checks, and limit price.
        option_price = None
        contract_greeks: dict[str, float] | None = None
        client = self._get_gateway_client()
        chain_result = await client.get_option_chain(candidate.ticker)

        if "error" not in chain_result and candidate.option_symbol:
            contracts = chain_result.get("contracts", [])
            for contract in contracts:
                # Gateway returns `contract_symbol`; `symbol` is for the underlying.
                if contract.get("contract_symbol") == candidate.option_symbol:
                    # Same chain response carries per-contract greeks — capture
                    # them here so the risk gate below has no extra round-trip.
                    contract_greeks = _extract_contract_greeks(contract)
                    bid = contract.get("bid")
                    ask = contract.get("ask")
                    try:
                        bid_f = float(bid) if bid not in (None, "") else 0.0
                        ask_f = float(ask) if ask not in (None, "") else 0.0
                    except (TypeError, ValueError):
                        bid_f = ask_f = 0.0
                    if bid_f > 0 and ask_f > 0:
                        option_price = (bid_f + ask_f) / 2
                    elif ask_f > 0:
                        option_price = ask_f
                    elif bid_f > 0:
                        option_price = bid_f
                    else:
                        last = contract.get("last")
                        try:
                            last_f = float(last) if last not in (None, "") else 0.0
                        except (TypeError, ValueError):
                            last_f = 0.0
                        if last_f > 0:
                            option_price = last_f
                    break

        # NOTE: `candidate.premium` is the UW-flow event's aggregate premium
        # (sum of all contracts in the sweep) — NOT a per-contract price. Using
        # it as option_price produced `options_calculated_0_contracts` because
        # risk_dollars / (34075 * 100) rounds to 0. We now fail closed instead.
        if not option_price or option_price <= 0:
            logger.error("options_price_fetch_failed", option_symbol=candidate.option_symbol, ticker=candidate.ticker)
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Option Price Fetch Failed"
            return

        # Snap the price to Alpaca's options tick increment. Prior to this,
        # mid-quotes like (bid 0.60 + ask 0.61) / 2 = 0.605 and float-
        # precision artefacts (5.789999999999999) were rejected at the
        # broker as `422 Unprocessable Entity`, which surfaced as orders
        # with status='' / broker_order_id IS NULL.
        option_price = round_to_options_tick(option_price)
        if option_price <= 0:
            logger.error(
                "options_price_rounded_to_zero",
                option_symbol=candidate.option_symbol,
                ticker=candidate.ticker,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Option Price Rounded To Zero"
            return

        # Solver-driven sizing: use risk_per_trade_bps × regime_size_multiplier
        # when available, with max_option_premium_pct as safety ceiling.
        ep = decision.execution_params or {}
        risk_bps = float(ep.get("risk_per_trade_bps", 0))
        regime_mult = float(ep.get("regime_size_multiplier", 1.0))
        max_premium = self.risk_manager.current_equity * risk_settings.max_option_premium_pct

        if risk_bps > 0:
            risk_dollars = (self.risk_manager.current_equity * risk_bps / 10000.0) * regime_mult
            risk_dollars = min(risk_dollars, max_premium)
        else:
            risk_dollars = max_premium

        num_contracts = max(0, int(risk_dollars / (option_price * 100)))

        if num_contracts <= 0:
            logger.warning(
                "options_calculated_0_contracts",
                option_symbol=candidate.option_symbol,
                ticker=candidate.ticker,
                option_price=option_price,
            )
            decision.executed_successfully = DecisionStatus.SKIPPED
            decision.reason = "Size 0 Contracts"
            return

        if dte is not None and dte == 0:
            allowed, reason = self.risk_manager.check_zero_dte_winddown(dte)
            if not allowed:
                logger.warning(
                    "options_blocked_zero_dte_winddown",
                    ticker=candidate.ticker,
                    option_symbol=candidate.option_symbol,
                    reason=reason,
                )
                decision.executed_successfully = DecisionStatus.FALSE
                decision.reason = f"0DTE Wind-Down: {reason}"
                return

            multiplier = self.risk_manager.get_zero_dte_size_multiplier(dte)
            if multiplier < 1.0:
                original_contracts = num_contracts
                num_contracts = max(1, int(num_contracts * multiplier))
                logger.info(
                    "zero_dte_size_reduced",
                    ticker=candidate.ticker,
                    original_contracts=original_contracts,
                    reduced_contracts=num_contracts,
                    multiplier=multiplier,
                )

        # Options-open is always BUY (calls on LONG bet, puts on SHORT bet);
        # SHORT does not mean shorting the contract. Exit uses the inverted
        # side in the position_monitor close path.
        side_value = OrderSide.BUY
        notional = option_price * 100

        # Greeks gate. Per-contract greeks → share-equivalent position greeks
        # (per-share × 100 × num_contracts). When available, enforce the
        # configured portfolio/position limits via check_options_order; when
        # unavailable, behavior is stage-gated (block in live, warn+skip in
        # paper/test) per _greeks_gate_blocks_on_missing.
        position_greeks = _project_position_greeks(contract_greeks, num_contracts)
        # Bind to the config the risk manager actually enforces greeks under so
        # the fail-safe decision matches check_greeks_limits' own toggle.
        greeks_enabled = getattr(
            getattr(self.risk_manager, "config", None), "enable_greeks_checks", risk_settings.enable_greeks_checks
        )

        if position_greeks is not None:
            risk_ok = self.risk_manager.check_options_order(
                candidate.ticker,
                num_contracts,
                notional,
                side_value,
                delta=position_greeks["delta"],
                gamma=position_greeks["gamma"],
                vega=position_greeks["vega"],
            )
        elif _greeks_gate_blocks_on_missing(greeks_enabled):
            logger.error(
                "options_blocked_greeks_unavailable",
                ticker=candidate.ticker,
                option_symbol=candidate.option_symbol,
                stage=system_settings.orion_stage,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Greeks Unavailable"
            return
        else:
            if greeks_enabled:
                logger.warning(
                    "greeks_unavailable_skipping_gate",
                    ticker=candidate.ticker,
                    option_symbol=candidate.option_symbol,
                    stage=system_settings.orion_stage,
                )
            risk_ok = self.risk_manager.check_order(candidate.ticker, num_contracts, notional, side_value)

        if not risk_ok:
            logger.error(
                "options_execution_blocked_by_risk",
                ticker=candidate.ticker,
                option_symbol=candidate.option_symbol,
                contracts=num_contracts,
                option_price=option_price,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Risk Rejection"
            return

        if self._check_circuit_breaker():
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "High Error Rate"
            return

        await self._submit_options_order(decision, candidate, num_contracts, option_price, position_greeks)

    async def _pre_flight_checks(self, decision: StrategyDecision, candidate: CandidateTrade) -> bool:
        """System Health, Data Lag, Shorting Checks"""
        if not await self._check_system_health():
            msg = "EXECUTION BLOCKED: System Status is UNHEALTHY."
            logger.critical(msg, extra={"event_type": "EXECUTION_BLOCKED", "ticker": candidate.ticker})
            decision.executed_successfully = DecisionStatus.FALSE
            decision.execution_log = msg
            return False

        # Options-open is always BUY (see _execute_options_candidate comment);
        # the shorting-disabled gate therefore never fires on the open path.
        # Leave it in place so anyone wiring a future equity short-sale flow
        # through this check gets blocked by default.
        side = OrderSide.BUY
        exposure = self.risk_manager.ticker_exposures.get(candidate.ticker, 0.0)
        is_short_opening = side == OrderSide.SELL and exposure <= 0

        if is_short_opening and not self.risk_manager.config.enable_shorting:
            logger.warning("Execution BLOCKED: Shorting is disabled")
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Shorting Disabled"
            return False

        now_utc = datetime.now(UTC)
        cand_ts = ensure_utc(candidate.timestamp_utc)
        assert cand_ts is not None  # candidate timestamp_utc is non-nullable
        lag = (now_utc - cand_ts).total_seconds()

        if lag > system_settings.max_data_lag_seconds:
            logger.critical(
                "execution_blocked_data_lag",
                lag_seconds=lag,
                max_lag=system_settings.max_data_lag_seconds,
                ticker=candidate.ticker,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Data Lag"
            return False

        return True

    def _check_circuit_breaker(self) -> bool:
        """Trip when the broker-error rate exceeds threshold within the
        configured time window AND we've seen at least min_samples broker
        round-trips.

        Returns True iff trading should be blocked.
        """
        self._prune_order_history()
        total = len(self.order_history)
        min_samples = system_settings.circuit_breaker_min_samples
        if total < min_samples:
            # Avoid tripping on a single failure right after restart — the
            # deliberate "do not seed history" decision means the deque is
            # always empty post-restart, so the first ~min_samples broker
            # calls run without breaker pressure.
            return False
        failures = sum(1 for _, success in self.order_history if not success)
        rate = failures / total
        threshold = system_settings.circuit_breaker_error_rate
        if rate > threshold:
            if not system_settings.circuit_breaker_enabled:
                logger.warning(
                    "execution_circuit_breaker_would_trip_but_disabled",
                    error_rate=rate,
                    limit=threshold,
                    window_seconds=system_settings.circuit_breaker_window_seconds,
                    samples=total,
                    failures=failures,
                )
                return False
            logger.critical(
                "execution_blocked_error_rate",
                error_rate=rate,
                limit=threshold,
                window_seconds=system_settings.circuit_breaker_window_seconds,
                samples=total,
                failures=failures,
            )
            return True
        return False

    def _prune_order_history(self) -> None:
        """Drop history entries older than the configured window."""
        cutoff = time.monotonic() - system_settings.circuit_breaker_window_seconds
        while self.order_history and self.order_history[0][0] < cutoff:
            self.order_history.popleft()

    async def _submit_options_order(
        self,
        decision: Any,
        candidate: Any,
        num_contracts: int,
        option_price: float,
        position_greeks: dict[str, float] | None = None,
    ) -> None:
        """Submit an options order via Data Gateway."""
        logger.info(
            "options_execution_triggered",
            num_contracts=num_contracts,
            option_symbol=candidate.option_symbol,
            option_price=option_price,
            ticker=candidate.ticker,
        )

        client_order_id = mint_orion_order_id()
        decision.execution_params = decision.execution_params or {}
        decision.execution_params["client_order_id"] = client_order_id
        decision.execution_params["order_type"] = "OPTIONS"
        decision.execution_params["contracts"] = num_contracts

        # Pre-trade buying-power gate (RCA 2026-06-02). The shared Alpaca paper
        # account periodically exhausts day-trading buying power (Alpaca
        # 40310000) — mostly driven by sibling systems — after which every new
        # opening order is rejected. Orion contributed 193 rejected buys in a
        # single day by hammering straight through it. Back off early here, with
        # a clear reason, before reserving rate-limiter / risk / pending-order
        # state. Fails OPEN, so a transient account-read blip never blocks us.
        estimated_cost = num_contracts * option_price * 100.0
        if self._in_dtbp_backoff() or not await self._has_daytrading_buying_power(estimated_cost):
            logger.warning(
                "insufficient_daytrading_buying_power",
                ticker=candidate.ticker,
                option_symbol=candidate.option_symbol,
                estimated_cost=estimated_cost,
                backoff_active=self._in_dtbp_backoff(),
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Insufficient day-trading buying power (shared account)"
            return

        if self._in_trading_capability_backoff():
            logger.error(
                "gateway_trading_capability_backoff_active",
                ticker=candidate.ticker,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Gateway key lacks trading capability"
            return

        # Orion only opens options positions from candidates — it buys calls on
        # a LONG bet and buys puts on a SHORT bet. Both are BUYs at the broker.
        # The SHORT direction reflects a bearish view on the underlying, not a
        # short-sale of the contract. Exit/close flows (see position_monitor)
        # own the OrderSide.SELL path.
        side = OrderSide.BUY

        rate_limiter = get_order_rate_limiter()
        if not await rate_limiter.acquire(timeout=10.0):
            logger.warning(
                "rate_limit_reached",
                ticker=candidate.ticker,
                capacity=rate_limiter.available_capacity,
                max_capacity=rate_limiter.max_per_minute,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = "Rate limit exceeded"
            return

        if hasattr(self.risk_manager, "update_post_trade"):
            await self.risk_manager.update_post_trade(
                ticker=candidate.ticker,
                qty=num_contracts,
                price=option_price,
                side=side,
                order_id=client_order_id,
            )

        # Stash projected greeks so the fill that lands this order updates
        # portfolio greeks (set_intended_position_greeks → process_fill). Cleared
        # below if the submission fails (no fill will ever arrive).
        if position_greeks is not None and hasattr(self.risk_manager, "set_intended_position_greeks"):
            self.risk_manager.set_intended_position_greeks(
                candidate.ticker,
                delta=position_greeks["delta"],
                gamma=position_greeks["gamma"],
                theta=position_greeks["theta"],
                vega=position_greeks["vega"],
            )

        # Two-phase persistence: write the PENDING_SUBMIT tracking row BEFORE the
        # Gateway round-trip so a crash mid-call (SIGTERM, cancel, OOM) cannot
        # leave the broker with an order Orion's DB knows nothing about.
        # Forensic context: between 2026-05-12 and 2026-05-21, 37 broker positions
        # had ZERO matching `orders` rows because the original write happened
        # after `client.create_order()` returned and the docker_execution
        # container was crash-looping (380 restarts in 24h, lease-conflict).
        # The post-Gateway persist_order_finalize fills in broker_order_id + status.
        #
        # If THIS write fails (DB error), the reservations made above
        # (update_post_trade pending order + set_intended_position_greeks)
        # would otherwise leak: the exception escapes the method before the
        # Gateway try/except below can compensate, inflating portfolio-greeks
        # checks for this ticker until the stale-pending prune. Roll them back
        # here. Do NOT call persist_order_finalize — the PENDING_SUBMIT row was
        # never written, so there is nothing to finalize.
        try:
            await persist_pending_order(
                decision=decision,
                candidate=candidate,
                client_order_id=client_order_id,
                side=side,
                qty=num_contracts,
                limit_price=option_price,
            )
        except Exception as e:
            error_text = str(e)
            await self._remove_pending_order_compat(client_order_id)
            if hasattr(self.risk_manager, "clear_intended_position_greeks"):
                self.risk_manager.clear_intended_position_greeks(candidate.ticker)
            logger.error(
                "options_pending_order_persist_failed",
                error=str(e),
                client_order_id=client_order_id,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = f"Pending-order persist failed: {e}"
            self._record_result(False)
            return

        try:
            client = self._get_gateway_client()
            result = await client.create_order(
                symbol=candidate.option_symbol,
                qty=num_contracts,
                side=side,
                order_type="limit",
                limit_price=option_price,
                time_in_force="day",
                client_order_id=client_order_id,
            )

            if "error" in result:
                # Include the Gateway/Alpaca response body so the REJECTED
                # row records the real reason (e.g. 40310000 insufficient
                # day-trading buying power) instead of a bare "403 Forbidden".
                detail = result.get("detail")
                raise RuntimeError(
                    f"Gateway options order failed: {result['error']}" + (f" | {detail}" if detail else "")
                )

            await persist_order_finalize(
                client_order_id=client_order_id,
                broker_order=result,
                error_message=None,
            )

            # Record in empire-core ledger for EmpireUI
            if self._ledger is not None:
                try:
                    self._ledger.on_order_placed(
                        decision_id=str(getattr(decision, "decision_id", client_order_id)),
                        ticker=candidate.option_symbol,
                        side=side,
                        qty=num_contracts,
                        client_order_id=client_order_id,
                        limit_price=option_price,
                    )
                except Exception as exc:
                    logger.warning("ledger_order_write_failed", error=str(exc))

            premium_paid = num_contracts * option_price * 100
            logger.info(
                "options_execution_successful",
                client_order_id=client_order_id,
                premium_paid=premium_paid,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = DecisionStatus.TRUE
            self._record_result(True)

            # Place bracket stop-loss / take-profit orders if enabled
            ep = decision.execution_params or {}
            if risk_settings.enable_bracket_orders:
                sl_pct = float(ep.get("stop_loss_pct", risk_settings.default_stop_loss_pct))
                tp_pct = float(ep.get("take_profit_pct", 0.50))
                bracket_result = await self._place_bracket_orders(
                    option_symbol=candidate.option_symbol,
                    qty=num_contracts,
                    entry_price=option_price,
                    stop_loss_pct=sl_pct,
                    take_profit_pct=tp_pct,
                    side=side,
                )
                ep["bracket_orders"] = bracket_result
                # Hoist protection state to top-level execution_params so a DB
                # query can find unprotected positions without parsing the
                # nested bracket_orders dict.
                if bracket_result.get("unprotected"):
                    ep["position_unprotected"] = True
                if bracket_result.get("partial_protection"):
                    ep["position_partial_protection"] = True
                # Give the risk layer and PositionMonitor first-class knowledge
                # of the missing protection, and alert operators ONCE per
                # occurrence. The execution_params flags above remain the
                # durable record; this in-memory registry drives re-protection.
                if bracket_result.get("unprotected") or bracket_result.get("partial_protection"):
                    await self._register_unprotected_position(
                        ticker=candidate.ticker,
                        option_symbol=candidate.option_symbol,
                        bracket_result=bracket_result,
                    )

        except Exception as e:
            error_text = str(e)
            await self._remove_pending_order_compat(client_order_id)
            if hasattr(self.risk_manager, "clear_intended_position_greeks"):
                self.risk_manager.clear_intended_position_greeks(candidate.ticker)

            # Confirmed day-trading-buying-power wall → arm the backoff so we
            # stop submitting opening orders for a cooldown instead of flooding.
            if "40310000" in error_text:
                self._note_dtbp_rejection()
            if _is_trading_capability_rejection_text(error_text):
                self._note_trading_capability_rejection()

            # Finalize the PENDING_SUBMIT row to REJECTED in place; the row
            # already exists from persist_pending_order above.
            await persist_order_finalize(
                client_order_id=client_order_id,
                broker_order=None,
                error_message=error_text,
            )
            logger.error(
                "options_execution_failed",
                error=error_text,
                client_order_id=client_order_id,
                option_symbol=candidate.option_symbol,
            )
            decision.executed_successfully = DecisionStatus.FALSE
            decision.reason = f"Options Broker Error: {error_text}"
            self._record_result(False)

    # ── Bracket orders (stop-loss / take-profit) ──────────────────────────

    async def _place_bracket_orders(
        self,
        option_symbol: str,
        qty: int,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        side: str,
        place_stop_loss: bool = True,
        place_take_profit: bool = True,
    ) -> dict[str, Any]:
        """Place stop-loss and take-profit orders after a successful entry.

        Non-fatal at the broker level: bracket failures don't roll back the entry.
        But protection-state is tracked in the return dict so the caller can
        surface unprotected positions to operators (otherwise they're invisible
        outside the log stream).

        ``place_stop_loss``/``place_take_profit`` let re-protection place ONLY
        the missing leg(s) — re-placing a leg that already exists would put a
        second GTC stop/limit on the same position (double-close risk). A
        skipped leg is reported as ``{"preexisting": True}`` so the
        protection-state math below treats it as present.
        """
        exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        sl_price = round(entry_price * (1 - stop_loss_pct), 2)
        tp_price = round(entry_price * (1 + take_profit_pct), 2)
        sl_failure_reason: str | None = None
        tp_failure_reason: str | None = None
        result: dict[str, Any] = {"stop_loss": None, "take_profit": None}

        # Bracket legs MUST be orion-attributed (orion_ client_order_id) and
        # reduce-only (adversarial review 2026-06-05). Without the orion_ id the
        # close-path cancel sweep (_cancel_resting_orion_orders) can't cancel a
        # resting bracket order before a flatten, so a surviving bracket SELL can
        # fire on a now-flat position as a naked short; the id also attributes the
        # bracket fill's P&L. position_intent is reduce-only defence-in-depth (the
        # Gateway threads it for the limit TP; it's a harmless no-op on the stop).
        exit_intent = "sell_to_close" if exit_side == OrderSide.SELL else "buy_to_close"

        client = self._get_gateway_client()

        if not place_stop_loss:
            result["stop_loss"] = {"preexisting": True}
        try:
            if not place_stop_loss:
                raise _SkipLeg()
            sl_order = await client.create_order(
                symbol=option_symbol,
                qty=qty,
                side=exit_side,
                order_type="stop",
                stop_price=sl_price,
                time_in_force="gtc",
                client_order_id=mint_orion_order_id(),
                position_intent=exit_intent,
            )
            # GatewayTradingClient returns HTTP failures as {"error": ...} rather
            # than raising — treat that as a failed leg, else we'd record an
            # order_id=None dict as "protected" and hide an unprotected position.
            if not isinstance(sl_order, dict) or "error" in sl_order or not sl_order.get("id"):
                raise RuntimeError(str(sl_order.get("detail") or sl_order.get("error") or "no order id"))
            result["stop_loss"] = {"order_id": sl_order.get("id"), "stop_price": sl_price}
            logger.info(
                "bracket_stop_loss_placed",
                option_symbol=option_symbol,
                stop_price=sl_price,
                order_id=sl_order.get("id"),
            )
        except _SkipLeg:
            pass
        except Exception as e:
            sl_failure_reason = str(e)
            logger.error("bracket_stop_loss_failed", error=sl_failure_reason, option_symbol=option_symbol)

        if not place_take_profit:
            result["take_profit"] = {"preexisting": True}
        try:
            if not place_take_profit:
                raise _SkipLeg()
            tp_order = await client.create_order(
                symbol=option_symbol,
                qty=qty,
                side=exit_side,
                order_type="limit",
                limit_price=tp_price,
                time_in_force="gtc",
                client_order_id=mint_orion_order_id(),
                position_intent=exit_intent,
            )
            if not isinstance(tp_order, dict) or "error" in tp_order or not tp_order.get("id"):
                raise RuntimeError(str(tp_order.get("detail") or tp_order.get("error") or "no order id"))
            result["take_profit"] = {"order_id": tp_order.get("id"), "limit_price": tp_price}
            logger.info(
                "bracket_take_profit_placed",
                option_symbol=option_symbol,
                take_profit_price=tp_price,
                order_id=tp_order.get("id"),
            )
        except _SkipLeg:
            pass
        except Exception as e:
            tp_failure_reason = str(e)
            logger.error("bracket_take_profit_failed", error=tp_failure_reason, option_symbol=option_symbol)

        # Surface protection state. `unprotected` means no automatic downside
        # exit was placed — the position depends entirely on PositionMonitor's
        # ML/rule exits to avoid full premium decay.
        sl_placed = result["stop_loss"] is not None
        tp_placed = result["take_profit"] is not None
        result["unprotected"] = not sl_placed
        result["partial_protection"] = sl_placed != tp_placed
        result["failure_reasons"] = [
            r
            for r in (
                sl_failure_reason and f"stop_loss: {sl_failure_reason}",
                tp_failure_reason and f"take_profit: {tp_failure_reason}",
            )
            if r
        ]

        if result["unprotected"]:
            logger.critical(
                "position_unprotected",
                event_type="POSITION_UNPROTECTED",
                option_symbol=option_symbol,
                qty=qty,
                entry_price=entry_price,
                stop_loss_failed=sl_failure_reason,
                take_profit_placed=tp_placed,
            )
        elif result["partial_protection"]:
            logger.warning(
                "position_partial_protection",
                event_type="POSITION_PARTIAL_PROTECTION",
                option_symbol=option_symbol,
                qty=qty,
                stop_loss_placed=sl_placed,
                take_profit_failed=tp_failure_reason,
            )

        return result

    async def _register_unprotected_position(
        self,
        ticker: str,
        option_symbol: str,
        bracket_result: dict[str, Any],
    ) -> None:
        """Mark a position as unprotected in the risk layer and alert ONCE.

        Called when bracket placement fully or partially failed. The risk
        manager's in-memory registry drives PositionMonitor re-protection;
        the Discord alert (deduped per option_symbol) tells operators. Never
        raises — a failed registration/alert must not roll back the entry.
        """
        fully = bool(bracket_result.get("unprotected"))
        reasons = bracket_result.get("failure_reasons") or []
        reason = "; ".join(reasons) if reasons else ("no protective legs placed" if fully else "partial protection")
        # Record WHICH legs are missing so re-protection places only those —
        # re-placing an existing leg would double-stop the position.
        missing_legs = [leg for leg in ("stop_loss", "take_profit") if bracket_result.get(leg) is None]
        try:
            mark = getattr(self.risk_manager, "mark_unprotected", None)
            if callable(mark):
                mark(ticker, option_symbol, reason, missing_legs=missing_legs)
        except Exception as exc:
            logger.error("unprotected_register_failed", option_symbol=option_symbol, error=str(exc))

        kind = "UNPROTECTED" if fully else "PARTIALLY PROTECTED"
        try:
            from orion.shared.alerts import send_discord_alert

            await send_discord_alert(
                f"Position {kind}: {ticker} {option_symbol} — bracket placement failed ({reason}). "
                f"PositionMonitor will retry re-protection.",
                dedupe_key=f"unprotected_{option_symbol}",
            )
        except Exception as exc:
            logger.error("unprotected_alert_failed", option_symbol=option_symbol, error=str(exc))

    async def reprotect_position(
        self,
        ticker: str,
        option_symbol: str,
        entry_price: float,
        qty: int,
        missing_legs: list[str] | None = None,
    ) -> bool:
        """Re-attempt protective bracket placement for an unprotected position.

        Called by PositionMonitor once per cycle for each position in the
        risk manager's unprotected registry. Places ONLY the legs recorded as
        missing (``missing_legs``; None = both) — re-placing an existing leg
        would put a second GTC stop on the position. Orion only opens LONG
        option positions (entries are always BUY; a SHORT candidate buys
        puts), so the entry side here is always BUY and exits are SELLs.
        On full success, clears the registry entry and sends a recovery
        alert. Returns True only on full re-protection. Never raises.
        """
        sl_pct = float(risk_settings.default_stop_loss_pct)
        tp_pct = 0.50
        legs = missing_legs if missing_legs is not None else ["stop_loss", "take_profit"]
        try:
            bracket_result = await self._place_bracket_orders(
                option_symbol=option_symbol,
                qty=qty,
                entry_price=entry_price,
                stop_loss_pct=sl_pct,
                take_profit_pct=tp_pct,
                side=OrderSide.BUY,
                place_stop_loss="stop_loss" in legs,
                place_take_profit="take_profit" in legs,
            )
        except Exception as exc:
            logger.error("reprotect_failed", option_symbol=option_symbol, error=str(exc))
            return False

        fully_protected = not bracket_result.get("unprotected") and not bracket_result.get("partial_protection")
        if not fully_protected:
            return False

        clear = getattr(self.risk_manager, "clear_unprotected", None)
        if callable(clear):
            clear(option_symbol)

        try:
            from orion.shared.alerts import send_discord_alert

            await send_discord_alert(
                f"Position RE-PROTECTED: {ticker} {option_symbol} — protective bracket re-placed successfully.",
                dedupe_key=f"reprotected_{option_symbol}",
            )
        except Exception as exc:
            logger.error("reprotect_alert_failed", option_symbol=option_symbol, error=str(exc))

        logger.info("position_reprotected", ticker=ticker, option_symbol=option_symbol)
        return True

    async def close_position(
        self,
        ticker: str,
        qty: float,
        exit_signal: Any,
        direction: str = "LONG",
        use_market_order: bool = False,
        current_price: float | None = None,
    ) -> bool:
        """Close a position based on exit signal via Data Gateway.

        Options behavior (Phase 2 of exit-pipeline RCA):

        - Outside RTH (Alpaca 9:30-16:00 ET): logs and returns False
          WITHOUT submitting. Submitting an options order outside this
          window gets rejected with ``42210000``, producing noise and
          no fill. The PositionMonitor loop will re-attempt on the
          next iteration; when the market opens, the gate flips and
          the submit goes through.
        - Inside RTH: ALWAYS uses a marketable LIMIT order priced
          aggressively relative to ``current_price`` (passed by the
          caller from ``TrackedPosition.current_price``, the mark
          maintained by sync_positions). The Gateway does not expose a
          per-symbol option-quote endpoint, so we use the tracked mark
          rather than pulling a fresh quote. For LONG close (SELL),
          limit is 7.5% below mark; for SHORT close (BUY), 7.5% above.
          Both rounded to the options tick (`round_to_options_tick`).
        - Without ``current_price``: returns False with a log — does
          NOT fall back to a market order. Defensive: any options
          market order is going to be rejected by Alpaca anyway.

        Equity behavior unchanged: market for IMMEDIATE / use_market,
        limit otherwise. Equity has no RTH gate.
        """
        if not await self._gateway_available_for_close(ticker):
            logger.warning(
                "Data Gateway unavailable after retries. Cannot close position.",
                extra={"event_type": "CLOSE_POSITION_NOOP", "ticker": ticker},
            )
            return False

        if qty == 0:
            logger.warning(
                f"close_position called with qty=0 for {ticker}; nothing to close",
                extra={"event_type": "EXIT_ORDER_SKIPPED", "ticker": ticker, "qty": qty},
            )
            return False

        # Reduce-only safety: re-verify the LIVE broker position so a stale
        # tracked qty can't turn a "close" into an OPENING (naked) short. Only
        # ever reduce an existing position — never open or flip. The broker's
        # signed qty is authoritative for the close direction (overriding the
        # caller's `direction` hint, which can be stale or default "LONG" for
        # sibling-system positions on the shared account). 2026-05-29: the close
        # path oversold longs into naked short 0DTE puts (~3,235 rejected
        # orders/day) because it trusted the passed qty — see
        # test_close_reduce_only.
        broker_qty = await self._live_position_qty(ticker)
        if broker_qty is None:
            logger.warning(
                f"Skipping close for {ticker}: live position could not be verified",
                extra={"event_type": "EXIT_SKIPPED_UNVERIFIED", "ticker": ticker},
            )
            return False
        if abs(broker_qty) < 1e-9:
            logger.info(
                f"Skipping close for {ticker}: broker holds no position to reduce",
                extra={"event_type": "EXIT_SKIPPED_NO_POSITION", "ticker": ticker},
            )
            return False

        # `held_short` from the broker sign; cap the close to the held qty (and
        # to the caller's requested qty). Down-stream Gateway calls receive the
        # positive `abs_qty`.
        held_short = broker_qty < 0
        abs_qty = min(abs(qty), abs(broker_qty))
        if abs_qty < 1e-9:
            return False

        # Lazy-instantiate the market schedule the first time we need
        # it. Tests inject a stub via `engine._market_schedule = ...`
        # for hermetic control.
        from orion.core.market_schedule import MarketSchedule

        if not hasattr(self, "_market_schedule") or self._market_schedule is None:
            self._market_schedule = MarketSchedule()

        is_option = is_occ_option_symbol(ticker)

        # ── Options outside RTH: skip submission ─────────────────
        if is_option and not self._market_schedule.is_market_open_for_options():
            # DEBUG level so the every-5-second retry across ~50
            # positions doesn't flood WARN logs while market is closed.
            # Operator can still see the gating decision via
            # event_type filter, and the first attempt at the next
            # market open is logged separately when the submit happens.
            logger.debug(
                f"Skipping options close for {ticker}: market closed for options",
                extra={
                    "event_type": "EXIT_SKIPPED_MARKET_CLOSED",
                    "ticker": ticker,
                    "rule_id": getattr(exit_signal, "rule_id", None),
                },
            )
            return False

        # ── Options inside RTH: attributed LIMIT first, native escalation ──
        #
        # RCA 2026-06-05 + adversarial review: the close MUST stay
        # orion-attributed so its fill reaches RiskManager.process_fill and
        # feeds realized-PnL / daily-loss / the drawdown kill switch. The
        # Gateway's native DELETE /positions close carries NO orion_
        # client_order_id, so its fill is dropped by the orion_-prefix fill
        # filter — using it as the default would make the kill switch blind to
        # option-close PnL. So:
        #   1. cancel Orion's own resting orders on this symbol (avoid a
        #      self-wash against a filled/working entry, and a re-open after);
        #   2. submit an orion-attributed marketable LIMIT, floored strictly
        #      above our own avg entry so it can't self-wash (the MU bug was a
        #      stale mark putting the SELL below our $5.00 entry), with a
        #      42210000 retry that re-verifies the live position (reduce-only);
        #   3. only if the limit is rejected, ESCALATE to the native flatten as
        #      a last resort to avoid stranding the position — accepting the
        #      attribution gap only in that rare case.
        #
        # Close-side comes from `held_short` (broker signed qty), NOT the
        # caller's `direction` hint (Codex review 2026-05-21 Important #2).
        if is_option:
            client = self._get_gateway_client()
            close_side = OrderSide.BUY if held_short else OrderSide.SELL

            # Cancel our own resting orders first. If we cannot CONFIRM they're
            # cleared, defer this close (don't risk a wash/re-open) and retry
            # next cycle rather than escalating blind.
            if not await self._cancel_resting_orion_orders(ticker):
                logger.warning(
                    f"Deferring close for {ticker}: own resting orders not confirmed cancelled",
                    extra={"event_type": "EXIT_DEFERRED_RESTING_ORDERS", "ticker": ticker},
                )
                return False

            # Price the close at the LIVE market from a fresh chain quote, not
            # the tracked mark. RCA 2026-06-08 (MU260612P00790000): the tracked
            # mark was 21.0 for a contract actually worth ~6.45, so the close
            # limit (mark*0.925 = 19.4) rested far off-market, reserved the long
            # for ~30 min, and every new full-size close into that window was
            # priced by Alpaca as OPENING a short → 40310000. A marketable limit
            # off the live bid/ask fills immediately and never rests.
            limit_price = await self._fresh_close_limit(client, ticker, held_short)

            # Fall back to the tracked mark only if no fresh quote is available.
            if limit_price is None or limit_price <= 0:
                if current_price is None or current_price <= 0:
                    return await self._native_close_escalation(
                        client, ticker, abs_qty, close_side, exit_signal, "no fresh quote or mark for limit"
                    )
                limit_price = self._compute_close_limit(current_price, held_short)

            if limit_price <= 0:
                return await self._native_close_escalation(
                    client,
                    ticker,
                    abs_qty,
                    close_side,
                    exit_signal,
                    f"limit priced <= 0 (mark={current_price})",
                )

            # 1) Primary: orion-attributed marketable LIMIT (keeps the fill
            #    orion-attributed → PnL / daily-loss / kill-switch accounting).
            client_order_id = mint_orion_order_id()
            result, client_order_id = await self._submit_close_limit(
                client=client,
                ticker=ticker,
                qty=abs_qty,
                close_side=close_side,
                limit_price=limit_price,
                client_order_id=client_order_id,
            )
            if "error" not in result:
                logger.info(
                    f"EXIT LIMIT ORDER (OPTION): {ticker} x{abs_qty} @ {limit_price} "
                    f"mark={current_price} - {getattr(exit_signal, 'reason', None)}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": abs_qty,
                        "order_type": "LIMIT",
                        "limit_price": limit_price,
                        "mark_price": current_price,
                        "rule_id": getattr(exit_signal, "rule_id", None),
                        "reason": getattr(exit_signal, "reason", None),
                    },
                )
                await persist_exit_decision(ticker, exit_signal, client_order_id, result)
                self._record_result(True)
                return True

            # 2) Limit failed. Escalate to the native flatten ONLY on a CONFIRMED
            #    broker rejection (4xx) — then the limit definitively did not
            #    rest. An AMBIGUOUS outcome (timeout / transport error / 5xx;
            #    GatewayTradingClient returns {"error": ...} with no status_code)
            #    could mean the limit was ACCEPTED and is resting: a native
            #    flatten now could double-close (both fill) and re-open the
            #    naked-short hole (adversarial review). Defer instead — next
            #    cycle's cancel-resting-first cancels any live limit before
            #    re-attempting, and a position that did close is caught by the
            #    early no-position guard.
            detail = str(result.get("detail") or result.get("error") or "")
            status_code = result.get("status_code")
            confirmed_rejection = classify_close_failure(result) == "confirmed_rejection"
            if not confirmed_rejection:
                logger.warning(
                    f"Close limit for {ticker} had an ambiguous outcome ({detail[:160]}); "
                    f"deferring rather than escalating (limit may be live)",
                    extra={"event_type": "EXIT_LIMIT_AMBIGUOUS_DEFER", "ticker": ticker, "error": detail[:200]},
                )
                self._record_result(False)
                return False
            logger.warning(
                f"Limit close rejected for {ticker} ({status_code}), escalating to native flatten: {detail[:200]}",
                extra={"event_type": "EXIT_LIMIT_REJECTED_ESCALATE", "ticker": ticker, "error": detail[:200]},
            )
            return await self._native_close_escalation(
                client, ticker, abs_qty, close_side, exit_signal, f"limit rejected: {detail}"
            )

        # ── Equity: market for IMMEDIATE, limit otherwise ──
        #
        # Position side for equity comes from the qty SIGN (`held_short`),
        # not from the `direction` hint. Alpaca's market-close endpoint
        # accepts only positive qty and infers BUY-to-cover from the
        # existing short side, so the DELETE path just passes `abs_qty`.
        # The limit path mirrors that: BUY when held_short else SELL,
        # with the limit price crossing in the direction needed to fill
        # (above mark for BUY-to-cover, below for SELL-to-close).
        try:
            client = self._get_gateway_client()
            client_order_id = mint_orion_order_id()

            if use_market_order or exit_signal.urgency == "IMMEDIATE":
                result = await client.close_position(ticker, qty=abs_qty)

                if "error" in result:
                    raise RuntimeError(f"Gateway close_position failed: {result['error']}")

                logger.info(
                    f"EXIT MARKET ORDER: {ticker} x{abs_qty} - Reason: {exit_signal.reason}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": abs_qty,
                        "order_type": "MARKET",
                        "rule_id": exit_signal.rule_id,
                        "reason": exit_signal.reason,
                    },
                )
            else:
                # Prefer caller-supplied current_price (mark from sync_positions);
                # fall back to a fresh snapshot only if the caller didn't supply.
                price = current_price
                if price is None or price <= 0:
                    snapshot = await client.get_stock_snapshot(ticker)
                    if "error" not in snapshot:
                        latest_trade = snapshot.get("latestTrade", {}) or snapshot.get("latest_trade", {})
                        if latest_trade:
                            price = float(latest_trade.get("p", 0) or latest_trade.get("price", 0) or 0)

                if price is None or price <= 0:
                    logger.error(f"Cannot close {ticker}: Failed to get current price")
                    return False

                exit_buffer_bps = 5
                if held_short:
                    # BUY-to-cover: pay slightly above mark to ensure fill.
                    limit_price = round(price * (1 + exit_buffer_bps / 10000.0), 2)
                    close_side = OrderSide.BUY
                else:
                    # SELL-to-close: hit slightly below mark.
                    limit_price = round(price * (1 - exit_buffer_bps / 10000.0), 2)
                    close_side = OrderSide.SELL

                result = await client.create_order(
                    symbol=ticker,
                    qty=abs_qty,
                    side=close_side,
                    order_type="limit",
                    limit_price=limit_price,
                    time_in_force="day",
                    client_order_id=client_order_id,
                    # Reduce-only intent: Alpaca will reject (not open a naked
                    # short) if no matching position exists — belt-and-suspenders
                    # alongside the live-position verification above.
                    position_intent=("buy_to_close" if close_side == OrderSide.BUY else "sell_to_close"),
                )

                if "error" in result:
                    raise RuntimeError(f"Gateway exit limit order failed: {result['error']}")

                logger.info(
                    f"EXIT LIMIT ORDER: {ticker} x{abs_qty} @ {limit_price} - Reason: {exit_signal.reason}",
                    extra={
                        "event_type": "EXIT_ORDER_SUBMITTED",
                        "ticker": ticker,
                        "qty": abs_qty,
                        "order_type": "LIMIT",
                        "limit_price": limit_price,
                        "rule_id": exit_signal.rule_id,
                        "reason": exit_signal.reason,
                    },
                )

            await persist_exit_decision(ticker, exit_signal, client_order_id, result)
            self._record_result(True)
            return True

        except Exception as e:
            logger.error(
                f"Failed to close position {ticker}: {e}",
                extra={"event_type": "EXIT_ORDER_FAILED", "ticker": ticker, "error": str(e)},
            )
            self._record_result(False)
            return False

    async def _cancel_resting_orion_orders(self, ticker: str) -> bool:
        """Cancel Orion's own OPEN orders on ``ticker`` before a close.

        Returns ``True`` only when it is SAFE to proceed — no Orion resting
        order remains that could self-wash the close or re-open exposure after
        it. Returns ``False`` if the open-order listing failed or ANY cancel was
        rejected, so the caller can defer and retry rather than close blind. The
        Gateway surfaces failures as ``{"error": ...}`` (not exceptions), so we
        inspect the cancel result rather than assuming success (adversarial
        review). Only Orion's own ``orion_``-prefixed orders are touched; on a
        successful cancel the order is dropped from risk pending exposure so it
        isn't double-counted until the hourly prune.
        """
        try:
            client = self._get_gateway_client()
            open_orders = await client.get_orders(status="open", limit=500)
        except Exception as e:
            logger.warning(
                f"Could not list open orders before closing {ticker}: {e}",
                extra={"event_type": "EXIT_CANCEL_RESTING_LIST_FAILED", "ticker": ticker},
            )
            return False
        if not isinstance(open_orders, list):
            return False

        safe = True
        for order in open_orders:
            if not isinstance(order, dict) or order.get("symbol") != ticker:
                continue
            coid = str(order.get("client_order_id") or "")
            if not coid.startswith(ORDER_ID_PREFIX):
                continue  # only Orion's own orders on the shared account
            order_id = order.get("id")
            if not order_id:
                continue
            result = await client.cancel_order(str(order_id))
            if isinstance(result, dict) and "error" in result:
                logger.warning(
                    f"Failed to cancel resting order {order_id} on {ticker}: "
                    f"{result.get('detail') or result.get('error')}",
                    extra={"event_type": "EXIT_CANCEL_RESTING_FAILED", "ticker": ticker, "order_id": str(order_id)},
                )
                safe = False
                continue
            # Cancel accepted → drop it from risk pending exposure.
            remove = getattr(self.risk_manager, "remove_pending_order", None)
            if remove is not None:
                try:
                    await remove(coid)
                except Exception:
                    pass
            logger.info(
                f"Cancelled resting Orion order {order_id} on {ticker} before close",
                extra={"event_type": "EXIT_CANCEL_RESTING", "ticker": ticker, "order_id": str(order_id)},
            )
        return safe

    async def _fetch_stale_entry_orders(self) -> list[dict[str, Any]]:
        """Orion entry orders that reached the broker, are still unfilled, and
        have aged past ``_STALE_ENTRY_ORDER_TTL_SECONDS``.

        Scoping to genuine buy-to-open entries comes from the filters, not from
        the table being entries-only: successful closes never get an ``orders``
        row (they persist to ``exit_decisions``) and bracket SL/TP legs are
        never persisted. The one exception — ``persist_exit_order_rejection``
        writes a REJECTED close row — always has ``broker_order_id IS NULL`` and
        a terminal status, so the ``broker_order_id IS NOT NULL`` + open-status
        filters exclude it. A buy-to-close on a short can therefore never be
        returned here. Only ``orion_``-prefixed rows are considered
        (shared-account safety).
        """
        from orion.storage.models_execution import OrderRecord

        cutoff = datetime.now(UTC) - timedelta(seconds=self._STALE_ENTRY_ORDER_TTL_SECONDS)
        # Broker-reported working states for an unfilled order. Terminal states
        # (filled / partially_filled / canceled / expired / rejected / ...) are
        # excluded so we never touch an order that is done or already filling.
        open_states = ("new", "accepted", "pending_new", "held", "accepted_for_bidding")

        async def query(session: Any) -> list[dict[str, Any]]:
            stmt = (
                select(OrderRecord.broker_order_id, OrderRecord.client_order_id, OrderRecord.ticker)
                .where(OrderRecord.client_order_id.like(orion_order_id_sql_pattern()))
                .where(OrderRecord.broker_order_id.isnot(None))
                .where(func.lower(OrderRecord.status).in_(open_states))
                .where(OrderRecord.created_at_utc < cutoff)
            )
            result = await session.execute(stmt)
            return [{"broker_order_id": r[0], "client_order_id": r[1], "ticker": r[2]} for r in result.all()]

        return await db_query(query)

    async def _cancel_stale_entry_orders(self, client: Any) -> int:
        """Cancel resting unfilled Orion entry orders past the stale-entry TTL.

        Frees the day-trading buying power they reserve on the shared account
        and prevents a late fill on an hours-old signal. Best-effort: the
        Gateway surfaces failures as ``{"error": ...}`` (not exceptions). On a
        clean cancel the order is dropped from risk pending exposure so it isn't
        double-counted until the hourly prune, and its backoff state is cleared.

        A rejected cancel no longer re-fires every 5s forever (the self-inflicted
        429 storm): a PERMANENT reject (only a known broker marker — GW-E2009 /
        "trading capability required") gives up after a single attempt with a
        durable error log + one alert and is never retried; every other reject
        (429 / 5xx / timeout / a generic 4xx) is TRANSIENT — it arms exponential
        backoff with jitter, is skipped until it elapses, and gives up only after
        ``_CANCEL_MAX_ATTEMPTS``. At most ``_CANCEL_MAX_PER_CYCLE`` cancels are
        attempted per sweep. Returns the number cancelled.
        """
        # __new__-constructed instances (some tests) skip __init__; seed lazily.
        if not hasattr(self, "_cancel_attempts"):
            self._cancel_attempts = {}

        try:
            stale = await self._fetch_stale_entry_orders()
        except Exception as e:
            logger.warning(
                "Could not list stale entry orders for cancellation",
                extra={"event_type": "STALE_ENTRY_QUERY_FAILED", "error": str(e)},
            )
            return 0

        # Prune state for orders no longer stale (filled / canceled / terminal),
        # so the dict can't grow unbounded over a long-running process.
        still_stale_ids = {str(r["broker_order_id"]) for r in stale if r.get("broker_order_id")}
        for known_id in list(self._cancel_attempts):
            if known_id not in still_stale_ids:
                del self._cancel_attempts[known_id]

        now = time.monotonic()
        cancelled = 0
        attempted = 0
        for row in stale:
            broker_id = row.get("broker_order_id")
            coid = row.get("client_order_id")
            ticker = row.get("ticker")
            if not broker_id:
                continue
            bid = str(broker_id)

            state = self._cancel_attempts.get(bid)
            # Skip orders that have given up or are still inside their backoff.
            if state is not None and (state.gave_up or now < state.next_eligible):
                continue

            # Per-sweep cap: bound Gateway load even when many orders are stale.
            if attempted >= self._CANCEL_MAX_PER_CYCLE:
                break
            attempted += 1

            try:
                result = await client.cancel_order(bid)
            except Exception as e:
                # A raised transport error is transient — back off like a 5xx.
                logger.warning(
                    f"Failed to cancel stale entry order {bid} on {ticker}: {e}",
                    extra={"event_type": "STALE_ENTRY_CANCEL_FAILED", "ticker": ticker, "order_id": bid},
                )
                await self._record_cancel_failure(bid, ticker, {"error": str(e)}, permanent=False)
                continue

            if isinstance(result, dict) and "error" in result:
                # The broker may reject the cancel because the order is ALREADY
                # terminal — poll_fills' 200-row status window aged it out before
                # it saw the fill. Reconcile the row to the broker's real state
                # and drop the reservation; this is a state-desync, NOT a stuck
                # order to retry and page (the 2026-06-22 false-alert storm).
                terminal_state = _parse_already_terminal_state(result)
                if terminal_state is not None:
                    # The broker says this order is already terminal — poll_fills'
                    # 200-row window aged it out before it saw the transition. If it
                    # FILLED, that fill was never processed: no FillRecord landed,
                    # so per-symbol cost basis / realized PnL are incomplete
                    # (_compute_cost_basis_from_fills can't replay an absent row).
                    # Recover it by fetching the order by id and feeding it through
                    # the idempotent fill processor.
                    #
                    # Best-effort, and intentionally does NOT gate the status flip
                    # below: the flip is what drops the order out of the stale set
                    # and stops the 2026-06-22 cancel/alert storm, and the broker
                    # has ALREADY confirmed the fill — so a get_order failure must
                    # not strand the order back in the storming set (and unconditional
                    # flip means each order triggers exactly one get_order, never a
                    # per-sweep re-fetch). A rare unrecovered fill is logged durably
                    # and fails safe downstream (reconcile_pnl routes an unbasis-able
                    # close to BROKER_UNAVAILABLE). The sweep only surfaces orders in
                    # open (pre-fill) states, never partially_filled, so recovery
                    # always applies to an order we have counted ZERO fills for —
                    # which sidesteps the partial-double-count hazard.
                    if terminal_state == "filled":
                        await self._recover_missed_fill(client, bid, ticker)
                    self._cancel_attempts.pop(bid, None)
                    await self._remove_pending_order_compat(coid)
                    try:
                        await persist_order_status_update(broker_order_id=bid, status=terminal_state)
                    except Exception as e:
                        logger.warning(
                            "Could not reconcile already-terminal stale entry order in DB",
                            extra={
                                "event_type": "STALE_ENTRY_STATUS_UPDATE_FAILED",
                                "order_id": bid,
                                "error": str(e),
                            },
                        )
                    logger.info(
                        f"Stale entry order {bid} on {ticker} already {terminal_state} at broker "
                        f"— reconciled (poll_fills missed the transition)",
                        extra={
                            "event_type": "STALE_ENTRY_RECONCILED",
                            "ticker": ticker,
                            "order_id": bid,
                            "broker_state": terminal_state,
                        },
                    )
                    continue

                # A legacy pre-2026-05-20 order (raw `orion_<uuid>`, no Gateway
                # `c-<client>-` ownership prefix) fail-closes every cancel with
                # 404 GW-E4404 — the Gateway can't confirm Orion owns it, so it
                # can NEVER be cancelled through this path. Retrying is pointless
                # (it produced the 2026-06-22..24 GW-A4001/GW-E4404 flood — 1,164
                # warnings). Reconcile the orphaned row terminal so the sweep
                # stops re-selecting it (within this process AND across restarts)
                # and drop the stale pending reservation. These are DAY orders
                # long expired at Alpaca; a real fill is still caught
                # (orion-attributed) by poll_fills / position-sync. get_order is
                # NOT attempted to recover a fill — it hits the same ownership
                # guard and 404s — and any order still open at Alpaca is cleared
                # out-of-band via the dashboard.
                if _is_legacy_unowned_cancel_rejection(result):
                    self._cancel_attempts.pop(bid, None)
                    await self._remove_pending_order_compat(coid)
                    try:
                        await persist_order_status_update(broker_order_id=bid, status="canceled")
                    except Exception as e:
                        logger.warning(
                            "Could not reconcile legacy-unowned stale entry order in DB",
                            extra={
                                "event_type": "STALE_ENTRY_STATUS_UPDATE_FAILED",
                                "order_id": bid,
                                "error": str(e),
                            },
                        )
                    logger.warning(
                        f"Stale entry order {bid} on {ticker} is a legacy pre-2026-05-20 order "
                        f"(404 GW-E4404, unowned by the Gateway) — reconciled out of the cancel "
                        f"sweep; clear it out-of-band at Alpaca if still open",
                        extra={
                            "event_type": "STALE_ENTRY_LEGACY_UNOWNED_RECONCILED",
                            "ticker": ticker,
                            "order_id": bid,
                        },
                    )
                    continue

                permanent = _is_permanent_cancel_rejection(result)
                logger.warning(
                    f"Cancel rejected for stale entry order {bid} on {ticker}: "
                    f"{result.get('detail') or result.get('error')}",
                    extra={
                        "event_type": "STALE_ENTRY_CANCEL_REJECTED",
                        "ticker": ticker,
                        "order_id": bid,
                        "permanent": permanent,
                    },
                )
                await self._record_cancel_failure(bid, ticker, result, permanent=permanent)
                continue

            # Success: clear backoff state and drop the pending reservation.
            self._cancel_attempts.pop(bid, None)
            await self._remove_pending_order_compat(coid)
            # Optimistically flip the DB row out of the open-status set so this
            # sweep doesn't re-issue a cancel for it every cycle when the row
            # falls outside the next status-poll's 200-row window — the exact
            # high-order-volume day this fix targets. The next poll reconciles
            # to the broker's real terminal status.
            try:
                await persist_order_status_update(broker_order_id=bid, status="canceled")
            except Exception as e:
                logger.warning(
                    "Could not mark cancelled stale entry order in DB",
                    extra={
                        "event_type": "STALE_ENTRY_STATUS_UPDATE_FAILED",
                        "order_id": bid,
                        "error": str(e),
                    },
                )
            cancelled += 1
            logger.info(
                f"Cancelled stale entry order {bid} on {ticker} "
                f"(unfilled > {self._STALE_ENTRY_ORDER_TTL_SECONDS:.0f}s)",
                extra={"event_type": "STALE_ENTRY_CANCELLED", "ticker": ticker, "order_id": bid},
            )
        return cancelled

    async def _record_cancel_failure(
        self, broker_id: str, ticker: Any, result: dict[str, Any], *, permanent: bool
    ) -> None:
        """Update per-order backoff/give-up state after a rejected cancel.

        Permanent rejections give up after one attempt; transient ones back off
        exponentially with jitter and give up after ``_CANCEL_MAX_ATTEMPTS``.
        A give-up is always recorded with a durable ERROR log (the operator's
        guaranteed signal that an order is stuck reserving DTBP) and then a
        best-effort, deduped Discord alert.
        """
        state = self._cancel_attempts.get(broker_id)
        if state is None:
            state = _CancelState()
            self._cancel_attempts[broker_id] = state

        state.attempts += 1
        status_code = result.get("status_code")
        state.last_code = status_code if isinstance(status_code, int) and not isinstance(status_code, bool) else None

        give_up = permanent or state.attempts >= self._CANCEL_MAX_ATTEMPTS
        if give_up:
            state.gave_up = True
            if not state.alerted:
                state.alerted = True
                detail = str(result.get("detail") or result.get("error") or "")[:200]
                kind = "permanent" if permanent else f"transient ({state.attempts} attempts)"
                # Durable record FIRST: send_discord_alert never raises and returns
                # False (no webhook / dedupe / delivery failure) without surfacing,
                # so the give-up must land in the error log regardless of whether
                # the page is delivered — the order keeps reserving DTBP until it
                # expires at the close and the operator has to be able to see it.
                logger.error(
                    "stale_cancel_gave_up",
                    event_type="STALE_ENTRY_CANCEL_GAVE_UP",
                    order_id=broker_id,
                    ticker=str(ticker),
                    permanent=permanent,
                    attempts=state.attempts,
                    last_code=state.last_code,
                    detail=detail,
                )
                if permanent and _is_trading_capability_rejection_text(detail):
                    logger.warning("stale_cancel_giveup_alert_skipped_gateway_permission", order_id=broker_id)
                    return
                try:
                    delivered = await send_discord_alert(
                        f"Stale entry-order cancel GAVE UP ({kind}): {ticker} order {broker_id} "
                        f"will keep reserving DTBP until it expires at the close. "
                        f"Last error: {detail}",
                        dedupe_key=f"stale_cancel_giveup_{broker_id}",
                    )
                except Exception as e:
                    logger.error("stale_cancel_giveup_alert_failed", order_id=broker_id, error=str(e))
                    delivered = False
                if not delivered:
                    logger.warning("stale_cancel_giveup_alert_undelivered", order_id=broker_id)
            return

        # Transient: arm exponential backoff with jitter, capped.
        backoff = min(
            self._CANCEL_BACKOFF_BASE_SECONDS * (2 ** (state.attempts - 1)),
            self._CANCEL_BACKOFF_CAP_SECONDS,
        )
        state.next_eligible = time.monotonic() + backoff + _cancel_backoff_jitter()

    async def _fresh_close_limit(self, client: Any, ticker: str, held_short: bool) -> float | None:
        """Marketable options close limit from a FRESH chain quote, or ``None``
        if no usable quote is available (the caller then falls back to the
        tracked mark).

        SELL-to-close hits the live bid; BUY-to-cover lifts the live ask — both
        immediately marketable, so the close fills instead of resting off-market
        (RCA 2026-06-08: a stale tracked mark priced the close 3× off, the order
        rested and reserved the position for 30 min, and new closes into that
        window were rejected as opening a cash-secured short). The price is
        rounded in the MARKETABLE direction (floor a sell to the tick so it stays
        ≤ bid; ceil a buy so it stays ≥ ask) — ``round_to_options_tick`` rounds
        to nearest, which could nudge a sell above the bid and re-create a
        resting order. Returns ``None`` on any quote problem (no method, error,
        biddless/askless contract) so the mark-based fallback still runs.
        """
        getq = getattr(client, "get_option_quote", None)
        if getq is None:
            return None
        try:
            quote = await getq(ticker)
        except Exception as e:
            logger.warning(
                f"Fresh option quote fetch failed for {ticker}: {e}",
                extra={"event_type": "CLOSE_QUOTE_FETCH_ERROR", "ticker": ticker},
            )
            return None
        if not isinstance(quote, dict):
            return None
        # Cross into the side we need: bid for a SELL-to-close, ask for a
        # BUY-to-cover. A missing/zero touch on that side → no usable quote.
        touch = quote.get("ask") if held_short else quote.get("bid")
        if not isinstance(touch, (int, float)) or isinstance(touch, bool) or touch <= 0:
            return None
        touch = float(touch)
        tick = 0.10 if touch >= 3.0 else 0.05
        limit = math.ceil(touch / tick) * tick if held_short else math.floor(touch / tick) * tick
        return round(limit, 2)

    def _compute_close_limit(self, mark: float, held_short: bool) -> float:
        """Marketable close limit: cross the spread by ~7.5% so the order
        actually fills. SELL-to-close hits the bid (below mark); BUY-to-cover
        lifts the offer (above mark). ``round_to_options_tick`` handles the
        $0.05/$0.10 increment requirement.

        Deliberately NOT floored against our own avg entry: an entry floor can
        push a loser's exit far from the market so Alpaca accepts a *resting*
        limit that never fills (adversarial review). Self-wash is handled
        instead by cancelling our own resting orders first, and by escalating to
        the native flatten if a residual wash rejects the limit.
        """
        buffer = 0.075
        if held_short:
            return round_to_options_tick(mark * (1 + buffer))  # BUY-to-cover lifts the offer
        return round_to_options_tick(mark * (1 - buffer))  # SELL-to-close hits the bid

    async def _submit_close_limit(
        self,
        *,
        client: Any,
        ticker: str,
        qty: float,
        close_side: Any,
        limit_price: float,
        client_order_id: str,
    ) -> tuple[dict[str, Any], str]:
        """Submit a single reduce-only close LIMIT with ``position_intent=
        *_to_close``.

        There is deliberately NO plain-limit retry. Alpaca rejects the forced
        intent with ``42210000`` when it infers the OPENING side, but retrying
        WITHOUT intent has an inherent reduce-only race: the live position can
        change between any pre-check and the ``create_order`` call, so a plain
        SELL/BUY with no broker-side reduce-only guard could open or flip
        exposure (adversarial review). Instead the rejection is returned to the
        caller, which escalates to the native flatten — reduce-only by
        construction and 404-safe.

        Returns ``(result, client_order_id)``.
        """
        intent = "buy_to_close" if close_side == OrderSide.BUY else "sell_to_close"
        result = await client.create_order(
            symbol=ticker,
            qty=qty,
            side=close_side,
            order_type="limit",
            limit_price=limit_price,
            time_in_force="day",
            client_order_id=client_order_id,
            position_intent=intent,
        )
        return result, client_order_id

    async def _native_close_escalation(
        self,
        client: Any,
        ticker: str,
        abs_qty: float,
        close_side: Any,
        exit_signal: Any,
        reason: str,
    ) -> bool:
        """Last-resort flatten via the Gateway native close (``DELETE
        /positions/{symbol}``), bounded to ``abs_qty``, used only when the
        attributed limit close can't be priced or is rejected.

        The native close fill is NOT orion-attributed (the endpoint takes no
        ``client_order_id``), so it does NOT feed ``RiskManager.process_fill`` —
        accepted only as an escape hatch to avoid stranding a position. A
        vanished position (404 / POSITION_NOT_FOUND / 40410000) counts as
        already-closed (do NOT submit an opposing order — that could open a
        naked short).
        """
        client_order_id = mint_orion_order_id()
        native = await client.close_position(ticker, qty=abs_qty)
        native_err = str(native.get("detail") or native.get("error") or "")
        if "error" not in native:
            logger.info(
                f"EXIT NATIVE CLOSE (escalation): {ticker} x{abs_qty} - {reason}",
                extra={
                    "event_type": "EXIT_ORDER_SUBMITTED",
                    "ticker": ticker,
                    "qty": abs_qty,
                    "order_type": "NATIVE_CLOSE",
                    "reason": getattr(exit_signal, "reason", None),
                },
            )
            await persist_exit_decision(ticker, exit_signal, client_order_id, native)
            self._record_result(True)
            return True
        if native.get("status_code") == 404 or "POSITION_NOT_FOUND" in native_err or "40410000" in native_err:
            logger.info(
                f"Close for {ticker}: broker reports no position (already closed)",
                extra={"event_type": "EXIT_SKIPPED_NO_POSITION", "ticker": ticker},
            )
            self._record_result(True)
            return True
        logger.error(
            f"Native close escalation failed for {ticker}: {native_err[:200]}",
            extra={"event_type": "EXIT_ORDER_FAILED", "ticker": ticker, "error": native_err[:200]},
        )
        await persist_exit_order_rejection(
            client_order_id=client_order_id,
            ticker=ticker,
            side=close_side,
            qty=abs_qty,
            limit_price=None,
            error_message=f"limit+native both failed ({reason}); native: {native_err}",
        )
        self._record_result(False)
        return False

    # ── System health ────────────────────────────────────────────────────

    async def _check_system_health(self) -> bool:
        """Queries SystemStatus table to ensure Global Health is OK and circuit breaker is not open.

        Results are cached for ``_health_cache_ttl`` seconds (default 10s) to
        avoid redundant DB queries when multiple candidates are evaluated in
        the same execution cycle.
        """
        if self._health_cache and (time.monotonic() - self._health_cache[1]) < self._health_cache_ttl:
            return self._health_cache[0]

        from orion.core.circuit_breaker import CircuitBreaker
        from orion.enrichment.heber_context import DEGRADED_DISCOVERY_KEY, DISCOVERY_STATUS_DEGRADED
        from orion.storage.models import SystemStatus

        try:

            async def fetch_health_records(session: Any) -> tuple[Any, Any, Any]:
                cb_stmt = select(SystemStatus).where(SystemStatus.key == CircuitBreaker.KEY)
                health_stmt = select(SystemStatus).where(SystemStatus.key == "global_health")
                discovery_stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
                cb_result = await session.execute(cb_stmt)
                health_result = await session.execute(health_stmt)
                discovery_result = await session.execute(discovery_stmt)
                return (
                    cb_result.scalars().first(),
                    health_result.scalars().first(),
                    discovery_result.scalars().first(),
                )

            cb_record, status_record, discovery_record = await db_query(fetch_health_records)

            if cb_record and cb_record.status == "OPEN":
                logger.critical(
                    "EXECUTION BLOCKED: Circuit breaker is OPEN",
                    extra={
                        "event_type": "HEALTH_CHECK_FAILED",
                        "reason": "Circuit Breaker Open",
                        "details": cb_record.details,
                    },
                )
                self._health_cache = (False, time.monotonic())
                return False

            # Discovery degradation: feature_enrichment writes DEGRADED when
            # ticker discovery has been falling back to the static SPY/QQQ/...
            # list past the warn-streak threshold. Block new trades — the
            # universe may be stale and emitting against the wrong tickers.
            if discovery_record and discovery_record.status == DISCOVERY_STATUS_DEGRADED:
                logger.critical(
                    "EXECUTION BLOCKED: Ticker discovery is DEGRADED",
                    extra={
                        "event_type": "HEALTH_CHECK_FAILED",
                        "reason": "Discovery Degraded",
                        "details": discovery_record.details,
                    },
                )
                self._health_cache = (False, time.monotonic())
                return False

            if not status_record:
                logger.error(
                    "System Health Record missing. Execution BLOCKED until health record is created.",
                    extra={"event_type": "HEALTH_CHECK_FAILED", "details": "Record Missing"},
                )
                self._health_cache = (False, time.monotonic())
                return False

            if status_record.status != "HEALTHY":
                logger.error(
                    f"System Health Check Failed: {status_record.status}",
                    extra={
                        "event_type": "HEALTH_CHECK_FAILED",
                        "status": status_record.status,
                        "details": status_record.details,
                    },
                )
                self._health_cache = (False, time.monotonic())
                return False

            now = datetime.now(UTC)
            if status_record.last_updated_utc:
                last_updated = ensure_utc(status_record.last_updated_utc)
                assert last_updated is not None  # guarded non-None above

                age = (now - last_updated).total_seconds()
                if age > system_settings.ingestion_heartbeat_max_age:
                    logger.error(
                        f"System Health Record STALE ({age:.1f}s). Ingestion likely dead.",
                        extra={"event_type": "HEALTH_CHECK_FAILED", "reason": "Stale", "age_seconds": age},
                    )
                    self._health_cache = (False, time.monotonic())
                    return False

            self._health_cache = (True, time.monotonic())
            return True
        except Exception as e:
            logger.error(
                f"Failed to check System Health: {e}",
                extra={"event_type": "HEALTH_CHECK_ERROR", "error_details": str(e)},
            )
            self._health_cache = (False, time.monotonic())
            return False

    # ── Fill polling (delegates to FillProcessor) ────────────────────────

    # Minimum interval between Gateway /alpaca/account polls. The execution
    # loop calls poll_fills every iteration (~1s when idle), but account
    # equity does not need second-by-second resolution. Throttling here
    # cuts ~3,600 redundant Gateway calls/hour during quiet periods.
    _ACCOUNT_POLL_MIN_INTERVAL_SECONDS: float = 15.0

    # Separate (faster) cadence for /alpaca/orders polling so fills land in
    # the local fills table before the position monitor evaluates exits.
    # Throttled vs the per-iteration loop to avoid spamming the Gateway.
    _ORDER_POLL_MIN_INTERVAL_SECONDS: float = 5.0

    # Cadence for re-grounding open_positions against the broker. Closes done
    # by the position monitor (direct to Gateway) and option expiries don't
    # flow back as Orion-attributed fills, so the in-memory count drifts high
    # over a long-running process — a periodic ground-truth resync corrects it.
    _POSITION_SYNC_MIN_INTERVAL_SECONDS: float = 120.0

    # An Orion entry is a mid-priced DAY limit. One that has filled NOTHING this
    # long after submission is working an increasingly stale signal and only
    # reserves shared day-trading buying power until it EXPIRES at the close
    # (2026-06-09: EWY/XHB entries sat unfilled all session, then expired).
    # poll_fills cancels it once past this TTL.
    _STALE_ENTRY_ORDER_TTL_SECONDS: float = 180.0

    # Stale-entry-cancel storm controls. A rejected cancel used to be re-issued
    # every _ORDER_POLL_MIN_INTERVAL_SECONDS (5s) forever — 68k+ self-inflicted
    # Gateway 429s/day. Now each order backs off exponentially with jitter on a
    # transient reject, or gives up immediately on a permanent one.
    _CANCEL_BACKOFF_BASE_SECONDS: float = 30.0
    _CANCEL_BACKOFF_CAP_SECONDS: float = 300.0
    _CANCEL_MAX_ATTEMPTS: int = 6
    _CANCEL_MAX_PER_CYCLE: int = 20

    # Missed-CLOSE reconcile. Bounds the by-id get_order lookups one cycle may
    # fan out when broker positions disagree with the fills replay, so a wide
    # disagreement can't reintroduce the stale-cancel 429 storm. Runs on the
    # same non-urgent cadence as the position re-grounding above.
    _CLOSE_RECON_MAX_PER_CYCLE: int = 20

    async def poll_fills(self) -> None:
        """Polls Data Gateway for account equity and updates RiskManager.

        Only account-level equity is synced (shared across all systems).
        Position-level data is filtered to Orion-only in _sync_risk_from_gateway.

        Also renews the service lease (no-op if `acquire_service_lease` was
        never called). Renewal is best-effort and never blocks fill polling.
        """
        await self.renew_service_lease()

        # Re-probe (60s-cached) instead of reading the cached flag. A gateway
        # flap flips _gateway_available False; on an at-max-positions day the
        # only other caller of _check_gateway_available (order submission) is
        # risk-rejected before it runs, so a stale-False flag would disable
        # fill/order polling, snapshots, risk-sync AND missed-fill recovery
        # until restart — observed 2026-06-26: blind 19h after a 00:29 flap.
        if not await self._check_gateway_available():
            return

        client = self._get_gateway_client()

        now = datetime.now(UTC)
        account_poll_due = (
            self._last_fill_poll_ts is None
            or (now - self._last_fill_poll_ts).total_seconds() >= self._ACCOUNT_POLL_MIN_INTERVAL_SECONDS
        )

        if account_poll_due:
            try:
                account = await client.get_account()
                if "error" not in account:
                    equity = float(account.get("equity", 0) or 0)
                    if equity > 0:
                        # Same seed-once pattern as `_sync_risk_from_gateway`:
                        # account-wide equity includes other systems' P&L on
                        # the shared paper account. After the initial seed,
                        # current_equity moves only from Orion-attributed
                        # fills via update_post_fill — overwriting it here
                        # would falsely trip Orion's drawdown kill switch
                        # whenever 3Roses/Cerberus/Kairos/etc. take losses.
                        self.risk_manager.seed_equity_baseline(equity)

                self._last_fill_poll_ts = now
            except Exception as e:
                logger.warning(
                    "Fill polling via Gateway failed",
                    extra={"event_type": "FILL_POLL_ERROR", "error": str(e)},
                )

        # Order/fill poll — separately throttled from account-equity (5s vs 15s)
        # so fills land promptly without spamming /api/v1/alpaca/account.
        now2 = datetime.now(UTC)
        if (
            self._last_order_poll_ts is None
            or (now2 - self._last_order_poll_ts).total_seconds() >= self._ORDER_POLL_MIN_INTERVAL_SECONDS
        ):
            try:
                orders = await client.get_orders(status="all", limit=200)
            except Exception as e:
                logger.warning(
                    "Order poll via Gateway failed",
                    extra={"event_type": "ORDER_POLL_ERROR", "error": str(e)},
                )
                orders = []
                # Intentional: the throttle timestamp (set below) advances even on
                # failure to prevent a tight retry loop when Gateway is unhealthy.
                # Next attempt waits the full _ORDER_POLL_MIN_INTERVAL_SECONDS.
                # Cost: filled orders land up to 5s late after a transient
                # failure; acceptable vs spamming Gateway during a real outage.

            for order in orders:
                # Filter to orion-attributed orders only (shared account safety).
                coid = order.get("client_order_id", "") or ""
                if not coid.startswith(ORDER_ID_PREFIX):
                    continue

                # Update orders.status whenever the broker has a fresher state
                # (idempotent — UPDATE with the latest known values).
                broker_id = order.get("id") or order.get("broker_order_id")
                status = order.get("status")
                if broker_id and status:
                    try:
                        await persist_order_status_update(
                            broker_order_id=str(broker_id),
                            status=str(status),
                            filled_qty=order.get("filled_qty"),
                            filled_avg_price=order.get("filled_avg_price"),
                        )
                    except Exception as e:
                        logger.warning(
                            "Order status update failed",
                            extra={
                                "event_type": "ORDER_STATUS_UPDATE_ERROR",
                                "broker_order_id": broker_id,
                                "error": str(e),
                            },
                        )

                # If the order is filled (or partially), feed it through the fill
                # processor — ProcessedFill table guards against double-processing.
                filled_qty = float(order.get("filled_qty") or 0)
                if filled_qty > 0:
                    try:
                        await self._process_single_fill(order)
                    except Exception as e:
                        logger.warning(
                            "Fill processing failed for order",
                            extra={
                                "event_type": "FILL_PROCESS_ERROR",
                                "broker_order_id": broker_id,
                                "error": str(e),
                            },
                        )

            # Cancel Orion's own resting unfilled entry orders that have aged
            # past the stale-entry TTL (statuses were just refreshed above).
            # Mid-priced DAY limits that never fill otherwise sit reserving
            # shared DTBP until they EXPIRE at the close. Closes don't get a
            # cancellable orders row and bracket legs aren't persisted, so the
            # query's filters scope this to buy-to-open entries only.
            try:
                await self._cancel_stale_entry_orders(client)
            except Exception as e:
                logger.warning(
                    "Stale entry-order cancel sweep failed",
                    extra={"event_type": "STALE_ENTRY_CANCEL_SWEEP_FAILED", "error": str(e)},
                )

            self._last_order_poll_ts = now2

        # Periodically re-ground open_positions against the broker. Closes done
        # by the position monitor (direct to Gateway) and option expiries don't
        # flow back as Orion-attributed fills, so the in-memory count drifts
        # high over a long-running process (2026-05-29: 15 vs broker 12) and
        # would keep entries blocked at the max_positions gate even after real
        # positions fall below the limit. Interval-gated so we don't hit the
        # Gateway every ~1s idle iteration.
        now3 = datetime.now(UTC)
        if (
            self._last_position_sync_ts is None
            or (now3 - self._last_position_sync_ts).total_seconds() >= self._POSITION_SYNC_MIN_INTERVAL_SECONDS
        ):
            self._last_position_sync_ts = now3
            # Recover aged-out CLOSING fills BEFORE the resync. Closes never get
            # an orders row (they persist to exit_decisions), so neither the
            # stale-entry sweep nor any OrderRecord reconcile can surface one — a
            # broker-positions-vs-fills reconcile is the only path to a missed
            # close's cost basis. It must run first: _sync_risk_from_gateway
            # re-grounds open positions to broker truth (flat for a closed
            # symbol), and process_fill on a flat in-memory position would mis-
            # book the close as a phantom short instead of realizing its PnL.
            # Shares the resync's non-urgent cadence; idempotent + bounded.
            try:
                await self._recover_missed_close_fills(client)
            except Exception as e:
                logger.warning(
                    "missed_close_recon_failed",
                    extra={"event_type": "MISSED_CLOSE_RECON_FAILED", "error": str(e)},
                )
            try:
                await self._sync_risk_from_gateway()
            except Exception as e:
                logger.warning(
                    "periodic_risk_resync_failed",
                    extra={"event_type": "PERIODIC_RISK_RESYNC_FAILED", "error": str(e)},
                )

        # Persist a position snapshot for observability (self-throttles to its
        # own interval; the table was empty because this was never called).
        await self._maybe_snapshot_positions()

    async def _process_single_fill(self, fill: Any) -> None:
        """Delegates fill processing to FillProcessor."""
        await self._fill_processor.process_single_fill(fill, self.risk_manager, self._remove_pending_order_compat)

    async def _recover_missed_fill(self, client: Any, broker_order_id: str, ticker: Any) -> bool:
        """Recover a fill that poll_fills' 200-row window aged out before processing.

        When the stale-entry sweep learns from the broker that an order is ALREADY
        FILLED (its cancel was rejected with "order is already in 'filled' state"),
        poll_fills never saw the fill, so no ``FillRecord`` was written and
        ``_compute_cost_basis_from_fills`` (which replays the fills table) cannot
        reconstruct this order's cost basis. Fetch the specific order by id — a
        direct lookup that is NOT bounded by the 200-row recent window — and feed
        it through the idempotent fill processor (``ProcessedFill`` guards against
        double-processing, including a later poll that re-sees the same order), so
        the ``FillRecord`` lands and risk state updates exactly as a live poll
        would have. Returns True iff a fill was processed.

        NEVER raises: a recovery failure must not break the sweep or block the
        caller's status reconcile (the storm fix). On any failure the per-order
        cost-basis gap simply persists until a later poll or the PnL
        reconciliation job surfaces it — better than the silent gap that exists
        today, where the fill is never recovered at all.
        """
        try:
            order = await client.get_order(broker_order_id)
        except Exception as e:
            logger.warning(
                "Missed-fill recovery: get_order failed; cost basis for this order stays unrecovered",
                extra={
                    "event_type": "MISSED_FILL_RECOVERY_FETCH_FAILED",
                    "order_id": broker_order_id,
                    "ticker": ticker,
                    "error": str(e),
                },
            )
            return False

        # get_order returns {"error": ...} (never raises) on 4xx/5xx/timeout. An
        # unguarded error dict would parse to filled_qty=0 and be silently dropped.
        if not isinstance(order, dict) or "error" in order:
            logger.warning(
                "Missed-fill recovery: gateway returned no usable order; cost basis stays unrecovered",
                extra={
                    "event_type": "MISSED_FILL_RECOVERY_NO_ORDER",
                    "order_id": broker_order_id,
                    "ticker": ticker,
                    "detail": (order.get("detail") or order.get("error")) if isinstance(order, dict) else None,
                },
            )
            return False

        if float(order.get("filled_qty") or 0) <= 0:
            # Race: the cancel-reject said "filled" but this snapshot shows zero.
            # Skip — process_single_fill would no-op on a zero increment anyway.
            logger.warning(
                "Missed-fill recovery: order reports zero filled_qty; skipping (race)",
                extra={
                    "event_type": "MISSED_FILL_RECOVERY_ZERO_QTY",
                    "order_id": broker_order_id,
                    "ticker": ticker,
                },
            )
            return False

        try:
            await self._process_single_fill(order)
        except Exception as e:
            logger.warning(
                "Missed-fill recovery: fill processing failed; cost basis stays unrecovered",
                extra={
                    "event_type": "MISSED_FILL_RECOVERY_PROCESS_FAILED",
                    "order_id": broker_order_id,
                    "ticker": ticker,
                    "error": str(e),
                },
            )
            return False

        logger.info(
            f"Recovered missed fill for already-filled stale entry order {broker_order_id} on {ticker} "
            f"(poll_fills' 200-row window aged it out)",
            extra={
                "event_type": "MISSED_FILL_RECOVERED",
                "order_id": broker_order_id,
                "ticker": ticker,
                "filled_qty": float(order.get("filled_qty") or 0),
            },
        )
        return True

    async def _recover_missed_close_fills(self, client: Any) -> int:
        """Recover aged-out CLOSING fills the 200-row order-poll window missed.

        A successful close never gets an ``orders`` row (it persists to
        ``exit_decisions``), so the stale-entry sweep — which keys off
        ``OrderRecord`` — can never surface a missed close. When such a close
        fill ages out of ``poll_fills``' 200-row ``get_orders`` window before it
        is processed, no ``FillRecord`` lands and
        ``_compute_cost_basis_from_fills`` keeps replaying a position the broker
        has already closed: realized PnL / cost basis stay incomplete.

        Detection: compare the broker's positions (``get_positions``) against the
        fills-derived positions. An Orion symbol whose fills-replay magnitude
        EXCEEDS the broker holding has an unprocessed REDUCING (closing) fill.
        The inverse — broker magnitude > fills — is a missed ENTRY, already
        handled by the stale-entry sweep, so it is ignored here.

        Recovery: for each disagreeing symbol read the broker's FILL activities
        (the only surface that still carries an aged-out close's order id —
        ``get_orders``' window is the very thing that missed it), and for an
        order we have processed ZERO fills for (the partial double-count guard
        the entry path gets for free from its pre-fill scoping), feed it through
        the same idempotent ``_recover_missed_fill`` the entry sweep uses.

        Shared-account safe: detection symbols come from orion-only fills, and
        ``_recover_missed_fill`` → ``_process_single_fill`` re-checks the
        ``orion_`` prefix, so another system's order on the same symbol is
        fetched (one bounded ``get_order``) and then skipped, never counted.

        Bounded against the 429-storm class: one ``get_account_activities`` call
        only when a disagreement exists, plus at most
        ``_CLOSE_RECON_MAX_PER_CYCLE`` ``get_order`` lookups per cycle.
        Idempotent (``ProcessedFill`` marker). NEVER raises. Returns the number
        of fills recovered.
        """
        fills_positions = await self._compute_cost_basis_from_fills()
        # Only symbols the fills replay still thinks we hold can have a missed
        # close. (_compute_cost_basis_from_fills is orion-only by construction.)
        held = {s: float(v.get("qty", 0.0)) for s, v in fills_positions.items() if abs(float(v.get("qty", 0.0))) > 1e-9}
        if not held:
            return 0

        try:
            broker_positions = await client.get_positions()
        except Exception as e:
            logger.warning(
                "close-recon: get_positions failed; skipping cycle",
                extra={"event_type": "MISSED_CLOSE_RECON_POSITIONS_FAILED", "error": str(e)},
            )
            return 0

        broker_qty: dict[str, float] = {}
        for p in broker_positions or []:
            sym = p.get("symbol")
            if sym:
                broker_qty[sym] = float(p.get("qty", 0) or 0)

        # fills magnitude > broker magnitude ⇒ a reducing fill never landed.
        # ponytail: a same-cycle close-and-flip (sign reversal) is rare and is
        # left to the next cycle / reconcile_pnl; this magnitude test targets the
        # common case (full or partial close that aged out) and stays idempotent.
        missed = [s for s, q in held.items() if abs(q) - abs(broker_qty.get(s, 0.0)) > 1e-9]
        if not missed:
            return 0

        try:
            activities = await client.get_account_activities("FILL")
        except Exception as e:
            logger.warning(
                "close-recon: get_account_activities failed; skipping cycle",
                extra={"event_type": "MISSED_CLOSE_RECON_ACTIVITIES_FAILED", "error": str(e)},
            )
            return 0

        # Map disagreeing symbol → de-duplicated broker order ids (the FILL
        # activity carries order_id + symbol but NOT client_order_id, so the
        # orion check happens later in _process_single_fill via get_order).
        order_ids_by_symbol: dict[str, list[str]] = {s: [] for s in missed}
        seen: dict[str, set[str]] = {s: set() for s in missed}
        for act in activities or []:
            sym = act.get("symbol")
            oid = act.get("order_id")
            if sym in order_ids_by_symbol and oid and oid not in seen[sym]:
                seen[sym].add(oid)
                order_ids_by_symbol[sym].append(str(oid))

        recovered = 0
        attempted = 0
        for sym in missed:
            for oid in order_ids_by_symbol[sym]:
                # An order we've already counted ANY fill for is either the entry
                # or an already-recovered close — re-feeding it would double-count.
                # DB read, not a Gateway call, so it does not consume the cap.
                if await has_processed_fill_for_order(oid):
                    continue
                if attempted >= self._CLOSE_RECON_MAX_PER_CYCLE:
                    logger.warning(
                        "close-recon: per-cycle get_order cap hit; remaining deferred to next cycle",
                        extra={"event_type": "MISSED_CLOSE_RECON_CAP_HIT", "cap": self._CLOSE_RECON_MAX_PER_CYCLE},
                    )
                    return recovered
                attempted += 1
                if await self._recover_missed_fill(client, oid, sym):
                    recovered += 1

        if recovered:
            logger.info(
                f"Recovered {recovered} missed closing fill(s) across {len(missed)} symbol(s) "
                f"(poll_fills' 200-row window aged them out)",
                extra={
                    "event_type": "MISSED_CLOSE_FILLS_RECOVERED",
                    "recovered": recovered,
                    "symbols": missed,
                },
            )
        return recovered

    # ── Position snapshots (delegates to fill_processor module) ──────────

    async def _maybe_snapshot_positions(self, min_interval_seconds: int = 60) -> None:
        """Persist positions snapshots via Data Gateway."""
        if not self._gateway_available:
            return

        client = self._get_gateway_client()
        result = await maybe_snapshot_positions(client, self.last_positions_snapshot_ts, min_interval_seconds)
        if result is not None:
            self.last_positions_snapshot_ts = result

    def _record_result(self, success: bool) -> None:
        """Record a broker round-trip outcome with its monotonic timestamp.

        The breaker uses a time window, so the timestamp determines whether a
        past failure is still counted. Pruning runs lazily on read in
        `_check_circuit_breaker` / `_prune_order_history`.
        """
        self.order_history.append((time.monotonic(), success))
