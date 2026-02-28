# OmniAgent `src/` 核心代码总览

本目录 (`src/`) 是 OmniAgent（飞书/Discord 协同智能体）的绝对核心代码库。系统所有的骨架、业务逻辑、大模型交互与基础设施均扎根于此。

## 架构核心思想：Nanoclaw 的极简降维

在设计本项目的代码域时，我们严格对齐了 **OpenClaw** 的中枢调度循环（Agent Loop）框架，但又基于 **[nanoclaw](https://github.com/Nanobot)** 敏捷部署落地的理念进行了“重炮换步枪”的降级裁剪：

1. **纯粹的无状态 (Stateless Engine)**：系统彻底剥离了沉重的本地关系型数据库（如 PostgreSQL），所有的长期记忆、权限校验与业务状态（如案件、提醒任务）全部以泛型结构化的方式托管在 **飞书多维表格 (Bitable) + MCP Server** 中，或下坠为极轻量的本地不可变缓存储存。
2. **轻依赖战略 (Zero-Heavy-Dependency)**：不使用由于过度设计导致的厚重抽象底座（如 LangChain 或 LlamaIndex），摒弃了沉重的 RBAC 权限服务、Celery 调度队列，转而使用 `apscheduler` 与极其精简的 `Feishu API` 裸包，旨在用最低的运维成本换取绝对的企业级吞吐量。
3. **强领域驱动 (Domain-Driven Design)**：各目录互不僭越，解耦了模型调度层（`core`）、通信协议层（`adapters` / `api`）以及基础设施层（`utils`），并在各个字域中通过依赖注入 (DI) 的思想组装能力。

---

## 核心域地图 (Domain Map)

### 🌀 1. 系统入口与调度门面 (Entrypoints)
- **`main.py`**: **FastAPI 启动总控中心**。负责整个 Agent 生命周期的管理。在这个入口不仅挂载了 HTTP 路由器（`/webhook`），还启动了系统的非阻塞配置热更新探针 (`hot_reload.py`) 与诸如每日案件简报扫描之类的定时后台作业组。
- **`config.py`**: 一级强类型的全局配置结构装载与解析树，配合根目录的 `env` 将环境变量与内部 Pydantic Settings 打通。
- **`api/`**: **全端事件总线口**。收拢了外部投递进来的 Webhook 以及系统自带的 `Health`（健康探测）与 `Metrics`（指标搜集）路由。在这里把来自 Discord 或飞书的原生数据包转化为内部标准协议事件。

### 🧠 2. 大脑中枢与技能核心 (Core Engine & Brain)
- **`core/`**: **Agent 的最高意志中枢**。包含：
  - `orchestration/` (或主干 `orchestrator.py`)：Agent Loop 的心脏，串联多路并发认知。
  - `intent/`：用户自然语言意图分拣中心。
  - `planner/`：复杂多步骤任务的 LLM L1 级计划与执行拆解。
  - `skills/`：具体行动能力（查询表格、更新数据等核心技能）。
  - `state/` & `memory/`：基于对话上下文跟踪意图的短期工作流容器。

### 🔌 3. 边界适配与协议防腐 (Protocol & Adapter)
- **`adapters/`**: **多端协议隔离器**。在此处将纷繁复杂的飞书 UI （如富文本 Card V2）或者是 Discord 样式结构解析出来，反向适配成系统统一认识的 `Action`。
- **`infra/mcp/`**: **模型上下文协议层 (Model Context Protocol)**。让模型跳出自身计算范畴的一双眼睛和手，所有对外部飞书多维表格的“增删改查”，皆通过此处的标准化协议下放到底层的外部大库中。
  - 兼容层：保留 `src/mcp/*` 作为 re-export shim，便于历史导入平滑迁移。

### 🎭 4. 身份与认知域 (Identity & Cognition)
- **`user/`**: **敏捷身份鉴权管线**。抛弃了死板的组织架构树，通过拿到用户真实姓名后去 MCP 大表里查证羁绊字段（如：是否是某案件的主办律师），通过 **Permission by Data**（数据即权限）的理念反推身份。
- **`infra/llm/`**: **大模型交互基座**。剔除各家 API 的差异隔离，使用封装的高性能 `AsyncOpenAI` 兼容客户端，自带严格的 Token 打点防爆器与 JSON 解析防呆保护壳。
- **`infra/vector/`**: **语义记忆引擎**。剥开玄学重整机制（Rerank），直接对接轻型 `ChromaDB`，主要负责抓取和回忆“结构化表格之外”的细枝末节备忘语境。
  - 兼容层：保留 `src/llm/*` 与 `src/vector/*` 作为 re-export shim。

### ⏰ 5. 异步流管线与基础设施 (Infrastructure)
- **`jobs/`**: **纯状态化的被动后台调度池**。彻底拆除了重型 PostgreSQL 定时扫描器，所有的开庭日查询、预警探测都是按点扫描大表然后投入 `Dispatcher` 去重重定向，不再自身囤积长任务。
- **`utils/`**: **通用基建沉淀**。已按职责拆分为 `observability/`、`platform/feishu/`、`runtime/`、`parsing/`、`errors/`，用于日志/指标、平台调用、运行时工具、时间解析与异常模型。
- **`skills_market/`**: **动态算力与市集插件**。预留的插拔机制，允许除了 `core/skills` 里的基本骨肉之外，在未来快速热更与接驳外部提供的特种分析技能。

---

## 开发引导指引 (Dev Guide)

- **如果遇到新端接入**（如你想增加类似微信的机器人）：请勿在 `core` 层里动任何一个字，前往 `adapters/` 和 `api/` 下开发对应的 `channel` 并将数据流汇集入原 `Event` 机制。
- **如果遇到大模型幻觉与新模型上架**：你的第一去处是 `infra/llm/` 调整客户端兼容，然后在 `core/expression/prompt.py` 中更新格式契约 (Format Contract)。
- **如果需要撰写全新的数据库查询能力**：直接去配置你的 `MCP Server` 以及去 `core/capabilities/skills/` 封装一套新的行为算子。

一切都围绕着一句话：**在保证 OpenClaw 级别的认知与调度严密度的同时，用 nanoclaw 将每一行实施代码写得“纯净如水，免于运维”。**
