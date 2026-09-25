from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.models import (
    DataSource,
    ItemType,
    PriceSource,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TravelRequest,
    TripState,
)
from app.planning.candidate_builder import CandidateBuilder
from app.planning.validation import ConstraintValidator
from app.tools.external import (
    ExternalToolError,
    GeoapifyActivitySearchTool,
    GeoapifyRouteTool,
    OpenWeatherTool,
    SerpApiFlightSearchTool,
    StayingApiHotelSearchTool,
)
from app.tools.external.fallback import FallbackFlightSearchTool
from app.tools.external.http import HttpResponse
from app.tools.local import LocalFlightSearchTool
from app.tools.local_data import load_catalog
from app.tools.registry import create_tool_registry
from app.planning.agent_loop import SearchFlightsParameters


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA_DIR)


def response(payload: dict, status: int = 200):
    def transport(method, url, headers, body, timeout):
        return HttpResponse(status, payload)

    return transport


def test_serpapi_normalizes_only_authoritative_inr_quote(catalog) -> None:
    payload = {
        "best_flights": [
            {
                "price": 4875,
                "total_duration": 75,
                "flights": [
                    {
                        "departure_airport": {"time": "2027-01-15 06:00"},
                        "arrival_airport": {"time": "2027-01-15 07:15"},
                        "airline": "Live Air",
                        "flight_number": "LA 101",
                    }
                ],
            },
            {"total_duration": 80, "flights": []},
        ]
    }
    tool = SerpApiFlightSearchTool(catalog, "secret", transport=response(payload))

    results = tool.search_flights(
        "city-mum", "city-goi", travel_date=date(2027, 1, 15), travelers=2
    )

    assert len(results) == 1
    assert results[0].price == Decimal("4875")
    assert results[0].available_seats is None
    assert results[0].source == DataSource.SERPAPI
    assert results[0].is_live is True
    assert results[0].price_source == PriceSource.LIVE_QUOTE


def test_serpapi_request_is_fixed_and_uses_inr(catalog) -> None:
    observed = {}

    def transport(method, url, headers, body, timeout):
        observed.update(method=method, url=url, timeout=timeout)
        return HttpResponse(200, {"best_flights": [], "other_flights": []})

    SerpApiFlightSearchTool(catalog, "secret", timeout=3, transport=transport).search_flights(
        "city-mum", "city-goi", travel_date=date(2027, 1, 15)
    )
    parsed = urlparse(observed["url"])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "serpapi.com"
    assert query["engine"] == ["google_flights"]
    assert query["currency"] == ["INR"]
    assert observed["timeout"] == 3


def test_serpapi_no_results_is_empty(catalog) -> None:
    tool = SerpApiFlightSearchTool(catalog, "secret", transport=response({}))
    assert tool.search_flights(
        "city-mum", "city-goi", travel_date=date(2027, 1, 15)
    ) == []


def test_serpapi_malformed_payload_is_structured(catalog) -> None:
    tool = SerpApiFlightSearchTool(
        catalog, "secret", transport=response({"best_flights": {}})
    )
    with pytest.raises(ExternalToolError) as captured:
        tool.search_flights("city-mum", "city-goi", travel_date=date(2027, 1, 15))
    assert captured.value.code == "malformed_response"


def test_serpapi_rate_limit_is_structured(catalog) -> None:
    def rate_limited(*_):
        raise ExternalToolError("http", "rate_limited", "Provider returned HTTP 429")

    tool = SerpApiFlightSearchTool(catalog, "secret", transport=rate_limited)
    with pytest.raises(ExternalToolError) as captured:
        tool.search_flights("city-mum", "city-goi", travel_date=date(2027, 1, 15))
    assert captured.value.provider == "serpapi"
    assert captured.value.code == "rate_limited"


def test_missing_live_key_fails_without_network(catalog) -> None:
    tool = SerpApiFlightSearchTool(
        catalog,
        None,
        transport=lambda *_: pytest.fail("transport must not run without a key"),
    )
    with pytest.raises(ExternalToolError, match="missing_api_key"):
        tool.search_flights("city-mum", "city-goi", travel_date=date(2027, 1, 15))


def test_live_timeout_has_structured_secret_free_error(catalog) -> None:
    def timeout(*_):
        raise TimeoutError("secret should not escape")

    tool = SerpApiFlightSearchTool(catalog, "actual-secret", transport=timeout)
    with pytest.raises(ExternalToolError) as captured:
        tool.search_flights("city-mum", "city-goi", travel_date=date(2027, 1, 15))
    assert captured.value.code == "timeout"
    assert "actual-secret" not in str(captured.value)


def test_stayingapi_normalizes_price_and_scaled_rating(catalog) -> None:
    payload = {
        "data": [
            {
                "id": "property-1",
                "name": "Live Goa Stay",
                "guestRating": 8.8,
                "ratingScale": 10,
                "amenities": ["wifi", "pool"],
                "price": {
                    "currency": "INR",
                    "nightlyPrice": 3200,
                    "totalPrice": 9600,
                    "nights": 3,
                },
            }
        ]
    }
    tool = StayingApiHotelSearchTool(catalog, "secret", transport=response(payload))
    results = tool.search_hotels(
        "city-goi",
        check_in=date(2027, 1, 15),
        check_out=date(2027, 1, 18),
        adults=2,
    )
    assert results[0].rating == Decimal("4.40")
    assert results[0].available_rooms is None
    assert results[0].source == DataSource.STAYINGAPI
    assert results[0].price_source == PriceSource.LIVE_QUOTE


def test_stayingapi_async_polling_is_bounded_and_completes(catalog) -> None:
    replies = iter(
        [
            HttpResponse(202, {"data": {"jobId": "job-1"}}),
            HttpResponse(200, {"data": {"status": "running"}}),
            HttpResponse(
                200,
                {
                    "data": {
                        "status": "completed",
                        "result": [
                            {
                                "id": "p2",
                                "name": "Async Stay",
                                "price": {"currency": "INR", "nightlyPrice": 2500},
                            }
                        ],
                    }
                },
            ),
        ]
    )
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        return next(replies)

    tool = StayingApiHotelSearchTool(
        catalog,
        "secret",
        max_polls=2,
        poll_interval=0,
        transport=transport,
        sleeper=lambda _: None,
    )
    results = tool.search_hotels(
        "city-goi", check_in=date(2027, 1, 15), check_out=date(2027, 1, 16)
    )
    assert results[0].name == "Async Stay"
    assert len(calls) == 3


def test_stayingapi_polling_limit_is_enforced(catalog) -> None:
    calls = 0

    def transport(method, url, headers, body, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return HttpResponse(202, {"data": {"jobId": "job-1"}})
        return HttpResponse(200, {"data": {"status": "running"}})

    tool = StayingApiHotelSearchTool(
        catalog, "secret", max_polls=2, poll_interval=0, transport=transport
    )
    with pytest.raises(ExternalToolError) as captured:
        tool.search_hotels(
            "city-goi", check_in=date(2027, 1, 15), check_out=date(2027, 1, 16)
        )
    assert captured.value.code == "polling_timeout"
    assert calls == 3


def test_stayingapi_no_results_is_empty(catalog) -> None:
    tool = StayingApiHotelSearchTool(
        catalog, "secret", transport=response({"data": []})
    )
    assert tool.search_hotels(
        "city-goi", check_in=date(2027, 1, 15), check_out=date(2027, 1, 16)
    ) == []


def test_stayingapi_malformed_result_is_structured(catalog) -> None:
    tool = StayingApiHotelSearchTool(
        catalog, "secret", transport=response({"data": "not-a-list"})
    )
    with pytest.raises(ExternalToolError) as captured:
        tool.search_hotels(
            "city-goi", check_in=date(2027, 1, 15), check_out=date(2027, 1, 16)
        )
    assert captured.value.code == "malformed_response"


def test_geoapify_places_remains_unpriced_advisory_data(catalog) -> None:
    payload = {
        "features": [
            {"properties": {"place_id": "place-1", "name": "Live Heritage Site"}}
        ]
    }
    tool = GeoapifyActivitySearchTool(catalog, "secret", transport=response(payload))
    result = tool.search_activities("city-goi", category="sightseeing")[0]
    assert result.price is None
    assert result.duration_minutes is None
    assert result.price_source == PriceSource.UNKNOWN
    assert result.is_live is True
    assert tool.search_activities("city-goi", max_cost=Decimal("500")) == []


def test_geoapify_places_category_mapping_and_no_results(catalog) -> None:
    observed = {}

    def transport(method, url, headers, body, timeout):
        observed["query"] = parse_qs(urlparse(url).query)
        return HttpResponse(200, {"features": []})

    tool = GeoapifyActivitySearchTool(catalog, "secret", transport=transport)
    assert tool.search_activities("city-goi", category="museum") == []
    assert observed["query"]["categories"] == ["entertainment.museum"]


def test_geoapify_places_malformed_payload_is_structured(catalog) -> None:
    tool = GeoapifyActivitySearchTool(
        catalog, "secret", transport=response({"features": {}})
    )
    with pytest.raises(ExternalToolError) as captured:
        tool.search_activities("city-goi")
    assert captured.value.code == "malformed_response"


def test_geoapify_route_uses_configured_explicit_cost_estimate(catalog) -> None:
    tool = GeoapifyRouteTool(
        catalog,
        "secret",
        cost_per_km=Decimal("10"),
        transport=response({"results": [{"distance": 100500, "time": 7200}]}),
    )
    result = tool.calculate_route("city-mum", "city-goi", TransportMode.CAR)
    assert result.feasible is True
    assert result.route is not None
    assert result.route.distance_km == Decimal("100.50")
    assert result.route.estimated_cost == Decimal("1005.00")
    assert result.route.is_estimate is True
    assert result.route.price_source == PriceSource.ESTIMATED


def test_geoapify_route_no_result_is_explicitly_infeasible(catalog) -> None:
    tool = GeoapifyRouteTool(
        catalog, "secret", transport=response({"results": []})
    )
    result = tool.calculate_route("city-mum", "city-goi", TransportMode.CAR)
    assert result.feasible is False
    assert result.route is None
    assert result.source == DataSource.GEOAPIFY


def test_geoapify_route_does_not_accept_provider_fare(catalog) -> None:
    tool = GeoapifyRouteTool(
        catalog,
        "secret",
        cost_per_km=Decimal("2"),
        transport=response(
            {"results": [{"distance": 1000, "time": 600, "price": 99999}]}
        ),
    )
    result = tool.calculate_route("city-mum", "city-goi", TransportMode.CAR)
    assert result.route is not None
    assert result.route.estimated_cost == Decimal("2.00")


def test_openweather_horizon_returns_unavailable_without_network(catalog) -> None:
    tool = OpenWeatherTool(
        catalog,
        "secret",
        today=lambda: date(2027, 1, 1),
        transport=lambda *_: pytest.fail("out-of-horizon request must not call provider"),
    )
    result = tool.get_forecast("city-goi", date(2027, 1, 10))
    assert result.weather_available is False
    assert result.reason == "outside_forecast_horizon"


def test_openweather_normalizes_matching_three_hour_entries(catalog) -> None:
    timestamp = int(datetime(2027, 1, 3, 6, tzinfo=UTC).timestamp())
    payload = {
        "list": [
            {
                "dt": timestamp,
                "main": {"temp": 29.5, "feels_like": 31, "humidity": 70},
                "weather": [{"description": "light rain"}],
                "pop": 0.4,
                "rain": {"3h": 1.2},
                "wind": {"speed": 4.5},
            }
        ]
    }
    tool = OpenWeatherTool(
        catalog,
        "secret",
        today=lambda: date(2027, 1, 1),
        transport=response(payload),
    )
    result = tool.get_forecast("city-goi", date(2027, 1, 3))
    assert result.weather_available is True
    assert result.forecasts[0].temperature_c == Decimal("29.5")
    assert result.forecasts[0].rain_mm == Decimal("1.2")


def test_openweather_malformed_payload_is_structured(catalog) -> None:
    tool = OpenWeatherTool(
        catalog,
        "secret",
        today=lambda: date(2027, 1, 1),
        transport=response({"list": {}}),
    )
    with pytest.raises(ExternalToolError) as captured:
        tool.get_forecast("city-goi", date(2027, 1, 2))
    assert captured.value.code == "malformed_response"


def test_openweather_provider_failure_is_structured(catalog) -> None:
    def unavailable(*_):
        raise ExternalToolError("http", "unavailable", "offline")

    tool = OpenWeatherTool(
        catalog,
        "secret",
        today=lambda: date(2027, 1, 1),
        transport=unavailable,
    )
    with pytest.raises(ExternalToolError) as captured:
        tool.get_forecast("city-goi", date(2027, 1, 2))
    assert captured.value.provider == "openweather"
    assert captured.value.code == "unavailable"


def test_registry_defaults_remain_offline(catalog) -> None:
    registry = create_tool_registry(Settings(), catalog=catalog)
    assert registry.weather is None
    assert registry.flights.__class__.__name__ == "LocalFlightSearchTool"


def test_registry_requires_both_live_switches(catalog) -> None:
    with pytest.raises(ValueError, match="APP_MODE=live"):
        create_tool_registry(
            Settings(app_mode="demo", external_providers_enabled=True), catalog=catalog
        )
    with pytest.raises(ValueError, match="APP_MODE=live"):
        create_tool_registry(
            Settings(app_mode="live", external_providers_enabled=False), catalog=catalog
        )


def test_registry_live_mode_selects_all_live_adapters(catalog) -> None:
    registry = create_tool_registry(
        Settings(app_mode="live", external_providers_enabled=True), catalog=catalog
    )
    assert isinstance(registry.flights, SerpApiFlightSearchTool)
    assert isinstance(registry.hotels, StayingApiHotelSearchTool)
    assert isinstance(registry.routes, GeoapifyRouteTool)
    assert isinstance(registry.activities, GeoapifyActivitySearchTool)
    assert isinstance(registry.weather, OpenWeatherTool)


def test_local_fallback_is_explicitly_labeled(catalog) -> None:
    failing = SerpApiFlightSearchTool(
        catalog,
        "secret",
        transport=lambda *_: (_ for _ in ()).throw(TimeoutError()),
    )
    tool = FallbackFlightSearchTool(failing, LocalFlightSearchTool(catalog))
    results = tool.search_flights(
        "city-mum", "city-goi", travel_date=date(2027, 1, 15)
    )
    assert results
    assert results[0].source == DataSource.LOCAL_DEMO
    assert results[0].is_live is False
    assert results[0].fallback_from == DataSource.SERPAPI


def test_live_failure_does_not_fallback_by_default(catalog) -> None:
    tool = SerpApiFlightSearchTool(
        catalog, "secret", transport=lambda *_: (_ for _ in ()).throw(TimeoutError())
    )
    with pytest.raises(ExternalToolError):
        tool.search_flights("city-mum", "city-goi", travel_date=date(2027, 1, 15))


def test_llm_parameters_cannot_supply_provider_url() -> None:
    with pytest.raises(ValidationError):
        SearchFlightsParameters.model_validate(
            {
                "origin_city_id": "city-mum",
                "destination_city_id": "city-goi",
                "travel_date": "2027-01-15",
                "travelers": 1,
                "provider_url": "https://attacker.invalid",
            }
        )


def test_unknown_city_is_rejected_before_http(catalog) -> None:
    tool = SerpApiFlightSearchTool(
        catalog,
        "secret",
        transport=lambda *_: pytest.fail("invalid city must not reach HTTP"),
    )
    with pytest.raises(ExternalToolError) as captured:
        tool.search_flights(
            "https://attacker.invalid", "city-goi", travel_date=date(2027, 1, 15)
        )
    assert captured.value.code == "unsupported_city"


def test_deterministic_validator_resolves_live_candidates_from_trip_state(catalog) -> None:
    constraints = TravelConstraints(
        origin_city_id="city-mum",
        destination_city_id="city-goi",
        start_date=date(2027, 1, 15),
        end_date=date(2027, 1, 18),
        total_budget=Decimal("100000"),
    )
    request = TravelRequest(
        request_id="live-validation",
        natural_language_request="test",
        constraints=constraints,
        preferences=SoftPreferences(),
    )
    itinerary = CandidateBuilder(
        create_tool_registry(Settings(), catalog=catalog)
    ).build(request)
    flight_item = next(
        item for day in itinerary.days for item in day.items if item.item_type == ItemType.FLIGHT
    )
    hotel_items = [
        item for day in itinerary.days for item in day.items if item.item_type == ItemType.HOTEL
    ]
    local_flight = next(item for item in catalog.flights if item.id == flight_item.option_id)
    local_hotel = next(item for item in catalog.hotels if item.id == hotel_items[0].option_id)
    live_flight = local_flight.model_copy(
        update={"id": "live-flight-id", "source": DataSource.SERPAPI, "is_live": True}
    )
    live_hotel = local_hotel.model_copy(
        update={"id": "live-hotel-id", "source": DataSource.STAYINGAPI, "is_live": True}
    )
    flight_item.option_id = live_flight.id
    for item in hotel_items:
        item.option_id = live_hotel.id
    state = TripState(
        trip_id="live-validation",
        original_request="test",
        constraints=constraints,
        preferences=SoftPreferences(),
        current_itinerary=itinerary,
        flight_candidates=[live_flight],
        hotel_candidates=[live_hotel],
    )
    assert ConstraintValidator(catalog).validate_state(state).is_valid is True
