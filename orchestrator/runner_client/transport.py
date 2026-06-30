from __future__ import annotations

import time
from typing import Any

import httpx


class RunnerTransportError(RuntimeError):
    """Base transport error for the client runner."""


class RunnerUnauthorized(RunnerTransportError):
    """Raised when the control plane rejects the runner token."""


class RunnerTransport:
    def __init__(
        self,
        base_url: str,
        *,
        api_token: str = "",
        http_client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_base_sec: float = 0.25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self._http_client = http_client
        self._owns_client = http_client is None
        self.max_retries = max(1, int(max_retries))
        self.backoff_base_sec = max(0.0, float(backoff_base_sec))

    def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def register(self, *, workspace_id: str, label: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/runner/register",
            json={"workspace_id": workspace_id, "label": label},
            runner_token=None,
            api_auth=True,
        )

    def heartbeat(self, runner_token: str) -> dict[str, Any]:
        return self._request("POST", "/api/runner/heartbeat", runner_token=runner_token)

    def lease(self, runner_token: str, *, ttl_sec: int) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/runner/lease",
            json={"ttl_sec": ttl_sec},
            runner_token=runner_token,
        )

    def send_events(self, runner_token: str, *, job_id: str, events: list[dict]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/runner/events",
            json={"job_id": job_id, "events": events},
            runner_token=runner_token,
        )

    def complete(
        self,
        runner_token: str,
        *,
        job_id: str,
        result: dict,
        status: str = "terminal",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/runner/jobs/{job_id}/complete",
            json={"status": status, "result": result},
            runner_token=runner_token,
        )

    def release(self, runner_token: str, *, job_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/runner/jobs/{job_id}/release",
            runner_token=runner_token,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        runner_token: str | None = None,
        api_auth: bool = False,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client().request(
                    method,
                    f"{self.base_url}{path}",
                    json=json,
                    headers=self._headers(runner_token=runner_token, api_auth=api_auth),
                )
                if response.status_code == 401:
                    raise RunnerUnauthorized("Runner token was revoked or rejected")
                if response.status_code >= 500:
                    raise RunnerTransportError(f"Control plane returned HTTP {response.status_code}")
                response.raise_for_status()
                body = response.json()
                return body if isinstance(body, dict) else {}
            except RunnerUnauthorized:
                raise
            except (httpx.HTTPError, RunnerTransportError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(self.backoff_base_sec * (2**attempt))
        raise RunnerTransportError(str(last_error or "control-plane request failed"))

    def _headers(self, *, runner_token: str | None, api_auth: bool) -> dict[str, str]:
        if runner_token:
            return {"Authorization": f"Bearer {runner_token}"}
        if api_auth and self.api_token:
            return {"Authorization": f"Bearer {self.api_token}"}
        return {}

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=30.0)
        return self._http_client
