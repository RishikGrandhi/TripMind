from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import CorrectiveAction, CorrectiveActionType, ViolationCode
from app.extraction.models import ExtractedTravelIntent


@dataclass(frozen=True)
class ExtractionContext:
    city_name_to_id: dict[str, str]
    known_airlines: tuple[str, ...]
    demo_reference_date: date


class LLMProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LLMProvider(Protocol):
    name: str

    def extract_travel_request(
        self, text: str, context: ExtractionContext
    ) -> ExtractedTravelIntent: ...


class GuardedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentActionType(StrEnum):
    SEARCH_FLIGHTS = "search_flights"
    SEARCH_HOTELS = "search_hotels"
    SEARCH_ACTIVITIES = "search_activities"
    GET_ROUTE = "get_route"
    GET_WEATHER = "get_weather"
    BUILD_CANDIDATE = "build_candidate"
    VALIDATE = "validate"
    PROPOSE_CORRECTIVE_ACTION = "propose_corrective_action"
    FINISH = "finish"


class AgentDecision(GuardedModel):
    action: AgentActionType
    reason_code: str = Field(min_length=1)
    target: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_purpose: str | None = None


class AgentDecisionContext(GuardedModel):
    step: int = Field(ge=1)
    allowed_actions: list[AgentActionType]
    state: dict[str, Any]
    previous_action_results: list[dict[str, Any]] = Field(default_factory=list)


class AgentDecisionProvider(Protocol):
    name: str

    def decide_next_action(self, context: AgentDecisionContext) -> AgentDecision: ...


class ActionProposalContext(GuardedModel):
    violation_codes: list[ViolationCode]
    allowed_actions: list[CorrectiveActionType]
    state_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_actions: list[CorrectiveAction] = Field(default_factory=list)


class ActionProposal(GuardedModel):
    action: CorrectiveActionType
    reason_code: str = Field(min_length=1)
    target_id: str | None = None


class ActionProposalProvider(Protocol):
    """Safe future extension point; the deterministic controller remains authoritative."""

    def propose_action(self, context: ActionProposalContext) -> ActionProposal: ...
