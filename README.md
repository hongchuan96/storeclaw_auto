# StoreClaw LUI 接口自动化测试

这个项目用于测试 StoreClaw AI 平台的 LUI 会话接口链路。当前实现基于 `pytest`，通过 JSON 文件管理批量用例，支持登录鉴权、创建/续用 LUI 会话、SSE runs 调用、skill/tool 命中断言、会话链并发执行，以及 HTML/JSON 测试报告生成。

## 当前能力

- 登录 StoreClaw 账号并自动获取 token
- 自动写入 `storeclaw-account-token` Cookie
- 获取当前账号最近选择的团队上下文
- 创建 LUI 会话，或继续上一条 case 的会话
- 调用 LUI runs SSE 接口发送用户提示词
- 校验 SSE 是否收到 `RunCompleted`
- 等待并轮询 session runs，降低 runs 落库延迟导致的误判
- 从 SSE 事件中提取 Agent 回复文本
- 支持响应内容、run 状态、skill 命中、工具调用、异常词过滤等断言
- 支持接入 DeepSeek/OpenAI-compatible Judge Agent，对开放预期结果进行语义评分
- 支持按 JSON 顺序切分会话链，同一会话链串行，不同会话链并发
- 自动生成 `reports/latest_report.html` 和 `reports/latest_report.json`
- 日志自动脱敏 token、password、cookie、authorization 等敏感字段

## 工作方式

项目主要由三部分组成：

- `clients/storeclaw_client.py`：负责 StoreClaw 登录、团队上下文、LUI session、runs SSE、session runs 查询等接口封装。
- `tests/test_lui_chat_flow.py`：负责读取 JSON case、拆分会话链、执行用例、做硬断言，并在需要时调用 Judge Agent。
- `conftest.py`：负责收集每条 JSON case 的执行详情，生成 HTML/JSON 报告和全局 Judge 评分统计。

单条 case 的结果由两类校验共同决定：

- 硬断言先执行：例如 run 状态、回复非空、skill/tool 命中、关键词包含等。硬断言失败时 case 直接失败。
- Judge 后执行：仅当 JSON 中配置 `assertions.judge.enabled: true` 时调用 DeepSeek/OpenAI-compatible 模型，按语义和执行证据打分。Judge 分数低于全局达标分，或模型返回 `passed: false`，case 也会失败。

硬断言适合验证确定性事实，Judge 适合验证开放式结果是否满足用户意图。两者同时存在时，必须全部通过，case 才算通过。

## 目录说明

```text
.
├── clients/
│   ├── __init__.py
│   ├── judge_client.py            # DeepSeek/OpenAI-compatible Judge Agent 客户端
│   └── storeclaw_client.py        # StoreClaw 接口客户端封装
├── tests/
│   ├── data/
│   │   ├── amazon_cases.json      # Amazon 相关 JSON 批量测试用例
│   │   └── shopify_cases.json     # Shopify 相关 JSON 批量测试用例
│   └── test_lui_chat_flow.py      # pytest 批量执行入口
├── conftest.py                    # pytest HTML/JSON 报告插件
├── pytest.ini                     # pytest 日志、marker、pythonpath 配置
├── requirements.txt               # Python 依赖
├── .env.example                   # 环境变量模板
└── reports/                       # 测试报告运行产物
```

## 环境准备

建议先创建虚拟环境：

```bash
cd storeclaw-auto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

复制环境变量模板：

```bash
cp .env.example .env
```

然后编辑本地 `.env`：

```env
STORECLAW_BASE_URL=https://www.storeclawdev.com
STORECLAW_EMAIL=你的账号
STORECLAW_PASSWORD=你的密码
STORECLAW_CASES_FILE=tests/data
STORECLAW_TIMEOUT=1800
STORECLAW_MAX_CONCURRENCY=1
STORECLAW_RUNS_POLL_TIMEOUT=120
STORECLAW_RUNS_POLL_INTERVAL=5
STORECLAW_JUDGE_BASE_URL=https://api.deepseek.com
STORECLAW_JUDGE_MODEL=deepseek-chat
STORECLAW_JUDGE_API_KEY=你的 DeepSeek API Key
STORECLAW_JUDGE_TIMEOUT=300
STORECLAW_JUDGE_PASS_SCORE=80
STORECLAW_JUDGE_FAIL_ON_ERROR=true
STORECLAW_JUDGE_MAX_RESPONSE_CHARS=50000
```

配置说明：

| 变量                                   | 说明                                                                                               |
| -------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `STORECLAW_BASE_URL`                 | StoreClaw 环境地址。QA 使用`https://www.storeclawdev.com`，线上使用 `https://www.storeclaw.ai` |
| `STORECLAW_EMAIL`                    | 登录账号                                                                                           |
| `STORECLAW_PASSWORD`                 | 登录密码                                                                                           |
| `STORECLAW_CASES_FILE`               | JSON 用例路径。可以指向单个 JSON 文件，也可以指向目录；目录模式会按文件名排序加载所有`*.json`       |
| `STORECLAW_TIMEOUT`                  | 单个 HTTP 请求的等待超时时间，单位秒；`1800` 等于 30 分钟                                        |
| `STORECLAW_MAX_CONCURRENCY`          | 最大并发会话链数量，范围`1` 到 `10`                                                            |
| `STORECLAW_RUNS_POLL_TIMEOUT`        | 收到`RunCompleted` 后，如果 session runs 暂时为空，最多继续等待多久                              |
| `STORECLAW_RUNS_POLL_INTERVAL`       | session runs 为空时的轮询间隔，单位秒                                                              |
| `STORECLAW_JUDGE_BASE_URL`           | DeepSeek/OpenAI-compatible API 地址。当前样例使用`https://api.deepseek.com`                     |
| `STORECLAW_JUDGE_MODEL`              | Judge 模型名称。当前样例使用`deepseek-chat`                                                     |
| `STORECLAW_JUDGE_API_KEY`            | Judge 模型 API Key。只放本地`.env`，不要提交                                                     |
| `STORECLAW_JUDGE_TIMEOUT`            | Judge 请求超时时间，单位秒，默认`300`，避免模型响应较慢时被过早中断                              |
| `STORECLAW_JUDGE_PASS_SCORE`         | Judge 全局达标分，默认`80`。JSON 用例里不配置单独分数，低于该分数的 case 会标记为需优化          |
| `STORECLAW_JUDGE_FAIL_ON_ERROR`      | Judge 调用失败时是否让 case 失败，默认`true`。设为`false`时只记录错误，不让 Judge 错误中断用例   |
| `STORECLAW_JUDGE_MAX_RESPONSE_CHARS` | 传给 Judge 的 StoreClaw 回复最大长度，默认`50000`。超过时保留头部证据和尾部最终回答               |

如果要跑线上环境，直接把 `.env` 里的域名改成：

```env
STORECLAW_BASE_URL=https://www.storeclaw.ai
```

`.env` 放真实账号密码，已被 `.gitignore` 忽略；`.env.example` 只保留模板，不放真实敏感信息。

## 运行测试

运行 LUI 批量用例：

```bash
python3 -m pytest tests/test_lui_chat_flow.py -q -s
```

按 marker 运行集成测试：

```bash
python3 -m pytest -m integration -q -s
```

只检查 pytest 能否正常收集测试入口：

```bash
python3 -m pytest tests/test_lui_chat_flow.py --collect-only -q
```

生成 CI 常用的 JUnit XML：

```bash
python3 -m pytest tests/test_lui_chat_flow.py -q -s --junitxml=reports/junit.xml
```

## 执行链路

每条 JSON case 会按下面链路执行：

1. `POST /api/account/login`
   使用 `.env` 里的账号密码登录，读取 token 和 account id。
2. `POST /app/api/storeclawTeam/business/v1/context/lastSelected`
   获取当前账号最近选择的团队上下文。
3. `POST /app/api/ai-agent/sessions`
   默认创建新的 LUI 会话。
4. `POST /app/api/ai-agent/sandbox/agents/runs`
   通过 SSE 流式接口发送 `prompt`。
5. 校验 SSE 事件。
   如果没有收到 `RunCompleted`，说明本次 run 没有完整结束，case 会失败。
6. 提取 Agent 回复文本。
7. `GET /app/api/ai-agent/sandbox/sessions/{session_id}/runs`
   查询 session runs。如果暂时为空，会按 `STORECLAW_RUNS_POLL_TIMEOUT` 和 `STORECLAW_RUNS_POLL_INTERVAL` 轮询。
8. 读取最新 run，并根据 JSON 中的 `assertions` 做断言。
9. 如果 case 启用了 `assertions.judge.enabled`，调用 Judge Agent 输出语义评分，并按通过分判断 case 是否通过。

如果 case 配置了 `"continue_previous": true`，第 3 步不会新建会话，而是复用上一条 case 执行得到的 `session_id` 继续发起 runs 请求。

## JSON 用例管理

默认用例目录：

```text
tests/data
```

可以把 `STORECLAW_CASES_FILE` 配置成单个 JSON 文件，也可以配置成目录：

```env
STORECLAW_CASES_FILE=tests/data/shopify_cases.json
STORECLAW_CASES_FILE=tests/data
```

目录模式会按文件名排序加载目录下所有 `*.json` 文件，并把每个文件中的 JSON 数组合并成一批 case 执行。

单条 case 示例：

```json
{
  "name": "shopify_list_products",
  "prompt": "帮我查询 Shopify 店铺最近上架的 5 个商品，展示商品标题、价格和库存状态",
  "session": {
    "continue_previous": false
  },
  "assertions": {
    "response_not_empty": true,
    "run_status": "COMPLETED",
    "skill_called": ["shopify-admin"],
    "tool_called": ["run_shell_command"],
    "contains_any": ["title", "price", "商品", "产品", "库存"]
  }
}
```

字段说明：

| 字段                          | 是否必填 | 说明                                      |
| ----------------------------- | -------- | ----------------------------------------- |
| `name`                      | 否       | 用例名称；不填时会自动生成`case_序号`   |
| `prompt`                    | 是       | 发送给 LUI Agent 的用户提示词             |
| `session.continue_previous` | 否       | 是否继续上一条 case 的会话，默认`false` |
| `assertions`                | 是       | 断言配置对象                              |

新增用例时，在 JSON 数组里追加一个对象即可。不要在 JSON 里手动写 `session_id`；续聊场景由执行器自动传递上一条 case 的会话 ID。

## 会话续聊与并发

执行器会先按 JSON 顺序把 cases 切成多条会话链：

- `continue_previous: false`：开启一条新的会话链，并创建新的 LUI session
- `continue_previous: true`：加入上一条 case 所在会话链，复用上一条 case 的 session
- 同一条会话链内部串行执行，保证上下文顺序正确
- 不同会话链之间按 `STORECLAW_MAX_CONCURRENCY` 并发执行

第一条 case 不能配置 `continue_previous: true`，因为它前面没有可复用的会话。

当前默认 JSON 中包含一组真实业务续聊场景：

```json
[
  {
    "name": "shopify_create_product_confirmation",
    "prompt": "帮我在 Shopify 创建一个新商品：标题 'Test Product'，价格 $29.99,库存 100",
    "session": {
      "continue_previous": false
    },
    "assertions": {
      "response_not_empty": true,
      "run_status": "COMPLETED",
      "skill_called": ["shopify-admin"],
      "tool_called": ["run_shell_command"],
      "contains_any": ["创建成功", "商品已创建", "Test Product", "29.99", "100"]
    }
  },
  {
    "name": "shopify_create_product_update",
    "prompt": "商品名称给我改成：上下文测试商品",
    "session": {
      "continue_previous": true
    },
    "assertions": {
      "response_not_empty": true,
      "run_status": "COMPLETED",
      "skill_called": ["shopify-admin"],
      "tool_called": ["run_shell_command"],
      "contains_any": ["上下文测试商品", "修改成功", "更新成功", "商品名称"]
    }
  }
]
```

这个例子里，第二条 case 会继续第一条 case 创建出来的会话，用来验证 Agent 是否能理解上一轮创建商品的上下文，并继续修改同一个业务目标。

## 断言规则

`assertions` 支持以下字段：

| 字段                     | 类型     | 说明                                                                                               |
| ------------------------ | -------- | -------------------------------------------------------------------------------------------------- |
| `response_not_empty`   | boolean  | Agent 回复文本不能为空，默认`true`                                                               |
| `run_status`           | string   | 最新 run 的状态，例如`COMPLETED`                                                                 |
| `max_duration_seconds` | number   | 可选。断言最新 run 的业务耗时上限，单位秒；默认用例不配置，接口等待时间统一看`STORECLAW_TIMEOUT` |
| `contains_all`         | string[] | 回复文本必须包含所有关键词                                                                         |
| `contains_any`         | string[] | 回复文本至少包含一个关键词                                                                         |
| `not_contains`         | string[] | 可选。回复文本不能包含这些关键词；默认用例不配置，避免过程性错误恢复日志误杀                      |
| `skill_called`         | string[] | 最新 run 或 SSE 内容中必须命中的 skill 名称                                                        |
| `skill_not_called`     | string[] | 最新 run 或 SSE 内容中不能命中的 skill 名称                                                        |
| `tool_called`          | string[] | 最新 run 中必须出现这些工具调用名称                                                                |
| `tool_not_called`      | string[] | 最新 run 中不能出现这些工具调用名称                                                                |
| `judge.enabled`        | boolean  | 是否为当前 case 启用 Judge Agent。配置为`true` 时会调用 Judge 模型                               |
| `judge.rubric`         | string   | 当前 case 的语义评测标准，用来告诉 Judge 什么算达成预期                                            |

固定答案场景示例：

```json
{
  "name": "reply_ok",
  "prompt": "接口自动化测试：请只回复 OK",
  "session": {
    "continue_previous": false
  },
  "assertions": {
    "response_not_empty": true,
    "run_status": "COMPLETED",
    "contains_all": ["OK"]
  }
}
```

开放推理场景示例：

```json
{
  "name": "shopify_get_orders",
  "prompt": "查询 Shopify 店铺最近 10 笔订单，显示订单号、金额和状态",
  "session": {
    "continue_previous": false
  },
  "assertions": {
    "response_not_empty": true,
    "run_status": "COMPLETED",
    "skill_called": ["shopify-admin"],
    "tool_called": ["run_shell_command"],
    "judge": {
      "enabled": true,
      "rubric": "判断回复是否完成了查询 Shopify 最近 10 笔订单并展示订单号、金额、状态的意图。若实际存在订单，应展示订单号、金额、状态；若 Shopify 返回无订单，明确说明当前无订单且没有编造订单也算满足。"
    }
  }
}
```

这类 case 不建议再写死 `contains_all: ["订单", "金额", "状态"]`。如果店铺真实没有订单，Agent 合理回复“当前无订单”也应该通过，语义判断交给 Judge Agent 更稳。

Judge Agent 固定按下面维度评分，总分 100：

| 维度 | 默认占比 | 说明 |
| --- | ---: | --- |
| 满足用户核心意图 | 50 | 是否完成用户真正要做的事 |
| 工具调用正确性 | 20 | 仅当 case 配置 `tool_called` 时适用 |
| Skill 命中正确性 | 20 | 仅当 case 配置 `skill_called` 时适用 |
| 输出结果表达清晰 | 10 | 结果是否清楚、可读、无明显误导 |

如果某条 case 没有配置 `tool_called` 或 `skill_called`，对应维度不会扣分，权重会自动并入“满足用户核心意图”。例如没有 tool 和 skill 要求时，核心意图占比会变为 90，表达清晰占比仍为 10。

Judge 入参由执行器自动组装，不需要在 JSON 里手动维护。当前会传给模型的信息包括：

| 入参字段 | 说明 |
| --- | --- |
| `case_name` | 当前 case 名称 |
| `prompt` | 用户发给 StoreClaw 的原始问题 |
| `rubric` | JSON 中配置的语义评测标准 |
| `scoring_criteria` | 代码内固定的四维评分标准、原始权重、是否适用、最终有效权重 |
| `response_text` | StoreClaw Agent 的最终回复文本；未超过 `STORECLAW_JUDGE_MAX_RESPONSE_CHARS` 时完整传递，超过时保留头部证据和尾部最终回答 |
| `response_text_original_chars` | StoreClaw Agent 原始回复字符数 |
| `response_text_sent_chars` | 实际传给 Judge 的回复字符数 |
| `response_text_truncated` | 是否发生截断 |
| `response_text_truncation_strategy` | 回复文本传递策略，`full` 表示完整传递，`head_and_tail` 表示保留头尾 |
| `run_status` | 最新 run 状态 |
| `skill_names` | 从 SSE、run、回复证据中提取到的 skill 名称 |
| `tool_names` | 最新 run 中提取到的工具调用名称 |
| `event_names` | SSE 事件类型列表 |

Judge 输出会被规范成下面结构，并写入报告：

| 输出字段 | 说明 |
| --- | --- |
| `score` | 总分，0 到 100 |
| `passed` | Judge 认为是否达标 |
| `reason` | 总体评分理由 |
| `strengths` | 做得好的点 |
| `issues` | 需要优化的问题 |
| `dimension_scores` | 四个维度的逐项得分、有效权重和理由 |
| `expected_behavior` | Judge 归纳的预期行为 |
| `actual_behavior` | Judge 归纳的实际表现 |
| `raw_response` | 模型原始 JSON 输出，便于排查评分异常 |

为了避免泄露敏感信息，报告中的 Judge 入参不会包含 `STORECLAW_JUDGE_API_KEY`。

启用 Judge Agent：

```env
STORECLAW_JUDGE_BASE_URL=https://api.deepseek.com
STORECLAW_JUDGE_MODEL=deepseek-chat
STORECLAW_JUDGE_API_KEY=你的 DeepSeek API Key
STORECLAW_JUDGE_PASS_SCORE=80
```

工具和 skill 场景示例：

```json
{
  "name": "shopify_query_products",
  "prompt": "查询 Shopify 最近 5 个商品",
  "session": {
    "continue_previous": false
  },
  "assertions": {
    "response_not_empty": true,
    "run_status": "COMPLETED",
    "skill_called": ["shopify-admin"],
    "tool_called": ["run_shell_command"]
  }
}
```

## 测试报告

每次正常运行 pytest 后会自动生成：

```text
reports/latest_report.html
reports/latest_report.json
```

HTML 报告使用 Chart.js 渲染执行结果和 Judge 评分图表；如果查看报告时网络不可用，图表可能不显示，但摘要、用例明细和 JSON 数据仍可正常查看。

报告内容包括：

- 总用例数、通过数、失败数、跳过数
- 通过率、总耗时、pytest exit status
- Agent Judge 全局平均分、默认及格线、已评分数、未评分数、达标数、需优化数、已评分最低分和最高分
- 报告版本、项目根目录、pytest 启动参数、Python 版本、运行平台
- 每条 JSON case 的执行结果、耗时、会话链位置
- 每条 case 的 prompt、断言配置、Agent 回复文本
- session id、run id、run 状态、run 耗时
- SSE 事件数量、是否收到 `RunCompleted`
- runs 数量、runs 轮询次数
- 命中的 skill 名称、工具调用名称、SSE 事件类型
- Judge Agent 的分数、达标状态、评分理由、问题列表和四维度得分
- Agent 评分详情中的 Input/Output 原始 JSON，方便核对实际传给模型的入参和模型输出
- 每条 case 的接口日志
- 失败堆栈信息

当前 pytest 外层只有一个测试函数，JSON cases 是在这个测试函数内部批量执行的。报告插件会优先使用内部收集到的 case 结果，所以 HTML/JSON 报告里看到的是每条 JSON case 的明细，而不是只有一个 pytest 函数。

`--collect-only` 只做收集检查，不会生成报告。

## 日志说明

运行时会输出接口相关日志，主要包括：

- 当前 case 名称
- 登录结果
- 团队上下文加载结果
- 创建或复用的 LUI session id
- runs SSE 返回事件数量
- 提取出的 Agent 回复文本
- session runs 轮询结果
- 失败堆栈

敏感字段会脱敏展示：

- `token`
- `password`
- `cookie`
- `authorization`
- `storeclaw-account-token`

## 常见问题

### 为什么测试被 skipped？

通常是 `.env` 缺少必要配置。可以用下面命令查看跳过原因：

```bash
python3 -m pytest tests/test_lui_chat_flow.py -q -rs
```

重点检查：

```text
STORECLAW_EMAIL
STORECLAW_PASSWORD
```

### 为什么 pytest 只收集到一个测试？

这是当前设计。`tests/test_lui_chat_flow.py` 里只有一个批量执行入口，真正的用例来自 JSON 文件。最终以 `reports/latest_report.html` 和 `reports/latest_report.json` 里的 case 明细为准。

### 如何修改并发数？

修改 `.env`：

```env
STORECLAW_MAX_CONCURRENCY=5
```

取值范围是 `1` 到 `10`。系统目前限制单账号最多 10 个会话同时进行，所以这里不会允许超过 10。

### 如何切换用例文件？

修改 `.env`：

```env
STORECLAW_CASES_FILE=tests/data/lui_cases.json
```

可以指向单个 JSON 文件，例如：

```env
STORECLAW_CASES_FILE=tests/data/shopify_cases.json
```

也可以指向目录，批量加载该目录下所有 `*.json`：

```env
STORECLAW_CASES_FILE=tests/data
```

### Agent 回复不稳定怎么断言？

不要断言完整回复文本，优先用“结果状态 + 关键内容 + skill 命中 + 工具调用 + Judge 语义评分”组合断言：

- `response_not_empty`：回复不能为空
- `run_status`：run 状态符合预期，例如 `COMPLETED`
- `contains_any`：至少命中一个业务关键词
- `contains_all`：必须命中全部关键内容
- `not_contains`：可选，默认用例不配置。只有在明确不允许某些固定词出现时再加，避免 Agent 过程性错误恢复日志造成误杀
- `skill_called`：验证 Agent 是否命中了预期 skill
- `skill_not_called`：验证 Agent 没有命中不应命中的 skill
- `tool_called`：验证 Agent 是否调用了预期工具
- `tool_not_called`：验证 Agent 没有调用不应调用的工具
- `max_duration_seconds`：可选，用于限制 run 业务耗时
- `judge`：当预期结果取决于外部实时数据或开放式回答质量时，用 rubric 让 Judge Agent 评分
