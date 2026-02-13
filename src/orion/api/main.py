import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from orion.api.auth import require_api_key
from orion.api.deps import get_db
from orion.api.schemas import ExperimentResponse, PromotionRecommendationResponse, SolverMetricsResponse, SolverResponse
from orion.clients.heber_reader import get_heber_reader
from orion.config import system_settings
from orion.rag.vector_store import VectorStore
from orion.shared.db_utils import db_write

# Setup logger (FastAPI usually handles its own, but we can hook in ours)
from orion.shared.logger import setup_struct_logger
from orion.storage.models import BronzeEvent
from orion.storage.models_audit import AuditLog
from orion.storage.models_gold import CandidateTrade, GoldTickerRollup
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


from orion.core.errors import OrionError


@app.exception_handler(OrionError)
async def orion_exception_handler(request: Request, exc: OrionError) -> JSONResponse:
    """
    Handle OrionError with standard Empire error envelope.
    """
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
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
                run_id=system_settings.run_id,
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
    stmt = select(Solver).offset(skip).limit(limit)
    if active_only:
        stmt = stmt.where((Solver.status == "active") | ((Solver.status.is_(None)) & (Solver.is_active)))

    # Order by creation desc
    stmt = stmt.order_by(desc(Solver.created_at_utc))

    result = await db.execute(stmt)
    return result.scalars().all()


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
    stmt = select(MetaExperiment).order_by(desc(MetaExperiment.start_time_utc)).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


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

    try:
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
        rows: List[Dict[str, Any]] = []
        for d in docs:
            meta = getattr(d, "metadata_json", None) or {}
            source_type = getattr(d, "source_type", None)
            source_id = getattr(d, "source_id", None)
            rows.append(
                {
                    "doc_id": getattr(d, "doc_id", None),
                    "source_type": source_type,
                    "source_id": source_id,
                    "content": getattr(d, "content", None),
                    "metadata": meta,
                    "pointers": {
                        "source_type": source_type,
                        "source_id": source_id,
                        **(meta.get("pointers") or {}),
                    },
                }
            )
        return rows
    except Exception as e:
        logger.error(
            "RAG search failed",
            extra={"event_type": "RAG_SEARCH_ERROR", "trace_id": trace_id, "error": str(e)},
            exc_info=True,
        )
        return []


def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _first_existing_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for column in candidates:
        if column in frame.columns:
            return column
    return None


def _normalize_flow_ticker(value: Any) -> str | None:
    if value is None:
        return None
    ticker = str(value).strip().upper()
    if not ticker:
        return None
    if ":" in ticker:
        ticker = ticker.split(":")[-1]
    return ticker or None


def _normalize_put_call(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().upper()
    if token in {"C", "CALL"}:
        return "C"
    if token in {"P", "PUT"}:
        return "P"
    return token or None


def _coerce_flow_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    token = str(value).strip().lower()
    if token in {"1", "true", "t", "yes", "y"}:
        return True
    if token in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _coerce_optional_float(value: Any) -> float | None:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    return float(numeric)


def _coerce_optional_int(value: Any) -> int | None:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    return int(numeric)


def _normalize_json_field(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)


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
    _: None = Depends(require_api_key),
) -> List[Dict[str, Any]]:
    from orion.shared.utils import parse_timestamptz

    start_dt = parse_timestamptz(start, strict=False) if start else None
    end_dt = parse_timestamptz(end, strict=False) if end else None

    reader = get_heber_reader()
    asof_time = datetime.now(timezone.utc)
    symbols = [ticker] if ticker else None
    try:
        frame = await asyncio.to_thread(
            reader.read_flow,
            symbols=symbols,
            asof_time=asof_time,
            start_time=start_dt,
            min_premium=float(min_premium_usd) if min_premium_usd is not None else None,
        )
    except Exception as exc:
        logger.warning(
            "Heber flow read failed",
            extra={
                "event_type": "FLOW_READ_FAILED",
                "ticker": ticker,
                "start": start,
                "end": end,
                "error": str(exc),
            },
        )
        return []

    if frame.empty:
        return []

    ticker_col = _first_existing_column(frame, ("ticker", "symbol", "instrument_key", "underlying"))
    ts_col = _first_existing_column(frame, ("flow_ts_utc", "ts_event", "timestamp", "ts_utc"))
    premium_col = _first_existing_column(frame, ("premium_usd", "premium"))
    if ticker_col is None or ts_col is None or premium_col is None:
        return []

    work = frame.copy()
    work["_event_ts"] = pd.to_datetime(work[ts_col], utc=True, errors="coerce")
    work = work.dropna(subset=["_event_ts"])
    if work.empty:
        return []

    if ticker:
        requested = ticker.upper().strip()
        work = work[work[ticker_col].map(_normalize_flow_ticker) == requested]
    if work.empty:
        return []

    if end_dt is not None:
        end_ts = pd.Timestamp(end_dt)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")
        work = work[work["_event_ts"] < end_ts]
    if work.empty:
        return []

    if min_premium_usd is not None:
        premium_series = pd.to_numeric(work[premium_col], errors="coerce")
        work = work[premium_series >= float(min_premium_usd)]
    if work.empty:
        return []

    event_id_col = _first_existing_column(work, ("event_id", "id"))
    source_event_id_col = _first_existing_column(work, ("source_event_id", "source_id"))
    put_call_col = _first_existing_column(work, ("put_call", "call_put"))
    expiry_col = _first_existing_column(work, ("expiry", "expiration"))
    strike_col = _first_existing_column(work, ("strike",))
    option_price_col = _first_existing_column(work, ("option_price", "price"))
    size_contracts_col = _first_existing_column(work, ("size_contracts", "size", "contracts"))
    bid_col = _first_existing_column(work, ("bid", "bid_price"))
    ask_col = _first_existing_column(work, ("ask", "ask_price"))
    underlying_col = _first_existing_column(work, ("underlying_price", "underlying"))
    aggressor_col = _first_existing_column(work, ("aggressor", "side", "aggressor_ind"))
    is_sweep_col = _first_existing_column(work, ("is_sweep", "sweep"))
    flags_col = _first_existing_column(work, ("flags_json", "flags"))
    volume_contract_col = _first_existing_column(work, ("volume_contract", "volume"))
    open_interest_col = _first_existing_column(work, ("open_interest", "oi"))
    ingest_col = _first_existing_column(work, ("ingest",))
    created_col = _first_existing_column(work, ("created_at_utc", "created_at", "ts_available"))

    work = work.sort_values("_event_ts", ascending=False).head(limit)
    rows: list[dict[str, Any]] = []
    for idx, row in work.iterrows():
        event_id = str(row.get(event_id_col)).strip() if event_id_col and row.get(event_id_col) else f"heber_flow_{idx}"
        source_event_id = (
            str(row.get(source_event_id_col)).strip() if source_event_id_col and row.get(source_event_id_col) else None
        )
        normalized_ticker = _normalize_flow_ticker(row.get(ticker_col))
        if normalized_ticker is None:
            continue
        created_at = (
            pd.to_datetime(row.get(created_col), utc=True, errors="coerce") if created_col else pd.NaT  # type: ignore[arg-type]
        )
        created_at_utc = created_at.to_pydatetime() if not pd.isna(created_at) else None

        rows.append(
            {
                "event_id": event_id,
                "source_event_id": source_event_id,
                "ticker": normalized_ticker,
                "flow_ts_utc": _dt_iso(row["_event_ts"].to_pydatetime()),
                "put_call": _normalize_put_call(row.get(put_call_col)) if put_call_col else None,
                "expiry": str(row.get(expiry_col)) if expiry_col and row.get(expiry_col) is not None else None,
                "strike": _coerce_optional_float(row.get(strike_col)) if strike_col else None,
                "option_price": _coerce_optional_float(row.get(option_price_col)) if option_price_col else None,
                "size_contracts": _coerce_optional_int(row.get(size_contracts_col)) if size_contracts_col else None,
                "premium_usd": _coerce_optional_float(row.get(premium_col)),
                "bid": _coerce_optional_float(row.get(bid_col)) if bid_col else None,
                "ask": _coerce_optional_float(row.get(ask_col)) if ask_col else None,
                "underlying_price": _coerce_optional_float(row.get(underlying_col)) if underlying_col else None,
                "aggressor": str(row.get(aggressor_col)).strip().upper()
                if aggressor_col and row.get(aggressor_col) is not None
                else None,
                "is_sweep": _coerce_flow_bool(row.get(is_sweep_col)) if is_sweep_col else None,
                "flags_json": _normalize_json_field(row.get(flags_col)) if flags_col else None,
                "volume_contract": _coerce_optional_float(row.get(volume_contract_col))
                if volume_contract_col
                else None,
                "open_interest": _coerce_optional_float(row.get(open_interest_col)) if open_interest_col else None,
                "ingest": _normalize_json_field(row.get(ingest_col)) if ingest_col else None,
                "created_at_utc": _dt_iso(created_at_utc),
            }
        )

    return rows


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
