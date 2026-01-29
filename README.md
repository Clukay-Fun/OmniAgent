# OmniAgent

基于自研 `universal-rag` 的多智能体协作平台。聚焦“多智能体 + 多数据源 + 工具编排 + SSE 实时通信”，用于搭建可扩展的业务智能体矩阵。

核心能力
- 智能体注册与隔离：每个智能体拥有独立的 system prompt 与配置
- 多数据源接入：PostgreSQL/MySQL/API 等外部数据源动态接入
- 工具编排：通过统一的工具注册中心扩展能力
- RAG 引擎：文档解析、结构化、向量检索与问答闭环
- SSE 实时通信：输出执行状态与内容流，支持可视化调试

## 当前进度（Feishu Agent）

- `agent/feishu-agent`：Webhook、会话管理、MCP 调用、Agent 编排已完成，可本地模拟
- `mcp/mcp-feishu-server`：多维表格检索、记录详情、文档搜索、record_url 拼接已完成
- 结果返回同时包含 `fields`（原始）与 `fields_text`（纯文本）
- `docker-compose.yml` 与 `docker-compose.dev.yml` 已准备，支持热更新
- 待办：单元测试、飞书真实回调与部署流程

## 架构概览

```mermaid
flowchart LR
  subgraph DataSources[DataSources]
    ds1[招标信息库]
    ds2[团队知识库]
    ds3[财务系统]
  end

  subgraph Platform[OmniAgent Platform]
    core[平台核心]
    agent[智能体管理]
    datasource[数据源管理]
    tools[工具注册中心]
    rag[RAG 引擎]
    sse[SSE 实时通信]
  end

  subgraph Agents[Agents]
    a1[投标助手]
    a2[团队助手]
    a3[财务助手]
  end

  core --> agent
  core --> datasource
  core --> tools
  core --> rag
  core --> sse

  ds1 --> datasource
  ds2 --> datasource
  ds3 --> datasource

  agent --> a1
  agent --> a2
  agent --> a3
  tools --> a1
  tools --> a2
  tools --> a3
  rag --> a1
  rag --> a2
  rag --> a3
  sse --> a1
  sse --> a2
  sse --> a3
```

## 快速开始（先 DB → 后端 → 前端）

环境要求
- Python 3.11+
- PostgreSQL 14+（已安装 pgvector 扩展）
- Node.js 18+（Vue 3 + Vite）

1) 配置环境变量（根目录）

在仓库根目录创建 `.env`：

```bash
DATABASE_URL=postgresql://user:pass@localhost:5432/omniagent
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8001
LOG_LEVEL=INFO

MODEL_API_BASE_URL=https://api.siliconflow.cn/v1
MODEL_API_KEY=sk-xxx
EMBEDDING_MODEL=BAAI/bge-m3
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
JSON_EXTRACT_MODEL=Qwen/Qwen3-8B
DOC_STRUCTURE_MODEL=Qwen/Qwen3-8B
VISION_MODEL=THUDM/GLM-4.1V-9B-Thinking
REASONING_MODEL=deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
CHAT_MODEL=internlm/internlm2_5-7b-chat
```

2) 初始化数据库

```bash
psql -U postgres -d omniagent -f .backend/sql/schema.sql
```

3) 启动后端（FastAPI）

```bash
pip install -r requirements.txt
cd .backend
python -m api.main
```

默认地址：`http://localhost:8001`

4) 启动前端（Vue 3 + Vite）

```bash
cd frontend
npm install
npm run dev
```

> 前端目录为 `frontend/`，若尚未初始化前端代码，可先跳过此步。

开发模式启动命令：
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## 核心概念

| 概念 | 说明 |
| --- | --- |
| Agent | 独立智能体，拥有专属 prompt 与配置 |
| Datasource | 智能体可接入的外部数据源（PostgreSQL/MySQL/API） |
| Tool | 可注册并被 Agent 调用的工具能力 |
| Session | 用户与智能体的对话会话 |
| Workflow | 多智能体协作流程（规划中） |

## API 参考

智能体管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/agents` | 创建智能体 |
| `GET` | `/agents` | 列出智能体 |
| `GET` | `/agents/{agent_id}` | 获取智能体详情 |
| `PUT` | `/agents/{agent_id}` | 更新智能体 |
| `DELETE` | `/agents/{agent_id}` | 删除智能体（默认智能体不可删） |

数据源管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/agents/{agent_id}/datasources` | 添加数据源 |
| `GET` | `/agents/{agent_id}/datasources` | 列出数据源 |
| `DELETE` | `/agents/datasources/{datasource_id}` | 删除数据源 |
| `POST` | `/agents/datasources/{datasource_id}/test` | 测试连接 |
| `GET` | `/agents/datasources/{datasource_id}/tables` | 列出表 |
| `POST` | `/agents/datasources/{datasource_id}/query` | 执行查询 |

文档解析

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/documents/parse` | 解析文档（可选持久化） |
| `GET` | `/documents/{doc_id}/structure` | 获取结构化结果 |
| `GET` | `/documents/{doc_id}/tree` | 获取文档节点树 |
| `GET` | `/documents/nodes/search` | 搜索文档节点 |

向量检索

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/vectors/document-nodes` | 构建文档节点向量 |
| `POST` | `/vectors/search` | 向量相似度检索 |

RAG 问答

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/qa/ask` | 检索增强问答 |

对话与 SSE

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/chat/sessions` | 获取最近会话 |
| `POST` | `/chat/sessions` | 创建会话 |
| `POST` | `/chat/sessions/{session_id}/messages` | 发送消息（SSE） |
| `GET` | `/chat/sessions/{session_id}/history` | 获取历史消息 |

健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 服务健康状态 |

SSE 事件类型：`status` / `progress` / `chunk` / `done` / `error`

## 技术栈

| 类别 | 技术 |
| --- | --- |
| 后端 | FastAPI（Python 3.11+） |
| RAG 引擎 | 自研 `universal-rag`（位于 `.backend/`，内部依赖 LlamaIndex） |
| 数据库 | PostgreSQL + pgvector |
| 模型编排 | 多模型协作（Embedding/Rerank/结构化/推理/对话） |
| 前端 | Vue 3 + Vite |
| 通信 | SSE（Server-Sent Events） |

## 目录结构

```
OmniAgent/
├── .backend/              # 后端服务（universal-rag 内核）
│   ├── api/               # FastAPI 路由
│   ├── services/          # 业务服务与 Agent Loop
│   ├── db/                # 数据库连接
│   ├── schemas/           # Pydantic 模型
│   ├── sql/               # Schema 与迁移
│   └── prompts/           # 系统提示词模板
├── frontend/              # 前端（Vue 3 + Vite）
├── requirements.txt       # 后端依赖
└── .env                   # 本地环境变量
```

## 扩展开发指南

添加新工具

```python
# .backend/services/tools/my_tool.py
from services.tool_registry import BaseTool, ToolRegistry

@ToolRegistry.register
class MyTool(BaseTool):
    name: str = "my_tool"
    description: str = "工具描述"
    param1: str

    def run(self) -> str:
        return "结果"
```

创建新智能体

```json
POST /agents
{
  "name": "投标助手",
  "description": "负责招投标资料检索与总结",
  "system_prompt": "你是专业投标顾问...",
  "config": {}
}
```

接入新数据源

```json
POST /agents/{agent_id}/datasources
{
  "name": "招标信息库",
  "ds_type": "postgresql",
  "connection_config": {
    "host": "localhost",
    "port": 5432,
    "database": "tender",
    "user": "postgres",
    "password": "***"
  }
}
```

## 排障指南

常见问题与处理方式见 `TROUBLESHOOTING.md`。

## 环境变量配置

必填
- `DATABASE_URL`
- `MODEL_API_BASE_URL`
- `MODEL_API_KEY`

服务配置
- `FASTAPI_HOST`（默认：`0.0.0.0`）
- `FASTAPI_PORT`（默认：`8001`）
- `LOG_LEVEL`（默认：`INFO`）

模型配置
- `EMBEDDING_MODEL`：`BAAI/bge-m3`
- `RERANKER_MODEL`：`BAAI/bge-reranker-v2-m3`
- `JSON_EXTRACT_MODEL`：`Qwen/Qwen3-8B`
- `DOC_STRUCTURE_MODEL`：`Qwen/Qwen3-8B`
- `VISION_MODEL`：`THUDM/GLM-4.1V-9B-Thinking`
- `REASONING_MODEL`：`deepseek-ai/DeepSeek-R1-0528-Qwen3-8B`
- `CHAT_MODEL`：`internlm/internlm2_5-7b-chat`

## 与 universal-rag 的差异

- universal-rag 作为后端内核迁入 `.backend/`，OmniAgent 是平台层与协作层
- 平台关注多智能体管理、工具编排与数据源隔离，而非单一 RAG 工具
- 增加前端（Vue 3 + Vite）与可视化协作流程能力

## 验证计划

- README 结构完整、章节与实际功能一致
- 快速开始顺序可执行（DB → 后端 → 前端）
- API 示例路径与后端路由一致

## 许可证

MIT
