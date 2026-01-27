# universal-rag
# 通用 RAG 智能体平台协作指南

## 项目定位
- 目标：**通用 RAG 内核**，支持多智能体 (Agent) 动态接入各自数据源
- 关键体验：SSE 实时展示 Agent 状态
- 默认服务地址：http://localhost:8001/

## 架构概述
```
Universal RAG Core
├── 通用表 (documents, document_nodes, chat_*)
├── 智能体配置 (agents, agent_datasources)
├── 通用工具 (search_knowledge_base)
└── 动态数据源连接机制
    ▼ 外接
  投标智能体 DB / 法律智能体 DB / 其他智能体 DB
```

## 模块与功能
- 文档解析：Word -> MarkItDown，保留结构
- 知识库：AI 解析章节层级，生成 Node 树
- 向量索引：pgvector 存储，BGE-M3 嵌入
- RAG 问答：引用来源的智能问答
- 智能体管理：多智能体配置与数据源隔离
- 数据库存储：PostgreSQL + pgvector 一体化

## 固定依赖（必须使用）
- 文档解析：MarkItDown、python-docx
- 向量化：BAAI/bge-m3（硅基流动 API）
- 重排序：BAAI/bge-reranker-v2-m3
- JSON 提取/标书解析：Qwen3-8B
- 视觉识别：GLM-4.1V-Thinking
- 规划与复杂推理：DeepSeek-R1-0528-Qwen3-8B
- 向量/关系数据库：PostgreSQL + pgvector
- 框架：LlamaIndex
- 对话模型：internlm/internlm2_5-7b-chat
- Web 框架：FastAPI

## 环境变量与密钥管理
- 必填：`DATABASE_URL`（PostgreSQL 连接串）
- 服务：`FASTAPI_HOST`、`FASTAPI_PORT`
- 模型：`MODEL_API_BASE_URL`、`MODEL_API_KEY`
- 日志：`LOG_LEVEL`
- 本地开发使用 `.env`，生产环境使用受控的 Secret 管理系统
- 禁止提交真实密钥；仅保留 `.env.example`
- 避免在日志、错误信息、追踪中输出密钥或敏感字段
- 不同环境使用不同密钥，定期轮换

## 常见问题排查
- `password authentication failed for user "postgres"`：通常是进程读取到的 `DATABASE_URL` 不是 `.env` 中的最新值。
  - Windows/PowerShell 里若系统环境变量已设置，会覆盖 `.env`；建议在入口 `load_dotenv(override=True)` 并重启服务。
  - 核对当前进程读取到的值：`python -c "import os; from dotenv import load_dotenv; load_dotenv(override=True); print(os.getenv('DATABASE_URL'))"`
  - 连接验证（PowerShell）：`psql -c "select 1" $env:DATABASE_URL`（注意 `-c` 在 URL 前）

## 单测运行（占位符模板）
- Pytest：pytest path/to/test.py::TestClass::test_name
- 单文件：pytest path/to/test.py
- 过滤用例：pytest -k "keyword"
- 其他框架：<single-test-command>  # 待补充

## Agent Loop (ReAct 实现)
- 核心逻辑：`Think-Act-Observe` 循环 (`services/agent_service.py`)
- 状态机流转：
  - `THINKING`: 规划下一步或分析结果
  - `EXECUTING`: 调用具体工具 (RAGSearch)
  - `DONE`: 生成最终回答
  - `ERROR`: 异常捕获与熔断
- 安全机制：
  - **死循环检测**：禁止连续使用相同参数调用同一工具
  - **最大步数**：默认 10 步，超时自动停止
  - **速率限制**：单步延时防止 API 429/403

## 工具系统 (Registry 模式)
- 基础设施：`services/tool_registry.py`
  - `BaseTool`: Pydantic 输入验证，统一 run 接口
  - `ToolRegistry`: 装饰器 `@register_tool` 自动注册
- 已实现工具：
  - `search_knowledge_base`: RAG 向量检索知识库
- 扩展指南：
  1. 继承 `BaseTool`
  2. 使用 `@register_tool` 装饰
  3. 实现 `run` 方法 (返回字符串结果)

## RAG 知识库链路
- Markdown 文档解析与分块
- Embedding 生成并落库
- pgvector 相似度检索（<->）
- ivfflat 索引优化，支持 10 万+ 向量
- 结构化与向量数据同库一致性

## SSE 实时通信
- SSE 推送 Agent 状态变化
- 前端/终端可实时看到执行过程
- 简单可靠的单向推送模型

## 对话会话（SSE + 持久化）
- 会话表：chat_sessions（UUID 主键、title、message_count、时间戳）
- 消息表：chat_messages（role/content/citations/token_count）
- SSE 接口采用 POST：
  - POST /chat/sessions
  - POST /chat/sessions/{session_id}/messages  # SSE 输出
  - GET /chat/sessions/{session_id}/history
- SSE 事件类型：status / chunk / message / done / error
- 历史读取：先查 20 条，再按 2000 字符阈值截断（保持消息完整）
- 引用落库：document_id / node_id / filename / preview / score / path
- CLI 展示引用建议：filename + preview + score（不展示 node_id）

## 开发约定（强制）
- Python 3.11+；依赖管理：requirements.txt
- 每个 Python 文件首行添加三引号注释
- 目录分层：api/、services/、db/、schemas/
- 类型提示必填；禁止无类型返回
- 避免全局 Session；用依赖注入或上下文管理
- 数据库存储：PostgreSQL + pgvector
- 默认向量维度 1024，余弦相似度
- 不使用 Redis；缓存暂缓

## 格式与导入
- 优先使用项目格式化工具
- 行长度目标 100-120 字符
- 导入分组：标准库 / 第三方 / 本地
- 组内按字母排序，禁止未使用导入

## 命名与类型
- 命名清晰且领域优先
- bool 命名使用谓词形式
- 公开 API 与导出符号必须显式类型
- 避免 any / object 等过宽类型

## 错误处理与日志
- 不吞异常，必须明确处理
- 错误信息包含上下文（输入、阶段、标识）
- 禁止抛出裸字符串
- 日志简洁，避免敏感信息

## 函数与结构
- 单一职责，保持小函数
- 复杂参数使用对象参数
- 采用 guard clause 提前返回
- 注释仅用于非显而易见逻辑

## region 注释规范
```python
# ============================================
# region 区域名称
# ============================================

# 这里是代码...

# endregion
# ============================================
```

## 函数/类注释模板
```python
def function_name(param: str) -> dict:
    """
    函数功能简述

    参数:
        param: 参数说明
    返回:
        返回值说明
    """
    pass
```

## 文件头模板（每个 Python 文件必须）
```python
"""
描述: 简述职责
主要功能:
    - 功能1
    - 功能2
依赖: 关键依赖
"""
```

## 数据库与索引规范
- 使用 PostgreSQL + pgvector 一体化存储
- 结构化数据与向量数据保持事务一致
- 索引：ivfflat；根据数据规模调整 lists
- 查询：使用 <-> 作为相似度排序

## 更新清单（仓库完善后）
- 标注模型版本与调用额度限制

## 验证与使用指南 (Walkthrough)
### 1. 启动 CLI 对话
```bash
python -m cli.main chat
```

### 2. 测试场景
- **RAG 检索**：
  > "查找关于诉讼的合同条款"
  - 预期：调用 `search_knowledge_base` -> 返回条款细节。

- **混合推理**：
  > "查找最近的诉讼合同，并总结它们的特点"
  - 预期：先检索，后总结。

### 3. 故障排查
- 如果遇到 `403/400 Error`，请检查 `.env` 中的 Embedding 模型配置。
- 如果 CLI 卡住，输入 `/exit` 退出。
