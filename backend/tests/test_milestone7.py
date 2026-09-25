import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import LLMProviderName, Settings
from app.domain.models import PlanningStatus, TransportMode
from app.extraction.models import ExtractedTravelIntent
from app.extraction.service import (
    ConstraintExtractionError,
    NaturalLanguagePlanningService,
    create_natural_language_planning_service,
)
from app.llm import ExtractionContext, LLMProviderError, OllamaProvider
from app.planning.coordinator import create_planning_coordinator
from app.tools.local_data import load_catalog


FLAGSHIP_QUERY = (
    "I want to travel from Mumbai to Goa for 4 days with a maximum budget of "
    "₹30,000. I prefer beaches, direct flights and a comfortable hotel."
)


@pytest.fixture(scope="module")
def catalog():
    from pathlib import Path

    return load_catalog(Path(__file__).resolve().parents[1] / "data")


@pytest.fixture(scope="module")
def fallback_service(catalog):
    settings = Settings(llm_provider="fallback")
    return create_natural_language_planning_service(settings, catalog=catalog)


@pytest.fixture(scope="module")
def extraction_context(catalog):
    return ExtractionContext(
        city_name_to_id={city.name.casefold(): city.id for city in catalog.cities},
        known_airlines=tuple(sorted({flight.airline for flight in catalog.flights})),
        demo_reference_date=date(2027, 1, 15),
    )


def test_default_provider_is_deterministic_fallback() -> None:
    settings = Settings()
    assert settings.llm_provider == LLMProviderName.FALLBACK
    assert settings.demo_reference_date == date(2027, 1, 15)


def test_ollama_provider_is_constructible_from_config() -> None:
    settings = Settings(llm_provider="ollama", ollama_model="demo-model")
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        transport=lambda *_: {},
    )
    assert provider.name == "ollama"


def test_ollama_url_must_be_local() -> None:
    with pytest.raises(ValidationError, match="local HTTP URL"):
        Settings(ollama_base_url="https://example.com")


def test_flagship_fallback_extraction(fallback_service) -> None:
    request, intent = fallback_service.extract(FLAGSHIP_QUERY)
    assert request.constraints.origin_city_id == "city-mum"
    assert request.constraints.destination_city_ids == ["city-goi"]
    assert request.constraints.start_date == date(2027, 1, 15)
    assert request.constraints.end_date == date(2027, 1, 18)
    assert request.constraints.total_budget == Decimal("30000")
    assert request.preferences.preferred_activity_categories == ["nature"]
    assert request.preferences.prefer_direct_flights is True
    assert "DEMO_REFERENCE_DATE" in intent.assumptions[0]


def test_multicity_extraction_preserves_order_and_plans(fallback_service) -> None:
    query = (
        "Plan Chennai to Delhi and Jaipur from 10 February 2027 to 14 February 2027 "
        "with a budget of 100000 rupees."
    )
    response = fallback_service.plan(query)
    assert response.extracted_request.constraints.destination_city_ids == [
        "city-del",
        "city-jai",
    ]
    assert response.result.status == PlanningStatus.COMPLETED


@pytest.mark.parametrize(
    "spelling",
    ["₹30000", "₹30,000", "30000 rupees", "30k"],
)
def test_currency_spellings_are_parsed_deterministically(fallback_service, spelling) -> None:
    request, _ = fallback_service.extract(f"Mumbai to Goa for 4 days under {spelling}.")
    assert request.constraints.total_budget == Decimal("30000")


def test_explicit_dates_do_not_use_demo_reference(fallback_service) -> None:
    request, intent = fallback_service.extract(
        "Mumbai to Goa from 2027-01-15 to 2027-01-18 under ₹30,000."
    )
    assert request.constraints.start_date == date(2027, 1, 15)
    assert request.constraints.end_date == date(2027, 1, 18)
    assert not any("DEMO_REFERENCE_DATE" in item for item in intent.assumptions)


def test_day_month_name_dates_are_parsed(fallback_service) -> None:
    request, _ = fallback_service.extract(
        "Mumbai to Goa from 15 January 2027 to 18 January 2027 under 30000 rupees."
    )
    assert request.constraints.start_date == date(2027, 1, 15)
    assert request.constraints.end_date == date(2027, 1, 18)


def test_hotel_and_flight_ceilings_are_separate_from_budget(fallback_service) -> None:
    request, _ = fallback_service.extract(
        "Mumbai to Goa for 4 days with budget ₹30,000. "
        "Hotel should cost no more than ₹3,000 per night and flight price under ₹6,000."
    )
    assert request.constraints.total_budget == Decimal("30000")
    assert request.constraints.max_hotel_price_per_night == Decimal("3000")
    assert request.constraints.max_flight_price == Decimal("6000")


def test_daily_travel_time_is_converted_to_minutes(fallback_service) -> None:
    request, _ = fallback_service.extract(
        "Delhi to Shimla from 2027-01-20 to 2027-01-23 with budget ₹30,000. "
        "I don't want to travel more than 6 hours per day and prefer bus travel."
    )
    assert request.constraints.max_travel_duration_minutes == 360
    assert request.constraints.allowed_transport_modes == [TransportMode.BUS]
    assert request.preferences.preferred_transport_modes == [TransportMode.BUS]


def test_soft_preferences_airline_amenities_pace_and_activity(fallback_service) -> None:
    request, _ = fallback_service.extract(
        "Mumbai to Goa for 4 days under ₹30000. Prefer Konkan Connect direct flights, "
        "a relaxed pace, beach activities, and a hotel with wifi, breakfast and a pool."
    )
    assert request.preferences.preferred_airlines == ["Konkan Connect"]
    assert request.preferences.preferred_hotel_amenities == ["wifi", "breakfast", "pool"]
    assert request.preferences.preferred_activity_categories == ["nature"]
    assert request.preferences.pace == "relaxed"
    assert request.preferences.prefer_direct_flights is True


def test_missing_budget_requires_clarification(fallback_service) -> None:
    with pytest.raises(ConstraintExtractionError) as captured:
        fallback_service.extract("Mumbai to Goa for 4 days.")
    assert "total_budget" in captured.value.missing_fields


def test_missing_origin_requires_clarification(fallback_service) -> None:
    with pytest.raises(ConstraintExtractionError) as captured:
        fallback_service.extract("Plan a trip to Goa for 4 days under ₹30000.")
    assert "origin" in captured.value.missing_fields


def test_unknown_city_requires_clarification(fallback_service) -> None:
    with pytest.raises(ConstraintExtractionError) as captured:
        fallback_service.extract("Mumbai to Atlantis for 4 days under ₹30000.")
    assert captured.value.missing_fields
    assert "Supported demo cities" in str(captured.value)


def test_repeated_extraction_is_identical(fallback_service) -> None:
    first, first_intent = fallback_service.extract(FLAGSHIP_QUERY)
    second, second_intent = fallback_service.extract(FLAGSHIP_QUERY)
    assert first.model_dump_json() == second.model_dump_json()
    assert first_intent.model_dump_json() == second_intent.model_dump_json()


def test_valid_mocked_ollama_output_is_schema_validated(extraction_context) -> None:
    raw = _valid_intent_dict()
    calls = []

    def transport(url, payload, timeout):
        calls.append((url, payload, timeout))
        return {"message": {"content": json.dumps(raw)}}

    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        timeout_seconds=2,
        transport=transport,
    )
    result = provider.extract_travel_request(FLAGSHIP_QUERY, extraction_context)
    assert result.origin_city_id == "city-mum"
    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"
    assert calls[0][1]["format"]["additionalProperties"] is False
    assert calls[0][1]["options"]["temperature"] == 0


def test_ollama_malformed_json_is_rejected(extraction_context) -> None:
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        timeout_seconds=2,
        transport=lambda *_: {"message": {"content": "not-json"}},
    )
    with pytest.raises(LLMProviderError) as captured:
        provider.extract_travel_request(FLAGSHIP_QUERY, extraction_context)
    assert captured.value.code == "malformed_json"


def test_ollama_invalid_schema_is_rejected(extraction_context) -> None:
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        timeout_seconds=2,
        transport=lambda *_: {"message": {"content": '{"travelers": 0}'}},
    )
    with pytest.raises(LLMProviderError) as captured:
        provider.extract_travel_request(FLAGSHIP_QUERY, extraction_context)
    assert captured.value.code == "invalid_schema"


@pytest.mark.parametrize(
    ("error_code", "exception"),
    [
        ("unavailable", LLMProviderError("unavailable", "not running")),
        ("timeout", LLMProviderError("timeout", "too slow")),
        ("malformed_json", LLMProviderError("malformed_json", "bad json")),
        ("invalid_schema", LLMProviderError("invalid_schema", "bad fields")),
    ],
)
def test_ollama_failures_use_visible_deterministic_fallback(
    catalog, error_code, exception
) -> None:
    service = _ollama_service(catalog, RaisingProvider(exception))
    response = service.plan(FLAGSHIP_QUERY)
    assert response.extraction.requested_provider == "ollama"
    assert response.extraction.provider_used == "fallback"
    assert response.extraction.fallback_used is True
    assert response.extraction.fallback_reason == error_code
    assert error_code in response.extraction.warnings[-1]
    assert response.result.status == PlanningStatus.COMPLETED


def test_valid_ollama_extraction_does_not_report_fallback(catalog) -> None:
    service = _ollama_service(catalog, StaticProvider(ExtractedTravelIntent(**_valid_intent_dict())))
    response = service.plan(FLAGSHIP_QUERY)
    assert response.extraction.provider_used == "ollama"
    assert response.extraction.fallback_used is False
    assert response.result.status == PlanningStatus.COMPLETED


def test_ollama_duration_only_uses_deterministic_demo_date(catalog) -> None:
    raw = _valid_intent_dict()
    raw.pop("start_date")
    raw.pop("end_date")
    service = _ollama_service(catalog, StaticProvider(ExtractedTravelIntent(**raw)))
    response = service.plan(FLAGSHIP_QUERY)
    assert response.extraction.provider_used == "ollama"
    assert response.extraction.fallback_used is False
    assert response.extracted_request.constraints.start_date == date(2027, 1, 15)
    assert response.extracted_request.constraints.end_date == date(2027, 1, 18)
    assert any("DEMO_REFERENCE_DATE" in item for item in response.extraction.assumptions)


def test_ollama_semantically_incomplete_output_falls_back(catalog) -> None:
    incomplete = ExtractedTravelIntent(origin_city_id="city-mum")
    service = _ollama_service(catalog, StaticProvider(incomplete))
    response = service.plan(FLAGSHIP_QUERY)
    assert response.extraction.fallback_used is True
    assert response.extraction.fallback_reason == "invalid_extraction"


def test_default_offline_pipeline_never_calls_network(monkeypatch, catalog) -> None:
    import app.llm.ollama_provider as ollama_module

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("default fallback mode attempted network access")

    monkeypatch.setattr(ollama_module, "urlopen", forbidden_network)
    response = create_natural_language_planning_service(
        Settings(llm_provider="fallback"), catalog=catalog
    ).plan(FLAGSHIP_QUERY)
    assert response.result.status == PlanningStatus.COMPLETED


def test_flagship_natural_pipeline_repairs_budget(fallback_service) -> None:
    response = fallback_service.plan(FLAGSHIP_QUERY)
    state = response.result
    assert state.initial_itinerary.costs.total == Decimal("38500.00")
    assert state.current_itinerary.costs.total == Decimal("10400.00")
    assert state.validation_history[0].is_valid is False
    assert state.current_validation.is_valid is True
    assert [attempt.actions[0].action.value for attempt in state.replanning_attempts] == [
        "search_cheaper_flight",
        "search_cheaper_hotel",
    ]
    assert state.preference_score is not None
    assert state.status == PlanningStatus.COMPLETED


def test_natural_endpoint_returns_frontend_friendly_envelope(api_client) -> None:
    response = api_client.post("/api/v1/trips/plan-natural", json={"query": FLAGSHIP_QUERY})
    assert response.status_code == 200
    body = response.json()
    assert body["original_query"] == FLAGSHIP_QUERY
    assert body["extracted_request"]["constraints"]["origin_city_id"] == "city-mum"
    assert body["extraction"]["provider_used"] == "fallback"
    assert body["sources"] == {
        "planning": "deterministic_coordinator",
        "travel_source": "local_demo_dataset",
        "validation": "deterministic",
        "replanning": "deterministic_policy",
    }
    assert body["result"]["status"] == "completed"


@pytest.mark.parametrize("payload", [{}, {"query": ""}, {"query": "   "}, {"query": 42}])
def test_natural_endpoint_rejects_malformed_or_empty_input(api_client, payload) -> None:
    response = api_client.post("/api/v1/trips/plan-natural", json=payload)
    assert response.status_code == 422


def test_natural_endpoint_returns_structured_clarification_error(api_client) -> None:
    response = api_client.post(
        "/api/v1/trips/plan-natural",
        json={"query": "Plan a trip to Goa for 4 days."},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "clarification_required"
    assert "origin" in detail["missing_fields"]
    assert "total_budget" in detail["missing_fields"]


def _valid_intent_dict() -> dict[str, object]:
    return {
        "origin_city_id": "city-mum",
        "destination_city_ids": ["city-goi"],
        "start_date": "2027-01-15",
        "end_date": "2027-01-18",
        "duration_days": 4,
        "travelers": 1,
        "total_budget": "30000",
        "allowed_transport_modes": ["flight"],
        "preferred_transport_modes": ["flight"],
        "preferred_activity_categories": ["nature"],
        "prefer_direct_flights": True,
    }


class RaisingProvider:
    name = "ollama"

    def __init__(self, exception: Exception) -> None:
        self._exception = exception

    def extract_travel_request(self, _text, _context):
        raise self._exception


class StaticProvider:
    name = "ollama"

    def __init__(self, intent: ExtractedTravelIntent) -> None:
        self._intent = intent

    def extract_travel_request(self, _text, _context):
        return self._intent


def _ollama_service(catalog, provider) -> NaturalLanguagePlanningService:
    settings = Settings(llm_provider="ollama", ollama_model="test-model")
    return NaturalLanguagePlanningService(
        settings=settings,
        catalog=catalog,
        coordinator=create_planning_coordinator(settings, catalog=catalog),
        primary_provider=provider,
    )
