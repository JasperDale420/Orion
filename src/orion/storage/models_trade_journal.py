from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class TradeJournalEntry(Base):
    """
    PRD.md §12.4: Trade journal entries linking signal/decision → evidence → orders → fills (PnL optional as computed later).
    """

    __tablename__ = "trade_journal_entries"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    signal_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    solver_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    direction: Mapped[str | None] = mapped_column(String, nullable=True)

    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    decision_trace_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    filled_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_trade_journal_ticker_created", "ticker", "created_at_utc"),)
