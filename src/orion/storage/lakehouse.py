import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import s3fs

from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)


class LakehouseWriter:
    """
    Writes BronzeEvents to S3-compatible storage in Parquet format.
    Partitions by date.
    """

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
    ):
        endpoint_url = endpoint_url or os.getenv("ORION_LAKEHOUSE_ENDPOINT_URL")
        access_key = access_key or os.getenv("ORION_LAKEHOUSE_ACCESS_KEY")
        secret_key = secret_key or os.getenv("ORION_LAKEHOUSE_SECRET_KEY")
        bucket = bucket or os.getenv("ORION_LAKEHOUSE_BUCKET")

        self.enabled = bool(endpoint_url and access_key and secret_key and bucket)
        self.bucket = bucket or ""

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

    def write_events(self, events: List[BronzeEvent]) -> None:
        """
        Writes a batch of events to the lakehouse.
        """
        if not self.enabled:
            return
        if not events:
            return

        # Convert to DataFrame
        # We need to serialize the payload to string or keep as struct?
        # Parquet handles nested structs well, but for bronze raw, json string is often safer/easier
        # or we rely on PyArrow's ability to handle dicts.
        # Let's try keeping it as dict first, Pandas might struggle without specific types.
        # Safer: normalize widely before writing?
        # "Bronze" usually implies raw.
        # For simplicity and robustness, we can dump payload to JSON string if nested.

        data = []
        for e in events:
            row = {
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
                "payload": e.payload,  # pyarrow/pandas might complain if schema varies
                "ingest": getattr(e, "ingest", None),
            }
            data.append(row)

        df = pd.DataFrame(data)

        # Determine partition date (using first event's date or current date?
        # Ideally we group by date if batch spans multiple days.)

        # Let's group by date to be safe
        # Ensure event_ts_utc is datetime
        # OPTIMIZATION: Use vectorized .dt.date.astype(str) instead of .apply(strftime)
        # This is approx 4-5x faster for large datasets.
        df["date"] = df["event_ts_utc"].dt.date.astype(str)

        for date_str, group_df in df.groupby("date"):
            # Construct path
            # s3://{bucket}/v1/{source}/{event_type}/date={YYYY-MM-DD}/{timestamp}_{uuid}.parquet

            # We can only write if source and event_type are uniform per group?
            # Or we include them in path?
            # Typically lakehouse is source/type/date.
            # If batch has mixed types, we must group by source/type too.

            for (source, event_type), sub_group in group_df.groupby(["source", "event_type"]):
                # Create unique filename
                filename = f"{datetime.now(timezone.utc).strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}.parquet"
                path = f"s3://{self.bucket}/v1/{source}/{event_type}/date={date_str}/{filename}"

                # Write to S3
                # We drop the partition columns from the file content usually, but keeping them is fine too.
                # Dropping 'date' as it's in the path.
                content_df = sub_group.drop(columns=["date"])

                try:
                    with self.fs.open(path, "wb") as f:
                        content_df.to_parquet(f, engine="pyarrow", index=False)
                    logger.info(f"Wrote {len(sub_group)} events to {path}")
                except Exception as e:
                    logger.error(f"Failed to write to {path}: {e}")
                    raise e
