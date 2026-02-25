# Project Context

本仓库以 `apps/agent-host` 为会话编排主服务，以 `integrations/feishu-mcp-server` 为数据与自动化能力服务。

## Dependency Layers

- `requirements.txt`（根：聚合安装）
- `apps/agent-host/requirements.txt`（Agent 运行依赖）
- `integrations/feishu-mcp-server/requirements.txt`（MCP 运行依赖）

## Service Roles

- `apps/agent-host`：处理入站消息、技能路由、响应渲染，核心文本输出统一经 `ResponseRenderer`。
- `integrations/feishu-mcp-server`：提供飞书数据访问、规则自动化与外部工具接口。

## Entry Convention

- 开发入口统一使用仓库根目录 `run_dev.py`。
- 模块文档以根 README 和部署文档为主，避免重复来源。
