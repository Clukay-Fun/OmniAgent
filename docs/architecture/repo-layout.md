# 仓库结构说明

## 目标

将“服务代码、部署配置、开发脚本、文档、运行态数据”分离，避免混放。

## 结构约定

- `agent/`：Agent 服务代码与本地 workspace
- `mcp/`：MCP 服务代码与自动化规则
- `deploy/`：所有部署相关配置（compose、monitoring、后续 nginx/systemd）
- `tools/`：工具脚本
  - `tools/dev`：本地联调
  - `tools/ci`：校验与门禁
- `docs/`：项目文档
  - `docs/deploy`：上传与上线清单
  - `docs/architecture`：架构与目录说明
  - `docs/tests`：测试说明

## 不纳入本次重构范围

- `.backend/` 保持原样，不做移动或改名

## 路径变更摘要

- `docker-compose.yml` -> `deploy/docker/compose.yml`
- `docker-compose.dev.yml` -> `deploy/docker/compose.dev.yml`
- `monitoring/*` -> `deploy/monitoring/*`
- 监控服务合并进 `deploy/docker/compose.yml`，通过 `--profile monitoring` 启动
- `scripts/*` -> `tools/dev/*` 与 `tools/ci/*`
- `TEST.md` -> `docs/tests/TEST.md`
