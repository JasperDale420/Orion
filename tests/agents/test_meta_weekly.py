from __future__ import annotations

import json

import pytest

from orion.main_meta_weekly import _save_summary


@pytest.mark.asyncio
async def test_save_summary_creates_parent_directory(tmp_path) -> None:
    output_path = tmp_path / "reports" / "weekly" / "summary.json"

    await _save_summary(str(output_path), {"status": "ok", "count": 3})

    assert output_path.exists()
    assert json.loads(output_path.read_text()) == {"status": "ok", "count": 3}
