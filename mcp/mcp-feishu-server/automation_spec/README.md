# 自动化设计评审文档

状态：评审中（设计已完成，运行时未启用）

本文档为自动化模块的“可直接评审”版本，包含目标、边界、架构、流程、接口草案、数据模型、幂等策略与上线方案。

## 1. 目标

在不依赖飞书内置自动化的前提下，实现“表格字段变化 -> 条件判断 -> 执行动作”的可控流程。

## 2. 范围

包含：

- 事件接入设计（`bitable_record_changed` / `bitable_field_changed`）
- 快照对比与规则匹配
- 动作执行（写表、建日历、发消息）
- 幂等、重试、轮询补偿

不包含：

- Phase B/C 的完整规则动作链落地
- 生产环境监控平台建设（仅定义埋点与日志字段）

## 3. 目录与关联文件

- `rules.template.yaml`：规则模板（仅示例，不被系统加载）
- `events.sample.json`：飞书事件样例（字段待以真实抓包为准）
- `fields.yaml`：表字段约定与自动化状态字段规范
- `todo.md`：实施任务拆解与验收项

### 3.1 v1.1 文件解读顺序（建议）

为加快评审，建议按下面顺序阅读：

1. 先看 `events.sample.json` 的 `contracts`：确认事件类型、必需字段与鉴权约定
2. 再看 `rules.template.yaml`：确认触发条件、动作链、状态写回与幂等边界
3. 最后看 `fields.yaml` 的 `validation`：确认字段存在性、状态枚举与规则依赖关系

补充：看完以上三步后，再回到 `todo.md` 对照实施阶段与验收项。

## 4. 架构决策

- 模式：`hybrid`（事件订阅主、轮询补偿辅）
- 核心组件：
  - Event Ingress（事件入口）
  - Snapshot Store（快照）
  - Rule Engine（规则匹配）
  - Action Runner（动作执行）
  - Idempotency Store（幂等）
  - Poller（补偿扫描）
- 凭证边界：
  - testA：数据读写（bitable、calendar）
  - testB：消息收发（IM）

## 5. 关键流程（评审版）

1) 接收事件或轮询发现变更
2) 提取 `table_id/record_id`
3) 拉取记录最新字段
4) 读取本地快照
5) 计算 `old -> new` 差异
6) 匹配规则
7) 写入 `自动化_执行状态=处理中`
8) 执行动作链
9) 成功写 `自动化_执行状态=成功`，失败写 `自动化_执行状态=失败` + `自动化_最近错误`
10) 更新快照与幂等键

## 6. 伪代码（核心路径）

```python
def handle_record_changed(event):
    ids = extract_ids(event)
    record = fetch_record(ids)
    old = snapshot.load(ids.table_id, ids.record_id)
    if old is None:
        snapshot.save(ids, record.fields)
        return "initialized"

    changes = diff(old.fields, record.fields)
    if not changes:
        snapshot.save(ids, record.fields)
        return "no_change"

    rules = match_rules(ids.table_id, changes, old.fields, record.fields)
    if not rules:
        snapshot.save(ids, record.fields)
        return "no_rule"

    set_status(ids, "处理中", "")
    errors = []
    for rule in rules:
        if is_duplicate_business(rule, ids, changes):
            continue
        result = run_actions(rule, record)
        if result.ok:
            mark_business_done(rule, ids, changes)
        else:
            errors.extend(result.errors)

    if errors:
        set_status(ids, "失败", join_errors(errors))
    else:
        set_status(ids, "成功", "")

    snapshot.save(ids, refetch_record(ids).fields)
    mark_event_done(event)
    return "triggered"
```

## 7. 接口（设计草案）

### 7.1 事件入口

- `POST /feishu/events`（预留路径，待实现）
  - 功能：接收飞书事件，支持 `url_verification`
  - 输入：`events.sample.json` 结构
  - 输出：`{"status":"ok"}` / `{"challenge":"..."}`

### 7.2 调试入口

- `POST /automation/init`（预留调试入口，待实现）
  - 功能：初始化快照（首次）
- `POST /automation/scan?table_id=...`（预留调试入口，待实现）
  - 功能：手动触发一次补偿扫描

## 8. 数据模型草案

- 快照：`snapshot.json`
  - `table_id -> record_id -> {fields, updated_at}`
- 幂等：`idempotency.json`
  - 事件级键：`event_id`
  - 业务级键：`record_id + table_id + field_hash`
- 游标：`checkpoint.json`
  - `table_id -> last_scan_cursor`

## 9. 字段规范

- `自动化_执行状态`：`处理中 | 成功 | 失败`
- `自动化_最近错误`：最后一次失败原因（文本）

## 10. 上线与回滚策略

上线：

1. `dry-run`（仅日志）
2. 单规则灰度
3. 指标观察（命中率、失败率、重复率）
4. 扩大规则范围

回滚：

- 一键关闭自动化开关（不删除规则）
- 保留快照与幂等数据供复盘

## 11. 评审检查清单

- [ ] 事件字段样例已由真实抓包确认
- [ ] 规则模板字段命名统一
- [ ] 双凭证边界确认（testA/testB）
- [ ] 幂等键策略确认
- [ ] 失败重试与死信策略确认
- [ ] 上线灰度与回滚策略确认

## 12. 约定

- 当前仓库仅保留自动化设计文档，不启用运行时自动化入口
- 进入 Phase B 前，先完成规则模板评审并确认字段契约
