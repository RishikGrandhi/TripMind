from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import socket

import pytest

from app.domain.models import (
    CorrectiveAction,
    CorrectiveActionType,
    ItemType,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    PlanningStatus,
    ReplanningAttempt,
    ReplanningOutcome,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TravelRequest,
    TripState,
    ViolationCode,
)
from app.planning import (
    CandidateBuilder,
    CandidateSelectionOverrides,
    ConstraintValidator,
    ReplanningEngine,
    calculate_cost_breakdown,
)
from app.planning.replanning.policy import ReplanningPolicy
from app.tools.local_data import load_catalog
from app.tools.registry import create_tool_registry

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA_DIR)


@pytest.fixture(scope="module")
def tools(catalog):
    return create_tool_registry(catalog=catalog)


@pytest.fixture(scope="module")
def builder(tools):
    return CandidateBuilder(tools)


@pytest.fixture(scope="module")
def validator(catalog):
    return ConstraintValidator(catalog)


def engine(builder, validator, tools, catalog, *, attempts=3, calls=20):
    return ReplanningEngine(
        builder=builder,
        validator=validator,
        tools=tools,
        catalog=catalog,
        policy=ReplanningPolicy(),
        max_attempts=attempts,
        max_tool_calls=calls,
    )


def make_request(
    *,
    request_id="replan-request",
    origin="city-mum",
    destination="city-goi",
    start=date(2027, 1, 15),
    end=date(2027, 1, 18),
    travelers=2,
    budget=Decimal("100000"),
    max_flight=None,
    max_hotel=None,
    max_travel=None,
    allowed_modes=None,
    preferences=None,
):
    return TravelRequest(
        request_id=request_id,
        natural_language_request=request_id,
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
            allowed_transport_modes=allowed_modes or [TransportMode.FLIGHT],
        ),
        preferences=preferences
        or SoftPreferences(
            preferred_transport_modes=[TransportMode.FLIGHT],
            preferred_hotel_amenities=["pool", "beach_access"],
            preferred_activity_categories=["nature"],
            pace="balanced",
        ),
    )


def make_state(request, builder, validator, overrides=None):
    candidate = builder.build(request, overrides)
    validation = validator.validate(candidate, request.constraints)
    return TripState(
        trip_id=request.request_id,
        original_request=request.natural_language_request,
        constraints=request.constraints,
        preferences=request.preferences,
        status=PlanningStatus.CANDIDATE_READY,
        current_itinerary=candidate,
        current_validation=validation,
        validation_history=[validation],
    )


def action_types(result):
    return [attempt.actions[0].action for attempt in result.state.replanning_attempts]


def test_flagship_budget_violation_is_repaired(builder, validator, tools, catalog) -> None:
    request = make_request(budget=Decimal("30000"))
    state = make_state(request, builder, validator)
    result = engine(builder, validator, tools, catalog).replan(state)
    assert state.current_itinerary.costs.total == Decimal("51500.00")
    assert result.outcome == ReplanningOutcome.FEASIBLE
    assert result.state.current_itinerary.costs.total == Decimal("15100.00")
    assert result.state.current_validation.feasible is True
    assert action_types(result) == [
        CorrectiveActionType.SEARCH_CHEAPER_FLIGHT,
        CorrectiveActionType.SEARCH_CHEAPER_HOTEL,
    ]


def test_flagship_history_explains_each_component_change(builder, validator, tools, catalog) -> None:
    state = make_state(make_request(budget=Decimal("30000")), builder, validator)
    result = engine(builder, validator, tools, catalog).replan(state)
    first, second = result.state.replanning_attempts
    assert (first.before_component_id, first.after_component_id) == (
        "flight-mum-goi-004",
        "flight-mum-goi-001",
    )
    assert first.cost_effect == Decimal("-16600.00")
    assert first.validation_result.feasible is False
    assert (second.before_component_id, second.after_component_id) == (
        "hotel-goi-004",
        "hotel-goi-001",
    )
    assert second.cost_effect == Decimal("-19800.00")
    assert second.validation_result.feasible is True


def test_hotel_ceiling_repaired_with_compliant_hotel(builder, validator, tools, catalog) -> None:
    request = make_request(max_hotel=Decimal("3000"))
    result = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.FEASIBLE
    assert action_types(result) == [CorrectiveActionType.SEARCH_CHEAPER_HOTEL]
    assert result.state.replanning_attempts[0].after_value == Decimal("1900.00")


def test_flight_ceiling_repaired_with_compliant_flight(builder, validator, tools, catalog) -> None:
    request = make_request(max_flight=Decimal("6000"))
    result = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.FEASIBLE
    assert action_types(result) == [CorrectiveActionType.SEARCH_CHEAPER_FLIGHT]
    assert result.state.replanning_attempts[0].after_value == Decimal("4200.00")


def test_daily_travel_time_repaired_with_faster_route(builder, validator, tools, catalog) -> None:
    request = make_request(
        origin="city-del",
        destination="city-sml",
        start=date(2027, 1, 20),
        end=date(2027, 1, 23),
        travelers=1,
        max_travel=500,
        allowed_modes=[TransportMode.BUS, TransportMode.CAR],
        preferences=SoftPreferences(preferred_transport_modes=[TransportMode.BUS]),
    )
    state = make_state(request, builder, validator)
    result = engine(builder, validator, tools, catalog).replan(state)
    attempt = result.state.replanning_attempts[0]
    assert attempt.before_component_id == "route-del-sml-bus-001"
    assert attempt.after_component_id == "route-del-sml-car-001"
    assert attempt.duration_effect_minutes == -150
    assert result.outcome == ReplanningOutcome.FEASIBLE


def test_explicitly_infeasible_route_repaired(builder, validator, tools, catalog) -> None:
    request = make_request(
        origin="city-del",
        destination="city-sml",
        start=date(2027, 1, 20),
        end=date(2027, 1, 23),
        travelers=1,
        allowed_modes=[TransportMode.TRAIN, TransportMode.CAR],
        preferences=SoftPreferences(preferred_transport_modes=[TransportMode.CAR]),
    )
    state = make_state(request, builder, validator)
    bad_route = ItineraryItem(
        id="item-transport-01",
        item_type=ItemType.ROUTE,
        option_id="route-del-sml-train-001",
        title="Infeasible train",
        starts_at=datetime(2027, 1, 20, 8, tzinfo=IST),
        ends_at=datetime(2027, 1, 20, 20, tzinfo=IST),
        cost=Decimal("750"),
        origin_city_id="city-del",
        destination_city_id="city-sml",
        duration_minutes=720,
    )
    days = []
    for day in state.current_itinerary.days:
        items = [
            item
            for item in day.items
            if item.item_type not in {ItemType.FLIGHT, ItemType.ROUTE}
        ]
        if day.date == request.constraints.start_date:
            items.append(bad_route)
        days.append(ItineraryDay(date=day.date, items=items))
    all_items = [item for day in days for item in day.items]
    state.current_itinerary = Itinerary(
        id=state.current_itinerary.id,
        days=days,
        costs=calculate_cost_breakdown(all_items),
    )
    state.current_validation = validator.validate(
        state.current_itinerary, state.constraints
    )
    state.validation_history = [state.current_validation]
    result = engine(builder, validator, tools, catalog).replan(state)
    assert result.outcome == ReplanningOutcome.FEASIBLE
    assert action_types(result) == [CorrectiveActionType.SELECT_FEASIBLE_ROUTE]
    assert result.state.replanning_attempts[0].after_component_id == "route-del-sml-car-001"


def test_no_alternative_returns_explicit_infeasible_result(builder, validator, tools, catalog) -> None:
    request = make_request(max_flight=Decimal("4000"))
    result = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.INFEASIBLE
    assert result.state.current_validation.feasible is False
    assert result.state.replanning_attempts[0].outcome == "no_cheaper_flight_available"


def test_multiple_violations_repaired_across_attempts(builder, validator, tools, catalog) -> None:
    request = make_request(
        budget=Decimal("30000"),
        max_flight=Decimal("6000"),
        max_hotel=Decimal("5000"),
    )
    result = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.FEASIBLE
    assert len(result.state.replanning_attempts) == 2
    assert all(len(attempt.actions) == 1 for attempt in result.state.replanning_attempts)


def test_maximum_replanning_attempts_is_enforced(builder, validator, tools, catalog) -> None:
    request = make_request(budget=Decimal("30000"))
    result = engine(builder, validator, tools, catalog, attempts=1).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.INFEASIBLE
    assert result.termination_reason == "maximum_replanning_attempts_reached"
    assert result.attempts_used == 1
    assert result.state.current_itinerary.costs.total == Decimal("34900.00")


def test_maximum_tool_calls_is_enforced(builder, validator, tools, catalog) -> None:
    request = make_request(budget=Decimal("30000"))
    result = engine(builder, validator, tools, catalog, attempts=3, calls=1).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.INFEASIBLE
    assert result.termination_reason == "maximum_tool_calls_reached"
    assert result.tool_calls_used == 1


def test_repeated_failed_action_is_not_retried(builder, validator, tools, catalog) -> None:
    request = make_request(max_flight=Decimal("4000"))
    result = engine(builder, validator, tools, catalog, attempts=3).replan(
        make_state(request, builder, validator)
    )
    assert action_types(result).count(CorrectiveActionType.SEARCH_CHEAPER_FLIGHT) == 1
    assert len(result.state.replanning_attempts) == 1


def test_no_improving_option_is_rejected(builder, validator, tools, catalog) -> None:
    request = make_request(max_flight=Decimal("4000"))
    result = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    attempt = result.state.replanning_attempts[0]
    assert attempt.succeeded is False
    assert attempt.before_component_id == attempt.after_component_id
    assert attempt.cost_effect == Decimal("0.00")


def test_hard_constraints_are_never_mutated(builder, validator, tools, catalog) -> None:
    request = make_request(budget=Decimal("30000"))
    state = make_state(request, builder, validator)
    before = state.constraints.model_dump_json()
    original_state = state.model_dump_json()
    result = engine(builder, validator, tools, catalog).replan(state)
    assert state.model_dump_json() == original_state
    assert state.constraints.model_dump_json() == before
    assert result.state.constraints.model_dump_json() == before


def test_previous_histories_are_retained(builder, validator, tools, catalog) -> None:
    request = make_request(budget=Decimal("30000"))
    state = make_state(request, builder, validator)
    previous_action = CorrectiveAction(
        action=CorrectiveActionType.SEARCH_CHEAPER_HOTEL,
        reason="Earlier unrelated attempt",
        target_id="hotel-old",
        target_violation=ViolationCode.HOTEL_COST_EXCEEDED,
        expected_goal="reduce nightly rate",
    )
    previous_attempt = ReplanningAttempt(
        attempt_number=1,
        triggered_by=[ViolationCode.HOTEL_COST_EXCEEDED],
        actions=[previous_action],
        created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        succeeded=False,
        outcome="previous",
    )
    previous_validation = state.current_validation.model_copy(deep=True)
    state.replanning_attempts = [previous_attempt]
    state.validation_history = [previous_validation]
    result = engine(builder, validator, tools, catalog, attempts=4).replan(state)
    assert result.state.replanning_attempts[0] == previous_attempt
    assert result.state.validation_history[0] == previous_validation
    assert len(result.state.replanning_attempts) == 3


def test_same_input_has_deterministic_repair_sequence(builder, validator, tools, catalog) -> None:
    request = make_request(budget=Decimal("30000"))
    first = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    second = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    assert first.model_dump_json() == second.model_dump_json()


def test_replanner_uses_no_network_in_demo_mode(
    monkeypatch: pytest.MonkeyPatch, builder, validator, tools, catalog
) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket, "socket", fail_network)
    request = make_request(budget=Decimal("30000"))
    result = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.FEASIBLE


def test_feasible_input_performs_zero_replanning(builder, validator, tools, catalog) -> None:
    request = make_request()
    result = engine(builder, validator, tools, catalog).replan(
        make_state(request, builder, validator)
    )
    assert result.outcome == ReplanningOutcome.FEASIBLE
    assert result.attempts_used == 0
    assert result.tool_calls_used == 0
    assert result.state.replanning_attempts == []
