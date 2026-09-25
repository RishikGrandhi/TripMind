from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.tools.external.errors import ExternalToolError


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    payload: dict[str, object]


HttpTransport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]


def request_json(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpResponse:
    request = Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except HTTPError as exc:
        code = "invalid_key" if exc.code in {401, 403} else "rate_limited" if exc.code == 429 else "provider_error"
        raise ExternalToolError("http", code, f"Provider returned HTTP {exc.code}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ExternalToolError("http", "timeout", "Provider request timed out") from exc
    except (URLError, OSError) as exc:
        raise ExternalToolError("http", "unavailable", "Provider request was unavailable") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalToolError("http", "malformed_response", "Provider returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ExternalToolError("http", "malformed_response", "Provider response must be an object")
    return HttpResponse(status_code=status, payload=payload)
