from __future__ import annotations

import json
from datetime import UTC
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import PlanningStatus, TripState
from app.extraction.models import NaturalLanguagePlanResponse
from app.persistence.models import PlanningSessionRecord
from app.persistence.schemas import PlanningSessionDetail, PlanningSessionSummary


class PersistenceError(RuntimeError):
    """Raised when a planning session cannot be stored or decoded safely."""


class PlanningSessionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(
        self,
        *,
        state: TripState,
        request_payload: BaseModel | dict[str, Any],
        provider_metadata: BaseModel | dict[str, Any] | None = None,
        response: NaturalLanguagePlanResponse | None = None,
    ) -> PlanningSessionDetail:
        if state.status not in {PlanningStatus.COMPLETED, PlanningStatus.INFEASIBLE}:
            raise PersistenceError(
                f"only completed or infeasible sessions may be saved, got {state.status.value}"
            )
        request_dict = _json_dict(request_payload)
        provider_dict = _json_dict(provider_metadata or {})
        record = PlanningSessionRecord(
            id=str(uuid4()),
            original_query=state.original_request,
            provider_metadata_json=json.dumps(provider_dict, sort_keys=True),
            final_status=state.status.value,
            origin_city_id=state.constraints.origin_city_id,
            destination_city_ids_json=json.dumps(state.constraints.destination_city_ids),
            initial_cost=_cost(state.initial_itinerary),
            final_cost=_cost(state.current_itinerary),
            preference_score=(
                str(state.preference_score.total) if state.preference_score is not None else None
            ),
            request_json=json.dumps(request_dict, sort_keys=True),
            state_json=state.model_dump_json(exclude_computed_fields=True),
            response_json=(
                response.model_dump_json(exclude_computed_fields=True)
                if response is not None
                else None
            ),
        )
        try:
            with self._session_factory() as session:
                session.add(record)
                session.flush()
                session.refresh(record)
                detail = _detail(record)
                session.commit()
                return detail
        except (SQLAlchemyError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError("planning session could not be saved") from exc

    def get_by_id(self, session_id: str) -> PlanningSessionDetail | None:
        try:
            with self._session_factory() as session:
                record = session.get(PlanningSessionRecord, session_id)
                return _detail(record) if record is not None else None
        except (SQLAlchemyError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError("planning session could not be loaded") from exc

    def list_recent(self, *, limit: int = 10) -> list[PlanningSessionSummary]:
        safe_limit = max(1, min(limit, 50))
        try:
            with self._session_factory() as session:
                records = session.scalars(
                    select(PlanningSessionRecord)
                    .order_by(desc(PlanningSessionRecord.created_at), desc(PlanningSessionRecord.id))
                    .limit(safe_limit)
                ).all()
                return [_summary(record) for record in records]
        except (SQLAlchemyError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError("planning history could not be loaded") from exc


def _json_dict(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return json.loads(value.model_dump_json())
    return json.loads(json.dumps(value, default=str))


def _cost(itinerary) -> str | None:
    return str(itinerary.costs.total) if itinerary is not None else None


def _summary(record: PlanningSessionRecord) -> PlanningSessionSummary:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return PlanningSessionSummary(
        id=record.id,
        created_at=created_at,
        query=record.original_query,
        origin_city_id=record.origin_city_id,
        destination_city_ids=json.loads(record.destination_city_ids_json),
        status=record.final_status,
        initial_cost=record.initial_cost,
        final_cost=record.final_cost,
        preference_score=record.preference_score,
    )


def _detail(record: PlanningSessionRecord) -> PlanningSessionDetail:
    return PlanningSessionDetail(
        **_summary(record).model_dump(),
        provider_metadata=json.loads(record.provider_metadata_json),
        request_payload=json.loads(record.request_json),
        result=TripState.model_validate_json(record.state_json),
        response=(
            NaturalLanguagePlanResponse.model_validate_json(record.response_json)
            if record.response_json is not None
            else None
        ),
    )
