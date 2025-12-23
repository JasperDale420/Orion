import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pandas as pd
from orion.processing.label_engine import TripleBarrierLabeling
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateLabel, CandidateTrade, GoldTickerRollup
from sqlalchemy import select

logger = logging.getLogger("orion.jobs.label_job")


class LabelingJob:
    """
    Periodic job to label older candidates once their outcome is known.
    """

    def __init__(self):
        self.labeler = TripleBarrierLabeling(upper_barrier=0.015, lower_barrier=0.010, time_barrier_bars=60)
        self.forward_horizons_min = self._parse_forward_horizons()

    @staticmethod
    def _parse_forward_horizons() -> List[int]:
        """
        PRD 6.3: forward returns (1m/5m/1h/1d/3d etc; configurable).
        Config via env `ORION_LABEL_FORWARD_HORIZONS_MIN` as comma-separated minutes.
        """
        import os

        raw = os.getenv("ORION_LABEL_FORWARD_HORIZONS_MIN", "1,5,60,390,1170")
        out: List[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                val = int(part)
            except ValueError:
                continue
            if val > 0:
                out.append(val)
        # Deterministic order
        return sorted(set(out))

    @staticmethod
    def _compute_forward_returns(
        close_series: pd.Series, entry_ts: pd.Timestamp, horizons_min: List[int]
    ) -> Dict[str, float | None]:
        """
        Computes forward returns for the given horizons from a close price series.
        Returns dict keyed by '<minutes>m' (e.g., '5m') with float returns or None if unavailable.
        """
        if close_series.empty:
            return {f"{h}m": None for h in horizons_min}

        prices = close_series.sort_index()
        if entry_ts not in prices.index:
            idx = prices.index.get_indexer([entry_ts], method="bfill")
            if idx is None or len(idx) == 0 or idx[0] < 0:
                return {f"{h}m": None for h in horizons_min}
            entry_ts = prices.index[idx[0]]

        p0 = float(prices.loc[entry_ts])
        if p0 == 0:
            return {f"{h}m": None for h in horizons_min}

        out: Dict[str, float | None] = {}
        for h in horizons_min:
            target_ts = entry_ts + pd.Timedelta(minutes=h)
            idx = prices.index.get_indexer([target_ts], method="bfill")
            if idx is None or len(idx) == 0 or idx[0] < 0:
                out[f"{h}m"] = None
                continue
            ts_h = prices.index[idx[0]]
            try:
                ph = float(prices.loc[ts_h])
                out[f"{h}m"] = (ph - p0) / p0
            except Exception:
                out[f"{h}m"] = None
        return out

    async def run_once(self):
        """
        Process batch of unlabeled candidates.
        """
        async with async_session_factory() as session:
            # 1. potential candidates: > 60 mins old, not in candidate_labels
            # Simplified for v1: Just grab some recent candidates and check if label exists
            # Ideally: Left join where label is null
            cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=65)

            # Select candidates created before cutoff
            # That don't have a label
            # Subquery exists?

            stmt = (
                select(CandidateTrade)
                .outerjoin(CandidateLabel, CandidateTrade.candidate_id == CandidateLabel.candidate_id)
                .where(CandidateTrade.timestamp_utc < cutoff_time)
                .where(CandidateLabel.candidate_id is None)
                .limit(50)
            )

            result = await session.execute(stmt)
            candidates = result.scalars().all()

            if not candidates:
                logger.info("No candidates pending labeling.")
                return

            logger.info(f"Labeling {len(candidates)} candidates...")

            for cand in candidates:
                # 2. Fetch Price Data
                # Need bars from cand.timestamp_utc to +65 mins
                start_ts = cand.timestamp_utc
                end_ts = start_ts + timedelta(minutes=70)

                # Fetch 1m bars? GoldTickerRollup usually 1m if period='1m'
                # Assuming GoldTickerRollup has period='1m'
                bar_stmt = (
                    select(GoldTickerRollup)
                    .where(GoldTickerRollup.ticker == cand.ticker)
                    .where(GoldTickerRollup.period == "1m")  # Assuming '1m' string
                    .where(GoldTickerRollup.timestamp_utc >= start_ts)
                    .where(GoldTickerRollup.timestamp_utc <= end_ts)
                    .order_by(GoldTickerRollup.timestamp_utc.asc())
                )

                bar_res = await session.execute(bar_stmt)
                bars = bar_res.scalars().all()

                if not bars:
                    # Data missing? Skip
                    continue

                # Convert to Series
                # TripleBarrierLabeling.compute_labels expects Series with DatetimeIndex
                # and 'close'

                df = pd.DataFrame([{"ts": b.timestamp_utc, "close": b.close} for b in bars])
                if df.empty:
                    continue

                df.set_index("ts", inplace=True)
                df.sort_index(inplace=True)

                # 3. Compute Label
                # We are labeling a single event here
                outcome_df = self.labeler.compute_labels(df["close"], pd.DatetimeIndex([cand.timestamp_utc]))

                if not outcome_df.empty:
                    row = outcome_df.iloc[0]
                    entry_ts = pd.Timestamp(cand.timestamp_utc)
                    entry_ts = entry_ts.tz_convert("UTC") if entry_ts.tzinfo else entry_ts.tz_localize("UTC")
                    fwd = self._compute_forward_returns(df["close"], entry_ts, self.forward_horizons_min)
                    # Persist
                    lbl = CandidateLabel(
                        candidate_id=cand.candidate_id,
                        label=float(row["label"]),
                        ret=float(row["ret"]),
                        barrier_hit_ts=row["barrier_hit_ts"].to_pydatetime(),
                        time_to_hit_seconds=(
                            float(row.get("time_to_hit_seconds"))
                            if row.get("time_to_hit_seconds") is not None
                            else None
                        ),
                        mfe=(float(row.get("mfe")) if row.get("mfe") is not None else None),
                        mae=(float(row.get("mae")) if row.get("mae") is not None else None),
                    )
                    session.add(lbl)
                    logger.info(f"Labeled {cand.ticker}: {row['label']} (Ret: {row['ret']:.4f})")

                    # PRD labels_event: persist forward returns + triple-barrier fields
                    try:
                        from orion.storage.models_gold import LabelEvent

                        le = LabelEvent(
                            candidate_id=cand.candidate_id,
                            ticker=cand.ticker,
                            event_ts_utc=cand.timestamp_utc,
                            forward_returns=fwd,
                            label=float(row["label"]),
                            ret=float(row["ret"]),
                            barrier_hit_ts=row["barrier_hit_ts"].to_pydatetime(),
                            time_to_hit_seconds=(
                                float(row.get("time_to_hit_seconds"))
                                if row.get("time_to_hit_seconds") is not None
                                else None
                            ),
                            mfe=(float(row.get("mfe")) if row.get("mfe") is not None else None),
                            mae=(float(row.get("mae")) if row.get("mae") is not None else None),
                            label_config={
                                "type": "triple_barrier",
                                "upper_barrier": float(self.labeler.upper_barrier),
                                "lower_barrier": float(self.labeler.lower_barrier),
                                "time_barrier_bars": int(self.labeler.time_barrier_bars),
                                "forward_horizons_min": list(self.forward_horizons_min),
                                "price_source": "gold_ticker_rollup:1m",
                            },
                        )
                        await session.merge(le)
                    except Exception as e:
                        logger.warning(f"Failed to persist labels_event for {cand.candidate_id}: {e}")

            await session.commit()
            logger.info("Batch labeling complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    job = LabelingJob()
    asyncio.run(job.run_once())
