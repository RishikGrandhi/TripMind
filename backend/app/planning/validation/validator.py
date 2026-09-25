from pathlib import Path

from app.domain.models import (
    ActivityOption,
    FlightOption,
    HotelOption,
    Itinerary,
    RouteInfo,
    TravelConstraints,
    TripState,
    ValidationResult,
)
from app.planning.validation.rules import RULES, ValidationCatalog, ValidationContext
from app.tools.local_data import LocalDataCatalog, load_catalog
from app.tools.registry import DEFAULT_DATA_DIR


class ConstraintValidator:
    def __init__(self, catalog: LocalDataCatalog) -> None:
        self._catalog = ValidationCatalog.from_catalog(catalog)

    def validate(
        self,
        itinerary: Itinerary,
        constraints: TravelConstraints,
        *,
        flight_candidates: list[FlightOption] | None = None,
        hotel_candidates: list[HotelOption] | None = None,
        activity_candidates: list[ActivityOption] | None = None,
        route_candidates: list[RouteInfo] | None = None,
    ) -> ValidationResult:
        catalog = ValidationCatalog(
            city_ids=self._catalog.city_ids,
            flights={
                **self._catalog.flights,
                **{item.id: item for item in flight_candidates or []},
            },
            hotels={
                **self._catalog.hotels,
                **{item.id: item for item in hotel_candidates or []},
            },
            activities={
                **self._catalog.activities,
                **{item.id: item for item in activity_candidates or []},
            },
            routes={
                **self._catalog.routes,
                **{item.id: item for item in route_candidates or []},
            },
        )
        context = ValidationContext(
            itinerary=itinerary,
            constraints=constraints,
            catalog=catalog,
        )
        checks = []
        violations = []
        for rule in RULES:
            outcome = rule(context)
            checks.append(outcome.check)
            violations.extend(outcome.violations)
        return ValidationResult(
            is_valid=not violations,
            checks=checks,
            violations=violations,
        )

    def validate_state(self, state: TripState) -> ValidationResult:
        if state.current_itinerary is None:
            raise ValueError("TripState has no current itinerary to validate")
        return self.validate(
            state.current_itinerary,
            state.constraints,
            flight_candidates=state.flight_candidates,
            hotel_candidates=state.hotel_candidates,
            activity_candidates=state.activity_candidates,
            route_candidates=state.route_candidates,
        )


def create_constraint_validator(
    *,
    catalog: LocalDataCatalog | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> ConstraintValidator:
    return ConstraintValidator(catalog or load_catalog(data_dir))
