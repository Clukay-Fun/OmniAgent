# 自动化回归验收清单 v1.0

> 用途：每次修改 `automation_rules.yaml` 或自动化代码后，快速确认“新增/修改/不命中/失败”链路正常。
> 执行方式：手工为主（5-15 分钟），可按步骤打钩。

---

## 0) 预检（1 分钟）

- [ ] 服务可用：`GET /health` 返回 200
- [ ] 自动化开启：`AUTOMATION_ENABLED=true`
- [ ] 当前规则文件是目标版本：`integrations/feishu-mcp-server/automation_rules.yaml`
- [ ] 已初始化快照：`POST /automation/init?table_id=<source_table_id>&app_token=<app_token>`

建议先准备两条源记录：

- 记录 A：用于“新增触发”
- 记录 B：用于“修改触发”

---

## 1) 新增触发（created）

目标：验证新增记录会同步到总览。

- [ ] 在源表新增 1 条记录，满足规则条件（例如协作类型在白名单内）
- [ ] 执行：`POST /automation/sync?table_id=<source_table_id>&app_token=<app_token>`
- [ ] 返回中 `status=ok`
- [ ] 返回中 `counters.initialized_triggered` 或 `counters.changed` 有增长
- [ ] 总览表出现对应记录（按 `源记录ID` 查）
- [ ] 源表回写字段符合预期（如 `已同步到总览=true`）

通过判据：总览表新增/更新成功，且运行日志里有命中规则记录。

---

## 2) 修改触发（updated）

目标：验证已同步记录更新后会同步到总览，而不是重复创建。

- [ ] 修改同一条源记录的业务字段（如 `任务状态/进度/截止时间`）
- [ ] 执行：`POST /automation/sync?table_id=<source_table_id>&app_token=<app_token>`
- [ ] 返回中 `status=ok`
- [ ] 总览表对应记录被更新（`源记录ID` 不变）
- [ ] 总览表无重复行（同一 `源记录ID` 只保留 1 条，或符合你的 `update_all_matches` 设计）

通过判据：同一锚点记录被更新，未出现意外重复。

---

## 3) 规则不命中（negative）

目标：验证条件不满足时不会误更新总览。

- [ ] 新增/修改一条不满足条件的记录（例如协作类型不在白名单）
- [ ] 执行：`POST /automation/scan?table_id=<source_table_id>&app_token=<app_token>`
- [ ] 总览表无新增/无变更
- [ ] `automation.db.run_logs` 出现 `result=no_match`

通过判据：规则过滤生效，不会误同步。

---

## 4) 幂等与重复触发

目标：验证重复扫描不会重复写入。

- [ ] 对同一份数据连续执行两次 `POST /automation/scan`（不改数据）
- [ ] 第二次执行不应产生额外业务更新
- [ ] `automation.db.run_logs` 可见 `no_match` 或 `duplicate_business` 相关结果

通过判据：重复触发被幂等保护。

---

## 5) 失败与可观测

目标：验证失败可定位、可重试。

- [ ] 人为制造一次失败（例如目标字段名写错）
- [ ] 执行 `POST /automation/sync`
- [ ] 失败写入 `automation.db.dead_letters`
- [ ] `automation.db.run_logs` 出现 `result=failed`
- [ ] 修复配置后再次执行，恢复成功

通过判据：失败有记录，修复后可恢复。

---

## 6) Webhook 触发（可选）

目标：验证外部系统可按规则 ID 精准触发。

- [ ] 配置 `AUTOMATION_WEBHOOK_ENABLED=true`
- [ ] 至少配置一项鉴权：`AUTOMATION_WEBHOOK_API_KEY` 或签名 secret
- [ ] 调用：`POST /automation/webhook/{rule_id}`
- [ ] 返回 `kind=webhook_rule_triggered`
- [ ] 命中规则并产生预期动作

通过判据：指定规则触发链路可用。

---

## 7) HTTP 动作安全（可选）

目标：验证 `http.request` 在安全边界内运行。

- [ ] 配置 `AUTOMATION_HTTP_ALLOWED_DOMAINS`
- [ ] 命中白名单域名时动作成功
- [ ] 非白名单域名被拒绝
- [ ] `localhost` / 内网 IP / `.local` / `.internal` 被拒绝
- [ ] 超时按上限（10 秒）生效

通过判据：请求可控，不会越界访问。

---

## 8) 本次发布签收

- [ ] 关键场景（新增/修改）通过
- [ ] 负向场景（不命中）通过
- [ ] 幂等与失败恢复通过
- [ ] 若启用 webhook/http，则安全项通过

签收信息：

- 执行人：
- 执行日期：
- 版本/提交：
- 结论：通过 / 不通过
