import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from orion.api.auth import require_api_key
from orion.api.deps import get_db
from orion.api.schemas import ExperimentResponse, PromotionRecommendationResponse, SolverMetricsResponse, SolverResponse
from orion.rag.vector_store import VectorStore
from orion.shared.db_utils import db_write

# Setup logger (FastAPI usually handles its own, but we can hook in ours)
from orion.shared.logger import setup_struct_logger
from orion.storage.models import BronzeEvent
from orion.storage.models_audit import AuditLog
from orion.storage.models_gold import CandidateTrade, GoldTickerRollup
from orion.storage.models_silver import SilverOptionFlow
from orion.storage.models_solvers import MetaExperiment, PromotionRecommendation, Solver, SolverMetrics

logger = setup_struct_logger("orion.api")

app = FastAPI(
    title="Orion Admin API",
    description="Operational visibility into Solvers, Experiments, and Metrics.",
    version="1.0.0",
)


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Returns a friendly 404 response without exposing internal routes.
    """
    return JSONResponse(
        status_code=404,
        content={
            "detail": "Resource not found",
            "suggestion": "Check the URL for typos.",
        },
    )


@app.middleware("http")
async def audit_middleware(request: Request, call_next: Any) -> Response:
    trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
    request.state.trace_id = trace_id
    response: Response = await call_next(request)

    async def save_audit_log(session: Any) -> None:
        session.add(
            AuditLog(
                id=str(uuid.uuid4()),
                run_id=os.getenv("ORION_RUN_ID"),
                trace_id=trace_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                client_host=request.client.host if request.client else None,
                query_params=dict(request.query_params),
            )
        )

    async def save_audit_log_safe() -> None:
        try:
            await db_write(save_audit_log)
        except Exception as e:
            logger.error(
                "Failed to write audit log",
                extra={"event_type": "AUDIT_LOG_ERROR", "trace_id": trace_id, "error": str(e)},
            )

    # Offload audit log writing to background task to avoid blocking the response
    if response.background:
        original_bg = response.background

        async def chained_background() -> None:
            await original_bg()
            await save_audit_log_safe()

        response.background = BackgroundTask(chained_background)
    else:
        response.background = BackgroundTask(save_audit_log_safe)

    response.headers["x-trace-id"] = trace_id
    return response


@app.get("/", tags=["System"])
async def root() -> Dict[str, Any]:
    """
    Root endpoint providing API information and status.
    """
    return {
        "message": "Welcome to Orion Admin API! 🚀",
        "app": "Orion Admin API",
        "version": "1.0.0",
        "status": "operational",
        "timestamp_utc": datetime.now(timezone.utc),
        "links": {
            "docs": "/docs",
            "health": "/health",
        },
    }


@app.get("/health", tags=["System"])
async def health_check() -> Dict[str, str]:
    return {"status": "ok"}


# --- Solvers ---


@app.get("/solvers", response_model=List[SolverResponse])
async def list_solvers(
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> List[Solver]:
    """
    List registered solvers with pagination.
    """
    # Optimization: Select specific columns to avoid loading large definition_json
    stmt = (
        select(
            Solver.solver_id,
            Solver.family_name,
            Solver.stage,
            Solver.is_active,
            Solver.config,
            Solver.created_at_utc,
            Solver.total_pnl,
            Solver.sharpe_ratio,
            Solver.win_rate,
            Solver.trades_count,
        )
        .offset(skip)
        .limit(limit)
    )
    if active_only:
        stmt = stmt.where((Solver.status == "active") | ((Solver.status.is_(None)) & (Solver.is_active)))

    # Order by creation desc
    stmt = stmt.order_by(desc(Solver.created_at_utc))

    result = await db.execute(stmt)
    return result.all()


@app.get("/solvers/{solver_id}", response_model=SolverResponse)
async def get_solver(solver_id: str, db: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> Solver:
    """
    Get a specific solver by ID (DNA).
    """
    stmt = select(Solver).where(Solver.solver_id == solver_id)
    result = await db.execute(stmt)
    solver = result.scalars().first()
    if not solver:
        raise HTTPException(status_code=404, detail="Solver not found")
    return solver


# --- Metrics ---


@app.get("/metrics", response_model=List[SolverMetricsResponse])
async def list_metrics(
    solver_id: Optional[str] = None,
    dataset_tag: Optional[str] = Query(None, description="e.g. train, val, test"),
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> List[SolverMetrics]:
    """
    Get performance metrics, optionally filtered by solver.
    """
    stmt = select(SolverMetrics).limit(limit).order_by(desc(SolverMetrics.evaluated_at_utc))

    if solver_id:
        stmt = stmt.where(SolverMetrics.solver_id == solver_id)
    if dataset_tag:
        stmt = stmt.where(SolverMetrics.dataset_tag == dataset_tag)

    result = await db.execute(stmt)
    return result.scalars().all()


# --- Experiments ---


@app.get("/experiments", response_model=List[ExperimentResponse])
async def list_experiments(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> List[MetaExperiment]:
    """
    List meta-search experiments.
    """
    # Optimization: Select specific columns to avoid loading large config_json
    stmt = (
        select(
            MetaExperiment.experiment_id,
            MetaExperiment.description,
            MetaExperiment.status,
            MetaExperiment.best_solver_id,
            MetaExperiment.start_time_utc,
            MetaExperiment.end_time_utc,
        )
        .order_by(desc(MetaExperiment.start_time_utc))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.all()


@app.get("/promotions", response_model=List[PromotionRecommendationResponse])
async def list_promotion_recommendations(
    status: Optional[str] = Query(None, description="PENDING|APPROVED|REJECTED"),
    solver_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> List[PromotionRecommendation]:
    stmt = select(PromotionRecommendation).order_by(desc(PromotionRecommendation.created_at_utc)).limit(limit)
    if status:
        stmt = stmt.where(PromotionRecommendation.status == status)
    if solver_id:
        stmt = stmt.where(PromotionRecommendation.solver_id == solver_id)
    res = await db.execute(stmt)
    return res.scalars().all()


@app.post("/promotions/{recommendation_id}/approve", response_model=PromotionRecommendationResponse)
async def approve_promotion_recommendation(
    recommendation_id: str,
    reviewed_by: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> PromotionRecommendation:
    stmt = select(PromotionRecommendation).where(PromotionRecommendation.id == recommendation_id)
    res = await db.execute(stmt)
    rec = res.scalars().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Promotion recommendation not found")
    if rec.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Recommendation is {rec.status}, not PENDING")

    solver_stmt = select(Solver).where(Solver.solver_id == rec.solver_id)
    solver_res = await db.execute(solver_stmt)
    solver = solver_res.scalars().first()
    if not solver:
        raise HTTPException(status_code=404, detail="Solver not found for recommendation")

    # Apply the stage change as an explicit workflow action (PRDv2 FR 5.5.2).
    solver.stage = rec.recommended_stage
    if rec.recommended_stage in ["shadow", "paper", "limited_live", "scaled_live"]:
        solver.is_active = True
        solver.status = "active"
    if rec.recommended_stage in ["research"]:
        solver.is_active = False
        solver.status = "candidate"

    rec.status = "APPROVED"
    rec.reviewed_at_utc = datetime.now(timezone.utc)
    rec.reviewed_by = reviewed_by

    await db.commit()
    return rec


@app.post("/promotions/{recommendation_id}/reject", response_model=PromotionRecommendationResponse)
async def reject_promotion_recommendation(
    recommendation_id: str,
    reviewed_by: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> PromotionRecommendation:
    stmt = select(PromotionRecommendation).where(PromotionRecommendation.id == recommendation_id)
    res = await db.execute(stmt)
    rec = res.scalars().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Promotion recommendation not found")
    if rec.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Recommendation is {rec.status}, not PENDING")

    rec.status = "REJECTED"
    rec.reviewed_at_utc = datetime.now(timezone.utc)
    rec.reviewed_by = reviewed_by

    await db.commit()
    return rec


@app.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    k: int = Query(10, ge=1, le=50),
    ticker: Optional[str] = Query(None),
    tickers: Optional[str] = Query(None, description="Comma-separated tickers (overrides ticker)"),
    doc_type: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    model_version: Optional[str] = Query(None),
    session: Optional[str] = Query(None),
    start: Optional[str] = Query(None, description="ISO-8601 start (inclusive)"),
    end: Optional[str] = Query(None, description="ISO-8601 end (exclusive)"),
    min_premium_usd: Optional[float] = Query(
        None, ge=0, description="Filter docs with metadata.premium_usd >= this (where supported)"
    ),
    max_premium_usd: Optional[float] = Query(
        None, ge=0, description="Filter docs with metadata.premium_usd <= this (where supported)"
    ),
    _: None = Depends(require_api_key),
) -> List[Dict[str, Any]]:
    trace_id = str(uuid.uuid4())
    logger.info("RAG search request", extra={"event_type": "RAG_SEARCH", "trace_id": trace_id, "ticker": ticker})

    store = VectorStore()
    ticker_list = None
    if tickers:
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    docs = await store.search(
        q,
        k=k,
        ticker=ticker,
        tickers=ticker_list,
        doc_type=doc_type,
        rule_id=rule_id,
        model_version=model_version,
        market_session=session,
        start=start,
        end=end,
        min_premium_usd=min_premium_usd,
        max_premium_usd=max_premium_usd,
    )
    return [
        {
            "doc_id": d.doc_id,
            "source_type": d.source_type,
            "source_id": d.source_id,
            "content": d.content,
            "metadata": d.metadata_json,
            "pointers": {
                "source_type": d.source_type,
                "source_id": d.source_id,
                **((d.metadata_json or {}).get("pointers") or {}),
            },
        }
        for d in docs
    ]


def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


@app.get("/events/{event_id}")
async def get_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> Dict[str, Any]:
    stmt = select(BronzeEvent).where(BronzeEvent.event_id == event_id)
    res = await db.execute(stmt)
    ev = res.scalars().first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    return {
        "event_id": ev.event_id,
        "source": ev.source,
        "source_event_id": ev.source_event_id,
        "event_type": ev.event_type,
        "ticker": ev.ticker,
        "trading_date": str(ev.trading_date) if ev.trading_date else None,
        "session": ev.session,
        "schema_version": ev.schema_version,
        "event_ts_utc": _dt_iso(ev.event_ts_utc),
        "received_ts_utc": _dt_iso(ev.received_ts_utc),
        "payload": ev.payload,
        "ingest": ev.ingest,
    }


@app.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> Dict[str, Any]:
    stmt = select(CandidateTrade).where(CandidateTrade.candidate_id == candidate_id)
    res = await db.execute(stmt)
    cand = res.scalars().first()
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return {
        "candidate_id": cand.candidate_id,
        "ticker": cand.ticker,
        "timestamp_utc": _dt_iso(cand.timestamp_utc),
        "rule_id": cand.rule_id,
        "direction": cand.direction,
        "confidence": cand.confidence,
        "source": cand.source,
        "execution_params": cand.execution_params,
        "evidence": cand.evidence,
        "created_at_utc": _dt_iso(cand.created_at_utc),
    }


@app.get("/rollups")
async def get_rollups(
    ticker: str = Query(..., min_length=1),
    period: str = Query("5m", description="1m|5m|1h|1d"),
    start: Optional[str] = Query(None, description="ISO-8601 start (inclusive)"),
    end: Optional[str] = Query(None, description="ISO-8601 end (exclusive)"),
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> List[Dict[str, Any]]:
    from orion.shared.utils import parse_timestamptz

    start_dt = parse_timestamptz(start, strict=False) if start else None
    end_dt = parse_timestamptz(end, strict=False) if end else None

    # Optimization: Use Core-style selection to avoid full ORM object overhead
    stmt = select(
        GoldTickerRollup.ticker,
        GoldTickerRollup.period,
        GoldTickerRollup.timestamp_utc,
        GoldTickerRollup.open,
        GoldTickerRollup.high,
        GoldTickerRollup.low,
        GoldTickerRollup.close,
        GoldTickerRollup.volume,
        GoldTickerRollup.vwap,
        GoldTickerRollup.created_at_utc,
    ).where(GoldTickerRollup.ticker == ticker, GoldTickerRollup.period == period)
    if start_dt is not None:
        stmt = stmt.where(GoldTickerRollup.timestamp_utc >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(GoldTickerRollup.timestamp_utc < end_dt)
    stmt = stmt.order_by(GoldTickerRollup.timestamp_utc.asc()).limit(limit)

    res = await db.execute(stmt)
    rows = res.all()
    return [
        {
            "ticker": r.ticker,
            "period": r.period,
            "timestamp_utc": _dt_iso(r.timestamp_utc),
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "vwap": r.vwap,
            "created_at_utc": _dt_iso(r.created_at_utc),
        }
        for r in rows
    ]


@app.get("/rollups/{ticker}/{period}/{timestamp_utc}")
async def get_rollup(
    ticker: str,
    period: str,
    timestamp_utc: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> Dict[str, Any]:
    from orion.shared.utils import parse_timestamptz

    ts = parse_timestamptz(timestamp_utc, strict=True)
    stmt = select(GoldTickerRollup).where(
        GoldTickerRollup.ticker == ticker,
        GoldTickerRollup.period == period,
        GoldTickerRollup.timestamp_utc == ts,
    )
    res = await db.execute(stmt)
    r = res.scalars().first()
    if not r:
        raise HTTPException(status_code=404, detail="Rollup not found")
    return {
        "ticker": r.ticker,
        "period": r.period,
        "timestamp_utc": _dt_iso(r.timestamp_utc),
        "open": r.open,
        "high": r.high,
        "low": r.low,
        "close": r.close,
        "volume": r.volume,
        "vwap": r.vwap,
        "created_at_utc": _dt_iso(getattr(r, "created_at_utc", None)),
    }


@app.get("/flows")
async def get_flows(
    ticker: Optional[str] = Query(None),
    min_premium_usd: Optional[float] = Query(None, ge=0),
    start: Optional[str] = Query(None, description="ISO-8601 start (inclusive)"),
    end: Optional[str] = Query(None, description="ISO-8601 end (exclusive)"),
    limit: int = Query(200, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> List[Dict[str, Any]]:
    from orion.shared.utils import parse_timestamptz

    start_dt = parse_timestamptz(start, strict=False) if start else None
    end_dt = parse_timestamptz(end, strict=False) if end else None

    # Optimization: Use Core-style selection to avoid full ORM object overhead
    stmt = (
        select(
            SilverOptionFlow.event_id,
            SilverOptionFlow.source_event_id,
            SilverOptionFlow.ticker,
            SilverOptionFlow.flow_ts_utc,
            SilverOptionFlow.put_call,
            SilverOptionFlow.expiry,
            SilverOptionFlow.strike,
            SilverOptionFlow.option_price,
            SilverOptionFlow.size_contracts,
            SilverOptionFlow.premium_usd,
            SilverOptionFlow.bid,
            SilverOptionFlow.ask,
            SilverOptionFlow.underlying_price,
            SilverOptionFlow.aggressor,
            SilverOptionFlow.is_sweep,
            SilverOptionFlow.flags_json,
            SilverOptionFlow.volume_contract,
            SilverOptionFlow.open_interest,
            SilverOptionFlow.ingest,
            SilverOptionFlow.created_at_utc,
        )
        .order_by(desc(SilverOptionFlow.flow_ts_utc))
        .limit(limit)
    )
    if ticker:
        stmt = stmt.where(SilverOptionFlow.ticker == ticker)
    if start_dt is not None:
        stmt = stmt.where(SilverOptionFlow.flow_ts_utc >= start_dt)
    if end_dt is not None:
        stmt = stmt.where(SilverOptionFlow.flow_ts_utc < end_dt)
    if min_premium_usd is not None:
        stmt = stmt.where(SilverOptionFlow.premium_usd >= float(min_premium_usd))

    res = await db.execute(stmt)
    rows = res.all()
    return [
        {
            "event_id": r.event_id,
            "source_event_id": r.source_event_id,
            "ticker": r.ticker,
            "flow_ts_utc": _dt_iso(r.flow_ts_utc),
            "put_call": r.put_call,
            "expiry": r.expiry,
            "strike": r.strike,
            "option_price": r.option_price,
            "size_contracts": r.size_contracts,
            "premium_usd": r.premium_usd,
            "bid": r.bid,
            "ask": r.ask,
            "underlying_price": r.underlying_price,
            "aggressor": r.aggressor,
            "is_sweep": r.is_sweep,
            "flags_json": r.flags_json,
            "volume_contract": r.volume_contract,
            "open_interest": r.open_interest,
            "ingest": r.ingest,
            "created_at_utc": _dt_iso(r.created_at_utc),
        }
        for r in rows
    ]


# --- Dashboard ---


@app.get("/dashboard/summary", tags=["Dashboard"])
async def get_dashboard_summary(
    _: None = Depends(require_api_key),
) -> Dict[str, Any]:
    """
    Get real-time portfolio P&L summary.

    Returns current unrealized/realized P&L, drawdown, trade stats,
    and equity curve data.
    """
    from orion.core.pnl_tracker import get_pnl_tracker

    tracker = get_pnl_tracker()
    return tracker.get_portfolio_summary()


@app.get("/dashboard/positions", tags=["Dashboard"])
async def get_dashboard_positions(
    _: None = Depends(require_api_key),
) -> List[Dict[str, Any]]:
    """
    Get all open positions with P&L details.

    Returns positions sorted by absolute unrealized P&L.
    """
    from orion.core.pnl_tracker import get_pnl_tracker

    tracker = get_pnl_tracker()
    return tracker.get_position_details()


@app.get("/dashboard/sectors", tags=["Dashboard"])
async def get_dashboard_sectors(
    _: None = Depends(require_api_key),
) -> Dict[str, Dict[str, float]]:
    """
    Get sector-level P&L breakdown.

    Returns market value and unrealized P&L per sector.
    """
    from orion.core.pnl_tracker import get_pnl_tracker

    tracker = get_pnl_tracker()
    return tracker.get_sector_breakdown()


@app.get("/dashboard/alerts", tags=["Dashboard"])
async def get_dashboard_alerts(
    _: None = Depends(require_api_key),
) -> List[Dict[str, Any]]:
    """
    Get active risk alerts.

    Checks risk thresholds and returns any breaches or warnings.
    """
    from orion.core.pnl_tracker import get_pnl_tracker

    tracker = get_pnl_tracker()
    alerts = tracker.check_risk_alerts()
    return [
        {
            "alert_type": a.alert_type,
            "severity": a.severity,
            "message": a.message,
            "current_value": a.current_value,
            "threshold": a.threshold,
            "timestamp": a.timestamp.isoformat(),
        }
        for a in alerts
    ]


@app.post("/dashboard/equity", tags=["Dashboard"])
async def set_dashboard_equity(
    equity: float = Query(..., gt=0, description="Starting equity for the day"),
    _: None = Depends(require_api_key),
) -> Dict[str, Any]:
    """
    Set starting equity for P&L calculations.

    Should be called at market open with account equity.
    """
    from orion.core.pnl_tracker import get_pnl_tracker

    tracker = get_pnl_tracker()
    tracker.set_starting_equity(equity)
    return {
        "status": "ok",
        "starting_equity": equity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/dashboard/reset", tags=["Dashboard"])
async def reset_dashboard_daily(
    _: None = Depends(require_api_key),
) -> Dict[str, str]:
    """
    Reset daily P&L counters.

    Call at start of trading day to reset realized P&L and trade counts.
    """
    from orion.core.pnl_tracker import get_pnl_tracker

    tracker = get_pnl_tracker()
    tracker.reset_daily()
    return {"status": "ok", "message": "Daily counters reset"}
