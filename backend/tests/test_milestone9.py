from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.trips import get_planning_session_repository
from app.core.config import Settings
from app.domain.models import PlanRequest, PlanningStatus, SoftPreferences, TravelConstraints
from app.evaluation.runner import run_evaluation
from app.main import app
from app.persistence import PlanningSessionRepository, PersistenceError, create_database
from app.planning.coordinator import create_planning_coordinator


FLAGSHIP_QUERY = "Mumbai to Goa for 4 days with a maximum budget of ₹30,000. I prefer beaches and direct flights."


@pytest.fixture
def repository(tmp_path):
    database = create_database(Settings(database_url=f"sqlite:///{tmp_path / 'history.db'}"))
    database.initialize()
    yield PlanningSessionRepository(database.session_factory)
    database.dispose()


@pytest.fixture
def client(repository):
    app.dependency_overrides[get_planning_session_repository] = lambda: repository
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.pop(get_planning_session_repository, None)


def _request(request_id: str = "persist-test", *, budget: str = "30000", max_flight: str | None = None) -> PlanRequest:
    return PlanRequest(
        request_id=request_id,
        natural_language_request="structured persistence test",
        constraints=TravelConstraints(
            origin_city_id="city-mum",
            destination_city_id="city-goi",
            start_date=date(2027, 1, 15),
            end_date=date(2027, 1, 18),
            total_budget=Decimal(budget),
            max_flight_price=Decimal(max_flight) if max_flight else None,
        ),
        preferences=SoftPreferences(preferred_activity_categories=["nature"]),
    )


def test_persistence_round_trip_preserves_decimal_dates_and_constraints(repository) -> None:
    request = _request()
    before = request.constraints.model_dump_json()
    state = create_planning_coordinator().plan(request)
    saved = repository.save(state=state, request_payload=request)
    restored = repository.get_by_id(saved.id)
    assert restored is not None
    assert restored.result.model_dump_json() == state.model_dump_json()
    assert restored.result.constraints.start_date == date(2027, 1, 15)
    assert restored.final_cost == Decimal("10400.00")
    assert restored.result.constraints.model_dump_json() == before


def test_repository_lists_newest_sessions_with_compact_summaries(repository) -> None:
    coordinator = create_planning_coordinator()
    first = repository.save(state=coordinator.plan(_request("one")), request_payload=_request("one"))
    second = repository.save(state=coordinator.plan(_request("two")), request_payload=_request("two"))
    recent = repository.list_recent(limit=1)
    assert len(recent) == 1
    assert recent[0].id == second.id
    assert recent[0].id != first.id
    assert recent[0].destination_city_ids == ["city-goi"]


def test_repository_saves_normal_infeasible_result(repository) -> None:
    request = _request("impossible", max_flight="4000")
    state = create_planning_coordinator().plan(request)
    assert state.status == PlanningStatus.INFEASIBLE
    saved = repository.save(state=state, request_payload=request)
    assert repository.get_by_id(saved.id).status == PlanningStatus.INFEASIBLE


def test_repository_rejects_partial_state(repository) -> None:
    state = create_planning_coordinator().plan(_request()).model_copy(update={"status": PlanningStatus.FAILED})
    with pytest.raises(PersistenceError, match="only completed or infeasible"):
        repository.save(state=state, request_payload={})


def test_natural_plan_is_saved_and_can_be_reopened(client) -> None:
    planned = client.post("/api/v1/trips/plan-natural", json={"query": FLAGSHIP_QUERY})
    assert planned.status_code == 200
    history = client.get("/api/v1/trips/history?limit=5")
    assert history.status_code == 200
    summary = history.json()["sessions"][0]
    assert summary["status"] == "completed"
    assert summary["initial_cost"] == "38500.00"
    assert summary["final_cost"] == "10400.00"
    detail = client.get(f"/api/v1/trips/{summary['id']}")
    assert detail.status_code == 200
    assert detail.json()["response"] == planned.json()


def test_structured_endpoint_saves_session(client) -> None:
    response = client.post("/api/v1/trips/plan", json=_request("structured-api").model_dump(mode="json"))
    assert response.status_code == 200
    detail_id = client.get("/api/v1/trips/history").json()["sessions"][0]["id"]
    detail = client.get(f"/api/v1/trips/{detail_id}").json()
    assert detail["response"] is None
    assert detail["result"]["trip_id"] == "structured-api"


@pytest.mark.parametrize("session_id", ["missing", "not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_unknown_or_malformed_session_id_returns_404(client, session_id) -> None:
    assert client.get(f"/api/v1/trips/{session_id}").status_code == 404


def test_clarification_is_not_saved(client) -> None:
    response = client.post("/api/v1/trips/plan-natural", json={"query": "Plan a trip to Goa for four days."})
    assert response.status_code == 422
    assert client.get("/api/v1/trips/history").json()["sessions"] == []


def test_evaluation_scenarios_match_expected_outcomes() -> None:
    report = run_evaluation()
    assert report.scenario_count == 7
    assert all(item.expectation_met for item in report.results)
    assert report.hard_constraint_satisfaction_rate == Decimal("100.00")
    assert report.repair_success_rate == Decimal("100.00")
    assert report.deterministic_replay_consistency == Decimal("100.00")
    assert report.correct_infeasible_detection is True
