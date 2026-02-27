# OmniAgent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Status: Active](https://img.shields.io/badge/Project%20Status-Active-brightgreen)](https://github.com/)

**OmniAgent** 是一个多模块智能 Agent 框架：以 `apps/agent-host` 作为会话编排主服务，以 `integrations/feishu-mcp-server` 作为数据与自动化能力服务，通过 MCP 工具层连接飞书多维表格/文档检索等底层能力，为上层对话提供“可解释、可验证、可运维”的业务自动化链路。

核心模块：
- 🤖 `apps/agent-host/`：飞书会话 Agent（入站消息 → 意图/路由 → 技能执行 → 回复渲染）
- 🔌 `integrations/feishu-mcp-server/`：飞书 MCP Server（工具注册/调用、自动化规则、schema 同步、外部触发）

统一口径：
- 权威开发入口：根目录 `run_dev.py`
- 权威阶段流程：`docs/deploy/three-stage-guide.md`
- 开发约束与测试命令：`AGENTS.md`

---

## 特性一览

- 多模型路由：Task LLM / Chat LLM 分工，兼顾准确率与成本
- 状态槽位与多轮对话：CRUD 闭环、指代（“第 N 个/这条”）、二次确认
- 多表联动与补偿：跨表写入失败可进入对话补录重试，降低长链路脆弱性
- 自动化规则引擎：事件入口 + 扫描补偿 + 幂等 + 运行日志 + 死信
- 可观测性：Prometheus 指标 + 结构化日志（含稳定 `event_code`）

---

## 快速开始

详细流程（部署前/备案中/上线后）以 `docs/deploy/three-stage-guide.md` 为准；下面仅给出本地联调最小闭环。
命令以 `docs/deploy/three-stage-guide.md` 为准。

### 1) 环境要求

- Python 3.10+
- Docker & Docker Compose（推荐：用容器联调 MCP + Agent + 可选监控/DB）

### 2) 安装依赖

```bash
pip install -r requirements.txt
```

> 依赖分层：根目录 `requirements.txt` 聚合开发依赖；子模块 `requirements.txt` 主要服务于生产镜像隔离。

### 3) 准备配置（只复制 example，不要提交真实密钥）

```bash
# 根级（可选：统一管理常用环境变量）
cp .env.example .env

# Agent
cp apps/agent-host/.env.example apps/agent-host/.env
cp apps/agent-host/config.yaml.example apps/agent-host/config.yaml

# MCP
cp integrations/feishu-mcp-server/.env.example integrations/feishu-mcp-server/.env
cp integrations/feishu-mcp-server/config.yaml.example integrations/feishu-mcp-server/config.yaml
```

### 4) 启动（推荐：统一入口）

```bash
# 启动 MCP + Agent
python run_dev.py up

# 启动完整生态（含 monitoring + db，具体以 run_dev.py 实现为准）
python run_dev.py up --all
```

健康检查：

```bash
curl http://localhost:8080/health
curl http://localhost:8081/health
curl http://localhost:8081/mcp/tools
```

常用命令：

```bash
python run_dev.py logs --follow
python run_dev.py ps
python run_dev.py down
python run_dev.py clean

# MCP helpers
python run_dev.py refresh-schema
python run_dev.py sync
python run_dev.py scan --table-id tbl_xxx --app-token app_xxx

# 备案阶段：本地长连接（Agent WebSocket）
python run_dev.py agent-ws
python run_dev.py agent-ws-watch
```

---

## 文档导航

- 模块说明：`apps/agent-host/README.md`、`integrations/feishu-mcp-server/README.md`
- 三阶段流程：`docs/deploy/three-stage-guide.md`
- 场景与回归：`docs/scenarios/README.md`
- 监控：`deploy/monitoring/README.md`
- 开发规范与测试命令：`AGENTS.md`

---

## 目录结构（已调整）

```text
OmniAgent/
├── apps/agent-host/                 # Agent 主服务
├── integrations/feishu-mcp-server/  # MCP Server（工具 + 自动化）
├── deploy/                          # Docker/监控
├── docs/                             # 流程/计划/场景
│   ├── deploy/
│   ├── plans/
│   └── scenarios/
├── tests/                            # 单测/场景回归
└── tools/                            # CI/本地调试脚本
```

---

## 规范与注意事项

- 单一事实来源：不要在多个 README 重复堆配置/命令（容易漂移）。流程与命令优先指向 `docs/deploy/three-stage-guide.md` 与 `AGENTS.md`。
- 不要提交密钥：`.env`/token/secret 只应来自 `.env.example` 复制后的本地文件。
- 结构化日志：业务日志用中文 message，稳定枚举 `event_code` 放在 `extra` 里。

---

## License

MIT
