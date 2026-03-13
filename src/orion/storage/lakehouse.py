import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import pandas as pd
import s3fs

from orion.shared.logger import setup_struct_logger
from orion.storage.models import BronzeEvent

logger = setup_struct_logger(__name__)


class LakehouseWriter:
    """
    Writes BronzeEvents to S3-compatible storage in Parquet format.
    Partitions by date.
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        max_workers: int | None = None,
    ):
        endpoint_url = endpoint_url or os.getenv("ORION_LAKEHOUSE_ENDPOINT_URL")
        access_key = access_key or os.getenv("ORION_LAKEHOUSE_ACCESS_KEY")
        secret_key = secret_key or os.getenv("ORION_LAKEHOUSE_SECRET_KEY")
        bucket = bucket or os.getenv("ORION_LAKEHOUSE_BUCKET")

        self.enabled = bool(endpoint_url and access_key and secret_key and bucket)
        self.bucket = bucket or ""
        self.max_workers = max_workers

        if not self.enabled:
            logger.warning("LakehouseWriter disabled (missing ORION_LAKEHOUSE_* config)")
            self.fs = None
            return

        client_kwargs = {
            "endpoint_url": endpoint_url,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }
        self.fs = s3fs.S3FileSystem(client_kwargs=client_kwargs, anon=False)

    def write_events(self, events: list[BronzeEvent]) -> None:
        """
        Writes a batch of events to the lakehouse.
        """
        if not self.enabled:
            return
        if not events:
            return

        data = [
            {
                "event_id": e.event_id,
                "source": e.source,
                "source_event_id": getattr(e, "source_event_id", None),
                "event_type": e.event_type,
                "event_ts_utc": e.event_ts_utc,
                "received_ts_utc": e.received_ts_utc,
                "trading_date": getattr(e, "trading_date", None),
                "session": getattr(e, "session", None),
                "ticker": getattr(e, "ticker", None),
                "schema_version": getattr(e, "schema_version", None),
                "payload": e.payload,
                "ingest": getattr(e, "ingest", None),
            }
            for e in events
        ]

        df = pd.DataFrame(data)
        df["date"] = df["event_ts_utc"].dt.date.astype(str)

        tasks = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for date_str, group_df in df.groupby("date"):
                for (source, event_type), sub_group in group_df.groupby(["source", "event_type"]):
                    tasks.append(executor.submit(self._write_partition, source, event_type, date_str, sub_group))

            # Wait for all tasks to complete and raise any exceptions
            for task in as_completed(tasks):
                task.result()

    def _write_partition(self, source: str, event_type: str, date_str: str, sub_group: pd.DataFrame) -> None:
        """
        Writes a single partition to S3.
        """
        filename = f"{datetime.now(UTC).strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}.parquet"
        path = f"s3://{self.bucket}/v1/{source}/{event_type}/date={date_str}/{filename}"

        # Write to S3
        content_df = sub_group.drop(columns=["date"])

        try:
            with self.fs.open(path, "wb") as f:
                content_df.to_parquet(f, engine="pyarrow", index=False)
            logger.info("lakehouse_write_ok", path=path, event_count=len(sub_group))
        except Exception as e:
            logger.error("lakehouse_write_failed", path=path, error=str(e))
            raise
