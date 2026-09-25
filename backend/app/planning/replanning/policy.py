from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.domain.models import (
    CorrectiveAction,
    CorrectiveActionType,
    ItemType,
    Itinerary,
    ValidationResult,
    ViolationCode,
)

ACTION_TYPES_BY_VIOLATION: dict[ViolationCode, tuple[CorrectiveActionType, ...]] = {
    ViolationCode.BUDGET_EXCEEDED: (
        CorrectiveActionType.SEARCH_CHEAPER_FLIGHT,
        CorrectiveActionType.SEARCH_CHEAPER_HOTEL,
        CorrectiveActionType.SEARCH_CHEAPER_TRANSPORT,
        CorrectiveActionType.REMOVE_OPTIONAL_ACTIVITY,
    ),
    ViolationCode.FLIGHT_COST_EXCEEDED: (
        CorrectiveActionType.SEARCH_CHEAPER_FLIGHT,
    ),
    ViolationCode.HOTEL_COST_EXCEEDED: (
        CorrectiveActionType.SEARCH_CHEAPER_HOTEL,
    ),
    ViolationCode.DAILY_TRAVEL_TIME_EXCEEDED: (
        CorrectiveActionType.SEARCH_FASTER_TRANSPORT,
    ),
    ViolationCode.ROUTE_INFEASIBLE: (
        CorrectiveActionType.SELECT_FEASIBLE_ROUTE,
    ),
}


class ReplanningPolicy:
    """Deterministic fallback policy; proposed actions still require controller checks."""

    def propose_actions(
        self,
        validation: ValidationResult,
        itinerary: Itinerary,
        attempted: set[tuple[ViolationCode, CorrectiveActionType]],
    ) -> list[CorrectiveAction]:
        items = [item for day in itinerary.days for item in day.items]
        actions: list[CorrectiveAction] = []
        seen: set[tuple[ViolationCode, CorrectiveActionType, str | None]] = set()
        for violation in validation.violations:
            for action_type in ACTION_TYPES_BY_VIOLATION.get(violation.code, ()):
                if (violation.code, action_type) in attempted:
                    continue
                target_id = self._target_for(action_type, violation.details, violation.item_id, items)
                if target_id is None:
                    continue
                key = (violation.code, action_type, target_id)
                if key in seen:
                    continue
                seen.add(key)
                actions.append(
                    CorrectiveAction(
                        action=action_type,
                        reason=f"Repair {violation.code.value} with a targeted selection change.",
                        target_id=target_id,
                        target_violation=violation.code,
                        expected_goal=self._goal_for(action_type),
                        parameters={"violation_details": violation.details},
                    )
                )
        return actions

    @staticmethod
    def is_legal(action: CorrectiveAction, validation: ValidationResult) -> bool:
        active_codes = {violation.code for violation in validation.violations}
        return (
            action.target_violation in active_codes
            and action.action in ACTION_TYPES_BY_VIOLATION.get(action.target_violation, ())
        )

    @staticmethod
    def _target_for(action_type, details, violation_item_id, items) -> str | None:
        if action_type == CorrectiveActionType.SEARCH_CHEAPER_FLIGHT:
            specified = details.get("flight_id")
            if specified:
                return str(specified)
            flights = [item for item in items if item.item_type == ItemType.FLIGHT]
            return max(flights, key=lambda item: (item.cost, item.option_id)).option_id if flights else None

        if action_type == CorrectiveActionType.SEARCH_CHEAPER_HOTEL:
            specified = details.get("hotel_id")
            if specified:
                return str(specified)
            totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            for item in items:
                if item.item_type == ItemType.HOTEL:
                    totals[item.option_id] += item.cost
            return max(totals, key=lambda option_id: (totals[option_id], option_id)) if totals else None

        if action_type == CorrectiveActionType.SEARCH_CHEAPER_TRANSPORT:
            routes = [item for item in items if item.item_type == ItemType.ROUTE]
            return max(routes, key=lambda item: (item.cost, item.option_id)).option_id if routes else None

        if action_type == CorrectiveActionType.REMOVE_OPTIONAL_ACTIVITY:
            activities = [item for item in items if item.item_type == ItemType.ACTIVITY and item.cost > 0]
            return (
                max(activities, key=lambda item: (item.cost, item.option_id)).option_id
                if activities
                else None
            )

        if action_type == CorrectiveActionType.SEARCH_FASTER_TRANSPORT:
            travel_date = details.get("date")
            transports = [
                item
                for item in items
                if item.item_type in {ItemType.FLIGHT, ItemType.ROUTE}
                and (travel_date is None or item.starts_at.date() == travel_date)
            ]
            return (
                max(transports, key=lambda item: (item.duration_minutes or 0, item.option_id)).option_id
                if transports
                else None
            )

        if action_type == CorrectiveActionType.SELECT_FEASIBLE_ROUTE:
            if violation_item_id:
                matched = next((item for item in items if item.id == violation_item_id), None)
                if matched:
                    return matched.option_id
            route_id = details.get("route_id") or details.get("option_id")
            return str(route_id) if route_id else None
        return None

    @staticmethod
    def _goal_for(action_type: CorrectiveActionType) -> str:
        return {
            CorrectiveActionType.SEARCH_CHEAPER_FLIGHT: "reduce selected flight cost",
            CorrectiveActionType.SEARCH_CHEAPER_HOTEL: "reduce selected nightly hotel rate",
            CorrectiveActionType.SEARCH_CHEAPER_TRANSPORT: "reduce selected route cost",
            CorrectiveActionType.SEARCH_FASTER_TRANSPORT: "reduce travel duration on the violating day",
            CorrectiveActionType.SELECT_FEASIBLE_ROUTE: "replace the segment with an explicitly feasible record",
            CorrectiveActionType.REMOVE_OPTIONAL_ACTIVITY: "reduce total cost without changing mandatory travel",
        }[action_type]
