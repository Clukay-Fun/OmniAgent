# Jobs (后台调度任务层)

`jobs/` 目录用于存放与用户主动交互无关的**后台非阻塞定时任务 (Scheduled Jobs)**。

在此之前，个人的“单次提醒任务”也依赖该包下基于 PostgreSQL 的调度器。现在该目录已经过“无状态化”重构，移除了重量级的本地数据库调度依赖，转化为纯依赖飞书 MCP 读取并进行播报的轻代理（Broker）。

## 目录模块解析

当前该包按职责拆成两个子目录：

- `dispatchers/`: 统一分发与幂等去重
- `schedulers/`: 定时扫描与任务编排

核心模块如下：

### 1. 统一分发器
- **`dispatchers/reminder_dispatcher.py`**: **消息分发与去重网关**。
  - 所有后台定时查询出的播报（开庭、摘要等），都不会直接调用飞书发消息，而是统一被封装为 `ReminderDispatchPayload` 并投递给这个 Dispatcher。
  - **核心价值**：它带有一个 `InMemoryReminderDedupeStore` 内存幂等去重器，保障同样的业务主键、同样的触发日期和同样的偏移量（如“开庭-案件123-提前3天”），在应用重启或者重试时**绝对不会向用户群组发送重复的垃圾通知**。

### 2. 每日数据推送任务
- **`schedulers/daily_digest.py`**: **每日律所案件摘要**。
  - 基于 `apscheduler` 的 Cron 触发。
  - **工作流**：到达配置的每天早晨触发点后 -> 悄悄请求 MCP 查询（今日到期的数量、本周新增的数量、待处理的数量） -> 构建 Markdown 文本 -> 移交给 `ReminderDispatcher` 发送至飞书指定提醒群 (`reminder_chat_id`)。

### 3. 多日历事件预警任务
- **`schedulers/hearing_reminder.py`**: **主动式开庭预警扫描**。
  - 同样基于 `apscheduler` 跑定时轮询（Interval）。
  - **工作流**：它会探测 `T+7, T+3, T+1, T+0` 这四个偏移维度。并把计算出来的这4个目标日期交给 MCP，让 MCP 去大库里找出在这些日期开庭的记录。找出来后渲染紧迫度预警（如 `🚨今天开庭` 或 `🟡3天后开庭`），然后移交 `ReminderDispatcher` 去重和播报。

### 4. 会话提醒扫描任务（兼容层）
- **`schedulers/reminder_scheduler.py`**: 会话提醒扫描器，负责读取到期提醒并统一交由 `ReminderDispatcher` 分发。
- **`reminder_scheduler.py`**: 兼容导入层（re-export），用于承接历史 `src.jobs.reminder_scheduler` 引用。
  
---

## 架构原则与未来演进

1. **纯粹的无状态计算 (Stateless)**：当前的所有任务都只需利用大库（飞书多维表格作为唯一的 Truth of Data）做定时扫表，自己不落数据库（除了短期的内存防重 Set）。这极大地降低了部署门槛。
2. **长远的 Automation 去除**：
   - 就像普通个人提醒已经被移交给飞书原生 Automation 处理一样，`daily_digest` 和 `hearing_reminder` 这类全局业务预警，在飞书自动化（Automation）的增强下，未来也可全部交给飞书原生能力来实现（多维表格可以直接支持提前若干天自动发 Webhook）。
   - 一旦飞书的触发器能力满足业务，这个 `jobs/` 整体目录都可以被平滑舍弃，彻底实现 Agent 全被动触发。
