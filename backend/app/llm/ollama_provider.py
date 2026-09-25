from __future__ import annotations

import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.extraction.models import ExtractedTravelIntent
from app.llm.base import ExtractionContext, LLMProviderError

OllamaTransport = Callable[[str, dict[str, Any], float], dict[str, Any]]


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        transport: OllamaTransport | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model.strip()
        self._timeout = timeout_seconds
        self._transport = transport or _post_json

    def extract_travel_request(
        self, text: str, context: ExtractionContext
    ) -> ExtractedTravelIntent:
        if not self._model:
            raise LLMProviderError("configuration_error", "OLLAMA_MODEL is empty")
        payload = {
            "model": self._model,
            "stream": False,
            "format": ExtractedTravelIntent.model_json_schema(),
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "system",
                    "content": _system_prompt(context),
                },
                {"role": "user", "content": text},
            ],
        }
        try:
            response = self._transport(self._url, payload, self._timeout)
        except (TimeoutError, socket.timeout) as exc:
            raise LLMProviderError("timeout", "Ollama request timed out") from exc
        except (ConnectionError, HTTPError, URLError, OSError) as exc:
            raise LLMProviderError("unavailable", f"Ollama unavailable: {exc}") from exc

        content = response.get("message", {}).get("content")
        if not isinstance(content, str):
            raise LLMProviderError("malformed_response", "Ollama response has no message content")
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("malformed_json", "Ollama returned malformed JSON") from exc
        try:
            return ExtractedTravelIntent.model_validate(raw)
        except ValidationError as exc:
            raise LLMProviderError("invalid_schema", "Ollama JSON failed extraction schema") from exc


def _system_prompt(context: ExtractionContext) -> str:
    allowed_cities = ", ".join(
        f"{name}={city_id}" for name, city_id in sorted(context.city_name_to_id.items())
    )
    airlines = ", ".join(context.known_airlines)
    return (
        "Extract only the user's travel facts into the supplied JSON schema. "
        "Do not plan, calculate feasibility, call tools, browse, or invent missing values. "
        "Use only these city IDs: "
        f"{allowed_cities}. Known demo airlines: {airlines}. "
        "Use INR numeric values for money. Put unavailable required fields in "
        "missing_required_fields and explain non-critical uncertainty in warnings. "
        "All destinations are mandatory and must remain in the user's order."
    )


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is config-validated local-only
        try:
            decoded = json.loads(response.read().decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                "malformed_response", "Ollama HTTP response was not valid JSON"
            ) from exc
    if not isinstance(decoded, dict):
        raise LLMProviderError("malformed_response", "Ollama response must be a JSON object")
    return decoded
