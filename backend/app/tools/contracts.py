from datetime import date
from decimal import Decimal
from typing import Protocol

from app.domain.models import (
    ActivityOption,
    FlightOption,
    HotelOption,
    RouteResult,
    TransportMode,
)


class FlightSearchTool(Protocol):
    def search_flights(
        self,
        origin_city_id: str,
        destination_city_id: str,
        *,
        travel_date: date | None = None,
        max_price: Decimal | None = None,
        travelers: int = 1,
    ) -> list[FlightOption]: ...


class HotelSearchTool(Protocol):
    def search_hotels(
        self,
        city_id: str,
        *,
        max_price_per_night: Decimal | None = None,
        min_rating: Decimal | None = None,
        required_amenities: set[str] | None = None,
        rooms: int = 1,
    ) -> list[HotelOption]: ...


class RouteTool(Protocol):
    def calculate_route(
        self, origin_city_id: str, destination_city_id: str, mode: TransportMode
    ) -> RouteResult: ...


class ActivitySearchTool(Protocol):
    def search_activities(
        self,
        city_id: str,
        *,
        category: str | None = None,
        max_cost: Decimal | None = None,
    ) -> list[ActivityOption]: ...
