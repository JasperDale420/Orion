from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Request identity / correlation
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # HTTP request basics
    method: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)

    # Minimal context
    client_host: Mapped[str | None] = mapped_column(String, nullable=True)
    query_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
