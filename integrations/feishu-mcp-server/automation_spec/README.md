# 自动化设计评审文档 v1.2

> 文件用途：自动化模块总览与评审基线。
> 运行时加载：否（文档文件，不会被服务直接读取）。
> 关联文件：`../automation_rules.yaml`（运行规则）、`todo.md`（任务跟踪）、`regression-checklist.md`（回归验收）。
> 最后对齐：2026-02-15（与当前实现一致）。

状态：已与当前实现对齐（2026-02-15）

本文档描述 `mcp-feishu-server` 自动化模块的当前实现方法，重点覆盖：

- watched_fields 自动提取（按规则字段最小化拉取）
- schema watcher（5 分钟轮询 + 字段事件即时刷新）
- `status_write_enabled` 可切换状态回写
- `run_logs.jsonl` 固定结构运行日志
- 重试、死信、轮询补偿

## 1. 目标

在不依赖飞书内置自动化的前提下，实现“记录变更 -> 规则匹配 -> 动作执行 -> 可观测”的稳定链路，并尽量降低 API 消耗。

## 2. 当前实现范围

包含：

- 事件入口：`/feishu/events`（含 `url_verification`）
- 补偿入口：`/automation/init`、`/automation/scan`
- 快照、幂等、游标、轮询补偿
- 规则匹配：`on(created/updated)` + `changed/equals/in/any_field_changed/exclude_fields`
- 动作执行：`log.write`、`bitable.update`、`bitable.upsert`、`calendar.create`、`http.request`
- schema 同步：`schema_cache.json` + `schema_runtime_state.json` + 风险 webhook
- 动作重试、死信记录、运行日志

不包含：

- IM 消息动作
- run_logs 文件轮转（已列入待办）

## 3. 目录与关联文件

- `rules.template.yaml`：规则模板（仅示例）
- `../automation_rules.yaml`：运行时规则文件（系统实际加载）
- `events.sample.json`：事件契约样例
- `fields.yaml`：字段与验证约定（含状态字段可选策略）
- `todo.md`：实施进度与待办
- `regression-checklist.md`：发布前回归验收清单

## 4. 核心策略

### 4.1 watched_fields（默认最小字段）

系统启动/运行时会从启用规则自动提取关注字段：

- 触发字段：`trigger.field`
- 动作模板引用字段：如 `{委托人}`
- 日历动作字段：`start_field` / `end_field`

轮询与单条拉取优先带 `field_names`，减少传输与 diff 计算。

### 4.2 any_field_changed 安全回退

若某表存在 `any_field_changed` 规则，watch 模式自动回退为全字段，避免漏触发。

### 4.3 状态字段回写可切换

- `status_write_enabled=false`（默认）：不写 `自动化_执行状态/自动化_最近错误`
- `status_write_enabled=true`：允许状态字段回写

即使关闭状态回写，也会持续写 `run_logs.jsonl` 与 `dead_letters.jsonl`。

### 4.4 运行日志固定结构

每条规则执行（含 no_match）写一条运行日志，便于灰度与排障。

## 5. 关键流程（当前）

1. 收到事件或轮询扫描记录
2. 获取 `table_id/record_id`（字段变更事件走 schema 刷新链路）
3. 计算 watch 计划（字段模式/全字段模式）
4. 按 watch 计划拉取记录字段
5. 与快照做 diff
6. 匹配启用规则
7. 执行动作链（含重试）
8. 写 `run_logs.jsonl`
9. 失败写 `dead_letters.jsonl`
10. 更新快照与幂等键

新记录触发边界：

- 首次初始化（`/automation/init`）只建快照，不触发规则
- 事件入口是否触发新记录由 `trigger_on_new_record_event` 控制
- 轮询是否触发新记录由 `trigger_on_new_record_scan` 控制
- 若 `trigger_on_new_record_scan_requires_checkpoint=true` 且游标为 0，轮询新记录不触发

触发条件写法：

- 触发范围：`trigger.on: [created|updated]`（可选，默认两者都可）
- 单条件：`trigger.field + condition`
- 多条件 AND：`trigger.all`
- 多条件 OR：`trigger.any`

## 6. 接口

- `POST /feishu/events`：事件入口（已实现）
- `POST /automation/init`：初始化快照（已实现）
- `POST /automation/scan`：手动补偿扫描（已实现）
- `POST /automation/sync`：手动全量同步（新增+修改+删除对账）
- `POST /automation/webhook/{rule_id}`：外部 webhook 触发指定规则（需鉴权）
- `GET /automation/auth/health`：鉴权健康检查（token 获取与网络连通性）

Webhook 鉴权：

- API Key：`x-automation-key`
- 签名（可选，与 API Key 可并存）：
  - `x-automation-timestamp`（秒级时间戳）
  - `x-automation-signature`（`sha256(timestamp + "." + raw_body)` 的 hex）

字段结构同步：

- `drive.file.bitable_field_changed_v1` 到达后立即刷新对应表 schema
- 后台轮询按 `AUTOMATION_SCHEMA_SYNC_INTERVAL_SECONDS` 执行全量刷新（需 `AUTOMATION_SCHEMA_POLLER_ENABLED=true`）
- trigger 字段被删除时，规则仅运行态禁用（不改 `automation_rules.yaml`）

表来源说明：

- 规则可在 `table.app_token` 指定该表所属 app_token（可选）
- 若不填写，默认使用 `BITABLE_APP_TOKEN`

## 7. 数据文件

- `snapshot.json`：快照
- `idempotency.json`：事件级/业务级去重键
- `checkpoint.json`：扫描游标
- `schema_cache.json`：字段快照缓存（按 `app_token::table_id`）
- `schema_runtime_state.json`：运行态 schema + 规则禁用状态
- `run_logs.jsonl`：规则执行日志
- `dead_letters.jsonl`：失败死信

## 8. 关键配置

- `AUTOMATION_ENABLED`
- `AUTOMATION_POLLER_ENABLED`
- `AUTOMATION_STATUS_WRITE_ENABLED`
- `AUTOMATION_RUN_LOG_FILE`
- `AUTOMATION_DEAD_LETTER_FILE`
- `AUTOMATION_ACTION_MAX_RETRIES`
- `AUTOMATION_ACTION_RETRY_DELAY_SECONDS`
- `AUTOMATION_SYNC_DELETIONS_ENABLED`
- `AUTOMATION_SYNC_DELETIONS_MAX_PER_RUN`
- `AUTOMATION_SCHEMA_SYNC_ENABLED`
- `AUTOMATION_SCHEMA_POLLER_ENABLED`
- `AUTOMATION_SCHEMA_SYNC_INTERVAL_SECONDS`
- `AUTOMATION_SCHEMA_SYNC_EVENT_DRIVEN`
- `AUTOMATION_SCHEMA_WEBHOOK_ENABLED`
- `AUTOMATION_SCHEMA_WEBHOOK_URL`
- `AUTOMATION_SCHEMA_WEBHOOK_SECRET`
- `AUTOMATION_NOTIFY_WEBHOOK_URL`
- `AUTOMATION_NOTIFY_API_KEY`
- `AUTOMATION_NOTIFY_TIMEOUT_SECONDS`
- `AUTOMATION_WEBHOOK_ENABLED`
- `AUTOMATION_WEBHOOK_API_KEY`
- `AUTOMATION_WEBHOOK_SIGNATURE_SECRET`
- `AUTOMATION_WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS`
- `AUTOMATION_HTTP_ALLOWED_DOMAINS`
- `AUTOMATION_HTTP_TIMEOUT_SECONDS`

`http.request` 安全约束：

- URL host 必须命中 `AUTOMATION_HTTP_ALLOWED_DOMAINS`
- 禁止 localhost / 内网 IP / `.local` / `.internal`
- 超时上限 10 秒
- 响应体不落日志（仅记录状态码和少量头信息）

Schema 日志说明：

- 首次基线：写入 `schema_bootstrap`
- 有差异：写入 `schema_changed` / `schema_policy_applied`
- 无差异：写入 `schema_refresh_noop`

## 9. run_logs 单条结构（固定）

成功样例：

```json
{
  "timestamp": "2025-01-15T10:30:00.123Z",
  "event_id": "evt_xxx",
  "rule_id": "R001",
  "app_token": "app_xxx",
  "record_id": "rec_xxx",
  "table_id": "tbl_xxx",
  "rules_evaluated": ["R001", "R002"],
  "rules_matched": ["R001"],
  "trigger_field": "案件分类",
  "changed": {"old": "民事", "new": "劳动争议"},
  "actions_executed": ["log.write", "calendar.create"],
  "actions_detail": [
    {"type": "log.write", "retry_count": 0, "duration_ms": 3},
    {"type": "calendar.create", "retry_count": 0, "duration_ms": 214}
  ],
  "result": "success",
  "error": null,
  "retry_count": 0,
  "sent_to_dead_letter": false,
  "duration_ms": 342
}
```

失败样例：

```json
{
  "timestamp": "2025-01-15T10:35:00.123Z",
  "event_id": "evt_yyy",
  "rule_id": "R001",
  "app_token": "app_xxx",
  "record_id": "rec_yyy",
  "table_id": "tbl_xxx",
  "rules_evaluated": ["R001", "R002"],
  "rules_matched": ["R001"],
  "trigger_field": "案件分类",
  "changed": {"old": "民事", "new": "劳动争议"},
  "actions_executed": ["bitable.update"],
  "actions_detail": [
    {"type": "bitable.update", "retry_count": 1, "duration_ms": 15210}
  ],
  "result": "failed",
  "error": "action bitable.update failed after 2 attempts: timeout",
  "retry_count": 1,
  "sent_to_dead_letter": true,
  "duration_ms": 15234
}
```

## 10. 灰度建议

- 有 API 压力时：`python scripts/automation_gray_check.py --no-api --strict`
- 常规灰度：`python scripts/automation_gray_check.py --hours 24 --strict --json`

## 11. 待办

- run_logs 文件轮转与保留策略（按天/按大小）
- run_logs + dead_letters 统一查询脚本增强

## 12. 本次变更总结（2026-02-15）

本轮围绕“快速识别修改位置 + 及时更新目标表”完成了以下落地：

- 触发能力增强：规则支持 `trigger.on`（`created/updated`），并在服务链路中显式传递事件类型。
- 路由可观测增强：`run_logs` 新增 `rules_evaluated/rules_matched/actions_detail`，可直接定位命中规则与目标记录。
- 动作可观测增强：动作结果写入 `duration_ms`，用于快速排查慢动作。
- 规则按现网参数收敛：`automation_rules.yaml` 已拆分新增与修改触发，分别由 `R_WORKBENCH_CREATE_FYK` 与 `R_WORKBENCH_UPDATE_FYK` 负责。
- 外部触发能力：新增 `POST /automation/webhook/{rule_id}`，支持按规则 ID 精准触发。
- Webhook 安全：支持 API Key 与签名校验（时间戳容忍窗口可配置）。
- 动作扩展：新增 `http.request`，带白名单、禁内网/localhost、超时上限、响应体不落盘。
- 配置对齐：`config.yaml`、`config.yaml.example`、`.env.example` 已补齐 webhook/http 相关配置项与映射。
- 验收资产：新增 `regression-checklist.md`，覆盖新增/修改/不命中/幂等/失败恢复/可选 webhook 与 http 安全验收。
