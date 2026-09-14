from __future__ import annotations

import json
import time
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import RunPayload


@dataclass(frozen=True, slots=True)
class UploadResult:
    success: bool
    status_code: int | None
    attempts: int
    message: str


class Opener(Protocol):
    def __call__(self, request: Request, *, timeout: float): ...


class SameOriginRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original = urlparse(req.full_url)
        redirected = urlparse(newurl)
        same_origin = redirected.scheme == "https" and (
            original.scheme,
            original.hostname,
            original.port,
        ) == (redirected.scheme, redirected.hostname, redirected.port)
        if not same_origin or code not in {307, 308}:
            raise HTTPError(
                newurl, code, "unsafe ingestion redirect refused", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def upload_run(
    payload: RunPayload,
    endpoint: str,
    secret: str,
    *,
    timeout_seconds: float = 20,
    max_attempts: int = 3,
    backoff_seconds: float = 1,
    opener: Opener | None = None,
) -> UploadResult:
    if not 0 < timeout_seconds <= 30:
        raise ValueError("timeout must be between 0 and 30 seconds")
    if not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5")
    if not endpoint or not secret:
        raise ValueError("endpoint and secret are required")
    parsed_endpoint = urlparse(endpoint)
    if (
        parsed_endpoint.scheme != "https"
        or not parsed_endpoint.hostname
        or parsed_endpoint.username
        or parsed_endpoint.password
    ):
        raise ValueError("endpoint must be an absolute HTTPS URL without credentials")

    open_request = opener or build_opener(SameOriginRedirectHandler()).open

    body = payload.to_json().encode("utf-8")
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "atlanta-car-finder-scrapers/0.1",
    }

    for attempt in range(1, max_attempts + 1):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with open_request(request, timeout=timeout_seconds) as response:
                status = response.status
                response_body = response.read(16_384).decode("utf-8", errors="replace")
                message = _response_message(response_body, status)
                if 200 <= status < 300:
                    return UploadResult(True, status, attempt, message)
                if status < 500 or attempt == max_attempts:
                    return UploadResult(False, status, attempt, message)
        except HTTPError as exc:
            status = exc.code
            response_body = exc.read(16_384).decode("utf-8", errors="replace")
            message = _response_message(response_body, status)
            if status < 500 or attempt == max_attempts:
                return UploadResult(False, status, attempt, message)
        except (
            URLError,
            TimeoutError,
            ConnectionError,
            RemoteDisconnected,
            OSError,
        ) as exc:
            if attempt == max_attempts:
                return UploadResult(
                    False, None, attempt, f"transport error: {type(exc).__name__}"
                )
        if backoff_seconds:
            time.sleep(backoff_seconds * attempt)

    raise RuntimeError("upload retry loop ended unexpectedly")


def _response_message(body: str, status: int) -> str:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return f"HTTP {status}"
    if isinstance(value, dict):
        for key in ("message", "error"):
            if isinstance(value.get(key), str):
                return value[key][:500]
    return f"HTTP {status}"

