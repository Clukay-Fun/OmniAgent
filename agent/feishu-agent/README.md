# Feishu Agent

飞书私聊案件助手服务，负责接收飞书事件回调、识别意图、调用业务技能，并返回可读结果。

统一流程（部署前/备案中/上线后）见：`../../docs/deploy/three-stage-guide.md`

---

## 📋 功能概览

- ✅ 飞书消息回调处理（私聊为主）
- ✅ 案件/文档查询（通过 MCP 服务调用）
- ✅ 查询结果摘要汇总
- ✅ 提醒创建、列表、完成、删除、定时推送
- ✅ 用户身份识别与"我的案件"过滤
- ✅ 会话上下文与用户记忆管理
- ✅ Prometheus 指标输出
- ✅ 配置热更新（skills/prompts）
- ✅ 本地技能市场动态加载

---

## 🏗️ 架构图

```mermaid
flowchart LR
    Feishu[Feishu 用户/客户端] --> Webhook[Webhook /feishu/webhook]
    Webhook --> Orchestrator[AgentOrchestrator]
    Orchestrator --> Intent[IntentParser]
    Orchestrator --> Router[SkillRouter]
    Router --> Query[QuerySkill]
    Router --> Summary[SummarySkill]
    Router --> Reminder[ReminderSkill]
    Router --> Chitchat[ChitchatSkill]
    Query --> MCP[MCP Feishu Server]
    Summary --> LLM[LLM Client]
    Chitchat --> LLM
    Reminder --> DB[(PostgreSQL)]
    Scheduler[ReminderScheduler] --> DB
    Scheduler --> FeishuAPI[Feishu API 发送]
    Orchestrator --> FeishuAPI
```

## 📊 数据流图

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as 飞书
    participant W as Webhook
    participant O as Orchestrator
    participant I as IntentParser
    participant R as SkillRouter
    participant S as Skill
    participant M as MCP/DB/LLM

    U->>F: 发送消息
    F->>W: 事件回调
    W->>O: 标准化请求
    O->>I: 意图解析
    I-->>O: IntentResult
    O->>R: 路由技能
    R->>S: 执行技能
    S->>M: 调用外部能力
    M-->>S: 返回结果
    S-->>O: SkillResult
    O->>F: 发送回复
```

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 准备配置

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

### 3. 配置环境变量

```env
# 飞书机器人凭证
FEISHU_BOT_APP_ID=cli_xxx
FEISHU_BOT_APP_SECRET=xxx
FEISHU_BOT_VERIFICATION_TOKEN=xxx
FEISHU_BOT_ENCRYPT_KEY=       # 可选

# LLM 配置
LLM_API_KEY=sk-xxx
LLM_MODEL=internlm/internlm2_5-7b-chat
LLM_API_BASE=https://api.siliconflow.cn/v1

# MCP Server 地址
MCP_SERVER_BASE=http://localhost:8081

# PostgreSQL 数据库（提醒功能需要）
POSTGRES_DSN=postgresql://user:pass@localhost:5432/omniagent

# 用户身份管理
USER_IDENTITY_AUTO_MATCH=false
```

### 4. 启动服务

```bash
# 统一开发入口（推荐，从仓库任意目录都可执行）
python run_dev.py

# 本地单服务模式（仅 Agent）
python run_server.py
```

默认端口：
- `run_dev.py`（统一开发入口）走 Docker，Agent 暴露 `8080`
- `run_server.py`（本地单服务模式）监听 `8088`
- 如需 ngrok 测试回调：统一开发入口用 `ngrok http 8080`，单服务模式用 `ngrok http 8088`

双组织说明：
- Agent 仅使用组织B机器人凭证（`FEISHU_BOT_*`）
- 数据查询通过 MCP Server（组织A数据凭证）完成

### 5. 运行场景回归

> 说明：`tests/` 目录默认按本地开发产物管理（仓库默认忽略），如需回归请在本地保留测试文件。

```bash
python tests/scenarios/runner.py
```

包含 docs 投影校验（读取仓库根目录 `docs/scenarios/scenarios.yaml`）：

```bash
python tests/scenarios/runner.py --docs-file ../../docs/scenarios/scenarios.yaml
```

阈值门禁示例（适合 CI）：

```bash
python tests/scenarios/runner.py \
  --min-planner-pass 20 \
  --min-l0-pass 4 \
  --min-docs-pass 40 \
  --min-guard-pass 10 \
  --min-behavior-pass 2 \
  --max-docs-skip 25
```

Runner 当前包含：
- Planner 回归（`tests/scenarios/*.test.yaml`）
- L0 规则回归（`tests/scenarios/l0.test.yaml`）
- Docs 场景投影校验（从 `../../docs/scenarios/scenarios.yaml` 自动抽取可映射场景）
- Error/Security 守卫校验（批量删除拦截、注入类输入、空输入等）
- Skill 行为回归（当前包含 Reminder 的时间澄清/过去时间校验）

本地单服务启动前自动执行场景回归（可选）：

```bash
# Windows PowerShell
$env:AGENT_SCENARIO_CHECK="1"
python run_server.py

# Linux/macOS
AGENT_SCENARIO_CHECK=1 python run_server.py
```

可选：通过 `AGENT_SCENARIO_CHECK_ARGS` 传入阈值参数：

```bash
# PowerShell
$env:AGENT_SCENARIO_CHECK="1"
$env:AGENT_SCENARIO_CHECK_ARGS="--min-planner-pass 20 --min-docs-pass 40 --max-docs-skip 25"
python run_server.py

# Linux/macOS
AGENT_SCENARIO_CHECK=1 \
AGENT_SCENARIO_CHECK_ARGS="--min-planner-pass 20 --min-docs-pass 40 --max-docs-skip 25" \
python run_server.py
```

说明：
- Planner 场景用例：`tests/scenarios/*.test.yaml`
- L0 规则用例：`tests/scenarios/l0.test.yaml`
- 场景规则配置：`config/scenarios/*.yaml`

---

## 🎯 技能系统

| 技能 | 功能 | 触发关键词 |
|------|------|------------|
| **QuerySkill** | 案件/文档查询 | 查、找、搜索、案件、开庭 |
| **SummarySkill** | 汇总查询结果 | 总结、汇总、概括 |
| **ReminderSkill** | 提醒管理 | 提醒、记得、别忘了 |
| **ChitchatSkill** | 问候/帮助/闲聊 | 你好、谢谢、帮助 |
| **CreateSkill** | 创建案件 | 新增、新建、创建 |
| **UpdateSkill** | 更新案件 | 更新、修改、编辑 |
| **DeleteSkill** | 删除案件 | 删除、删掉、移除 |

---

## 📡 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/feishu/webhook` | POST | 飞书事件回调 |
| `/health` | GET | 健康检查 |
| `/metrics` | GET | Prometheus 指标 |

---

## 📁 核心模块

### 入口与路由

- **`src/main.py`** - FastAPI 入口，注册路由和调度器
- **`src/api/webhook.py`** - 飞书事件回调处理（验证/解密/去重）

### 编排与意图

- **`src/core/orchestrator.py`** - 主流程编排器
- **`src/core/l0/engine.py`** - L0 硬规则层（确认/取消/分页/空输入）
- **`src/core/planner/*`** - L1 规划层（单次 LLM + Schema 校验 + 降级）
- **`src/core/intent/parser.py`** - 意图解析（规则优先 + LLM 兜底）
- **`src/core/intent/rules.py`** - 日期类查询规则
- **`src/core/state/*`** - 会话状态管理（Memory + TTL，可替换 Redis）

### 技能系统

- **`src/core/router/router.py`** - 技能路由与链式执行
- **`src/core/skills/query.py`** - 案件查询
- **`src/core/skills/summary.py`** - 结果汇总
- **`src/core/skills/reminder.py`** - 提醒 CRUD
- **`src/core/skills/chitchat.py`** - 闲聊与问候

### 用户身份

- **`src/user/manager.py`** - 用户身份管理
- **`src/user/__init__.py`** - UserProfile 数据模型

### 提醒调度与数据库

- **`src/jobs/reminder_scheduler.py`** - APScheduler 定时扫描
- **`src/db/postgres.py`** - Reminder 表 CRUD

### 记忆与向量检索

- **`src/core/memory/manager.py`** - 用户记忆、日志记录
- **`src/vector/*`** - Chroma 存储（可选启用）

### 监控与工具

- **`src/utils/metrics.py`** - Prometheus 指标
- **`src/utils/feishu_api.py`** - 飞书消息发送

---

## ⚙️ 配置文件说明

### config/skills.yaml

```yaml
intent:
  thresholds:
    direct_execute: 0.5
    llm_confirm: 0.3
  llm_timeout: 10

planner:
  enabled: true
  confidence_threshold: 0.65
  scenarios_dir: config/scenarios

query:
  keywords: [查, 找, 搜索, 案件, 开庭]
  time_keywords: [今天, 明天, 本周]

table_aliases:
  案件项目总库: ["案件", "项目", "案子", "我的案件"]
  招投标台账: ["招投标", "投标"]

table_recognition:
  confidence_threshold: 0.65
  auto_confirm_threshold: 0.85
  max_candidates: 3
```

### config/prompts.yaml

```yaml
intent_parser:
  system: |
    你是一个意图分类器。根据用户输入，判断最匹配的技能。

summary:
  system: |
    你是一个专业的律师助理。请根据以下案件查询结果，生成简洁的摘要。
```

---

## 📊 监控指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `feishu_agent_requests_total` | Counter | 请求总数 |
| `feishu_agent_skill_executions_total` | Counter | 技能执行次数 |
| `feishu_agent_skill_execution_duration_seconds` | Histogram | 技能执行延迟 |
| `feishu_agent_intent_parse_duration_seconds` | Histogram | 意图解析延迟 |
| `feishu_agent_llm_calls_total` | Counter | LLM 调用次数 |

---

## 🐛 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Webhook 无响应 | 验签失败 | 检查 `FEISHU_BOT_VERIFICATION_TOKEN` |
| 重复回复 | 去重失效 | 检查 `message_id` 去重逻辑 |
| "我的案件"无结果 | 用户不在主办律师 | 确认表格中设置了当前用户 |
| LLM 超时 | 模型响应慢 | 调整 `llm_timeout` 或更换模型 |

---

## 🗃️ Workspace 目录

```
workspace/
├── SOUL.md              # 人格准则
├── IDENTITY.md          # 对外身份
├── MEMORY.md            # 团队共享记忆
└── users/{open_id}/     # 用户隔离记忆
    ├── memory.md        # 用户长期记忆
    └── logs/            # 每日对话日志
```

首次运行会自动创建上述文件与目录。

---

## 📄 License

MIT License
