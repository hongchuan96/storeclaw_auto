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
- 支持按 JSON 顺序切分会话链，同一会话链串行，不同会话链并发
- 自动生成 `reports/latest_report.html` 和 `reports/latest_report.json`
- 日志自动脱敏 token、password、cookie、authorization 等敏感字段

## 目录说明

```text
.
├── clients/
│   ├── __init__.py
│   └── storeclaw_client.py        # StoreClaw 接口客户端封装
├── tests/
│   ├── data/
│   │   └── lui_cases.json         # JSON 批量测试用例
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
STORECLAW_CASES_FILE=tests/data/lui_cases.json
STORECLAW_TIMEOUT=1800
STORECLAW_MAX_CONCURRENCY=1
STORECLAW_RUNS_POLL_TIMEOUT=120
STORECLAW_RUNS_POLL_INTERVAL=5
```

配置说明：

| 变量 | 说明 |
| --- | --- |
| `STORECLAW_BASE_URL` | StoreClaw 环境地址。QA 使用 `https://www.storeclawdev.com`，线上使用 `https://www.storeclaw.ai` |
| `STORECLAW_EMAIL` | 登录账号 |
| `STORECLAW_PASSWORD` | 登录密码 |
| `STORECLAW_CASES_FILE` | JSON 用例文件路径，支持相对项目根目录，也支持绝对路径 |
| `STORECLAW_TIMEOUT` | 单个 HTTP 请求的等待超时时间，单位秒；`1800` 等于 30 分钟 |
| `STORECLAW_MAX_CONCURRENCY` | 最大并发会话链数量，范围 `1` 到 `10` |
| `STORECLAW_RUNS_POLL_TIMEOUT` | 收到 `RunCompleted` 后，如果 session runs 暂时为空，最多继续等待多久 |
| `STORECLAW_RUNS_POLL_INTERVAL` | session runs 为空时的轮询间隔，单位秒 |

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

如果 case 配置了 `"continue_previous": true`，第 3 步不会新建会话，而是复用上一条 case 执行得到的 `session_id` 继续发起 runs 请求。

## JSON 用例管理

默认用例文件：

```text
tests/data/lui_cases.json
```

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
    "contains_any": ["title", "price", "商品", "产品", "库存"],
    "not_contains": ["系统错误", "无法处理", "traceback", "Traceback"]
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
      "contains_any": ["创建成功", "商品已创建", "Test Product", "29.99", "100"],
      "not_contains": ["系统错误", "无法处理", "traceback", "Traceback"]
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
      "contains_any": ["上下文测试商品", "修改成功", "更新成功", "商品名称"],
      "not_contains": ["系统错误", "无法处理", "traceback", "Traceback"]
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
| `not_contains`         | string[] | 回复文本不能包含这些异常词                                                                         |
| `skill_called`         | string[] | 最新 run 或 SSE 内容中必须命中的 skill 名称                                                        |
| `skill_not_called`     | string[] | 最新 run 或 SSE 内容中不能命中的 skill 名称                                                        |
| `tool_called`          | string[] | 最新 run 中必须出现这些工具调用名称                                                                |
| `tool_not_called`      | string[] | 最新 run 中不能出现这些工具调用名称                                                                |

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
    "contains_all": ["OK"],
    "not_contains": ["系统错误", "无法处理", "traceback", "Traceback"]
  }
}
```

开放推理场景示例：

```json
{
  "name": "agent_open_reasoning",
  "prompt": "帮我分析一下如何提升店铺转化率，给出可执行建议",
  "session": {
    "continue_previous": false
  },
  "assertions": {
    "response_not_empty": true,
    "run_status": "COMPLETED",
    "contains_any": ["转化率", "商品", "流量", "用户", "运营"],
    "not_contains": ["系统错误", "无法处理", "traceback", "Traceback"]
  }
}
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
    "tool_called": ["run_shell_command"],
    "not_contains": ["系统错误", "无法处理", "traceback", "Traceback"]
  }
}
```

## 测试报告

每次正常运行 pytest 后会自动生成：

```text
reports/latest_report.html
reports/latest_report.json
```

报告内容包括：

- 总用例数、通过数、失败数、跳过数
- 通过率、总耗时、pytest exit status
- 报告版本、项目根目录、pytest 启动参数、Python 版本、运行平台
- 每条 JSON case 的执行结果、耗时、会话链位置
- 每条 case 的 prompt、断言配置、Agent 回复文本
- session id、run id、run 状态、run 耗时
- SSE 事件数量、是否收到 `RunCompleted`
- runs 数量、runs 轮询次数
- 命中的 skill 名称、工具调用名称、SSE 事件类型
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

也可以指向其他 JSON 文件，例如：

```env
STORECLAW_CASES_FILE=tests/data/smoke_cases.json
```

### Agent 回复不稳定怎么断言？

不要断言完整回复文本，优先用“结果状态 + 关键内容 + 异常词过滤 + skill 命中 + 工具调用”组合断言：

- `response_not_empty`：回复不能为空
- `run_status`：run 状态符合预期，例如 `COMPLETED`
- `contains_any`：至少命中一个业务关键词
- `contains_all`：必须命中全部关键内容
- `not_contains`：不能出现错误、异常、堆栈等关键词
- `skill_called`：验证 Agent 是否命中了预期 skill
- `skill_not_called`：验证 Agent 没有命中不应命中的 skill
- `tool_called`：验证 Agent 是否调用了预期工具
- `tool_not_called`：验证 Agent 没有调用不应调用的工具
- `max_duration_seconds`：可选，用于限制 run 业务耗时
