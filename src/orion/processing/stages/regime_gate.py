"""
RegimeGate Pipeline Stage.

Detects multi-axis market regime and blocks trading during SHOCK conditions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from orion.analysis.regime import MultiAxisRegimeDetector, VolRegime, is_trusted_vix_source
from orion.analysis.regime_risk import RegimeRiskManager
from orion.processing.pipeline import PipelineContext, StageResult
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.db import async_session_factory
from orion.storage.models import RegimeSnapshot

logger = setup_struct_logger("orion.processing.stages.regime_gate")

# feature_enrichment writes one regime_snapshots row roughly every 5 minutes
# (REGIME_SNAPSHOT_INTERVAL in main_feature_enrichment.py). 15 minutes (3x
# that cadence) tolerates a missed write cycle before a row is treated as
# stale rather than live.
REGIME_INPUT_FRESHNESS_MINUTES = 15

# The DB read is on the per-candidate decision hot path; a value that only
# changes every ~5 minutes doesn't need a fresh query per candidate, so
# cache it for up to 60 seconds.
REGIME_INPUT_CACHE_SECONDS = 60

# Hard timeout on the read itself: main_execution awaits SignalEngine.decide()
# sequentially per candidate, so a hung TimescaleDB read here would stall the
# whole pipeline rather than degrade — same idiom as shared/liveness.py's
# publish call (never-raises is not never-blocks).
REGIME_INPUT_DB_TIMEOUT_SECONDS = 5.0

# feature_enrichment always persists the market-wide snapshot under this
# ticker (see persist_regime_snapshot's default in enrichment/heber_context.py).
REGIME_SNAPSHOT_TICKER = "SPY"


@dataclass
class _RegimeInputs:
    """Real market inputs sourced from the regime_snapshots table, if any."""

    vix: float | None
    vix_source: str | None
    vix_observed_at: datetime | None
    realized_vol: float | None
    source: str  # "regime_snapshots" when real data was found, else "none"


class RegimeGate:
    """Block trading during SHOCK regimes and compute sizing multiplier."""

    def __init__(self) -> None:
        self.multi_axis_detector = MultiAxisRegimeDetector()
        self.risk_manager = RegimeRiskManager()
        self._cached_inputs: _RegimeInputs | None = None
        self._cache_expires_at: datetime | None = None
        self._warned_inert = False
        self._proxy_shock_alerted = False

    @property
    def name(self) -> str:
        return "regime_gate"

    @staticmethod
    async def _read_latest_snapshot() -> RegimeSnapshot | None:
        async with async_session_factory() as session:
            stmt = (
                select(RegimeSnapshot)
                .where(RegimeSnapshot.ticker == REGIME_SNAPSHOT_TICKER)
                .order_by(RegimeSnapshot.ts_utc.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def _get_regime_inputs(self) -> _RegimeInputs:
        """Bounded, cached read of the latest persisted regime snapshot.

        Feeds detect() real vix/realized_vol values when a fresh, genuinely
        populated regime_snapshots row exists. Any failure (DB error, DB
        timeout, no row, a stale or future-dated row, a fresh row with no
        vix_level captured, or a vix reading whose own observation is stale)
        degrades to source="none" — never blocks trading and never crashes
        or stalls the pipeline.
        """
        now = datetime.now(UTC)
        if self._cached_inputs is not None and self._cache_expires_at is not None and now < self._cache_expires_at:
            return self._cached_inputs

        row: RegimeSnapshot | None = None
        try:
            row = await asyncio.wait_for(self._read_latest_snapshot(), timeout=REGIME_INPUT_DB_TIMEOUT_SECONDS)
        except Exception:
            logger.warning("regime_gate_input_read_failed", exc_info=True)
            row = None

        inputs = _RegimeInputs(vix=None, vix_source=None, vix_observed_at=None, realized_vol=None, source="none")
        cache_expires_at = now + timedelta(seconds=REGIME_INPUT_CACHE_SECONDS)

        if row is not None and row.vix_level is not None:
            row_ts = ensure_utc(row.ts_utc)
            age_seconds = (now - row_ts).total_seconds() if row_ts is not None else None
            # age_seconds must be non-negative too: a future-dated row (clock
            # skew or bad data) would otherwise read as "fresh forever" until
            # real time caught up to its timestamp, latching a false block.
            if age_seconds is not None and 0 <= age_seconds <= REGIME_INPUT_FRESHNESS_MINUTES * 60:
                # The row's own write time is not proof the underlying vix
                # observation is current — a live process can re-persist the
                # same stale close every ~5 minute cycle and look fresh by
                # write time alone. A missing observed_at (e.g. a row
                # written before this column existed) is treated the same
                # as stale: fail toward not trusting it.
                observed_at = ensure_utc(row.vix_observed_at) if row.vix_observed_at is not None else None
                vix_age = (now - observed_at).total_seconds() if observed_at is not None else None
                vix_is_fresh = vix_age is not None and 0 <= vix_age <= REGIME_INPUT_FRESHNESS_MINUTES * 60

                if vix_is_fresh:
                    inputs = _RegimeInputs(
                        vix=row.vix_level,
                        vix_source=row.vix_source,
                        vix_observed_at=observed_at,
                        realized_vol=row.realized_vol,
                        source="regime_snapshots",
                    )
                    # The 60s read-cache must not keep this input alive past
                    # the row's own freshness deadline, NOR past the
                    # underlying vix observation's own deadline — a row read
                    # with an observation already 14m59s old must not still
                    # be trusted 61 seconds later just because the row write
                    # itself has headroom. Cap at whichever comes first.
                    row_deadline = row_ts + timedelta(minutes=REGIME_INPUT_FRESHNESS_MINUTES)
                    cache_expires_at = min(cache_expires_at, row_deadline)
                    if observed_at is not None:  # always true here; narrows for the type checker
                        vix_deadline = observed_at + timedelta(minutes=REGIME_INPUT_FRESHNESS_MINUTES)
                        cache_expires_at = min(cache_expires_at, vix_deadline)

        if inputs.source == "none" and not self._warned_inert:
            logger.warning(
                "regime_gate_inert_no_inputs",
                extra={"event_type": "REGIME_GATE_INERT_NO_INPUTS"},
            )
            self._warned_inert = True

        self._cached_inputs = inputs
        self._cache_expires_at = cache_expires_at
        return inputs

    async def evaluate(self, ctx: PipelineContext) -> StageResult:
        candidate = ctx.candidate

        regime_inputs = await self._get_regime_inputs()

        # Detect multi-axis regime snapshot. Only override vix/realized_vol
        # when a real, fresh reading was found — otherwise this call is
        # byte-for-byte the same no-input call as before this fix.
        detect_kwargs: dict[str, Any] = {}
        if regime_inputs.source == "regime_snapshots":
            detect_kwargs["vix"] = regime_inputs.vix
            detect_kwargs["vix_source"] = regime_inputs.vix_source
            detect_kwargs["vix_observed_at"] = regime_inputs.vix_observed_at
            if regime_inputs.realized_vol is not None:
                detect_kwargs["realized_vol"] = regime_inputs.realized_vol

        regime_snapshot = self.multi_axis_detector.detect(
            ts=candidate.timestamp_utc,
            **detect_kwargs,
        )
        ctx.regime_snapshot = regime_snapshot

        # A SHOCK classification backed by an untrusted vix source (today,
        # only the VIXY-ETF-price proxy exists) never hard-blocks — see
        # should_trade() — but it must still be operator-visible instead of
        # silently sizing around a signal that may be a real shock the
        # system has no trusted way to confirm. Alert once per onset, not
        # once per candidate, to avoid spamming logs while it persists.
        proxy_would_block = regime_snapshot.vol == VolRegime.SHOCK and not is_trusted_vix_source(
            regime_snapshot.vix_source
        )
        if proxy_would_block and not self._proxy_shock_alerted:
            logger.critical(
                "proxy_would_block",
                extra={
                    "event": "proxy_would_block",
                    "vix_level": regime_snapshot.vix_level,
                    "vix_source": regime_snapshot.vix_source,
                    "vix_regime": regime_snapshot.vix_regime.value,
                },
            )
            self._proxy_shock_alerted = True
        elif not proxy_would_block:
            self._proxy_shock_alerted = False

        # Check if regime allows trading
        if not self.risk_manager.should_trade(regime_snapshot):
            return StageResult(
                action="SKIP",
                reason=f"Regime SHOCK/blocked: vol={regime_snapshot.vol.value}, vix={regime_snapshot.vix_regime.value}",
                trace={
                    "regime_blocked": True,
                    "vol_regime": regime_snapshot.vol.value,
                    "vix_regime": regime_snapshot.vix_regime.value,
                    "regime_inputs": regime_inputs.source,
                },
            )

        # Compute regime sizing multiplier for downstream use
        ctx.regime_size_multiplier = self.risk_manager.calculate_combined_multiplier(regime_snapshot)

        return StageResult(
            action="CONTINUE",
            trace={
                "regime_snapshot": {
                    "trend": regime_snapshot.trend.value,
                    "vol": regime_snapshot.vol.value,
                    "risk": regime_snapshot.risk.value,
                    "session": regime_snapshot.session.value,
                    "vix_regime": regime_snapshot.vix_regime.value,
                },
                "regime_size_multiplier": ctx.regime_size_multiplier,
                "regime_inputs": regime_inputs.source,
            },
        )
