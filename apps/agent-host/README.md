# agent-host

`apps/agent-host` 是单Agent主应用入口。

当前目录通过 shim 兼容旧路径 `agent/feishu-agent`，用于平滑迁移：

- 对外统一使用 `apps/agent-host` 作为入口文档与脚本路径。
- 现有运行命令可继续使用，最终执行仍会转发到旧实现。
