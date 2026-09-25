from __future__ import annotations

import hashlib
import time
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Iterable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from app.domain.models import (
    ActivityOption,
    City,
    DataSource,
    FlightOption,
    HotelOption,
    PriceSource,
    RouteInfo,
    RouteResult,
    TransportMode,
    WeatherForecast,
    WeatherResult,
)
from app.tools.external.errors import ExternalToolError
from app.tools.external.http import HttpResponse, HttpTransport, request_json
from app.tools.local_data import LocalDataCatalog


def _key(value: SecretStr | str | None, provider: str) -> str:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not raw or not raw.strip():
        raise ExternalToolError(provider, "missing_api_key", "API key is not configured")
    return raw.strip()


def _cities(catalog: LocalDataCatalog) -> dict[str, City]:
    return {city.id: city for city in catalog.cities}


def _city(mapping: dict[str, City], city_id: str, provider: str) -> City:
    city = mapping.get(city_id)
    if city is None:
        raise ExternalToolError(provider, "unsupported_city", f"Unknown city ID: {city_id}")
    return city


def _number(value: object, *, provider: str, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ExternalToolError(provider, "malformed_response", f"Missing numeric field: {field}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExternalToolError(provider, "malformed_response", f"Invalid numeric field: {field}") from exc
    if not number.is_finite():
        raise ExternalToolError(provider, "malformed_response", f"Invalid numeric field: {field}")
    return number


def _stable_id(prefix: str, values: Iterable[object]) -> str:
    digest = hashlib.sha256("|".join(str(value) for value in values).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _city_timezone(city: City):
    # The checked-in catalog is India-only. This fixed-offset path keeps the
    # Windows demo environment independent of an optional tzdata wheel.
    if city.timezone == "Asia/Kolkata":
        return timezone(timedelta(hours=5, minutes=30), name="IST")
    try:
        return ZoneInfo(city.timezone)
    except Exception as exc:
        raise ExternalToolError(
            "serpapi", "unsupported_timezone", f"Timezone data is unavailable for {city.id}"
        ) from exc


def _call(
    transport: HttpTransport,
    provider: str,
    method: str,
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> HttpResponse:
    try:
        return transport(method, url, headers, None, timeout)
    except ExternalToolError as exc:
        if exc.provider == "http":
            raise ExternalToolError(provider, exc.code, exc.message) from exc
        raise
    except (TimeoutError, OSError) as exc:
        raise ExternalToolError(provider, "timeout", "Provider request timed out") from exc


class SerpApiFlightSearchTool:
    provider_source = DataSource.SERPAPI
    _URL = "https://serpapi.com/search"

    def __init__(self, catalog: LocalDataCatalog, api_key: SecretStr | str | None, *, timeout: float = 10, transport: HttpTransport = request_json) -> None:
        self._cities = _cities(catalog)
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def search_flights(self, origin_city_id: str, destination_city_id: str, *, travel_date: date | None = None, max_price: Decimal | None = None, travelers: int = 1) -> list[FlightOption]:
        if travel_date is None:
            raise ValueError("travel_date is required for live flight search")
        if travelers < 1:
            raise ValueError("travelers must be at least 1")
        origin = _city(self._cities, origin_city_id, "serpapi")
        destination = _city(self._cities, destination_city_id, "serpapi")
        if not origin.airport_code or not destination.airport_code:
            raise ExternalToolError("serpapi", "unsupported_city", "Both cities require airport codes")
        params = {
            "engine": "google_flights",
            "departure_id": origin.airport_code,
            "arrival_id": destination.airport_code,
            "outbound_date": travel_date.isoformat(),
            "type": "2",
            "currency": "INR",
            "adults": str(travelers),
            "api_key": _key(self._api_key, "serpapi"),
        }
        payload = _call(self._transport, "serpapi", "GET", f"{self._URL}?{urlencode(params)}", {}, self._timeout).payload
        if payload.get("error"):
            raise ExternalToolError("serpapi", "provider_error", "Flight provider returned an error")
        raw_options = [*self._list(payload, "best_flights"), *self._list(payload, "other_flights")]
        results: list[FlightOption] = []
        for raw in raw_options:
            try:
                option = self._normalize(raw, origin, destination)
            except (ExternalToolError, KeyError, TypeError, ValueError):
                continue
            if max_price is None or option.price <= max_price:
                results.append(option)
        return sorted(results, key=lambda item: (item.price, item.duration_minutes, item.departure, item.id))

    @staticmethod
    def _list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
        value = payload.get(key, [])
        if not isinstance(value, list):
            raise ExternalToolError("serpapi", "malformed_response", f"{key} must be a list")
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _normalize(raw: dict[str, object], origin: City, destination: City) -> FlightOption:
        legs = raw.get("flights")
        if not isinstance(legs, list) or not legs or not all(isinstance(leg, dict) for leg in legs):
            raise ValueError("missing legs")
        first, last = legs[0], legs[-1]
        departure_airport = first.get("departure_airport")
        arrival_airport = last.get("arrival_airport")
        if not isinstance(departure_airport, dict) or not isinstance(arrival_airport, dict):
            raise ValueError("missing airports")
        departure = datetime.strptime(str(departure_airport["time"]), "%Y-%m-%d %H:%M").replace(tzinfo=_city_timezone(origin))
        arrival = datetime.strptime(str(arrival_airport["time"]), "%Y-%m-%d %H:%M").replace(tzinfo=_city_timezone(destination))
        duration = int(_number(raw.get("total_duration"), provider="serpapi", field="total_duration"))
        price = _number(raw.get("price"), provider="serpapi", field="price")
        if duration <= 0 or price <= 0:
            raise ValueError("non-positive quote")
        airlines = list(dict.fromkeys(str(leg.get("airline", "")).strip() for leg in legs if str(leg.get("airline", "")).strip()))
        numbers = [str(leg.get("flight_number", "")).strip() for leg in legs if str(leg.get("flight_number", "")).strip()]
        if not airlines or not numbers:
            raise ValueError("missing carrier identity")
        identifier = _stable_id("serp-flight", [origin.id, destination.id, departure.isoformat(), *numbers, price])
        return FlightOption(id=identifier, origin_city_id=origin.id, destination_city_id=destination.id, airline=" / ".join(airlines), flight_number=" / ".join(numbers), departure=departure, arrival=arrival, duration_minutes=duration, stops=max(0, len(legs) - 1), price=price, available_seats=None, source=DataSource.SERPAPI, is_live=True, price_source=PriceSource.LIVE_QUOTE)


class StayingApiHotelSearchTool:
    provider_source = DataSource.STAYINGAPI
    _URL = "https://api.stayingapi.com/v1"

    def __init__(self, catalog: LocalDataCatalog, api_key: SecretStr | str | None, *, timeout: float = 12, max_polls: int = 6, poll_interval: float = 1, transport: HttpTransport = request_json, sleeper: Callable[[float], None] = time.sleep) -> None:
        self._cities = _cities(catalog)
        self._api_key = api_key
        self._timeout = timeout
        self._max_polls = max_polls
        self._poll_interval = poll_interval
        self._transport = transport
        self._sleeper = sleeper

    def search_hotels(self, city_id: str, *, max_price_per_night: Decimal | None = None, min_rating: Decimal | None = None, required_amenities: set[str] | None = None, rooms: int = 1, check_in: date | None = None, check_out: date | None = None, adults: int | None = None) -> list[HotelOption]:
        if check_in is None or check_out is None:
            raise ValueError("check_in and check_out are required for live hotel search")
        if check_out <= check_in or rooms < 1 or (adults is not None and adults < 1):
            raise ValueError("invalid live hotel search dates or occupancy")
        city = _city(self._cities, city_id, "stayingapi")
        params: dict[str, str] = {"location": city.name, "checkIn": check_in.isoformat(), "checkOut": check_out.isoformat(), "rooms": str(rooms), "adults": str(adults or rooms), "currency": "INR", "limit": "20"}
        if max_price_per_night is not None:
            params["priceMax"] = str(max_price_per_night)
        if min_rating is not None:
            params["minGuestRating"] = str(min_rating)
        if required_amenities:
            params["amenities"] = ",".join(sorted(required_amenities))
        headers = {"Authorization": f"Bearer {_key(self._api_key, 'stayingapi')}", "Accept": "application/json"}
        response = _call(self._transport, "stayingapi", "GET", f"{self._URL}/search?{urlencode(params)}", headers, self._timeout)
        data = response.payload.get("data")
        if response.status_code == 202 or isinstance(data, dict) and data.get("jobId"):
            job_id = data.get("jobId") if isinstance(data, dict) else None
            if not job_id:
                raise ExternalToolError("stayingapi", "malformed_response", "Async search omitted jobId")
            data = self._poll(str(job_id), headers)
        if not isinstance(data, list):
            raise ExternalToolError("stayingapi", "malformed_response", "Hotel response data must be a list")
        raw_hotels = data
        results = [option for raw in raw_hotels if isinstance(raw, dict) and (option := self._normalize(raw, city_id)) is not None]
        return sorted(results, key=lambda item: (item.price_per_night, -(item.rating or Decimal("0")), item.id))

    def _poll(self, job_id: str, headers: dict[str, str]) -> object:
        for attempt in range(self._max_polls):
            if attempt and self._poll_interval:
                self._sleeper(self._poll_interval)
            payload = _call(self._transport, "stayingapi", "GET", f"{self._URL}/jobs/{job_id}", headers, self._timeout).payload
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ExternalToolError("stayingapi", "malformed_response", "Job response omitted data")
            status = str(data.get("status", "")).casefold()
            if status == "completed":
                result = data.get("result", [])
                if isinstance(result, dict):
                    return result.get("data", result.get("hotels", []))
                return result
            if status == "failed":
                raise ExternalToolError("stayingapi", "provider_error", "Hotel search job failed")
            if status not in {"pending", "running"}:
                raise ExternalToolError("stayingapi", "malformed_response", "Unknown hotel job status")
        raise ExternalToolError("stayingapi", "polling_timeout", "Hotel search did not finish within the polling limit")

    @staticmethod
    def _normalize(raw: dict[str, object], city_id: str) -> HotelOption | None:
        price = raw.get("price")
        if not isinstance(price, dict) or str(price.get("currency", "INR")).upper() != "INR":
            return None
        nightly = price.get("nightlyPrice")
        try:
            nightly_value = _number(nightly, provider="stayingapi", field="nightlyPrice")
        except ExternalToolError:
            return None
        if nightly_value <= 0 or not str(raw.get("name", "")).strip():
            return None
        rating = None
        if raw.get("guestRating") is not None and raw.get("ratingScale") is not None:
            scale = _number(raw["ratingScale"], provider="stayingapi", field="ratingScale")
            if scale > 0:
                rating = (_number(raw["guestRating"], provider="stayingapi", field="guestRating") * Decimal("5") / scale).quantize(Decimal("0.01"))
        amenities = [str(value) for value in raw.get("amenities", []) if isinstance(value, str)] if isinstance(raw.get("amenities", []), list) else []
        provider_id = str(raw.get("id", "")).strip() or str(raw["name"])
        return HotelOption(id=_stable_id("staying-hotel", [provider_id, city_id, nightly_value]), city_id=city_id, name=str(raw["name"]).strip(), price_per_night=nightly_value, rating=rating, amenities=amenities, available_rooms=None, source=DataSource.STAYINGAPI, is_live=True, price_source=PriceSource.LIVE_QUOTE)


class GeoapifyActivitySearchTool:
    provider_source = DataSource.GEOAPIFY
    _URL = "https://api.geoapify.com/v2/places"
    _CATEGORIES = {
        "sightseeing": "tourism.sights",
        "attractions": "tourism.attraction",
        "museum": "entertainment.museum",
        "food": "catering.restaurant",
        "restaurant": "catering.restaurant",
        "outdoor": "leisure",
        "outdoor activities": "leisure",
        "nature": "natural",
        "beach": "beach",
    }

    def __init__(self, catalog: LocalDataCatalog, api_key: SecretStr | str | None, *, timeout: float = 8, transport: HttpTransport = request_json) -> None:
        self._cities = _cities(catalog)
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def search_activities(self, city_id: str, *, category: str | None = None, max_cost: Decimal | None = None) -> list[ActivityOption]:
        if max_cost is not None:
            # Geoapify Places does not quote admission prices, so no result can
            # honestly be asserted to satisfy a caller's hard price ceiling.
            return []
        city = _city(self._cities, city_id, "geoapify")
        if city.latitude is None or city.longitude is None:
            raise ExternalToolError("geoapify", "unsupported_city", "City coordinates are unavailable")
        categories = self._CATEGORIES.get((category or "sightseeing").casefold(), "tourism,entertainment,leisure")
        params = {"categories": categories, "filter": f"circle:{city.longitude},{city.latitude},20000", "bias": f"proximity:{city.longitude},{city.latitude}", "limit": "20", "apiKey": _key(self._api_key, "geoapify")}
        payload = _call(self._transport, "geoapify", "GET", f"{self._URL}?{urlencode(params)}", {}, self._timeout).payload
        features = payload.get("features", [])
        if not isinstance(features, list):
            raise ExternalToolError("geoapify", "malformed_response", "Places response omitted features")
        results: list[ActivityOption] = []
        for feature in features:
            properties = feature.get("properties") if isinstance(feature, dict) else None
            if not isinstance(properties, dict) or not str(properties.get("name", "")).strip():
                continue
            place_id = str(properties.get("place_id", "")).strip()
            results.append(ActivityOption(id=_stable_id("geo-place", [place_id or properties["name"], city_id]), city_id=city_id, name=str(properties["name"]).strip(), category=category or "sightseeing", duration_minutes=None, price=None, source=DataSource.GEOAPIFY, is_live=True, price_source=PriceSource.UNKNOWN))
        return sorted(results, key=lambda item: (item.name.casefold(), item.id))


class GeoapifyRouteTool:
    provider_source = DataSource.GEOAPIFY
    _URL = "https://api.geoapify.com/v1/routing"
    _MODES = {TransportMode.CAR: "drive", TransportMode.WALK: "walk", TransportMode.BUS: "bus", TransportMode.TRAIN: "approximated_transit"}

    def __init__(self, catalog: LocalDataCatalog, api_key: SecretStr | str | None, *, cost_per_km: Decimal = Decimal("12"), timeout: float = 8, transport: HttpTransport = request_json) -> None:
        self._cities = _cities(catalog)
        self._api_key = api_key
        self._cost_per_km = cost_per_km
        self._timeout = timeout
        self._transport = transport

    def calculate_route(self, origin_city_id: str, destination_city_id: str, mode: TransportMode) -> RouteResult:
        if mode not in self._MODES:
            return RouteResult(origin_city_id=origin_city_id, destination_city_id=destination_city_id, mode=mode, feasible=False, reason="Geoapify routing does not support this transport mode.", source=DataSource.GEOAPIFY, is_live=True)
        origin = _city(self._cities, origin_city_id, "geoapify")
        destination = _city(self._cities, destination_city_id, "geoapify")
        if None in {origin.latitude, origin.longitude, destination.latitude, destination.longitude}:
            raise ExternalToolError("geoapify", "unsupported_city", "City coordinates are unavailable")
        params = {"waypoints": f"{origin.latitude},{origin.longitude}|{destination.latitude},{destination.longitude}", "mode": self._MODES[mode], "format": "json", "apiKey": _key(self._api_key, "geoapify")}
        payload = _call(self._transport, "geoapify", "GET", f"{self._URL}?{urlencode(params)}", {}, self._timeout).payload
        results = payload.get("results", [])
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return RouteResult(origin_city_id=origin_city_id, destination_city_id=destination_city_id, mode=mode, feasible=False, reason="Live routing provider returned no route.", source=DataSource.GEOAPIFY, is_live=True)
        distance_km = (_number(results[0].get("distance"), provider="geoapify", field="distance") / Decimal("1000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        duration_minutes = int((_number(results[0].get("time"), provider="geoapify", field="time") / Decimal("60")).to_integral_value(rounding=ROUND_HALF_UP))
        if distance_km <= 0 or duration_minutes <= 0:
            raise ExternalToolError("geoapify", "malformed_response", "Route distance or duration is invalid")
        estimated_cost = (distance_km * self._cost_per_km).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        route = RouteInfo(id=_stable_id("geo-route", [origin_city_id, destination_city_id, mode, distance_km, duration_minutes]), origin_city_id=origin_city_id, destination_city_id=destination_city_id, mode=mode, duration_minutes=duration_minutes, distance_km=distance_km, estimated_cost=estimated_cost, source=DataSource.GEOAPIFY, is_live=True, is_estimate=True, price_source=PriceSource.ESTIMATED)
        return RouteResult(origin_city_id=origin_city_id, destination_city_id=destination_city_id, mode=mode, feasible=True, route=route, source=DataSource.GEOAPIFY, is_live=True)


class OpenWeatherTool:
    provider_source = DataSource.OPENWEATHER
    _URL = "https://api.openweathermap.org/data/2.5/forecast"

    def __init__(self, catalog: LocalDataCatalog, api_key: SecretStr | str | None, *, timeout: float = 8, transport: HttpTransport = request_json, today: Callable[[], date] = lambda: datetime.now(UTC).date()) -> None:
        self._cities = _cities(catalog)
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self._today = today

    def get_forecast(self, city_id: str, target_date: date) -> WeatherResult:
        today = self._today()
        if target_date < today or target_date > today + timedelta(days=5):
            return WeatherResult(city_id=city_id, requested_date=target_date, weather_available=False, reason="outside_forecast_horizon")
        city = _city(self._cities, city_id, "openweather")
        if city.latitude is None or city.longitude is None:
            raise ExternalToolError("openweather", "unsupported_city", "City coordinates are unavailable")
        params = {"lat": str(city.latitude), "lon": str(city.longitude), "appid": _key(self._api_key, "openweather"), "units": "metric"}
        payload = _call(self._transport, "openweather", "GET", f"{self._URL}?{urlencode(params)}", {}, self._timeout).payload
        entries = payload.get("list", [])
        if not isinstance(entries, list):
            raise ExternalToolError("openweather", "malformed_response", "Forecast response omitted list")
        forecasts: list[WeatherForecast] = []
        for raw in entries:
            if not isinstance(raw, dict) or raw.get("dt") is None:
                continue
            forecast_at = datetime.fromtimestamp(int(raw["dt"]), UTC)
            if forecast_at.date() != target_date:
                continue
            main, weather = raw.get("main"), raw.get("weather")
            if not isinstance(main, dict) or not isinstance(weather, list) or not weather or not isinstance(weather[0], dict):
                continue
            forecasts.append(WeatherForecast(forecast_at=forecast_at, temperature_c=_number(main.get("temp"), provider="openweather", field="temp"), feels_like_c=_number(main["feels_like"], provider="openweather", field="feels_like") if main.get("feels_like") is not None else None, condition=str(weather[0].get("description", "unknown")), precipitation_probability=_number(raw["pop"], provider="openweather", field="pop") if raw.get("pop") is not None else None, rain_mm=_number(raw["rain"].get("3h"), provider="openweather", field="rain.3h") if isinstance(raw.get("rain"), dict) and raw["rain"].get("3h") is not None else None, humidity_percent=int(main["humidity"]) if main.get("humidity") is not None else None, wind_speed_mps=_number(raw["wind"].get("speed"), provider="openweather", field="wind.speed") if isinstance(raw.get("wind"), dict) and raw["wind"].get("speed") is not None else None))
        if not forecasts:
            return WeatherResult(city_id=city_id, requested_date=target_date, weather_available=False, reason="outside_forecast_horizon")
        return WeatherResult(city_id=city_id, requested_date=target_date, weather_available=True, forecasts=forecasts)
