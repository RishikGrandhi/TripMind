from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from math import ceil
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.models import (
    AgentTraceRecord,
    AgentTraceStatus,
    DataSource,
    PlanningStatus,
    ReplanningOutcome,
    ToolCallRecord,
    ToolCallStatus,
    TransportMode,
    TravelRequest,
    TripState,
    WeatherResult,
)
from app.llm.base import (
    AgentActionType,
    AgentDecision,
    AgentDecisionContext,
    AgentDecisionProvider,
    LLMProviderError,
)
from app.planning.candidate_builder import CandidateBuildError, CandidateBuilder
from app.planning.replanning import ReplanningEngine
from app.planning.validation import ConstraintValidator
from app.tools.external.errors import ExternalToolError
from app.tools.registry import ToolRegistry

AGENT_HISTORY_EPOCH = datetime(2000, 1, 2, tzinfo=UTC)


class AgentParameterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchFlightsParameters(AgentParameterModel):
    origin_city_id: str = Field(min_length=1)
    destination_city_id: str = Field(min_length=1)
    travel_date: date
    travelers: int = Field(ge=1, le=20)
    max_price: Decimal | None = Field(default=None, gt=0)


class SearchHotelsParameters(AgentParameterModel):
    city_id: str = Field(min_length=1)
    rooms: int = Field(ge=1, le=10)
    max_price_per_night: Decimal | None = Field(default=None, gt=0)
    min_rating: Decimal | None = Field(default=None, ge=0, le=5)
    required_amenities: set[str] = Field(default_factory=set)
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = Field(default=None, ge=1, le=20)


class SearchActivitiesParameters(AgentParameterModel):
    city_id: str = Field(min_length=1)
    category: str | None = None
    max_cost: Decimal | None = Field(default=None, ge=0)


class GetRouteParameters(AgentParameterModel):
    origin_city_id: str = Field(min_length=1)
    destination_city_id: str = Field(min_length=1)
    mode: TransportMode


class GetWeatherParameters(AgentParameterModel):
    city_id: str = Field(min_length=1)
    target_date: date


class AgentLoopError(RuntimeError):
    def __init__(self, code: str, message: str, state: TripState) -> None:
        super().__init__(message)
        self.code = code
        self.state = state


class AgentExecutionLoop:
    """Bounded Groq-controlled loop over registered TripMind capabilities."""

    def __init__(
        self,
        *,
        provider: AgentDecisionProvider,
        tools: ToolRegistry,
        builder: CandidateBuilder,
        validator: ConstraintValidator,
        replanner: ReplanningEngine,
        max_steps: int,
        max_tool_calls: int,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._builder = builder
        self._validator = validator
        self._replanner = replanner
        self._max_steps = max_steps
        self._max_tool_calls = max_tool_calls

    def plan(self, request: TravelRequest) -> TripState:
        constraints_snapshot = request.constraints.model_dump_json()
        state = TripState(
            trip_id=request.request_id,
            original_request=request.natural_language_request,
            constraints=request.constraints.model_copy(deep=True),
            preferences=request.preferences.model_copy(deep=True),
            status=PlanningStatus.SEARCHING,
            requested_provider=self._provider.name,
            provider_used=self._provider.name,
        )
        seen: set[str] = set()

        for step in range(1, self._max_steps + 1):
            state.agent_steps_used = step
            allowed = self._allowed_actions(state)
            if not allowed:
                code = (
                    "maximum_tool_calls_reached"
                    if len(state.tool_call_history) >= self._max_tool_calls
                    else "no_legal_action"
                )
                self._fail(
                    state,
                    code,
                    "No legal agent action remains within the configured bounds",
                )
            context = AgentDecisionContext(
                step=step,
                allowed_actions=allowed,
                state=self._state_observation(state),
                previous_action_results=[
                    item.model_dump(mode="json", exclude_none=True)
                    for item in state.agent_trace[-12:]
                ],
            )
            try:
                decision = self._provider.decide_next_action(context)
            except (LLMProviderError, ValidationError, ValueError) as exc:
                code = exc.code if isinstance(exc, LLMProviderError) else "invalid_action"
                self._record(
                    state,
                    action="provider_decision",
                    reason_code=code,
                    status=AgentTraceStatus.FAILED,
                    result_summary=str(exc),
                )
                raise AgentLoopError(code, str(exc), state) from exc

            if decision.action not in allowed:
                self._record(
                    state,
                    decision=decision,
                    status=AgentTraceStatus.REJECTED,
                    result_summary="Action is not legal for the current TripState",
                )
                raise AgentLoopError(
                    "illegal_action",
                    f"Groq proposed disallowed action {decision.action.value}",
                    state,
                )

            signature = self._decision_signature(state, decision)
            if signature in seen:
                self._record(
                    state,
                    decision=decision,
                    status=AgentTraceStatus.REJECTED,
                    result_summary="Repeated state/action pair rejected",
                )
                raise AgentLoopError(
                    "repeated_state_action",
                    "Groq repeated the same action against an unchanged planning state",
                    state,
                )
            seen.add(signature)

            try:
                terminal = self._execute(state, decision)
            except ExternalToolError as exc:
                self._record_failed_tool_call(state, decision, exc)
                self._record(
                    state,
                    decision=decision,
                    reason_code=f"external_tool_{exc.code}",
                    source=_provider_source(exc.provider),
                    status=AgentTraceStatus.FAILED,
                    result_summary=f"{exc.provider} live tool failed: {exc.code}",
                )
                raise AgentLoopError(
                    f"external_tool_{exc.code}",
                    f"{exc.provider} live tool failed: {exc.code}",
                    state,
                ) from exc
            except (ValidationError, ValueError, CandidateBuildError) as exc:
                self._record(
                    state,
                    decision=decision,
                    status=AgentTraceStatus.REJECTED,
                    result_summary=f"Invalid or unusable action parameters: {exc}",
                )
                raise AgentLoopError("invalid_action_parameters", str(exc), state) from exc
            if terminal:
                if state.constraints.model_dump_json() != constraints_snapshot:
                    raise RuntimeError("hard constraints changed during agent planning")
                return state

        self._record(
            state,
            action="agent_loop",
            reason_code="maximum_agent_steps_reached",
            status=AgentTraceStatus.FAILED,
            result_summary=f"Stopped after {self._max_steps} bounded steps",
        )
        raise AgentLoopError(
            "maximum_agent_steps_reached",
            f"Groq agent reached MAX_AGENT_STEPS={self._max_steps}",
            state,
        )

    def _allowed_actions(self, state: TripState) -> list[AgentActionType]:
        actions: list[AgentActionType] = []
        if len(state.tool_call_history) < self._max_tool_calls:
            if TransportMode.FLIGHT in state.constraints.allowed_transport_modes:
                actions.append(AgentActionType.SEARCH_FLIGHTS)
            actions.extend(
                [AgentActionType.SEARCH_HOTELS, AgentActionType.SEARCH_ACTIVITIES]
            )
            if any(
                mode != TransportMode.FLIGHT
                for mode in state.constraints.allowed_transport_modes
            ):
                actions.append(AgentActionType.GET_ROUTE)
            if self._tools.weather is not None:
                actions.append(AgentActionType.GET_WEATHER)
        if self._builder.has_required_gathered_candidates(state):
            actions.append(AgentActionType.BUILD_CANDIDATE)
        if state.current_itinerary is not None:
            actions.append(AgentActionType.VALIDATE)
        if state.current_validation is not None and not state.current_validation.is_valid:
            actions.append(AgentActionType.PROPOSE_CORRECTIVE_ACTION)
        if state.current_validation is not None and (
            state.current_validation.is_valid or state.status == PlanningStatus.INFEASIBLE
        ):
            actions.append(AgentActionType.FINISH)
        return list(dict.fromkeys(actions))

    def _execute(self, state: TripState, decision: AgentDecision) -> bool:
        if decision.action == AgentActionType.SEARCH_FLIGHTS:
            parameters = SearchFlightsParameters.model_validate(decision.parameters)
            self._authorize_flight_search(state, parameters)
            results = self._tools.flights.search_flights(**parameters.model_dump())
            state.flight_candidates = _merge_by_id(state.flight_candidates, results)
            self._record_tool_action(
                state,
                decision,
                "FlightSearchTool.search_flights",
                parameters,
                len(results),
                source=_result_source(results, self._tools.flights),
            )
            return False
        if decision.action == AgentActionType.SEARCH_HOTELS:
            parameters = SearchHotelsParameters.model_validate(decision.parameters)
            if parameters.check_in is None and parameters.check_out is None:
                check_in, check_out = self._hotel_dates(state, parameters.city_id)
                parameters = parameters.model_copy(
                    update={
                        "check_in": check_in,
                        "check_out": check_out,
                    }
                )
            if parameters.adults is None:
                parameters = parameters.model_copy(
                    update={"adults": state.constraints.travelers}
                )
            self._authorize_hotel_search(state, parameters)
            results = self._tools.hotels.search_hotels(**parameters.model_dump())
            state.hotel_candidates = _merge_by_id(state.hotel_candidates, results)
            self._record_tool_action(
                state,
                decision,
                "HotelSearchTool.search_hotels",
                parameters,
                len(results),
                source=_result_source(results, self._tools.hotels),
            )
            return False
        if decision.action == AgentActionType.SEARCH_ACTIVITIES:
            parameters = SearchActivitiesParameters.model_validate(decision.parameters)
            self._authorize_destination(state, parameters.city_id)
            results = self._tools.activities.search_activities(**parameters.model_dump())
            state.activity_candidates = _merge_by_id(state.activity_candidates, results)
            self._record_tool_action(
                state,
                decision,
                "ActivitySearchTool.search_activities",
                parameters,
                len(results),
                source=_result_source(results, self._tools.activities),
            )
            return False
        if decision.action == AgentActionType.GET_ROUTE:
            parameters = GetRouteParameters.model_validate(decision.parameters)
            self._authorize_route_search(state, parameters)
            result = self._tools.routes.calculate_route(**parameters.model_dump())
            if result.route is not None:
                state.route_candidates = _merge_by_id(
                    state.route_candidates, [result.route]
                )
            self._record_tool_action(
                state,
                decision,
                "RouteTool.calculate_route",
                parameters,
                1 if result.route is not None else 0,
                source=result.source,
                summary=(
                    f"Route feasible: {result.route.id}"
                    if result.feasible and result.route is not None
                    else f"No feasible route: {result.reason}"
                ),
            )
            return False
        if decision.action == AgentActionType.GET_WEATHER:
            parameters = GetWeatherParameters.model_validate(decision.parameters)
            self._authorize_weather(state, parameters)
            if self._tools.weather is None:
                raise ValueError("weather is not registered in the active tool mode")
            result = self._tools.weather.get_forecast(**parameters.model_dump())
            state.weather_results = _merge_weather(state.weather_results, result)
            self._record_tool_action(
                state,
                decision,
                "WeatherTool.get_forecast",
                parameters,
                len(result.forecasts),
                source=result.source,
                summary=(
                    f"Weather available with {len(result.forecasts)} forecast interval(s)"
                    if result.weather_available
                    else f"Weather unavailable: {result.reason}"
                ),
            )
            return False
        if decision.action == AgentActionType.BUILD_CANDIDATE:
            itinerary = self._builder.build_from_gathered_candidates(state)
            if state.initial_itinerary is None:
                state.initial_itinerary = itinerary.model_copy(deep=True)
            state.current_itinerary = itinerary
            state.current_validation = None
            state.status = PlanningStatus.CANDIDATE_READY
            self._record(
                state,
                decision=decision,
                status=AgentTraceStatus.SUCCEEDED,
                result_summary=(
                    f"Candidate built with authoritative total INR {itinerary.costs.total}"
                ),
            )
            return False
        if decision.action == AgentActionType.VALIDATE:
            if state.current_itinerary is None:
                raise ValueError("No candidate exists to validate")
            state.status = PlanningStatus.VALIDATING
            validation = self._validator.validate_state(state)
            state.current_validation = validation
            state.validation_history.append(validation)
            self._record(
                state,
                decision=decision,
                status=AgentTraceStatus.SUCCEEDED,
                result_summary=(
                    "Deterministic validation passed"
                    if validation.is_valid
                    else "Deterministic validation found: "
                    + ", ".join(item.code.value for item in validation.violations)
                ),
                violation=(validation.violations[0].code if validation.violations else None),
            )
            if validation.is_valid:
                state.status = PlanningStatus.COMPLETED
                return True
            return False
        if decision.action == AgentActionType.PROPOSE_CORRECTIVE_ACTION:
            if state.current_validation is None or state.current_validation.is_valid:
                raise ValueError("No active validation violation requires repair")
            result = self._replanner.replan(state)
            repaired = result.state
            state.__dict__.update(repaired.__dict__)
            latest_attempt = state.replanning_attempts[-1] if state.replanning_attempts else None
            self._record(
                state,
                decision=decision,
                status=AgentTraceStatus.SUCCEEDED,
                result_summary=(
                    f"Replanning outcome {result.outcome.value}: {result.termination_reason}"
                ),
                before_component_id=(latest_attempt.before_component_id if latest_attempt else None),
                after_component_id=(latest_attempt.after_component_id if latest_attempt else None),
                before_value=(latest_attempt.before_value if latest_attempt else None),
                after_value=(latest_attempt.after_value if latest_attempt else None),
            )
            return result.outcome in {
                ReplanningOutcome.FEASIBLE,
                ReplanningOutcome.INFEASIBLE,
            }
        if decision.action == AgentActionType.FINISH:
            if state.current_validation is None:
                raise ValueError("Cannot finish without deterministic validation")
            if state.current_validation.is_valid:
                state.status = PlanningStatus.COMPLETED
            elif state.status != PlanningStatus.INFEASIBLE:
                raise ValueError("Cannot finish while unhandled hard violations remain")
            self._record(
                state,
                decision=decision,
                status=AgentTraceStatus.SUCCEEDED,
                result_summary=f"Accepted terminal status {state.status.value}",
            )
            return True
        raise ValueError(f"Unsupported action {decision.action}")

    def _authorize_flight_search(
        self, state: TripState, parameters: SearchFlightsParameters
    ) -> None:
        expected_dates = self._segment_dates(state)
        pair = (parameters.origin_city_id, parameters.destination_city_id)
        if pair not in expected_dates:
            raise ValueError("flight search is not for a requested trip segment")
        if parameters.travel_date != expected_dates[pair]:
            raise ValueError("flight date does not match the requested segment date")
        if parameters.travelers != state.constraints.travelers:
            raise ValueError("traveler count must match the immutable request")
        if TransportMode.FLIGHT not in state.constraints.allowed_transport_modes:
            raise ValueError("flight is not an allowed transport mode")

    def _authorize_hotel_search(
        self, state: TripState, parameters: SearchHotelsParameters
    ) -> None:
        self._authorize_destination(state, parameters.city_id)
        expected_rooms = ceil(state.constraints.travelers / 2)
        if parameters.rooms != expected_rooms:
            raise ValueError("room count must match the immutable traveler count")
        expected_check_in, expected_check_out = self._hotel_dates(state, parameters.city_id)
        if (parameters.check_in, parameters.check_out) != (
            expected_check_in,
            expected_check_out,
        ):
            raise ValueError("hotel dates must match the requested destination segment")
        if parameters.adults != state.constraints.travelers:
            raise ValueError("hotel adult count must match the immutable request")

    @staticmethod
    def _hotel_dates(state: TripState, city_id: str) -> tuple[date, date]:
        destinations = state.constraints.destination_city_ids
        index = destinations.index(city_id)
        nights = (state.constraints.end_date - state.constraints.start_date).days
        check_in = state.constraints.start_date + timedelta(
            days=(index * nights) // len(destinations)
        )
        check_out = (
            state.constraints.start_date
            + timedelta(days=((index + 1) * nights) // len(destinations))
            if index + 1 < len(destinations)
            else state.constraints.end_date
        )
        return check_in, check_out

    def _authorize_route_search(
        self, state: TripState, parameters: GetRouteParameters
    ) -> None:
        if (
            parameters.origin_city_id,
            parameters.destination_city_id,
        ) not in self._segment_dates(state):
            raise ValueError("route search is not for a requested trip segment")
        if parameters.mode == TransportMode.FLIGHT:
            raise ValueError("flight searches must use FlightSearchTool")
        if parameters.mode not in state.constraints.allowed_transport_modes:
            raise ValueError("route mode is not allowed by the immutable request")

    def _authorize_weather(
        self, state: TripState, parameters: GetWeatherParameters
    ) -> None:
        self._authorize_destination(state, parameters.city_id)
        if not state.constraints.start_date <= parameters.target_date <= state.constraints.end_date:
            raise ValueError("weather date must fall within the requested trip window")

    @staticmethod
    def _authorize_destination(state: TripState, city_id: str) -> None:
        if city_id not in state.constraints.destination_city_ids:
            raise ValueError("city is not a requested mandatory destination")

    @staticmethod
    def _segment_dates(state: TripState) -> dict[tuple[str, str], date]:
        destinations = state.constraints.destination_city_ids
        nights = (state.constraints.end_date - state.constraints.start_date).days
        origin = state.constraints.origin_city_id
        result: dict[tuple[str, str], date] = {}
        for index, destination in enumerate(destinations):
            result[(origin, destination)] = state.constraints.start_date + timedelta(
                days=(index * nights) // len(destinations)
            )
            origin = destination
        return result

    def _record_tool_action(
        self,
        state: TripState,
        decision: AgentDecision,
        tool: str,
        parameters: BaseModel,
        result_count: int,
        *,
        summary: str | None = None,
        source: DataSource | None = None,
    ) -> None:
        sequence = len(state.tool_call_history) + 1
        timestamp = AGENT_HISTORY_EPOCH + timedelta(milliseconds=sequence)
        arguments = parameters.model_dump(mode="json")
        state.tool_call_history.append(
            ToolCallRecord(
                id=f"agent-tool-call-{sequence:03d}",
                tool_name=tool,
                arguments=arguments,
                status=ToolCallStatus.SUCCEEDED,
                result_count=result_count,
                started_at=timestamp,
                completed_at=timestamp,
            )
        )
        self._record(
            state,
            decision=decision,
            tool=tool,
            source=source,
            parameters=arguments,
            status=AgentTraceStatus.SUCCEEDED,
            result_summary=summary or f"Found {result_count} normalized option(s)",
        )

    def _record_failed_tool_call(
        self, state: TripState, decision: AgentDecision, error: ExternalToolError
    ) -> None:
        tool_names = {
            AgentActionType.SEARCH_FLIGHTS: "FlightSearchTool.search_flights",
            AgentActionType.SEARCH_HOTELS: "HotelSearchTool.search_hotels",
            AgentActionType.SEARCH_ACTIVITIES: "ActivitySearchTool.search_activities",
            AgentActionType.GET_ROUTE: "RouteTool.calculate_route",
            AgentActionType.GET_WEATHER: "WeatherTool.get_forecast",
        }
        tool_name = tool_names.get(decision.action)
        if tool_name is None:
            return
        sequence = len(state.tool_call_history) + 1
        timestamp = AGENT_HISTORY_EPOCH + timedelta(milliseconds=sequence)
        state.tool_call_history.append(
            ToolCallRecord(
                id=f"agent-tool-call-{sequence:03d}",
                tool_name=tool_name,
                arguments=decision.parameters,
                status=ToolCallStatus.FAILED,
                started_at=timestamp,
                completed_at=timestamp,
                error=f"{error.provider}:{error.code}",
            )
        )

    def _record(
        self,
        state: TripState,
        *,
        decision: AgentDecision | None = None,
        action: str | None = None,
        reason_code: str | None = None,
        tool: str | None = None,
        source: DataSource | None = None,
        parameters: dict[str, Any] | None = None,
        status: AgentTraceStatus,
        result_summary: str | None = None,
        violation=None,
        before_component_id: str | None = None,
        after_component_id: str | None = None,
        before_value=None,
        after_value=None,
    ) -> None:
        state.agent_trace.append(
            AgentTraceRecord(
                step=len(state.agent_trace) + 1,
                provider=self._provider.name,
                action=action or decision.action.value,
                reason_code=reason_code or (decision.reason_code if decision else None),
                tool=tool,
                source=source,
                parameters=parameters or (decision.parameters if decision else {}),
                status=status,
                result_summary=result_summary,
                violation=violation,
                before_component_id=before_component_id,
                after_component_id=after_component_id,
                before_value=before_value,
                after_value=after_value,
            )
        )

    def _fail(self, state: TripState, code: str, message: str) -> None:
        self._record(
            state,
            action="agent_loop",
            reason_code=code,
            status=AgentTraceStatus.FAILED,
            result_summary=message,
        )
        raise AgentLoopError(code, message, state)

    def _state_observation(self, state: TripState) -> dict[str, Any]:
        itinerary = state.current_itinerary
        validation = state.current_validation
        return {
            "trip_id": state.trip_id,
            "status": state.status.value,
            "constraints": state.constraints.model_dump(mode="json", exclude_none=True),
            "preferences": state.preferences.model_dump(mode="json", exclude_none=True),
            "flight_candidates": [_flight_summary(item) for item in state.flight_candidates],
            "hotel_candidates": [_hotel_summary(item) for item in state.hotel_candidates],
            "activity_candidates": [
                _activity_summary(item) for item in state.activity_candidates
            ],
            "route_candidates": [_route_summary(item) for item in state.route_candidates],
            "weather_results": [
                item.model_dump(mode="json", exclude_none=True)
                for item in state.weather_results
            ],
            "selected_option_ids": (
                [item.option_id for day in itinerary.days for item in day.items]
                if itinerary
                else []
            ),
            "current_cost": str(itinerary.costs.total) if itinerary else None,
            "current_itinerary": (
                {
                    "selected_option_ids": [
                        item.option_id for day in itinerary.days for item in day.items
                    ],
                    "total_cost": str(itinerary.costs.total),
                }
                if itinerary
                else None
            ),
            "current_validation": (
                {
                    "is_valid": validation.is_valid,
                    "violations": [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in validation.violations
                    ],
                }
                if validation
                else None
            ),
            "limits": {
                "agent_steps_used": state.agent_steps_used,
                "agent_steps_remaining": max(0, self._max_steps - state.agent_steps_used),
                "tool_calls_used": len(state.tool_call_history),
                "tool_calls_remaining": max(
                    0, self._max_tool_calls - len(state.tool_call_history)
                ),
                "replanning_attempts_used": len(state.replanning_attempts),
            },
        }

    @staticmethod
    def _decision_signature(state: TripState, decision: AgentDecision) -> str:
        semantic_state = {
            "flights": [item.id for item in state.flight_candidates],
            "hotels": [item.id for item in state.hotel_candidates],
            "activities": [item.id for item in state.activity_candidates],
            "routes": [item.id for item in state.route_candidates],
            "weather": [
                f"{item.city_id}:{item.requested_date}:{item.weather_available}:{item.reason}"
                for item in state.weather_results
            ],
            "itinerary": (
                [
                    item.option_id
                    for day in state.current_itinerary.days
                    for item in day.items
                ]
                if state.current_itinerary
                else []
            ),
            "violations": (
                [item.code.value for item in state.current_validation.violations]
                if state.current_validation
                else []
            ),
        }
        payload = {
            "state": semantic_state,
            "action": decision.model_dump(mode="json", exclude_none=True),
        }
        return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _merge_by_id(existing, selected):
    records = {item.id: item for item in existing}
    records.update({item.id: item for item in selected})
    return [records[item_id] for item_id in sorted(records)]


def _merge_weather(existing: list[WeatherResult], selected: WeatherResult) -> list[WeatherResult]:
    records = {(item.city_id, item.requested_date): item for item in existing}
    records[(selected.city_id, selected.requested_date)] = selected
    return [records[key] for key in sorted(records)]


def _result_source(results, tool) -> DataSource | None:
    if results:
        return results[0].source
    source = getattr(tool, "provider_source", None)
    return source if isinstance(source, DataSource) else None


def _provider_source(provider: str) -> DataSource | None:
    try:
        return DataSource(provider)
    except ValueError:
        return None


def _flight_summary(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "route": f"{item.origin_city_id}->{item.destination_city_id}",
        "departure": item.departure.isoformat(),
        "duration_minutes": item.duration_minutes,
        "stops": item.stops,
        "price": str(item.price),
        "source": item.source.value,
    }


def _hotel_summary(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "city_id": item.city_id,
        "price_per_night": str(item.price_per_night),
        "rating": str(item.rating) if item.rating is not None else None,
        "amenities": item.amenities,
        "source": item.source.value,
    }


def _activity_summary(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "city_id": item.city_id,
        "category": item.category,
        "duration_minutes": item.duration_minutes,
        "price": str(item.price) if item.price is not None else None,
        "price_source": item.price_source.value,
        "source": item.source.value,
    }


def _route_summary(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "route": f"{item.origin_city_id}->{item.destination_city_id}",
        "mode": item.mode.value,
        "duration_minutes": item.duration_minutes,
        "distance_km": str(item.distance_km),
        "estimated_cost": str(item.estimated_cost),
        "is_feasible": item.is_feasible,
        "source": item.source.value,
    }
