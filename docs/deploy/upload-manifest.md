# 上传清单（备案通过后）

本文档用于打包上传到云服务器时快速核对文件范围。

## 必传目录与文件

### MCP 服务

- `mcp/mcp-feishu-server/src/`
- `mcp/mcp-feishu-server/requirements.txt`
- `mcp/mcp-feishu-server/run_server.py`
- `mcp/mcp-feishu-server/config.yaml.example`（上传后复制为 `config.yaml`）
- `mcp/mcp-feishu-server/.env.example`（参考模板）
- `mcp/mcp-feishu-server/automation_rules.yaml`

### Agent 服务

- `agent/feishu-agent/src/`
- `agent/feishu-agent/config/`
- `agent/feishu-agent/requirements.txt`
- `agent/feishu-agent/run_server.py`
- `agent/feishu-agent/config.yaml.example`（上传后复制为 `config.yaml`）
- `agent/feishu-agent/.env.example`（参考模板）

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
  - `mcp/mcp-feishu-server/automation_data/*`
  - `agent/feishu-agent/workspace/*`
- 本地调试脚本（按需）：`tools/dev/*`
- 测试目录（生产可不上传）：`mcp/mcp-feishu-server/tests/`、`agent/feishu-agent/tests/`

## 上传后立即执行

1. 在服务器中手工创建并填写服务 `.env`
2. 由模板生成 `config.yaml`
3. 执行健康检查：`/health`、`/mcp/tools`
4. 验证飞书回调路径是否可达
