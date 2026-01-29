# OmniAgent 故障说明书

本文记录近期排查过的关键问题与解决方案，便于后续快速审查和复现。

## 1) 飞书多维表格日期查询“无结果”

### 现象
- 使用日期范围查询（如 `2026-01-28`）返回空记录或 `total` 异常
- UI 中“开庭日”已填写，但 API 查询不命中

### 根因
1. 时间戳时区偏差
   - 飞书返回的是毫秒时间戳（UTC）
   - 直接用 `datetime.fromtimestamp()` 会导致日期偏移
2. 关键词包含日期导致过滤为空
   - 例如“1月28号有什么庭要开”被当作关键词过滤

### 解决方案
1. 时间戳统一按 UTC+8 转换
   - 文件：`mcp/mcp-feishu-server/src/tools/bitable.py`
   - 函数：`_format_timestamp` / `_parse_date_text`
2. 日期提问时移除关键词中的日期片段
   - 文件：`agent/feishu-agent/src/agent/core.py`
   - 逻辑：`_strip_date_tokens`

### 验证
```bash
curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.search \
  -H "Content-Type: application/json" \
  -d "{\"params\": {\"date_from\": \"2026-01-28\", \"date_to\": \"2026-01-28\"}}"
```
预期：返回包含该日期记录的 `records`。

---

## 2) Docker 无法启动 / 连接失败

### 现象
- `docker compose up` 报错：`open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`
- 或提示 `the attribute version is obsolete`

### 根因
- Docker Desktop 未启动或未连接 Linux 引擎
- `version` 字段已被 Compose 忽略（不影响功能）

### 解决方案
- 启动 Docker Desktop，确认引擎运行
- 可删除 `docker-compose.yml` 中 `version` 字段

---

## 3) Bitable 403 Forbidden

### 现象
- 调用 `bitable.records.search` 返回 403

### 根因
1) app_token 错误（wiki token ≠ base app_token）
2) 应用身份未配置数据范围（bitable:app:readonly）

### 解决方案
- 从 Base 链接获取 app_token：`https://<domain>.feishu.cn/base/appXXXX?table=...`
- 在飞书开放平台为应用身份配置可访问的数据范围

---

## 4) FieldNameNotFound / InvalidSort

### 现象
- `FieldNameNotFound` 或 `InvalidSort` 报错

### 根因
- 请求中使用了不存在的字段名或排序字段

### 解决方案
- 先读取字段列表（`/fields`）再过滤/排序
- 兼容字段名空格差异（如 `对方当 事人`）

---

## 5) record_url 打开提示“记录已删除”

## 2) LLM 调用 400（SiliconFlow）

### 现象
- 日志出现：`POST https://api.siliconflow.cn/v1/chat/completions "HTTP/1.1 400 Bad Request"`

### 根因
1. 容器内配置未生效（仍在使用默认模型）
2. 模型名或权限不匹配

### 解决方案
1. 明确设置模型与 API Base
   - 文件：`agent/feishu-agent/.env`
   - 示例：
     ```
     LLM_API_BASE=https://api.siliconflow.cn/v1
     LLM_MODEL=internlm/internlm2_5-7b-chat
     ```
2. 重新构建并重启 feishu-agent
   - 让容器加载最新 env 和代码

### 验证
```bash
docker compose exec feishu-agent env | findstr LLM
```
预期：看到 `LLM_MODEL` 和 `LLM_API_BASE` 为期望值。

---

## 3) record_url 打开提示“记录已删除”

### 现象
- `record_url` 打开提示记录不存在

### 根因
- 链接中携带了不正确的 `view_id`（视图过滤导致记录不可见）

### 解决方案
- 置空 `BITABLE_VIEW_ID`
  - 文件：`.env`、`mcp/mcp-feishu-server/.env`
- 生成链接时不携带 `view` 参数

### 验证
`record_url` 应类似：
```
https://<domain>.feishu.cn/base/<app_token>?table=<table_id>&record=<record_id>
```

---

## 6) 关键词查询返回空

### 现象
- 输入“找一下李四的案子”返回空

### 根因
- 关键词提取未剔除口语词（如“找一下/案子/的”）

### 解决方案
- 在关键词提取中移除口语词与“的”
  - 文件：`agent/feishu-agent/src/agent/core.py`
  - 方法：`_extract_keyword`

### 验证
输入“找一下李四的案子”应命中记录。

---

## 7) Webhook 重复回调导致多次回复

### 现象
- 同一条消息多次回复

### 根因
- 飞书回调重试或多实例导致重复处理

### 解决方案
- 去重逻辑使用 `message_id` 优先、`event_id` 兜底
  - 文件：`agent/feishu-agent/src/api/webhook.py`

---

## 8) LLM 调用 400 / Invalid token

### 现象
- `chat/completions 400 Bad Request`
- `Invalid token` / `The parameter is invalid`

### 根因
1) 使用了错误的模型名或模型无权限
2) 容器未加载最新 env 配置

### 解决方案
- 使用可用模型（如 `internlm/internlm2_5-7b-chat`）
- 在容器内确认环境变量
  ```bash
  docker compose exec feishu-agent env | findstr LLM
  ```
- 重新构建并重启服务

---

## 9) Webhook 模拟请求 500 / JSONDecodeError

### 现象
- `JSONDecodeError: Extra data`

### 根因
- curl 请求体转义不正确

### 解决方案
- 使用文件方式发送
  ```bash
  curl -X POST http://localhost:8080/feishu/webhook \
    -H "Content-Type: application/json" \
    --data-binary @payload.json
  ```

## 常用排查命令

```bash
# MCP 健康检查
curl http://localhost:8081/health

# MCP 工具列表
curl http://localhost:8081/mcp/tools

# 查询单条记录
curl -X POST http://localhost:8081/mcp/tools/feishu.v1.bitable.record.get \
  -H "Content-Type: application/json" \
  -d "{\"params\": {\"record_id\": \"<record_id>\"}}"

# 查看服务日志
docker compose logs --tail=200 feishu-agent
docker compose logs --tail=200 mcp-feishu-server
```
