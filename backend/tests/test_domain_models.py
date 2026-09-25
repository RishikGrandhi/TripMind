from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.models import (
    ActivityOption,
    FlightOption,
    HotelOption,
    PlanningStatus,
    RouteInfo,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TripState,
)


def valid_constraints(**overrides: object) -> TravelConstraints:
    values = {
        "origin_city_id": "city-hyd",
        "destination_city_id": "city-goi",
        "start_date": date(2027, 1, 15),
        "end_date": date(2027, 1, 18),
        "travelers": 2,
        "total_budget": Decimal("30000.00"),
    }
    values.update(overrides)
    return TravelConstraints(**values)


def test_travel_constraints_accept_valid_values() -> None:
    constraints = valid_constraints()
    assert constraints.travelers == 2
    assert constraints.total_budget == Decimal("30000.00")
    assert constraints.allowed_transport_modes == [TransportMode.FLIGHT]


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1")])
@pytest.mark.parametrize("field", ["total_budget", "max_flight_price", "max_hotel_price_per_night"])
def test_non_positive_monetary_limits_are_rejected(field: str, value: Decimal) -> None:
    with pytest.raises(ValidationError):
        valid_constraints(**{field: value})


def test_invalid_dates_are_rejected() -> None:
    with pytest.raises(ValidationError, match="end_date must be after start_date"):
        valid_constraints(end_date=date(2027, 1, 15))


def test_hard_constraints_and_soft_preferences_are_separate() -> None:
    constraints = valid_constraints()
    preferences = SoftPreferences(preferred_airlines=["Demo Air"], pace="relaxed")
    assert "preferred_airlines" not in type(constraints).model_fields
    assert "total_budget" not in type(preferences).model_fields


def test_option_model_serializes_decimal_and_datetime() -> None:
    departure = datetime(2027, 1, 15, 8, tzinfo=timezone.utc)
    flight = FlightOption(
        id="flight-1",
        origin_city_id="city-hyd",
        destination_city_id="city-goi",
        airline="Demo Air",
        flight_number="DA101",
        departure=departure,
        arrival=departure + timedelta(minutes=80),
        duration_minutes=80,
        price=Decimal("4500.25"),
        available_seats=4,
    )
    serialized = flight.model_dump_json()
    assert '"price":"4500.25"' in serialized
    assert '"departure":"2027-01-15T08:00:00Z"' in serialized


def test_hotel_activity_and_route_models_serialize() -> None:
    options = [
        HotelOption(
            id="hotel-1",
            city_id="city-goi",
            name="Demo Stay",
            price_per_night=Decimal("2800.00"),
            rating=Decimal("4.2"),
            available_rooms=2,
        ),
        ActivityOption(
            id="activity-1",
            city_id="city-goi",
            name="Heritage Walk",
            category="culture",
            duration_minutes=120,
            price=Decimal("500.00"),
        ),
        RouteInfo(
            id="route-1",
            origin_city_id="city-hyd",
            destination_city_id="city-goi",
            mode=TransportMode.CAR,
            duration_minutes=840,
            distance_km=Decimal("650.5"),
            estimated_cost=Decimal("7000.00"),
        ),
    ]
    serialized = [option.model_dump_json() for option in options]
    assert all(option.id in payload for option, payload in zip(options, serialized))


def test_trip_state_round_trip_serialization() -> None:
    state = TripState(
        trip_id="trip-001",
        original_request="Plan a relaxed Goa trip under Rs 30,000",
        constraints=valid_constraints(),
        preferences=SoftPreferences(pace="relaxed"),
        status=PlanningStatus.RECEIVED,
    )
    restored = TripState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.constraints.total_budget == Decimal("30000.00")
