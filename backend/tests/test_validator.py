from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.models import (
    ItemType,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TravelRequest,
    ViolationCode,
)
from app.planning import CandidateBuilder, ConstraintValidator, calculate_cost_breakdown
from app.tools.local_data import load_catalog
from app.tools.registry import create_tool_registry

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA_DIR)


@pytest.fixture(scope="module")
def builder(catalog):
    return CandidateBuilder(create_tool_registry(catalog=catalog))


@pytest.fixture(scope="module")
def validator(catalog):
    return ConstraintValidator(catalog)


def make_request(
    *,
    request_id: str = "validation-request",
    origin: str = "city-hyd",
    destination: str = "city-goi",
    additional_destinations: list[str] | None = None,
    start: date = date(2027, 1, 15),
    end: date = date(2027, 1, 18),
    travelers: int = 2,
    budget: Decimal = Decimal("100000"),
    max_flight: Decimal | None = None,
    max_hotel: Decimal | None = None,
    max_travel: int | None = None,
    allowed_modes: list[TransportMode] | None = None,
    preferences: SoftPreferences | None = None,
) -> TravelRequest:
    return TravelRequest(
        request_id=request_id,
        natural_language_request=request_id,
        constraints=TravelConstraints(
            origin_city_id=origin,
            destination_city_id=destination,
            additional_destination_city_ids=additional_destinations or [],
            start_date=start,
            end_date=end,
            travelers=travelers,
            total_budget=budget,
            max_flight_price=max_flight,
            max_hotel_price_per_night=max_hotel,
            max_travel_duration_minutes=max_travel,
            allowed_transport_modes=allowed_modes or [TransportMode.FLIGHT],
        ),
        preferences=preferences or SoftPreferences(),
    )


def flagship_request(
    *,
    budget: Decimal = Decimal("30000"),
    max_hotel: Decimal | None = None,
    max_travel: int | None = None,
) -> TravelRequest:
    return make_request(
        request_id="flagship-validation",
        origin="city-mum",
        budget=budget,
        max_hotel=max_hotel,
        max_travel=max_travel,
        preferences=SoftPreferences(
            preferred_transport_modes=[TransportMode.FLIGHT],
            preferred_hotel_amenities=["pool", "beach_access"],
            preferred_activity_categories=["nature"],
            pace="balanced",
        ),
    )


def violation_codes(result) -> list[ViolationCode]:
    return [violation.code for violation in result.violations]


def check_for(result, constraint: str):
    return next(check for check in result.checks if check.constraint == constraint)


def itinerary_with_days(candidate: Itinerary, days: list[ItineraryDay]) -> Itinerary:
    items = [item for day in days for item in day.items]
    return Itinerary(
        id=candidate.id,
        days=days,
        costs=calculate_cost_breakdown(items),
    )


def test_fully_feasible_candidate_passes(builder, validator) -> None:
    request = make_request()
    result = validator.validate(builder.build(request), request.constraints)
    assert result.is_valid is True
    assert result.feasible is True
    assert result.violations == []
    assert all(check.passed for check in result.checks)


def test_flagship_budget_violation_has_exact_decimal_details(builder, validator) -> None:
    request = flagship_request()
    result = validator.validate(builder.build(request), request.constraints)
    budget_violation = next(
        violation
        for violation in result.violations
        if violation.code == ViolationCode.BUDGET_EXCEEDED
    )
    assert result.feasible is False
    assert budget_violation.details == {
        "limit": Decimal("30000"),
        "actual": Decimal("51500.00"),
        "difference": Decimal("21500.00"),
    }
    assert [check.constraint for check in result.checks if not check.passed] == [
        "total_budget"
    ]


def test_candidate_exactly_equal_to_budget_passes(builder, validator) -> None:
    request = make_request()
    candidate = builder.build(request)
    exact_constraints = request.constraints.model_copy(
        update={"total_budget": candidate.costs.total}
    )
    assert validator.validate(candidate, exact_constraints).is_valid is True


def test_date_before_allowed_window_fails(builder, validator) -> None:
    request = make_request()
    candidate = builder.build(request)
    early_day = ItineraryDay(date=request.constraints.start_date - timedelta(days=1))
    changed = itinerary_with_days(candidate, [early_day, *candidate.days])
    result = validator.validate(changed, request.constraints)
    assert ViolationCode.DATE_WINDOW_VIOLATION in violation_codes(result)


def test_date_after_allowed_window_fails(builder, validator) -> None:
    request = make_request()
    candidate = builder.build(request)
    late_day = ItineraryDay(date=request.constraints.end_date + timedelta(days=1))
    changed = itinerary_with_days(candidate, [*candidate.days, late_day])
    result = validator.validate(changed, request.constraints)
    assert ViolationCode.DATE_WINDOW_VIOLATION in violation_codes(result)


def test_correct_inclusive_duration_passes(builder, validator) -> None:
    request = make_request()
    result = validator.validate(builder.build(request), request.constraints)
    assert check_for(result, "trip_duration").passed is True


def test_duration_mismatch_fails(builder, validator) -> None:
    request = make_request()
    candidate = builder.build(request)
    changed = itinerary_with_days(candidate, candidate.days[:-1])
    result = validator.validate(changed, request.constraints)
    assert ViolationCode.TRIP_DURATION_MISMATCH in violation_codes(result)


def test_mandatory_destination_present_passes(builder, validator) -> None:
    request = make_request()
    result = validator.validate(builder.build(request), request.constraints)
    assert check_for(result, "mandatory_destinations").passed is True


def test_missing_mandatory_destination_fails(builder, validator) -> None:
    request = make_request()
    candidate = builder.build(request)
    empty_days = [ItineraryDay(date=day.date) for day in candidate.days]
    changed = itinerary_with_days(candidate, empty_days)
    result = validator.validate(changed, request.constraints)
    violation = next(
        item
        for item in result.violations
        if item.code == ViolationCode.MANDATORY_DESTINATION_MISSING
    )
    assert violation.details["missing"] == ["city-goi"]


def test_hotel_at_ceiling_passes(builder, validator) -> None:
    request = make_request(max_hotel=Decimal("8500"))
    result = validator.validate(builder.build(request), request.constraints)
    assert check_for(result, "hotel_price_per_night").passed is True


def test_flight_at_ceiling_passes(builder, validator) -> None:
    request = make_request(max_flight=Decimal("4500"))
    result = validator.validate(builder.build(request), request.constraints)
    assert check_for(result, "flight_price_per_traveler").passed is True


def test_flight_above_ceiling_fails(builder, validator) -> None:
    request = make_request(max_flight=Decimal("4000"))
    result = validator.validate(builder.build(request), request.constraints)
    violation = next(
        item
        for item in result.violations
        if item.code == ViolationCode.FLIGHT_COST_EXCEEDED
    )
    assert violation.details["flight_id"] == "flight-hyd-goi-001"
    assert violation.details["fare_per_traveler"] == Decimal("4500.00")


def test_hotel_above_ceiling_fails(builder, validator) -> None:
    request = make_request(max_hotel=Decimal("8000"))
    result = validator.validate(builder.build(request), request.constraints)
    violation = next(
        item for item in result.violations if item.code == ViolationCode.HOTEL_COST_EXCEEDED
    )
    assert violation.details["hotel_id"] == "hotel-goi-004"
    assert violation.details["rate_per_night"] == Decimal("8500.00")


def test_multiple_hotels_reports_only_violating_stay(builder, validator) -> None:
    request = make_request(
        origin="city-maa",
        destination="city-del",
        additional_destinations=["city-jai"],
        start=date(2027, 2, 10),
        end=date(2027, 2, 14),
        travelers=1,
        max_hotel=Decimal("7500"),
    )
    result = validator.validate(builder.build(request), request.constraints)
    hotel_violations = [
        item for item in result.violations if item.code == ViolationCode.HOTEL_COST_EXCEEDED
    ]
    assert [item.details["hotel_id"] for item in hotel_violations] == ["hotel-del-003"]


def test_daily_travel_time_at_limit_passes(builder, validator) -> None:
    request = make_request(max_travel=80)
    result = validator.validate(builder.build(request), request.constraints)
    assert check_for(result, "maximum_daily_travel_time").passed is True


def test_daily_travel_time_above_limit_fails(builder, validator) -> None:
    request = make_request(max_travel=79)
    result = validator.validate(builder.build(request), request.constraints)
    violation = next(
        item
        for item in result.violations
        if item.code == ViolationCode.DAILY_TRAVEL_TIME_EXCEEDED
    )
    assert violation.details["actual_minutes"] == 80
    assert violation.details["difference_minutes"] == 1


def test_explicitly_infeasible_route_fails(builder, validator) -> None:
    request = make_request(
        origin="city-del",
        destination="city-sml",
        start=date(2027, 1, 20),
        end=date(2027, 1, 23),
        travelers=1,
    )
    candidate = builder.build(request)
    route_item = ItineraryItem(
        id="item-transport-01",
        item_type=ItemType.ROUTE,
        option_id="route-del-sml-train-001",
        title="Explicitly infeasible demo train",
        starts_at=datetime(2027, 1, 20, 8, tzinfo=IST),
        ends_at=datetime(2027, 1, 20, 20, tzinfo=IST),
        cost=Decimal("750"),
        origin_city_id="city-del",
        destination_city_id="city-sml",
        duration_minutes=720,
    )
    days = []
    for day in candidate.days:
        retained = [
            item
            for item in day.items
            if item.item_type not in {ItemType.FLIGHT, ItemType.ROUTE}
        ]
        if day.date == date(2027, 1, 20):
            retained.append(route_item)
        days.append(ItineraryDay(date=day.date, items=retained))
    changed = itinerary_with_days(candidate, days)
    result = validator.validate(changed, request.constraints)
    violation = next(
        item for item in result.violations if item.code == ViolationCode.ROUTE_INFEASIBLE
    )
    assert violation.details["route_id"] == "route-del-sml-train-001"


def test_feasible_recorded_route_passes(builder, validator) -> None:
    request = make_request(
        origin="city-mum",
        allowed_modes=[TransportMode.TRAIN, TransportMode.FLIGHT],
        preferences=SoftPreferences(preferred_transport_modes=[TransportMode.TRAIN]),
    )
    result = validator.validate(builder.build(request), request.constraints)
    assert check_for(result, "route_feasibility").passed is True


def test_multiple_simultaneous_violations_are_all_returned(builder, validator) -> None:
    request = flagship_request(max_hotel=Decimal("8000"), max_travel=60)
    result = validator.validate(builder.build(request), request.constraints)
    codes = violation_codes(result)
    assert ViolationCode.BUDGET_EXCEEDED in codes
    assert ViolationCode.HOTEL_COST_EXCEEDED in codes
    assert ViolationCode.DAILY_TRAVEL_TIME_EXCEEDED in codes


def test_soft_preferences_do_not_enter_feasibility_decision(builder, validator) -> None:
    plain = make_request()
    preferred = make_request(
        preferences=SoftPreferences(
            preferred_airlines=["Demo Air"],
            preferred_activity_categories=["culture"],
            pace="packed",
        )
    )
    candidate = builder.build(plain)
    assert validator.validate(candidate, plain.constraints) == validator.validate(
        candidate, preferred.constraints
    )


def test_same_input_produces_identical_validation_output(builder, validator) -> None:
    request = flagship_request()
    candidate = builder.build(request)
    first = validator.validate(candidate, request.constraints)
    second = validator.validate(candidate, request.constraints)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.validated_at is None


def test_validation_does_not_mutate_itinerary(builder, validator) -> None:
    request = flagship_request()
    candidate = builder.build(request)
    before = candidate.model_dump_json()
    validator.validate(candidate, request.constraints)
    assert candidate.model_dump_json() == before


def test_unknown_component_reference_is_integrity_failure(builder, validator) -> None:
    request = make_request()
    candidate = builder.build(request)
    days = []
    changed_once = False
    for day in candidate.days:
        items = []
        for item in day.items:
            if not changed_once and item.item_type == ItemType.ACTIVITY:
                item = item.model_copy(update={"option_id": "activity-missing"})
                changed_once = True
            items.append(item)
        days.append(ItineraryDay(date=day.date, items=items))
    changed = itinerary_with_days(candidate, days)
    result = validator.validate(changed, request.constraints)
    assert ViolationCode.SCHEDULE_INTEGRITY_ERROR in violation_codes(result)


def test_duplicate_day_is_integrity_failure(builder, validator) -> None:
    request = make_request()
    candidate = builder.build(request)
    duplicate = ItineraryDay(date=candidate.days[-1].date)
    changed = itinerary_with_days(candidate, [*candidate.days, duplicate])
    result = validator.validate(changed, request.constraints)
    assert ViolationCode.SCHEDULE_INTEGRITY_ERROR in violation_codes(result)
