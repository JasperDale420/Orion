"""
Position Manager - Track open positions with entry context for exit rule evaluation.

Per Dynamic Exit Strategies PDF:
- Stores entry context (IV, premium window, sweep count)
- Provides position state to exit rules
- Integrates with broker for actual position tracking
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.core.enums import DecisionStatus
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.models_gold import CandidateTrade, ExitDecision, StrategyDecision

logger = setup_struct_logger(__name__)


@dataclass
class OpenPosition:
    """Represents an open position with entry context for exit rule evaluation."""

    ticker: str
    direction: str  # LONG or SHORT
    candidate_id: str
    decision_id: str

    # Entry timing
    entry_ts: datetime
    entry_price: float
    entry_option_price: float | None = None

    # Entry context for exit rules
    option_chain: str | None = None  # Full OCC symbol
    entry_iv: float | None = None
    entry_premium_window: float = 0.0  # Sum of aligned premium at entry
    entry_sweep_count: int = 0  # Sweeps in first 5 min window
    entry_oi: float | None = None  # Open interest at entry

    # Position size
    qty: float = 0.0

    # Metadata
    rule_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class PositionManager:
    """
    Manages open positions and provides context to exit rules.

    Responsibilities:
    - Track positions opened by execution engine
    - Store entry context (IV, premium, sweep count) for exit rule evaluation
    - Sync with broker positions on startup
    - Provide position list to exit rule evaluator
    """

    def __init__(self) -> None:
        # Key positions by candidate_id to avoid collapsing same-underlying contracts.
        self._positions: dict[str, OpenPosition] = {}
        self._initialized = False
        # Guard against duplicate close orders from parallel exit systems
        # (PositionMonitor ML exits + rule-based exits in main_execution.py).
        self._closing_symbols: set[str] = set()

    async def initialize(self) -> None:
        """Load open positions from database on startup."""
        try:
            # Fetch executed decisions without exit
            async def fetch_open_positions(session: Any) -> list[Any]:
                stmt = (
                    select(StrategyDecision, CandidateTrade)
                    .join(CandidateTrade, StrategyDecision.candidate_id == CandidateTrade.candidate_id)
                    .outerjoin(ExitDecision, StrategyDecision.candidate_id == ExitDecision.candidate_id)
                    .where(
                        StrategyDecision.executed_successfully == DecisionStatus.TRUE,
                        ExitDecision.exit_id.is_(None),
                    )
                    .order_by(StrategyDecision.timestamp_utc.desc())
                )
                result = await session.execute(stmt)
                return result.all()

            rows = await db_query(fetch_open_positions)

            for decision, candidate in rows:
                pos = self._create_position_from_decision(decision, candidate)
                if pos:
                    self._positions[pos.candidate_id] = pos

            self._initialized = True
            logger.info(
                "PositionManager initialized",
                extra={"event_type": "POSITION_MANAGER_INIT", "position_count": len(self._positions)},
            )
        except Exception as e:
            logger.error(f"Failed to initialize PositionManager: {e}")
            self._initialized = True  # Mark as initialized to avoid blocking

    def _create_position_from_decision(
        self, decision: StrategyDecision, candidate: CandidateTrade
    ) -> OpenPosition | None:
        """Create OpenPosition from database records."""
        try:
            ep = decision.execution_params or {}
            evidence = candidate.evidence or {}
            entry_price = float(ep.get("limit_price", 0))
            option_chain = self._resolve_option_chain(candidate=candidate, entry_context=evidence)

            return OpenPosition(
                ticker=candidate.ticker,
                direction=candidate.direction,
                candidate_id=candidate.candidate_id,
                decision_id=decision.decision_id,
                entry_ts=decision.timestamp_utc,
                entry_price=entry_price,
                entry_option_price=entry_price if option_chain else None,
                option_chain=option_chain,
                entry_iv=evidence.get("iv"),
                entry_premium_window=float(evidence.get("premium", 0)),
                entry_oi=evidence.get("open_interest"),
                rule_id=candidate.rule_id,
            )
        except Exception as e:
            logger.warning(f"Failed to create position from decision {decision.decision_id}: {e}")
            return None

    def add_position(
        self,
        candidate: CandidateTrade,
        decision: StrategyDecision,
        entry_context: dict[str, Any] | None = None,
    ) -> OpenPosition:
        """
        Add a new position after successful execution.

        Args:
            candidate: The CandidateTrade that triggered the position
            decision: The StrategyDecision with execution details
            entry_context: Additional context (IV, premium window, sweep count)
        """
        ctx = entry_context or {}
        ep = decision.execution_params or {}
        entry_price = float(ep.get("limit_price", 0))
        option_chain = self._resolve_option_chain(candidate=candidate, entry_context=ctx)
        entry_option_price = ctx.get("entry_option_price")
        if entry_option_price is None and option_chain:
            entry_option_price = entry_price

        pos = OpenPosition(
            ticker=candidate.ticker,
            direction=candidate.direction,
            candidate_id=candidate.candidate_id,
            decision_id=decision.decision_id,
            entry_ts=decision.timestamp_utc or datetime.now(UTC),
            entry_price=entry_price,
            entry_option_price=float(entry_option_price) if entry_option_price is not None else None,
            option_chain=option_chain,
            entry_iv=ctx.get("iv"),
            entry_premium_window=float(ctx.get("premium_window", 0)),
            entry_sweep_count=int(ctx.get("sweep_count", 0)),
            entry_oi=ctx.get("open_interest"),
            qty=float(ep.get("qty", 0)),
            rule_id=candidate.rule_id,
        )

        self._positions[candidate.candidate_id] = pos
        logger.info(
            f"Position added: {candidate.ticker} {candidate.direction}",
            extra={
                "event_type": "POSITION_ADDED",
                "ticker": candidate.ticker,
                "direction": candidate.direction,
                "entry_iv": pos.entry_iv,
            },
        )
        return pos

    def is_closing(self, symbol: str) -> bool:
        """Check if a close order is already in progress for this symbol."""
        return symbol in self._closing_symbols

    def mark_closing(self, symbol: str) -> bool:
        """
        Mark a symbol as having a close order in progress.

        Returns True if the symbol was successfully marked (was not already closing).
        Returns False if a close is already in progress (duplicate close blocked).
        """
        if symbol in self._closing_symbols:
            logger.warning(
                f"Duplicate close blocked: {symbol} already has a close in progress",
                extra={"event_type": "DUPLICATE_CLOSE_BLOCKED", "ticker": symbol},
            )
            return False
        self._closing_symbols.add(symbol)
        logger.info(
            f"Symbol marked as closing: {symbol}",
            extra={"event_type": "CLOSE_IN_PROGRESS", "ticker": symbol},
        )
        return True

    def unmark_closing(self, symbol: str) -> None:
        """Remove the closing-in-progress guard for a symbol."""
        self._closing_symbols.discard(symbol)

    def get_position(self, ticker: str) -> OpenPosition | None:
        """Get the most recent tracked position for a ticker if exists."""
        matches = [p for p in self._positions.values() if p.ticker == ticker]
        if not matches:
            return None
        return max(matches, key=lambda p: p.entry_ts)

    def get_open_positions(self) -> list[OpenPosition]:
        """Return all open positions."""
        return list(self._positions.values())

    def remove_position(self, identifier: str) -> OpenPosition | None:
        """
        Remove position after exit.

        Accepts either candidate_id (preferred) or ticker for backward compatibility.
        """
        pos = self._positions.pop(identifier, None)
        if pos is None:
            for candidate_id, existing in list(self._positions.items()):
                if existing.ticker == identifier:
                    pos = self._positions.pop(candidate_id)
                    break
        if pos:
            logger.info(
                f"Position removed: {pos.ticker}",
                extra={"event_type": "POSITION_REMOVED", "ticker": pos.ticker},
            )
        return pos

    def has_position(self, ticker: str) -> bool:
        """Check if we have an open position for ticker."""
        return any(position.ticker == ticker for position in self._positions.values())

    async def sync_with_broker(self, connector: Any) -> None:
        """
        Sync internal state with broker positions.
        Removes positions that no longer exist at broker.
        """
        if not connector:
            return

        try:
            import asyncio

            broker_positions = await asyncio.to_thread(connector.client.get_all_positions)
            broker_tickers = {str(p.symbol) for p in broker_positions}

            # Remove positions not at broker
            to_remove = [
                candidate_id
                for candidate_id, position in self._positions.items()
                if position.ticker not in broker_tickers
            ]
            for candidate_id in to_remove:
                removed = self.remove_position(candidate_id)
                if removed:
                    logger.info(f"Position {removed.ticker} removed - not found at broker")

            logger.info(
                "PositionManager synced with broker",
                extra={"broker_count": len(broker_tickers), "tracked_count": len(self._positions)},
            )
        except Exception as e:
            logger.error(f"Failed to sync with broker: {e}")

    @staticmethod
    def _resolve_option_chain(candidate: CandidateTrade, entry_context: dict[str, Any]) -> str | None:
        """
        Resolve canonical option contract for tracked positions.

        Priority:
        1) candidate.option_symbol (canonical model field)
        2) runtime entry_context.option_chain
        3) candidate evidence option_chain (legacy payloads)
        """
        option_symbol = (getattr(candidate, "option_symbol", None) or "").strip()
        if option_symbol:
            return option_symbol

        ctx_chain = (entry_context.get("option_chain") or "").strip()
        if ctx_chain:
            return ctx_chain

        evidence = candidate.evidence or {}
        evidence_chain = (evidence.get("option_chain") or "").strip()
        return evidence_chain or None
