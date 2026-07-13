from __future__ import annotations

import html
import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest


REPORTS_DIR = Path("reports")
JSON_REPORT = REPORTS_DIR / "latest_report.json"
HTML_REPORT = REPORTS_DIR / "latest_report.html"


def pytest_configure(config: pytest.Config) -> None:
    config._storeclaw_report_items = []  # type: ignore[attr-defined]
    config._storeclaw_case_report_items = []  # type: ignore[attr-defined]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    config = item.config
    report_items = config._storeclaw_report_items  # type: ignore[attr-defined]
    report_items.append(
        {
            "name": item.name,
            "nodeid": item.nodeid,
            "outcome": report.outcome,
            "duration_seconds": round(report.duration, 3),
            "longrepr": str(report.longrepr) if report.failed else "",
            "stdout": getattr(report, "capstdout", ""),
            "stderr": getattr(report, "capstderr", ""),
            "logs": getattr(report, "caplog", ""),
        }
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if session.config.option.collectonly:
        return

    case_items = session.config._storeclaw_case_report_items  # type: ignore[attr-defined]
    items = case_items or session.config._storeclaw_report_items  # type: ignore[attr-defined]
    summary = _summary(items, exitstatus)
    metadata = _metadata(session)

    REPORTS_DIR.mkdir(exist_ok=True)
    JSON_REPORT.write_text(
        json.dumps({"summary": summary, "metadata": metadata, "tests": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    HTML_REPORT.write_text(_html_report(summary, metadata, items), encoding="utf-8")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    terminalreporter.write_sep("=", "StoreClaw test reports")
    terminalreporter.write_line(f"HTML: {HTML_REPORT}")
    terminalreporter.write_line(f"JSON: {JSON_REPORT}")


def _summary(items: list[dict[str, Any]], exitstatus: int) -> dict[str, Any]:
    total = len(items)
    passed = sum(1 for item in items if item["outcome"] == "passed")
    failed = sum(1 for item in items if item["outcome"] == "failed")
    skipped = sum(1 for item in items if item["outcome"] == "skipped")
    duration = round(sum(item["duration_seconds"] for item in items), 3)
    pass_rate = round((passed / total) * 100, 2) if total else 0
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "exitstatus": exitstatus,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_seconds": duration,
        "pass_rate": pass_rate,
    }


def _metadata(session: pytest.Session) -> dict[str, Any]:
    invocation_args = list(session.config.invocation_params.args)
    return {
        "report_version": "2.0",
        "rootpath": str(session.config.rootpath),
        "pytest_args": invocation_args,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _html_report(summary: dict[str, Any], metadata: dict[str, Any], items: list[dict[str, Any]]) -> str:
    cards = "\n".join(_html_case_card(index, item) for index, item in enumerate(items, start=1))
    status = "通过" if summary["failed"] == 0 and summary["exitstatus"] == 0 else "失败"
    status_class = "passed" if status == "通过" else "failed"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>StoreClaw 自动化测试报告</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9e0ea;
      --soft: #eef3f8;
      --ok: #087f5b;
      --ok-bg: #e7f7ef;
      --bad: #b42318;
      --bad-bg: #fee4e2;
      --skip: #9a6700;
      --skip-bg: #fff4d6;
      --accent: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
      line-height: 1.55;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 24px 48px; }}
    header {{ margin-bottom: 22px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; line-height: 1.2; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; letter-spacing: 0; }}
    .meta {{ color: var(--muted); display: flex; flex-wrap: wrap; gap: 10px 18px; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
      border: 1px solid var(--line);
      background: #ffffff;
    }}
    .passed {{ color: var(--ok); background: var(--ok-bg); }}
    .failed {{ color: var(--bad); background: var(--bad-bg); }}
    .skipped {{ color: var(--skip); background: var(--skip-bg); }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 12px;
      margin: 22px 0;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
      min-height: 92px;
    }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .metric b {{ display: block; margin-top: 6px; font-size: 26px; line-height: 1.1; }}
    .case-list {{ display: grid; gap: 14px; }}
    .info {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 22px;
    }}
    .info-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 16px;
    }}
    .info-grid div {{ min-width: 0; }}
    .info-grid span {{ display: block; color: var(--muted); font-size: 12px; }}
    .info-grid b {{ display: block; margin-top: 2px; word-break: break-word; font-size: 13px; }}
    .case {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .case > summary {{
      list-style: none;
      cursor: pointer;
      color: inherit;
      font-weight: inherit;
    }}
    .case > summary::-webkit-details-marker {{ display: none; }}
    .case-head {{
      display: grid;
      grid-template-columns: auto minmax(220px, 1fr) auto auto;
      gap: 12px;
      align-items: center;
      padding: 16px 18px;
      background: #fbfcfe;
    }}
    .case[open] .case-head {{ border-bottom: 1px solid var(--line); }}
    .case-toggle {{
      display: inline-grid;
      place-items: center;
      width: 24px;
      height: 24px;
      border: 1px solid var(--line);
      border-radius: 50%;
      color: var(--muted);
      background: #ffffff;
      font-size: 15px;
      line-height: 1;
      transition: transform .15s ease;
    }}
    .case[open] .case-toggle {{ transform: rotate(90deg); }}
    .case-title {{ font-weight: 800; font-size: 16px; word-break: break-word; }}
    .case-node {{ margin-top: 3px; color: var(--muted); font-size: 12px; word-break: break-all; }}
    .case-body {{ padding: 16px 18px 18px; }}
    .kv {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .kv div {{ background: var(--soft); border-radius: 8px; padding: 9px 10px; min-width: 0; }}
    .kv span {{ display: block; color: var(--muted); font-size: 12px; }}
    .kv b {{ display: block; margin-top: 2px; font-size: 13px; word-break: break-word; }}
    .block {{ margin-top: 12px; }}
    .block-title {{ margin-bottom: 6px; color: var(--muted); font-size: 13px; font-weight: 700; }}
    .text-box, pre {{
      margin: 0;
      width: 100%;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .response-view {{
      display: grid;
      gap: 8px;
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
    }}
    .response-step,
    .response-line {{
      overflow-wrap: anywhere;
      border: 1px solid #e4eaf2;
      border-radius: 6px;
      background: #ffffff;
      padding: 8px 10px;
      font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .response-step {{
      color: #344054;
      background: #f1f5f9;
    }}
    .response-command {{
      border: 1px solid #cfd8e6;
      border-radius: 6px;
      overflow: hidden;
      background: #ffffff;
    }}
    .response-command-label {{
      padding: 6px 10px;
      border-bottom: 1px solid #e4eaf2;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      background: #eef3f8;
    }}
    .response-command pre {{
      border: 0;
      border-radius: 0;
      background: #ffffff;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    details {{ margin-top: 10px; }}
    details > summary:not(.case-head) {{ cursor: pointer; color: var(--accent); font-weight: 700; }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 900px) {{
      main {{ padding: 24px 14px 36px; }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .info-grid {{ grid-template-columns: 1fr; }}
      .case-head {{ grid-template-columns: auto 1fr; }}
      .kv {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>StoreClaw LUI 接口自动化测试报告</h1>
      <div class="meta">
        <span>生成时间：{html.escape(summary["generated_at"])}</span>
        <span>执行结果：<span class="badge {status_class}">{status}</span></span>
        <span>Exit status：{summary["exitstatus"]}</span>
      </div>
    </header>

    <section class="summary" aria-label="测试摘要">
      <div class="metric"><span>总用例</span><b>{summary["total"]}</b></div>
      <div class="metric"><span>通过</span><b>{summary["passed"]}</b></div>
      <div class="metric"><span>失败</span><b>{summary["failed"]}</b></div>
      <div class="metric"><span>跳过</span><b>{summary["skipped"]}</b></div>
      <div class="metric"><span>通过率</span><b>{summary["pass_rate"]}%</b></div>
      <div class="metric"><span>总耗时</span><b>{summary["duration_seconds"]}s</b></div>
    </section>

    <section class="info" aria-label="运行信息">
      <div class="info-grid">
        {_info("报告版本", metadata.get("report_version"))}
        {_info("项目根目录", metadata.get("rootpath"))}
        {_info("Pytest 参数", " ".join(str(arg) for arg in metadata.get("pytest_args", [])))}
        {_info("Python", metadata.get("python_version"))}
        {_info("运行平台", metadata.get("platform"))}
      </div>
    </section>

    <h2>用例明细</h2>
    <section class="case-list">
      {cards}
    </section>
  </main>
</body>
</html>
"""


def _html_case_card(index: int, item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    response_text = str(details.get("response_text") or "")
    tool_names = details.get("tool_names") if isinstance(details.get("tool_names"), list) else []
    skill_names = details.get("skill_names") if isinstance(details.get("skill_names"), list) else []
    event_names = details.get("event_names") if isinstance(details.get("event_names"), list) else []
    kv_items = [
        _kv("Session ID", details.get("session_id")),
        _kv("继续上文", _yes_no(details.get("continue_previous"))),
        _kv("Run ID", details.get("latest_run_id")),
        _kv("Run 状态", details.get("latest_run_status")),
        _kv("Run 耗时", _seconds(details.get("latest_run_duration_seconds"))),
        _kv("SSE 事件数", details.get("events_count")),
        _kv("RunCompleted", _yes_no(details.get("run_completed_event_received"))),
        _kv("Runs 数量", details.get("runs_count")),
        _kv("Runs 轮询", details.get("runs_poll_attempts")),
        _kv("断言结果", _yes_no(details.get("assertions_passed"))),
        _kv("会话链", _chain(details.get("chain_index"), details.get("chain_position"))),
        _kv("请求超时", _seconds(details.get("timeout_seconds"))),
    ]
    kv_html = "\n      ".join(part for part in kv_items if part)
    open_attr = " open" if item.get("outcome") == "failed" else ""

    return f"""<details class="case"{open_attr}>
  <summary class="case-head">
    <span class="case-toggle" aria-hidden="true">›</span>
    <div>
      <div class="case-title">#{index} {html.escape(item["name"])}</div>
      <div class="case-node">{html.escape(item["nodeid"])}</div>
    </div>
    <span class="badge {html.escape(item["outcome"])}">{_outcome_label(item["outcome"])}</span>
    <span class="badge">{item["duration_seconds"]}s</span>
  </summary>
  <div class="case-body">
    <div class="kv">
      {kv_html}
    </div>
    {_text_block("Prompt", details.get("prompt"))}
    {_agent_response_block("Agent 回复", response_text)}
    {_text_block("命中 Skill", ", ".join(str(name) for name in skill_names) if skill_names else "")}
    {_text_block("工具调用", ", ".join(str(name) for name in tool_names) if tool_names else "")}
    {_text_block("SSE 事件类型", ", ".join(str(name) for name in event_names) if event_names else "")}
    {_details_block("断言配置", _format_json(details.get("assertions")))}
    {_details_block("执行日志", item.get("logs", ""))}
    {_details_block("失败信息", item.get("longrepr", ""))}
    {_details_block("标准输出", item.get("stdout", ""))}
    {_details_block("标准错误", item.get("stderr", ""))}
  </div>
</details>"""


def _kv(label: str, value: Any) -> str:
    text = _display(value)
    if not text:
        return ""
    return f"""<div><span>{html.escape(label)}</span><b>{html.escape(text)}</b></div>"""


def _info(label: str, value: Any) -> str:
    return f"""<div><span>{html.escape(label)}</span><b>{html.escape(_display(value) or "无")}</b></div>"""


def _text_block(title: str, content: Any) -> str:
    text = _display(content)
    if not text:
        text = "无"
    return f"""<div class="block">
  <div class="block-title">{html.escape(title)}</div>
  <div class="text-box">{html.escape(text)}</div>
</div>"""


def _agent_response_block(title: str, content: Any) -> str:
    text = _display(content)
    if not text:
        return _text_block(title, "")

    parts = _agent_response_parts(text)
    body = "\n".join(_agent_response_part_html(part) for part in parts)
    return f"""<div class="block">
  <div class="block-title">{html.escape(title)}</div>
  <div class="response-view">{body}</div>
</div>"""


def _agent_response_parts(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    parts: list[dict[str, str]] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if buffer:
            parts.append({"type": "text", "text": _compact_response_text(buffer)})
            buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_completed_line(line):
            flush_buffer()
            parts.append({"type": "step", "text": line})
            index += 1
            continue
        if line == "bash" and index + 2 < len(lines) and lines[index + 1] == "-c":
            flush_buffer()
            parts.append({"type": "command", "label": "bash -c", "text": _format_shell_command(lines[index + 2])})
            index += 3
            continue
        if _looks_like_json(line):
            flush_buffer()
            parts.append({"type": "command", "label": "JSON", "text": _pretty_json(line)})
            index += 1
            continue
        buffer.append(line)
        index += 1

    flush_buffer()
    return parts


def _agent_response_part_html(part: dict[str, str]) -> str:
    text = html.escape(part["text"])
    if part["type"] == "step":
        return f"""<div class="response-step">{text}</div>"""
    if part["type"] == "command":
        label = html.escape(part.get("label", "命令"))
        return f"""<div class="response-command">
  <div class="response-command-label">{label}</div>
  <pre>{text}</pre>
</div>"""
    return f"""<div class="response-line">{text}</div>"""


def _is_completed_line(line: str) -> bool:
    return bool(re.search(r"\bcompleted in \d+(?:\.\d+)?s\.?$", line))


def _looks_like_json(line: str) -> bool:
    return (line.startswith("{") and line.endswith("}")) or (line.startswith("[") and line.endswith("]"))


def _pretty_json(line: str) -> str:
    try:
        return json.dumps(json.loads(line), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return line


def _format_shell_command(command: str) -> str:
    return re.sub(r"\s+(-H|-d|--data|--data-raw)\s+", r"\n  \1 ", command)


def _compact_response_text(lines: list[str]) -> str:
    compacted: list[str] = []
    current = ""
    for line in lines:
        if not current:
            current = line
            continue
        if _should_join_response_line(current, line):
            current += _response_join_separator(current, line) + line
        else:
            compacted.append(current)
            current = line
    if current:
        compacted.append(current)
    return "\n".join(compacted)


def _should_join_response_line(previous: str, line: str) -> bool:
    if len(previous) > 80 or len(line) > 40:
        return False
    if not re.search(r"[\u4e00-\u9fff]", previous + line):
        return False
    cjk_or_punctuation = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9 ，。！？、：；（）()%-]+$")
    return bool(cjk_or_punctuation.match(previous) and cjk_or_punctuation.match(line))


def _response_join_separator(previous: str, line: str) -> str:
    if not previous or not line:
        return ""
    if previous[-1].isspace() or line[0].isspace():
        return ""
    previous_is_ascii = previous[-1].isascii() and previous[-1].isalnum()
    line_is_ascii = line[0].isascii() and line[0].isalnum()
    if previous_is_ascii != line_is_ascii:
        return " "
    return ""


def _details_block(title: str, content: Any) -> str:
    text = _display(content)
    if not text:
        return ""
    return f"""<details>
  <summary>{html.escape(title)}</summary>
  <pre>{html.escape(text)}</pre>
</details>"""


def _format_json(value: Any) -> str:
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return _format_json(value)
    return str(value)


def _outcome_label(outcome: str) -> str:
    return {
        "passed": "通过",
        "failed": "失败",
        "skipped": "跳过",
    }.get(outcome, outcome)


def _yes_no(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return ""


def _seconds(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{round(float(value), 3)}s"
    return ""


def _chain(chain_index: Any, chain_position: Any) -> str:
    if chain_index is None:
        return ""
    if chain_position is None:
        return f"第 {chain_index} 条"
    return f"第 {chain_index} 条 / 第 {chain_position} 步"
