# Skills 目录说明

`src/core/capabilities/skills` 按职责域拆分，避免将“顶层技能”与“底层基础设施”平铺在同一级目录。

## 目录分层

- `base/`: 技能共享抽象与公共协议（如 `BaseSkill`、语义槽位、技能元数据、响应池）
- `actions/`: 复合动作决策与执行（动作引擎、写入服务）
- `bitable/`: 飞书多维表格领域封装（适配器、schema 缓存、多表关联）
- `reminders/`: 提醒与日程相关逻辑
- `utils/`: 技能侧通用解析与格式化工具
- `implementations/`: 由路由器直接分发的顶层技能实现（`query.py`, `create.py`, `update.py`, `delete.py`, `summary.py`, `chitchat.py`）

## 设计意图

- 顶层技能与基础设施解耦，便于定位“可路由技能入口”
- 外部数据源适配逻辑集中，降低未来替换底层存储/平台的迁移成本
- 动作执行、提醒、工具函数形成独立演进边界，降低模块耦合
