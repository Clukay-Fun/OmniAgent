# OmniAgent 开发任务计划

## 总览

```
开发阶段（共 4 周）
├── Phase 1：项目初始化
├── Phase 2：MCP Server 开发
├── Phase 3：Feishu Agent 开发
├── Phase 4：集成联调与部署
└── Phase 5：优化与验收
```

---

## Phase 1：项目初始化

### Task 1.1 飞书应用创建与配置

**目标**：完成飞书开放平台应用注册，获取开发凭证

**步骤**：
1. 登录 [飞书开放平台](https://open.feishu.cn/)
2. 创建企业自建应用
3. 记录 `App ID` 和 `App Secret`
4. 开启「机器人」能力
5. 配置 Webhook 地址（先填占位符，后续更新）
6. 订阅事件：`im.message.receive_v1`
7. 申请权限（见下表）
8. 提交审核，等待通过

**所需权限**：

| 权限标识 | 用途 |
|----------|------|
| `bitable:app:readonly` | 读取多维表格 |
| `docx:document:readonly` | 读取文档 |
| `im:message` | 发送消息 |
| `im:message:readonly` | 接收消息 |
| `contact:user.base:readonly` | 获取用户信息 |

**交付物**：
- [ ] App ID / App Secret 已获取
- [ ] 机器人能力已开启
- [ ] 权限已申请/审批通过
- [ ] Verification Token 已记录

---

### Task 1.2 项目目录结构初始化

**目标**：创建项目骨架，初始化 Git 仓库

**步骤**：
1. 创建根目录及子项目结构
2. 初始化各项目的基础文件
3. 配置 `.gitignore`
4. 初始化 Git 仓库并首次提交

**执行命令**：
```bash
mkdir -p omniagent/{agent/feishu-agent,mcp/mcp-feishu-server}

# MCP Server 结构
mkdir -p omniagent/mcp/mcp-feishu-server/{src/{server,tools,feishu,utils},tests}

# Feishu Agent 结构
mkdir -p omniagent/agent/feishu-agent/{src/{api,agent,llm,mcp,utils},tests}

# 创建基础文件
touch omniagent/{README.md,docker-compose.yml,.env.example,.gitignore}
touch omniagent/mcp/mcp-feishu-server/{README.md,Dockerfile,requirements.txt,config.yaml.example,.env.example}
touch omniagent/agent/feishu-agent/{README.md,Dockerfile,requirements.txt,config.yaml.example,.env.example}

# 各模块 __init__.py
find omniagent -type d -name "src" -exec sh -c 'find "{}" -type d -exec touch {}/__init__.py \;' \;
```

**交付物**：
- [ ] 目录结构已创建
- [ ] Git 仓库已初始化
- [ ] `.gitignore` 已配置（忽略 `.env`、`__pycache__`、`.venv` 等）

---

### Task 1.3 开发环境配置

**目标**：配置本地开发环境，确保可运行

**步骤**：
1. 创建 Python 虚拟环境（各子项目独立）
2. 编写初始 `requirements.txt`
3. 配置 `.env.example` 模板
4. 配置 `config.yaml.example` 模板

**mcp/mcp-feishu-server/requirements.txt**：
```
fastapi>=0.109.0
uvicorn>=0.27.0
httpx>=0.26.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
pyyaml>=6.0
python-dotenv>=1.0.0
```

**agent/feishu-agent/requirements.txt**：
```
fastapi>=0.109.0
uvicorn>=0.27.0
httpx>=0.26.0
openai>=1.12.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
pyyaml>=6.0
python-dotenv>=1.0.0
pycryptodome>=3.20.0    # 飞书消息解密
```

**交付物**：
- [ ] 虚拟环境可激活
- [ ] 依赖可正常安装
- [ ] `.env.example` 已编写
- [ ] `config.yaml.example` 已编写

---

## Phase 2：MCP Server 开发

### Task 2.1 飞书 API Client 封装

**目标**：封装飞书 API 调用基础能力，包括 Token 管理

**开发文件**：
```
mcp/mcp-feishu-server/src/
├── config.py              # 配置加载
└── feishu/
    ├── __init__.py
    ├── client.py          # HTTP 客户端封装
    ├── token.py           # Token 获取与自动刷新
    └── models.py          # 数据模型定义
```

**核心逻辑**：
1. `token.py`：实现 `tenant_access_token` 获取与缓存
   - Token 有效期 2 小时，提前 5 分钟刷新
   - 线程安全的单例模式
2. `client.py`：封装通用请求方法
   - 自动携带 Token
   - 统一错误处理
   - 请求重试（指数退避）
3. `models.py`：定义飞书 API 响应模型

**验收标准**：
- [ ] 可成功获取 `tenant_access_token`
- [ ] Token 过期后自动刷新
- [ ] API 调用失败时正确抛出异常

---

### Task 2.2 多维表格工具开发

**目标**：实现 `feishu.v1.bitable.search` 和 `feishu.v1.bitable.record.get` 工具

**开发文件**：
```
mcp/mcp-feishu-server/src/
├── tools/
│   ├── __init__.py
│   ├── base.py            # 工具基类
│   ├── registry.py        # 工具注册表
│   └── bitable.py         # 多维表格工具
└── utils/
    └── url_builder.py     # record_url 拼接
```

**工具定义**：

```
feishu.v1.bitable.search
├── 输入参数
│   ├── keyword: str（可选，搜索关键词）
│   ├── date_from: str（可选，ISO 格式）
│   ├── date_to: str（可选，ISO 格式）
│   ├── filters: dict（可选，字段过滤条件）
│   ├── app_token: str（可选，默认从配置读取）
│   └── table_id: str（可选，默认从配置读取）
│
└── 输出
    ├── records: list
    │   ├── record_id: str
    │   ├── fields: dict（案号、委托人、开庭日等）
    │   └── record_url: str
    └── total: int

feishu.v1.bitable.record.get
├── 输入参数
│   ├── record_id: str（必填）
│   ├── app_token: str（可选）
│   └── table_id: str（可选）
│
└── 输出
    ├── record_id: str
    ├── fields: dict（完整字段）
    └── record_url: str
```

**验收标准**：
- [ ] 可根据关键词搜索记录
- [ ] 可根据日期范围过滤
- [ ] 返回的 `record_url` 可正常打开
- [ ] 单条记录查询正常

---

### Task 2.3 文档搜索工具开发

**目标**：实现 `feishu.v1.doc.search` 工具

**开发文件**：
```
mcp/mcp-feishu-server/src/tools/
└── doc.py                 # 文档搜索工具
```

**工具定义**：

```
feishu.v1.doc.search
├── 输入参数
│   ├── keyword: str（必填，搜索关键词）
│   ├── folder_token: str（可选，限定文件夹）
│   └── limit: int（可选，默认 10）
│
└── 输出
    └── documents: list
        ├── doc_token: str
        ├── title: str
        ├── url: str
        └── preview: str（前 200 字摘要）
```

**验收标准**：
- [ ] 可根据关键词搜索文档
- [ ] 返回文档链接可正常打开
- [ ] 包含文档预览摘要

---

### Task 2.4 MCP Server HTTP 接口

**目标**：实现 HTTP 模式的 MCP Server 入口

**开发文件**：
```
mcp/mcp-feishu-server/src/
├── main.py                # FastAPI 入口
└── server/
    ├── __init__.py
    ├── http.py            # HTTP 路由
    └── schema.py          # 请求/响应模型
```

**接口设计**：

```
POST /mcp/tools/{tool_name}
├── Request Body
│   └── { "params": { ... } }
│
└── Response Body
    ├── 成功：{ "success": true, "data": { ... } }
    └── 失败：{ "success": false, "error": { "code": "...", "message": "..." } }

GET /mcp/tools
└── 返回所有可用工具列表及其参数定义

GET /health
└── 健康检查
```

**验收标准**：
- [ ] 服务可正常启动（`uvicorn`）
- [ ] `/health` 返回 200
- [ ] `/mcp/tools` 返回工具列表
- [ ] `/mcp/tools/feishu.v1.bitable.search` 可正常调用

---

### Task 2.5 MCP Server 单元测试

**目标**：核心功能测试覆盖

**开发文件**：
```
mcp/mcp-feishu-server/tests/
├── conftest.py            # 测试配置
├── test_token.py          # Token 管理测试
├── test_bitable.py        # 多维表格工具测试
├── test_doc.py            # 文档工具测试
└── test_http.py           # HTTP 接口测试
```

**验收标准**：
- [ ] Token 获取与刷新测试通过
- [ ] 工具调用测试通过（可用 mock）
- [ ] HTTP 接口测试通过

---

## Phase 3：Feishu Agent 开发

### Task 3.1 飞书 Webhook 接入

**目标**：接收并处理飞书机器人消息事件

**开发文件**：
```
agent/feishu-agent/src/
├── main.py                # FastAPI 入口
├── config.py              # 配置加载
└── api/
    ├── __init__.py
    ├── webhook.py         # Webhook 处理
    └── health.py          # 健康检查
```

**核心逻辑**：

```
Webhook 处理流程
├── 1. URL 验证（返回 challenge）
├── 2. 验签校验
├── 3. 消息解密（如启用）
├── 4. 事件去重（event_id，内存 Set）
├── 5. 消息过滤
│   ├── 仅处理 text 类型
│   ├── 仅处理私聊
│   └── 忽略机器人自身消息
├── 6. 立即返回 200（异步处理）
└── 7. 后台任务：调用 Agent 并回复
```

**验收标准**：
- [ ] 飞书后台 Webhook 配置验证通过
- [ ] 私聊消息可正常接收
- [ ] 重复事件被正确过滤
- [ ] 消息处理异步执行，Webhook 响应 < 1s

---

### Task 3.2 MCP Client 封装

**目标**：封装调用 MCP Server 的客户端

**开发文件**：
```
agent/feishu-agent/src/mcp/
├── __init__.py
└── client.py              # MCP 调用封装
```

**核心接口**：

```python
class MCPClient:
    async def call_tool(self, tool_name: str, params: dict) -> dict
    async def list_tools(self) -> list
```

**验收标准**：
- [ ] 可正常调用 MCP Server 工具
- [ ] 错误响应正确解析
- [ ] 超时处理正常

---

### Task 3.3 会话管理

**目标**：实现基于 user_id 的会话上下文管理

**开发文件**：
```
agent/feishu-agent/src/agent/
├── __init__.py
└── session.py             # 会话管理
```

**核心逻辑**：

```
SessionManager
├── 存储：内存 Dict
├── Key：feishu_user_id
├── Value：
│   ├── messages: list（最近 N 轮）
│   ├── created_at: datetime
│   └── last_active: datetime
├── 配置：
│   ├── max_rounds: 5
│   └── ttl_minutes: 30
└── 方法：
    ├── get_or_create(user_id) -> Session
    ├── add_message(user_id, role, content)
    ├── get_context(user_id) -> list
    └── cleanup_expired()
```

**验收标准**：
- [ ] 同一用户多轮对话上下文保持
- [ ] 超过 5 轮自动截断旧消息
- [ ] 30 分钟无活动会话自动清理

---

### Task 3.4 LLM 调用封装

**目标**：封装 LLM 调用，支持多 Provider

**开发文件**：
```
agent/feishu-agent/src/llm/
├── __init__.py
├── client.py              # 统一调用接口
└── provider.py            # 多模型适配
```

**支持的 Provider**：

| Provider | 模型 | 优先级 |
|----------|------|--------|
| OpenAI | gpt-4o-mini | 主选 |
| Anthropic | claude-3-5-sonnet | 备选 |
| Deepseek | deepseek-chat | 备用/降级 |

**核心接口**：

```python
class LLMClient:
    async def chat(
        self,
        messages: list,
        tools: list = None,      # 工具定义
        tool_choice: str = "auto"
    ) -> LLMResponse
```

**验收标准**：
- [ ] 可正常调用配置的 LLM
- [ ] 支持 Function Calling / Tool Use
- [ ] 超时和错误处理正常

---

### Task 3.5 Agent 核心编排

**目标**：实现 Agent 主循环，协调 LLM 与工具调用

**开发文件**：
```
agent/feishu-agent/src/agent/
├── core.py                # Agent 编排核心
└── prompt.py              # Prompt 模板
```

**编排流程**：

```
Agent 执行流程
├── 1. 获取用户会话上下文
├── 2. 构建 System Prompt（含工具说明）
├── 3. 调用 LLM
├── 4. 判断响应类型
│   ├── 文本回复 → 直接返回
│   └── 工具调用 → 执行工具
├── 5. 工具执行
│   ├── 调用 MCP Client
│   └── 获取工具结果
├── 6. 将工具结果返回 LLM
├── 7. 生成最终回复
└── 8. 更新会话上下文
```

**System Prompt 要点**：

```
你是一个法律事务助手，帮助律师查询案件信息。

## 能力
- 查询诉讼案件（开庭日期、案号、当事人等）
- 搜索法律文档

## 工具使用
- 查询案件：使用 feishu.v1.bitable.search
- 搜索文档：使用 feishu.v1.doc.search

## 时间理解
- "今天"：{today}
- "本周"：{week_start} 至 {week_end}

## 输出格式
- 简洁、结构化
- 包含关键字段和链接
- 无结果时礼貌告知，不要编造
```

**验收标准**：
- [ ] 用户问题可触发正确的工具调用
- [ ] 工具结果可正确解析并生成回复
- [ ] 多轮对话上下文连贯

---

### Task 3.6 时间解析工具

**目标**：解析自然语言时间表达为日期范围

**开发文件**：
```
agent/feishu-agent/src/utils/
└── time_parser.py         # 时间解析
```

**支持的表达式**：

| 输入 | 输出（date_from, date_to） |
|------|---------------------------|
| 今天 | 今天 00:00, 今天 23:59 |
| 明天 | 明天 00:00, 明天 23:59 |
| 本周 | 本周一 00:00, 本周日 23:59 |
| 下周 | 下周一 00:00, 下周日 23:59 |
| 下周一 | 下周一 00:00, 下周一 23:59 |
| 这个月 | 本月 1 号, 本月最后一天 |
| 1月28号 | 指定日期 00:00 - 23:59 |

**实现方式**：LLM 负责解析，工具辅助验证

**验收标准**：
- [ ] 常见时间表达式正确解析
- [ ] 返回 ISO 格式日期字符串

---

### Task 3.7 消息回复

**目标**：将 Agent 回复发送到飞书私聊

**开发文件**：
```
agent/feishu-agent/src/api/
└── webhook.py             # 补充回复逻辑
```

**回复格式（Phase 1 纯文本）**：

```
📅 本周庭审安排（共 2 场）

1️⃣ 张三 vs 李四 | 合同纠纷
   • 案号：（2025）粤0306民初123号
   • 时间：2026-01-28 09:00
   • 法院：深圳市宝安区人民法院
   • 🔗 查看详情：https://xxx.feishu.cn/base/...

2️⃣ 王五 vs 赵六 | 劳动争议
   • 案号：（2025）粤0306民初456号
   • 时间：2026-01-30 14:00
   • 法院：深圳市中级人民法院
   • 🔗 查看详情：https://xxx.feishu.cn/base/...
```

**验收标准**：
- [ ] 消息可正常发送到用户私聊
- [ ] 链接可点击跳转
- [ ] 格式清晰易读

---

### Task 3.8 Feishu Agent 单元测试

**目标**：核心功能测试覆盖

**开发文件**：
```
agent/feishu-agent/tests/
├── conftest.py
├── test_webhook.py        # Webhook 处理测试
├── test_session.py        # 会话管理测试
├── test_agent.py          # Agent 编排测试
└── test_time_parser.py    # 时间解析测试
```

**验收标准**：
- [ ] Webhook 验签测试通过
- [ ] 会话管理逻辑测试通过
- [ ] Agent 编排测试通过（mock LLM 和 MCP）

---

## Phase 4：集成联调与部署

### Task 4.1 本地联调

**目标**：本地环境端到端跑通

**步骤**：
1. 启动 MCP Server（端口 8081）
2. 启动 Feishu Agent（端口 8080）
3. 使用 ngrok 暴露本地端口
4. 更新飞书 Webhook 地址
5. 私聊机器人测试

**测试用例**：

| 输入 | 预期 |
|------|------|
| "你好" | 问候回复 |
| "这周有什么庭" | 返回本周庭审列表 |
| "（2025）粤0306民初123号案件详情" | 返回单条记录详情 |
| "找一下关于XX的文档" | 返回文档列表 |

**验收标准**：
- [ ] 所有测试用例通过
- [ ] 响应时间 < 10s
- [ ] 错误场景返回友好提示

---

### Task 4.2 Docker 镜像构建

**目标**：构建生产环境 Docker 镜像

**开发文件**：
```
mcp/mcp-feishu-server/Dockerfile
agent/feishu-agent/Dockerfile
omniagent/docker-compose.yml
```

**MCP Server Dockerfile**：
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY config.yaml.example ./config.yaml
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8081"]
```

**docker-compose.yml**：
```yaml
version: '3.8'
services:
  mcp-feishu:
    build: ./mcp/mcp-feishu-server
    ports:
      - "8081:8081"
    env_file:
      - ./mcp/mcp-feishu-server/.env
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M

  feishu-agent:
    build: ./agent/feishu-agent
    ports:
      - "8080:8080"
    env_file:
      - ./agent/feishu-agent/.env
    environment:
      - MCP_SERVER_BASE=http://mcp-feishu:8081
    depends_on:
      - mcp-feishu
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M
```

**验收标准**：
- [ ] 镜像构建成功
- [ ] `docker-compose up` 可正常启动
- [ ] 容器间通信正常

---

### Task 4.3 VPS 部署

**目标**：部署到生产环境 VPS

**步骤**：
1. VPS 环境准备（Docker、Docker Compose）
2. 配置域名与 HTTPS（Let's Encrypt）
3. 上传代码或配置 Git 拉取
4. 创建 `.env` 文件（生产配置）
5. 启动服务
6. 更新飞书 Webhook 地址为生产域名
7. 验证服务正常

**验收标准**：
- [ ] 服务稳定运行
- [ ] HTTPS 访问正常
- [ ] Webhook 接收正常
- [ ] 私聊问答功能正常

---

### Task 4.4 日志与监控

**目标**：基础可观测性

**内容**：
1. 结构化日志输出（JSON 格式）
2. 日志持久化（挂载 volume）
3. 关键指标日志：
   - 请求量
   - 响应时间
   - 错误率
   - LLM 调用耗时/Token 数

**验收标准**：
- [ ] 日志可查询
- [ ] 错误可追溯（trace_id）

---

## Phase 5：优化与验收

### Task 5.1 异常处理完善

**目标**：完善各类异常场景处理

| 场景 | 处理 |
|------|------|
| 飞书 API 失败 | 返回"数据获取失败，请稍后重试" |
| LLM 调用超时 | 返回"思考超时，请简化问题重试" |
| 无搜索结果 | 返回"未找到相关记录" |
| MCP Server 不可用 | 返回"服务暂时不可用" |

**验收标准**：
- [ ] 所有异常场景有友好提示
- [ ] 服务不崩溃
- [ ] 错误日志完整

---

### Task 5.2 性能优化

**目标**：确保 1C2G 下稳定运行

**优化项**：
1. 并发限制（最大 3 并发）
2. 请求超时设置（30s）
3. 内存监控与告警
4. 定期清理过期会话

**验收标准**：
- [ ] 3 并发请求正常响应
- [ ] 内存稳定在 1.5G 以下
- [ ] 无 OOM 崩溃

---

### Task 5.3 最终验收

**验收用例**：

| 场景 | 输入 | 预期输出 | 状态 |
|------|------|----------|------|
| 基础问候 | 你好 | 问候回复 | [ ] |
| 本周庭审 | 这周有什么庭要开 | 返回本周庭审列表 + 链接 | [ ] |
| 下周庭审 | 下周的庭审安排 | 返回下周庭审列表 | [ ] |
| 条件查询 | 在中级法院的案件 | 精确过滤结果 | [ ] |
| 案件详情 | （2025）粤0306民初123号 | 返回完整案件信息 | [ ] |
| 文档搜索 | 找一下关于XX的文档 | 返回文档列表 + 链接 | [ ] |
| 空结果 | 明年的庭审安排 | 友好提示无记录 | [ ] |
| 异常场景 | （模拟 API 故障） | 返回可读错误 | [ ] |
| 多轮对话 | 上一个案件的法官是谁 | 基于上文回答 | [ ] |

**验收标准**：
- [ ] 所有用例通过
- [ ] 响应时间 ≤ 10s
- [ ] 连续运行 24h 无崩溃

---

## 任务检查清单

```
Phase 1：项目初始化
  [ ] Task 1.1 飞书应用创建与配置
  [ ] Task 1.2 项目目录结构初始化
  [ ] Task 1.3 开发环境配置

Phase 2：MCP Server 开发
  [ ] Task 2.1 飞书 API Client 封装
  [ ] Task 2.2 多维表格工具开发
  [ ] Task 2.3 文档搜索工具开发
  [ ] Task 2.4 MCP Server HTTP 接口
  [ ] Task 2.5 MCP Server 单元测试

Phase 3：Feishu Agent 开发
  [ ] Task 3.1 飞书 Webhook 接入
  [ ] Task 3.2 MCP Client 封装
  [ ] Task 3.3 会话管理
  [ ] Task 3.4 LLM 调用封装
  [ ] Task 3.5 Agent 核心编排
  [ ] Task 3.6 时间解析工具
  [ ] Task 3.7 消息回复
  [ ] Task 3.8 Feishu Agent 单元测试

Phase 4：集成联调与部署
  [ ] Task 4.1 本地联调
  [ ] Task 4.2 Docker 镜像构建
  [ ] Task 4.3 VPS 部署
  [ ] Task 4.4 日志与监控

Phase 5：优化与验收
  [ ] Task 5.1 异常处理完善
  [ ] Task 5.2 性能优化
  [ ] Task 5.3 最终验收
```
