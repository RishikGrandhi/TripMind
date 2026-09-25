from decimal import Decimal, ROUND_HALF_UP

from app.domain.models import (
    ActivityOption,
    FlightOption,
    HotelOption,
    ItemType,
    Itinerary,
    PreferenceScore,
    RouteInfo,
    SoftPreferences,
    TransportMode,
    TravelConstraints,
    ValidationResult,
)
from app.tools.local_data import LocalDataCatalog

SCORE_QUANTUM = Decimal("0.01")
DEFAULT_WEIGHTS = {
    "activity_match": Decimal("0.20"),
    "hotel_amenity_match": Decimal("0.20"),
    "transport_match": Decimal("0.15"),
    "airline_match": Decimal("0.15"),
    "pace_match": Decimal("0.10"),
    "cost_efficiency": Decimal("0.20"),
}


class InfeasiblePreferenceScoreError(ValueError):
    """Raised when soft scoring is requested before hard feasibility is established."""


class PreferenceScorer:
    def __init__(self, catalog: LocalDataCatalog) -> None:
        self._flights = {item.id: item for item in catalog.flights}
        self._hotels = {item.id: item for item in catalog.hotels}
        self._activities = {item.id: item for item in catalog.activities}
        self._routes = {item.id: item for item in catalog.routes}

    def score(
        self,
        itinerary: Itinerary,
        constraints: TravelConstraints,
        preferences: SoftPreferences,
        validation: ValidationResult,
        *,
        flight_candidates: list[FlightOption] | None = None,
        hotel_candidates: list[HotelOption] | None = None,
        activity_candidates: list[ActivityOption] | None = None,
        route_candidates: list[RouteInfo] | None = None,
    ) -> PreferenceScore:
        if not validation.is_valid:
            raise InfeasiblePreferenceScoreError(
                "Preference scoring requires a hard-constraint-feasible itinerary"
            )
        items = [item for day in itinerary.days for item in day.items]
        flights = {**self._flights, **{item.id: item for item in flight_candidates or []}}
        hotels = {**self._hotels, **{item.id: item for item in hotel_candidates or []}}
        activities = {
            **self._activities,
            **{item.id: item for item in activity_candidates or []},
        }
        routes = {**self._routes, **{item.id: item for item in route_candidates or []}}
        components = {
            "activity_match": self._activity_match(items, preferences, activities),
            "hotel_amenity_match": self._hotel_match(items, preferences, hotels),
            "transport_match": self._transport_match(items, preferences, routes),
            "airline_match": self._airline_match(items, preferences, flights),
            "pace_match": self._pace_match(items, constraints, preferences),
            "cost_efficiency": self._cost_efficiency(itinerary, constraints),
        }
        components = {name: _score(value) for name, value in components.items()}
        total = _score(
            sum(
                components[name] * weight
                for name, weight in DEFAULT_WEIGHTS.items()
            )
        )
        explanations = [
            f"{name.replace('_', ' ').title()}: {components[name]}/100 "
            f"at weight {DEFAULT_WEIGHTS[name]}."
            for name in DEFAULT_WEIGHTS
        ]
        return PreferenceScore(
            total=total,
            components=components,
            weights=DEFAULT_WEIGHTS,
            explanation=explanations,
        )

    def _activity_match(self, items, preferences: SoftPreferences, activities) -> Decimal:
        preferred = {item.casefold() for item in preferences.preferred_activity_categories}
        if not preferred:
            return Decimal("100")
        selected = [
            activities[item.option_id]
            for item in items
            if item.item_type == ItemType.ACTIVITY and item.option_id in activities
        ]
        if not selected:
            return Decimal("0")
        matches = sum(activity.category.casefold() in preferred for activity in selected)
        return Decimal(matches) * Decimal("100") / Decimal(len(selected))

    def _hotel_match(self, items, preferences: SoftPreferences, hotels) -> Decimal:
        preferred = {item.casefold() for item in preferences.preferred_hotel_amenities}
        if not preferred:
            return Decimal("100")
        option_ids = list(
            dict.fromkeys(
                item.option_id for item in items if item.item_type == ItemType.HOTEL
            )
        )
        selected = [hotels[item_id] for item_id in option_ids if item_id in hotels]
        if not selected:
            return Decimal("0")
        scores = []
        for hotel in selected:
            amenities = {item.casefold() for item in hotel.amenities}
            scores.append(
                Decimal(len(preferred & amenities)) * Decimal("100") / Decimal(len(preferred))
            )
        return sum(scores, Decimal("0")) / Decimal(len(scores))

    def _transport_match(self, items, preferences: SoftPreferences, routes) -> Decimal:
        preferred = set(preferences.preferred_transport_modes)
        if not preferred:
            return Decimal("100")
        modes: list[TransportMode] = []
        for item in items:
            if item.item_type == ItemType.FLIGHT:
                modes.append(TransportMode.FLIGHT)
            elif item.item_type == ItemType.ROUTE and item.option_id in routes:
                modes.append(routes[item.option_id].mode)
        if not modes:
            return Decimal("0")
        matches = sum(mode in preferred for mode in modes)
        return Decimal(matches) * Decimal("100") / Decimal(len(modes))

    def _airline_match(self, items, preferences: SoftPreferences, flights) -> Decimal:
        preferred = {item.casefold() for item in preferences.preferred_airlines}
        if not preferred:
            return Decimal("100")
        flights = [
            flights[item.option_id]
            for item in items
            if item.item_type == ItemType.FLIGHT and item.option_id in flights
        ]
        if not flights:
            return Decimal("0")
        matches = sum(flight.airline.casefold() in preferred for flight in flights)
        return Decimal(matches) * Decimal("100") / Decimal(len(flights))

    @staticmethod
    def _pace_match(items, constraints, preferences: SoftPreferences) -> Decimal:
        if preferences.pace is None:
            return Decimal("100")
        activity_count = sum(item.item_type == ItemType.ACTIVITY for item in items)
        destinations = max(1, len(constraints.destination_city_ids))
        per_destination = Decimal(activity_count) / Decimal(destinations)
        if preferences.pace == "relaxed":
            return Decimal("100") if per_destination <= 1 else max(
                Decimal("0"), Decimal("100") - (per_destination - 1) * Decimal("25")
            )
        if preferences.pace == "balanced":
            if Decimal("1") <= per_destination <= Decimal("2"):
                return Decimal("100")
            return Decimal("50") if per_destination < 1 else Decimal("75")
        if per_destination >= 3:
            return Decimal("100")
        if per_destination >= 2:
            return Decimal("75")
        if per_destination >= 1:
            return Decimal("50")
        return Decimal("0")

    @staticmethod
    def _cost_efficiency(itinerary: Itinerary, constraints: TravelConstraints) -> Decimal:
        headroom = constraints.total_budget - itinerary.costs.total
        if headroom < 0:
            raise InfeasiblePreferenceScoreError(
                "Cost efficiency cannot be scored for an over-budget itinerary"
            )
        return headroom * Decimal("100") / constraints.total_budget


def _score(value: Decimal) -> Decimal:
    bounded = min(Decimal("100"), max(Decimal("0"), value))
    return bounded.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
