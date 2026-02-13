"""Heber data reader for Orion.

This adapter follows Heber's supported access model:
- Catalog metadata and health over HTTP (`/health`, `/api/v1/*`)
- Silver/Gold data reads from Heber parquet layout on disk

It intentionally avoids unsupported endpoints like `/silver/read` and `/gold/read`.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import httpx
import pandas as pd
import pyarrow.parquet as pq
import structlog

from orion.config import system_settings

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


class HeberReader:
    """Read-only client for Heber datasets used by Orion."""

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

        if "bar_start_ts" in df.columns and "ts_event" not in df.columns:
            df = df.copy()
            df["ts_event"] = pd.to_datetime(df["bar_start_ts"], utc=True, errors="coerce")

        if "instrument_key" in df.columns and "symbol" not in df.columns:
            df = df.copy()
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

    def read_gold_features(
        self,
        dataset: str,
        asof_time: datetime,
        symbols: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read Gold features/labels from Heber parquet layout."""
        instrument_keys = self._to_instrument_keys(symbols) if symbols else None
        filters: list[tuple[str, str, Any]] = []
        if instrument_keys:
            filters.append(("instrument_key", "in", instrument_keys))

        frames: list[pd.DataFrame] = []
        for gold_path in self._gold_dataset_candidate_paths(dataset):
            if not gold_path.exists():
                continue
            frame = self._read_parquet(gold_path, filters=filters)
            if frame.empty:
                continue
            frames.append(frame)

        if not frames:
            return pd.DataFrame()

        if len(frames) == 1:
            df = frames[0]
        else:
            df = pd.concat(frames, ignore_index=True).drop_duplicates()

        if df.empty:
            return df

        return self._apply_asof_filter(df, asof_time)

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

    @staticmethod
    def _pick_first_existing_column(df: pd.DataFrame, columns: list[str]) -> str | None:
        for column in columns:
            if column in df.columns:
                return column
        return None

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
    ) -> pd.DataFrame:
        try:
            # Heber paths are hive-partitioned, and some partition keys also exist in parquet
            # columns (for example `instrument_type`). Disable partition discovery to avoid
            # Arrow schema merge conflicts (`string` vs `dictionary`).
            table = self._read_table(path=path, columns=columns, filters=filters, partitioning=None)
            return cast(pd.DataFrame, table.to_pandas())
        except Exception as exc:
            if self._is_corrupt_parquet_error(exc) or self._is_schema_merge_parquet_error(exc):
                logger.warning(
                    "heber_reader_filewise_fallback",
                    path=str(path),
                    error=str(exc),
                )
                return self._read_parquet_filewise(path=path, columns=columns, filters=filters)
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
    ) -> Any:
        try:
            return pq.read_table(path, columns=columns, filters=filters if filters else None, partitioning=partitioning)
        except Exception as exc:
            if self._is_corrupt_parquet_error(exc):
                raise
            if filters:
                logger.warning(
                    "heber_reader_filter_fallback",
                    path=str(path),
                    error=str(exc),
                )
                return pq.read_table(path, columns=columns, partitioning=partitioning)
            raise

    def _read_parquet_filewise(
        self,
        path: Path,
        columns: list[str] | None,
        filters: list[tuple[str, str, Any]] | None,
    ) -> pd.DataFrame:
        parquet_files = [
            file_path for file_path in sorted(path.rglob("*.parquet")) if not file_path.name.startswith("._")
        ]
        if not parquet_files:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for parquet_file in parquet_files:
            try:
                table = self._read_table(path=parquet_file, columns=columns, filters=filters, partitioning=None)
                frame = cast(pd.DataFrame, table.to_pandas())
                if not frame.empty:
                    frames.append(frame)
            except Exception as file_exc:
                logger.warning(
                    "heber_reader_skip_file",
                    path=str(parquet_file),
                    error=str(file_exc),
                )

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _is_corrupt_parquet_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "parquet magic bytes not found in footer" in message
            or "could not read schema from" in message
            or "is this a 'parquet' file?" in message
            or ("error creating dataset" in message and "could not open parquet input source" in message)
        )

    @staticmethod
    def _is_schema_merge_parquet_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return ("unsupported cast from" in message and "to null" in message and "cast_null" in message) or (
            "could not merge schemas" in message
        )


@lru_cache
def get_heber_reader() -> HeberReader:
    """Get singleton HeberReader instance."""
    return HeberReader()
