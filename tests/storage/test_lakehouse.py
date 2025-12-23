import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from orion.storage.lakehouse import LakehouseWriter
from orion.storage.models import BronzeEvent


class TestLakehouseWriter(unittest.TestCase):
    def setUp(self):
        self.endpoint = "http://localhost:9000"
        self.key = "test"
        self.secret = "test"
        self.bucket = "test-bucket"

        # Patch s3fs
        self.fs_patcher = patch("orion.storage.lakehouse.s3fs.S3FileSystem")
        self.MockFS = self.fs_patcher.start()
        self.mock_fs_instance = self.MockFS.return_value

    def tearDown(self):
        self.fs_patcher.stop()

    @patch("pandas.DataFrame.to_parquet")
    def test_write_events(self, mock_to_parquet):
        writer = LakehouseWriter(self.endpoint, self.key, self.secret, self.bucket)

        # Create dummy events
        events = [
            BronzeEvent(
                event_id="e1",
                source="UW",
                event_type="UW_FLOW",
                event_ts_utc=datetime(2023, 1, 1, 12, 0, 0),
                received_ts_utc=datetime(2023, 1, 1, 12, 0, 1),
                payload={"data": "test"},
            )
        ]

        # Mock fs.open to return a context manager that yields a mock file
        mock_file = MagicMock()
        self.mock_fs_instance.open.return_value.__enter__.return_value = mock_file

        writer.write_events(events)

        # Verify open called with correct path
        self.mock_fs_instance.open.assert_called()
        call_args = self.mock_fs_instance.open.call_args
        path = call_args[0][0]

        self.assertTrue(path.startswith("s3://test-bucket/v1/UW/UW_FLOW/date=2023-01-01/"))
        self.assertTrue(path.endswith(".parquet"))

        # Verify to_parquet called
        mock_to_parquet.assert_called()
