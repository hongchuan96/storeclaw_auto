from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

import httpx


LOGGER = logging.getLogger(__name__)


@dataclass
class JudgeRequest:
    case_name: str
    prompt: str
    rubric: str
    scoring_criteria: dict[str, Any]
    response_text: str
    run_status: str
    skill_names: list[str]
    tool_names: list[str]
    event_names: list[str]


@dataclass
class JudgeResult:
    enabled: bool
    model: str
    score: int
    passed: bool
    reason: str
    strengths: list[str]
    issues: list[str]
    dimension_scores: dict[str, Any]
    expected_behavior: str
    actual_behavior: str
    raw_response: str = ""


class JudgeClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("STORECLAW_JUDGE_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("STORECLAW_JUDGE_BASE_URL", "")).rstrip("/")
        self.model = (model or os.getenv("STORECLAW_JUDGE_MODEL", "")).strip()
        self.timeout = timeout if timeout is not None else float(os.getenv("STORECLAW_JUDGE_TIMEOUT", "300"))
        if not self.api_key:
            raise ValueError("STORECLAW_JUDGE_API_KEY is required when judge is enabled")
        if not self.base_url:
            raise ValueError("STORECLAW_JUDGE_BASE_URL is required when judge is enabled")
        if not self.model:
            raise ValueError("STORECLAW_JUDGE_MODEL must not be empty when judge is enabled")
        self.client = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def close(self) -> None:
        self.client.close()

    def evaluate(self, request: JudgeRequest, pass_score: int) -> JudgeResult:
        messages = [
            {"role": "system", "content": self._system_prompt(pass_score)},
            {"role": "user", "content": self._user_prompt(request)},
        ]
        response = self.client.post(
            "/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = self._message_content(payload)
        result_payload = self._parse_json_object(content)
        result = self._normalize_result(result_payload, pass_score, content)
        LOGGER.info(
            "judge result case=%s model=%s score=%s passed=%s reason=%s",
            request.case_name,
            self.model,
            result.score,
            result.passed,
            result.reason,
        )
        return result

    def _system_prompt(self, pass_score: int) -> str:
        return (
            "你是 StoreClaw LUI 自动化测试的评审 agent。"
            "你的任务是根据用户 prompt、评测 rubric、StoreClaw 输出和执行证据，判断输出是否满足用户意图。"
            "只评价 StoreClaw 的最终表现，不要补写答案。"
            "工具/skill/run 状态是证据，不能忽略。"
            "如果外部系统真实数据为空，只要回答明确说明为空且没有误导，可以判为满足。"
            f"默认通过阈值是 {pass_score} 分。"
            "必须按 user message 中的 scoring_criteria 逐项评分。"
            "每个维度的 score 必须是 0 到该维度 effective_weight 的得分，而不是百分比。"
            "如果存在 weight 和 effective_weight，以 effective_weight 为准。"
            "维度 not_applicable 为 true 时，不评价该维度，也不能因为该维度扣分。"
            "总分 score 必须等于所有适用维度 score 之和。"
            "如果核心意图没有满足，总分最高 60。"
            "如果明显编造外部系统结果，总分最高 40。"
            "如果本应调用工具/skill 但完全未调用，总分最高 70。"
            "必须只返回一个 JSON object，字段为："
            "score(number 0-100), passed(boolean), reason(string), strengths(array string), "
            "issues(array string), dimension_scores(object), expected_behavior(string), actual_behavior(string)。"
            "dimension_scores 的 key 必须与 scoring_criteria 一致，每项包含 score(number), weight(number), reason(string)。"
            "其中 weight 必须填写该维度的 effective_weight。"
        )

    def _user_prompt(self, request: JudgeRequest) -> str:
        return json.dumps(self.evidence_payload(request), ensure_ascii=False, indent=2)

    def evidence_payload(self, request: JudgeRequest) -> dict[str, Any]:
        evidence = asdict(request)
        response_text = request.response_text
        truncated_response_text = self._truncate_response_text(response_text)
        evidence["response_text"] = truncated_response_text
        evidence["response_text_original_chars"] = len(response_text)
        evidence["response_text_sent_chars"] = len(truncated_response_text)
        evidence["response_text_truncated"] = len(truncated_response_text) < len(response_text)
        evidence["response_text_truncation_strategy"] = (
            "head_and_tail" if evidence["response_text_truncated"] else "full"
        )
        return evidence

    @staticmethod
    def _truncate_response_text(response_text: str) -> str:
        max_response_chars = int(os.getenv("STORECLAW_JUDGE_MAX_RESPONSE_CHARS", "50000"))
        if len(response_text) <= max_response_chars:
            return response_text
        marker = f"\n<truncated middle {len(response_text) - max_response_chars} chars>\n"
        if max_response_chars <= len(marker) + 200:
            return response_text[-max_response_chars:]
        head_size = min(2000, max_response_chars // 4)
        tail_size = max_response_chars - head_size - len(marker)
        return response_text[:head_size] + marker + response_text[-tail_size:]

    def _message_content(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AssertionError(f"judge response missing choices: {self._redact(payload)}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise AssertionError(f"judge response missing message: {self._redact(payload)}")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AssertionError(f"judge response missing content: {self._redact(payload)}")
        return content.strip()

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                raise AssertionError(f"judge response is not JSON: {content!r}")
            payload = json.loads(match.group(0))
        if not isinstance(payload, dict):
            raise AssertionError(f"judge response must be a JSON object: {content!r}")
        return payload

    def _normalize_result(self, payload: dict[str, Any], pass_score: int, raw_response: str) -> JudgeResult:
        dimension_scores = self._dict(payload.get("dimension_scores"))
        dimension_total = self._dimension_total_score(dimension_scores)
        raw_score = dimension_total if dimension_total is not None else payload.get("score", 0)
        score = int(float(raw_score))
        score = max(0, min(100, score))
        passed = bool(payload.get("passed", score >= pass_score))
        return JudgeResult(
            enabled=True,
            model=self.model,
            score=score,
            passed=passed,
            reason=str(payload.get("reason") or "").strip(),
            strengths=self._string_list(payload.get("strengths")),
            issues=self._string_list(payload.get("issues")),
            dimension_scores=dimension_scores,
            expected_behavior=str(payload.get("expected_behavior") or "").strip(),
            actual_behavior=str(payload.get("actual_behavior") or "").strip(),
            raw_response=raw_response,
        )

    @staticmethod
    def _dimension_total_score(dimension_scores: dict[str, Any]) -> int | None:
        if not dimension_scores:
            return None
        total = 0.0
        scored = False
        for item in dimension_scores.values():
            if not isinstance(item, dict):
                continue
            value = item.get("score")
            if isinstance(value, bool):
                continue
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue
            scored = True
        if not scored:
            return None
        return int(round(total))

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _redact(payload: Any) -> Any:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        text = re.sub(r"(sk-[A-Za-z0-9_-]{8,})", "***REDACTED***", text)
        return json.loads(text)
