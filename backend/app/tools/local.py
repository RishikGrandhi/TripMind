from datetime import date
from decimal import Decimal

from app.domain.models import (
    ActivityOption,
    FlightOption,
    HotelOption,
    RouteResult,
    TransportMode,
)
from app.tools.local_data import LocalDataCatalog


def _require_positive(value: int, label: str) -> None:
    if value < 1:
        raise ValueError(f"{label} must be at least 1")


class LocalFlightSearchTool:
    def __init__(self, catalog: LocalDataCatalog) -> None:
        self._flights = tuple(catalog.flights)

    def search_flights(
        self,
        origin_city_id: str,
        destination_city_id: str,
        *,
        travel_date: date | None = None,
        max_price: Decimal | None = None,
        travelers: int = 1,
    ) -> list[FlightOption]:
        _require_positive(travelers, "travelers")
        results = [
            flight
            for flight in self._flights
            if flight.origin_city_id == origin_city_id
            and flight.destination_city_id == destination_city_id
            and (travel_date is None or flight.departure.date() == travel_date)
            and (max_price is None or flight.price <= max_price)
            and flight.available_seats >= travelers
        ]
        return sorted(results, key=lambda item: (item.price, item.duration_minutes, item.departure, item.id))


class LocalHotelSearchTool:
    def __init__(self, catalog: LocalDataCatalog) -> None:
        self._hotels = tuple(catalog.hotels)

    def search_hotels(
        self,
        city_id: str,
        *,
        max_price_per_night: Decimal | None = None,
        min_rating: Decimal | None = None,
        required_amenities: set[str] | None = None,
        rooms: int = 1,
    ) -> list[HotelOption]:
        _require_positive(rooms, "rooms")
        amenities = {item.casefold() for item in (required_amenities or set())}
        results = [
            hotel
            for hotel in self._hotels
            if hotel.city_id == city_id
            and (max_price_per_night is None or hotel.price_per_night <= max_price_per_night)
            and (min_rating is None or hotel.rating >= min_rating)
            and amenities.issubset({item.casefold() for item in hotel.amenities})
            and hotel.available_rooms >= rooms
        ]
        return sorted(results, key=lambda item: (item.price_per_night, -item.rating, item.id))


class LocalRouteTool:
    def __init__(self, catalog: LocalDataCatalog) -> None:
        self._routes = tuple(catalog.routes)

    def calculate_route(
        self, origin_city_id: str, destination_city_id: str, mode: TransportMode
    ) -> RouteResult:
        matches = [
            route
            for route in self._routes
            if route.origin_city_id == origin_city_id
            and route.destination_city_id == destination_city_id
            and route.mode == mode
        ]
        if not matches:
            return RouteResult(
                origin_city_id=origin_city_id,
                destination_city_id=destination_city_id,
                mode=mode,
                feasible=False,
                reason="No demo route is available for this city pair and transport mode.",
            )
        route = min(matches, key=lambda item: (not item.is_feasible, item.duration_minutes, item.estimated_cost, item.id))
        return RouteResult(
            origin_city_id=origin_city_id,
            destination_city_id=destination_city_id,
            mode=mode,
            feasible=route.is_feasible,
            route=route,
            reason=route.unavailable_reason,
        )


class LocalActivitySearchTool:
    def __init__(self, catalog: LocalDataCatalog) -> None:
        self._activities = tuple(catalog.activities)

    def search_activities(
        self,
        city_id: str,
        *,
        category: str | None = None,
        max_cost: Decimal | None = None,
    ) -> list[ActivityOption]:
        normalized_category = category.casefold() if category else None
        results = [
            activity
            for activity in self._activities
            if activity.city_id == city_id
            and (normalized_category is None or activity.category.casefold() == normalized_category)
            and (max_cost is None or activity.price <= max_cost)
        ]
        return sorted(results, key=lambda item: (item.price, item.duration_minutes, item.id))
