from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.domain.models import (
    DataSource,
    PlanRequest,
    PlanningStatus,
    WeatherForecast,
    WeatherResult,
)
from app.llm import ActionProposal, AgentDecision, LLMProviderError
from app.planning.coordinator import create_planning_coordinator
from app.tools.external.errors import ExternalToolError
from app.tools.local_data import load_catalog
from app.tools.registry import DEFAULT_DATA_DIR, create_tool_registry

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIOS = BACKEND_ROOT / "evaluation" / "agentic_scenarios.json"
DEFAULT_MARKDOWN = BACKEND_ROOT.parent / "docs" / "agentic-evaluation-results.md"
DEFAULT_JSON = BACKEND_ROOT.parent / "docs" / "agentic-evaluation-results.json"


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgenticScenario(EvaluationModel):
    id: str
    name: str
    expected_status: PlanningStatus
    request: PlanRequest
    decisions: list[AgentDecision]
    proposals: list[ActionProposal] = Field(default_factory=list)
    weather_mode: str | None = None
    fail_weather: bool = False
    max_agent_steps: int = Field(default=12, ge=1, le=50)


class AgenticScenarioResult(EvaluationModel):
    scenario_id: str
    name: str
    expected_status: PlanningStatus
    final_status: PlanningStatus
    agent_steps: int
    ordered_actions: list[str]
    tool_calls: int
    tools_used: list[str]
    rejected_actions: int
    provider_failures: int
    initial_cost: Decimal | None
    final_cost: Decimal | None
    violations: list[str]
    replanning_attempts: int
    final_feasible: bool
    preference_score: Decimal | None
    termination_reason: str
    deterministic_replay: bool
    expectation_met: bool


class AgenticEvaluationReport(EvaluationModel):
    generated_at: datetime
    scenario_count: int
    deterministic_replay_count: int
    expectation_met_count: int
    results: list[AgenticScenarioResult]


class ScriptedAgentProvider:
    name = "groq"

    def __init__(self, decisions: list[AgentDecision], proposals: list[ActionProposal]) -> None:
        self._decisions = [item.model_copy(deep=True) for item in decisions]
        self._proposals = [item.model_copy(deep=True) for item in proposals]
        self.contexts = []

    def decide_next_action(self, context):
        self.contexts.append(context.model_copy(deep=True))
        if not self._decisions:
            raise LLMProviderError("script_exhausted", "No mocked decision remains")
        return self._decisions.pop(0)

    def propose_action(self, context):
        if self._proposals:
            return self._proposals.pop(0)
        candidate = context.candidate_actions[0]
        return ActionProposal(
            action=candidate.action,
            target_id=candidate.target_id,
            reason_code="first_legal_mocked_repair",
        )


class MockWeatherTool:
    provider_source = DataSource.LOCAL_DEMO

    def get_forecast(self, city_id, target_date):
        return WeatherResult(
            city_id=city_id,
            requested_date=target_date,
            weather_available=True,
            forecasts=[
                WeatherForecast(
                    forecast_at=datetime.combine(target_date, datetime.min.time(), UTC),
                    temperature_c=Decimal("24"),
                    condition="heavy rain",
                    precipitation_probability=Decimal("0.90"),
                    rain_mm=Decimal("12"),
                )
            ],
            source=DataSource.LOCAL_DEMO,
            is_live=False,
        )


class FailingWeatherTool:
    provider_source = DataSource.OPENWEATHER

    def get_forecast(self, city_id, target_date):
        raise ExternalToolError("openweather", "timeout", "Mocked provider timeout")


def load_scenarios(path: Path = DEFAULT_SCENARIOS) -> list[AgenticScenario]:
    return [AgenticScenario.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def run_agentic_evaluation(path: Path = DEFAULT_SCENARIOS) -> AgenticEvaluationReport:
    scenarios = load_scenarios(path)
    results: list[AgenticScenarioResult] = []
    for scenario in scenarios:
        state = run_agentic_scenario(scenario)
        replay = run_agentic_scenario(scenario)
        results.append(_result(scenario, state, state.model_dump_json() == replay.model_dump_json()))
    return AgenticEvaluationReport(
        generated_at=datetime.now(UTC),
        scenario_count=len(results),
        deterministic_replay_count=sum(item.deterministic_replay for item in results),
        expectation_met_count=sum(item.expectation_met for item in results),
        results=results,
    )


def run_agentic_scenario(scenario: AgenticScenario):
    catalog = load_catalog(DEFAULT_DATA_DIR)
    settings = Settings(
        llm_provider="groq",
        groq_api_key="mocked-evaluation-key",
        groq_model="mocked-agent",
        max_agent_steps=scenario.max_agent_steps,
    )
    tools = create_tool_registry(settings, catalog=catalog)
    if scenario.weather_mode:
        tools = replace(tools, weather=MockWeatherTool())
    if scenario.fail_weather:
        tools = replace(tools, weather=FailingWeatherTool())
    provider = ScriptedAgentProvider(scenario.decisions, scenario.proposals)
    coordinator = create_planning_coordinator(
        settings,
        catalog=catalog,
        agent_provider=provider,
        proposal_provider=provider,
        tool_registry=tools,
    )
    return coordinator.plan(scenario.request)


def _result(scenario: AgenticScenario, state, replay: bool) -> AgenticScenarioResult:
    validation = state.current_validation
    termination = state.fallback_reason or (
        "completed_after_replanning"
        if state.replanning_attempts
        else "agent_completed"
    )
    return AgenticScenarioResult(
        scenario_id=scenario.id,
        name=scenario.name,
        expected_status=scenario.expected_status,
        final_status=state.status,
        agent_steps=state.agent_steps_used,
        ordered_actions=[item.action for item in state.agent_trace],
        tool_calls=len(state.tool_call_history),
        tools_used=list(dict.fromkeys(item.tool_name for item in state.tool_call_history)),
        rejected_actions=sum(item.status.value == "rejected" for item in state.agent_trace),
        provider_failures=sum(item.status.value == "failed" for item in state.tool_call_history),
        initial_cost=(state.initial_itinerary.costs.total if state.initial_itinerary else None),
        final_cost=(state.current_itinerary.costs.total if state.current_itinerary else None),
        violations=(
            [item.code.value for item in validation.violations] if validation else []
        ),
        replanning_attempts=len(state.replanning_attempts),
        final_feasible=bool(validation and validation.is_valid),
        preference_score=(state.preference_score.total if state.preference_score else None),
        termination_reason=termination,
        deterministic_replay=replay,
        expectation_met=state.status == scenario.expected_status,
    )


def write_report(report: AgenticEvaluationReport, markdown_path: Path, json_path: Path) -> None:
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    rows = [
        "# TripMind mocked agentic evaluation results",
        "",
        f"Generated: {report.generated_at.isoformat()}",
        "",
        "These are fixed, network-independent mocked-agent scenarios. They measure factual execution behavior, not universal AI accuracy or live-provider quality.",
        "",
        "| Scenario | Steps | Ordered actions | Tools | Rejected | Failures | Replans | Final | Feasible | Replay |",
        "|---|---:|---|---|---:|---:|---:|---|---|---|",
    ]
    for item in report.results:
        rows.append(
            f"| {item.scenario_id} — {item.name} | {item.agent_steps} | "
            f"{' → '.join(item.ordered_actions)} | {', '.join(item.tools_used) or 'none'} | "
            f"{item.rejected_actions} | {item.provider_failures} | {item.replanning_attempts} | "
            f"{item.final_status.value} | {'yes' if item.final_feasible else 'no'} | "
            f"{'consistent' if item.deterministic_replay else 'mismatch'} |"
        )
    rows.extend([
        "",
        "## Factual aggregate",
        "",
        f"- Scenarios meeting expected terminal status: {report.expectation_met_count}/{report.scenario_count}",
        f"- Deterministic replay matches: {report.deterministic_replay_count}/{report.scenario_count}",
        "- Live network calls: 0",
    ])
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run mocked TripMind agentic scenarios")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()
    report = run_agentic_evaluation(args.scenarios)
    write_report(report, args.markdown, args.json)
    print(f"Evaluated {report.scenario_count} mocked agentic scenarios; report: {args.markdown}")


if __name__ == "__main__":
    main()
