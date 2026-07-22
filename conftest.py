from __future__ import annotations

import html
import json
import os
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
        **_judge_summary(items),
    }


def _judge_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    scored = []
    default_pass_score = _default_judge_pass_score()
    for item in items:
        judge = _item_judge(item)
        score = _number(judge.get("score"))
        if score is None:
            continue
        pass_score = _number(judge.get("pass_score"))
        scored.append(
            {
                "score": score,
                "pass_score": pass_score if pass_score is not None else default_pass_score,
                "passed": judge.get("passed") is not False,
            }
        )

    judged_total = len(scored)
    judged_passed = sum(1 for item in scored if item["score"] >= item["pass_score"] and item["passed"])
    judged_needs_optimization = judged_total - judged_passed
    average_score = round(sum(item["score"] for item in scored) / judged_total, 2) if judged_total else None
    min_score = round(min(item["score"] for item in scored), 2) if judged_total else None
    max_score = round(max(item["score"] for item in scored), 2) if judged_total else None
    return {
        "judge_default_pass_score": default_pass_score,
        "judge_scored": judged_total,
        "judge_unscored": max(len(items) - judged_total, 0),
        "judge_average_score": average_score,
        "judge_passed": judged_passed,
        "judge_needs_optimization": judged_needs_optimization,
        "judge_min_score": min_score,
        "judge_max_score": max_score,
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
    judge_metrics = _html_judge_metrics(summary)
    chart_panel = _html_chart_panel(summary, items)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>StoreClaw 自动化测试报告</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f6fb;
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
      --warn: #b54708;
      --warn-bg: #fff7ed;
      --accent: #2563eb;
      --accent-soft: #dbeafe;
      --shadow: 0 10px 30px rgba(21, 32, 55, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
      line-height: 1.55;
    }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 32px 24px 48px; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-start;
      margin-bottom: 22px;
      padding: 24px;
      background: linear-gradient(135deg, #ffffff 0%, #f8fbff 100%);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
    }}
    h1 {{ margin: 0 0 8px; font-size: 30px; line-height: 1.2; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; letter-spacing: 0; }}
    .meta {{ color: var(--muted); display: flex; flex-wrap: wrap; gap: 10px 18px; }}
    .headline-score {{
      display: grid;
      justify-items: end;
      gap: 4px;
      min-width: 160px;
    }}
    .headline-score span {{ color: var(--muted); font-size: 13px; font-weight: 700; }}
    .headline-score b {{ font-size: 34px; line-height: 1; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
      border: 1px solid var(--line);
      background: #ffffff;
    }}
    .passed {{ color: var(--ok); background: var(--ok-bg); }}
    .failed {{ color: var(--bad); background: var(--bad-bg); }}
    .skipped {{ color: var(--skip); background: var(--skip-bg); }}
    .needs-optimization {{ color: var(--warn); background: var(--warn-bg); }}
    .score-good {{ color: var(--ok); background: var(--ok-bg); }}
    .score-bad {{ color: var(--bad); background: var(--bad-bg); }}
    .score-empty {{ color: var(--muted); background: #f8fafc; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 12px;
      margin: 22px 0;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 14px 16px;
      min-height: 92px;
      box-shadow: 0 6px 18px rgba(21, 32, 55, .04);
    }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .metric b {{ display: block; margin-top: 6px; font-size: 26px; line-height: 1.1; }}
    .quality {{
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 12px;
      margin: 0 0 22px;
    }}
    .quality .metric {{ min-height: 86px; }}
    .dashboard {{
      display: grid;
      grid-template-columns: minmax(260px, .75fr) minmax(360px, 1.25fr);
      gap: 14px;
      margin: 0 0 22px;
    }}
    .chart-panel {{
      min-height: 280px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px 18px 18px;
      box-shadow: var(--shadow);
    }}
    .chart-title {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      margin-bottom: 12px;
    }}
    .chart-title b {{ font-size: 15px; }}
    .chart-title span {{ color: var(--muted); font-size: 12px; }}
    .chart-scroll {{ overflow-x: auto; overflow-y: hidden; padding-bottom: 4px; }}
    .chart-box {{ height: 220px; }}
    .score-chart-box {{ min-width: var(--score-chart-width, 640px); }}
    .chart-box canvas {{ width: 100% !important; height: 100% !important; }}
    .case-list {{ display: grid; gap: 14px; }}
    .info {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 22px;
      box-shadow: 0 6px 18px rgba(21, 32, 55, .04);
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
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 8px 24px rgba(21, 32, 55, .05);
    }}
    .case.failed {{ border-color: #f3b4ae; }}
    .case.skipped {{ border-color: #f1d28a; }}
    .case > summary {{
      list-style: none;
      cursor: pointer;
      color: inherit;
      font-weight: inherit;
    }}
    .case > summary::-webkit-details-marker {{ display: none; }}
    .case-head {{
      display: grid;
      grid-template-columns: auto minmax(220px, 1fr) auto auto auto;
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
    .dimension-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin: 2px 0 14px;
    }}
    .dimension-card {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 11px 12px;
      background: #ffffff;
      min-width: 0;
    }}
    .dimension-card h3 {{
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0;
    }}
    .dimension-score {{
      display: flex;
      align-items: baseline;
      gap: 5px;
      margin-top: 6px;
      color: var(--ink);
      font-weight: 800;
    }}
    .dimension-score b {{ font-size: 22px; line-height: 1; }}
    .dimension-score span {{ color: var(--muted); font-size: 12px; }}
    .dimension-reason {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}
    .dimension-card.not-applicable {{ background: #f8fafc; }}
    .dimension-card.not-applicable .dimension-score b {{ color: var(--muted); }}
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
    .judge-detail {{
      display: grid;
      gap: 12px;
      margin-top: 10px;
    }}
    .judge-section {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #ffffff;
    }}
    .judge-section-title {{
      padding: 8px 12px;
      color: var(--ink);
      font-size: 13px;
      font-weight: 800;
      background: var(--soft);
      border-bottom: 1px solid var(--line);
    }}
    .judge-section pre {{
      border: 0;
      border-radius: 0;
      background: #ffffff;
    }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 900px) {{
      main {{ padding: 24px 14px 36px; }}
      header {{ display: block; padding: 18px; }}
      .headline-score {{ justify-items: start; margin-top: 14px; }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .quality {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .dashboard {{ grid-template-columns: 1fr; }}
      .info-grid {{ grid-template-columns: 1fr; }}
      .case-head {{ grid-template-columns: auto 1fr; }}
      .kv {{ grid-template-columns: 1fr; }}
      .dimension-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>StoreClaw LUI 接口自动化测试报告</h1>
        <div class="meta">
          <span>生成时间：{html.escape(summary["generated_at"])}</span>
          <span>执行结果：<span class="badge {status_class}">{status}</span></span>
          <span>Exit status：{summary["exitstatus"]}</span>
        </div>
      </div>
      <div class="headline-score">
        <span>Agent Judge 平均分</span>
        <b>{_display_score(summary.get("judge_average_score"))}</b>
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

    {judge_metrics}

    {chart_panel}

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
  {_chart_script(summary, items)}
</body>
</html>
"""


def _html_judge_metrics(summary: dict[str, Any]) -> str:
    if not summary.get("judge_scored"):
        return ""
    return f"""<section class="quality" aria-label="Agent Judge 质量评分">
      <div class="metric"><span>全局平均分</span><b>{_display_score(summary.get("judge_average_score"))}</b></div>
      <div class="metric"><span>默认及格线</span><b>{_display_score(summary.get("judge_default_pass_score"))}</b></div>
      <div class="metric"><span>已评分</span><b>{summary["judge_scored"]}</b></div>
      <div class="metric"><span>达标</span><b>{summary["judge_passed"]}</b></div>
      <div class="metric"><span>需优化 / 未评分</span><b>{summary["judge_needs_optimization"]} / {summary.get("judge_unscored", 0)}</b></div>
      <div class="metric"><span>已评分最低 / 最高</span><b>{_display_score(summary.get("judge_min_score"))} / {_display_score(summary.get("judge_max_score"))}</b></div>
    </section>"""


def _html_chart_panel(summary: dict[str, Any], items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    chart_data = _chart_data(summary, items)
    score_panel = ""
    if summary.get("judge_scored"):
        score_panel = """<section class="chart-panel" aria-label="Agent Judge 分数区间分布">
      <div class="chart-title"><b>评分区间分布</b><span>按分数段统计</span></div>
      <div class="chart-box"><canvas id="scoreBucketChart"></canvas></div>
    </section>"""
        if chart_data["lowScores"]["labels"]:
            score_panel += """<section class="chart-panel" aria-label="Agent Judge 不达标用例">
      <div class="chart-title"><b>不达标用例 Top 20</b><span>优先优化</span></div>
      <div class="chart-box"><canvas id="lowScoreChart"></canvas></div>
    </section>"""
    return f"""<section class="dashboard" aria-label="测试图表">
    <section class="chart-panel" aria-label="执行结果分布">
      <div class="chart-title"><b>执行结果分布</b><span>通过 / 失败 / 跳过</span></div>
      <div class="chart-box"><canvas id="outcomeChart"></canvas></div>
    </section>
    {score_panel}
  </section>"""


def _chart_script(summary: dict[str, Any], items: list[dict[str, Any]]) -> str:
    data = _chart_data(summary, items)
    payload = json.dumps(data, ensure_ascii=False)
    return f"""<script>
    window.storeclawReportData = {payload};
    (function () {{
      const data = window.storeclawReportData;
      if (!window.Chart || !data) {{
        return;
      }}
      const textColor = "#172033";
      const mutedColor = "#667085";
      Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, sans-serif";
      Chart.defaults.color = mutedColor;

      const outcomeCanvas = document.getElementById("outcomeChart");
      if (outcomeCanvas) {{
        new Chart(outcomeCanvas, {{
          type: "doughnut",
          data: {{
            labels: ["通过", "失败", "跳过"],
            datasets: [{{
              data: [data.outcomes.passed, data.outcomes.failed, data.outcomes.skipped],
              backgroundColor: ["#12b76a", "#f04438", "#f79009"],
              borderColor: "#ffffff",
              borderWidth: 4,
              hoverOffset: 6
            }}]
          }},
          options: {{
            responsive: true,
            maintainAspectRatio: false,
            cutout: "62%",
            plugins: {{
              legend: {{ position: "bottom", labels: {{ boxWidth: 12, color: textColor }} }},
              tooltip: {{ callbacks: {{ label: (ctx) => `${{ctx.label}}: ${{ctx.parsed}} 条` }} }}
            }}
          }}
        }});
      }}

      const scoreBucketCanvas = document.getElementById("scoreBucketChart");
      if (scoreBucketCanvas && data.scoreBuckets.labels.length) {{
        new Chart(scoreBucketCanvas, {{
          type: "bar",
          data: {{
            labels: data.scoreBuckets.labels,
            datasets: [{{
              label: "用例数",
              data: data.scoreBuckets.values,
              backgroundColor: ["#f04438", "#f79009", "#fdb022", "#12b76a", "#039855", "#027a48"],
              borderRadius: 6,
              maxBarThickness: 42
            }}]
          }},
          options: {{
            responsive: true,
            maintainAspectRatio: false,
            scales: {{
              y: {{ beginAtZero: true, ticks: {{ precision: 0 }}, grid: {{ color: "#eef2f7" }} }},
              x: {{ grid: {{ display: false }}, ticks: {{ maxRotation: 0, autoSkip: false }} }}
            }},
            plugins: {{
              legend: {{ display: false }},
              tooltip: {{ callbacks: {{ label: (ctx) => `${{ctx.parsed.y}} 条` }} }}
            }}
          }}
        }});
      }}

      const lowScoreCanvas = document.getElementById("lowScoreChart");
      if (lowScoreCanvas && data.lowScores.labels.length) {{
        new Chart(lowScoreCanvas, {{
          type: "bar",
          data: {{
            labels: data.lowScores.shortLabels,
            datasets: [{{
              label: "得分",
              data: data.lowScores.values,
              backgroundColor: data.lowScores.values.map((score) => score >= data.lowScores.passScore ? "#12b76a" : "#f04438"),
              borderRadius: 6,
              maxBarThickness: 28
            }}]
          }},
          options: {{
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            scales: {{
              x: {{ min: 0, max: 100, grid: {{ color: "#eef2f7" }} }},
              y: {{ grid: {{ display: false }} }}
            }},
            plugins: {{
              legend: {{ display: false }},
              tooltip: {{
                callbacks: {{
                  title: (items) => data.lowScores.labels[items[0].dataIndex] || "",
                  label: (ctx) => `得分: ${{ctx.parsed.x}}`
                }}
              }}
            }}
          }}
        }});
      }}
    }})();
  </script>"""


def _chart_data(summary: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    scored_items = []
    bucket_defs = [
        ("0-59", 0, 59.999),
        ("60-79", 60, 79.999),
        ("80-89", 80, 89.999),
        ("90-94", 90, 94.999),
        ("95-99", 95, 99.999),
        ("100", 100, 100),
    ]
    bucket_values = [0 for _ in bucket_defs]
    for item in items:
        judge = _item_judge(item)
        score = _number(judge.get("score"))
        if score is None:
            continue
        name = str(item.get("name") or "case")
        pass_score = _number(judge.get("pass_score")) or _default_judge_pass_score()
        scored_items.append(
            {
                "name": name,
                "score": score,
                "pass_score": pass_score,
                "passed": judge.get("passed") is not False,
            }
        )
        for index, (_, start, end) in enumerate(bucket_defs):
            if start <= score <= end:
                bucket_values[index] += 1
                break

    low_score_items = sorted(
        (item for item in scored_items if item["score"] < item["pass_score"] or item["passed"] is False),
        key=lambda item: (item["score"], item["name"]),
    )[:20]

    return {
        "outcomes": {
            "passed": summary.get("passed", 0),
            "failed": summary.get("failed", 0),
            "skipped": summary.get("skipped", 0),
        },
        "scoreBuckets": {
            "labels": [label for label, _, _ in bucket_defs],
            "values": bucket_values,
        },
        "lowScores": {
            "labels": [item["name"] for item in low_score_items],
            "shortLabels": [_short_case_label(item["name"], index) for index, item in enumerate(low_score_items, start=1)],
            "values": [item["score"] for item in low_score_items],
            "passScore": _default_judge_pass_score(),
        },
    }


def _short_case_label(name: str, index: int, max_chars: int = 24) -> str:
    label = f"#{index} {name}"
    if len(label) <= max_chars:
        return label
    return label[: max_chars - 1] + "…"


def _html_case_card(index: int, item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    response_text = str(details.get("response_text") or "")
    tool_names = details.get("tool_names") if isinstance(details.get("tool_names"), list) else []
    skill_names = details.get("skill_names") if isinstance(details.get("skill_names"), list) else []
    event_names = details.get("event_names") if isinstance(details.get("event_names"), list) else []
    judge = details.get("judge") if isinstance(details.get("judge"), dict) else {}
    judge_input = details.get("judge_input") if isinstance(details.get("judge_input"), dict) else {}
    score_badge = _judge_score_badge(judge)
    score_status_badge = _judge_status_badge(judge)
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
        _kv("Agent 评分", _judge_score(judge.get("score")) if judge else ""),
        _kv("Agent 通过", _yes_no(judge.get("passed")) if judge else ""),
        _kv("会话链", _chain(details.get("chain_index"), details.get("chain_position"))),
        _kv("请求超时", _seconds(details.get("timeout_seconds"))),
    ]
    kv_html = "\n      ".join(part for part in kv_items if part)
    open_attr = " open" if item.get("outcome") == "failed" else ""

    return f"""<details class="case {html.escape(str(item.get("outcome", "")))}"{open_attr}>
  <summary class="case-head">
    <span class="case-toggle" aria-hidden="true">›</span>
    <div>
      <div class="case-title">#{index} {html.escape(item["name"])}</div>
      <div class="case-node">{html.escape(item["nodeid"])}</div>
    </div>
    <span class="badge {html.escape(item["outcome"])}">{_outcome_label(item["outcome"])}</span>
    {score_badge}
    {score_status_badge}
    <span class="badge">{item["duration_seconds"]}s</span>
  </summary>
  <div class="case-body">
    <div class="kv">
      {kv_html}
    </div>
    {_dimension_scores_block(judge_input, judge)}
    {_text_block("Prompt", details.get("prompt"))}
    {_agent_response_block("Agent 回复", response_text)}
    {_text_block("命中 Skill", ", ".join(str(name) for name in skill_names) if skill_names else "")}
    {_text_block("工具调用", ", ".join(str(name) for name in tool_names) if tool_names else "")}
    {_text_block("SSE 事件类型", ", ".join(str(name) for name in event_names) if event_names else "")}
    {_judge_detail_block(judge_input, judge)}
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


def _dimension_scores_block(judge_input: dict[str, Any], judge: dict[str, Any]) -> str:
    criteria = _judge_input_criteria(judge_input)
    dimension_scores = judge.get("dimension_scores") if isinstance(judge.get("dimension_scores"), dict) else {}
    if not criteria and not dimension_scores:
        return ""

    cards = []
    for key in _dimension_order(criteria, dimension_scores):
        criterion = criteria.get(key) if isinstance(criteria.get(key), dict) else {}
        score_item = dimension_scores.get(key) if isinstance(dimension_scores.get(key), dict) else {}
        cards.append(_dimension_card(key, criterion, score_item))
    return f"""<div class="block">
  <div class="block-title">维度评分</div>
  <div class="dimension-grid">
    {"".join(cards)}
  </div>
</div>"""


def _judge_input_criteria(judge_input: dict[str, Any]) -> dict[str, Any]:
    request = judge_input.get("request") if isinstance(judge_input.get("request"), dict) else {}
    criteria = request.get("scoring_criteria") if isinstance(request.get("scoring_criteria"), dict) else {}
    return criteria


def _dimension_order(criteria: dict[str, Any], scores: dict[str, Any]) -> list[str]:
    preferred = ["intent_fulfillment", "tool_correctness", "skill_correctness", "clarity"]
    keys = [key for key in preferred if key in criteria or key in scores]
    keys.extend(key for key in sorted(set(criteria) | set(scores)) if key not in keys)
    return keys


def _dimension_card(key: str, criterion: dict[str, Any], score_item: dict[str, Any]) -> str:
    label = str(criterion.get("label") or _dimension_label(key))
    not_applicable = bool(criterion.get("not_applicable"))
    weight = _number(score_item.get("weight"))
    if weight is None:
        weight = _number(criterion.get("effective_weight"))
    if weight is None:
        weight = _number(criterion.get("weight"))
    score = _number(score_item.get("score"))
    reason = str(score_item.get("reason") or "").strip()
    if not reason:
        reason = "待下次执行生成逐项评分" if not score_item else "无"
    css_class = "dimension-card not-applicable" if not_applicable else "dimension-card"
    score_text = "-" if score is None else _display_score(score)
    weight_text = "-" if weight is None else _display_score(weight)
    return f"""<section class="{css_class}">
  <h3>{html.escape(label)}</h3>
  <div class="dimension-score"><b>{html.escape(score_text)}</b><span>/ {html.escape(weight_text)}</span></div>
  <div class="dimension-reason">{html.escape(reason)}</div>
</section>"""


def _dimension_label(key: str) -> str:
    return {
        "intent_fulfillment": "满足用户核心意图",
        "tool_correctness": "工具调用正确性",
        "skill_correctness": "Skill 命中正确性",
        "clarity": "输出结果表达清晰",
    }.get(key, key)


def _judge_detail_block(judge_input: dict[str, Any], judge: dict[str, Any]) -> str:
    sections = []
    if judge_input:
        sections.append(_judge_section("Input", judge_input))
    if judge:
        sections.append(_judge_section("Output", judge))
    if not sections:
        return ""
    return f"""<details>
  <summary>Agent 评分详情</summary>
  <div class="judge-detail">
    {"".join(sections)}
  </div>
</details>"""


def _judge_section(title: str, content: Any) -> str:
    return f"""<section class="judge-section">
  <div class="judge-section-title">{html.escape(title)}</div>
  <pre>{html.escape(_format_json(content))}</pre>
</section>"""


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


def _item_judge(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    judge = details.get("judge") if isinstance(details.get("judge"), dict) else {}
    return judge


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _display_score(value: Any) -> str:
    score = _number(value)
    if score is None:
        return "-"
    if score.is_integer():
        return str(int(score))
    return str(round(score, 2))


def _default_judge_pass_score() -> int:
    raw_value = os.getenv("STORECLAW_JUDGE_PASS_SCORE", "80")
    try:
        score = int(raw_value)
    except (TypeError, ValueError):
        return 80
    if score < 0 or score > 100:
        return 80
    return score


def _judge_score(score: Any) -> str:
    if score is None:
        return ""
    return f"得分 {_display_score(score)}"


def _judge_score_badge(judge: dict[str, Any]) -> str:
    score = _number(judge.get("score"))
    if score is None:
        return """<span class="badge score-empty">未评分</span>""" if judge else ""
    pass_score = _number(judge.get("pass_score")) or _default_judge_pass_score()
    score_class = "score-good" if score >= pass_score else "score-bad"
    return (
        f"""<span class="badge {score_class}">"""
        f"""得分 {html.escape(_display_score(score))}</span>"""
    )


def _judge_status_badge(judge: dict[str, Any]) -> str:
    score = _number(judge.get("score"))
    if score is None:
        return ""
    pass_score = _number(judge.get("pass_score")) or _default_judge_pass_score()
    if score >= pass_score and judge.get("passed") is not False:
        return """<span class="badge score-good">达标</span>"""
    return """<span class="badge needs-optimization">需优化</span>"""


def _chain(chain_index: Any, chain_position: Any) -> str:
    if chain_index is None:
        return ""
    if chain_position is None:
        return f"第 {chain_index} 条"
    return f"第 {chain_index} 条 / 第 {chain_position} 步"
