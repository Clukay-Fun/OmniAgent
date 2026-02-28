# 配置目录说明

`config/` 仅存放运行期声明式数据，不存放业务代码。当前按职责域拆分如下：

## 目录结构

- `identity/`：人格与长期提示词（`SOUL.md`、`IDENTITY.md`、`MEMORY.md`）
- `skills/` + `skills.yaml`：技能注册、触发规则与技能级提示词
- `messages/zh-CN/`：用户可见文案（`responses.yaml`、`casual.yaml`、`error_messages.yaml`）
- `ui_templates/feishu/`：飞书 UI 模板资产（`card_templates.yaml`、`templates/*.json`）
- `engine/`：引擎级参数（`l0_rules.yaml`、`prompts.yaml`、`vector.yaml`）
- `scenarios/`：Planner 场景规则（保持独立，不并入 messages）

## 设计约束

- `messages/` 只放文案，不放路由规则或业务流程。
- `ui_templates/` 只放前端呈现资产，避免与 Prompt 配置混淆。
- `engine/` 只放后端运行控制参数，不放技能清单。
- `scenarios/` 属于规划规则域，独立维护版本和回归。

## 迁移说明

- 代码默认路径已切到新目录结构。
- 关键读取点保留了旧路径回退（兼容历史环境变量与未迁移部署）。
- 新增其他语言时，按 `messages/<locale>/` 扩展（例如 `messages/en-US/`）。
