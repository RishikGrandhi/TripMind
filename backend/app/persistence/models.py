from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PlanningSessionRecord(Base):
    __tablename__ = "planning_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    original_query: Mapped[str] = mapped_column(Text, nullable=False)
    provider_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    final_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    origin_city_id: Mapped[str] = mapped_column(String(80), nullable=False)
    destination_city_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    initial_cost: Mapped[str | None] = mapped_column(String(40), nullable=True)
    final_cost: Mapped[str | None] = mapped_column(String(40), nullable=True)
    preference_score: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
