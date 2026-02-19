# 文档合并与导航重构设计（方案B）

## 背景

当前仓库在根 README、模块 README、模块 docs 下存在介绍类内容重复，导致两类问题：

- 人工阅读时入口多、信息重复、维护成本高。
- AI 读取上下文时需要跨多个文档拼接，容易出现口径不一致。

## 目标

- 建立“一主两辅+AI上下文”的文档结构。
- 删除重复的模块介绍文档，避免再次分叉。
- 保持部署、场景、架构文档的独立职责不变。

## 方案（已选）

采用方案B：

- `README.md` 作为仓库导航入口（总览、启动、文档索引）。
- `apps/agent-host/README.md` 作为 Agent 权威文档。
- `integrations/feishu-mcp-server/README.md` 作为 MCP 权威文档。
- 新增 `docs/project-context.md` 作为 AI 快速上下文单页。

## 删除与保留

删除（用户已确认）：

- `apps/agent-host/docs/agent-module-intro.md`
- `integrations/feishu-mcp-server/docs/mcp-module-intro.md`

保留：

- `integrations/feishu-mcp-server/docs/PROJECT_STRUCTURE.md`（结构约定）
- `docs/deploy/*`、`docs/scenarios/README.md`（流程与验证）

## 风险与约束

- 删除文档后需同步更新所有引用，避免悬挂链接。
- 不改业务代码，仅调整文档层与索引测试。

## 验收标准

- 删除文件后仓库不再引用被删路径。
- 根 README 与测试中的文档入口引用完整。
- 文档相关测试通过。
