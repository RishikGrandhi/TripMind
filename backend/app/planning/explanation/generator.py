from decimal import Decimal

from app.domain.models import PlanningStatus, TripState, ViolationCode


class ExplanationGenerator:
    """Render a deterministic summary using only facts stored in TripState."""

    def generate(self, state: TripState) -> str:
        sentences: list[str] = []
        initial_total = (
            state.initial_itinerary.costs.total if state.initial_itinerary else None
        )
        final_total = (
            state.current_itinerary.costs.total if state.current_itinerary else None
        )

        if initial_total is not None:
            sentences.append(f"The initial itinerary cost {_money(initial_total)}.")

        initial_validation = state.validation_history[0] if state.validation_history else None
        if initial_validation is not None:
            if initial_validation.is_valid:
                sentences.append("It satisfied all hard constraints without replanning.")
            else:
                labels = ", ".join(
                    violation.code.value for violation in initial_validation.violations
                )
                sentences.append(f"Initial hard-constraint violations: {labels}.")
                if any(
                    violation.code == ViolationCode.BUDGET_EXCEEDED
                    for violation in initial_validation.violations
                ) and initial_total is not None:
                    excess = initial_total - state.constraints.total_budget
                    sentences.append(
                        f"It exceeded the {_money(state.constraints.total_budget)} budget "
                        f"by {_money(excess)}."
                    )

        for attempt in state.replanning_attempts:
            action = attempt.actions[0] if attempt.actions else None
            if action is None:
                continue
            change = action.action.value.replace("_", " ")
            if attempt.before_component_id != attempt.after_component_id:
                component = (
                    f" changed {attempt.before_component_id or 'none'} to "
                    f"{attempt.after_component_id or 'none'}"
                )
            else:
                component = f" kept {attempt.before_component_id or 'the selected component'}"
            effect = f", changing total cost by {_signed_money(attempt.cost_effect)}"
            if attempt.duration_effect_minutes is not None:
                effect += f" and duration by {_signed_minutes(attempt.duration_effect_minutes)}"
            resulting_total = action.parameters.get("after_total")
            if resulting_total is not None:
                effect += f", resulting in {_money(Decimal(str(resulting_total)))} total"
            sentences.append(
                f"Replanning attempt {attempt.attempt_number} ({change}){component}{effect}; "
                f"outcome: {attempt.outcome or 'recorded'}."
            )
            if (
                attempt.cost_effect is not None
                and attempt.cost_effect > 0
                and attempt.duration_effect_minutes is not None
                and attempt.duration_effect_minutes < 0
            ):
                sentences.append(
                    f"This trade-off increased cost by {_money(attempt.cost_effect)} "
                    f"and reduced travel time by {abs(attempt.duration_effect_minutes)} minutes."
                )
            elif (
                attempt.cost_effect is not None
                and attempt.cost_effect < 0
                and attempt.duration_effect_minutes is not None
                and attempt.duration_effect_minutes > 0
            ):
                sentences.append(
                    f"This trade-off reduced cost by {_money(abs(attempt.cost_effect))} "
                    f"and increased travel time by {attempt.duration_effect_minutes} minutes."
                )
            facts = action.parameters
            if "minimum_available_fare" in facts:
                sentences.append(
                    "The recorded cheapest available flight fare was "
                    f"{_money(Decimal(str(facts['minimum_available_fare'])))}."
                )
            if "minimum_available_nightly_rate" in facts:
                sentences.append(
                    "The recorded cheapest available hotel rate was "
                    f"{_money(Decimal(str(facts['minimum_available_nightly_rate'])))} per night."
                )

        if state.status == PlanningStatus.COMPLETED and final_total is not None:
            if state.replanning_attempts:
                sentences.append(
                    f"The final itinerary costs {_money(final_total)} and satisfies all hard constraints."
                )
            if state.preference_score is not None:
                component_text = ", ".join(
                    f"{name} {score}/100"
                    for name, score in state.preference_score.components.items()
                )
                sentences.append(
                    f"Its deterministic preference score is {state.preference_score.total}/100 "
                    f"({component_text})."
                )
        elif state.status == PlanningStatus.INFEASIBLE:
            remaining = state.current_validation.violations if state.current_validation else []
            if remaining:
                messages = "; ".join(violation.message for violation in remaining)
                sentences.append(f"No feasible itinerary was found. Remaining issues: {messages}")
            else:
                sentences.append("No feasible itinerary was found within the replanning bounds.")
        elif state.status == PlanningStatus.FAILED:
            sentences.append("Planning failed before a feasible final itinerary could be produced.")

        return " ".join(sentences)


def _money(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"))
    amount = f"{normalized:,.2f}".rstrip("0").rstrip(".")
    return f"INR {amount}"


def _signed_money(value: Decimal | None) -> str:
    if value is None or value == 0:
        return "INR 0"
    sign = "+" if value > 0 else "-"
    return f"{sign}{_money(abs(value))}"


def _signed_minutes(value: int) -> str:
    if value == 0:
        return "0 minutes"
    return f"{value:+d} minutes"
