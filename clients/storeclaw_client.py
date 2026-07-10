from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx


AUTH_COOKIE_NAME = "storeclaw-account-token"
LOGGER = logging.getLogger(__name__)


@dataclass
class LoginResult:
    account_id: str
    token: str
    raw: dict[str, Any]


class StoreClawClient:
    def __init__(self, base_url: str, timeout: float = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=True)
        self.account_id = os.getenv("STORECLAW_ACCOUNT_ID", "").strip()
        self.team_id = os.getenv("STORECLAW_TEAM_ID", "").strip()

    def close(self) -> None:
        self.client.close()

    def login(self, email: str, password: str) -> LoginResult:
        response = self.client.post(
            "/api/account/login",
            json={"parameter": {"email": email, "password": password}},
            headers=self._account_headers(),
        )
        payload = self._json(response)
        self._log_response("POST", "/api/account/login", response.status_code, payload)
        self._assert_business_success(payload, "login failed")

        data = payload.get("data") or {}
        token = str(data.get("token") or self.client.cookies.get(AUTH_COOKIE_NAME) or "")
        account_id = str(data.get("accountId") or data.get("account_id") or self.account_id or "")

        if not token:
            raise AssertionError(f"login succeeded but token was not found: {self._redact(payload)}")
        if not account_id:
            raise AssertionError(f"login succeeded but accountId was not found: {self._redact(payload)}")

        self.account_id = account_id
        self._set_auth_cookie(token)
        return LoginResult(account_id=account_id, token=token, raw=data)

    def load_team_context(self) -> dict[str, Any]:
        response = self.client.post(
            "/app/api/storeclawTeam/business/v1/context/lastSelected",
            json={},
            headers=self._app_headers(include_team=False),
        )
        payload = self._json(response)
        self._log_response(
            "POST",
            "/app/api/storeclawTeam/business/v1/context/lastSelected",
            response.status_code,
            payload,
        )
        self._assert_business_success(payload, "load team context failed")
        data = payload.get("data") or {}
        self.team_id = str(data.get("teamId") or self.team_id or "")
        return data

    def create_lui_session(self, session_name: str | None = None) -> dict[str, Any]:
        body = {"session_name": session_name} if session_name else {}
        LOGGER.info("request POST /app/api/ai-agent/sessions body=%s", body)
        response = self.client.post(
            "/app/api/ai-agent/sessions",
            json=body,
            headers={**self._ai_headers(), "Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = self._json(response)
        self._log_response("POST", "/app/api/ai-agent/sessions", response.status_code, payload)

        if payload.get("error_type"):
            raise AssertionError(f"create session failed: {payload}")
        if not self._session_id(payload):
            raise AssertionError(f"create session response missing session_id: {payload}")
        return payload

    def send_lui_message(self, session_id: str, message: str) -> list[dict[str, Any]]:
        multipart = {
            "message": (None, message),
            "stream": (None, "true"),
            "session_id": (None, session_id),
        }
        headers = {**self._ai_headers(), "Accept": "text/event-stream"}

        events: list[dict[str, Any]] = []
        LOGGER.info(
            "request POST /app/api/ai-agent/sandbox/agents/runs session_id=%s message=%r",
            session_id,
            message,
        )
        with self.client.stream(
            "POST",
            "/app/api/ai-agent/sandbox/agents/runs",
            files=multipart,
            headers=headers,
            timeout=self.timeout,
        ) as response:
            response.raise_for_status()
            for event in self._iter_sse(response):
                events.append(event)

        if not events:
            raise AssertionError("LUI run stream returned no SSE events")
        LOGGER.info(
            "response POST /app/api/ai-agent/sandbox/agents/runs status=%s events=%s response_text=%r",
            response.status_code,
            len(events),
            self.response_text_from_events(events),
        )
        return events

    def get_session_runs(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/app/api/ai-agent/sandbox/sessions/{session_id}/runs",
            params={"limit": limit, "sort": "desc"},
            headers=self._ai_headers(),
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            runs = payload
            self._log_response(
                "GET",
                f"/app/api/ai-agent/sandbox/sessions/{session_id}/runs",
                response.status_code,
                self._summarize_runs(runs),
            )
            return runs
        if isinstance(payload, dict):
            data = payload.get("data", [])
            if isinstance(data, list):
                self._log_response(
                    "GET",
                    f"/app/api/ai-agent/sandbox/sessions/{session_id}/runs",
                    response.status_code,
                    self._summarize_runs(data),
                )
                return data
        raise AssertionError(f"expected session run list, got: {payload!r}")

    @staticmethod
    def response_text_from_events(events: list[dict[str, Any]]) -> str:
        chunks: list[str] = []
        for event in events:
            event_name = str(event.get("event", "")).lower()
            if event_name in {"error", "user_message", "user-message"}:
                continue
            chunks.extend(StoreClawClient._text_chunks(event))
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    def _account_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json;charset=utf-8",
            "x-storeclaw-i18n": self._i18n_header(),
        }

    def _app_headers(self, include_team: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json;charset=utf-8",
            "x-storeclaw-i18n": self._i18n_header(),
        }
        if include_team:
            headers["x-storeclaw-team-id"] = self.team_id
        return headers

    def _ai_headers(self) -> dict[str, str]:
        return {
            "x-storeclaw-account-id": self.account_id,
            "x-storeclaw-team-id": self.team_id,
            "x-storeclaw-i18n": self._i18n_header(),
        }

    @staticmethod
    def _i18n_header() -> str:
        return json.dumps(
            {
                "language": "zh-CN",
                "timezone": "-8",
                "timezoneCity": "Asia/Shanghai",
            },
            ensure_ascii=False,
        )

    def _set_auth_cookie(self, token: str) -> None:
        host = urlparse(self.base_url).hostname or "www.storeclawdev.com"
        self.client.cookies.set(AUTH_COOKIE_NAME, token, domain=host, path="/")

    @staticmethod
    def _log_response(method: str, path: str, status_code: int, payload: Any) -> None:
        LOGGER.info(
            "response %s %s status=%s body=%s",
            method,
            path,
            status_code,
            StoreClawClient._compact_json(StoreClawClient._redact(payload)),
        )

    @staticmethod
    def _compact_json(payload: Any, max_length: int = 3000) -> str:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) <= max_length:
            return text
        return f"{text[:max_length]}...<truncated {len(text) - max_length} chars>"

    @staticmethod
    def _summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(runs),
            "latest": StoreClawClient._redact(runs[0]) if runs else None,
        }

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise AssertionError(f"expected JSON object, got: {data!r}")
        return data

    @staticmethod
    def _assert_business_success(payload: dict[str, Any], message: str) -> None:
        errcode = payload.get("errcode")
        if errcode not in (0, "0", None):
            raise AssertionError(f"{message}: {payload}")

    @staticmethod
    def _session_id(payload: dict[str, Any]) -> str:
        return str(payload.get("session_id") or payload.get("sessionId") or payload.get("id") or "")

    @staticmethod
    def _iter_sse(response: httpx.Response) -> Iterator[dict[str, Any]]:
        buffer: list[str] = []
        for line in response.iter_lines():
            if line == "":
                event = StoreClawClient._parse_sse_block(buffer)
                buffer = []
                if event is not None:
                    yield event
                continue
            buffer.append(line)

        event = StoreClawClient._parse_sse_block(buffer)
        if event is not None:
            yield event

    @staticmethod
    def _parse_sse_block(lines: list[str]) -> dict[str, Any] | None:
        data_lines = [line[5:].strip() for line in lines if line.startswith("data:")]
        if not data_lines:
            return None

        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return None

        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return {"event": "raw", "data": data}
        return parsed if isinstance(parsed, dict) else {"event": "data", "data": parsed}

    @staticmethod
    def _text_chunks(value: Any) -> list[str]:
        text_keys = {
            "answer",
            "content",
            "delta",
            "message",
            "output",
            "reply",
            "response",
            "text",
        }
        ignored_keys = {
            "email",
            "error",
            "event",
            "id",
            "metadata",
            "password",
            "prompt",
            "role",
            "session_id",
            "sessionid",
            "token",
            "type",
        }

        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            chunks: list[str] = []
            for item in value:
                chunks.extend(StoreClawClient._text_chunks(item))
            return chunks
        if not isinstance(value, dict):
            return []
        if str(value.get("role", "")).lower() in {"human", "user"}:
            return []

        chunks = []
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in ignored_keys:
                continue
            if normalized_key in text_keys:
                chunks.extend(StoreClawClient._text_chunks(item))
            elif isinstance(item, (dict, list)):
                chunks.extend(StoreClawClient._text_chunks(item))
        return chunks

    @staticmethod
    def _redact(payload: Any) -> Any:
        sensitive_keys = {"authorization", "cookie", "password", "token", AUTH_COOKIE_NAME}

        def walk(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: "***REDACTED***" if str(key).lower() in sensitive_keys else walk(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [walk(item) for item in value]
            return value

        return walk(payload)
