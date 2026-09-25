from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.models import (
    ItemType,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TravelRequest,
    TripState,
)
from app.planning.candidate_builder import CandidateBuildError, CandidateBuilder
from app.tools.local_data import LocalDataCatalog, load_catalog
from app.tools.registry import create_tool_registry

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA_DIR)


@pytest.fixture(scope="module")
def builder(catalog):
    return CandidateBuilder(create_tool_registry(catalog=catalog))


def make_request(
    *,
    request_id: str = "request-single",
    origin: str = "city-hyd",
    destination: str = "city-goi",
    additional_destinations: list[str] | None = None,
    start: date = date(2027, 1, 15),
    end: date = date(2027, 1, 18),
    travelers: int = 2,
    allowed_modes: list[TransportMode] | None = None,
    preferences: SoftPreferences | None = None,
    budget: Decimal = Decimal("30000"),
) -> TravelRequest:
    return TravelRequest(
        request_id=request_id,
        natural_language_request="Deterministic candidate test request",
        constraints=TravelConstraints(
            origin_city_id=origin,
            destination_city_id=destination,
            additional_destination_city_ids=additional_destinations or [],
            start_date=start,
            end_date=end,
            travelers=travelers,
            total_budget=budget,
            allowed_transport_modes=allowed_modes or [TransportMode.FLIGHT],
        ),
        preferences=preferences or SoftPreferences(),
    )


def all_items(itinerary):
    return [item for day in itinerary.days for item in day.items]


def test_single_destination_candidate_construction(builder) -> None:
    candidate = builder.build(make_request())
    items = all_items(candidate)
    assert candidate.id == "candidate-request-single"
    assert [day.date for day in candidate.days] == [
        date(2027, 1, 15),
        date(2027, 1, 16),
        date(2027, 1, 17),
        date(2027, 1, 18),
    ]
    assert any(item.item_type == ItemType.FLIGHT for item in items)
    assert any(item.item_type == ItemType.HOTEL for item in items)
    assert any(item.item_type == ItemType.ACTIVITY for item in items)


def test_mumbai_goa_four_day_flagship_candidate_exceeds_budget(builder) -> None:
    request = make_request(
        request_id="request-flagship",
        origin="city-mum",
        preferences=SoftPreferences(
            preferred_transport_modes=[TransportMode.FLIGHT],
            preferred_hotel_amenities=["pool", "beach_access"],
            preferred_activity_categories=["nature"],
            pace="balanced",
        ),
    )
    candidate = builder.build(request)
    items = all_items(candidate)
    assert next(item for item in items if item.item_type == ItemType.FLIGHT).option_id == (
        "flight-mum-goi-004"
    )
    assert {item.option_id for item in items if item.item_type == ItemType.HOTEL} == {
        "hotel-goi-004"
    }
    assert candidate.costs.total == Decimal("51500.00")
    assert candidate.costs.total > request.constraints.total_budget


def test_multi_destination_candidate_is_sequential(builder) -> None:
    request = make_request(
        request_id="request-multi",
        origin="city-maa",
        destination="city-del",
        additional_destinations=["city-jai"],
        start=date(2027, 2, 10),
        end=date(2027, 2, 14),
        travelers=1,
    )
    candidate = builder.build(request)
    movements = [
        item
        for item in all_items(candidate)
        if item.item_type in {ItemType.FLIGHT, ItemType.ROUTE}
    ]
    assert [(item.origin_city_id, item.destination_city_id) for item in movements] == [
        ("city-maa", "city-del"),
        ("city-del", "city-jai"),
    ]
    assert [item.starts_at.date() for item in movements] == [
        date(2027, 2, 10),
        date(2027, 2, 12),
    ]


def test_hotel_night_calculation_is_exact(builder) -> None:
    candidate = builder.build(make_request())
    hotel_items = [
        item for item in all_items(candidate) if item.item_type == ItemType.HOTEL
    ]
    assert len(hotel_items) == 3
    assert all(item.cost == Decimal("8500.00") for item in hotel_items)
    assert candidate.costs.hotels == Decimal("25500.00")


def test_flight_and_activity_costs_are_per_traveler(builder) -> None:
    candidate = builder.build(make_request())
    assert candidate.costs.flights == Decimal("9000.00")
    assert candidate.costs.activities == Decimal("1000.00")


def test_total_is_exact_decimal_sum(builder) -> None:
    candidate = builder.build(make_request())
    costs = candidate.costs
    assert isinstance(costs.total, Decimal)
    assert costs.total == (
        costs.flights
        + costs.hotels
        + costs.activities
        + costs.local_transport
        + costs.other
    )
    assert costs.total == Decimal("35500.00")


def test_route_movement_is_not_double_counted_as_flight(builder) -> None:
    candidate = builder.build(
        make_request(
            request_id="request-route",
            origin="city-mum",
            allowed_modes=[TransportMode.TRAIN, TransportMode.FLIGHT],
            preferences=SoftPreferences(preferred_transport_modes=[TransportMode.TRAIN]),
        )
    )
    movement = next(
        item
        for item in all_items(candidate)
        if item.item_type in {ItemType.FLIGHT, ItemType.ROUTE}
    )
    assert movement.item_type == ItemType.ROUTE
    assert movement.option_id == "route-mum-goi-train-001"
    assert candidate.costs.local_transport == Decimal("950.00")
    assert candidate.costs.flights == Decimal("0")


def test_days_and_items_are_ordered_and_structurally_consistent(builder) -> None:
    candidate = builder.build(make_request())
    assert [day.date for day in candidate.days] == sorted(day.date for day in candidate.days)
    for day in candidate.days:
        assert [item.starts_at for item in day.items] == sorted(
            item.starts_at for item in day.items
        )
        assert all(item.starts_at.date() == day.date for item in day.items)
        assert all(item.ends_at > item.starts_at for item in day.items)
        assert all(
            item.city_id or (item.origin_city_id and item.destination_city_id)
            for item in day.items
        )
        for position, item in enumerate(day.items):
            assert all(
                item.ends_at <= later.starts_at or later.ends_at <= item.starts_at
                for later in day.items[position + 1 :]
            )


def test_candidate_generation_is_deterministic(builder) -> None:
    request = make_request()
    first = builder.build(request)
    second = builder.build(request)
    assert first.model_dump_json() == second.model_dump_json()


def test_builder_accepts_existing_trip_state(builder) -> None:
    request = make_request(request_id="request-state")
    state = TripState(
        trip_id="trip-state",
        original_request=request.natural_language_request,
        constraints=request.constraints,
        preferences=request.preferences,
    )
    assert builder.build(state).id == "candidate-trip-state"


def test_no_transport_option_fails_cleanly(catalog) -> None:
    empty_transport_catalog = LocalDataCatalog(
        cities=catalog.cities,
        flights=[],
        hotels=catalog.hotels,
        activities=catalog.activities,
        routes=[],
    )
    builder = CandidateBuilder(create_tool_registry(catalog=empty_transport_catalog))
    with pytest.raises(CandidateBuildError, match="No transport option from city-hyd to city-goi"):
        builder.build(make_request())


def test_no_required_hotel_fails_cleanly(catalog) -> None:
    no_goa_hotels = LocalDataCatalog(
        cities=catalog.cities,
        flights=catalog.flights,
        hotels=[hotel for hotel in catalog.hotels if hotel.city_id != "city-goi"],
        activities=catalog.activities,
        routes=catalog.routes,
    )
    builder = CandidateBuilder(create_tool_registry(catalog=no_goa_hotels))
    with pytest.raises(CandidateBuildError, match="No usable hotel option in city-goi"):
        builder.build(make_request())


def test_unknown_city_fails_before_tool_selection(builder) -> None:
    with pytest.raises(CandidateBuildError, match="Unknown city IDs: city-unknown"):
        builder.build(make_request(destination="city-unknown"))


def test_flagship_has_cheaper_unselected_alternatives(builder, catalog) -> None:
    request = make_request(
        request_id="request-alternatives",
        origin="city-mum",
        preferences=SoftPreferences(
            preferred_transport_modes=[TransportMode.FLIGHT],
            preferred_hotel_amenities=["pool", "beach_access"],
        ),
    )
    candidate = builder.build(request)
    registry = create_tool_registry(catalog=catalog)
    flights = registry.flights.search_flights(
        "city-mum", "city-goi", travel_date=date(2027, 1, 15), travelers=2
    )
    hotels = registry.hotels.search_hotels("city-goi", rooms=1)
    cheaper_structural_total = flights[0].price * 2 + hotels[0].price_per_night * 3
    assert candidate.costs.total > Decimal("30000")
    assert cheaper_structural_total == Decimal("14100.00")
    assert cheaper_structural_total <= request.constraints.total_budget
