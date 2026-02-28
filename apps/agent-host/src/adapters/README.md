# Adapters (适配器层)

`adapters/` 负责把外部平台与基础设施接入到系统中，同时保持 `core/` 业务域对渠道和 SDK 无感。

## 目录结构

### 1) `channels/` (通信渠道适配)

用于对接飞书、Discord 等渠道，把“收消息 / 发消息 / 渲染回复”映射到各平台协议。

#### `channels/discord/`

- 轻量渠道适配，负责 Discord 事件解析与消息格式化。

#### `channels/feishu/`

飞书适配器已按职责域拆分，避免平铺：

```text
feishu/
├── protocol/
│   ├── event_adapter.py
│   ├── sender.py
│   └── formatter.py
├── ui_cards/
│   ├── card_templates.py
│   ├── card_template_registry.py
│   ├── card_template_config.py
│   ├── card_scaffold.py
│   └── template_runtime.py
├── actions/
│   ├── action_engine.py
│   ├── smart_engine.py
│   └── processing_status.py
├── skills/
│   └── bitable_writer.py
└── utils/
    ├── record_links.py
    └── reminder_target_adapter.py
```

- `protocol/`: 渠道协议层，负责入站事件标准化、出站发送封装、回复格式化。
- `ui_cards/`: 飞书交互卡片渲染子系统（模板、注册表、配置与运行时）。
- `actions/`: 卡片动作事件处理与交互状态管理。
- `skills/`: 渠道侧独立工具（如 bitable 写入器）。
- `utils/`: 飞书专属小工具（链接与目标映射等）。

### 2) `file/` (文件处理适配)

统一文件提取能力抽象，包含请求/结果模型与不同 provider 的实现。

## 设计原则

- 依赖倒置：`core/` 依赖抽象能力，不直接依赖渠道 SDK。
- 单一职责：协议处理、卡片渲染、动作逻辑、工具函数分层维护。
- 渐进演进：先做目录分层和 import 解耦，再按业务节奏清理历史实现。

## 演进说明

- `actions/smart_engine.py` 当前仍保留，用于兼容既有规则逻辑。
- 后续可逐步把规则判断下沉到上层技能编排，`adapters` 回归“纯协议与渲染执行”角色。
