# 自动化设计评审文档 v1.2

状态：已与当前实现对齐（2026-02）

本文档描述 `mcp-feishu-server` 自动化模块的当前实现方法，重点覆盖：

- watched_fields 自动提取（按规则字段最小化拉取）
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
- 规则匹配：`changed/equals/in/any_field_changed/exclude_fields`
- 动作执行：`log.write`、`bitable.update`、`calendar.create`
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
2. 获取 `table_id/record_id`
3. 计算 watch 计划（字段模式/全字段模式）
4. 按 watch 计划拉取记录字段
5. 与快照做 diff
6. 匹配启用规则
7. 执行动作链（含重试）
8. 写 `run_logs.jsonl`
9. 失败写 `dead_letters.jsonl`
10. 更新快照与幂等键

## 6. 接口

- `POST /feishu/events`：事件入口（已实现）
- `POST /automation/init`：初始化快照（已实现）
- `POST /automation/scan`：手动补偿扫描（已实现）

## 7. 数据文件

- `snapshot.json`：快照
- `idempotency.json`：事件级/业务级去重键
- `checkpoint.json`：扫描游标
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

## 9. run_logs 单条结构（固定）

成功样例：

```json
{
  "timestamp": "2025-01-15T10:30:00.123Z",
  "event_id": "evt_xxx",
  "rule_id": "R001",
  "record_id": "rec_xxx",
  "table_id": "tbl_xxx",
  "trigger_field": "案件分类",
  "changed": {"old": "民事", "new": "劳动争议"},
  "actions_executed": ["log.write", "calendar.create"],
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
  "record_id": "rec_yyy",
  "table_id": "tbl_xxx",
  "trigger_field": "案件分类",
  "changed": {"old": "民事", "new": "劳动争议"},
  "actions_executed": ["bitable.update"],
  "result": "failed",
  "error": "action bitable.update failed after 2 attempts: timeout",
  "retry_count": 1,
  "sent_to_dead_letter": true,
  "duration_ms": 15234
}
```

## 10. 灰度建议

- 有 API 压力时：`python automation_gray_check.py --no-api --strict`
- 常规灰度：`python automation_gray_check.py --hours 24 --strict --json`

## 11. 待办

- run_logs 文件轮转与保留策略（按天/按大小）
- run_logs + dead_letters 统一查询脚本增强
