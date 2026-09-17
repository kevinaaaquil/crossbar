"""Shared HTTP plumbing: one client, bounded retries, honest errors."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

import httpx

from crossbar.providers.base import ProviderError

RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


class HttpProvider:
    """Base for HTTP-speaking providers.

    ``transport`` exists so the whole request/response path can be tested
    without a network, and ``sleep`` so retry tests do not actually wait.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout_s: float = 120.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.sleep = sleep
        self.extra_headers = dict(extra_headers or {})
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, payload: Mapping[str, Any], headers: Mapping[str, str]) -> dict:
        url = f"{self.base_url}{path}"
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(url, json=dict(payload), headers=dict(headers))
            except httpx.HTTPError as exc:
                last_error = f"request to {url} failed: {exc}"
                if attempt >= self.max_retries:
                    raise ProviderError(last_error) from exc
                self.sleep(self._backoff(attempt))
                continue

            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                self.sleep(self._backoff(attempt))
                continue
            if response.status_code >= 400:
                raise ProviderError(
                    f"{self.model}: HTTP {response.status_code} from {url}: "
                    f"{response.text[:400]}"
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderError(
                    f"{self.model}: response was not JSON: {response.text[:200]}"
                ) from exc
            if not isinstance(body, dict):
                raise ProviderError(f"{self.model}: expected a JSON object, got {type(body).__name__}")
            return body
        raise ProviderError(last_error or f"{self.model}: exhausted retries against {url}")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.5 * (2**attempt))
