# API 层 (Input Ports / Controllers)

`api` 目录在应用隔离架构中扮演**应用入口**和**事件总线中枢**的角色。它的核心职责是接收来自外部系统的请求和事件（如用户消息、系统回调和定时任务触发），将其标准化为内部领域的统一事件，然后分发给相应的业务核心或 Agent 服务处理。

可以说，如果适配器 (`adapters/`) 负责的是“如何将内部指令翻译并发送到外部系统（向外）”，那么 `api/` 层负责的就是“如何接受并翻译外部世界的杂乱信号以供内部系统使用（向内）”。

## 目录模块解析

### 1. 通信接入层 (Entrypoints)
负责实现不同的通信协议和服务入口点，建立与外部系统（如飞书、Discord 等）的连接通道。
- **`channels/feishu/webhook_router.py`**: 基于 FastAPI 的 HTTP 路由及接口服务。主要端点暴露供外部系统被动推送使用（例如飞书的回调 Webhook）。
- **`channels/feishu/ws_client.py`**: 飞书 WebSocket 长连接客户端。使用长连接不仅可以实现本地断点调试被动接收飞书消息和事件，也可做为非公网暴漏部署的关键替代方案。
- **`channels/discord/discord_client.py`**: 专用于维持和处理 Discord 平台的客户端长连接及消息下发接口。

### 2. 事件分发与标准化层 (Routing & Normalization)
负责将来自不同渠道、不同协议的长相各异的裸数据字典（Raw Payloads），进行清洗且组装为可控边界的各种内部标准事件对象。
- **`core/inbound_normalizer.py`**: 负责入站数据归一化。把不可靠的协议级数据转换为核心引擎认可的事件模型实例类型（例如抽象聊天平台特征的统一 `MessageEvent` 结构）。
- **`core/event_router.py`**: **核心事件路由器或事件总线总骨架**。收集好清洗过的规范对象后，对 Event Type 归类（比如纯交流消息调度到大模型业务处理槽；而多维表格记录变化、日历项发生变化这类事件会被路由到下游对应的 Hook 处理器中）。
- **`core/callback_deduper.py`**: 处理回调或者并发情况下的防重发和请求去重机制（防抖），确保单一非幂等外部事件不因重试导致业务上的重复处理灾难（如重发两次消息）。

### 3. 后台自动化处理 (Automation Mechanics)
涵盖处理系统中非用户直接发起的触发式消息流调度，通常如服务内的守护行为或者由于异步推迟导致的被动工作池消费者。
- **`automation/automation_rules.py`**: 聚合定义了针对企业系统变动产生的自动化规则集（典型如A1-A3场景化监控：比如监听表格数据发现「新增开庭信息」，需要按规则发什么提醒群信息等）。
- **`automation/automation_consumer.py`**: 配套的自动化队列与消费者运行引擎。负责解析及真正实现 `automation_rules` 判断完毕后下发的操作包。

### 4. 入站处理辅助组件 (Inbound Utilities)
- **`inbound/chunk_assembler.py`**: 将分片消息按会话和时序聚合，确保多段输入在进入编排层前可还原为稳定文本。
- **`inbound/conversation_scope.py`**: 统一构建会话作用域与会话 key，保证不同渠道的会话隔离规则一致。
- **`inbound/file_pipeline.py`**: 处理含实体附件和非结构性长内容输入时的管道流程编排引擎。处理可能出现的文件加载及预处理流水线节点。

### 5. 系统探针与可观测 (System Endpoints)
- **`system/health.py`**: 健康检查探针，供容器/编排系统做存活与就绪检测。
- **`system/metrics.py`**: 指标采集出口，对外暴露应用层可观测数据（如 Prometheus 抓取）。
