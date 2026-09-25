import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.domain.models import ActivityOption, City, FlightOption, HotelOption, RouteInfo


class LocalDataError(ValueError):
    """Raised when a local data file is missing, malformed, or fails schema validation."""


ModelT = TypeVar("ModelT", bound=BaseModel)


def load_records(path: Path, model_type: type[ModelT]) -> list[ModelT]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LocalDataError(f"Local data file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LocalDataError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    try:
        return TypeAdapter(list[model_type]).validate_python(raw)
    except ValidationError as exc:
        raise LocalDataError(f"Schema validation failed for {path}: {exc}") from exc


@dataclass(frozen=True)
class LocalDataCatalog:
    cities: list[City]
    flights: list[FlightOption]
    hotels: list[HotelOption]
    activities: list[ActivityOption]
    routes: list[RouteInfo]


def load_catalog(data_dir: Path) -> LocalDataCatalog:
    catalog = LocalDataCatalog(
        cities=load_records(data_dir / "cities.json", City),
        flights=load_records(data_dir / "flights.json", FlightOption),
        hotels=load_records(data_dir / "hotels.json", HotelOption),
        activities=load_records(data_dir / "activities.json", ActivityOption),
        routes=load_records(data_dir / "routes.json", RouteInfo),
    )
    _validate_catalog_references(catalog)
    return catalog


def _validate_catalog_references(catalog: LocalDataCatalog) -> None:
    city_ids = {city.id for city in catalog.cities}
    if len(city_ids) != len(catalog.cities):
        raise LocalDataError("Duplicate city IDs in local demo data")

    collections = {
        "flight": catalog.flights,
        "hotel": catalog.hotels,
        "activity": catalog.activities,
        "route": catalog.routes,
    }
    for label, records in collections.items():
        record_ids = [record.id for record in records]
        if len(record_ids) != len(set(record_ids)):
            raise LocalDataError(f"Duplicate {label} IDs in local demo data")

    for flight in catalog.flights:
        _require_city(city_ids, flight.origin_city_id, "flight", flight.id)
        _require_city(city_ids, flight.destination_city_id, "flight", flight.id)
    for hotel in catalog.hotels:
        _require_city(city_ids, hotel.city_id, "hotel", hotel.id)
    for activity in catalog.activities:
        _require_city(city_ids, activity.city_id, "activity", activity.id)
    for route in catalog.routes:
        _require_city(city_ids, route.origin_city_id, "route", route.id)
        _require_city(city_ids, route.destination_city_id, "route", route.id)


def _require_city(city_ids: set[str], city_id: str, record_type: str, record_id: str) -> None:
    if city_id not in city_ids:
        raise LocalDataError(
            f"Unknown city reference {city_id!r} in {record_type} record {record_id!r}"
        )
