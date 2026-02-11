# 自动化实施任务单 v1.2

> 文件用途：记录自动化实施进度、回归项与遗留待办。
> 运行时加载：否（文档文件，不会被服务直接读取）。
> 关联文件：`README.md`（总览）、`rules.template.yaml`（模板）。
> 最后对齐：2026-02。

状态：核心能力已落地，文档与实现同步中

元信息：version=v1.2, reviewed_at=2026-02

## 0. 评审门槛

- [x] 事件入口契约与安全校验明确
- [x] 规则模板结构稳定（trigger/pipeline）
- [x] 幂等策略确认（event_key + business_key）
- [x] 失败路径可观测（run_logs + dead_letters）

## 1. Phase A（底座）

- [x] `/feishu/events`（challenge/token/encrypt）
- [x] 快照模块（load/save/diff/init）
- [x] 幂等模块（event/business）
- [x] checkpoint 模块（初始化与增量游标）

## 2. Phase B（规则与动作）

- [x] 匹配能力：`changed/equals/in/any_field_changed/exclude_fields`
- [x] 匹配能力扩展：`trigger.all`（AND）/`trigger.any`（OR）
- [x] 动作能力：`log.write` / `bitable.update` / `calendar.create`
- [x] 动作能力扩展：`bitable.upsert`（跨表 upsert）
- [x] watched_fields 自动提取
- [x] `any_field_changed` 自动全字段回退
- [x] 状态回写可切换（`status_write_enabled`）
- [x] 运行日志固定结构（`run_logs.jsonl`）

## 3. Phase C（稳定性）

- [x] 轮询补偿（poller + hybrid）
- [x] 动作级重试
- [x] 死信记录（`dead_letters.jsonl`）

## 4. 当前默认运行建议

- [x] 默认使用日志观测：`status_write_enabled=false`
- [x] 状态字段删除场景可运行（仅日志+死信）
- [x] 灰度检查支持低 API：`--no-api`

## 4.1 Schema Watcher（Webhook 版）

- [x] schema watcher 后台任务（默认 300s）
- [x] `drive.file.bitable_field_changed_v1` 事件触发即时刷新
- [x] 字段快照写入 `schema_cache.json`
- [x] 运行态状态写入 `schema_runtime_state.json`
- [x] trigger 字段删除仅运行态禁用规则
- [x] 风险变更 webhook（飞书机器人兼容 payload）

## 5. 回归清单

- [x] 单规则命中
- [x] any_field_changed 排除字段
- [x] 重复事件不重复执行
- [x] 失败重试后入死信
- [x] 无状态字段下仍可观测

## 6. 待办

- [ ] run_logs 文件轮转（按天/按大小）
- [ ] run_logs 保留策略（建议 30 天）
- [ ] 灰度脚本支持 rule_id 维度聚合报告
