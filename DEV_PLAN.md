# OmniAgent 飞书 Agent 开发计划

## 目标与范围

目标
- 以私聊机器人形式提供对话式问答
- 自动检索飞书多维表格与文档，生成结构化答案
- 低成本 VPS 部署（1 核 2G），Docker 方式运行

范围内
- 私聊消息接入、会话映射与答复
- MCP Server 统一封装飞书数据访问工具
- OmniAgent 负责意图解析、工具编排与答案生成
- 文件/图片上传下载 MCP 工具（后续阶段）

范围外（当前阶段）
- 用户级权限隔离（暂不做）
- 本地或云端向量索引（暂不使用 Postgres + pgvector）
- 群聊、多租户、复杂审批流

## 关键设计决策

- 访问方式：应用级访问（tenant app access）
- 交互方式：仅私聊
- 数据源：飞书多维表格 + 飞书文档
- 运行方式：VPS + Docker
- 存储策略：不落库，实时检索
- 前端交互：不单独建设 Web 前端，直接在飞书 App 内对话

## 技术选型

- 后端：Python 3.11 + FastAPI
- LLM 编排：OmniAgent（自研编排逻辑）
- MCP Server：Python（建议 FastAPI 或轻量 ASGI）
- 飞书接入：Tenant App Access（应用级）
- 部署：Docker + Docker Compose + Nginx（可选）
- 存储：不落库（Phase 1），必要时再引入 Postgres + pgvector

## 系统架构与数据流

高层架构
- 飞书私聊机器人 → Webhook 网关 → OmniAgent → MCP Server → 飞书 API

数据流（一次问答）
1. 用户私聊发送问题
2. Webhook 接收消息事件并验签
3. 解析消息文本，映射/创建 OmniAgent 会话
4. OmniAgent 调用 MCP 工具检索数据
5. 聚合检索结果并生成回答
6. 回传飞书私聊回复（可选附带引用链接）

## MCP 工具清单

第一阶段（检索能力）
- `search_bitable`
  - 输入：关键词、时间范围、过滤字段
  - 输出：记录列表（标题、时间、地点、状态、链接）
- `search_docs`
  - 输入：关键词、范围（可选文件夹）
  - 输出：文档摘要与链接
- `get_bitable_record`
  - 输入：record_id
  - 输出：完整字段详情

第二阶段（文件能力）
- `upload_file`
  - 输入：文件/图片流、文件名、类型
  - 输出：file_token 与可访问链接
- `download_file`
  - 输入：file_token
  - 输出：文件流或下载链接
- `get_file_meta`（可选）
  - 输入：file_token
  - 输出：文件元数据

统一约定
- 请求与响应均为 JSON
- 错误统一返回：`code`、`message`、`detail`
- MCP 层只做数据访问与转换，不做业务推理
- MCP 工具命名采用 `biz.version.resource.method` 模式

## 飞书机器人接入设计

应用配置
- 开启机器人能力
- 订阅私聊消息事件（message 相关事件）
- 使用应用级 token

消息处理
- 仅处理私聊消息
- 忽略机器人自身消息与系统事件
- 消息 → OmniAgent 输入

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

## OmniAgent 编排策略

- 解析意图（如时间范围、本周/下周）
- 选择 MCP 工具并执行
- 结果归一化 → 生成可读答案
- 可选：附带引用链接（表格记录/文档）

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
- `omniagent-backend`：API 与编排
- `mcp-server`：飞书数据访问
- 可选：`nginx`（反向代理与统一入口）

基础路由
- `/feishu/webhook` → Webhook 接入
- `/api` → OmniAgent API
- `/mcp` → MCP Server

运行要求
- 1 核 2G 最低可用
- 开放 HTTPS 访问（Webhook 需要公网可达）

Docker Compose 结构建议
```yaml
# docker-compose.yml 骨架
version: '3.8'
services:
  omniagent:
    build: ./omniagent
    ports:
      - "8080:8080"
    environment:
      - FEISHU_APP_ID=${FEISHU_APP_ID}
      - FEISHU_APP_SECRET=${FEISHU_APP_SECRET}
      # ...
    depends_on:
      - mcp-server
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1G

  mcp-server:
    build: ./mcp-server
    # stdio 模式下可作为 sidecar 或合并到 omniagent
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

## 配置与密钥

建议环境变量（示例名称）
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_VERIFICATION_TOKEN`
- `FEISHU_ENCRYPT_KEY`（若启用消息加密）
- `OMNIAGENT_API_BASE`
- `MCP_SERVER_BASE`

## 里程碑与交付物

里程碑 1：私聊问答闭环
- Webhook 接入
- MCP 工具：多维表格/文档检索
- OmniAgent 回答生成
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

| 场景 | 输入 | 预期输出 |
| --- | --- | --- |
| 基础查询 | 这周有什么庭要开 | 返回本周庭审列表 + 引用链接 |
| 条件过滤 | 下周一在中级法院的庭 | 精确过滤结果 |
| 空结果 | 明年的庭审安排 | 友好提示“未找到相关记录” |
| 文档搜索 | 找一下关于XX的文档 | 返回文档摘要 + 链接 |
| 异常场景 | 飞书 API 故障 | 返回可读错误，不崩溃 |
| 并发测试 | 3 人同时提问 | 均能正常响应（≤10s） |
