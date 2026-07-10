import json
import logging
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import pytest
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Allow IDEs to run this file directly from tests/.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from clients.storeclaw_client import StoreClawClient  # noqa: E402


load_dotenv()
LOGGER = logging.getLogger(__name__)
DEFAULT_CASES_FILE = PROJECT_ROOT / "tests/data/lui_cases.json"
RUN_COMPLETED_EVENT = "RunCompleted"


def _load_lui_cases() -> list[dict[str, Any]]:
    cases_file = Path(os.getenv("STORECLAW_CASES_FILE", str(DEFAULT_CASES_FILE)))
    if not cases_file.is_absolute():
        cases_file = PROJECT_ROOT / cases_file
    if not cases_file.exists():
        raise FileNotFoundError(f"LUI cases file not found: {cases_file}")

    with cases_file.open(encoding="utf-8") as file:
        cases = json.load(file)
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"LUI cases file must contain a non-empty JSON list: {cases_file}")
    return [_normalize_case(case, index) for index, case in enumerate(cases, start=1)]


def _normalize_case(case: Any, index: int) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError(f"LUI case #{index} must be a JSON object: {case!r}")

    name = str(case.get("name") or f"case_{index}")
    prompt = str(case.get("prompt") or "").strip()
    assertions = case.get("assertions")
    session = _normalize_session(case.get("session"), name)

    if not prompt:
        raise ValueError(f"LUI case {name!r} missing required field: prompt")
    if not isinstance(assertions, dict) or not assertions:
        raise ValueError(f"LUI case {name!r} missing required object: assertions")

    return {
        "name": name,
        "prompt": prompt,
        "session": session,
        "assertions": assertions,
    }


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"missing env var: {name}")
    return value


def _normalize_session(session: Any, case_name: str) -> dict[str, Any]:
    if session is None:
        return {"continue_previous": False}
    if not isinstance(session, dict):
        raise ValueError(f"LUI case {case_name!r} field session must be a JSON object")

    return {"continue_previous": bool(session.get("continue_previous", False))}


@pytest.mark.integration
def test_login_and_start_lui_chat_sessions_batch(request: pytest.FixtureRequest):
    base_url = _required_env("STORECLAW_BASE_URL")
    email = _required_env("STORECLAW_EMAIL")
    password = _required_env("STORECLAW_PASSWORD")
    timeout = float(os.getenv("STORECLAW_TIMEOUT", "1800"))
    runs_poll_timeout = _float_env("STORECLAW_RUNS_POLL_TIMEOUT", 120)
    runs_poll_interval = _float_env("STORECLAW_RUNS_POLL_INTERVAL", 5)
    max_concurrency = _max_concurrency()
    cases = _load_lui_cases()
    case_chains = _case_chains(cases)
    for chain_index, chain in enumerate(case_chains, start=1):
        for chain_position, case in enumerate(chain, start=1):
            case["chain_index"] = chain_index
            case["chain_position"] = chain_position

    LOGGER.info(
        "start LUI batch cases=%s chains=%s max_concurrency=%s",
        len(cases),
        len(case_chains),
        max_concurrency,
    )
    results = []
    with ThreadPoolExecutor(max_workers=min(max_concurrency, len(case_chains))) as executor:
        future_to_case = {
            executor.submit(
                _run_lui_case_chain,
                chain,
                base_url,
                email,
                password,
                timeout,
                runs_poll_timeout,
                runs_poll_interval,
            ): chain
            for chain in case_chains
        }
        for future in as_completed(future_to_case):
            chain_results = future.result()
            results.extend(chain_results)
            for result in chain_results:
                _add_case_report_item(request, result)

    failed_results = [result for result in results if result["outcome"] == "failed"]
    assert not failed_results, _failure_summary(failed_results)


def _case_chains(cases: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chains: list[list[dict[str, Any]]] = []
    for case in cases:
        if case["session"]["continue_previous"]:
            if not chains:
                raise ValueError(
                    f"LUI case {case['name']!r} enabled session.continue_previous, "
                    "but there is no previous case"
                )
            chains[-1].append(case)
            continue
        chains.append([case])
    return chains


def _run_lui_case_chain(
    cases: list[dict[str, Any]],
    base_url: str,
    email: str,
    password: str,
    timeout: float,
    runs_poll_timeout: float,
    runs_poll_interval: float,
) -> list[dict[str, Any]]:
    results = []
    previous_session_id = ""
    for case in cases:
        result, previous_session_id = _run_lui_case(
            case=case,
            base_url=base_url,
            email=email,
            password=password,
            timeout=timeout,
            runs_poll_timeout=runs_poll_timeout,
            runs_poll_interval=runs_poll_interval,
            previous_session_id=previous_session_id,
        )
        results.append(result)
        if result["outcome"] == "failed":
            results.extend(_skipped_after_chain_failure(cases, case))
            break
    return results


def _run_lui_case(
    case: dict[str, Any],
    base_url: str,
    email: str,
    password: str,
    timeout: float,
    runs_poll_timeout: float,
    runs_poll_interval: float,
    previous_session_id: str,
) -> tuple[dict[str, Any], str]:
    started_at = time.monotonic()
    prompt = case["prompt"]
    assertions = case["assertions"]
    session_config = case["session"]
    case_name = case["name"]
    logs: list[str] = []
    details: dict[str, Any] = {
        "prompt": prompt,
        "assertions": assertions,
        "continue_previous": session_config["continue_previous"],
        "chain_index": case.get("chain_index"),
        "chain_position": case.get("chain_position"),
        "timeout_seconds": timeout,
        "runs_poll_timeout_seconds": runs_poll_timeout,
        "runs_poll_interval_seconds": runs_poll_interval,
    }

    def record(message: str) -> None:
        logs.append(message)
        LOGGER.info("[%s] %s", case_name, message)

    record(f"start assertions={assertions} prompt={prompt!r}")
    client = StoreClawClient(base_url=base_url, timeout=timeout)
    try:
        login = client.login(email=email, password=password)
        record(f"login success account_id={login.account_id} token_present={bool(login.token)}")
        details["account_id"] = login.account_id
        details["token_present"] = bool(login.token)
        assert login.account_id
        assert login.token

        team_context = client.load_team_context()
        record(f"team context loaded team_id={client.team_id} keys={sorted(team_context.keys())}")
        details["team_id"] = client.team_id
        details["team_context_keys"] = sorted(team_context.keys())

        if session_config["continue_previous"]:
            assert previous_session_id, "previous session_id is required when session.continue_previous is true"
            session_id = previous_session_id
            record(f"LUI session continued session_id={session_id}")
        else:
            session = client.create_lui_session(session_name=prompt[:30])
            session_id = session.get("session_id") or session.get("sessionId") or session.get("id")
            record(f"LUI session created session_id={session_id}")
            assert session_id, session

        details["session_id"] = session_id
        events = client.send_lui_message(session_id=session_id, message=prompt)
        details["events_count"] = len(events)

        event_names = {event.get("event") for event in events}
        details["event_names"] = sorted(str(event_name) for event_name in event_names if event_name)
        assert event_names, events[:5]
        assert not any(str(event.get("event", "")).lower() == "error" for event in events), events[-5:]
        run_completed = _has_event(events, RUN_COMPLETED_EVENT)
        details["run_completed_event_received"] = run_completed

        response_text = client.response_text_from_events(events)
        record(f"LUI response_text={response_text!r}")
        details["response_text"] = response_text
        details["skill_names"] = sorted(_skill_names({}, events, response_text))
        assert run_completed, (
            f"LUI stream ended before {RUN_COMPLETED_EVENT}. "
            f"event_names={details['event_names']} response_text={response_text!r}"
        )

        runs, runs_poll_attempts = _wait_for_session_runs(
            client=client,
            session_id=session_id,
            timeout=runs_poll_timeout,
            interval=runs_poll_interval,
            record=record,
        )
        details["runs_poll_attempts"] = runs_poll_attempts
        details["runs_count"] = len(runs)
        assert isinstance(runs, list)
        assert runs, "session run list should not be empty after sending a LUI message"
        latest_run = runs[0]
        details["latest_run_id"] = latest_run.get("run_id") or latest_run.get("id")
        details["latest_run_status"] = latest_run.get("status")
        details["latest_run_duration_seconds"] = _run_duration_seconds(latest_run)
        details["tool_names"] = sorted(_tool_call_names(latest_run))
        details["skill_names"] = sorted(_skill_names(latest_run, events, response_text))
        _assert_lui_result(assertions, response_text, latest_run, events)
        details["assertions_passed"] = True
        return _case_result(case_name, "passed", started_at, logs=logs, details=details), session_id
    except Exception:
        error = traceback.format_exc()
        details["error"] = error
        details["assertions_passed"] = False
        record(error)
        return _case_result(case_name, "failed", started_at, longrepr=error, logs=logs, details=details), ""
    finally:
        client.close()


def _skipped_after_chain_failure(cases: list[dict[str, Any]], failed_case: dict[str, Any]) -> list[dict[str, Any]]:
    skipped = []
    skip_remaining = False
    for case in cases:
        if skip_remaining:
            skipped.append(
                _case_result(
                    case["name"],
                    "skipped",
                    time.monotonic(),
                    longrepr=f"Skipped because previous case {failed_case['name']!r} failed in the same session chain.",
                    details={
                        "skip_reason": f"Previous case {failed_case['name']!r} failed in the same session chain.",
                        "continue_previous": case["session"]["continue_previous"],
                        "chain_index": case.get("chain_index"),
                        "chain_position": case.get("chain_position"),
                        "prompt": case["prompt"],
                        "assertions": case["assertions"],
                    },
                )
            )
        if case is failed_case:
            skip_remaining = True
    return skipped


def _max_concurrency() -> int:
    raw_value = os.getenv("STORECLAW_MAX_CONCURRENCY", "10").strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("STORECLAW_MAX_CONCURRENCY must be an integer from 1 to 10") from exc
    if value < 1 or value > 10:
        raise ValueError("STORECLAW_MAX_CONCURRENCY must be between 1 and 10")
    return value


def _float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def _has_event(events: list[dict[str, Any]], event_name: str) -> bool:
    return any(str(event.get("event") or "") == event_name for event in events)


def _wait_for_session_runs(
    client: StoreClawClient,
    session_id: str,
    timeout: float,
    interval: float,
    record: Callable[[str], None],
) -> tuple[list[dict[str, Any]], int]:
    deadline = time.monotonic() + timeout
    attempt = 1
    while True:
        runs = client.get_session_runs(session_id=session_id, limit=5)
        record(f"session runs poll attempt={attempt} count={len(runs)}")
        if runs:
            return runs, attempt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return runs, attempt
        time.sleep(min(interval, remaining))
        attempt += 1


def _case_result(
    case_name: str,
    outcome: str,
    started_at: float,
    longrepr: str = "",
    logs: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": case_name,
        "nodeid": f"tests/test_lui_chat_flow.py::{case_name}",
        "outcome": outcome,
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "longrepr": longrepr,
        "stdout": "",
        "stderr": "",
        "logs": "\n".join(logs or []),
        "details": details or {},
    }


def _add_case_report_item(request: pytest.FixtureRequest, result: dict[str, Any]) -> None:
    report_items = request.config._storeclaw_case_report_items  # type: ignore[attr-defined]
    report_items.append(result)


def _failure_summary(results: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"{result['name']} failed:\n{result['longrepr']}" for result in results)


def _assert_lui_result(
    assertions: dict[str, Any],
    response_text: str,
    latest_run: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    if assertions.get("response_not_empty", True):
        assert response_text, events[-10:]

    expected_run_status = assertions.get("run_status")
    if expected_run_status is not None:
        actual_status = str(latest_run.get("status") or "")
        assert actual_status == expected_run_status, latest_run

    max_duration_seconds = assertions.get("max_duration_seconds")
    if max_duration_seconds is not None:
        duration = _run_duration_seconds(latest_run)
        assert duration is not None, latest_run
        assert duration <= float(max_duration_seconds), latest_run

    contains_all = _string_list(assertions.get("contains_all"), "contains_all")
    for keyword in contains_all:
        assert keyword in response_text, response_text

    contains_any = _string_list(assertions.get("contains_any"), "contains_any")
    if contains_any:
        assert any(keyword in response_text for keyword in contains_any), response_text

    not_contains = _string_list(assertions.get("not_contains"), "not_contains")
    for keyword in not_contains:
        assert keyword not in response_text, response_text

    tool_called = _string_list(assertions.get("tool_called"), "tool_called")
    if tool_called:
        actual_tool_names = _tool_call_names(latest_run)
        for tool_name in tool_called:
            assert tool_name in actual_tool_names, latest_run

    tool_not_called = _string_list(assertions.get("tool_not_called"), "tool_not_called")
    if tool_not_called:
        actual_tool_names = _tool_call_names(latest_run)
        for tool_name in tool_not_called:
            assert tool_name not in actual_tool_names, latest_run

    skill_called = _string_list(assertions.get("skill_called"), "skill_called")
    if skill_called:
        actual_skill_names = _skill_names(latest_run, events, response_text)
        for skill_name in skill_called:
            assert skill_name in actual_skill_names, {
                "expected_skill": skill_name,
                "actual_skills": sorted(actual_skill_names),
                "response_text": response_text,
                "latest_run": latest_run,
            }

    skill_not_called = _string_list(assertions.get("skill_not_called"), "skill_not_called")
    if skill_not_called:
        actual_skill_names = _skill_names(latest_run, events, response_text)
        for skill_name in skill_not_called:
            assert skill_name not in actual_skill_names, {
                "unexpected_skill": skill_name,
                "actual_skills": sorted(actual_skill_names),
                "response_text": response_text,
                "latest_run": latest_run,
            }


def _run_duration_seconds(run: dict[str, Any]) -> float | None:
    metrics = run.get("metrics")
    if not isinstance(metrics, dict):
        return None
    duration = metrics.get("duration")
    if isinstance(duration, (int, float)):
        return float(duration)
    return None


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"assertions.{field_name} must be a list of strings")
    result = [str(item).strip() for item in value]
    return [item for item in result if item]


def _tool_call_names(run: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    messages = run.get("messages")
    if not isinstance(messages, list):
        return names

    for message in messages:
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if isinstance(function, dict) and function.get("name"):
                names.add(str(function["name"]))
    return names


def _skill_names(run: dict[str, Any], events: list[dict[str, Any]], response_text: str) -> set[str]:
    names: set[str] = set()
    names.update(_skill_names_from_value(run))
    names.update(_skill_names_from_value(events))
    names.update(_skill_names_from_text(response_text))
    return {name for name in names if name}


def _skill_names_from_value(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "skill_name":
                names.add(str(item))
                continue
            if str(key) == "arguments" and isinstance(item, str):
                try:
                    names.update(_skill_names_from_value(json.loads(item)))
                    continue
                except json.JSONDecodeError:
                    names.update(_skill_names_from_text(item))
            names.update(_skill_names_from_value(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_skill_names_from_value(item))
    elif isinstance(value, str):
        names.update(_skill_names_from_text(value))
    return names


def _skill_names_from_text(text: str) -> set[str]:
    names = set()
    patterns = [
        r"skill_name\s*=\s*['\"]?([A-Za-z0-9_.-]+)",
        r'"skill_name"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        names.update(match.group(1) for match in re.finditer(pattern, text))
    return names
