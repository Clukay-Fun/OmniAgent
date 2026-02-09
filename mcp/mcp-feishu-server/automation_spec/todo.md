# 自动化实施任务单 v1.1（评审版）

状态：开发中（Phase A/B/C 核心已落地）

## 0. 评审结论门槛

- [ ] README（评审文档）已通过评审
- [ ] `rules.template.yaml` v1.1 已通过评审
- [ ] 事件样例字段已抓包确认（以真实回调为准）
- [ ] 凭证边界已确认（testA/testB）

## 1. Phase A - 事件与存储底座

目标：具备“可接收、可对比、可去重”的最小闭环

- [x] A1 新增事件入口 `/feishu/events`
  - [x] 支持 `url_verification`
  - [x] 支持 token 校验
  - [x] 可选支持 encrypt 解密
- [x] A2 新增快照模块 `snapshot`
  - [x] `load/save/diff/init_full_snapshot`
- [x] A3 新增幂等模块 `store`
  - [x] `event_key` 去重
  - [x] `business_key` 去重
- [x] A4 新增 checkpoint 模块
  - [x] 首次初始化游标
  - [x] 增量扫描游标

交付标准（DoD）：

- [x] 单测覆盖 snapshot diff 与去重逻辑
- [x] 首次初始化不触发业务动作

## 2. Phase B - 规则与动作引擎

目标：在字段变化时稳定触发动作链

- [x] B1 规则加载与匹配（rules + engine）
  - [x] 支持 `changed/equals/in/any_field_changed`
  - [x] 支持 `exclude_fields`
- [x] B2 动作执行器（actions）
  - [x] `log.write`
  - [x] `bitable.update`
  - [x] `calendar.create`（可开关）
- [x] B3 状态可观测
  - [x] `自动化_执行状态`（处理中/成功/失败）
  - [x] `自动化_最近错误`

交付标准（DoD）：

- [x] 命中规则后可见“处理中 -> 成功/失败”
- [x] 失败信息可直接在表中查看

## 3. Phase C - 补偿与稳定性

目标：事件丢失/重放情况下仍可最终一致

- [x] C1 轮询补偿器 `poller`
  - [x] 固定间隔扫描
  - [x] 与事件模式共存（hybrid）
- [x] C2 错误重试与死信（可选）
  - [x] 动作级重试
  - [x] 死信记录

交付标准（DoD）：

- [x] 事件丢失时可由轮询补偿恢复
- [x] 重复事件不重复执行动作

## 4. 回归与验收

- [ ] R1 单规则命中：`案件分类 -> 劳动争议`
- [ ] R2 任意字段规则（排除自动化字段）
- [ ] R3 幂等校验（重复事件）
- [ ] R4 失败路径（权限/字段不存在）
- [ ] R5 回滚开关验证（停用自动化）

## 5. 运行与运维清单

- [ ] 发布前：`dry-run` 至少 1 天
- [ ] 灰度：仅开启 1 条规则
- [ ] 观察：命中率、失败率、重复率
- [ ] 稳定后逐步扩展规则

## 6. 风险与决策待办

- [ ] 是否保留 `calendar.create`（当前建议可开关）
- [ ] 是否引入消息动作（需明确 testB 调用边界）
- [ ] 数据存储选型：json vs sqlite vs postgres
