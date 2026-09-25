from pathlib import Path

from app.core.config import Settings, get_settings
from app.domain.models import (
    FlightOption,
    PlanningStatus,
    ReplanningOutcome,
    RouteInfo,
    TravelRequest,
    TripState,
)
from app.planning.candidate_builder import CandidateBuildError, CandidateBuilder
from app.planning.explanation import ExplanationGenerator
from app.planning.replanning import ReplanningEngine
from app.planning.scoring import PreferenceScorer
from app.planning.selection import CandidateSelectionOverrides, selection_overrides_from_itinerary
from app.planning.validation import ConstraintValidator
from app.tools.local_data import LocalDataCatalog, load_catalog
from app.tools.registry import DEFAULT_DATA_DIR, create_tool_registry


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
    ) -> None:
        self._builder = builder
        self._validator = validator
        self._replanner = replanner
        self._scorer = scorer
        self._explainer = explainer
        self._catalog = catalog

    def plan(self, request: TravelRequest) -> TripState:
        constraints_snapshot = request.constraints.model_dump_json()
        state = TripState(
            trip_id=request.request_id,
            original_request=request.natural_language_request,
            constraints=request.constraints.model_copy(deep=True),
            preferences=request.preferences.model_copy(deep=True),
            status=PlanningStatus.SEARCHING,
        )
        try:
            candidate = self._builder.build(request)
        except (CandidateBuildError, ValueError) as exc:
            raise PlanningCoordinatorError(f"Initial candidate could not be built: {exc}") from exc

        state.initial_itinerary = candidate.model_copy(deep=True)
        state.current_itinerary = candidate
        state.status = PlanningStatus.CANDIDATE_READY
        self._retain_candidates(state, candidate)

        state.status = PlanningStatus.VALIDATING
        validation = self._validator.validate(candidate, state.constraints)
        state.current_validation = validation
        state.validation_history.append(validation)

        if validation.is_valid:
            state.status = PlanningStatus.COMPLETED
        else:
            result = self._replanner.replan(state)
            state = result.state
            if result.outcome == ReplanningOutcome.FEASIBLE:
                state.status = PlanningStatus.COMPLETED
            elif result.outcome == ReplanningOutcome.INFEASIBLE:
                state.status = PlanningStatus.INFEASIBLE
            else:
                state.status = PlanningStatus.FAILED

        if state.status == PlanningStatus.COMPLETED:
            if state.current_itinerary is None or state.current_validation is None:
                raise RuntimeError("completed planning state lacks an itinerary or validation")
            state.preference_score = self._scorer.score(
                state.current_itinerary,
                state.constraints,
                state.preferences,
                state.current_validation,
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
            itinerary, state.constraints, self._catalog
        )
        _merge_candidates(state, selections)


def create_planning_coordinator(
    settings: Settings | None = None,
    *,
    catalog: LocalDataCatalog | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> PlanningCoordinator:
    configured = settings or get_settings()
    local_catalog = catalog or load_catalog(data_dir)
    tools = create_tool_registry(configured, catalog=local_catalog)
    builder = CandidateBuilder(tools)
    validator = ConstraintValidator(local_catalog)
    replanner = ReplanningEngine(
        builder=builder,
        validator=validator,
        tools=tools,
        catalog=local_catalog,
        max_attempts=configured.max_replanning_attempts,
        max_tool_calls=configured.max_tool_calls,
    )
    return PlanningCoordinator(
        builder=builder,
        validator=validator,
        replanner=replanner,
        scorer=PreferenceScorer(local_catalog),
        explainer=ExplanationGenerator(),
        catalog=local_catalog,
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
