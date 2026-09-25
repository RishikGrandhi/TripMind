"""One-call, opt-in smoke checks for TripMind's configured live providers."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from app.llm import AgentActionType, AgentDecisionContext, ExtractionContext, GroqProvider  # noqa: E402
from app.planning.agent_loop import SearchFlightsParameters  # noqa: E402
from app.tools.external import (  # noqa: E402
    ExternalToolError,
    GeoapifyActivitySearchTool,
    GeoapifyRouteTool,
    OpenWeatherTool,
    SerpApiFlightSearchTool,
    StayingApiHotelSearchTool,
)
from app.tools.local_data import load_catalog  # noqa: E402
from app.domain.models import TransportMode  # noqa: E402


QUERY = "Plan a 4-day trip from Mumbai to Goa under ₹30,000. I prefer beaches and direct flights."


def _print(value) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def smoke_groq(settings: Settings, catalog) -> None:
    provider = GroqProvider(
        api_key=(settings.groq_api_key.get_secret_value() if settings.groq_api_key else None),
        model=settings.groq_model,
        timeout_seconds=settings.groq_timeout_seconds,
    )
    context = ExtractionContext(
        city_name_to_id={city.name.casefold(): city.id for city in catalog.cities},
        known_airlines=tuple(sorted({flight.airline for flight in catalog.flights})),
        demo_reference_date=settings.demo_reference_date,
    )
    intent = provider.extract_travel_request(QUERY, context)
    decision = provider.decide_next_action(
        AgentDecisionContext(
            step=1,
            allowed_actions=[AgentActionType.SEARCH_FLIGHTS],
            state={
                "constraints": {
                    "origin_city_id": "city-mum",
                    "destination_city_ids": ["city-goi"],
                    "start_date": "2027-01-15",
                    "end_date": "2027-01-18",
                    "travelers": 1,
                    "allowed_transport_modes": ["flight"],
                }
            },
        )
    )
    validated = SearchFlightsParameters.model_validate(decision.parameters)
    _print({"provider": "groq", "extraction": intent, "decision": decision, "validated_parameters": validated})


def smoke_serpapi(settings: Settings, catalog) -> None:
    tool = SerpApiFlightSearchTool(catalog, settings.serpapi_api_key, timeout=settings.serpapi_timeout_seconds)
    results = tool.search_flights("city-mum", "city-goi", travel_date=date.today() + timedelta(days=30))
    _print({"provider": "serpapi", "result_count": len(results), "first_result": results[0] if results else None})


def smoke_stayingapi(settings: Settings, catalog) -> None:
    check_in = date.today() + timedelta(days=30)
    tool = StayingApiHotelSearchTool(catalog, settings.staying_api_key, timeout=settings.staying_api_timeout_seconds, max_polls=settings.staying_api_max_polls, poll_interval=settings.staying_api_poll_interval_seconds)
    results = tool.search_hotels("city-goi", check_in=check_in, check_out=check_in + timedelta(days=1), adults=1)
    _print({"provider": "stayingapi", "result_count": len(results), "first_result": results[0] if results else None})


def smoke_places(settings: Settings, catalog) -> None:
    tool = GeoapifyActivitySearchTool(catalog, settings.geoapify_places_api_key, timeout=settings.geoapify_timeout_seconds)
    results = tool.search_activities("city-goi", category="sightseeing")
    _print({"provider": "geoapify_places", "result_count": len(results), "first_result": results[0] if results else None})


def smoke_routing(settings: Settings, catalog) -> None:
    tool = GeoapifyRouteTool(catalog, settings.geoapify_routing_api_key, timeout=settings.geoapify_timeout_seconds, cost_per_km=settings.live_route_cost_per_km)
    _print(tool.calculate_route("city-mum", "city-goi", TransportMode.CAR))


def smoke_weather(settings: Settings, catalog) -> None:
    tool = OpenWeatherTool(catalog, settings.openweather_api_key, timeout=settings.openweather_timeout_seconds)
    _print(tool.get_forecast("city-goi", datetime.now(UTC).date() + timedelta(days=1)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["groq", "serpapi", "stayingapi", "places", "routing", "weather"])
    args = parser.parse_args()
    settings = Settings()
    catalog = load_catalog(BACKEND_ROOT / "data")
    actions = {"groq": smoke_groq, "serpapi": smoke_serpapi, "stayingapi": smoke_stayingapi, "places": smoke_places, "routing": smoke_routing, "weather": smoke_weather}
    try:
        actions[args.provider](settings, catalog)
    except ExternalToolError as exc:
        _print({"provider": exc.provider, "status": "failed", "code": exc.code, "message": exc.message})
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "smoke_failed")
        _print({"provider": args.provider, "status": "failed", "code": code, "message": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
