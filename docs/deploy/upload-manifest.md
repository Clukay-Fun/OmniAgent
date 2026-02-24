# 上传清单（备案通过后）

本文档用于打包上传到云服务器时快速核对文件范围。

## 必传目录与文件

### MCP 服务

- `integrations/feishu-mcp-server/src/`
- `integrations/feishu-mcp-server/requirements.txt`
- `integrations/feishu-mcp-server/run_server.py`
- `integrations/feishu-mcp-server/config.yaml.example`（上传后复制为 `config.yaml`）
- `integrations/feishu-mcp-server/.env.example`（参考模板）
- `integrations/feishu-mcp-server/automation_rules.yaml`

### Agent 服务

- `apps/agent-host/src/`
- `apps/agent-host/config/`
- `apps/agent-host/requirements.txt`
- `apps/agent-host/run_server.py`
- `apps/agent-host/config.yaml.example`（上传后复制为 `config.yaml`）
- `apps/agent-host/.env.example`（参考模板）

### 部署辅助

- `deploy/docker/compose.yml`
- `deploy/docker/compose.dev.yml`（可选）
- `deploy/monitoring/*`（可选）

监控启动方式（合并后）：

- `docker compose -f deploy/docker/compose.yml --profile monitoring up -d`

## 不上传（或不入库）

- 所有 `.env` 实值文件
- 所有 `__pycache__/`、`*.pyc`、`.pytest_cache/`
- 本地虚拟环境 `.venv/`
- 运行态数据：
  - `integrations/feishu-mcp-server/automation_data/*`
  - `apps/agent-host/workspace/*`
- 本地调试脚本（按需）：`tools/dev/*`
- 测试目录（生产可不上传）：`integrations/feishu-mcp-server/tests/`、`apps/agent-host/tests/`

## 上传后立即执行

1. 在服务器中手工创建并填写服务 `.env`
2. 由模板生成 `config.yaml`
3. 执行健康检查：`/health`、`/mcp/tools`
4. 验证飞书回调路径是否可达
