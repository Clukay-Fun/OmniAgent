# OmniAgent

智能 Agent 系统，面向律师事务所场景，具备 **Skill 技能系统**、**Soul 人格系统**、**Memory 记忆系统**，并与飞书深度集成。

## 项目目标

- 构建类似 OpenClaw 的智能 Agent 框架
- 支持技能插件化、人格定制与持久记忆
- 作为律师事务所智能助手服务飞书私聊

## 核心特性

| 特性 | 说明 |
|------|------|
| **Skill 技能系统** | 规则优先 + LLM 兜底，支持链式调用 |
| **Soul 人格系统** | `SOUL.md` + `IDENTITY.md`，60s 热更新 |
| **Memory 记忆系统** | 分层记忆（共享 + 用户隔离），自动裁剪 |
| **飞书集成** | Webhook 接入 + MCP 工具访问多维表格 |
| **提醒系统** | Phase 1 存取，Phase 2 定时推送 |

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户 (飞书)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Feishu Webhook                                    │
│                         (接收消息 / 发送回复)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AGENT 容器                                      │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                           Soul 人格系统                               │   │
│  │                    SOUL.md + IDENTITY.md (60s 热更新)                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         Memory 记忆系统                               │   │
│  │           共享记忆 (MEMORY.md) + 用户记忆 (users/{id}/)               │   │
│  │                    2天上下文 + 2000 token 裁剪                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         Skill Manager                                 │   │
│  │   IntentParser ──▶ SkillRouter ──▶ SkillChain (max_hops=2)           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  QuerySkill │ SummarySkill │ ReminderSkill │ ChitchatSkill           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
            │                   │                   │
            ▼                   ▼                   ▼
     MCP Server           LLM Client           PostgreSQL
   (feishu.bitable)                           (reminders)
```

## 请求流程示例

```
用户: "帮我总结今天开庭的案子"
           │
           ▼
   ┌───────────────┐
   │  Soul 注入    │ ← 读取 SOUL.md + IDENTITY.md
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ Memory 加载   │ ← 读取共享记忆 + 用户记忆 + 最近 2 天日志
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ IntentParser  │ ← 规则命中: Query(0.85) + Summary(0.80)
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ SkillChain    │ ← [QuerySkill, SummarySkill]
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ QuerySkill    │ ← 调用 MCP，写入 context.last_result
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ SummarySkill  │ ← 调用 LLM 生成摘要
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ Memory 写入   │ ← 记录到每日日志
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ 返回飞书      │
   └───────────────┘
```

---

## 目录结构

```
OmniAgent/
├── agent/feishu-agent/
│   ├── src/
│   │   ├── api/
│   │   ├── core/
│   │   │   ├── orchestrator.py
│   │   │   ├── soul/
│   │   │   │   └── soul.py
│   │   │   ├── memory/
│   │   │   │   └── manager.py
│   │   │   ├── intent/
│   │   │   │   ├── parser.py
│   │   │   │   └── rules.py
│   │   │   ├── router/
│   │   │   │   └── router.py
│   │   │   └── skills/
│   │   │       ├── base.py
│   │   │       ├── query.py
│   │   │       ├── summary.py
│   │   │       ├── reminder.py
│   │   │       └── chitchat.py
│   │   ├── llm/
│   │   ├── mcp/
│   │   └── utils/
│   ├── workspace/
│   │   ├── SOUL.md
│   │   ├── IDENTITY.md
│   │   ├── MEMORY.md
│   │   └── users/{open_id}/
│   │       ├── memory.md
│   │       └── daily/
│   ├── config/
│   │   ├── skills.yaml
│   │   └── prompts.yaml
│   └── tests/
├── mcp/mcp-feishu-server/
├── docs/
├── DEV_PLAN.md
├── TASK.md
└── docker-compose.yml
```

---

## 核心接口

```python
@dataclass
class IntentResult:
    top_skills: List[SkillMatch]
    is_chain: bool = False
    requires_llm_confirm: bool = False

@dataclass
class SkillResult:
    success: bool
    data: Any = None
    message: str = ""
    next_skill: Optional[str] = None
    confidence: Optional[float] = None
    citations: Optional[List[str]] = None

@dataclass
class Context:
    user_id: str
    message: str
    soul_prompt: str
    shared_memory: str
    user_memory: str
    recent_logs: str
    last_result: Optional[Any] = None
    last_skill: Optional[str] = None
```

---

## Workspace

- `agent/feishu-agent/workspace/SOUL.md`：人格准则
- `agent/feishu-agent/workspace/IDENTITY.md`：对外身份
- `agent/feishu-agent/workspace/MEMORY.md`：团队共享记忆
- `agent/feishu-agent/workspace/users/{open_id}/`：用户隔离记忆

首次运行会自动创建上述文件与目录。

---

## 配置文件

### skills.yaml

```yaml
intent:
  thresholds:
    direct_execute: 0.7
    llm_confirm: 0.4
  llm_timeout: 10

query:
  keywords: [查, 找, 搜索, 案件, 开庭]

summary:
  keywords: [总结, 汇总, 概括]
  default_fields: [案号, 案由, 当事人, 开庭日, 主办律师]

reminder:
  keywords: [提醒, 记得, 别忘了]

chain:
  triggers:
    - pattern: "(查|找).*(总结|汇总)"
      skills: [QuerySkill, SummarySkill]
  max_hops: 2
```

### prompts.yaml

```yaml
intent_parser:
  system: |
    你是一个意图分类器。根据用户输入，判断最匹配的技能。

summary:
  system: |
    你是一个专业的律师助理。请根据以下案件查询结果，生成简洁的摘要。
```

---

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 基础框架 | ✅ 完成 |
| Phase 2 | Soul 人格系统 | ✅ 完成 |
| Phase 3 | Memory 记忆系统 | ✅ 完成 |
| Phase 4 | Skill 系统完善 | ⏳ 待开始 |
| Phase 5 | 集成与编排 | ⏳ 待开始 |
| Phase 6 | 测试与监控 | ⏳ 待开始 |

详细任务进度见 `TASK.md`。

---

## 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL 14+（Reminder 需要）
- 飞书开放平台应用（机器人能力）

### 启动 MCP Server

```bash
cd mcp/mcp-feishu-server
pip install -r requirements.txt
uvicorn src.main:app --port 8081
```

### 启动 Feishu Agent

```bash
cd agent/feishu-agent
pip install -r requirements.txt
uvicorn src.main:app --port 8080
```

---

## 数据库

```sql
CREATE TABLE reminders (
    id              SERIAL PRIMARY KEY,
    user_id         VARCHAR(64) NOT NULL,
    content         TEXT NOT NULL,
    due_at          TIMESTAMP,
    priority        VARCHAR(16) DEFAULT 'medium',
    status          VARCHAR(16) DEFAULT 'pending',
    created_at      TIMESTAMP DEFAULT NOW()
);
```
