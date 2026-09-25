from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import PlanningStatus, TripState
from app.extraction.models import NaturalLanguagePlanResponse


class PersistenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanningSessionSummary(PersistenceModel):
    id: str
    created_at: datetime
    query: str
    origin_city_id: str
    destination_city_ids: list[str]
    status: PlanningStatus
    initial_cost: Decimal | None = None
    final_cost: Decimal | None = None
    preference_score: Decimal | None = None


class PlanningHistoryResponse(PersistenceModel):
    sessions: list[PlanningSessionSummary]


class PlanningSessionDetail(PlanningSessionSummary):
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    request_payload: dict[str, Any]
    result: TripState
    response: NaturalLanguagePlanResponse | None = None
