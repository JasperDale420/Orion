"""SQLAlchemy models for the standardized trading ledger."""
from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class LedgerOrder(Base):
    __tablename__ = "ledger_orders"

    id = Column(String, primary_key=True)
    correlation_id = Column(String, nullable=False, index=True)
    system = Column(String, nullable=False, index=True)
    strategy = Column(String, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    qty = Column(Float, nullable=False)
    order_type = Column(String, nullable=False)
    limit_price = Column(Float, nullable=True)
    status = Column(String, nullable=False)
    broker_order_id = Column(String, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    meta = Column(Text, nullable=True)


class LedgerFill(Base):
    __tablename__ = "ledger_fills"

    id = Column(String, primary_key=True)
    order_id = Column(String, nullable=False, index=True)
    fill_price = Column(Float, nullable=False)
    fill_qty = Column(Float, nullable=False)
    commission = Column(Float, nullable=False, default=0.0)
    filled_at = Column(DateTime(timezone=True), nullable=False)


class LedgerTrade(Base):
    __tablename__ = "ledger_trades"

    id = Column(String, primary_key=True)
    correlation_id = Column(String, nullable=False, index=True)
    system = Column(String, nullable=False, index=True)
    strategy = Column(String, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    qty = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    pnl_gross = Column(Float, nullable=True)
    pnl_net = Column(Float, nullable=True)
    commission_total = Column(Float, nullable=False, default=0.0)
    initial_risk = Column(Float, nullable=True)
    pnl_r = Column(Float, nullable=True)
    mae_r = Column(Float, nullable=True)
    mfe_r = Column(Float, nullable=True)
    ts_mfe = Column(DateTime(timezone=True), nullable=True)
    ts_mae = Column(DateTime(timezone=True), nullable=True)
    time_to_mfe_seconds = Column(Float, nullable=True)
    time_to_mae_seconds = Column(Float, nullable=True)
    mfe_mae_ratio = Column(Float, nullable=True)
    capture_efficiency = Column(Float, nullable=True)
    excursion_velocity = Column(Float, nullable=True)
    slippage = Column(Float, nullable=True)
    regime_entry = Column(String, nullable=True)
    regime_exit = Column(String, nullable=True)
    holding_seconds = Column(Integer, nullable=True)
    tags = Column(Text, nullable=True)
    meta = Column(Text, nullable=True)


class LedgerSnapshot(Base):
    __tablename__ = "ledger_snapshots"
    __table_args__ = (UniqueConstraint("system", "date", name="uq_snapshot_system_date"),)

    id = Column(String, primary_key=True)
    system = Column(String, nullable=False, index=True)
    date = Column(Date, nullable=False)
    equity = Column(Float, nullable=False)
    realized_pnl = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
    high_water_mark = Column(Float, nullable=False)
    drawdown = Column(Float, nullable=False)
    drawdown_pct = Column(Float, nullable=False)
    trades_today = Column(Integer, nullable=False)
    winners = Column(Integer, nullable=False)
    losers = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=False)
    meta = Column(Text, nullable=True)


class LedgerPosition(Base):
    __tablename__ = "ledger_positions"
    __table_args__ = (UniqueConstraint("system", "symbol", name="uq_position_system_symbol"),)

    id = Column(String, primary_key=True)
    system = Column(String, nullable=False, index=True)
    strategy = Column(String, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    qty = Column(Float, nullable=False)
    avg_entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    meta = Column(Text, nullable=True)
