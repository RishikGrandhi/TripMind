from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import ceil
from pathlib import Path

from app.core.config import Settings, get_settings
from app.domain.models import (
    ActivityOption,
    AgentTraceRecord,
    AgentTraceStatus,
    CorrectiveAction,
    CorrectiveActionType,
    FlightOption,
    HotelOption,
    ItemType,
    PlanningStatus,
    ReplanningAttempt,
    ReplanningOutcome,
    ReplanningResult,
    RouteInfo,
    ToolCallRecord,
    ToolCallStatus,
    TransportMode,
    TripState,
    ValidationResult,
    ViolationCode,
)
from app.llm.base import ActionProposalContext, ActionProposalProvider, LLMProviderError
from app.planning.candidate_builder import CandidateBuildError, CandidateBuilder
from app.planning.replanning.policy import ReplanningPolicy
from app.planning.selection import (
    CandidateSelectionOverrides,
    TransportOption,
    selection_overrides_from_itinerary,
)
from app.planning.validation import ConstraintValidator
from app.tools.local_data import LocalDataCatalog, load_catalog
from app.tools.registry import DEFAULT_DATA_DIR, ToolRegistry, create_tool_registry

DETERMINISTIC_HISTORY_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class ActionExecution:
    overrides: CandidateSelectionOverrides
    changed: bool
    reason: str
    tool_records: list[ToolCallRecord]
    before_component_id: str | None = None
    after_component_id: str | None = None
    before_value: Decimal | int | str | None = None
    after_value: Decimal | int | str | None = None
    duration_effect_minutes: int | None = None
    tool_name: str | None = None
    tool_limit_reached: bool = False
    facts: dict[str, object] = field(default_factory=dict)


class ReplanningEngine:
    def __init__(
        self,
        *,
        builder: CandidateBuilder,
        validator: ConstraintValidator,
        tools: ToolRegistry,
        catalog: LocalDataCatalog,
        policy: ReplanningPolicy | None = None,
        proposal_provider: ActionProposalProvider | None = None,
        max_attempts: int = 3,
        max_tool_calls: int = 20,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        self._builder = builder
        self._validator = validator
        self._tools = tools
        self._catalog = catalog
        self._policy = policy or ReplanningPolicy()
        self._proposal_provider = proposal_provider
        self._max_attempts = max_attempts
        self._max_tool_calls = max_tool_calls

    def replan(self, state: TripState) -> ReplanningResult:
        constraints_snapshot = state.constraints.model_dump_json()
        working = state.model_copy(deep=True)
        new_attempts = 0
        new_tool_calls = 0

        try:
            if working.current_itinerary is None:
                working.current_itinerary = self._builder.build(working)
            validation = self._validator.validate_state(working)
        except (CandidateBuildError, ValueError) as exc:
            working.status = PlanningStatus.FAILED
            return ReplanningResult(
                outcome=ReplanningOutcome.FAILED,
                state=working,
                termination_reason=f"candidate_unplannable: {exc}",
                attempts_used=0,
                tool_calls_used=0,
            )

        working.current_validation = validation
        if not working.validation_history or working.validation_history[-1] != validation:
            working.validation_history.append(validation)
        if validation.is_valid:
            working.status = PlanningStatus.COMPLETED
            return ReplanningResult(
                outcome=ReplanningOutcome.FEASIBLE,
                state=working,
                termination_reason="candidate_already_feasible",
                attempts_used=0,
                tool_calls_used=0,
            )

        try:
            overrides = selection_overrides_from_itinerary(
                working.current_itinerary,
                working.constraints,
                self._catalog,
                flight_candidates=working.flight_candidates,
                hotel_candidates=working.hotel_candidates,
                activity_candidates=working.activity_candidates,
                route_candidates=working.route_candidates,
            )
        except ValueError as exc:
            working.status = PlanningStatus.FAILED
            return ReplanningResult(
                outcome=ReplanningOutcome.FAILED,
                state=working,
                termination_reason=f"selection_state_unavailable: {exc}",
                attempts_used=0,
                tool_calls_used=0,
            )

        working.status = PlanningStatus.REPLANNING
        attempted = {
            (action.target_violation, action.action)
            for attempt in working.replanning_attempts
            for action in attempt.actions
        }
        seen_states = {
            attempt.state_fingerprint
            for attempt in working.replanning_attempts
            if attempt.state_fingerprint
        }
        seen_states.add(_state_fingerprint(working.current_itinerary, validation))
        termination_reason = "no_legal_action"

        while len(working.replanning_attempts) < self._max_attempts:
            actions = self._policy.propose_actions(
                validation, working.current_itinerary, attempted
            )
            if not actions:
                termination_reason = "no_legal_corrective_action"
                break
            action = self._select_action(working, validation, actions)
            if not self._policy.is_legal(action, validation):
                termination_reason = "policy_proposed_illegal_action"
                break

            attempted.add((action.target_violation, action.action))
            attempt_number = len(working.replanning_attempts) + 1
            remaining_calls = self._max_tool_calls - len(working.tool_call_history)
            execution = self._execute_action(
                action=action,
                state=working,
                overrides=overrides,
                remaining_tool_calls=max(0, remaining_calls),
                first_tool_sequence=len(working.tool_call_history) + 1,
            )
            working.tool_call_history.extend(execution.tool_records)
            new_tool_calls += len(execution.tool_records)

            if not execution.changed:
                recorded_action = action.model_copy(
                    update={"parameters": {**action.parameters, **execution.facts}}
                )
                attempt = self._attempt_record(
                    attempt_number=attempt_number,
                    validation=validation,
                    action=recorded_action,
                    execution=execution,
                    after_validation=validation,
                    before_total=working.current_itinerary.costs.total,
                    after_total=working.current_itinerary.costs.total,
                    succeeded=False,
                    state_fingerprint=_state_fingerprint(
                        working.current_itinerary, validation
                    ),
                )
                working.replanning_attempts.append(attempt)
                new_attempts += 1
                termination_reason = execution.reason
                if execution.tool_limit_reached:
                    break
                continue

            try:
                rebuilt = self._builder.build(working, execution.overrides)
                self._retain_selected_candidates(working, execution.overrides)
                after_validation = self._validator.validate(
                    rebuilt,
                    working.constraints,
                    flight_candidates=working.flight_candidates,
                    hotel_candidates=working.hotel_candidates,
                    activity_candidates=working.activity_candidates,
                    route_candidates=working.route_candidates,
                )
            except (CandidateBuildError, ValueError) as exc:
                attempt = self._attempt_record(
                    attempt_number=attempt_number,
                    validation=validation,
                    action=action,
                    execution=execution,
                    after_validation=validation,
                    before_total=working.current_itinerary.costs.total,
                    after_total=working.current_itinerary.costs.total,
                    succeeded=False,
                    state_fingerprint=_state_fingerprint(
                        working.current_itinerary, validation
                    ),
                    outcome=f"candidate_rebuild_failed: {exc}",
                )
                working.replanning_attempts.append(attempt)
                new_attempts += 1
                working.status = PlanningStatus.FAILED
                return ReplanningResult(
                    outcome=ReplanningOutcome.FAILED,
                    state=working,
                    termination_reason=f"candidate_rebuild_failed: {exc}",
                    attempts_used=new_attempts,
                    tool_calls_used=new_tool_calls,
                )

            before_total = working.current_itinerary.costs.total
            after_total = rebuilt.costs.total
            meaningful = self._is_meaningful_improvement(
                action, execution, before_total, after_total
            )
            fingerprint = _state_fingerprint(rebuilt, after_validation)
            repeated_state = fingerprint in seen_states
            succeeded = meaningful and not repeated_state
            outcome = (
                "improved"
                if succeeded
                else "repeated_state" if repeated_state else "no_meaningful_improvement"
            )
            enriched_action = action.model_copy(
                update={
                    "parameters": {
                        **action.parameters,
                        "before_component_id": execution.before_component_id,
                        "after_component_id": execution.after_component_id,
                        "before_total": before_total,
                        "after_total": after_total,
                        "savings": before_total - after_total,
                        **execution.facts,
                    }
                }
            )
            attempt = self._attempt_record(
                attempt_number=attempt_number,
                validation=validation,
                action=enriched_action,
                execution=execution,
                after_validation=after_validation,
                before_total=before_total,
                after_total=after_total,
                succeeded=succeeded,
                state_fingerprint=fingerprint,
                outcome=outcome,
            )
            working.replanning_attempts.append(attempt)
            working.validation_history.append(after_validation)
            new_attempts += 1

            if not succeeded:
                termination_reason = outcome
                if repeated_state:
                    break
                continue

            seen_states.add(fingerprint)
            overrides = execution.overrides
            working.current_itinerary = rebuilt
            working.current_validation = after_validation
            validation = after_validation
            self._retain_selected_candidates(working, overrides)
            if validation.is_valid:
                working.status = PlanningStatus.COMPLETED
                if working.constraints.model_dump_json() != constraints_snapshot:
                    raise RuntimeError("hard constraints changed during replanning")
                return ReplanningResult(
                    outcome=ReplanningOutcome.FEASIBLE,
                    state=working,
                    termination_reason="candidate_repaired",
                    attempts_used=new_attempts,
                    tool_calls_used=new_tool_calls,
                )
            termination_reason = "remaining_constraint_violations"

        if working.constraints.model_dump_json() != constraints_snapshot:
            raise RuntimeError("hard constraints changed during replanning")
        if len(working.replanning_attempts) >= self._max_attempts:
            termination_reason = "maximum_replanning_attempts_reached"
        elif len(working.tool_call_history) >= self._max_tool_calls:
            termination_reason = "maximum_tool_calls_reached"
        working.status = PlanningStatus.INFEASIBLE
        return ReplanningResult(
            outcome=ReplanningOutcome.INFEASIBLE,
            state=working,
            termination_reason=termination_reason,
            attempts_used=new_attempts,
            tool_calls_used=new_tool_calls,
        )

    def _select_action(
        self,
        state: TripState,
        validation: ValidationResult,
        candidates: list[CorrectiveAction],
    ) -> CorrectiveAction:
        if self._proposal_provider is None:
            return candidates[0]
        provider_name = getattr(self._proposal_provider, "name", "configured_provider")
        context = ActionProposalContext(
            violation_codes=list(
                dict.fromkeys(item.code for item in validation.violations)
            ),
            allowed_actions=list(
                dict.fromkeys(item.action for item in candidates)
            ),
            state_summary={
                "constraints": state.constraints.model_dump(mode="json"),
                "current_total": (
                    str(state.current_itinerary.costs.total)
                    if state.current_itinerary is not None
                    else None
                ),
                "selected_option_ids": (
                    [
                        item.option_id
                        for day in state.current_itinerary.days
                        for item in day.items
                    ]
                    if state.current_itinerary is not None
                    else []
                ),
                "violations": [
                    item.model_dump(mode="json") for item in validation.violations
                ],
                "previous_attempts": [
                    attempt.model_dump(mode="json", exclude_none=True)
                    for attempt in state.replanning_attempts
                ],
                "tool_results": [
                    record.model_dump(mode="json", exclude_none=True)
                    for record in state.tool_call_history
                ],
            },
            candidate_actions=candidates,
        )
        try:
            proposal = self._proposal_provider.propose_action(context)
        except (LLMProviderError, ValueError) as exc:
            reason = exc.code if isinstance(exc, LLMProviderError) else "invalid_proposal"
            state.agent_trace.append(
                AgentTraceRecord(
                    step=len(state.agent_trace) + 1,
                    provider=provider_name,
                    action="propose_corrective_action",
                    reason_code=reason,
                    status=AgentTraceStatus.FALLBACK,
                    result_summary=(
                        "Provider proposal failed; deterministic policy selected a legal action"
                    ),
                    violation=validation.violations[0].code,
                )
            )
            return candidates[0]

        matches = [
            candidate
            for candidate in candidates
            if candidate.action == proposal.action
            and (proposal.target_id is None or candidate.target_id == proposal.target_id)
        ]
        if not matches or not self._policy.is_legal(matches[0], validation):
            state.agent_trace.append(
                AgentTraceRecord(
                    step=len(state.agent_trace) + 1,
                    provider=provider_name,
                    action=proposal.action.value,
                    reason_code=proposal.reason_code,
                    parameters={"target_id": proposal.target_id},
                    status=AgentTraceStatus.REJECTED,
                    result_summary=(
                        "Illegal or unusable corrective proposal rejected; "
                        "deterministic policy fallback selected"
                    ),
                    violation=validation.violations[0].code,
                )
            )
            return candidates[0]

        selected = matches[0].model_copy(
            update={
                "parameters": {
                    **matches[0].parameters,
                    "proposal_provider": provider_name,
                    "proposal_reason_code": proposal.reason_code,
                }
            }
        )
        state.agent_trace.append(
            AgentTraceRecord(
                step=len(state.agent_trace) + 1,
                provider=provider_name,
                action=selected.action.value,
                reason_code=proposal.reason_code,
                parameters={"target_id": selected.target_id},
                status=AgentTraceStatus.APPROVED,
                result_summary="Corrective proposal authorized by deterministic policy",
                violation=selected.target_violation,
            )
        )
        return selected

    def _execute_action(
        self,
        *,
        action: CorrectiveAction,
        state: TripState,
        overrides: CandidateSelectionOverrides,
        remaining_tool_calls: int,
        first_tool_sequence: int,
    ) -> ActionExecution:
        if action.action == CorrectiveActionType.SEARCH_CHEAPER_FLIGHT:
            return self._replace_flight(
                action, state, overrides, remaining_tool_calls, first_tool_sequence
            )
        if action.action == CorrectiveActionType.SEARCH_CHEAPER_HOTEL:
            return self._replace_hotel(
                action, state, overrides, remaining_tool_calls, first_tool_sequence
            )
        if action.action == CorrectiveActionType.SEARCH_CHEAPER_TRANSPORT:
            return self._replace_route(
                action,
                state,
                overrides,
                remaining_tool_calls,
                first_tool_sequence,
                faster=False,
                require_feasible=False,
            )
        if action.action == CorrectiveActionType.SEARCH_FASTER_TRANSPORT:
            return self._replace_transport(
                action,
                state,
                overrides,
                remaining_tool_calls,
                first_tool_sequence,
                require_faster=True,
            )
        if action.action == CorrectiveActionType.SELECT_FEASIBLE_ROUTE:
            return self._replace_transport(
                action,
                state,
                overrides,
                remaining_tool_calls,
                first_tool_sequence,
                require_faster=False,
            )
        if action.action == CorrectiveActionType.REMOVE_OPTIONAL_ACTIVITY:
            return self._remove_activity(action, overrides)
        return ActionExecution(overrides, False, "unsupported_action", [])

    def _replace_flight(
        self,
        action,
        state,
        overrides,
        remaining,
        sequence,
    ) -> ActionExecution:
        segment = _find_transport_segment(overrides, action.target_id, FlightOption)
        if segment is None:
            return ActionExecution(overrides, False, "target_flight_not_selected", [])
        if remaining < 1:
            return ActionExecution(overrides, False, "maximum_tool_calls_reached", [], tool_limit_reached=True)
        current = overrides.transport_by_segment[segment]
        assert isinstance(current, FlightOption)
        ceiling = (
            state.constraints.max_flight_price
            if action.target_violation == ViolationCode.FLIGHT_COST_EXCEEDED
            else current.price
        )
        results = self._tools.flights.search_flights(
            current.origin_city_id,
            current.destination_city_id,
            travel_date=current.departure.date(),
            max_price=None,
            travelers=state.constraints.travelers,
        )
        record = _tool_record(
            sequence,
            "FlightSearchTool.search_flights",
            {
                "origin_city_id": current.origin_city_id,
                "destination_city_id": current.destination_city_id,
                "travel_date": current.departure.date(),
                "max_price": None,
                "travelers": state.constraints.travelers,
            },
            len(results),
        )
        alternatives = [item for item in results if item.id != current.id and item.price < current.price]
        if action.target_violation == ViolationCode.FLIGHT_COST_EXCEEDED:
            alternatives = [item for item in alternatives if item.price <= ceiling]
        if not alternatives:
            facts = {}
            if results:
                facts["minimum_available_fare"] = min(item.price for item in results)
            return ActionExecution(
                overrides,
                False,
                "no_cheaper_flight_available",
                [record],
                current.id,
                current.id,
                current.price,
                current.price,
                tool_name="FlightSearchTool.search_flights",
                facts=facts,
            )
        replacement = min(alternatives, key=lambda item: (item.price, item.duration_minutes, item.id))
        changed = overrides.model_copy(deep=True)
        changed.transport_by_segment[segment] = replacement
        return ActionExecution(
            changed,
            True,
            "cheaper_flight_selected",
            [record],
            current.id,
            replacement.id,
            current.price,
            replacement.price,
            duration_effect_minutes=replacement.duration_minutes - current.duration_minutes,
            tool_name="FlightSearchTool.search_flights",
        )

    def _replace_hotel(self, action, state, overrides, remaining, sequence) -> ActionExecution:
        segment = _find_hotel_segment(overrides, action.target_id)
        if segment is None:
            return ActionExecution(overrides, False, "target_hotel_not_selected", [])
        if remaining < 1:
            return ActionExecution(overrides, False, "maximum_tool_calls_reached", [], tool_limit_reached=True)
        current = overrides.hotel_by_segment[segment]
        ceiling = (
            state.constraints.max_hotel_price_per_night
            if action.target_violation == ViolationCode.HOTEL_COST_EXCEEDED
            else current.price_per_night
        )
        rooms = ceil(state.constraints.travelers / 2)
        check_in = _segment_travel_date(state, segment)
        check_out = (
            _segment_travel_date(state, segment + 1)
            if segment + 1 < len(state.constraints.destination_city_ids)
            else state.constraints.end_date
        )
        results = self._tools.hotels.search_hotels(
            current.city_id,
            max_price_per_night=None,
            rooms=rooms,
            check_in=check_in,
            check_out=check_out,
            adults=state.constraints.travelers,
        )
        record = _tool_record(
            sequence,
            "HotelSearchTool.search_hotels",
            {
                "city_id": current.city_id,
                "max_price_per_night": None,
                "rooms": rooms,
                "check_in": check_in,
                "check_out": check_out,
                "adults": state.constraints.travelers,
            },
            len(results),
        )
        alternatives = [
            item
            for item in results
            if item.id != current.id and item.price_per_night < current.price_per_night
        ]
        if action.target_violation == ViolationCode.HOTEL_COST_EXCEEDED:
            alternatives = [item for item in alternatives if item.price_per_night <= ceiling]
        if not alternatives:
            facts = {}
            if results:
                facts["minimum_available_nightly_rate"] = min(
                    item.price_per_night for item in results
                )
            return ActionExecution(
                overrides,
                False,
                "no_cheaper_hotel_available",
                [record],
                current.id,
                current.id,
                current.price_per_night,
                current.price_per_night,
                tool_name="HotelSearchTool.search_hotels",
                facts=facts,
            )
        replacement = min(
            alternatives,
            key=lambda item: (
                item.price_per_night,
                -(item.rating or Decimal("0")),
                item.id,
            ),
        )
        changed = overrides.model_copy(deep=True)
        changed.hotel_by_segment[segment] = replacement
        return ActionExecution(
            changed,
            True,
            "cheaper_hotel_selected",
            [record],
            current.id,
            replacement.id,
            current.price_per_night,
            replacement.price_per_night,
            tool_name="HotelSearchTool.search_hotels",
        )

    def _replace_route(
        self,
        action,
        state,
        overrides,
        remaining,
        sequence,
        *,
        faster,
        require_feasible,
    ) -> ActionExecution:
        return self._replace_transport(
            action,
            state,
            overrides,
            remaining,
            sequence,
            require_faster=faster,
            routes_only=True,
            require_feasible=require_feasible,
        )

    def _replace_transport(
        self,
        action,
        state,
        overrides,
        remaining,
        sequence,
        *,
        require_faster: bool,
        routes_only: bool = False,
        require_feasible: bool = True,
    ) -> ActionExecution:
        segment = _find_transport_segment(overrides, action.target_id)
        if segment is None:
            return ActionExecution(overrides, False, "target_transport_not_selected", [])
        current = overrides.transport_by_segment[segment]
        candidates: list[TransportOption] = []
        records: list[ToolCallRecord] = []
        travel_date = _segment_travel_date(state, segment)
        modes = state.constraints.allowed_transport_modes
        for mode in modes:
            if len(records) >= remaining:
                break
            if mode == TransportMode.FLIGHT:
                if routes_only:
                    continue
                results = self._tools.flights.search_flights(
                    current.origin_city_id,
                    current.destination_city_id,
                    travel_date=travel_date,
                    travelers=state.constraints.travelers,
                )
                records.append(
                    _tool_record(
                        sequence + len(records),
                        "FlightSearchTool.search_flights",
                        {
                            "origin_city_id": current.origin_city_id,
                            "destination_city_id": current.destination_city_id,
                            "travel_date": travel_date,
                            "travelers": state.constraints.travelers,
                        },
                        len(results),
                    )
                )
                candidates.extend(results)
                continue
            result = self._tools.routes.calculate_route(
                current.origin_city_id, current.destination_city_id, mode
            )
            records.append(
                _tool_record(
                    sequence + len(records),
                    "RouteTool.calculate_route",
                    {
                        "origin_city_id": current.origin_city_id,
                        "destination_city_id": current.destination_city_id,
                        "mode": mode,
                    },
                    1 if result.route is not None else 0,
                )
            )
            if result.feasible and result.route is not None:
                candidates.append(result.route)

        if not records and remaining < 1:
            return ActionExecution(overrides, False, "maximum_tool_calls_reached", [], tool_limit_reached=True)
        candidates = [item for item in candidates if item.id != current.id]
        if require_feasible:
            candidates = [
                item for item in candidates if not isinstance(item, RouteInfo) or item.is_feasible
            ]
        if require_faster:
            candidates = [item for item in candidates if item.duration_minutes < current.duration_minutes]
            ranking = lambda item: (item.duration_minutes, _transport_cost(item), item.id)
        else:
            if action.action == CorrectiveActionType.SEARCH_CHEAPER_TRANSPORT:
                candidates = [item for item in candidates if _transport_cost(item) < _transport_cost(current)]
                ranking = lambda item: (_transport_cost(item), item.duration_minutes, item.id)
            else:
                ranking = lambda item: (item.duration_minutes, _transport_cost(item), item.id)
        if not candidates:
            return ActionExecution(
                overrides,
                False,
                "no_improving_transport_available",
                records,
                current.id,
                current.id,
                _transport_cost(current),
                _transport_cost(current),
                tool_name=_tool_names(records),
                tool_limit_reached=len(records) >= remaining and remaining == 0,
            )
        replacement = min(candidates, key=ranking)
        changed = overrides.model_copy(deep=True)
        changed.transport_by_segment[segment] = replacement
        before_metric = current.duration_minutes if require_faster else _transport_cost(current)
        after_metric = replacement.duration_minutes if require_faster else _transport_cost(replacement)
        return ActionExecution(
            changed,
            True,
            "transport_replacement_selected",
            records,
            current.id,
            replacement.id,
            before_metric,
            after_metric,
            duration_effect_minutes=replacement.duration_minutes - current.duration_minutes,
            tool_name=_tool_names(records),
        )

    @staticmethod
    def _remove_activity(action, overrides) -> ActionExecution:
        changed = overrides.model_copy(deep=True)
        for options in changed.activities_by_segment.values():
            selected = next((item for item in options if item.id == action.target_id), None)
            if selected is not None:
                options.remove(selected)
                return ActionExecution(
                    changed,
                    True,
                    "optional_activity_removed",
                    [],
                    selected.id,
                    None,
                    selected.price or Decimal("0"),
                    Decimal("0"),
                )
        return ActionExecution(overrides, False, "target_activity_not_selected", [])

    @staticmethod
    def _is_meaningful_improvement(action, execution, before_total, after_total) -> bool:
        if action.target_violation == ViolationCode.BUDGET_EXCEEDED:
            return after_total < before_total
        if action.action in {
            CorrectiveActionType.SEARCH_CHEAPER_FLIGHT,
            CorrectiveActionType.SEARCH_CHEAPER_HOTEL,
            CorrectiveActionType.SEARCH_CHEAPER_TRANSPORT,
            CorrectiveActionType.REMOVE_OPTIONAL_ACTIVITY,
        }:
            return after_total < before_total
        if action.action == CorrectiveActionType.SEARCH_FASTER_TRANSPORT:
            return execution.duration_effect_minutes is not None and execution.duration_effect_minutes < 0
        if action.action == CorrectiveActionType.SELECT_FEASIBLE_ROUTE:
            return execution.before_component_id != execution.after_component_id
        return False

    @staticmethod
    def _attempt_record(
        *,
        attempt_number,
        validation,
        action,
        execution,
        after_validation,
        before_total,
        after_total,
        succeeded,
        state_fingerprint,
        outcome=None,
    ) -> ReplanningAttempt:
        return ReplanningAttempt(
            attempt_number=attempt_number,
            triggered_by=list(dict.fromkeys(item.code for item in validation.violations)),
            actions=[action],
            created_at=DETERMINISTIC_HISTORY_EPOCH + timedelta(seconds=attempt_number),
            succeeded=succeeded,
            before_component_id=execution.before_component_id,
            after_component_id=execution.after_component_id,
            before_value=execution.before_value,
            after_value=execution.after_value,
            cost_effect=after_total - before_total,
            duration_effect_minutes=execution.duration_effect_minutes,
            tool_name=execution.tool_name,
            validation_result=after_validation,
            outcome=outcome or execution.reason,
            state_fingerprint=state_fingerprint,
        )

    @staticmethod
    def _retain_selected_candidates(
        state: TripState, overrides: CandidateSelectionOverrides
    ) -> None:
        flights = [
            item
            for item in overrides.transport_by_segment.values()
            if isinstance(item, FlightOption)
        ]
        routes = [
            item
            for item in overrides.transport_by_segment.values()
            if isinstance(item, RouteInfo)
        ]
        hotels = list(overrides.hotel_by_segment.values())
        activities = [
            item for options in overrides.activities_by_segment.values() for item in options
        ]
        state.flight_candidates = _merge_by_id(state.flight_candidates, flights)
        state.route_candidates = _merge_by_id(state.route_candidates, routes)
        state.hotel_candidates = _merge_by_id(state.hotel_candidates, hotels)
        state.activity_candidates = _merge_by_id(state.activity_candidates, activities)


def create_replanning_engine(
    settings: Settings | None = None,
    *,
    catalog: LocalDataCatalog | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> ReplanningEngine:
    configured = settings or get_settings()
    local_catalog = catalog or load_catalog(data_dir)
    tools = create_tool_registry(configured, catalog=local_catalog)
    return ReplanningEngine(
        builder=CandidateBuilder(tools),
        validator=ConstraintValidator(local_catalog),
        tools=tools,
        catalog=local_catalog,
        max_attempts=configured.max_replanning_attempts,
        max_tool_calls=configured.max_tool_calls,
    )


def _find_transport_segment(overrides, target_id, expected_type=None):
    for index, option in overrides.transport_by_segment.items():
        if option.id == target_id and (expected_type is None or isinstance(option, expected_type)):
            return index
    return None


def _find_hotel_segment(overrides, target_id):
    return next(
        (index for index, option in overrides.hotel_by_segment.items() if option.id == target_id),
        None,
    )


def _segment_travel_date(state: TripState, segment: int) -> date:
    nights = (state.constraints.end_date - state.constraints.start_date).days
    count = len(state.constraints.destination_city_ids)
    return state.constraints.start_date + timedelta(days=(segment * nights) // count)


def _transport_cost(option: TransportOption) -> Decimal:
    return option.price if isinstance(option, FlightOption) else option.estimated_cost


def _tool_record(sequence, tool_name, arguments, result_count) -> ToolCallRecord:
    timestamp = DETERMINISTIC_HISTORY_EPOCH + timedelta(milliseconds=sequence)
    return ToolCallRecord(
        id=f"tool-call-{sequence:03d}",
        tool_name=tool_name,
        arguments=arguments,
        status=ToolCallStatus.SUCCEEDED,
        result_count=result_count,
        started_at=timestamp,
        completed_at=timestamp,
    )


def _tool_names(records: list[ToolCallRecord]) -> str | None:
    names = list(dict.fromkeys(record.tool_name for record in records))
    return ", ".join(names) if names else None


def _state_fingerprint(itinerary, validation: ValidationResult) -> str:
    items = [item for day in itinerary.days for item in day.items]
    selected = ",".join(
        item.option_id
        for item in items
        if item.item_type in {ItemType.FLIGHT, ItemType.ROUTE, ItemType.HOTEL, ItemType.ACTIVITY}
    )
    violations = ",".join(item.code.value for item in validation.violations)
    return f"total={itinerary.costs.total}|selected={selected}|violations={violations}"


def _merge_by_id(existing, selected):
    by_id = {item.id: item for item in existing}
    for item in selected:
        by_id[item.id] = item
    return [by_id[item_id] for item_id in sorted(by_id)]
