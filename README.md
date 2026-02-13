# OmniAgent

多模块智能 Agent 项目，当前主线是：

- `mcp/mcp-feishu-server`：飞书数据侧 MCP 服务（查询、CRUD、自动化、schema watcher）
- `agent/feishu-agent`：飞书机器人侧 Agent（意图识别、技能路由、MCP 调用）

## 当前阶段

- 云服务器与域名已准备，暂不上传生产部署（等待备案审核）
- 本仓库已完成一次目录重整：部署文件、监控文件、工具脚本、场景文档已拆分归位
- 开发/备案/上线统一口径见：`docs/deploy/three-stage-guide.md`

## 目录结构（已调整）

```text
OmniAgent/
├── agent/
│   ├── feishu-agent/                # Agent 服务代码
│       └── workspace/               # Agent 运行态工作区（本地）
├── mcp/
│   └── mcp-feishu-server/           # MCP 服务代码
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
    ├── architecture/
    │   └── repo-layout.md
    └── tests/
        └── TEST.md
```

## 本地开发（不依赖云）

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

# 一键拉起全部（MCP + Agent + Monitoring + DB）
python run_dev.py up --all
```

容器名冲突或历史残留时，先执行 `python run_dev.py clean` 再 `up`。

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

- MCP 详细文档：`mcp/mcp-feishu-server/README.md`
- Agent 详细文档：`agent/feishu-agent/README.md`
- 三阶段统一文档：`docs/deploy/three-stage-guide.md`
- 统一变量参考（合并版）：`.env.example`
- 上传清单（备案后用）：`docs/deploy/upload-manifest.md`
- 云部署检查清单（无 DB 版）：`docs/deploy/cloud-checklist-no-db.md`
- 仓库结构说明：`docs/architecture/repo-layout.md`
- 测试说明：`docs/tests/TEST.md`

## 许可证

MIT
