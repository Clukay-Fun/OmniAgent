# mcp-feishu-server 目录规划（轻量版）

本文件用于说明当前目录职责，减少“配置/模板/运行文件”混淆。

## 顶层目录职责

- `src/`：服务源码（路由、自动化引擎、工具实现）
- `tests/`：自动化与路由测试
- `automation_spec/`：文档与模板（不被运行时直接加载）
- `automation_data/`：运行时产物目录（快照、幂等、日志、死信）

## 顶层关键文件

- `automation_rules.yaml`：运行时规则文件（实际生效）
- `scripts/automation_gray_check.py`：灰度检查脚本
- `config.yaml` / `.env`：配置与环境变量
- `run_server.py` / `run_dev.py`：启动入口

## 文档与模板约定

- `automation_spec/README.md`：总览与流程
- `automation_spec/rules.template.yaml`：规则写法模板
- `automation_spec/events.sample.json`：事件契约样例
- `automation_spec/fields.yaml`：字段契约与校验约定
- `automation_spec/todo.md`：任务与待办跟踪

## 运行产物约定

运行期间可能生成：

- `automation_data/snapshot.json`
- `automation_data/idempotency.json`
- `automation_data/checkpoint.json`
- `automation_data/run_logs.jsonl`
- `automation_data/dead_letters.jsonl`

这些文件已在子项目 `.gitignore` 中忽略，不参与版本管理。

## 后续可选重排（非本次）

- 若后续再做中度重整，可新增 `docs/` 并迁移非运行文档。
- 若后续做深度重构，再拆分 `src/automation/` 与 `src/server/` 子模块边界。
