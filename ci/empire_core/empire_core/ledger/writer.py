"""LedgerWriter — primary interface for recording standardized trade data."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from empire_core.ledger.models import (
    Base,
    LedgerFill,
    LedgerOrder,
    LedgerPosition,
    LedgerSnapshot,
    LedgerTrade,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


def _json_dumps(value: Any | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value)


def _json_loads(value: str | None) -> Any | None:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _row_to_dict(row: Any, json_fields: tuple[str, ...] = ()) -> dict:
    """Convert a SQLAlchemy model instance to a dict, parsing JSON fields."""
    d = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if col.name in json_fields:
            val = _json_loads(val)
        # Convert date/datetime to ISO strings for consistency
        if isinstance(val, (date, datetime)):
            val = val.isoformat()
        d[col.name] = val
    return d


class LedgerWriter:
    """Records standardized trade data to a local ledger.db."""

    def __init__(self, db_path: str, system: str) -> None:
        self._system = system
        self._engine = create_engine(f"sqlite:///{db_path}", pool_pre_ping=True)
        Base.metadata.create_all(self._engine)
        self._migrate()

    def _migrate(self) -> None:
        """Add any columns present in the ORM models but missing from the DB.

        SQLAlchemy's ``create_all`` only creates missing *tables*, it never
        alters existing tables.  This method inspects each model table and
        issues ``ALTER TABLE ... ADD COLUMN`` for every column that exists in
        the model but is absent from the physical schema.  This handles DB
        files created before new columns were added to the models.
        """
        inspector = inspect(self._engine)
        with self._engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                table_name = table.name
                if not inspector.has_table(table_name):
                    continue
                existing = {col["name"] for col in inspector.get_columns(table_name)}
                for col in table.columns:
                    if col.name in existing:
                        continue
                    col_type = col.type.compile(dialect=self._engine.dialect)
                    nullable = "NULL" if col.nullable else "NOT NULL DEFAULT ''"
                    conn.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type} {nullable}")
                    )
            conn.commit()

    def _session(self) -> Session:
        return Session(self._engine)

    # --- Orders ---

    def record_order(
        self,
        correlation_id: str,
        symbol: str,
        side: str,
        qty: float,
        order_type: str,
        strategy: str,
        limit_price: float | None = None,
        broker_order_id: str | None = None,
        meta: dict | None = None,
    ) -> str:
        order_id = _new_id()
        now = _now()
        with self._session() as session:
            order = LedgerOrder(
                id=order_id,
                correlation_id=correlation_id,
                system=self._system,
                strategy=strategy,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                status="submitted",
                broker_order_id=broker_order_id,
                submitted_at=now,
                updated_at=now,
                meta=_json_dumps(meta),
            )
            session.add(order)
            session.commit()
        return order_id

    def update_order_status(self, order_id: str, status: str, broker_order_id: str | None = None) -> None:
        with self._session() as session:
            order = session.get(LedgerOrder, order_id)
            if order is None:
                raise ValueError(f"Order {order_id} not found")
            order.status = status
            order.updated_at = _now()
            if broker_order_id is not None:
                order.broker_order_id = broker_order_id
            session.commit()

    def get_orders(self, limit: int = 100) -> list[dict]:
        with self._session() as session:
            rows = session.query(LedgerOrder).order_by(LedgerOrder.submitted_at.desc()).limit(limit).all()
            return [_row_to_dict(r, json_fields=("meta",)) for r in rows]

    # --- Fills ---

    def record_fill(
        self,
        order_id: str,
        fill_price: float,
        fill_qty: float,
        commission: float = 0.0,
        filled_at: datetime | None = None,
    ) -> str:
        fill_id = _new_id()
        with self._session() as session:
            fill = LedgerFill(
                id=fill_id,
                order_id=order_id,
                fill_price=fill_price,
                fill_qty=fill_qty,
                commission=commission,
                filled_at=filled_at or _now(),
            )
            session.add(fill)
            session.commit()
        return fill_id

    # --- Trades ---

    def open_trade(
        self,
        correlation_id: str,
        symbol: str,
        side: str,
        qty: float,
        entry_price: float,
        entry_time: datetime,
        strategy: str,
        initial_risk: float | None = None,
        regime_entry: str | None = None,
        tags: list | None = None,
        meta: dict | None = None,
    ) -> str:
        trade_id = _new_id()
        with self._session() as session:
            trade = LedgerTrade(
                id=trade_id,
                correlation_id=correlation_id,
                system=self._system,
                strategy=strategy,
                symbol=symbol,
                side=side,
                qty=qty,
                entry_price=entry_price,
                entry_time=entry_time,
                commission_total=0.0,
                initial_risk=initial_risk,
                regime_entry=regime_entry,
                tags=_json_dumps(tags),
                meta=_json_dumps(meta),
            )
            session.add(trade)
            session.commit()
        return trade_id

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        pnl_gross: float,
        pnl_net: float,
        commission_total: float = 0.0,
        pnl_r: float | None = None,
        mae_r: float | None = None,
        mfe_r: float | None = None,
        ts_mfe: datetime | None = None,
        ts_mae: datetime | None = None,
        time_to_mfe_seconds: float | None = None,
        time_to_mae_seconds: float | None = None,
        mfe_mae_ratio: float | None = None,
        capture_efficiency: float | None = None,
        excursion_velocity: float | None = None,
        slippage: float | None = None,
        regime_exit: str | None = None,
        holding_seconds: int | None = None,
        tags: list | None = None,
        meta: dict | None = None,
    ) -> None:
        with self._session() as session:
            trade = session.get(LedgerTrade, trade_id)
            if trade is None:
                raise ValueError(f"Trade {trade_id} not found")
            trade.exit_price = exit_price
            trade.exit_time = exit_time
            trade.pnl_gross = pnl_gross
            trade.pnl_net = pnl_net
            trade.commission_total = commission_total
            trade.pnl_r = pnl_r
            trade.mae_r = mae_r
            trade.mfe_r = mfe_r
            trade.ts_mfe = ts_mfe
            trade.ts_mae = ts_mae
            trade.time_to_mfe_seconds = time_to_mfe_seconds
            trade.time_to_mae_seconds = time_to_mae_seconds
            trade.mfe_mae_ratio = mfe_mae_ratio
            trade.capture_efficiency = capture_efficiency
            trade.excursion_velocity = excursion_velocity
            trade.slippage = slippage
            trade.regime_exit = regime_exit
            trade.holding_seconds = holding_seconds
            if tags is not None:
                trade.tags = _json_dumps(tags)
            if meta is not None:
                trade.meta = _json_dumps(meta)
            session.commit()

    def get_trades(self, since: datetime | None = None, limit: int = 100) -> list[dict]:
        with self._session() as session:
            q = session.query(LedgerTrade)
            if since is not None:
                q = q.filter(LedgerTrade.entry_time >= since)
            rows = q.order_by(LedgerTrade.entry_time.desc()).limit(limit).all()
            return [_row_to_dict(r, json_fields=("tags", "meta")) for r in rows]

    # --- Positions ---

    def upsert_position(
        self,
        symbol: str,
        side: str,
        qty: float,
        avg_entry_price: float,
        strategy: str,
        current_price: float | None = None,
        unrealized_pnl: float | None = None,
        meta: dict | None = None,
    ) -> None:
        now = _now()
        with self._session() as session:
            existing = (
                session.query(LedgerPosition)
                .filter_by(system=self._system, symbol=symbol)
                .first()
            )
            if existing is not None:
                existing.side = side
                existing.qty = qty
                existing.avg_entry_price = avg_entry_price
                existing.strategy = strategy
                existing.current_price = current_price
                existing.unrealized_pnl = unrealized_pnl
                existing.updated_at = now
                existing.meta = _json_dumps(meta)
            else:
                pos = LedgerPosition(
                    id=_new_id(),
                    system=self._system,
                    strategy=strategy,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    avg_entry_price=avg_entry_price,
                    current_price=current_price,
                    unrealized_pnl=unrealized_pnl,
                    opened_at=now,
                    updated_at=now,
                    meta=_json_dumps(meta),
                )
                session.add(pos)
            session.commit()

    def close_position(self, symbol: str) -> None:
        with self._session() as session:
            pos = (
                session.query(LedgerPosition)
                .filter_by(system=self._system, symbol=symbol)
                .first()
            )
            if pos is not None:
                session.delete(pos)
                session.commit()

    def get_positions(self) -> list[dict]:
        with self._session() as session:
            rows = (
                session.query(LedgerPosition)
                .filter_by(system=self._system)
                .all()
            )
            return [_row_to_dict(r, json_fields=("meta",)) for r in rows]

    # --- Snapshots ---

    def snapshot_day(
        self,
        date: date,
        equity: float,
        realized_pnl: float,
        unrealized_pnl: float,
        total_pnl: float,
        high_water_mark: float,
        drawdown: float,
        drawdown_pct: float,
        trades_today: int,
        winners: int,
        losers: int,
        win_rate: float,
        meta: dict | None = None,
    ) -> None:
        with self._session() as session:
            existing = (
                session.query(LedgerSnapshot)
                .filter_by(system=self._system, date=date)
                .first()
            )
            if existing is not None:
                existing.equity = equity
                existing.realized_pnl = realized_pnl
                existing.unrealized_pnl = unrealized_pnl
                existing.total_pnl = total_pnl
                existing.high_water_mark = high_water_mark
                existing.drawdown = drawdown
                existing.drawdown_pct = drawdown_pct
                existing.trades_today = trades_today
                existing.winners = winners
                existing.losers = losers
                existing.win_rate = win_rate
                existing.meta = _json_dumps(meta)
            else:
                snap = LedgerSnapshot(
                    id=_new_id(),
                    system=self._system,
                    date=date,
                    equity=equity,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    total_pnl=total_pnl,
                    high_water_mark=high_water_mark,
                    drawdown=drawdown,
                    drawdown_pct=drawdown_pct,
                    trades_today=trades_today,
                    winners=winners,
                    losers=losers,
                    win_rate=win_rate,
                    meta=_json_dumps(meta),
                )
                session.add(snap)
            session.commit()

    def get_snapshots(self, days: int = 30) -> list[dict]:
        with self._session() as session:
            rows = (
                session.query(LedgerSnapshot)
                .filter_by(system=self._system)
                .order_by(LedgerSnapshot.date.desc())
                .limit(days)
                .all()
            )
            return [_row_to_dict(r, json_fields=("meta",)) for r in rows]

    # --- Summaries ---

    def get_daily_summary(self, target_date: date | None = None) -> dict:
        if target_date is None:
            target_date = _now().date()

        with self._session() as session:
            # Get all closed trades for this system on the target date
            trades = (
                session.query(LedgerTrade)
                .filter(
                    LedgerTrade.system == self._system,
                    LedgerTrade.exit_time.isnot(None),
                )
                .all()
            )

        # Filter to trades closed on target_date
        day_trades = []
        for t in trades:
            exit_dt = t.exit_time
            if isinstance(exit_dt, str):
                exit_dt = datetime.fromisoformat(exit_dt)
            if exit_dt.date() == target_date:
                day_trades.append(t)

        total_trades = len(day_trades)
        winners = sum(1 for t in day_trades if (t.pnl_gross or 0) > 0)
        losers = sum(1 for t in day_trades if (t.pnl_gross or 0) < 0)
        win_rate = winners / total_trades if total_trades > 0 else 0.0
        total_pnl_gross = sum(t.pnl_gross or 0 for t in day_trades)
        total_pnl_net = sum(t.pnl_net or 0 for t in day_trades)

        return {
            "date": target_date.isoformat(),
            "system": self._system,
            "total_trades": total_trades,
            "winners": winners,
            "losers": losers,
            "win_rate": win_rate,
            "total_pnl_gross": total_pnl_gross,
            "total_pnl_net": total_pnl_net,
        }
