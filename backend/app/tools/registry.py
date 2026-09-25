from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.tools.contracts import ActivitySearchTool, FlightSearchTool, HotelSearchTool, RouteTool
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


def create_tool_registry(
    settings: Settings | None = None,
    *,
    catalog: LocalDataCatalog | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> ToolRegistry:
    configured = settings or get_settings()
    if configured.external_providers_enabled:
        raise ValueError("External travel providers are not implemented; use local demo tools")
    local_catalog = catalog or load_catalog(data_dir)
    return ToolRegistry(
        flights=LocalFlightSearchTool(local_catalog),
        hotels=LocalHotelSearchTool(local_catalog),
        routes=LocalRouteTool(local_catalog),
        activities=LocalActivitySearchTool(local_catalog),
        city_ids=frozenset(city.id for city in local_catalog.cities),
    )
