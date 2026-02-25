# OmniAgent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Status: Active](https://img.shields.io/badge/Project%20Status-Active-brightgreen)](https://github.com/)

**OmniAgent** 是一个多模块智能 Agent 框架，通过聚合底层系统能力（如飞书多维表格引擎、知识库检索等），为用户提供一体化的智能会话、数据联动与业务自动化服务。

当前主要包含以下核心子模块：
* 🤖 **[apps/agent-host](apps/agent-host/)**: 飞书会话 Agent 主应用入口
* 🔌 **[integrations/feishu-mcp-server](integrations/feishu-mcp-server/)**: 飞书数据侧 MCP (Model Context Protocol) 协议服务层

---

## 🌟 核心特性

- **双模路由架构**：结合高优任务模型 (Task LLM) 与高质量对话模型 (Chat LLM)，在保障意图解析精准度的同时降低长期对话成本。
- **上下文感知记忆**：自动提取用户偏好并存储于知识图谱中，实现千人千面的个性化响应。
- **多表长程联动**：基于 MCP 协议，支持跨数据表的高阶联动（例如：从案件库流转至合同库）。
- **自动化运行时保障**：内建子表失败节点补偿录入机制，提供可靠的长链条业务保障。

---

## 📌 项目定位与核心认知

- **产品主线**：个人 AI Agent（当前先通过飞书接入）
- **架构形态**：单 Agent 主应用 (`apps/agent-host`) + MCP 工具服务 (`integrations/feishu-mcp-server`)
- **人格命名**：统一为“小敬”

## ⚠️ 关键代码与日志规范

- **隔离性**：Core 层不直接依赖渠道协议细节。
- **回复结构**：回复链路必须采用通用结构，支持向文本平滑降级 (fallback)。
- **单一来源**：文档结构采用单一权威来源（Single Source of Truth），绝不在多处重复罗列相同的配置、命令或设计，避免版本不同步。
- **中文业务日志**：业务日志 `message` 请使用**中文**，便于开发时人工排障。
- **稳定事件码**：日志的 `extra` 属性中统一携带 `event_code`（英文稳定枚举码，便于后续流转与告警）。
- **结构化上下文**：关键上下文字段保持严格的结构化（如 `request_id`、`user_id`、`duration_ms`）不能丢失。

---

## 目录结构（已调整）

```text
OmniAgent/
├── apps/agent-host/                 # 🤖 单 Agent 主应用入口
│   ├── config/                      # ⚙️ 配置文件目录 (技能提示词与回复文案池)
│   ├── src/                         
│   │   ├── core/                    # 🧠 Agent 核心引擎层 (意图/路由/技能/编排)
│   │   ├── adapters/                # 🔌 渠道适配器层 (优先支持飞书渠道)
│   │   ├── db/                      # 🗄️ 专属数据持久化层操作
│   │   ├── llm/                     # 🌐 底层大模型客户端封装库
│   │   ├── mcp/                     # 🔗 核心模型上下文协议(MCP)交互端
│   │   └── utils/                   # 🛠️ 通用工具组件
│   └── README.md                    # 📖 Agent 主应用详细使用说明
├── integrations/feishu-mcp-server/  # 🔌 飞书 MCP 服务主入口
│   ├── src/
│   │   ├── automation/              # ⚙️ 自动化规则引擎 (监听回调及数据流转)
│   │   ├── feishu/                  # 🟢 飞书开放平台 SDK 封装层
│   │   ├── server/                  # 📡 MCP Server 协议与服务层绑定
│   │   └── tools/                   # 🧰 暴露给 MCP 客户端的具体能力 (如增删改查表单)
│   └── README.md                    # 📖 MCP 服务详细能力文档
├── deploy/                          
│   ├── docker/                      # 🐳 Docker Compose 容器编排文件
│   └── monitoring/                  # 📊 Prometheus 与 Grafana 监控配置
├── tools/                           
│   ├── dev/                         # 🛠️ 本地调试与对账脚本
│   └── ci/                          # 🧪 验证与覆盖率检查脚本
└── docs/                            
    ├── scenarios/                   # 📝 自动化场景演练与人类评审用例 (各类自动化验收清单)
    ├── deploy/                      # 🚢 云服部署与上线相关文档 (架构设计及各阶段部署手册)
    └── ROADMAP.md                   # 🗺️ 项目未来演进路线图
```

---

## 🚀 快速开始

开发与运行统一建议使用 `run_dev.py` 脚本，屏蔽了复杂的环境依赖和 Docker 配置。
命令以 `docs/deploy/three-stage-guide.md` 为准。
> *详细的三阶段开发/部署/上线指南，请参阅：[`docs/deploy/three-stage-guide.md`](docs/deploy/three-stage-guide.md)*

### 1. 前置环境要求
- Python 3.10+
- Docker & Docker Compose (用于启动完整多服务集)

### 2. 准备配置及依赖
建议在当前根目录下统一初始化环境变量配置：
```bash
# 生成本地配置
cp config.yaml.example config.yaml
cp .env.example .env

# 如果使用 run_dev.py，脚本会自动处理容器内依赖。
# 若想在宿主机原生联调，请统一安装根目录的聚合依赖：
pip install -r requirements.txt
```
> **依赖分层说明**：根目录 `requirements.txt` 统一聚合了所有子模块的依赖；各模块的独立依赖文件（如 `apps/agent-host/requirements.txt`）仅用于构建隔离的生产镜像或微服务化场景，日常开发操作根目录这一处即可。

### 3. 一键启动 (推荐)
```bash
# 自动通过 Docker 启动 Agent 及 MCP Server
python run_dev.py up

# 或拉起完整生态 (含 MCP, Agent 环境，Monitoring, DB)
python run_dev.py up --all
```

### 4. 常用命令集
```bash
python run_dev.py logs --follow      # 追踪所有容器实时日志
python run_dev.py ps                 # 查看关联容器的运行状态
python run_dev.py down               # 停止所有服务
python run_dev.py clean              # 彻底清理遗留的容器、网络及残留状态
python run_dev.py sync               # 执行全量的业务表结构与记录同步（处理新增、修改及删除对账）
python run_dev.py agent-ws           # 启动支持热更新的本地长连接开发模式 (推荐未备案阶段使用)
```

---

## 📋 模块文档导航

若要深入了解各个核心模块的架构或 API 详情，请查阅下方独立文档：

* 📘 **[Agent 主应用文档](apps/agent-host/README.md)**: 意图解析、多模型路由与用户偏好说明
* 📒 **[MCP 服务文档](integrations/feishu-mcp-server/README.md)**: 飞书数据操作工具注册、自动化规则引擎与字段同步
* 📗 **[自动化监控服务说明](deploy/monitoring/README.md)**: Prometheus / Grafana 的启用与查看方法
* 📔 **[系统测试与场景评审说明](docs/scenarios/README.md)**: 场景用例构成与回归验证规范

---

## 启动装配提示 (仅面向原生代码调试者)

若不使用 `run_dev.py` 而是通过源码挂载运行时：
`AgentOrchestrator` 初始化**必须**注入合法的 `data_writer` 实例以承接 MCP 能力，否则会在初始化阶段抛出异常。
相关测试/Mock手段，请参考 [apps/agent-host/README.md](apps/agent-host/README.md) 获取具体代码实现。

---

## 🪪 开源协议

MIT License
