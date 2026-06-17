"""Heber data reader for Orion.

This adapter follows Heber's supported access model:
- Catalog metadata and health over HTTP (`/health`, `/api/v1/*`)
- Silver/Gold data reads from Heber parquet layout on disk

It intentionally avoids unsupported endpoints like `/silver/read` and `/gold/read`.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from orion.config import system_settings
from orion.shared.dataframe_utils import first_existing_column

logger = structlog.get_logger(__name__)

_SILVER_BARS_DATASET = "bars"
_SILVER_FLOW_DATASET = "flow_alerts"
_SILVER_DARKPOOL_DATASET = "darkpool"
_SILVER_DARKPOOL_DATASET_ALIASES = ("darkpool", "darkpool_trades")
_SILVER_MARKET_TIDE_DATASET = "market_tide"
_SILVER_GREEK_EXPOSURE_DATASET = "greek_exposure"
_SILVER_MAX_PAIN_DATASET = "max_pain"
_SILVER_IV_RANK_DATASET = "iv_rank"
_SUPPORTED_BAR_TIMEFRAMES = {"1m"}

_GOLD_EMPTY_DATASET_TTL_SECONDS = 300.0


class HeberReader:
    """Read-only client for Heber datasets used by Orion."""

    # Process-wide negative cache for gold datasets that returned empty.
    # Keyed by dataset name → monotonic-clock expiry. Entry skips the
    # full path walk + ParquetDataset open for ~3-second savings per
    # call when the dataset is genuinely empty upstream.
    _gold_empty_dataset_cache: dict[str, float] = {}

    def __init__(
        self,
        catalog_url: str | None = None,
        data_root: str | Path | None = None,
        http_client: httpx.Client | None = None,
        darkpool_dataset: str | None = None,
    ):
        self.catalog_url = catalog_url or system_settings.heber_catalog_url
        self.data_root = Path(data_root) if data_root is not None else Path(system_settings.heber_data_root)
        self._client = http_client
        preferred_darkpool_dataset = darkpool_dataset or _SILVER_DARKPOOL_DATASET
        self._silver_darkpool_datasets = self._resolve_dataset_alias_order(
            preferred=preferred_darkpool_dataset,
            aliases=_SILVER_DARKPOOL_DATASET_ALIASES,
        )

    @property
    def client(self) -> httpx.Client:
        """Lazy HTTP client initialization for Catalog API calls."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.catalog_url,
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> HeberReader:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def health_check(self) -> bool:
        """Check Heber catalog API health via supported endpoint."""
        errors: list[str] = []
        for path in (self._catalog_health_url(), self._catalog_api_url("health")):
            try:
                response = self.client.get(path)
                if response.status_code == 200:
                    return True
            except Exception as exc:
                errors.append(str(exc))

        if errors:
            logger.warning("heber_health_check_failed", error=" | ".join(errors))
        return False

    def list_datasets(self, layer: str | None = None) -> list[dict[str, Any]]:
        """List datasets from Heber catalog (`/datasets`)."""
        params: dict[str, str] = {}
        if layer:
            params["layer"] = layer

        response = self.client.get(self._catalog_api_url("datasets"), params=params)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        return []

    def _catalog_origin(self) -> str:
        base = self.client.base_url
        origin = f"{base.scheme}://{base.host}"
        if base.port is not None:
            is_default_port = (base.scheme == "http" and base.port == 80) or (
                base.scheme == "https" and base.port == 443
            )
            if not is_default_port:
                origin = f"{origin}:{base.port}"
        return origin

    def _catalog_health_url(self) -> str:
        return f"{self._catalog_origin()}/health"

    def _catalog_api_url(self, endpoint: str) -> str:
        normalized_endpoint = endpoint.lstrip("/")
        return f"{self._catalog_origin()}/api/v1/{normalized_endpoint}"

    def read_bars(
        self,
        symbols: list[str],
        asof_time: datetime,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        timeframe: str = "1m",
    ) -> pd.DataFrame:
        """Read bars from Heber Silver (`feed=bars`) with as-of filtering.

        Orion currently supports only canonical 1-minute bars.
        Unsupported timeframes fail fast to prevent silent granularity mismatches.
        """
        normalized_timeframe = timeframe.strip().lower()
        if normalized_timeframe not in _SUPPORTED_BAR_TIMEFRAMES:
            raise ValueError(
                f"Unsupported bars timeframe '{timeframe}'. Supported values: {sorted(_SUPPORTED_BAR_TIMEFRAMES)}"
            )

        instrument_keys = self._to_instrument_keys(symbols)
        df = self._read_silver_dataset(
            dataset=_SILVER_BARS_DATASET,
            instrument_keys=instrument_keys,
            start_time=start_time,
            end_time=end_time,
            asof_time=asof_time,
        )

        if df.empty:
            return df

        needs_ts_event = "bar_start_ts" in df.columns and "ts_event" not in df.columns
        needs_symbol = "instrument_key" in df.columns and "symbol" not in df.columns

        if needs_ts_event or needs_symbol:
            df = df.copy()
            if needs_ts_event:
                df["ts_event"] = pd.to_datetime(df["bar_start_ts"], utc=True, errors="coerce")
            if needs_symbol:
                df["symbol"] = df["instrument_key"].astype(str).str.split(":").str[-1]

        return df

    def read_flow(
        self,
        symbols: list[str] | None = None,
        asof_time: datetime | None = None,
        start_time: datetime | None = None,
        min_premium: float | None = None,
    ) -> pd.DataFrame:
        """Read options flow from Heber Silver (`feed=flow_alerts`)."""
        instrument_keys = self._to_instrument_keys(symbols) if symbols else None

        df = self._read_silver_dataset(
            dataset=_SILVER_FLOW_DATASET,
            instrument_keys=instrument_keys,
            start_time=start_time,
            end_time=None,
            asof_time=asof_time,
        )

        if df.empty or min_premium is None:
            return df

        premium_column = self._pick_first_existing_column(df, ["premium", "premium_usd"])
        if premium_column is None:
            return df

        return df[df[premium_column] >= min_premium]

    def read_darkpool(
        self,
        symbols: list[str] | None = None,
        asof_time: datetime | None = None,
        start_time: datetime | None = None,
    ) -> pd.DataFrame:
        """Read darkpool prints from Heber Silver (`feed=darkpool`)."""
        instrument_keys = self._to_instrument_keys(symbols) if symbols else None
        for dataset in self._silver_darkpool_datasets:
            df = self._read_silver_dataset(
                dataset=dataset,
                instrument_keys=instrument_keys,
                start_time=start_time,
                end_time=None,
                asof_time=asof_time,
            )
            if not df.empty:
                return df
        return pd.DataFrame()

    def read_market_tide(
        self,
        asof_time: datetime | None = None,
        start_time: datetime | None = None,
    ) -> pd.DataFrame:
        """Read market tide aggregates from Heber Silver (`feed=market_tide`)."""
        return self._read_silver_dataset(
            dataset=_SILVER_MARKET_TIDE_DATASET,
            instrument_keys=None,
            start_time=start_time,
            end_time=None,
            asof_time=asof_time,
        )

    def read_greek_exposure(
        self,
        symbols: list[str] | None = None,
        asof_time: datetime | None = None,
        start_time: datetime | None = None,
    ) -> pd.DataFrame:
        """Read greek exposure snapshots from Heber Silver (`feed=greek_exposure`)."""
        instrument_keys = self._to_instrument_keys(symbols) if symbols else None

        return self._read_silver_dataset(
            dataset=_SILVER_GREEK_EXPOSURE_DATASET,
            instrument_keys=instrument_keys,
            start_time=start_time,
            end_time=None,
            asof_time=asof_time,
        )

    def read_max_pain(
        self,
        symbols: list[str] | None = None,
        asof_time: datetime | None = None,
        start_time: datetime | None = None,
    ) -> pd.DataFrame:
        """Read max pain snapshots from Heber Silver (`feed=max_pain`)."""
        instrument_keys = self._to_instrument_keys(symbols) if symbols else None

        return self._read_silver_dataset(
            dataset=_SILVER_MAX_PAIN_DATASET,
            instrument_keys=instrument_keys,
            start_time=start_time,
            end_time=None,
            asof_time=asof_time,
        )

    def read_iv_rank(
        self,
        symbols: list[str] | None = None,
        asof_time: datetime | None = None,
        start_time: datetime | None = None,
    ) -> pd.DataFrame:
        """Read IV rank snapshots from Heber Silver (`feed=iv_rank`)."""
        instrument_keys = self._to_instrument_keys(symbols) if symbols else None

        return self._read_silver_dataset(
            dataset=_SILVER_IV_RANK_DATASET,
            instrument_keys=instrument_keys,
            start_time=start_time,
            end_time=None,
            asof_time=asof_time,
        )

    def read_recent_equity_symbols(
        self,
        asof_time: datetime,
        start_time: datetime | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Extract unique equity ticker symbols from recent bars data.

        Useful for discovering what instruments are actively being ingested
        without requiring a specific symbol list.
        """
        df = self._read_silver_dataset(
            dataset=_SILVER_BARS_DATASET,
            instrument_keys=None,
            start_time=start_time,
            end_time=None,
            asof_time=asof_time,
        )
        if df.empty:
            return []

        ik_col = self._pick_first_existing_column(df, ["instrument_key"])
        if ik_col is None:
            return []

        keys = df[ik_col].dropna().astype(str)
        equity_keys = keys[keys.str.startswith("equity:")]
        symbols = equity_keys.str.split(":").str[-1].str.upper().str.strip()
        symbols = symbols[symbols != ""]
        if symbols.empty:
            return []

        counts = symbols.value_counts()
        return [str(symbol) for symbol in counts.head(limit).index.tolist()]

    def read_gold_features(
        self,
        dataset: str,
        asof_time: datetime,
        symbols: list[str] | None = None,
        lookback_days: int | None = None,
    ) -> pd.DataFrame:
        """Read Gold features/labels from Heber parquet layout.

        When ``lookback_days`` is set, only ``dt=`` partitions on or after
        ``asof_time.date() - lookback_days`` are scanned, skipping footer reads
        over the full dataset history (which grows unbounded as Gold accumulates
        and was aging live candidates past ``max_data_lag_seconds``). Default
        (``None``) reads all partitions — required by training/backfill callers
        that consume full history.
        """
        # Negative cache: skip the full path walk and ParquetDataset open
        # for datasets we just confirmed are empty. Empty source data
        # (Gold-builder upstream gap) was costing ~3s per call across 6
        # known-empty datasets per ML prefilter pass — sufficient to
        # produce 'Data Lag' SKIPs by aging candidates past 600s.
        cache_expiry = self._gold_empty_dataset_cache.get(dataset)
        if cache_expiry is not None and cache_expiry > time.monotonic():
            return pd.DataFrame()

        min_dt = (asof_time.date() - timedelta(days=lookback_days)) if lookback_days is not None else None

        instrument_keys = self._to_instrument_keys(symbols) if symbols else None
        filters: list[tuple[str, str, Any]] = []
        if instrument_keys:
            filters.append(("instrument_key", "in", instrument_keys))

        candidate_paths = self._gold_dataset_candidate_paths(dataset)
        frames, paths_checked = self._read_gold_frames(candidate_paths, filters, min_dt)

        if not frames and min_dt is not None:
            # Widen-if-empty: no rows in the lookback window for this symbol. Its
            # latest feature predates the window — a low-cadence or stalled
            # dataset (e.g. base rates updated weekly). Fall back to a full read
            # so we still return its most recent row instead of dropping it to
            # None. These datasets are small, so the fallback read is cheap; the
            # speedup is kept for high-cadence datasets, whose window is non-empty.
            frames, paths_checked = self._read_gold_frames(candidate_paths, filters, None)

        if not frames:
            # By here a full read has always run (min_dt was None, or we widened),
            # so an empty result means the dataset is genuinely empty upstream —
            # safe to negative-cache to skip the full-history walk next time.
            self._gold_empty_dataset_cache[dataset] = time.monotonic() + _GOLD_EMPTY_DATASET_TTL_SECONDS
            logger.warning(
                "gold_dataset_empty",
                dataset=dataset,
                paths_checked=paths_checked,
                data_root=str(self.data_root),
                data_root_exists=self.data_root.exists(),
                gold_dir_exists=(self.data_root / "gold").exists(),
                negative_cache_ttl_seconds=_GOLD_EMPTY_DATASET_TTL_SECONDS,
            )
            return pd.DataFrame()

        if len(frames) == 1:
            df = frames[0]
        else:
            df = pd.concat(frames, ignore_index=True).drop_duplicates()

        if df.empty:
            return df

        return self._apply_asof_filter(df, asof_time)

    def _read_gold_frames(
        self,
        candidate_paths: tuple[Path, ...],
        filters: list[tuple[str, str, Any]],
        min_dt: date | None,
    ) -> tuple[list[pd.DataFrame], list[str]]:
        """Read each candidate gold path, pruning to ``min_dt`` when set.

        Returns the non-empty frames and a per-path found/missing audit trail.
        """
        frames: list[pd.DataFrame] = []
        paths_checked: list[str] = []
        for gold_path in candidate_paths:
            if not gold_path.exists():
                paths_checked.append(f"{gold_path} (missing)")
                continue
            paths_checked.append(f"{gold_path} (found)")
            frame = self._read_parquet(gold_path, filters=filters, min_dt=min_dt)
            if frame.empty:
                continue
            frames.append(frame)
        return frames, paths_checked

    def _gold_dataset_candidate_paths(self, dataset: str) -> tuple[Path, ...]:
        """Resolve supported Heber gold path variants for a dataset."""
        canonical = self.data_root / "gold" / f"dataset={dataset}"
        nested_watch = self.data_root / "gold" / "labels_alert_barriers" / f"dataset={dataset}"
        if canonical == nested_watch:
            return (canonical,)
        return (canonical, nested_watch)

    def _read_silver_dataset(
        self,
        dataset: str,
        instrument_keys: list[str] | None,
        start_time: datetime | None,
        end_time: datetime | None,
        asof_time: datetime | None,
    ) -> pd.DataFrame:
        silver_path = self.data_root / "silver" / f"feed={dataset}"
        if not silver_path.exists():
            return pd.DataFrame()

        filters: list[tuple[str, str, Any]] = []
        if instrument_keys:
            filters.append(("instrument_key", "in", instrument_keys))

        df = self._read_parquet(silver_path, filters=filters)
        if df.empty:
            return df

        df = self._apply_time_range_filter(df, start_time=start_time, end_time=end_time)

        if asof_time is not None:
            df = self._apply_asof_filter(df, asof_time)

        return df

    @staticmethod
    def _to_instrument_keys(symbols: list[str] | None) -> list[str]:
        if not symbols:
            return []
        return [f"equity:{symbol.upper()}" for symbol in symbols if symbol]

    _pick_first_existing_column = staticmethod(first_existing_column)

    @staticmethod
    def _resolve_dataset_alias_order(preferred: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
        ordered: list[str] = []
        for dataset in (preferred, *aliases):
            name = dataset.strip()
            if name and name not in ordered:
                ordered.append(name)
        return tuple(ordered)

    @staticmethod
    def _to_utc_timestamp(value: datetime) -> pd.Timestamp:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize("UTC")
        return ts.tz_convert("UTC")

    def _apply_asof_filter(self, df: pd.DataFrame, asof_time: datetime) -> pd.DataFrame:
        if "ts_available" not in df.columns:
            return df

        asof_ts = self._to_utc_timestamp(asof_time)
        available = pd.to_datetime(df["ts_available"], utc=True, errors="coerce")
        return cast(pd.DataFrame, df[available <= asof_ts])

    def _apply_time_range_filter(
        self,
        df: pd.DataFrame,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> pd.DataFrame:
        if start_time is None and end_time is None:
            return df

        time_column = self._pick_first_existing_column(
            df,
            ["ts_event", "bar_start_ts", "flow_ts_utc", "dark_ts_utc", "ts_utc"],
        )
        if time_column is None:
            return df

        series = pd.to_datetime(df[time_column], utc=True, errors="coerce")
        mask = pd.Series(True, index=df.index)

        if start_time is not None:
            mask = mask & (series >= self._to_utc_timestamp(start_time))
        if end_time is not None:
            mask = mask & (series <= self._to_utc_timestamp(end_time))

        return df[mask]

    def _read_parquet(
        self,
        path: Path,
        columns: list[str] | None = None,
        filters: list[tuple[str, str, Any]] | None = None,
        min_dt: date | None = None,
    ) -> pd.DataFrame:
        try:
            # Heber paths are hive-partitioned, and some partition keys also exist in parquet
            # columns (for example `instrument_type`). Disable partition discovery to avoid
            # Arrow schema merge conflicts (`string` vs `dictionary`).
            table = self._read_table(path=path, columns=columns, filters=filters, partitioning=None, min_dt=min_dt)
            # use_threads=False: keep the Arrow->pandas conversion single-threaded
            # too. to_pandas() defaults to spawning the same Arrow CPU threadpool
            # that aborted the process on 2026-06-02; these frames are small.
            return cast(pd.DataFrame, table.to_pandas(use_threads=False))
        except Exception as exc:
            if (
                self._is_corrupt_parquet_error(exc)
                or self._is_schema_merge_parquet_error(exc)
                or isinstance(exc, OSError)
            ):
                logger.warning(
                    "heber_reader_filewise_fallback",
                    path=str(path),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return self._read_parquet_filewise(path=path, columns=columns, filters=filters, min_dt=min_dt)
            logger.error(
                "heber_read_failed",
                path=str(path),
                error=str(exc),
            )
            return pd.DataFrame()

    def _read_table(
        self,
        path: Path,
        columns: list[str] | None,
        filters: list[tuple[str, str, Any]] | None,
        partitioning: str | None,
        min_dt: date | None = None,
    ) -> Any:
        source: Path | list[str] = path
        if path.is_dir():
            # Pre-filter to skip macOS ._ sidecar files that cause EPERM errors
            # and trigger noisy filewise fallback warnings, and (when min_dt is
            # set) to prune dt= partitions older than the lookback window so we
            # never open footers for irrelevant history.
            valid_files = [str(f) for f in self._partition_parquet_files(path, min_dt)]
            if valid_files:
                source = valid_files
            elif min_dt is not None:
                # No in-window partitions: return empty rather than re-walking
                # the full directory (which would defeat the pruning).
                return pa.table({})
        try:
            # use_threads=False AND pre_buffer=False: do NOT spin up any of
            # Arrow's C++ thread pools for these reads — neither the CPU pool
            # (use_threads) nor the background I/O prefetch pool (pre_buffer).
            # On 2026-06-02 the execution service took two SIGABRTs (Abort
            # trap: 6) — a PyArrow worker thread (arrow::internal::ThreadPool)
            # hit an unhandled C++ exception that escaped to
            # std::terminate()->abort(), crashing the whole process. The read is
            # invoked from inside an asyncio.to_thread executor thread, and
            # spinning a detached Arrow threadpool from there is what aborted.
            # These reads are small (single-symbol/recent rows) so single-thread
            # is plenty; this removes the abort path entirely.
            return pq.read_table(
                source,
                columns=columns,
                filters=filters if filters else None,
                partitioning=partitioning,
                use_threads=False,
                pre_buffer=False,
            )
        except Exception as exc:
            if self._is_corrupt_parquet_error(exc):
                raise
            if filters:
                logger.warning(
                    "heber_reader_filter_fallback",
                    path=str(path),
                    error=str(exc),
                )
                # Reuse the pruned/sidecar-filtered source so the fallback keeps
                # the same partition window as the primary read.
                return pq.read_table(
                    source,
                    columns=columns,
                    partitioning=partitioning,
                    use_threads=False,
                    pre_buffer=False,
                )
            raise

    def _partition_parquet_files(self, path: Path, min_dt: date | None) -> list[Path]:
        """List a directory's parquet files, skipping ._ sidecars and (when
        ``min_dt`` is set) dt= partitions older than the lookback window."""
        return [
            f
            for f in sorted(path.rglob("*.parquet"))
            if not f.name.startswith("._") and (min_dt is None or self._partition_dt_within_window(f, min_dt))
        ]

    @staticmethod
    def _partition_dt_within_window(file_path: Path, min_dt: date) -> bool:
        """True if the file's hive ``dt=`` partition is on/after ``min_dt``.

        Files with no parseable ``dt=`` partition are kept — never silently drop
        data that cannot be classified by date.
        """
        for part in file_path.parts:
            if part.startswith("dt="):
                try:
                    return date.fromisoformat(part[3:]) >= min_dt
                except ValueError:
                    return True
        return True

    def _read_parquet_filewise(
        self,
        path: Path,
        columns: list[str] | None,
        filters: list[tuple[str, str, Any]] | None,
        min_dt: date | None = None,
    ) -> pd.DataFrame:
        parquet_files = self._partition_parquet_files(path, min_dt)
        if not parquet_files:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        skipped_count = 0
        for parquet_file in parquet_files:
            try:
                table = self._read_table(path=parquet_file, columns=columns, filters=filters, partitioning=None)
                # use_threads=False: single-threaded Arrow->pandas conversion, same
                # SIGABRT-avoidance rationale as the primary _read_parquet path.
                frame = cast(pd.DataFrame, table.to_pandas(use_threads=False))
                if not frame.empty:
                    frames.append(frame)
            except Exception as file_exc:
                skipped_count += 1
                logger.warning(
                    "heber_reader_skip_corrupt_file",
                    path=str(parquet_file),
                    error=str(file_exc),
                    error_type=type(file_exc).__name__,
                )

        if skipped_count:
            logger.warning(
                "heber_reader_filewise_summary",
                dataset_path=str(path),
                total_files=len(parquet_files),
                skipped_files=skipped_count,
                loaded_files=len(frames),
            )

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _is_corrupt_parquet_error(exc: Exception) -> bool:
        exc_type_name = type(exc).__name__
        if exc_type_name in ("ArrowInvalid", "ArrowIOError"):
            return True
        message = str(exc).lower()
        return (
            "parquet magic bytes not found in footer" in message
            or "could not read schema from" in message
            or "is this a 'parquet' file?" in message
            or "couldn't deserialize thrift" in message
            or "not a parquet file" in message
            or "corrupted" in message
            or ("error creating dataset" in message and "could not open parquet input source" in message)
        )

    @staticmethod
    def _is_schema_merge_parquet_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            ("unsupported cast from" in message and "to null" in message and "cast_null" in message)
            or ("could not merge schemas" in message)
            or ("unable to merge" in message and "incompatible types" in message)
        )


@lru_cache
def get_heber_reader() -> HeberReader:
    """Get singleton HeberReader instance."""
    return HeberReader()
