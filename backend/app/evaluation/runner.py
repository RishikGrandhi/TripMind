from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import PlanRequest, PlanningStatus
from app.planning.coordinator import create_planning_coordinator

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIOS = BACKEND_ROOT / "evaluation" / "scenarios.json"
DEFAULT_MARKDOWN = BACKEND_ROOT.parent / "docs" / "evaluation-results.md"
DEFAULT_JSON = BACKEND_ROOT.parent / "docs" / "evaluation-results.json"


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationScenario(EvaluationModel):
    id: str
    name: str
    expected_status: PlanningStatus
    request: PlanRequest


class ScenarioResult(EvaluationModel):
    scenario_id: str
    name: str
    expected_status: PlanningStatus
    final_status: PlanningStatus
    initial_feasible: bool
    initial_violation_count: int = Field(ge=0)
    replanning_attempts: int = Field(ge=0)
    final_feasible: bool
    initial_cost: Decimal
    final_cost: Decimal
    preference_score: Decimal | None
    tool_calls: int = Field(ge=0)
    termination_reason: str
    deterministic_replay: bool
    expectation_met: bool


class EvaluationReport(EvaluationModel):
    generated_at: datetime
    scenario_count: int
    hard_constraint_satisfaction_rate: Decimal
    repair_success_rate: Decimal
    average_replanning_attempts: Decimal
    deterministic_replay_consistency: Decimal
    correct_infeasible_detection: bool
    results: list[ScenarioResult]


def load_scenarios(path: Path = DEFAULT_SCENARIOS) -> list[EvaluationScenario]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [EvaluationScenario.model_validate(item) for item in raw]


def run_evaluation(path: Path = DEFAULT_SCENARIOS) -> EvaluationReport:
    coordinator = create_planning_coordinator()
    results: list[ScenarioResult] = []
    for scenario in load_scenarios(path):
        state = coordinator.plan(scenario.request)
        replay = coordinator.plan(scenario.request)
        initial_validation = state.validation_history[0]
        final_validation = state.current_validation
        if state.status == PlanningStatus.COMPLETED:
            termination = "initial_candidate_feasible" if initial_validation.is_valid else "repair_succeeded"
        elif state.status == PlanningStatus.INFEASIBLE:
            termination = "bounded_replanning_exhausted_or_no_legal_improvement"
        else:
            termination = "planning_failed"
        results.append(
            ScenarioResult(
                scenario_id=scenario.id,
                name=scenario.name,
                expected_status=scenario.expected_status,
                final_status=state.status,
                initial_feasible=initial_validation.is_valid,
                initial_violation_count=len(initial_validation.violations),
                replanning_attempts=len(state.replanning_attempts),
                final_feasible=bool(final_validation and final_validation.is_valid),
                initial_cost=state.initial_itinerary.costs.total,
                final_cost=state.current_itinerary.costs.total,
                preference_score=(state.preference_score.total if state.preference_score else None),
                tool_calls=len(state.tool_call_history),
                termination_reason=termination,
                deterministic_replay=state.model_dump_json() == replay.model_dump_json(),
                expectation_met=state.status == scenario.expected_status,
            )
        )
    expected_feasible = [item for item in results if item.expected_status == PlanningStatus.COMPLETED]
    repair_cases = [item for item in expected_feasible if not item.initial_feasible]
    impossible = [item for item in results if item.expected_status == PlanningStatus.INFEASIBLE]
    return EvaluationReport(
        generated_at=datetime.now(UTC),
        scenario_count=len(results),
        hard_constraint_satisfaction_rate=_rate(sum(item.final_feasible for item in expected_feasible), len(expected_feasible)),
        repair_success_rate=_rate(sum(item.final_feasible for item in repair_cases), len(repair_cases)),
        average_replanning_attempts=_average([item.replanning_attempts for item in results]),
        deterministic_replay_consistency=_rate(sum(item.deterministic_replay for item in results), len(results)),
        correct_infeasible_detection=all(item.final_status == PlanningStatus.INFEASIBLE for item in impossible),
        results=results,
    )


def write_report(report: EvaluationReport, markdown_path: Path, json_path: Path) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    rows = [
        "# TripMind deterministic evaluation results",
        "",
        f"Generated: {report.generated_at.isoformat()}",
        "",
        "These results come from executing the real local planning pipeline against the versioned demo dataset. They are not live-travel or ML benchmark claims.",
        "",
        "| Scenario | Initial | Violations | Attempts | Tool calls | Final | Initial cost | Final cost | Score | Replay |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    for item in report.results:
        rows.append(
            f"| {item.scenario_id} — {item.name} | {'feasible' if item.initial_feasible else 'invalid'} | "
            f"{item.initial_violation_count} | {item.replanning_attempts} | {item.tool_calls} | {item.final_status.value} | "
            f"₹{item.initial_cost} | ₹{item.final_cost} | {item.preference_score or '—'} | "
            f"{'consistent' if item.deterministic_replay else 'mismatch'} |"
        )
    rows.extend([
        "",
        "## Aggregate metrics",
        "",
        f"- Hard-constraint satisfaction on expected-feasible scenarios: {report.hard_constraint_satisfaction_rate}%",
        f"- Repair success rate for initially invalid expected-feasible scenarios: {report.repair_success_rate}%",
        f"- Average replanning attempts: {report.average_replanning_attempts}",
        f"- Deterministic replay consistency: {report.deterministic_replay_consistency}%",
        f"- Correct impossible-case detection: {'yes' if report.correct_infeasible_detection else 'no'}",
        "",
        "`SC-006` is intentionally infeasible; its unresolved hard violation is the correct outcome.",
    ])
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _rate(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.00")
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"))


def _average(values: list[int]) -> Decimal:
    if not values:
        return Decimal("0.00")
    return (Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.01"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic TripMind evaluation scenarios")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()
    report = run_evaluation(args.scenarios)
    write_report(report, args.markdown, args.json)
    print(f"Evaluated {report.scenario_count} scenarios; report: {args.markdown}")


if __name__ == "__main__":
    main()
