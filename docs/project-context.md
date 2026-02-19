# Project Context

本页用于让人和 AI 在 1 分钟内理解项目当前状态与关键入口。

## 项目定位

- 产品主线：个人 AI Agent（当前先通过飞书接入）
- 架构形态：单 Agent 主应用 + MCP 工具服务
- 人格命名：统一为“小敬”

## 主入口与目录

- Agent 主入口：`apps/agent-host`
- MCP 主入口：`integrations/feishu-mcp-server`
- 仓库开发入口：`run_dev.py`（唯一权威实现）
- `apps/agent-host/run_dev.py`：代理入口
- `integrations/feishu-mcp-server/run_dev.py`：代理入口

## 依赖分层

- `requirements.txt`（根：聚合安装）
- `requirements/dev.txt`（开发与测试依赖）
- `apps/agent-host/requirements.txt`（Agent 运行依赖）
- `integrations/feishu-mcp-server/requirements.txt`（MCP 运行依赖）

## 核心运行链路

1. 渠道消息进入 Agent API
2. Orchestrator 解析意图并路由技能
3. 技能调用 MCP/LLM/DB 等能力
4. `ResponseRenderer` 生成通用回复结构
5. `ChannelFormatter` 转换为渠道消息并发送

## 关键约束

- Core 层不直接依赖渠道协议细节
- 回复链路采用通用结构，支持文本 fallback
- 文档结构采用单一权威来源，避免重复介绍文档

## 日志规范（中文 + 事件码）

- 业务日志 `message` 使用中文，便于人工排障
- 日志 `extra` 统一携带 `event_code`（英文稳定码，便于检索与告警）
- 关键上下文字段保持结构化（如 `request_id`、`user_id`、`duration_ms`）

## 常用命令

```bash
python run_dev.py up
python run_dev.py agent-ws
python run_dev.py agent-ws-watch
python run_dev.py logs --follow
python run_dev.py ps
python run_dev.py down
python run_dev.py clean
python run_dev.py sync
```

本地未备案阶段建议：Agent 走 `agent-ws` 长连接，MCP 通过 `sync/scan` 手动补偿。

## 深入文档入口

- 仓库总览：`README.md`
- Agent 详情：`apps/agent-host/README.md`
- MCP 详情：`integrations/feishu-mcp-server/README.md`
- 部署流程：`docs/deploy/three-stage-guide.md`
- 场景说明：`docs/scenarios/README.md`
