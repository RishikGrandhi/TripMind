from __future__ import annotations

import json
import socket
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from app.extraction.models import ExtractedTravelIntent
from app.llm.base import (
    ActionProposal,
    ActionProposalContext,
    AgentDecision,
    AgentDecisionContext,
    ExtractionContext,
    LLMProviderError,
)

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
GroqTransport = Callable[
    [str, dict[str, str], dict[str, Any], float], dict[str, Any]
]
ModelT = TypeVar("ModelT", bound=BaseModel)


class GroqProvider:
    """Groq JSON provider bounded to TripMind's typed extraction and action contracts."""

    name = "groq"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        transport: GroqTransport | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._model = model.strip()
        self._timeout = timeout_seconds
        self._transport = transport or _post_json

    def extract_travel_request(
        self, text: str, context: ExtractionContext
    ) -> ExtractedTravelIntent:
        city_ids = ", ".join(
            f"{name}={city_id}"
            for name, city_id in sorted(context.city_name_to_id.items())
        )
        system = (
            "Extract only explicit travel facts into the supplied JSON schema. "
            "Do not plan, call tools, calculate feasibility, alter hard constraints, or "
            "invent missing values. All destinations are mandatory and retain user order. "
            f"Allowed city IDs: {city_ids}. Known demo airlines: "
            f"{', '.join(context.known_airlines)}. Money is numeric INR. "
            "Put missing required facts in missing_required_fields. "
            f"Schema: {json.dumps(ExtractedTravelIntent.model_json_schema())}"
        )
        return self._request_model(
            system=system,
            user=text,
            model_type=ExtractedTravelIntent,
            invalid_code="invalid_schema",
        )

    def decide_next_action(self, context: AgentDecisionContext) -> AgentDecision:
        system = (
            "You are TripMind's planning controller. Return one JSON action only. "
            "Choose only from allowed_actions and use only parameters required by the "
            "registered TripMind travel capability. Observe the entire supplied structured "
            "state, including prior normalized tool results and validation. Never change "
            "constraints, calculate authoritative totals, set feasibility, invoke unknown "
            "tools, or provide chain-of-thought. Weather is optional advisory context and "
            "never determines hard feasibility. Give a short reason_code. "
            f"Schema: {json.dumps(AgentDecision.model_json_schema())}"
        )
        return self._request_model(
            system=system,
            user=context.model_dump_json(exclude_none=True),
            model_type=AgentDecision,
            invalid_code="invalid_action",
        )

    def propose_action(self, context: ActionProposalContext) -> ActionProposal:
        system = (
            "Select one corrective action from candidate_actions. Return JSON only. "
            "The deterministic policy will authorize and execute it. Never modify hard "
            "constraints, declare feasibility, invent a target, or request an action not "
            "listed in allowed_actions. Give a short reason_code. "
            f"Schema: {json.dumps(ActionProposal.model_json_schema())}"
        )
        return self._request_model(
            system=system,
            user=context.model_dump_json(exclude_none=True),
            model_type=ActionProposal,
            invalid_code="invalid_action_proposal",
        )

    def _request_model(
        self,
        *,
        system: str,
        user: str,
        model_type: type[ModelT],
        invalid_code: str,
    ) -> ModelT:
        if not self._api_key:
            raise LLMProviderError("missing_api_key", "GROQ_API_KEY is not configured")
        if not self._model:
            raise LLMProviderError("configuration_error", "GROQ_MODEL is empty")
        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self._transport(
                GROQ_CHAT_COMPLETIONS_URL, headers, payload, self._timeout
            )
        except (TimeoutError, socket.timeout) as exc:
            raise LLMProviderError("timeout", "Groq request timed out") from exc
        except HTTPError as exc:
            code = (
                "invalid_key"
                if exc.code in {401, 403}
                else "unavailable_model"
                if exc.code == 404
                else "rate_limited"
                if exc.code == 429
                else "provider_error"
            )
            raise LLMProviderError(code, f"Groq returned HTTP {exc.code}") from exc
        except (ConnectionError, URLError, OSError) as exc:
            raise LLMProviderError("unavailable", f"Groq unavailable: {exc}") from exc

        content = _message_content(response)
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("malformed_json", "Groq returned malformed JSON") from exc
        try:
            return model_type.model_validate(raw)
        except ValidationError as exc:
            raise LLMProviderError(
                invalid_code, f"Groq JSON failed {model_type.__name__} validation"
            ) from exc


def _message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError("malformed_response", "Groq response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMProviderError("malformed_response", "Groq choice is not an object")
    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LLMProviderError("malformed_response", "Groq response has no message content")
    return content


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official Groq URL
        try:
            decoded = json.loads(response.read().decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                "malformed_response", "Groq HTTP response was not valid JSON"
            ) from exc
    if not isinstance(decoded, dict):
        raise LLMProviderError("malformed_response", "Groq response must be a JSON object")
    return decoded
