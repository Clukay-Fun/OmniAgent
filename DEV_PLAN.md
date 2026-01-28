# OmniAgent 飞书 Agent 开发计划

## 目标与范围

目标
- 以私聊机器人形式提供对话式问答
- 自动检索飞书多维表格与文档，生成结构化答案
- 低成本 VPS 部署（1 核 2G），Docker 方式运行

范围内
- 私聊消息接入、会话映射与答复
- MCP Server 统一封装飞书数据访问工具
- feishu-agent 负责意图解析、工具编排与答案生成
- 文件/图片上传下载 MCP 工具（后续阶段）

范围外（当前阶段）
- 用户级权限隔离（暂不做）
- 本地或云端向量索引（暂不使用 Postgres + pgvector）
- 群聊、多租户、复杂审批流

## 关键设计决策

- 访问方式：应用级访问（tenant app access）
- 交互方式：仅私聊
- 数据源：飞书多维表格 + 飞书文档
- 架构模式：双服务分离（feishu-agent + mcp-feishu-server）
- MCP Server 定位：共享飞书数据访问层
- 通信方式：HTTP REST
- 运行方式：VPS + Docker
- 存储策略：不落库，实时检索
- 前端交互：不单独建设 Web 前端，直接在飞书 App 内对话
- shared：Phase 1 不启用，第二个 Agent 出现再启用

## 技术选型

- 后端：Python 3.11 + FastAPI
- LLM 编排：feishu-agent（自研编排逻辑）
- MCP Server：Python（建议 FastAPI 或轻量 ASGI）
- 飞书接入：Tenant App Access（应用级）
- 部署：Docker + Docker Compose + Nginx（可选）
- 存储：不落库（Phase 1），必要时再引入 Postgres + pgvector

## LLM 选型与配置

- `EMBEDDING_MODEL`：BAAI/bge-m3
- `RERANKER_MODEL`：BAAI/bge-reranker-v2-m3
- `JSON_EXTRACT_MODEL`：Qwen/Qwen3-8B
- `DOC_STRUCTURE_MODEL`：Qwen/Qwen3-8B
- `VISION_MODEL`：THUDM/GLM-4.1V-9B-Thinking
- `REASONING_MODEL`：deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
- `CHAT_MODEL`：internlm/internlm2_5-7b-chat

## 系统架构与数据流

高层架构
- 飞书私聊机器人 → Webhook 网关 → feishu-agent → MCP Server → 飞书 API

多智能体复用架构
```
                    ┌─────────────────┐
                    │   MCP Server    │  ← 飞书数据访问层（共享）
                    │  (Feishu Tools) │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Feishu Agent   │ │   Agent B       │ │   Agent C       │
│  (案件助手)      │ │  (文档助手)     │ │  (未来扩展)     │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

数据流（一次问答）
1. 用户私聊发送问题
2. Webhook 接收消息事件并验签
3. 解析消息文本，映射/创建 feishu-agent 会话
4. feishu-agent 调用 MCP 工具检索数据
5. 聚合检索结果并生成回答
6. 回传飞书私聊回复（可选附带引用链接）

## MCP 通信模式

MCP 通信模式选型
├── 方案 A：HTTP REST（推荐 Phase 1）
│   ├── feishu-agent 通过 HTTP 调用 MCP Server
│   ├── 简单直接，易于调试
│   └── 路由：`POST /mcp/tools/{tool_name}`
│
├── 方案 B：stdio 模式
│   ├── feishu-agent 作为父进程启动 MCP Server
│   ├── 通过 stdin/stdout 通信
│   └── 适合单体部署，省资源
│
└── 方案 C：SSE 模式
    ├── MCP Server 提供 SSE 端点
    └── 适合需要流式返回的场景

## MCP 工具清单

第一阶段（检索能力）
- `feishu.v1.bitable.search`
  - 输入：关键词、时间范围、过滤字段
  - 输出：记录列表（案号/委托人/对方当事人/案由/程序阶段、开庭日、审理法院、record_url）
- `feishu.v1.doc.search`
  - 输入：关键词、范围（可选文件夹）
  - 范围：全局搜索（Phase 1）
  - 输出：`doc_token`、`title`、`url`、`preview`（前 200 字）
- `feishu.v1.bitable.record.get`
  - 输入：record_id
  - 输出：完整字段详情

第二阶段（文件能力）
- `feishu.v1.file.upload`
  - 输入：文件/图片流、文件名、类型
  - 输出：file_token 与可访问链接
- `feishu.v1.file.download`
  - 输入：file_token
  - 输出：文件流或下载链接
- `feishu.v1.file.meta.get`（可选）
  - 输入：file_token
  - 输出：文件元数据

统一约定
- 请求与响应均为 JSON
- 错误统一返回：`code`、`message`、`detail`
- MCP 层只做数据访问与转换，不做业务推理
- MCP 工具命名采用 `biz.version.resource.method` 模式

## 飞书权限申请清单

| 权限范围 | 权限标识 | 用途 |
| --- | --- | --- |
| 多维表格 | bitable:app:readonly | 读取表格数据 |
| 多维表格 | bitable:app | 读写表格（Phase 2） |
| 云文档 | docx:document:readonly | 读取文档内容 |
| 消息 | im:message | 发送消息 |
| 消息 | im:message:readonly | 读取消息（事件订阅） |
| 通讯录 | contact:user.base:readonly | 获取用户基本信息 |
| 文件 | drive:drive:readonly | 文件下载（Phase 2） |

## 测试表与字段映射（诉讼案件）

用途
- 以测试表完成检索与卡片渲染验证
- 后续切换为固定表配置，仅修改配置文件

核心字段（来自测试表）
- 序号（自动编号）
- 主办律师（多选）
- 案号（文本，多行）
- 委托人及联系方式（文本，多行）
- 对方当事人（文本，多行）
- 案由（多选）
- 审理法院（单选）
- 程序阶段（单选）
- 承办法官及助理联系方式（文本，多行）
- 管辖权异议截止日（日期）
- 举证截止日（日期）
- 查封到期日（日期）
- 反诉截止日（日期）
- 上诉截止日（日期）
- 开庭日（日期，作为“开庭日期”）
- 案件进展（文本，多行）
- 待做事项（文本，多行）
- 备注（文本，多行）
- 工作记录（文本，多行）
- 案件状态（文本，多行）

“开庭日期”映射
- 开庭日期字段：`开庭日`

展示组合（卡片标题信息）
- 组合字段：委托人 + 案号 + 对方当事人 + 案由 + 程序阶段

## 记录链接生成

链接格式
- `https://{domain}.feishu.cn/base/{app_token}?table={table_id}&view={view_id}&record={record_id}`

参数来源
- `domain`：配置文件（企业域名，如 `your-company`）
- `app_token`：配置文件（默认）或 MCP 入参
- `table_id`：配置文件（默认）或 MCP 入参
- `view_id`：配置文件（默认，可选）
- `record_id`：飞书 API 返回

实现建议
- MCP Server 统一拼接 `record_url`
- MCP 返回结构示例：
```json
{
  "records": [
    {
      "record_id": "recXXXXXX",
      "fields": {
        "案号": "（2025）粤 0306 民初 X 号",
        "开庭日": "2026-01-28",
        "审理法院": "第三法庭"
      },
      "record_url": "https://xxx.feishu.cn/base/appXXX?table=tblXXX&view=vewXXX&record=recXXXXXX"
    }
  ]
}
```

配置项补充（配置文件）
```yaml
bitable:
  domain: your-company
  default_app_token: ${BITABLE_APP_TOKEN}
  default_table_id: ${BITABLE_TABLE_ID}
  default_view_id: ${BITABLE_VIEW_ID}
```

## 飞书机器人接入设计

应用配置
- 开启机器人能力
- 订阅私聊消息事件（message 相关事件）
- 使用应用级 token

消息处理
- 仅处理私聊消息
- 忽略机器人自身消息与系统事件
- 消息 → feishu-agent 输入

会话策略
```
会话策略
├── 会话存储：内存 Dict（重启丢失，可接受）
├── 会话 Key：feishu_user_id
├── 上下文窗口：最近 5 轮（可配置）
├── 上下文格式：
│   └── [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
├── Token 预算：单次请求 ≤ 4000 tokens（含上下文）
└── 过期策略：30 分钟无活动自动清理
```

Webhook 处理流程
```
Webhook 处理流程
├── 1. URL 验证请求（首次配置）
│   └── 返回 challenge 值
│
├── 2. 消息解密（若启用加密）
│   └── AES-256-CBC 解密
│
├── 3. 事件去重
│   └── 基于 event_id 去重（内存 Set，TTL 5min）
│
├── 4. 消息过滤
│   ├── 忽略 message_type != "text"（Phase 1）
│   ├── 忽略机器人自身消息
│   └── 忽略非私聊消息
│
├── 5. 异步处理
│   └── 立即返回 200，后台处理（避免超时）
│
└── 6. 回复发送
    └── POST /im/v1/messages（reply 模式）
```

## Agent Prompt 设计框架

System Prompt 结构
├── 角色定义
│   └── 你是一个法律事务助手，帮助律师查询案件信息
│
├── 能力边界
│   └── 仅查询多维表格案件数据与飞书文档，不编造结果
│
├── 工具使用指引
│   ├── 何时使用 `feishu.v1.bitable.search`
│   ├── 何时使用 `feishu.v1.doc.search`
│   └── 参数提取规则（如“本周”→日期范围）
│
├── 输出格式要求
│   └── 回复简洁、结构化，包含关键字段与链接
│
└── 限制与兜底
    └── 无结果礼貌告知，不编造数据

## 时间解析逻辑

时间表达式解析（由 LLM 直接解析为日期范围）
- 今天 → 当天 00:00 - 23:59
- 明天 → 明天 00:00 - 23:59
- 本周 → 本周一 00:00 - 本周日 23:59
- 下周 → 下周一 00:00 - 下周日 23:59
- 下周一 → 下周一 00:00 - 23:59
- 这个月 → 本月 1 号 - 本月最后一天
- 1 月 28 号 → 指定日期

实现方式
- 方案 A：LLM 直接解析为日期范围（推荐，灵活）
- 方案 B：正则 + 规则引擎（可选，确定性高）

## 回复消息格式

回复类型
- 优先：飞书消息卡片（结构化列表）
- 兜底：纯文本

卡片展示字段（默认）
- 开庭日
- 组合标题：委托人 + 案号 + 对方当事人 + 案由 + 程序阶段
- 审理法院
- record_url

## 日志规范

日志格式建议（JSON 结构化）
```json
{
  "timestamp": "2026-01-27T10:30:00Z",
  "level": "INFO",
  "service": "feishu-agent",
  "trace_id": "abc123",
  "user_id": "ou_xxxxx",
  "event": "tool_call",
  "tool": "feishu.v1.bitable.search",
  "duration_ms": 320,
  "status": "success",
  "detail": {}
}
```

关键日志点
- Webhook 接收与验签
- LLM 调用（耗时、token 数）
- MCP 工具调用（耗时、结果数量）
- 错误与异常

## Agent 编排策略（feishu-agent）

- 解析意图（如时间范围、本周/下周）
- 选择 MCP 工具并执行
- 结果归一化 → 生成可读答案
- 可选：附带引用链接（表格记录/文档）

## 项目目录结构

```
omniagent/                          # 项目根目录
├── README.md                       # 项目总览
├── docker-compose.yml              # 统一编排
├── .env.example                    # 环境变量参考（汇总）
├── Makefile                        # 常用命令（可选）
│
├── agent/
│   └── feishu-agent/               # 飞书案件助手 Agent
│       ├── README.md
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── config.yaml.example
│       ├── .env.example
│       ├── src/
│       │   ├── main.py
│       │   ├── config.py
│       │   ├── api/
│       │   │   ├── __init__.py
│       │   │   ├── webhook.py
│       │   │   └── health.py
│       │   ├── agent/
│       │   │   ├── __init__.py
│       │   │   ├── core.py
│       │   │   ├── prompt.py
│       │   │   └── session.py
│       │   ├── llm/
│       │   │   ├── __init__.py
│       │   │   ├── client.py
│       │   │   └── provider.py
│       │   ├── mcp/
│       │   │   ├── __init__.py
│       │   │   └── client.py
│       │   └── utils/
│       │       ├── __init__.py
│       │       ├── time_parser.py
│       │       └── logger.py
│       └── tests/
│
├── mcp/
│   └── mcp-feishu-server/          # 飞书 MCP Server（可复用）
│       ├── README.md
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── config.yaml.example
│       ├── .env.example
│       ├── src/
│       │   ├── main.py
│       │   ├── config.py
│       │   ├── server/
│       │   │   ├── __init__.py
│       │   │   ├── http.py
│       │   │   ├── stdio.py
│       │   │   └── sse.py
│       │   ├── tools/
│       │   │   ├── __init__.py
│       │   │   ├── registry.py
│       │   │   ├── bitable.py
│       │   │   ├── doc.py
│       │   │   └── file.py
│       │   ├── feishu/
│       │   │   ├── __init__.py
│       │   │   ├── client.py
│       │   │   ├── token.py
│       │   │   └── models.py
│       │   └── utils/
│       │       ├── __init__.py
│       │       ├── url_builder.py
│       │       └── logger.py
│       └── tests/
│
└── shared/                         # [可选] Phase 1 不创建
```

shared 启用条件
- 出现第二个 Agent
- 有明确的公共模型/工具需要复用

## 统一错误码

| 错误码 | 场景 | 用户提示 |
| --- | --- | --- |
| MCP_001 | 飞书 API 调用失败 | 数据获取失败，请稍后重试 |
| MCP_002 | 多维表格不存在 | 未找到指定数据表 |
| MCP_003 | 权限不足 | 暂无权限访问该数据 |
| AGENT_001 | LLM 调用超时 | 思考超时，请简化问题重试 |
| AGENT_002 | 工具执行失败 | 查询失败：{detail} |
| WEBHOOK_001 | 验签失败 | 不回复，仅日志 |

## 部署方案（VPS + Docker）

容器
- `feishu-agent`：API 与编排
- `mcp-feishu-server`：飞书数据访问
- 可选：`nginx`（反向代理与统一入口）

基础路由
- `/feishu/webhook` → Webhook 接入
- `/api` → feishu-agent API
- `/mcp` → MCP Server

运行要求
- 1 核 2G 最低可用
- 开放 HTTPS 访问（Webhook 需要公网可达）

Docker Compose 结构建议
```yaml
# docker-compose.yml 骨架
version: '3.8'
services:
  feishu-agent:
    build: ./agent/feishu-agent
    ports:
      - "8080:8080"
    environment:
      - FEISHU_APP_ID=${FEISHU_APP_ID}
      - FEISHU_APP_SECRET=${FEISHU_APP_SECRET}
      # ...
    depends_on:
      - mcp-feishu-server
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1G

  mcp-feishu-server:
    build: ./mcp/mcp-feishu-server
    ports:
      - "8081:8081"
    # stdio 模式下可作为 sidecar 或合并到 feishu-agent
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M

  # 可选：Nginx 反代
  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./certs:/etc/nginx/certs
```

## 1C2G 资源下的部署策略

策略 | 说明
--- | ---
轻量化 MCP Server | 纯工具层、无状态，内存占用约 100-200MB
共享 Python 基础镜像 | 减少磁盘和拉取开销
按需启动 | 初期只部署 feishu-agent + mcp-feishu-server
单 Worker | 两个服务各 1 worker

## 配置与密钥

建议环境变量（示例名称）
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_VERIFICATION_TOKEN`
- `FEISHU_ENCRYPT_KEY`（若启用消息加密）
- `MCP_SERVER_BASE`
- `BITABLE_DOMAIN`
- `BITABLE_APP_TOKEN`
- `BITABLE_TABLE_ID`
- `BITABLE_VIEW_ID`
- `DOC_FOLDER_TOKEN`
- `LLM_API_KEY`
- `LLM_API_BASE`
- `LLM_FALLBACK_API_KEY`

配置文件放置
- `agent/feishu-agent/config.yaml.example`：Agent 专属配置
- `mcp/mcp-feishu-server/config.yaml.example`：MCP Server 专属配置
- 根目录 `.env.example`：变量汇总参考（可选）

重复配置处理
- 飞书凭证通过环境变量注入
- 各服务从环境变量读取，无需硬编码

## 配置文件结构（示例）

```yaml
server:
  host: 0.0.0.0
  port: 8080
  workers: 1

feishu:
  app_id: ${FEISHU_APP_ID}
  app_secret: ${FEISHU_APP_SECRET}
  verification_token: ${FEISHU_VERIFICATION_TOKEN}
  encrypt_key: ${FEISHU_ENCRYPT_KEY}

bitable:
  domain: your-company
  default_app_token: ${BITABLE_APP_TOKEN}
  default_table_id: ${BITABLE_TABLE_ID}
  default_view_id: ${BITABLE_VIEW_ID}

llm:
  embedding_model: BAAI/bge-m3
  reranker_model: BAAI/bge-reranker-v2-m3
  json_extract_model: Qwen/Qwen3-8B
  doc_structure_model: Qwen/Qwen3-8B
  vision_model: THUDM/GLM-4.1V-9B-Thinking
  reasoning_model: deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
  chat_model: internlm/internlm2_5-7b-chat
  temperature: 0.3
  max_tokens: 2000
  timeout: 30

mcp:
  mode: http
  base_url: http://mcp-feishu-server:8081

session:
  max_rounds: 5
  ttl_minutes: 30
  max_tokens: 4000

logging:
  level: INFO
  format: json
```

## 里程碑与交付物

里程碑 1：私聊问答闭环
- Webhook 接入
- MCP 工具：多维表格/文档检索
- feishu-agent 回答生成
- 基础日志与错误处理

里程碑 2：文件/图片 MCP 工具
- 上传/下载接口
- 文件类型与大小限制
- 错误码与可观测性

里程碑 3：体验与稳定性
- 结果引用与卡片展示（可选）
- 超时与重试策略
- 监控与告警

## 潜在风险与应对

| 风险 | 影响 | 应对措施 |
| --- | --- | --- |
| 飞书 Webhook 超时（5s 限制） | 复杂查询无法及时响应 | 立即返回 200，异步处理后主动推送 |
| 1C2G 内存不足 | OOM 导致服务崩溃 | 限制并发数（≤3）、流式响应、定期 GC |
| Token 管理失效 | API 调用 401 | Token Manager 自动刷新 + 重试 |
| LLM 幻觉 | 返回不存在的记录 | 结果校验（record_id 回查）+ 引用链接 |
| 飞书 API 频控 | 请求被拒绝 | Rate Limiter + 指数退避重试 |

## 验收标准

| 级别 | 场景 | 输入 | 预期输出 |
| --- | --- | --- | --- |
| P0 | 基础查询 | 这周有什么庭要开 | 返回本周庭审列表 + 引用链接 |
| P1 | 条件过滤 | 下周一在中级法院的庭 | 精确过滤结果 |
| P0 | 空结果 | 明年的庭审安排 | 友好提示“未找到相关记录” |
| P0 | 文档搜索 | 找一下关于XX的文档 | 返回文档摘要 + 链接 |
| P0 | 异常场景 | 飞书 API 故障 | 返回可读错误，不崩溃 |
| P1 | 并发测试 | 3 人同时提问 | 均能正常响应（≤10s） |
