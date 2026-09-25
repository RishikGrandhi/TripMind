from pathlib import Path

import pytest

from app.domain.models import City
from app.tools.local_data import LocalDataError, load_catalog, load_records

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def test_demo_catalog_loads_and_validates() -> None:
    catalog = load_catalog(DATA_DIR)
    assert len(catalog.cities) == 8
    assert catalog.flights[0].origin_city_id == "city-mum"
    assert catalog.hotels[0].city_id == "city-goi"
    assert catalog.activities[0].price >= 0
    assert catalog.routes[0].duration_minutes > 0


def test_all_catalog_city_references_are_valid() -> None:
    catalog = load_catalog(DATA_DIR)
    city_ids = {city.id for city in catalog.cities}
    assert all(
        flight.origin_city_id in city_ids and flight.destination_city_id in city_ids
        for flight in catalog.flights
    )
    assert all(hotel.city_id in city_ids for hotel in catalog.hotels)
    assert all(activity.city_id in city_ids for activity in catalog.activities)
    assert all(
        route.origin_city_id in city_ids and route.destination_city_id in city_ids
        for route in catalog.routes
    )


def test_malformed_local_data_is_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / "cities.json"
    malformed.write_text('[{"id": "broken", "name": "Missing fields"}]', encoding="utf-8")
    with pytest.raises(LocalDataError, match="Schema validation failed"):
        load_records(malformed, City)


def test_invalid_json_is_rejected_with_clear_error(tmp_path: Path) -> None:
    malformed = tmp_path / "cities.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(LocalDataError, match="Invalid JSON"):
        load_records(malformed, City)
