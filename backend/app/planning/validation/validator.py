from pathlib import Path

from app.domain.models import Itinerary, TravelConstraints, TripState, ValidationResult
from app.planning.validation.rules import RULES, ValidationCatalog, ValidationContext
from app.tools.local_data import LocalDataCatalog, load_catalog
from app.tools.registry import DEFAULT_DATA_DIR


class ConstraintValidator:
    def __init__(self, catalog: LocalDataCatalog) -> None:
        self._catalog = ValidationCatalog.from_catalog(catalog)

    def validate(
        self, itinerary: Itinerary, constraints: TravelConstraints
    ) -> ValidationResult:
        context = ValidationContext(
            itinerary=itinerary,
            constraints=constraints,
            catalog=self._catalog,
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
        return self.validate(state.current_itinerary, state.constraints)


def create_constraint_validator(
    *,
    catalog: LocalDataCatalog | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> ConstraintValidator:
    return ConstraintValidator(catalog or load_catalog(data_dir))
