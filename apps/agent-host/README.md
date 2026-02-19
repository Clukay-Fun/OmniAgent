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
- ✅ **多模型路由**（任务模型 + 对话模型分离）
- ✅ **人格化回复**（模板随机池 + 时间感知问候 + 柔性拒绝）
- ✅ **回复模板外置**（`config/responses.yaml` 集中管理）

## 🆕 近期开发进展（2026-02）

- ✅ 对话上下文主键收敛为 `open_id`（私聊场景多用户隔离、同用户多设备连续）
- ✅ 状态槽位增强：`active_table` / `active_record` / `pending_action` / `last_result_ids`
- ✅ 单表 CRUD 多轮闭环：创建补字段、确认/取消、更新目标定位、删除二次确认
- ✅ L0 指代执行增强：支持“第N个/这个/那条”直接驱动更新与删除
- ✅ 多表联动能力上线（当前默认启用 `case_to_contract`）
- ✅ 子表失败补录机制上线：主表成功保留，子表失败进入对话补录重试
- ✅ 多表离线回归脚本：`python ../../tools/dev/verify_multitable_linker.py`

---

## 🏗️ 架构图

```mermaid
flowchart LR
    Feishu[Feishu 用户/客户端] --> Webhook[Webhook /feishu/webhook]
    Webhook --> Orchestrator[AgentOrchestrator]
    Orchestrator --> Intent[IntentParser]
    Orchestrator --> Planner[PlannerEngine]
    Orchestrator --> Router[SkillRouter]
    Router --> Query[QuerySkill]
    Router --> Summary[SummarySkill]
    Router --> Reminder[ReminderSkill]
    Router --> Chitchat[ChitchatSkill]
    Intent --> TaskLLM["Task LLM\n(MiniMax M2.5)"]
    Planner --> TaskLLM
    Query --> TaskLLM
    Query --> MCP[MCP Feishu Server]
    Summary --> ChatLLM["Chat LLM\n(Qwen3-8B)"]
    Chitchat --> ChatLLM
    Reminder --> DB[(PostgreSQL)]
    Scheduler[ReminderScheduler] --> DB
    Scheduler --> FeishuAPI[Feishu API 发送]
    Orchestrator --> FeishuAPI

    style TaskLLM fill:#e8d44d,color:#000
    style ChatLLM fill:#4da6e8,color:#fff
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

## 🧠 多模型路由

采用**单 Agent + 多模型**策略，按任务类型路由到不同 LLM，兼顾准确率和成本：

| 模型角色 | 模型 | 用途 | 计费 |
|----------|------|------|------|
| **Task LLM** | MiniMax M2.5 | 意图识别、工具参数提取、表名匹配 | 按量付费 |
| **Chat LLM** | Qwen3-8B | 闲聊、摘要、自由对话 | 免费 |

路由逻辑（`orchestrator.py`）：
- `IntentParser` → Task LLM
- `PlannerEngine` → Task LLM
- `QuerySkill._llm_pick_table` → Task LLM
- `ChitchatSkill` / `SummarySkill` → Chat LLM

启用方式：在 `config.yaml` 中配置 `task_llm` 段或设置环境变量：

```env
TASK_LLM_ENABLED=true
TASK_LLM_MODEL=MiniMax-M2.5
TASK_LLM_API_KEY=your-api-key
TASK_LLM_API_BASE=https://api.minimax.chat/v1
```

> 未启用时所有环节共享主 LLM，行为不变（零影响）。

## 🎭 人格化回复

通过**模板随机池 + 时间感知**提升交互自然度：

| 特性 | 覆盖范围 |
|------|----------|
| 随机池 | 每种回复类型 5-6 条变体，`random.choice` 选取 |
| 时间感知 | 早间 / 晚间自动切换问候语风格 |
| 柔性拒绝 | 离题请求用轻松语气引导 |
| 查询开场白 | 结果前随机加"查到啦~"等 |
| 空结果软化 | 多种温和提示轮替 |
| CRUD 操作 | 创建/更新/删除成功均用随机回复 |
| 错误/超时 | 失败提示多条变体轮替 |

所有回复模板集中在 **`config/responses.yaml`**，修改文案无需改代码。
技能通过共享 `ResponsePool` 单例（`src/core/skills/response_pool.py`）访问模板。

## 💡 自动偏好记忆

根据对话自动提取用户偏好，有偏好信号时才触发，**零 LLM 成本**：

```
用户消息 → 关键词信号检测（零成本）→ 规则提取偏好 → 写入 memory.md
```

| 用户说 | 自动提取 |
|--------|----------|
| "太长了"、"简单点" | `[偏好] 偏好简洁回复` |
| "详细点"、"展开说" | `[偏好] 偏好详细回复` |
| "别加emoji" | `[偏好] 不喜欢 emoji` |
| "不用确认" | `[偏好] 希望跳过二次确认` |

偏好自动写入 `workspace/users/{open_id}/memory.md`，下次对话时 LLM 会读取并调整风格。

---

## 🚀 快速开始

说明：`run_dev.py（根目录权威实现）`，本目录 `run_dev.py` 仅作为代理入口。

### 1. 安装依赖

```bash
# 全仓开发（推荐，在仓库根目录执行）
# pip install -r requirements.txt

# 仅 Agent 单服务依赖（在当前目录执行）
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
REMINDER_SCHEDULER_ENABLED=false

# 用户身份管理
USER_IDENTITY_AUTO_MATCH=false
```

说明：
- 默认建议 `REMINDER_SCHEDULER_ENABLED=false`（开发态减少轮询噪音）
- 只有在同时满足 `REMINDER_SCHEDULER_ENABLED=true` 且配置 `POSTGRES_DSN` 时，Reminder 轮询才会启动

### 4. 启动服务

```bash
# 统一开发入口（推荐，当前目录）
python run_dev.py up

# 或从仓库根目录执行
# python run_dev.py up

# 一键拉起全部（含 monitoring + db）
python run_dev.py up --all

# 冲突清理（容器名/历史残留）
python run_dev.py clean

# 自动化全量补偿同步（新增 + 修改）
python run_dev.py sync

# 本地长连接模式（未备案阶段推荐）
python run_dev.py agent-ws

# 说明：sync 会对 upsert 目标表做删除对账

# 本地单服务模式（仅 Agent）
python run_server.py
```

默认端口：
- `run_dev.py`（统一开发入口）走 Docker，Agent 暴露 `8080`
- `run_server.py`（本地单服务模式）监听 `8088`
- 如需固定 `ngrok 8088`：先启动 `python ../../tools/dev/ngrok_mux.py --port 8088`，再执行 `ngrok http 8088`
  - `/feishu/webhook` -> Agent(8080)
  - `/feishu/events` -> MCP(8081)

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

多表联动离线校验（不依赖真实飞书环境）：

```bash
python ../../tools/dev/verify_multitable_linker.py
```

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
- **`src/core/skills/chitchat.py`** - 闲聊与问候（随机池 + 时间感知）
- **`src/core/skills/response_pool.py`** - 回复模板随机池单例（全 Skill 共享）

### 配置文件

- **`config/responses.yaml`** - 回复模板随机池（集中管理所有文案）

### 用户身份

- **`src/user/manager.py`** - 用户身份管理
- **`src/user/__init__.py`** - UserProfile 数据模型

### 提醒调度与数据库

- **`src/jobs/reminder_scheduler.py`** - APScheduler 定时扫描
- **`src/db/postgres.py`** - Reminder 表 CRUD

### 记忆与向量检索

- **`src/core/memory/manager.py`** - 用户记忆、日志记录、自动偏好存储
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

### config/responses.yaml

回复模板随机池（集中管理所有 Agent 回复文案）：

```yaml
# 日间通用问候
greeting:
  - "您好！有什么可以帮您的？"
  - "来啦~ 有什么事找我？"

# 早间 / 晚间问候
greeting_morning:
  - "早上好！今天有什么需要处理的吗？"
greeting_evening:
  - "晚上好！还有什么需要处理的吗？"

# 业务回复包装
result_opener:
  - "查到啦~ "
  - "找到了！"
empty_result:
  - "嗯...没找到相关记录 🤔 试试换个关键词？"
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
├── SOUL.md              # 人格准则（身份/性格/行为视则）
├── IDENTITY.md          # 对外身份
├── MEMORY.md            # 团队共享记忆
└── users/{open_id}/     # 用户隔离记忆
    ├── memory.md        # 用户长期记忆（含自动提取的 [偏好] 条目）
    └── logs/            # 每日对话日志
```

首次运行会自动创建上述文件与目录。用户偏好（如"偏好简洁回复"）会自动写入各用户的 `memory.md`。

---

## 📄 License

MIT License
