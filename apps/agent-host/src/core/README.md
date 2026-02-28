# Core (业务核心与引擎层)

`core/` 是 Agent 的核心执行域。当前已按职责再封装为 6 个一级分类目录，根目录只保留分层入口与文档。

## 一级分类

### 1) Agent 大脑中枢与编排机制 (`brain/`)

- `brain/orchestration/orchestrator.py`: 主流程编排器（核心入口）
- `brain/l0/`: L0 快速规则与短路处理

### 2) 意图与路由 (`understanding/`)

- `understanding/intent/`: 意图识别与技能配置加载
- `understanding/router/`: 技能路由与模型选路

### 3) 规划与记忆 (`runtime/`)

- `runtime/planner/`: 规划器与输出结构约束
- `runtime/memory/`: 长短期记忆管理
- `runtime/state/`: 会话状态、存储实现与 `session.py`

### 4) 技能与工具下发层 (`capabilities/`)

- `capabilities/skills/`: 顶层技能实现与底层技能基础设施

### 5) 表达与灵魂塑造 (`expression/`)

- `expression/response/`: 响应模型与渲染
- `expression/soul/`: 人设加载与融合
- `expression/prompt.py`: Prompt 拼装工具

### 6) 控制、监控与基础契约 (`foundation/`)

- `foundation/telemetry/`: 成本与用量观测
- `foundation/progress/`: 处理进度事件模型
- `foundation/common/`: 跨域共享类型与错误契约

## 设计原则

- 分层明确：编排、理解、状态、技能、表达、基础契约各自独立演进。
- 核心解耦：业务编排不直接耦合渠道协议与第三方 SDK。
- 渐进重构：优先做目录收敛与导入稳定，再处理内部进一步拆分。
