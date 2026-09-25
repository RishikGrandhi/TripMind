import json
import socket
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import LLMProviderName, Settings
from app.domain.models import (
    CorrectiveActionType,
    PlanningStatus,
    ReplanningOutcome,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    TravelRequest,
)
from app.extraction.models import ExtractedTravelIntent
from app.extraction.service import (
    NaturalLanguagePlanningService,
    create_natural_language_planning_service,
)
from app.llm import (
    ActionProposal,
    AgentActionType,
    AgentDecision,
    ExtractionContext,
    GroqProvider,
    LLMProviderError,
)
from app.planning.coordinator import create_planning_coordinator
from app.tools.local_data import load_catalog


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FLAGSHIP_QUERY = (
    "I want to travel from Mumbai to Goa for 4 days with a maximum budget of "
    "₹30,000. I prefer beaches, direct flights and a comfortable hotel."
)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DATA_DIR)


@pytest.fixture(scope="module")
def extraction_context(catalog):
    return ExtractionContext(
        city_name_to_id={city.name.casefold(): city.id for city in catalog.cities},
        known_airlines=tuple(sorted({flight.airline for flight in catalog.flights})),
        demo_reference_date=date(2027, 1, 15),
    )


def test_groq_configuration_is_opt_in_and_secret(capsys) -> None:
    settings = Settings(
        llm_provider="groq",
        groq_api_key="unit-test-secret",
        groq_model=" configurable-model ",
        max_agent_steps=9,
    )
    assert settings.llm_provider == LLMProviderName.GROQ
    assert settings.groq_model == "configurable-model"
    assert settings.max_agent_steps == 9
    print(settings)
    assert "unit-test-secret" not in capsys.readouterr().out
    assert Settings().llm_provider == LLMProviderName.FALLBACK


def test_groq_missing_key_fails_without_network(extraction_context) -> None:
    provider = GroqProvider(
        api_key=None,
        model="test-model",
        timeout_seconds=1,
        transport=lambda *_: pytest.fail("network transport should not be called"),
    )
    with pytest.raises(LLMProviderError) as captured:
        provider.extract_travel_request(FLAGSHIP_QUERY, extraction_context)
    assert captured.value.code == "missing_api_key"


def test_groq_valid_extraction_is_schema_validated(extraction_context) -> None:
    calls = []

    def transport(url, headers, payload, timeout):
        calls.append((url, headers, payload, timeout))
        return _groq_response(_valid_intent_dict())

    provider = GroqProvider(
        api_key="not-a-real-key",
        model="test-model",
        timeout_seconds=2,
        transport=transport,
    )
    result = provider.extract_travel_request(FLAGSHIP_QUERY, extraction_context)
    assert result.origin_city_id == "city-mum"
    assert result.total_budget == Decimal("30000")
    assert calls[0][0].startswith("https://api.groq.com/")
    assert calls[0][1]["Authorization"] == "Bearer not-a-real-key"
    assert calls[0][2]["response_format"] == {"type": "json_object"}
    assert calls[0][3] == 2


def test_groq_valid_registered_action_is_typed() -> None:
    provider = _provider_for_payload(
        {
            "action": "search_flights",
            "reason_code": "need_transport",
            "parameters": {
                "origin_city_id": "city-mum",
                "destination_city_id": "city-goi",
                "travel_date": "2027-01-15",
                "travelers": 1,
            },
        }
    )
    result = provider.decide_next_action(_decision_context())
    assert result.action == AgentActionType.SEARCH_FLIGHTS


def test_groq_unknown_action_is_rejected() -> None:
    provider = _provider_for_payload(
        {"action": "run_shell", "reason_code": "unsafe", "parameters": {}}
    )
    with pytest.raises(LLMProviderError) as captured:
        provider.decide_next_action(_decision_context())
    assert captured.value.code == "invalid_action"


def test_groq_cannot_set_feasibility_in_action_output() -> None:
    provider = _provider_for_payload(
        {
            "action": "finish",
            "reason_code": "trust_me",
            "parameters": {},
            "feasible": True,
        }
    )
    with pytest.raises(LLMProviderError) as captured:
        provider.decide_next_action(_decision_context())
    assert captured.value.code == "invalid_action"


def test_groq_timeout_is_typed(extraction_context) -> None:
    def timeout(*_args):
        raise socket.timeout("slow")

    provider = GroqProvider(
        api_key="fake",
        model="test-model",
        timeout_seconds=1,
        transport=timeout,
    )
    with pytest.raises(LLMProviderError) as captured:
        provider.extract_travel_request(FLAGSHIP_QUERY, extraction_context)
    assert captured.value.code == "timeout"


def test_groq_malformed_response_is_typed(extraction_context) -> None:
    provider = GroqProvider(
        api_key="fake",
        model="test-model",
        timeout_seconds=1,
        transport=lambda *_: {"choices": [{"message": {"content": "not-json"}}]},
    )
    with pytest.raises(LLMProviderError) as captured:
        provider.extract_travel_request(FLAGSHIP_QUERY, extraction_context)
    assert captured.value.code == "malformed_json"


def test_flagship_mocked_groq_drives_tools_and_guarded_replanning(catalog) -> None:
    provider = AdaptiveFakeGroq()
    service = _groq_service(catalog, provider)
    response = service.plan(FLAGSHIP_QUERY)
    state = response.result

    assert provider.extraction_queries == [FLAGSHIP_QUERY]
    assert state.requested_provider == "groq"
    assert state.provider_used == "groq"
    assert state.fallback_used is False
    assert state.status == PlanningStatus.COMPLETED
    assert state.initial_itinerary.costs.total == Decimal("38500.00")
    assert state.current_itinerary.costs.total == Decimal("10400.00")
    assert state.validation_history[0].is_valid is False
    assert state.current_validation.is_valid is True
    assert state.preference_score is not None

    actions = [item.action for item in state.agent_trace]
    assert actions[:5] == [
        "search_flights",
        "search_hotels",
        "search_activities",
        "build_candidate",
        "validate",
    ]
    assert [record.tool_name for record in state.tool_call_history[:3]] == [
        "FlightSearchTool.search_flights",
        "HotelSearchTool.search_hotels",
        "ActivitySearchTool.search_activities",
    ]
    assert provider.decision_contexts[1].state["flight_candidates"]
    assert provider.decision_contexts[2].state["hotel_candidates"]
    assert provider.proposal_contexts[0].violation_codes[0].value == "budget_exceeded"
    assert provider.proposal_contexts[0].state_summary["current_total"] == "38500.00"
    assert [attempt.actions[0].action for attempt in state.replanning_attempts] == [
        CorrectiveActionType.SEARCH_CHEAPER_FLIGHT,
        CorrectiveActionType.SEARCH_CHEAPER_HOTEL,
    ]
    assert all(
        attempt.validation_result is not None for attempt in state.replanning_attempts
    )
    assert response.sources.planning == "groq_agent_loop"
    assert response.sources.validation == "deterministic"
    assert response.sources.travel_source == "local_demo_dataset"


def test_previous_results_can_change_next_action_sequence(catalog) -> None:
    flight_first = AdaptiveFakeGroq()
    hotel_first = AdaptiveFakeGroq(hotel_first=True)
    first = _groq_service(catalog, flight_first).plan(FLAGSHIP_QUERY).result
    second = _groq_service(catalog, hotel_first).plan(FLAGSHIP_QUERY).result
    assert first.agent_trace[0].action == "search_flights"
    assert second.agent_trace[0].action == "search_hotels"
    assert first.status == second.status == PlanningStatus.COMPLETED


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (
            AgentDecision(
                action=AgentActionType.SEARCH_FLIGHTS,
                reason_code="missing_date",
                parameters={
                    "origin_city_id": "city-mum",
                    "destination_city_id": "city-goi",
                    "travelers": 1,
                },
            ),
            "invalid_action_parameters",
        ),
        (
            AgentDecision(
                action=AgentActionType.SEARCH_HOTELS,
                reason_code="wrong_city",
                parameters={"city_id": "city-del", "rooms": 1},
            ),
            "invalid_action_parameters",
        ),
    ],
)
def test_malformed_or_illegal_tool_parameters_trigger_visible_fallback(
    catalog, decision, reason
) -> None:
    provider = ScriptedFakeGroq([decision])
    state = _groq_coordinator(catalog, provider).plan(_travel_request())
    assert state.status == PlanningStatus.COMPLETED
    assert state.provider_used == "fallback"
    assert state.fallback_used is True
    assert state.fallback_reason == reason
    assert state.agent_trace[0].status.value == "rejected"
    assert state.agent_trace[-1].status.value == "fallback"


def test_repeated_state_action_is_detected(catalog) -> None:
    repeated = AgentDecision(
        action=AgentActionType.SEARCH_FLIGHTS,
        reason_code="repeat",
        parameters={
            "origin_city_id": "city-mum",
            "destination_city_id": "city-goi",
            "travel_date": "2027-01-15",
            "travelers": 1,
            "max_price": "1",
        },
    )
    state = _groq_coordinator(catalog, ScriptedFakeGroq([repeated, repeated])).plan(
        _travel_request()
    )
    assert state.fallback_reason == "repeated_state_action"
    assert any("Repeated state/action" in (item.result_summary or "") for item in state.agent_trace)


def test_maximum_agent_steps_is_bounded_and_falls_back(catalog) -> None:
    settings = _groq_settings(max_agent_steps=1)
    provider = ScriptedFakeGroq(
        [
            AgentDecision(
                action=AgentActionType.SEARCH_FLIGHTS,
                reason_code="only_step",
                parameters={
                    "origin_city_id": "city-mum",
                    "destination_city_id": "city-goi",
                    "travel_date": "2027-01-15",
                    "travelers": 1,
                },
            )
        ]
    )
    state = create_planning_coordinator(
        settings, catalog=catalog, agent_provider=provider, proposal_provider=provider
    ).plan(_travel_request())
    assert state.agent_steps_used == 1
    assert state.fallback_reason == "maximum_agent_steps_reached"
    assert state.status == PlanningStatus.COMPLETED


def test_maximum_tool_calls_is_enforced(catalog) -> None:
    settings = _groq_settings(max_tool_calls=1)
    provider = ScriptedFakeGroq(
        [
            AgentDecision(
                action=AgentActionType.SEARCH_FLIGHTS,
                reason_code="first",
                parameters={
                    "origin_city_id": "city-mum",
                    "destination_city_id": "city-goi",
                    "travel_date": "2027-01-15",
                    "travelers": 1,
                },
            ),
            AgentDecision(
                action=AgentActionType.SEARCH_HOTELS,
                reason_code="over_limit",
                parameters={"city_id": "city-goi", "rooms": 1},
            ),
        ]
    )
    state = create_planning_coordinator(
        settings, catalog=catalog, agent_provider=provider, proposal_provider=provider
    ).plan(_travel_request())
    assert state.fallback_reason == "maximum_tool_calls_reached"
    assert state.agent_trace[1].reason_code == "maximum_tool_calls_reached"


def test_illegal_corrective_action_is_rejected_then_policy_fallback_runs(catalog) -> None:
    provider = AdaptiveFakeGroq(illegal_first_proposal=True)
    state = _groq_service(catalog, provider).plan(FLAGSHIP_QUERY).result
    assert state.status == PlanningStatus.COMPLETED
    rejected = [item for item in state.agent_trace if item.status.value == "rejected"]
    assert rejected
    assert rejected[0].action == "select_feasible_route"
    assert "deterministic policy fallback" in rejected[0].result_summary
    assert state.constraints.total_budget == Decimal("30000")


def test_corrective_provider_failure_uses_deterministic_policy(catalog) -> None:
    provider = AdaptiveFakeGroq(proposal_error=True)
    state = _groq_service(catalog, provider).plan(FLAGSHIP_QUERY).result
    assert state.status == PlanningStatus.COMPLETED
    fallbacks = [item for item in state.agent_trace if item.status.value == "fallback"]
    assert fallbacks
    assert fallbacks[0].reason_code == "timeout"
    assert state.provider_used == "groq"


def test_hard_constraints_remain_immutable_through_agent_and_replanning(catalog) -> None:
    request = _travel_request()
    before = request.constraints.model_dump_json()
    state = _groq_coordinator(catalog, AdaptiveFakeGroq()).plan(request)
    assert state.constraints.model_dump_json() == before
    assert request.constraints.model_dump_json() == before


def test_maximum_replanning_attempts_is_still_authoritative(catalog) -> None:
    settings = _groq_settings(max_replanning_attempts=1)
    provider = AdaptiveFakeGroq()
    state = create_planning_coordinator(
        settings, catalog=catalog, agent_provider=provider, proposal_provider=provider
    ).plan(_travel_request())
    assert len(state.replanning_attempts) == 1
    assert state.status == PlanningStatus.INFEASIBLE
    assert state.current_validation.is_valid is False


def test_groq_extraction_failure_visibly_falls_back(catalog) -> None:
    settings = _groq_settings()
    deterministic_coordinator = create_planning_coordinator(
        Settings(llm_provider="fallback"), catalog=catalog
    )
    service = NaturalLanguagePlanningService(
        settings=settings,
        catalog=catalog,
        coordinator=deterministic_coordinator,
        primary_provider=RaisingExtractionGroq(
            LLMProviderError("invalid_schema", "bad extraction")
        ),
    )
    response = service.plan(FLAGSHIP_QUERY)
    assert response.extraction.requested_provider == "groq"
    assert response.extraction.provider_used == "fallback"
    assert response.extraction.fallback_used is True
    assert response.extraction.fallback_reason == "invalid_schema"
    assert response.result.status == PlanningStatus.COMPLETED


def test_groq_agent_timeout_visibly_falls_back(catalog) -> None:
    provider = AdaptiveFakeGroq(decision_error=LLMProviderError("timeout", "slow"))
    state = _groq_coordinator(catalog, provider).plan(_travel_request())
    assert state.provider_used == "fallback"
    assert state.fallback_used is True
    assert state.fallback_reason == "timeout"
    assert state.status == PlanningStatus.COMPLETED


def test_default_demo_fallback_has_no_groq_network_dependency(monkeypatch, catalog) -> None:
    import app.llm.groq_provider as groq_module

    monkeypatch.setattr(
        groq_module,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("fallback mode attempted Groq network"),
    )
    response = create_natural_language_planning_service(
        Settings(llm_provider="fallback"), catalog=catalog
    ).plan(FLAGSHIP_QUERY)
    assert response.result.status == PlanningStatus.COMPLETED
    assert response.result.agent_trace == []


class AdaptiveFakeGroq:
    name = "groq"

    def __init__(
        self,
        *,
        hotel_first: bool = False,
        illegal_first_proposal: bool = False,
        proposal_error: bool = False,
        decision_error: Exception | None = None,
    ) -> None:
        self.hotel_first = hotel_first
        self.illegal_first_proposal = illegal_first_proposal
        self.proposal_error = proposal_error
        self.decision_error = decision_error
        self.extraction_queries = []
        self.decision_contexts = []
        self.proposal_contexts = []

    def extract_travel_request(self, text, _context):
        self.extraction_queries.append(text)
        return ExtractedTravelIntent(**_valid_intent_dict())

    def decide_next_action(self, context):
        self.decision_contexts.append(context.model_copy(deep=True))
        if self.decision_error is not None:
            raise self.decision_error
        state = context.state
        no_flights = not state.get("flight_candidates")
        no_hotels = not state.get("hotel_candidates")
        if self.hotel_first and no_hotels:
            return _hotel_decision()
        if no_flights:
            return _flight_decision()
        if no_hotels:
            return _hotel_decision()
        if not state.get("activity_candidates"):
            return AgentDecision(
                action=AgentActionType.SEARCH_ACTIVITIES,
                reason_code="need_destination_activities",
                parameters={"city_id": "city-goi"},
            )
        if not state.get("current_itinerary"):
            return AgentDecision(
                action=AgentActionType.BUILD_CANDIDATE,
                reason_code="required_options_ready",
            )
        if not state.get("current_validation"):
            return AgentDecision(
                action=AgentActionType.VALIDATE,
                reason_code="candidate_requires_authoritative_validation",
            )
        return AgentDecision(
            action=AgentActionType.PROPOSE_CORRECTIVE_ACTION,
            reason_code="hard_violation_requires_guarded_repair",
        )

    def propose_action(self, context):
        self.proposal_contexts.append(context.model_copy(deep=True))
        if self.proposal_error:
            raise LLMProviderError("timeout", "mock proposal timed out")
        if self.illegal_first_proposal and len(self.proposal_contexts) == 1:
            return ActionProposal(
                action=CorrectiveActionType.SELECT_FEASIBLE_ROUTE,
                reason_code="illegal_for_budget",
            )
        candidate = context.candidate_actions[0]
        return ActionProposal(
            action=candidate.action,
            target_id=candidate.target_id,
            reason_code=f"repair_{candidate.target_violation.value}",
        )


class ScriptedFakeGroq(AdaptiveFakeGroq):
    def __init__(self, decisions):
        super().__init__()
        self.decisions = list(decisions)

    def decide_next_action(self, context):
        self.decision_contexts.append(context.model_copy(deep=True))
        if not self.decisions:
            raise LLMProviderError("script_exhausted", "no mock decision remains")
        return self.decisions.pop(0)


class RaisingExtractionGroq:
    name = "groq"

    def __init__(self, error):
        self.error = error

    def extract_travel_request(self, _text, _context):
        raise self.error


def _groq_settings(**updates) -> Settings:
    return Settings(
        llm_provider="groq",
        groq_api_key="fake-test-key",
        groq_model="mock-model",
        **updates,
    )


def _groq_service(catalog, provider):
    return create_natural_language_planning_service(
        _groq_settings(), catalog=catalog, primary_provider=provider
    )


def _groq_coordinator(catalog, provider):
    settings = _groq_settings()
    return create_planning_coordinator(
        settings,
        catalog=catalog,
        agent_provider=provider,
        proposal_provider=provider,
    )


def _travel_request() -> TravelRequest:
    return TravelRequest(
        request_id="groq-agent-test",
        natural_language_request=FLAGSHIP_QUERY,
        constraints=TravelConstraints(
            origin_city_id="city-mum",
            destination_city_id="city-goi",
            start_date=date(2027, 1, 15),
            end_date=date(2027, 1, 18),
            travelers=1,
            total_budget=Decimal("30000"),
            allowed_transport_modes=[TransportMode.FLIGHT],
        ),
        preferences=SoftPreferences(
            preferred_transport_modes=[TransportMode.FLIGHT],
            preferred_activity_categories=["nature"],
            prefer_direct_flights=True,
            notes="comfortable hotel",
        ),
    )


def _flight_decision():
    return AgentDecision(
        action=AgentActionType.SEARCH_FLIGHTS,
        reason_code="need_transport_options",
        parameters={
            "origin_city_id": "city-mum",
            "destination_city_id": "city-goi",
            "travel_date": "2027-01-15",
            "travelers": 1,
        },
    )


def _hotel_decision():
    return AgentDecision(
        action=AgentActionType.SEARCH_HOTELS,
        reason_code="need_stay_options",
        parameters={"city_id": "city-goi", "rooms": 1},
    )


def _valid_intent_dict():
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
        "notes": "comfortable hotel",
    }


def _groq_response(payload):
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


def _provider_for_payload(payload):
    return GroqProvider(
        api_key="fake",
        model="test-model",
        timeout_seconds=1,
        transport=lambda *_: _groq_response(payload),
    )


def _decision_context():
    from app.llm import AgentDecisionContext

    return AgentDecisionContext(
        step=1,
        allowed_actions=[AgentActionType.SEARCH_FLIGHTS],
        state={"constraints": {}},
    )
