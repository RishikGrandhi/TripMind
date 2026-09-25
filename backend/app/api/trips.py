from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from app.domain.models import PlanRequest, TripState
from app.extraction.models import (
    NaturalLanguagePlanRequest,
    NaturalLanguagePlanResponse,
)
from app.extraction.service import (
    ConstraintExtractionError,
    NaturalLanguagePlanningService,
    create_natural_language_planning_service,
)
from app.planning.coordinator import (
    PlanningCoordinator,
    PlanningCoordinatorError,
    create_planning_coordinator,
)
from app.persistence import PlanningSessionRepository, PersistenceError, get_database
from app.persistence.schemas import PlanningHistoryResponse, PlanningSessionDetail

router = APIRouter(tags=["planning"])


@lru_cache
def get_planning_coordinator() -> PlanningCoordinator:
    return create_planning_coordinator()


@lru_cache
def get_natural_language_planning_service() -> NaturalLanguagePlanningService:
    return create_natural_language_planning_service()


@lru_cache
def get_planning_session_repository() -> PlanningSessionRepository:
    database = get_database()
    database.initialize()
    return PlanningSessionRepository(database.session_factory)


def _persistence_unavailable(exc: PersistenceError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "persistence_unavailable", "message": str(exc)},
    )


@router.get("/trips/history", response_model=PlanningHistoryResponse)
def list_trip_history(
    limit: int = 10,
    repository: PlanningSessionRepository = Depends(get_planning_session_repository),
) -> PlanningHistoryResponse:
    try:
        return PlanningHistoryResponse(sessions=repository.list_recent(limit=limit))
    except PersistenceError as exc:
        raise _persistence_unavailable(exc) from exc


@router.get("/trips/{session_id}", response_model=PlanningSessionDetail)
def get_trip_session(
    session_id: str,
    repository: PlanningSessionRepository = Depends(get_planning_session_repository),
) -> PlanningSessionDetail:
    try:
        result = repository.get_by_id(session_id)
    except PersistenceError as exc:
        raise _persistence_unavailable(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Planning session not found")
    return result


@router.post("/trips/plan", response_model=TripState)
def plan_trip(
    request: PlanRequest,
    coordinator: PlanningCoordinator = Depends(get_planning_coordinator),
    repository: PlanningSessionRepository = Depends(get_planning_session_repository),
) -> TripState:
    try:
        result = coordinator.plan(request)
    except PlanningCoordinatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        repository.save(
            state=result,
            request_payload=request,
            provider_metadata={"request_type": "structured", "provider_used": "none"},
        )
    except PersistenceError as exc:
        raise _persistence_unavailable(exc) from exc
    return result


@router.post("/trips/plan-natural", response_model=NaturalLanguagePlanResponse)
def plan_natural_language_trip(
    request: NaturalLanguagePlanRequest,
    service: NaturalLanguagePlanningService = Depends(get_natural_language_planning_service),
    repository: PlanningSessionRepository = Depends(get_planning_session_repository),
) -> NaturalLanguagePlanResponse:
    try:
        result = service.plan(request.query)
    except ConstraintExtractionError as exc:
        raise HTTPException(status_code=422, detail=exc.as_detail()) from exc
    except PlanningCoordinatorError as exc:
        raise HTTPException(status_code=422, detail={"code": "planning_failed", "message": str(exc)}) from exc
    try:
        repository.save(
            state=result.result,
            request_payload=request,
            provider_metadata=result.extraction,
            response=result,
        )
    except PersistenceError as exc:
        raise _persistence_unavailable(exc) from exc
    return result
