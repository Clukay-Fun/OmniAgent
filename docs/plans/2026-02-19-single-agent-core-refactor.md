# 单 Agent 内核化与目录重构 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重构为单 Agent 内核架构，飞书作为渠道适配器，保持现有业务能力稳定。

**Architecture:** 引入分层架构：`core`（业务与决策）/`adapters`（渠道与工具协议）/`infra`（技术实现）/`integrations`（外部系统）。响应链路统一为 `SkillResult -> ResponseRenderer -> OutboundMessage -> ChannelFormatter`，支持通用 rich blocks。

**Tech Stack:** Python, FastAPI, Pydantic, httpx, APScheduler, Prometheus, pytest, Docker Compose

---

### Task 1: 架构守卫与目录骨架

**Files:**
- Create: `apps/agent-host/src/core/__init__.py`
- Create: `apps/agent-host/src/adapters/__init__.py`
- Create: `apps/agent-host/src/infra/__init__.py`
- Create: `integrations/feishu-mcp-server/.keep`
- Create: `tools/ci/check_core_boundary.py`
- Test: `tests/architecture/test_core_boundary.py`

**Step 1: Write the failing test**
- 新增架构测试，断言 `core` 不得 import 飞书协议关键词（`lark_oapi`, `feishu`, `webhook`）。

**Step 2: Run test to verify it fails**
- Run: `python3 -m pytest tests/architecture/test_core_boundary.py -v`

**Step 3: Write minimal implementation**
- 用 `ast` 扫描目录，输出违规导入列表。

**Step 4: Run test to verify it passes**
- Run: `python3 -m pytest tests/architecture/test_core_boundary.py -v`

**Step 5: Commit**
- 提交 Task 1 相关文件。

---

### Task 2: 定义通用回复 IR（rich blocks）

**Files:**
- Create: `apps/agent-host/src/core/response/models.py`
- Test: `tests/core/response/test_models.py`

**Steps:**
1. 先写失败测试（`RenderedResponse` 必须有 `text_fallback`）。
2. 运行失败测试。
3. 实现最小模型（`text_fallback`, `blocks`, `meta`）。
4. 运行通过测试。
5. 提交。

---

### Task 3: 实现 core ResponseRenderer（小敬人格）

**Files:**
- Create: `apps/agent-host/src/core/response/renderer.py`
- Create: `apps/agent-host/config/identity/IDENTITY.md`
- Create: `apps/agent-host/config/identity/SOUL.md`
- Create: `apps/agent-host/config/identity/MEMORY.md`
- Modify: `apps/agent-host/config/responses.yaml`
- Test: `tests/core/response/test_renderer.py`

**Steps:**
1. 写失败测试（SkillResult -> RenderedResponse）。
2. 运行失败测试。
3. 实现最小渲染器，保证 text fallback 与 blocks 输出。
4. 运行通过测试。
5. 提交。

---

### Task 4: 实现 Feishu ChannelFormatter（只包装）

**Files:**
- Create: `apps/agent-host/src/adapters/channels/feishu/formatter.py`
- Create: `apps/agent-host/src/adapters/channels/feishu/sender.py`
- Test: `tests/adapters/feishu/test_formatter.py`

**Steps:**
1. 写失败测试（不支持卡片时自动降级文本）。
2. 运行失败测试。
3. 实现格式转换与降级逻辑。
4. 运行通过测试。
5. 提交。

---

### Task 5: Orchestrator 接入新响应链路

**Files:**
- Modify: `agent/feishu-agent/src/core/orchestrator.py`
- Modify: `agent/feishu-agent/src/api/webhook.py`
- Test: `tests/core/test_orchestrator_response_pipeline.py`

**Steps:**
1. 写失败测试（Orchestrator 输出 OutboundMessage）。
2. 运行失败测试。
3. 改造处理链路。
4. 运行通过测试。
5. 提交。

---

### Task 6: 目录迁移与入口收口

**Files:**
- Move/Create: `apps/agent-host/*`
- Move/Create: `integrations/feishu-mcp-server/*`
- Modify: `run_dev.py`
- Modify: `deploy/docker/compose.yml`
- Modify: `deploy/docker/compose.dev.yml`
- Test: `tests/smoke/test_dev_entrypoints.py`

**Steps:**
1. 写失败测试（根入口指向新主应用）。
2. 运行失败测试。
3. 迁移目录与入口。
4. 运行通过测试。
5. 提交。

---

### Task 7: 配置统一与文档收口

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/repo-layout.md`
- Modify: `docs/deploy/three-stage-guide.md`
- Test: `tests/config/test_identity_consistency.py`

**Steps:**
1. 写失败测试（统一人格名为“小敬”）。
2. 运行失败测试。
3. 更新文档与配置说明。
4. 运行通过测试。
5. 提交。

---

### Task 8: 回归与发布前验证

**Files:**
- Modify: `tools/ci/validate_scenarios.py`
- Modify: `docs/scenarios/README.md`

**Steps:**
1. 运行主链路回归（Query/Summary/Reminder/CRUD）。
2. 运行架构守卫脚本。
3. 运行健康检查与启动 smoke。
4. 修复异常并重新验证。
5. 提交最终收口。
