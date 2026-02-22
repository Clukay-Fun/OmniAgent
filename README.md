# OmniAgent

多模块智能 Agent 项目，当前主线是：

- `apps/agent-host`：单 Agent 主应用入口
- `integrations/feishu-mcp-server`：飞书数据侧 MCP 服务

## 当前阶段

- 云服务器与域名已准备，暂不上传生产部署（等待备案审核）
- 本仓库已完成一次目录重整：部署文件、监控文件、工具脚本、场景文档已拆分归位
- Feishu Agent 已完成上下文强化、单表 CRUD 闭环与多表联动第一版（当前默认链路：案件 -> 合同管理表）
- 开发/备案/上线统一口径见：`docs/deploy/three-stage-guide.md`

## 目录结构（已调整）

```text
OmniAgent/
├── apps/agent-host/                 # 单 Agent 主应用入口
├── integrations/feishu-mcp-server/  # 飞书数据侧 MCP 服务主入口
├── deploy/
│   ├── docker/
│   │   ├── compose.yml              # 主 compose
│   │   └── compose.dev.yml          # 开发态 compose 覆盖
│   └── monitoring/
│       ├── prometheus.yml
│       ├── run_monitoring.sh
│       ├── run_monitoring.ps1
│       └── grafana/
├── tools/
│   ├── dev/                         # 本地调试脚本
│   └── ci/                          # 校验/覆盖率脚本
└── docs/
    ├── scenarios/
    │   ├── scenarios.yaml
    │   ├── scenarios.schema.yaml
    │   └── README.md
    ├── deploy/
    │   ├── upload-manifest.md
    │   ├── cloud-checklist-no-db.md
    │   └── three-stage-guide.md
    └── architecture/
        └── repo-layout.md
```

## 本地开发（不依赖云）

命令以 `docs/deploy/three-stage-guide.md` 为准（本节仅保留高频快捷入口）。

统一开发入口（推荐）：

```bash
python run_dev.py up
```

常用操作：

```bash
python run_dev.py logs --follow
python run_dev.py ps
python run_dev.py down
python run_dev.py clean
python run_dev.py refresh-schema
python run_dev.py refresh-schema --table-id tbl_xxx --app-token app_xxx
python run_dev.py auth-health
python run_dev.py sync
python run_dev.py scan --table-id tbl_xxx --app-token app_xxx
python run_dev.py agent-ws
python run_dev.py agent-ws-watch

# 一键拉起全部（MCP + Agent + Monitoring + DB）
python run_dev.py up --all
```

说明：`sync` 执行全量补偿（新增+修改+删除对账），`refresh-schema` 仅刷新字段结构。

本地未备案阶段建议使用长连接：`python run_dev.py agent-ws`（MCP 侧用 `sync/scan` 手动补偿）。
开发期可使用 `python run_dev.py agent-ws-watch`，修改 `apps/agent-host/src` 后自动重启长连接进程。

容器名冲突或历史残留时，先执行 `python run_dev.py clean` 再 `up`。

## 启动装配要求

`AgentOrchestrator` 初始化时必须注入 `data_writer` 实例，否则启动会直接报错。

```python
from src.adapters.channels.feishu.skills.bitable_writer import BitableWriter


writer = BitableWriter(mcp_client)
orchestrator = AgentOrchestrator(data_writer=writer, ...)
```

如需在测试中使用 mock：

```python
from unittest.mock import AsyncMock

from src.core.skills.data_writer import DataWriter


mock_writer = AsyncMock(spec=DataWriter)
orchestrator = AgentOrchestrator(data_writer=mock_writer, ...)
```

默认端口：

- MCP：`8081`
- Agent：`8080`（统一开发入口 / Docker）

## Docker 编排命令（新路径）

```bash
docker compose -f deploy/docker/compose.yml up -d
docker compose -f deploy/docker/compose.yml -f deploy/docker/compose.dev.yml up -d

# 启用监控 profile
docker compose -f deploy/docker/compose.yml --profile monitoring up -d

# 启用数据库 profile（可选）
docker compose -f deploy/docker/compose.yml --profile db up -d
```

监控（可选）：

```bash
./deploy/monitoring/run_monitoring.sh
# 或 PowerShell
./deploy/monitoring/run_monitoring.ps1
```

## 常用检查

```bash
curl http://localhost:8081/health
curl http://localhost:8080/health
curl http://localhost:8081/mcp/tools
```

## 文档入口

- 主应用文档：`apps/agent-host/README.md`
- MCP 详细文档：`integrations/feishu-mcp-server/README.md`
- 项目快速上下文（人/AI）：`docs/project-context.md`
- 三阶段统一文档：`docs/deploy/three-stage-guide.md`
- 统一变量参考（合并版）：`.env.example`
- 上传清单（备案后用）：`docs/deploy/upload-manifest.md`
- 云部署检查清单（无 DB 版）：`docs/deploy/cloud-checklist-no-db.md`
- 仓库结构说明：`docs/architecture/repo-layout.md`
- 测试说明：`docs/scenarios/README.md`

## 许可证

MIT
