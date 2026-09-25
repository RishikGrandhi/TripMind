from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.domain.models import (
    DataSource,
    PlanRequest,
    SoftPreferences,
    TravelConstraints,
    WeatherResult,
)
from app.evaluation.agentic_runner import (
    MockWeatherTool,
    ScriptedAgentProvider,
    load_scenarios,
    run_agentic_evaluation,
    run_agentic_scenario,
)
from app.llm import AgentActionType, AgentDecision
from app.persistence import PlanningSessionRepository, create_database
from app.planning.coordinator import create_planning_coordinator
from app.tools.local_data import load_catalog
from app.tools.registry import DEFAULT_DATA_DIR, create_tool_registry


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DEFAULT_DATA_DIR)


def request() -> PlanRequest:
    return PlanRequest(
        request_id="adaptive-weather-test",
        natural_language_request="Mumbai to Goa with outdoor preferences.",
        constraints=TravelConstraints(
            origin_city_id="city-mum",
            destination_city_id="city-goi",
            start_date=date(2027, 1, 15),
            end_date=date(2027, 1, 18),
            total_budget=Decimal("100000"),
        ),
        preferences=SoftPreferences(
            preferred_activity_categories=["nature", "culture"]
        ),
    )


class WeatherAdaptiveProvider:
    name = "groq"

    def __init__(self):
        self.contexts = []

    def decide_next_action(self, context):
        self.contexts.append(context.model_copy(deep=True))
        state = context.state
        if not state["weather_results"]:
            return AgentDecision(
                action=AgentActionType.GET_WEATHER,
                reason_code="outdoor_preference_needs_weather",
                parameters={"city_id": "city-goi", "target_date": "2027-01-16"},
            )
        if not state["activity_candidates"]:
            rainy = any(
                "rain" in forecast["condition"]
                for result in state["weather_results"]
                for forecast in result.get("forecasts", [])
            )
            return AgentDecision(
                action=AgentActionType.SEARCH_ACTIVITIES,
                reason_code="weather_changed_activity_category",
                parameters={"city_id": "city-goi", "category": "culture" if rainy else "nature"},
            )
        if not state["flight_candidates"]:
            return AgentDecision(
                action=AgentActionType.SEARCH_FLIGHTS,
                reason_code="need_transport",
                parameters={"origin_city_id":"city-mum","destination_city_id":"city-goi","travel_date":"2027-01-15","travelers":1},
            )
        if not state["hotel_candidates"]:
            return AgentDecision(
                action=AgentActionType.SEARCH_HOTELS,
                reason_code="need_stay",
                parameters={"city_id": "city-goi", "rooms": 1},
            )
        if not state["current_itinerary"]:
            return AgentDecision(action=AgentActionType.BUILD_CANDIDATE, reason_code="ready")
        return AgentDecision(action=AgentActionType.VALIDATE, reason_code="validate")


class UnavailableWeatherTool:
    provider_source = DataSource.LOCAL_DEMO

    def get_forecast(self, city_id, target_date):
        return WeatherResult(
            city_id=city_id,
            requested_date=target_date,
            weather_available=False,
            reason="outside_forecast_horizon",
            source=DataSource.LOCAL_DEMO,
            is_live=False,
        )


def coordinator_with_weather(catalog, provider, weather):
    settings = Settings(
        llm_provider="groq", groq_api_key="mocked", groq_model="mocked-agent"
    )
    tools = replace(create_tool_registry(settings, catalog=catalog), weather=weather)
    return create_planning_coordinator(
        settings,
        catalog=catalog,
        agent_provider=provider,
        proposal_provider=provider if hasattr(provider, "propose_action") else None,
        tool_registry=tools,
    )


def test_agentic_scenarios_sc008_through_sc013_are_reproducible() -> None:
    report = run_agentic_evaluation()
    assert [item.scenario_id for item in report.results] == [
        "SC-008", "SC-009", "SC-010", "SC-011", "SC-012", "SC-013"
    ]
    assert report.expectation_met_count == 6
    assert report.deterministic_replay_count == 6


def test_different_requests_produce_different_non_universal_sequences() -> None:
    report = run_agentic_evaluation()
    sequences = {item.scenario_id: item.ordered_actions for item in report.results}
    assert sequences["SC-008"][0:2] == ["get_weather", "search_activities"]
    assert sequences["SC-010"][0:2] == ["search_hotels", "search_flights"]
    assert sequences["SC-011"][0] == "get_route"
    assert "search_flights" not in sequences["SC-011"]
    assert "get_weather" not in sequences["SC-010"]


def test_previous_weather_result_changes_next_activity_action(catalog) -> None:
    provider = WeatherAdaptiveProvider()
    state = coordinator_with_weather(catalog, provider, MockWeatherTool()).plan(request())
    assert state.weather_results[0].forecasts[0].condition == "heavy rain"
    assert state.agent_trace[1].action == "search_activities"
    assert state.agent_trace[1].parameters["category"] == "culture"
    assert provider.contexts[1].state["weather_results"][0]["weather_available"] is True


def test_unavailable_weather_is_recorded_and_planning_continues(catalog) -> None:
    provider = WeatherAdaptiveProvider()
    state = coordinator_with_weather(catalog, provider, UnavailableWeatherTool()).plan(request())
    assert state.status.value == "completed"
    assert state.weather_results[0].reason == "outside_forecast_horizon"
    assert state.agent_trace[0].result_summary == "Weather unavailable: outside_forecast_horizon"


@pytest.mark.parametrize(
    ("decision", "summary"),
    [
        (AgentDecision(action=AgentActionType.BUILD_CANDIDATE, reason_code="too_early"), "not legal"),
        (AgentDecision(action=AgentActionType.FINISH, reason_code="skip_validation"), "not legal"),
        (
            AgentDecision(
                action=AgentActionType.GET_WEATHER,
                reason_code="bad_date",
                parameters={"city_id":"city-goi","target_date":"2030-01-01"},
            ),
            "Invalid or unusable",
        ),
    ],
)
def test_illegal_or_invalid_agent_actions_are_rejected(catalog, decision, summary) -> None:
    provider = ScriptedAgentProvider([decision], [])
    state = coordinator_with_weather(catalog, provider, MockWeatherTool()).plan(request())
    rejected = next(item for item in state.agent_trace if item.status.value == "rejected")
    assert summary in rejected.result_summary
    assert state.fallback_used is True
    assert state.current_validation.is_valid is True


def test_agent_observation_is_concise_and_secret_free(catalog) -> None:
    provider = WeatherAdaptiveProvider()
    coordinator_with_weather(catalog, provider, MockWeatherTool()).plan(request())
    serialized = str(provider.contexts[0].state).casefold()
    assert "api_key" not in serialized
    assert "final_explanation" not in serialized
    assert "agent_trace" not in serialized
    assert "constraints" in provider.contexts[0].state
    assert "limits" in provider.contexts[0].state


def test_trace_and_weather_survive_sqlite_round_trip(catalog, tmp_path) -> None:
    provider = WeatherAdaptiveProvider()
    state = coordinator_with_weather(catalog, provider, MockWeatherTool()).plan(request())
    database = create_database(Settings(database_url=f"sqlite:///{tmp_path / 'agent.db'}"))
    database.initialize()
    repository = PlanningSessionRepository(database.session_factory)
    try:
        saved = repository.save(state=state, request_payload=request())
        restored = repository.get_by_id(saved.id)
        assert restored is not None
        assert restored.result.agent_trace == state.agent_trace
        assert restored.result.weather_results == state.weather_results
    finally:
        database.dispose()


def test_failed_live_tool_is_factual_and_bounded_in_trace() -> None:
    scenario = next(item for item in load_scenarios() if item.id == "SC-012")
    state = run_agentic_scenario(scenario)
    failed = next(item for item in state.agent_trace if item.status.value == "failed")
    assert failed.action == "get_weather"
    assert failed.source == DataSource.OPENWEATHER
    assert failed.reason_code == "external_tool_timeout"
    assert state.tool_call_history[0].status.value == "failed"
    assert state.fallback_reason == "external_tool_timeout"


def test_trace_sources_reflect_actual_local_tool_execution(catalog) -> None:
    provider = WeatherAdaptiveProvider()
    state = coordinator_with_weather(catalog, provider, MockWeatherTool()).plan(request())
    tool_entries = [item for item in state.agent_trace if item.tool]
    assert tool_entries
    assert all(item.source == DataSource.LOCAL_DEMO for item in tool_entries)
