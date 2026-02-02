# OmniAgent 开发任务计划 v2.0

## 总览

```
Phase 1：基础框架
Phase 2：Soul 人格系统
Phase 3：Memory 记忆系统
Phase 4：Skill 系统
Phase 5：集成与编排
Phase 6：测试与监控
```

---

## Phase 1：基础框架

### 任务清单
- [x] 1.1 目录结构搭建（core/soul、core/memory、core/intent、core/router、workspace）
- [x] 1.2 初始化脚本（自动创建 workspace + 默认模板）
- [x] 1.3 配置管理（ConfigManager + 60s 热更新）
- [x] 1.4 文件锁工具（filelock）

### 验收标准
- [x] 目录结构完整
- [x] 首次运行自动创建 SOUL.md / IDENTITY.md / MEMORY.md
- [x] 配置修改后 60s 内生效

---

## Phase 2：Soul 人格系统

### 任务清单
- [ ] 2.1 Soul 加载器（soul.py，支持热更新）
- [ ] 2.2 SOUL.md 模板（律师助手人格定义）
- [ ] 2.3 IDENTITY.md 模板（对外自我介绍）
- [ ] 2.4 注入到 LLM（system prompt 组装）

### 验收标准
- [ ] 修改 SOUL.md 后 60s 内对话风格变化
- [ ] LLM 回复符合人格设定

---

## Phase 3：Memory 记忆系统

### 任务清单
- [ ] 3.1 MemoryManager 基础（读写共享/用户记忆）
- [ ] 3.2 每日日志（自动记录对话）
- [ ] 3.3 上下文裁剪（2 天 + 2000 token）
- [ ] 3.4 用户隔离（users/{open_id}/ 目录）
- [ ] 3.5 写入策略（手动触发 + 自动事件）
- [ ] 3.6 日志清理（30 天保留 + 启动清理）
- [ ] 3.7 并发安全（filelock 写入锁）

### 验收标准
- [ ] 对话历史正确记录到每日日志
- [ ] "记住我喜欢简洁回复" 写入用户长期记忆
- [ ] 多用户记忆完全隔离
- [ ] 超过 30 天的日志自动删除

---

## Phase 4：Skill 系统

### 任务清单
- [ ] 4.1 BaseSkill 基类（统一接口）
- [ ] 4.2 IntentParser（规则匹配 + LLM 兜底）
- [ ] 4.3 SkillRouter（阈值判断 + 分发）
- [ ] 4.4 SkillChain（链式执行，max_hops=2）
- [ ] 4.5 QuerySkill（案件查询，调用 MCP）
- [ ] 4.6 SummarySkill（总结 last_result）
- [ ] 4.7 ReminderSkill（CRUD，Postgres）
- [ ] 4.8 ChitchatSkill（白名单 + 引导）

### 验收标准
- [ ] "查一下今天的庭" 命中 QuerySkill
- [ ] "帮我总结今天的案子" 触发 Query → Summary 链式
- [ ] "提醒我明天开庭" 创建提醒
- [ ] "你好" 返回问候 + 功能引导

---

## Phase 5：集成与编排

### 任务清单
- [ ] 5.1 Orchestrator（主流程编排）
- [ ] 5.2 上下文组装（Soul + Memory + Skill）
- [ ] 5.3 错误处理（友好提示）
- [ ] 5.4 超时控制（LLM 10s 超时）
- [ ] 5.5 Webhook 集成（飞书入口）

### 验收标准
- [ ] 完整对话流程跑通
- [ ] LLM 超时时返回兜底回复
- [ ] 错误时返回"抱歉，处理出错"而非堆栈

---

## Phase 6：测试与监控

### 任务清单
- [ ] 6.1 单元测试（Soul / Memory / Intent / Skills）
- [ ] 6.2 集成测试（端到端流程）
- [ ] 6.3 日志埋点（JSON 结构化日志）
- [ ] 6.4 监控指标（Prometheus metrics）

### 验收标准
- [ ] 测试覆盖率 > 80%
- [ ] 日志可按 skill / user_id / score 过滤
- [ ] Grafana 可视化请求量 / 延迟 / 错误率
