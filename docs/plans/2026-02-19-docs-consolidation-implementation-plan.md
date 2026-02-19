# 文档合并与导航重构 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 合并重复介绍文档、补充 AI 上下文单页，并完成文档导航收口。

**Architecture:** 采用“根导航 + 模块权威 README + AI context 单页”结构。删除重复模块介绍文档，保留部署/场景/结构约定文档作为独立层。通过文档测试与路径搜索保证无悬挂引用。

**Tech Stack:** Markdown, Python pytest, ripgrep

---

### Task 1: 新增 AI 快速上下文文档

**Files:**
- Create: `docs/project-context.md`
- Modify: `README.md`

**Step 1: Write the failing test**
- 在 `tests/config/test_identity_consistency.py` 新增断言：`docs/project-context.md` 存在并包含关键入口路径。

**Step 2: Run test to verify it fails**
- Run: `python3 -m pytest tests/config/test_identity_consistency.py -v`

**Step 3: Write minimal implementation**
- 新增 `docs/project-context.md`，写入定位、入口、数据流、约束、常用命令。
- 在 `README.md` 文档入口中加入该文件链接。

**Step 4: Run test to verify it passes**
- Run: `python3 -m pytest tests/config/test_identity_consistency.py -v`

**Step 5: Commit**
- `git add docs/project-context.md README.md tests/config/test_identity_consistency.py`
- `git commit -m "docs: add project context single-page for human and AI"`

---

### Task 2: 删除重复模块介绍文档并收口索引

**Files:**
- Delete: `apps/agent-host/docs/agent-module-intro.md`
- Delete: `integrations/feishu-mcp-server/docs/mcp-module-intro.md`
- Modify: `apps/agent-host/docs/README.md`
- Modify: `integrations/feishu-mcp-server/docs/PROJECT_STRUCTURE.md` (if needed for link text only)

**Step 1: Write the failing test**
- 在 `tests/smoke/test_directory_migration.py` 或新建文档测试中新增断言：被删文档不存在，且 docs 索引不再指向它们。

**Step 2: Run test to verify it fails**
- Run: `python3 -m pytest tests/smoke/test_directory_migration.py -v`

**Step 3: Write minimal implementation**
- 删除两份重复介绍文档。
- 将 `apps/agent-host/docs/README.md` 改为“历史文档已并入模块 README”。

**Step 4: Run test to verify it passes**
- Run: `python3 -m pytest tests/smoke/test_directory_migration.py -v`

**Step 5: Commit**
- `git add -A`
- `git commit -m "docs: remove duplicated module intro docs and keep single sources"`

---

### Task 3: 全局引用校验与回归

**Files:**
- Modify: `README.md`
- Modify: `docs/deploy/three-stage-guide.md` (only if broken links)
- Modify: `tests/config/test_identity_consistency.py`

**Step 1: Write the failing test**
- 新增断言：根 README 文档入口包含 `docs/project-context.md`。

**Step 2: Run test to verify it fails**
- Run: `python3 -m pytest tests/config/test_identity_consistency.py -v`

**Step 3: Write minimal implementation**
- 更新根 README 文档入口。
- 修复可能残留的引用文字。

**Step 4: Run test to verify it passes**
- Run: `python3 -m pytest tests/config/test_identity_consistency.py -v`
- Run: `python3 -m pytest tests -q`

**Step 5: Commit**
- `git add README.md docs tests`
- `git commit -m "docs: finalize documentation navigation and reference consistency"`
