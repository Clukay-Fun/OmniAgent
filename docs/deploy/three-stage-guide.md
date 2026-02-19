# 三阶段文档（部署前 / 备案中 / 上线后）

本文件是当前仓库的统一流程口径。根 README 与两个子 README 只保留服务说明，阶段流程以此为准。

统一入口说明：当前主入口为 `apps/agent-host`（Agent）与 `integrations/feishu-mcp-server`（MCP）。

## 阶段一：部署前（本地开发联调）

目标：在本地完成 MCP + Agent 功能联调，不对公网提供正式回调。

### 1) 准备配置

- 主入口模板：`integrations/feishu-mcp-server/.env.example` -> `integrations/feishu-mcp-server/.env`
- 主入口模板：`apps/agent-host/.env.example` -> `apps/agent-host/.env`
- 主入口模板：`integrations/feishu-mcp-server/config.yaml.example` -> `integrations/feishu-mcp-server/config.yaml`
- 主入口模板：`apps/agent-host/config.yaml.example` -> `apps/agent-host/config.yaml`

### 2) 统一开发入口（推荐）

```bash
python run_dev.py up
```

常用命令：

```bash
python run_dev.py logs --follow
python run_dev.py ps
python run_dev.py down
python run_dev.py clean
python run_dev.py refresh-schema
python run_dev.py sync
python run_dev.py scan --table-id tbl_xxx --app-token app_xxx

# 一键拉起全部（含 monitoring + db）
python run_dev.py up --all
```

说明：`sync` 执行全量补偿（新增+修改+删除对账）。

### 3) 本地验证

- MCP：`http://localhost:8081/health`
- Agent：`http://localhost:8080/health`（容器开发态）
- 工具列表：`http://localhost:8081/mcp/tools`

## 阶段二：备案中（冻结公网上线）

目标：保持本地可迭代，暂停公网正式发布。

- 不切换生产 DNS 回调
- 不上传正式服务器配置实值（`.env`）
- 继续本地联调与规则验证
- 产出并维护上传清单：`docs/deploy/upload-manifest.md`

建议动作：

- 每次改动后保留 `automation_rules.yaml` 版本注记
- 定期运行场景与脚本校验
- 在文档中记录待上线变更点

## 阶段三：上线后（云服务器生产）

目标：将本地通过的版本部署到云服务器并接入正式回调。

### 1) 服务启动口径

- Docker 主文件：`deploy/docker/compose.yml`
- 生产核心服务：`mcp-feishu-server` + `feishu-agent`（代码主入口分别对应 `integrations/feishu-mcp-server` 与 `apps/agent-host`）
- 可选 profile：
  - 监控：`--profile monitoring`
  - 数据库：`--profile db`

示例：

```bash
docker compose -f deploy/docker/compose.yml up -d
docker compose -f deploy/docker/compose.yml --profile monitoring up -d
```

### 2) 公网与回调

- `https://<domain>/feishu/events` -> MCP
- `https://<domain>/feishu/webhook` -> Agent
- 飞书后台验证通过后再切流量

### 3) 上线检查

完整检查项见：`docs/deploy/cloud-checklist-no-db.md`
