# 单 Agent 内核化架构设计

## 背景

当前项目以飞书接入为中心，代码边界与产品定位（个人 Agent）不一致。目标是重构为单 Agent 主体架构，飞书仅为可插拔渠道之一。

## 设计目标

- 保持单 Agent，不引入多 Agent 运行时复杂度。
- Core 层不直接依赖飞书协议字段与 SDK。
- 统一响应链路：`SkillResult -> ResponseRenderer -> OutboundMessage -> ChannelFormatter`。
- 支持通用 rich blocks，V1 仅只读块（无交互按钮）。
- 保持现有 Query/Summary/Reminder/CRUD 能力不回退。

## 目标分层

- `apps/agent-host/src/core`：意图、规划、路由、技能、状态、响应渲染。
- `apps/agent-host/src/adapters/channels/feishu`：飞书协议适配与发送。
- `apps/agent-host/src/adapters/tools/mcp`：MCP 调用协议适配。
- `apps/agent-host/src/infra`：LLM、内存、DB、日志、指标、配置实现。
- `integrations/feishu-mcp-server`：飞书数据侧 MCP 服务（同仓保留，语义下沉）。

## 人格与记忆

- 统一人格名为“小敬”。
- `IDENTITY.md`：对外身份与能力边界。
- `SOUL.md`：人格行为准则。
- `MEMORY.md`：团队共享记忆。
- `workspace/users/{user_id}/memory.md`：用户私有记忆。

## 迁移策略

采用四周渐进迁移：先建边界与守卫，再迁 core，再迁 adapters/integrations，最后入口与文档收口。全程保持旧入口可回滚。

## 约束与验收

- 约束：Core 禁止直接依赖飞书协议对象。
- 验收：主流程不回退、可观测指标不退化、目录与文档一致。
