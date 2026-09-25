from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import CorrectiveActionType, ViolationCode
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


class ActionProposalContext(GuardedModel):
    violation_codes: list[ViolationCode]
    allowed_actions: list[CorrectiveActionType]
    state_summary: dict[str, str | int] = Field(default_factory=dict)


class ActionProposal(GuardedModel):
    action: CorrectiveActionType
    reason_code: str = Field(min_length=1)


class ActionProposalProvider(Protocol):
    """Safe future extension point; the deterministic controller remains authoritative."""

    def propose_action(self, context: ActionProposalContext) -> ActionProposal: ...
