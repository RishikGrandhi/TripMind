from datetime import date
from decimal import Decimal
from pathlib import Path
import socket

import pytest

from app.core.config import AppMode, Settings
from app.domain.models import DataSource, TransportMode
from app.tools.local import (
    LocalActivitySearchTool,
    LocalFlightSearchTool,
    LocalHotelSearchTool,
    LocalRouteTool,
)
from app.tools.local_data import load_catalog
from app.tools.registry import create_tool_registry

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def registry():
    return create_tool_registry(catalog=load_catalog(DATA_DIR))


def test_flight_search_returns_mumbai_goa_options(registry) -> None:
    flights = registry.flights.search_flights(
        "city-mum", "city-goi", travel_date=date(2027, 1, 15)
    )
    assert [flight.id for flight in flights] == [
        "flight-mum-goi-001",
        "flight-mum-goi-002",
        "flight-mum-goi-003",
        "flight-mum-goi-004",
    ]
    assert all(flight.duration_minutes > 0 and flight.stops >= 0 for flight in flights)


def test_flight_filters_date_price_and_capacity(registry) -> None:
    assert [
        flight.id
        for flight in registry.flights.search_flights(
            "city-mum",
            "city-goi",
            travel_date=date(2027, 1, 15),
            max_price=Decimal("7000"),
            travelers=7,
        )
    ] == ["flight-mum-goi-001"]
    assert [
        flight.id
        for flight in registry.flights.search_flights(
            "city-mum", "city-goi", travel_date=date(2027, 1, 16)
        )
    ] == ["flight-mum-goi-005"]


def test_hotel_filters_price_rating_amenities_and_capacity(registry) -> None:
    hotels = registry.hotels.search_hotels(
        "city-sml",
        max_price_per_night=Decimal("5500"),
        min_rating=Decimal("4.0"),
        required_amenities={"breakfast", "heater"},
        rooms=3,
    )
    assert [hotel.id for hotel in hotels] == ["hotel-sml-002", "hotel-sml-003"]


def test_route_lookup_returns_stored_distance_time_and_cost(registry) -> None:
    result = registry.routes.calculate_route("city-del", "city-sml", TransportMode.CAR)
    assert result.feasible is True
    assert result.route is not None
    assert result.route.distance_km == Decimal("350.0")
    assert result.route.duration_minutes == 450
    assert result.route.estimated_cost == Decimal("5200.00")


def test_recorded_infeasible_and_unsupported_routes_are_distinct(registry) -> None:
    recorded = registry.routes.calculate_route("city-del", "city-sml", TransportMode.TRAIN)
    unsupported = registry.routes.calculate_route("city-agr", "city-sml", TransportMode.BUS)
    assert recorded.feasible is False and recorded.route is not None
    assert "direct demo train" in recorded.reason
    assert unsupported.feasible is False and unsupported.route is None
    assert "No demo route" in unsupported.reason


def test_activity_filtering(registry) -> None:
    activities = registry.activities.search_activities(
        "city-goi", category="culture", max_cost=Decimal("600")
    )
    assert [activity.id for activity in activities] == ["activity-goi-001"]


def test_empty_searches_return_empty_lists(registry) -> None:
    assert registry.flights.search_flights("city-agr", "city-sml") == []
    assert registry.hotels.search_hotels("city-goi", max_price_per_night=Decimal("100")) == []
    assert registry.activities.search_activities("city-sml", category="diving") == []


def test_result_ordering_is_deterministic(registry) -> None:
    first = registry.hotels.search_hotels("city-goi")
    second = registry.hotels.search_hotels("city-goi")
    assert [item.id for item in first] == [item.id for item in second]
    assert [item.price_per_night for item in first] == sorted(item.price_per_night for item in first)


def test_mumbai_goa_supports_future_budget_replanning(registry) -> None:
    flights = registry.flights.search_flights(
        "city-mum", "city-goi", travel_date=date(2027, 1, 15)
    )
    hotels = registry.hotels.search_hotels("city-goi")
    assert len(flights) >= 4 and len(hotels) >= 4
    expensive_example = flights[-1].price * 2 + hotels[-1].price_per_night * 3
    cheaper_example = flights[0].price * 2 + hotels[0].price_per_night * 3
    assert expensive_example > Decimal("30000")
    assert cheaper_example <= Decimal("30000")


def test_tool_results_identify_local_non_live_provenance(registry) -> None:
    flight = registry.flights.search_flights("city-mum", "city-goi")[0]
    route = registry.routes.calculate_route("city-del", "city-jai", TransportMode.TRAIN)
    assert flight.source == DataSource.LOCAL_DEMO and flight.is_live is False
    assert route.source == DataSource.LOCAL_DEMO and route.is_live is False


def test_demo_tools_make_no_network_calls(monkeypatch: pytest.MonkeyPatch, registry) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket, "socket", fail_network)
    registry.flights.search_flights("city-mum", "city-goi")
    registry.hotels.search_hotels("city-sml")
    registry.routes.calculate_route("city-del", "city-jai", TransportMode.BUS)
    registry.activities.search_activities("city-goi")


def test_registry_resolves_only_local_implementations_in_demo_mode() -> None:
    registry = create_tool_registry(Settings(app_mode=AppMode.DEMO), data_dir=DATA_DIR)
    assert isinstance(registry.flights, LocalFlightSearchTool)
    assert isinstance(registry.hotels, LocalHotelSearchTool)
    assert isinstance(registry.routes, LocalRouteTool)
    assert isinstance(registry.activities, LocalActivitySearchTool)


@pytest.mark.parametrize("travelers", [0, -1])
def test_invalid_capacity_request_is_rejected(registry, travelers: int) -> None:
    with pytest.raises(ValueError, match="travelers must be at least 1"):
        registry.flights.search_flights("city-mum", "city-goi", travelers=travelers)
