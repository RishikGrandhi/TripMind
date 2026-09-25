from dataclasses import dataclass
from pathlib import Path

from app.core.config import AppMode, Settings, get_settings
from app.tools.contracts import (
    ActivitySearchTool,
    FlightSearchTool,
    HotelSearchTool,
    RouteTool,
    WeatherTool,
)
from app.tools.external import (
    GeoapifyActivitySearchTool,
    GeoapifyRouteTool,
    OpenWeatherTool,
    SerpApiFlightSearchTool,
    StayingApiHotelSearchTool,
)
from app.tools.external.fallback import (
    FallbackActivitySearchTool,
    FallbackFlightSearchTool,
    FallbackHotelSearchTool,
    FallbackRouteTool,
)
from app.tools.local import (
    LocalActivitySearchTool,
    LocalFlightSearchTool,
    LocalHotelSearchTool,
    LocalRouteTool,
)
from app.tools.local_data import LocalDataCatalog, load_catalog

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class ToolRegistry:
    flights: FlightSearchTool
    hotels: HotelSearchTool
    routes: RouteTool
    activities: ActivitySearchTool
    city_ids: frozenset[str]
    weather: WeatherTool | None = None


def create_tool_registry(
    settings: Settings | None = None,
    *,
    catalog: LocalDataCatalog | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> ToolRegistry:
    configured = settings or get_settings()
    local_catalog = catalog or load_catalog(data_dir)
    local_flights = LocalFlightSearchTool(local_catalog)
    local_hotels = LocalHotelSearchTool(local_catalog)
    local_routes = LocalRouteTool(local_catalog)
    local_activities = LocalActivitySearchTool(local_catalog)
    if configured.external_providers_enabled != (configured.app_mode == AppMode.LIVE):
        raise ValueError("Live providers require APP_MODE=live and EXTERNAL_PROVIDERS_ENABLED=true")
    if configured.app_mode != AppMode.LIVE:
        return ToolRegistry(
            flights=local_flights,
            hotels=local_hotels,
            routes=local_routes,
            activities=local_activities,
            city_ids=frozenset(city.id for city in local_catalog.cities),
        )

    live_flights = SerpApiFlightSearchTool(
        local_catalog, configured.serpapi_api_key, timeout=configured.serpapi_timeout_seconds
    )
    live_hotels = StayingApiHotelSearchTool(
        local_catalog,
        configured.staying_api_key,
        timeout=configured.staying_api_timeout_seconds,
        max_polls=configured.staying_api_max_polls,
        poll_interval=configured.staying_api_poll_interval_seconds,
    )
    live_routes = GeoapifyRouteTool(
        local_catalog,
        configured.geoapify_routing_api_key,
        timeout=configured.geoapify_timeout_seconds,
        cost_per_km=configured.live_route_cost_per_km,
    )
    live_activities = GeoapifyActivitySearchTool(
        local_catalog,
        configured.geoapify_places_api_key,
        timeout=configured.geoapify_timeout_seconds,
    )
    if configured.live_tool_fallback_to_local:
        flights: FlightSearchTool = FallbackFlightSearchTool(live_flights, local_flights)
        hotels: HotelSearchTool = FallbackHotelSearchTool(live_hotels, local_hotels)
        routes: RouteTool = FallbackRouteTool(live_routes, local_routes)
        activities: ActivitySearchTool = FallbackActivitySearchTool(
            live_activities, local_activities
        )
    else:
        flights, hotels, routes, activities = (
            live_flights,
            live_hotels,
            live_routes,
            live_activities,
        )
    return ToolRegistry(
        flights=flights,
        hotels=hotels,
        routes=routes,
        activities=activities,
        city_ids=frozenset(city.id for city in local_catalog.cities),
        weather=OpenWeatherTool(
            local_catalog,
            configured.openweather_api_key,
            timeout=configured.openweather_timeout_seconds,
        ),
    )
