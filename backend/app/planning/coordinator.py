from pathlib import Path

from app.core.config import LLMProviderName, Settings, get_settings
from app.domain.models import (
    AgentTraceRecord,
    AgentTraceStatus,
    FlightOption,
    PlanningStatus,
    ReplanningOutcome,
    RouteInfo,
    TravelRequest,
    TripState,
)
from app.llm import ActionProposalProvider, AgentDecisionProvider, GroqProvider
from app.planning.agent_loop import AgentExecutionLoop, AgentLoopError
from app.planning.candidate_builder import CandidateBuildError, CandidateBuilder
from app.planning.explanation import ExplanationGenerator
from app.planning.replanning import ReplanningEngine
from app.planning.scoring import PreferenceScorer
from app.planning.selection import CandidateSelectionOverrides, selection_overrides_from_itinerary
from app.planning.validation import ConstraintValidator
from app.tools.local_data import LocalDataCatalog, load_catalog
from app.tools.external.errors import ExternalToolError
from app.tools.registry import DEFAULT_DATA_DIR, create_tool_registry
from app.tools.registry import ToolRegistry


class PlanningCoordinatorError(RuntimeError):
    """Raised when a structured request cannot produce an initial candidate."""


class PlanningCoordinator:
    def __init__(
        self,
        *,
        builder: CandidateBuilder,
        validator: ConstraintValidator,
        replanner: ReplanningEngine,
        scorer: PreferenceScorer,
        explainer: ExplanationGenerator,
        catalog: LocalDataCatalog,
        agent_loop: AgentExecutionLoop | None = None,
        deterministic_replanner: ReplanningEngine | None = None,
        allow_deterministic_agent_fallback: bool = True,
    ) -> None:
        self._builder = builder
        self._validator = validator
        self._replanner = replanner
        self._scorer = scorer
        self._explainer = explainer
        self._catalog = catalog
        self._agent_loop = agent_loop
        self._deterministic_replanner = deterministic_replanner or replanner
        self._allow_deterministic_agent_fallback = allow_deterministic_agent_fallback

    def plan(self, request: TravelRequest) -> TripState:
        constraints_snapshot = request.constraints.model_dump_json()
        if self._agent_loop is not None:
            try:
                state = self._agent_loop.plan(request)
            except AgentLoopError as exc:
                if not self._allow_deterministic_agent_fallback:
                    raise PlanningCoordinatorError(str(exc)) from exc
                state = self._plan_deterministic(request)
                state.tool_call_history = [
                    *exc.state.tool_call_history,
                    *state.tool_call_history,
                ]
                state.agent_trace = [
                    *exc.state.agent_trace,
                    AgentTraceRecord(
                        step=len(exc.state.agent_trace) + 1,
                        provider="fallback",
                        action="deterministic_planning_fallback",
                        reason_code=exc.code,
                        status=AgentTraceStatus.FALLBACK,
                        result_summary=(
                            "Groq agent failed safely; existing deterministic planner completed the request"
                        ),
                    ),
                ]
                state.requested_provider = "groq"
                state.provider_used = "fallback"
                state.fallback_used = True
                state.fallback_reason = exc.code
                state.agent_steps_used = exc.state.agent_steps_used
        else:
            state = self._plan_deterministic(request)

        return self._finalize(state, constraints_snapshot)

    def _plan_deterministic(self, request: TravelRequest) -> TripState:
        state = TripState(
            trip_id=request.request_id,
            original_request=request.natural_language_request,
            constraints=request.constraints.model_copy(deep=True),
            preferences=request.preferences.model_copy(deep=True),
            status=PlanningStatus.SEARCHING,
        )
        try:
            candidate = self._builder.build(request)
        except ExternalToolError as exc:
            raise PlanningCoordinatorError(
                f"Live {exc.provider} tool failed: {exc.code}"
            ) from exc
        except (CandidateBuildError, ValueError) as exc:
            raise PlanningCoordinatorError(f"Initial candidate could not be built: {exc}") from exc

        state.initial_itinerary = candidate.model_copy(deep=True)
        state.current_itinerary = candidate
        state.status = PlanningStatus.CANDIDATE_READY
        self._retain_candidates(state, candidate)

        state.status = PlanningStatus.VALIDATING
        validation = self._validator.validate_state(state)
        state.current_validation = validation
        state.validation_history.append(validation)

        if validation.is_valid:
            state.status = PlanningStatus.COMPLETED
        else:
            result = self._deterministic_replanner.replan(state)
            state = result.state
            if result.outcome == ReplanningOutcome.FEASIBLE:
                state.status = PlanningStatus.COMPLETED
            elif result.outcome == ReplanningOutcome.INFEASIBLE:
                state.status = PlanningStatus.INFEASIBLE
            else:
                state.status = PlanningStatus.FAILED

        return state

    def _finalize(self, state: TripState, constraints_snapshot: str) -> TripState:
        if state.status == PlanningStatus.COMPLETED:
            if state.current_itinerary is None or state.current_validation is None:
                raise RuntimeError("completed planning state lacks an itinerary or validation")
            state.preference_score = self._scorer.score(
                state.current_itinerary,
                state.constraints,
                state.preferences,
                state.current_validation,
                flight_candidates=state.flight_candidates,
                hotel_candidates=state.hotel_candidates,
                activity_candidates=state.activity_candidates,
                route_candidates=state.route_candidates,
            )
        else:
            state.preference_score = None

        if state.current_itinerary is not None:
            self._retain_candidates(state, state.current_itinerary)
        state.final_explanation = self._explainer.generate(state)

        if state.constraints.model_dump_json() != constraints_snapshot:
            raise RuntimeError("hard constraints changed during planning")
        return state

    def _retain_candidates(self, state: TripState, itinerary) -> None:
        selections = selection_overrides_from_itinerary(
            itinerary,
            state.constraints,
            self._catalog,
            flight_candidates=state.flight_candidates,
            hotel_candidates=state.hotel_candidates,
            activity_candidates=state.activity_candidates,
            route_candidates=state.route_candidates,
        )
        _merge_candidates(state, selections)


def create_planning_coordinator(
    settings: Settings | None = None,
    *,
    catalog: LocalDataCatalog | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
    agent_provider: AgentDecisionProvider | None = None,
    proposal_provider: ActionProposalProvider | None = None,
    tool_registry: ToolRegistry | None = None,
) -> PlanningCoordinator:
    configured = settings or get_settings()
    if configured.external_providers_enabled and configured.llm_provider != LLMProviderName.GROQ:
        raise ValueError("Live agent mode requires LLM_PROVIDER=groq")
    local_catalog = catalog or load_catalog(data_dir)
    tools = tool_registry or create_tool_registry(configured, catalog=local_catalog)
    builder = CandidateBuilder(tools)
    validator = ConstraintValidator(local_catalog)
    configured_agent_provider = agent_provider
    if configured.llm_provider.value == "groq" and configured_agent_provider is None:
        configured_agent_provider = GroqProvider(
            api_key=(
                configured.groq_api_key.get_secret_value()
                if configured.groq_api_key is not None
                else None
            ),
            model=configured.groq_model,
            timeout_seconds=configured.groq_timeout_seconds,
        )
    configured_proposal_provider = proposal_provider
    if configured_proposal_provider is None and configured_agent_provider is not None:
        if hasattr(configured_agent_provider, "propose_action"):
            configured_proposal_provider = configured_agent_provider  # type: ignore[assignment]
    replanner = ReplanningEngine(
        builder=builder,
        validator=validator,
        tools=tools,
        catalog=local_catalog,
        max_attempts=configured.max_replanning_attempts,
        max_tool_calls=configured.max_tool_calls,
        proposal_provider=configured_proposal_provider,
    )
    deterministic_replanner = ReplanningEngine(
        builder=builder,
        validator=validator,
        tools=tools,
        catalog=local_catalog,
        max_attempts=configured.max_replanning_attempts,
        max_tool_calls=configured.max_tool_calls,
    )
    agent_loop = (
        AgentExecutionLoop(
            provider=configured_agent_provider,
            tools=tools,
            builder=builder,
            validator=validator,
            replanner=replanner,
            max_steps=configured.max_agent_steps,
            max_tool_calls=configured.max_tool_calls,
        )
        if configured.llm_provider.value == "groq"
        and configured_agent_provider is not None
        else None
    )
    return PlanningCoordinator(
        builder=builder,
        validator=validator,
        replanner=replanner,
        scorer=PreferenceScorer(local_catalog),
        explainer=ExplanationGenerator(),
        catalog=local_catalog,
        agent_loop=agent_loop,
        deterministic_replanner=deterministic_replanner,
        allow_deterministic_agent_fallback=not configured.external_providers_enabled,
    )


def _merge_candidates(state: TripState, selections: CandidateSelectionOverrides) -> None:
    transports = list(selections.transport_by_segment.values())
    state.flight_candidates = _merge_by_id(
        state.flight_candidates,
        [item for item in transports if isinstance(item, FlightOption)],
    )
    state.route_candidates = _merge_by_id(
        state.route_candidates,
        [item for item in transports if isinstance(item, RouteInfo)],
    )
    state.hotel_candidates = _merge_by_id(
        state.hotel_candidates, list(selections.hotel_by_segment.values())
    )
    state.activity_candidates = _merge_by_id(
        state.activity_candidates,
        [item for items in selections.activities_by_segment.values() for item in items],
    )


def _merge_by_id(existing, selected):
    records = {item.id: item for item in existing}
    records.update({item.id: item for item in selected})
    return [records[item_id] for item_id in sorted(records)]
