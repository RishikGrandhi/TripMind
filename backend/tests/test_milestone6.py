from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.models import (
    PlanningStatus,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TravelRequest,
)
from app.planning import CandidateBuilder, ConstraintValidator
from app.planning.coordinator import PlanningCoordinatorError, create_planning_coordinator
from app.planning.scoring import InfeasiblePreferenceScoreError, PreferenceScorer
from app.tools.local_data import load_catalog
from app.tools.registry import create_tool_registry

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA_DIR)


@pytest.fixture(scope="module")
def coordinator(catalog):
    return create_planning_coordinator(catalog=catalog)


def request(
    *,
    request_id: str = "milestone-6",
    origin: str = "city-mum",
    destination: str = "city-goi",
    start: date = date(2027, 1, 15),
    end: date = date(2027, 1, 18),
    travelers: int = 2,
    budget: Decimal = Decimal("100000"),
    max_flight: Decimal | None = None,
    max_hotel: Decimal | None = None,
    max_travel: int | None = None,
    modes: list[TransportMode] | None = None,
    preferences: SoftPreferences | None = None,
) -> TravelRequest:
    return TravelRequest(
        request_id=request_id,
        natural_language_request=f"structured request {request_id}",
        constraints=TravelConstraints(
            origin_city_id=origin,
            destination_city_id=destination,
            start_date=start,
            end_date=end,
            travelers=travelers,
            total_budget=budget,
            max_flight_price=max_flight,
            max_hotel_price_per_night=max_hotel,
            max_travel_duration_minutes=max_travel,
            allowed_transport_modes=modes or [TransportMode.FLIGHT],
        ),
        preferences=preferences
        or SoftPreferences(
            preferred_transport_modes=[TransportMode.FLIGHT],
            preferred_hotel_amenities=["pool", "beach_access"],
            preferred_activity_categories=["nature"],
            pace="balanced",
        ),
    )


def test_feasible_request_scores_without_replanning(coordinator) -> None:
    state = coordinator.plan(request())
    assert state.status == PlanningStatus.COMPLETED
    assert state.replanning_attempts == []
    assert state.preference_score is not None
    assert state.initial_itinerary == state.current_itinerary
    assert "without replanning" in state.final_explanation


def test_flagship_pipeline_repairs_then_scores_and_explains(coordinator) -> None:
    state = coordinator.plan(request(request_id="flagship", budget=Decimal("30000")))
    assert state.status == PlanningStatus.COMPLETED
    assert state.initial_itinerary.costs.total == Decimal("51500.00")
    assert state.current_itinerary.costs.total == Decimal("15100.00")
    assert len(state.replanning_attempts) == 2
    assert len(state.validation_history) == 3
    assert len(state.tool_call_history) == 2
    assert state.preference_score.total == Decimal("59.93")
    assert state.preference_score.components == {
        "activity_match": Decimal("50.00"),
        "hotel_amenity_match": Decimal("0.00"),
        "transport_match": Decimal("100.00"),
        "airline_match": Decimal("100.00"),
        "pace_match": Decimal("100.00"),
        "cost_efficiency": Decimal("49.67"),
    }
    assert "flight-mum-goi-004 to flight-mum-goi-001" in state.final_explanation
    assert "hotel-goi-004 to hotel-goi-001" in state.final_explanation
    assert "59.93/100" in state.final_explanation


def test_infeasible_request_is_not_soft_scored_and_cites_recorded_minimum(
    coordinator,
) -> None:
    state = coordinator.plan(request(max_flight=Decimal("4000")))
    assert state.status == PlanningStatus.INFEASIBLE
    assert state.preference_score is None
    assert state.current_validation.is_valid is False
    assert state.replanning_attempts[0].actions[0].parameters[
        "minimum_available_fare"
    ] == Decimal("4200.00")
    assert "cheapest available flight fare was INR 4,200" in state.final_explanation
    assert "Remaining issues" in state.final_explanation


def test_multiple_violations_repair_history_is_coherent(coordinator) -> None:
    state = coordinator.plan(
        request(
            budget=Decimal("30000"),
            max_flight=Decimal("6000"),
            max_hotel=Decimal("5000"),
        )
    )
    assert state.status == PlanningStatus.COMPLETED
    assert len(state.replanning_attempts) == 2
    assert [attempt.succeeded for attempt in state.replanning_attempts] == [True, True]
    assert [result.is_valid for result in state.validation_history] == [False, False, True]


def test_hard_constraints_and_input_request_are_immutable(coordinator) -> None:
    source = request(budget=Decimal("30000"))
    before = source.model_dump_json()
    state = coordinator.plan(source)
    assert source.model_dump_json() == before
    assert state.constraints.model_dump_json() == source.constraints.model_dump_json()


def test_same_request_has_identical_serialized_result(coordinator) -> None:
    source = request(request_id="deterministic", budget=Decimal("30000"))
    assert coordinator.plan(source).model_dump_json() == coordinator.plan(source).model_dump_json()


def test_tradeoff_explanation_uses_recorded_cost_and_duration(coordinator) -> None:
    source = request(
        origin="city-del",
        destination="city-sml",
        start=date(2027, 1, 20),
        end=date(2027, 1, 23),
        travelers=1,
        max_travel=500,
        modes=[TransportMode.BUS, TransportMode.CAR],
        preferences=SoftPreferences(preferred_transport_modes=[TransportMode.BUS]),
    )
    state = coordinator.plan(source)
    attempt = state.replanning_attempts[0]
    assert attempt.cost_effect > 0
    assert attempt.duration_effect_minutes == -150
    assert "trade-off increased cost" in state.final_explanation
    assert "reduced travel time by 150 minutes" in state.final_explanation


def test_initial_candidate_failure_raises_typed_error(coordinator) -> None:
    with pytest.raises(PlanningCoordinatorError, match="Unknown city IDs"):
        coordinator.plan(request(origin="city-unknown"))


def test_scorer_rejects_hard_constraint_violation(catalog) -> None:
    source = request(max_flight=Decimal("4000"))
    candidate = CandidateBuilder(create_tool_registry(catalog=catalog)).build(source)
    validation = ConstraintValidator(catalog).validate(candidate, source.constraints)
    with pytest.raises(InfeasiblePreferenceScoreError):
        PreferenceScorer(catalog).score(
            candidate, source.constraints, source.preferences, validation
        )


def test_score_weights_are_explicit_and_sum_to_one(coordinator) -> None:
    score = coordinator.plan(request()).preference_score
    assert score is not None
    assert sum(score.weights.values(), Decimal("0")) == Decimal("1.00")
    assert all(Decimal("0") <= value <= Decimal("100") for value in score.components.values())


def test_activity_preference_changes_only_observed_match_component(catalog) -> None:
    base = request(preferences=SoftPreferences(pace="balanced"))
    candidate = CandidateBuilder(create_tool_registry(catalog=catalog)).build(base)
    validation = ConstraintValidator(catalog).validate(candidate, base.constraints)
    scorer = PreferenceScorer(catalog)
    nature = scorer.score(
        candidate,
        base.constraints,
        SoftPreferences(preferred_activity_categories=["nature"], pace="balanced"),
        validation,
    )
    missing = scorer.score(
        candidate,
        base.constraints,
        SoftPreferences(preferred_activity_categories=["not-present"], pace="balanced"),
        validation,
    )
    assert nature.components["activity_match"] > missing.components["activity_match"]


def test_hotel_preference_changes_amenity_component(catalog) -> None:
    base = request(preferences=SoftPreferences(pace="balanced"))
    candidate = CandidateBuilder(create_tool_registry(catalog=catalog)).build(base)
    validation = ConstraintValidator(catalog).validate(candidate, base.constraints)
    scorer = PreferenceScorer(catalog)
    matching = scorer.score(
        candidate,
        base.constraints,
        SoftPreferences(preferred_hotel_amenities=["beach_access"], pace="balanced"),
        validation,
    )
    missing = scorer.score(
        candidate,
        base.constraints,
        SoftPreferences(preferred_hotel_amenities=["not-present"], pace="balanced"),
        validation,
    )
    assert matching.components["hotel_amenity_match"] == Decimal("100.00")
    assert missing.components["hotel_amenity_match"] == Decimal("0.00")


def test_transport_and_airline_preferences_use_selected_flight(catalog) -> None:
    base = request(preferences=SoftPreferences(pace="balanced"))
    candidate = CandidateBuilder(create_tool_registry(catalog=catalog)).build(base)
    validation = ConstraintValidator(catalog).validate(candidate, base.constraints)
    scorer = PreferenceScorer(catalog)
    matching = scorer.score(
        candidate,
        base.constraints,
        SoftPreferences(
            preferred_transport_modes=[TransportMode.FLIGHT],
            preferred_airlines=["SkyLux Demo"],
            pace="balanced",
        ),
        validation,
    )
    missing = scorer.score(
        candidate,
        base.constraints,
        SoftPreferences(
            preferred_transport_modes=[TransportMode.BUS],
            preferred_airlines=["not-present"],
            pace="balanced",
        ),
        validation,
    )
    assert matching.components["transport_match"] == Decimal("100.00")
    assert matching.components["airline_match"] == Decimal("100.00")
    assert missing.components["transport_match"] == Decimal("0.00")
    assert missing.components["airline_match"] == Decimal("0.00")


def test_cost_efficiency_uses_exact_budget_headroom(coordinator) -> None:
    score = coordinator.plan(request()).preference_score
    assert score is not None
    assert score.components["cost_efficiency"] == Decimal("48.50")


def test_structured_plan_endpoint_returns_full_state(api_client) -> None:
    payload = request(request_id="api", budget=Decimal("30000")).model_dump(mode="json")
    response = api_client.post("/api/v1/trips/plan", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["initial_itinerary"]["costs"]["total"] == "51500.00"
    assert body["current_itinerary"]["costs"]["total"] == "15100.00"
    assert body["preference_score"]["total"] == "59.93"
    assert body["current_validation"]["is_valid"] is True
    assert len(body["replanning_attempts"]) == 2
    assert "budget_exceeded" in body["final_explanation"]


def test_structured_plan_endpoint_rejects_malformed_input(api_client) -> None:
    response = api_client.post("/api/v1/trips/plan", json={"request_id": "bad"})
    assert response.status_code == 422


def test_explanation_does_not_invent_replanning_for_feasible_initial_plan(
    coordinator,
) -> None:
    explanation = coordinator.plan(request()).final_explanation
    assert "without replanning" in explanation
    assert "Replanning attempt" not in explanation
    assert "cheapest available" not in explanation
