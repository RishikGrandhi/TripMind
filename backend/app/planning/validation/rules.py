from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

from app.domain.models import (
    ActivityOption,
    ConstraintCheck,
    ConstraintViolation,
    CostBreakdown,
    FlightOption,
    HotelOption,
    ItemType,
    Itinerary,
    ItineraryItem,
    RouteInfo,
    TravelConstraints,
    ViolationCode,
)
from app.planning.cost_engine import calculate_cost_breakdown
from app.tools.local_data import LocalDataCatalog


@dataclass(frozen=True)
class ValidationCatalog:
    city_ids: frozenset[str]
    flights: dict[str, FlightOption]
    hotels: dict[str, HotelOption]
    activities: dict[str, ActivityOption]
    routes: dict[str, RouteInfo]

    @classmethod
    def from_catalog(cls, catalog: LocalDataCatalog) -> "ValidationCatalog":
        return cls(
            city_ids=frozenset(city.id for city in catalog.cities),
            flights={item.id: item for item in catalog.flights},
            hotels={item.id: item for item in catalog.hotels},
            activities={item.id: item for item in catalog.activities},
            routes={item.id: item for item in catalog.routes},
        )


@dataclass(frozen=True)
class ValidationContext:
    itinerary: Itinerary
    constraints: TravelConstraints
    catalog: ValidationCatalog

    @property
    def items(self) -> list[ItineraryItem]:
        return [item for day in self.itinerary.days for item in day.items]


@dataclass(frozen=True)
class RuleOutcome:
    check: ConstraintCheck
    violations: list[ConstraintViolation]


Rule = Callable[[ValidationContext], RuleOutcome]


def check_total_budget(context: ValidationContext) -> RuleOutcome:
    costs = getattr(context.itinerary, "costs", None)
    limit = context.constraints.total_budget
    if not isinstance(costs, CostBreakdown):
        violation = ConstraintViolation(
            code=ViolationCode.SCHEDULE_INTEGRITY_ERROR,
            message="The itinerary has no authoritative cost breakdown.",
            details={"required": "CostBreakdown"},
        )
        return _failed("total_budget", f"<= INR {limit}", "unavailable", violation)

    actual = costs.total
    if actual <= limit:
        return _passed("total_budget", f"<= INR {limit}", f"INR {actual}")
    violation = ConstraintViolation(
        code=ViolationCode.BUDGET_EXCEEDED,
        message=f"Itinerary total INR {actual} exceeds budget INR {limit}.",
        details={"limit": limit, "actual": actual, "difference": actual - limit},
    )
    return _failed("total_budget", f"<= INR {limit}", f"INR {actual}", violation)


def check_date_window(context: ValidationContext) -> RuleOutcome:
    start = context.constraints.start_date
    end = context.constraints.end_date
    invalid_days = [day.date for day in context.itinerary.days if not start <= day.date <= end]
    invalid_items = [
        item.id
        for item in context.items
        if item.starts_at.date() < start or item.ends_at.date() > end
    ]
    if not invalid_days and not invalid_items:
        return _passed(
            "date_window",
            f"{start.isoformat()} through {end.isoformat()} inclusive",
            "all days and items within window",
        )
    violation = ConstraintViolation(
        code=ViolationCode.DATE_WINDOW_VIOLATION,
        message="One or more itinerary dates fall outside the requested date window.",
        details={
            "start_date": start,
            "end_date": end,
            "invalid_days": invalid_days,
            "invalid_item_ids": invalid_items,
        },
    )
    return _failed(
        "date_window",
        f"{start.isoformat()} through {end.isoformat()} inclusive",
        f"invalid days={len(invalid_days)}, invalid items={len(invalid_items)}",
        violation,
    )


def check_trip_duration(context: ValidationContext) -> RuleOutcome:
    expected_days = (context.constraints.end_date - context.constraints.start_date).days + 1
    actual_dates = [day.date for day in context.itinerary.days]
    expected_dates = [
        context.constraints.start_date.fromordinal(
            context.constraints.start_date.toordinal() + offset
        )
        for offset in range(expected_days)
    ]
    if actual_dates == expected_dates:
        return _passed(
            "trip_duration",
            f"{expected_days} inclusive calendar days",
            f"{len(actual_dates)} inclusive calendar days",
        )
    violation = ConstraintViolation(
        code=ViolationCode.TRIP_DURATION_MISMATCH,
        message="Itinerary day coverage does not match the requested inclusive date range.",
        details={
            "expected_days": expected_days,
            "actual_days": len(actual_dates),
            "expected_dates": expected_dates,
            "actual_dates": actual_dates,
        },
    )
    return _failed(
        "trip_duration",
        f"{expected_days} inclusive calendar days",
        f"{len(actual_dates)} day records",
        violation,
    )


def check_mandatory_destinations(context: ValidationContext) -> RuleOutcome:
    required = context.constraints.destination_city_ids
    visited: set[str] = set()
    for item in context.items:
        if item.city_id:
            visited.add(item.city_id)
        if item.destination_city_id:
            visited.add(item.destination_city_id)
    missing = [city_id for city_id in required if city_id not in visited]
    if not missing:
        return _passed(
            "mandatory_destinations",
            ", ".join(required),
            ", ".join(city_id for city_id in required if city_id in visited),
        )
    violation = ConstraintViolation(
        code=ViolationCode.MANDATORY_DESTINATION_MISSING,
        message=f"Mandatory destinations are missing: {', '.join(missing)}.",
        details={"required": required, "visited": sorted(visited), "missing": missing},
    )
    return _failed(
        "mandatory_destinations",
        ", ".join(required),
        f"missing: {', '.join(missing)}",
        violation,
    )


def check_hotel_ceiling(context: ValidationContext) -> RuleOutcome:
    limit = context.constraints.max_hotel_price_per_night
    if limit is None:
        return _passed("hotel_price_per_night", "not constrained", "not constrained")

    selected_ids = list(
        dict.fromkeys(
            item.option_id for item in context.items if item.item_type == ItemType.HOTEL
        )
    )
    resolved = [context.catalog.hotels[item_id] for item_id in selected_ids if item_id in context.catalog.hotels]
    violations = [
        ConstraintViolation(
            code=ViolationCode.HOTEL_COST_EXCEEDED,
            message=(
                f"Hotel {hotel.id} nightly rate INR {hotel.price_per_night} "
                f"exceeds ceiling INR {limit}."
            ),
            item_id=hotel.id,
            details={
                "hotel_id": hotel.id,
                "city_id": hotel.city_id,
                "rate_per_night": hotel.price_per_night,
                "limit": limit,
                "difference": hotel.price_per_night - limit,
            },
        )
        for hotel in resolved
        if hotel.price_per_night > limit
    ]
    max_rate = max((hotel.price_per_night for hotel in resolved), default=Decimal("0"))
    if not violations:
        return _passed(
            "hotel_price_per_night", f"<= INR {limit}", f"maximum selected rate INR {max_rate}"
        )
    return RuleOutcome(
        check=ConstraintCheck(
            constraint="hotel_price_per_night",
            passed=False,
            expected=f"<= INR {limit}",
            actual=f"maximum selected rate INR {max_rate}",
            violation_code=ViolationCode.HOTEL_COST_EXCEEDED,
        ),
        violations=violations,
    )


def check_flight_ceiling(context: ValidationContext) -> RuleOutcome:
    limit = context.constraints.max_flight_price
    if limit is None:
        return _passed("flight_price_per_traveler", "not constrained", "not constrained")

    selected_ids = list(
        dict.fromkeys(
            item.option_id for item in context.items if item.item_type == ItemType.FLIGHT
        )
    )
    resolved = [
        context.catalog.flights[item_id]
        for item_id in selected_ids
        if item_id in context.catalog.flights
    ]
    violations = [
        ConstraintViolation(
            code=ViolationCode.FLIGHT_COST_EXCEEDED,
            message=f"Flight {flight.id} fare INR {flight.price} exceeds ceiling INR {limit}.",
            item_id=flight.id,
            details={
                "flight_id": flight.id,
                "origin_city_id": flight.origin_city_id,
                "destination_city_id": flight.destination_city_id,
                "fare_per_traveler": flight.price,
                "limit": limit,
                "difference": flight.price - limit,
            },
        )
        for flight in resolved
        if flight.price > limit
    ]
    max_fare = max((flight.price for flight in resolved), default=Decimal("0"))
    if not violations:
        return _passed(
            "flight_price_per_traveler",
            f"<= INR {limit}",
            f"maximum selected fare INR {max_fare}",
        )
    return RuleOutcome(
        check=ConstraintCheck(
            constraint="flight_price_per_traveler",
            passed=False,
            expected=f"<= INR {limit}",
            actual=f"maximum selected fare INR {max_fare}",
            violation_code=ViolationCode.FLIGHT_COST_EXCEEDED,
        ),
        violations=violations,
    )


def check_daily_travel_time(context: ValidationContext) -> RuleOutcome:
    limit = context.constraints.max_travel_duration_minutes
    if limit is None:
        return _passed("maximum_daily_travel_time", "not constrained", "not constrained")

    totals: dict[date, int] = defaultdict(int)
    for item in context.items:
        if item.item_type not in {ItemType.FLIGHT, ItemType.ROUTE}:
            continue
        duration = item.duration_minutes
        if duration is None:
            duration = int((item.ends_at - item.starts_at).total_seconds() // 60)
        totals[item.starts_at.date()] += duration

    violations = [
        ConstraintViolation(
            code=ViolationCode.DAILY_TRAVEL_TIME_EXCEEDED,
            message=(
                f"Travel time on {travel_date.isoformat()} is {actual} minutes, "
                f"above the {limit}-minute limit."
            ),
            details={
                "date": travel_date,
                "limit_minutes": limit,
                "actual_minutes": actual,
                "difference_minutes": actual - limit,
            },
        )
        for travel_date, actual in sorted(totals.items())
        if actual > limit
    ]
    maximum = max(totals.values(), default=0)
    if not violations:
        return _passed(
            "maximum_daily_travel_time", f"<= {limit} minutes per day", f"maximum {maximum} minutes"
        )
    return RuleOutcome(
        check=ConstraintCheck(
            constraint="maximum_daily_travel_time",
            passed=False,
            expected=f"<= {limit} minutes per day",
            actual=f"maximum {maximum} minutes",
            violation_code=ViolationCode.DAILY_TRAVEL_TIME_EXCEEDED,
        ),
        violations=violations,
    )


def check_route_feasibility(context: ValidationContext) -> RuleOutcome:
    violations: list[ConstraintViolation] = []
    transport_items = [
        item for item in context.items if item.item_type in {ItemType.FLIGHT, ItemType.ROUTE}
    ]
    for item in transport_items:
        option: FlightOption | RouteInfo | None
        if item.item_type == ItemType.FLIGHT:
            option = context.catalog.flights.get(item.option_id)
        else:
            option = context.catalog.routes.get(item.option_id)
        if option is None:
            violations.append(
                ConstraintViolation(
                    code=ViolationCode.ROUTE_INFEASIBLE,
                    message=f"Transport reference {item.option_id} does not exist in demo data.",
                    item_id=item.id,
                    details={"option_id": item.option_id, "item_type": item.item_type},
                )
            )
            continue
        if isinstance(option, RouteInfo) and not option.is_feasible:
            violations.append(
                ConstraintViolation(
                    code=ViolationCode.ROUTE_INFEASIBLE,
                    message=f"Selected route {option.id} is explicitly infeasible.",
                    item_id=item.id,
                    details={
                        "route_id": option.id,
                        "origin_city_id": option.origin_city_id,
                        "destination_city_id": option.destination_city_id,
                        "mode": option.mode,
                        "reason": option.unavailable_reason,
                    },
                )
            )
            continue
        if (
            option.origin_city_id != item.origin_city_id
            or option.destination_city_id != item.destination_city_id
        ):
            violations.append(
                ConstraintViolation(
                    code=ViolationCode.ROUTE_INFEASIBLE,
                    message=f"Transport item {item.id} does not match referenced option {option.id}.",
                    item_id=item.id,
                    details={
                        "option_id": option.id,
                        "item_origin": item.origin_city_id,
                        "item_destination": item.destination_city_id,
                        "record_origin": option.origin_city_id,
                        "record_destination": option.destination_city_id,
                    },
                )
            )
    if not violations:
        return _passed(
            "route_feasibility",
            "all transport segments reference supported feasible records",
            f"{len(transport_items)} supported segment(s)",
        )
    return RuleOutcome(
        check=ConstraintCheck(
            constraint="route_feasibility",
            passed=False,
            expected="all transport segments reference supported feasible records",
            actual=f"{len(violations)} unsupported or infeasible segment(s)",
            violation_code=ViolationCode.ROUTE_INFEASIBLE,
        ),
        violations=violations,
    )


def check_schedule_integrity(context: ValidationContext) -> RuleOutcome:
    problems: list[dict[str, object]] = []
    day_dates = [day.date for day in context.itinerary.days]
    if day_dates != sorted(day_dates):
        problems.append({"problem": "itinerary days are not ordered"})
    if len(day_dates) != len(set(day_dates)):
        problems.append({"problem": "itinerary contains duplicate day dates"})

    items = context.items
    item_ids = [item.id for item in items]
    if len(item_ids) != len(set(item_ids)):
        problems.append({"problem": "itinerary contains duplicate item IDs"})

    reference_maps = {
        ItemType.FLIGHT: context.catalog.flights,
        ItemType.HOTEL: context.catalog.hotels,
        ItemType.ACTIVITY: context.catalog.activities,
        ItemType.ROUTE: context.catalog.routes,
    }
    for day in context.itinerary.days:
        for item in day.items:
            if item.starts_at.date() != day.date:
                problems.append(
                    {"problem": "item is assigned to the wrong itinerary day", "item_id": item.id}
                )
            if item.ends_at <= item.starts_at:
                problems.append({"problem": "item has a non-positive duration", "item_id": item.id})
            if item.option_id not in reference_maps[item.item_type]:
                problems.append(
                    {
                        "problem": "item references an unknown option",
                        "item_id": item.id,
                        "option_id": item.option_id,
                    }
                )

    ordered_items = sorted(items, key=lambda item: (item.starts_at, item.ends_at, item.id))
    for position, item in enumerate(ordered_items):
        for later in ordered_items[position + 1 :]:
            if later.starts_at >= item.ends_at:
                break
            problems.append(
                {
                    "problem": "scheduled items overlap",
                    "item_id": item.id,
                    "overlaps_item_id": later.id,
                }
            )

    transport_items = [
        item for item in ordered_items if item.item_type in {ItemType.FLIGHT, ItemType.ROUTE}
    ]
    expected_origin = context.constraints.origin_city_id
    for item in transport_items:
        if item.origin_city_id != expected_origin:
            problems.append(
                {
                    "problem": "transport sequence is discontinuous",
                    "item_id": item.id,
                    "expected_origin": expected_origin,
                    "actual_origin": item.origin_city_id,
                }
            )
        if item.destination_city_id:
            expected_origin = item.destination_city_id

    costs = getattr(context.itinerary, "costs", None)
    if not isinstance(costs, CostBreakdown):
        problems.append({"problem": "authoritative cost breakdown is missing"})
    else:
        calculated = calculate_cost_breakdown(items)
        categories = ("flights", "hotels", "activities", "local_transport", "other", "currency")
        if any(getattr(costs, field) != getattr(calculated, field) for field in categories):
            problems.append(
                {
                    "problem": "cost breakdown does not match itinerary items",
                    "stated": costs.model_dump(mode="json"),
                    "calculated": calculated.model_dump(mode="json"),
                }
            )

    if not problems:
        return _passed("schedule_integrity", "ordered, unique, coherent itinerary", "valid")
    violations = [
        ConstraintViolation(
            code=ViolationCode.SCHEDULE_INTEGRITY_ERROR,
            message=str(problem["problem"]),
            item_id=str(problem["item_id"]) if "item_id" in problem else None,
            details=problem,
        )
        for problem in problems
    ]
    return RuleOutcome(
        check=ConstraintCheck(
            constraint="schedule_integrity",
            passed=False,
            expected="ordered, unique, coherent itinerary",
            actual=f"{len(problems)} integrity problem(s)",
            violation_code=ViolationCode.SCHEDULE_INTEGRITY_ERROR,
        ),
        violations=violations,
    )


RULES: tuple[Rule, ...] = (
    check_total_budget,
    check_date_window,
    check_trip_duration,
    check_mandatory_destinations,
    check_flight_ceiling,
    check_hotel_ceiling,
    check_daily_travel_time,
    check_route_feasibility,
    check_schedule_integrity,
)


def _passed(constraint: str, expected: str, actual: str) -> RuleOutcome:
    return RuleOutcome(
        check=ConstraintCheck(
            constraint=constraint,
            passed=True,
            expected=expected,
            actual=actual,
        ),
        violations=[],
    )


def _failed(
    constraint: str,
    expected: str,
    actual: str,
    violation: ConstraintViolation,
) -> RuleOutcome:
    return RuleOutcome(
        check=ConstraintCheck(
            constraint=constraint,
            passed=False,
            expected=expected,
            actual=actual,
            violation_code=violation.code,
        ),
        violations=[violation],
    )
