from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.models import DataSource, RouteResult, TransportMode
from app.tools.contracts import ActivitySearchTool, FlightSearchTool, HotelSearchTool, RouteTool
from app.tools.external.errors import ExternalToolError


def _source(tool: object) -> DataSource:
    return getattr(tool, "provider_source")


class FallbackFlightSearchTool:
    def __init__(self, primary: FlightSearchTool, fallback: FlightSearchTool) -> None:
        self._primary, self._fallback = primary, fallback
        self.provider_source = _source(primary)

    def search_flights(self, origin_city_id: str, destination_city_id: str, *, travel_date: date | None = None, max_price: Decimal | None = None, travelers: int = 1):
        try:
            return self._primary.search_flights(origin_city_id, destination_city_id, travel_date=travel_date, max_price=max_price, travelers=travelers)
        except ExternalToolError:
            return [item.model_copy(update={"fallback_from": _source(self._primary)}) for item in self._fallback.search_flights(origin_city_id, destination_city_id, travel_date=travel_date, max_price=max_price, travelers=travelers)]


class FallbackHotelSearchTool:
    def __init__(self, primary: HotelSearchTool, fallback: HotelSearchTool) -> None:
        self._primary, self._fallback = primary, fallback
        self.provider_source = _source(primary)

    def search_hotels(self, city_id: str, *, max_price_per_night: Decimal | None = None, min_rating: Decimal | None = None, required_amenities: set[str] | None = None, rooms: int = 1, check_in: date | None = None, check_out: date | None = None, adults: int | None = None):
        try:
            return self._primary.search_hotels(city_id, max_price_per_night=max_price_per_night, min_rating=min_rating, required_amenities=required_amenities, rooms=rooms, check_in=check_in, check_out=check_out, adults=adults)
        except ExternalToolError:
            return [item.model_copy(update={"fallback_from": _source(self._primary)}) for item in self._fallback.search_hotels(city_id, max_price_per_night=max_price_per_night, min_rating=min_rating, required_amenities=required_amenities, rooms=rooms, check_in=check_in, check_out=check_out, adults=adults)]


class FallbackRouteTool:
    def __init__(self, primary: RouteTool, fallback: RouteTool) -> None:
        self._primary, self._fallback = primary, fallback
        self.provider_source = _source(primary)

    def calculate_route(self, origin_city_id: str, destination_city_id: str, mode: TransportMode) -> RouteResult:
        try:
            return self._primary.calculate_route(origin_city_id, destination_city_id, mode)
        except ExternalToolError:
            result = self._fallback.calculate_route(origin_city_id, destination_city_id, mode)
            route = result.route.model_copy(update={"fallback_from": _source(self._primary)}) if result.route else None
            return result.model_copy(update={"route": route, "fallback_from": _source(self._primary)})


class FallbackActivitySearchTool:
    def __init__(self, primary: ActivitySearchTool, fallback: ActivitySearchTool) -> None:
        self._primary, self._fallback = primary, fallback
        self.provider_source = _source(primary)

    def search_activities(self, city_id: str, *, category: str | None = None, max_cost: Decimal | None = None):
        try:
            return self._primary.search_activities(city_id, category=category, max_cost=max_cost)
        except ExternalToolError:
            return [item.model_copy(update={"fallback_from": _source(self._primary)}) for item in self._fallback.search_activities(city_id, category=category, max_cost=max_cost)]
