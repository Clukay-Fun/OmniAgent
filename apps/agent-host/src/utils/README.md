# Utils (基础设施与通用工具库)

`utils/` 负责跨业务域复用的底层能力，避免在 `core/`、`api/`、`adapters/` 中重复实现。

## 目录分层

- `observability/`
  - `logger.py`: 结构化日志与请求上下文
  - `metrics.py`: Prometheus 指标收集与降级空实现
- `platform/feishu/`
  - `feishu_api.py`: 飞书 API 调用封装（token 管理、消息发送、卡片回退）
- `runtime/`
  - `hot_reload.py`: 配置热更新 watcher
  - `workspace.py`: 工作区初始化与默认资源生成
  - `filelock.py`: 跨平台文件锁
  - `config.py`: 轻量配置加载器
- `parsing/`
  - `time_parser.py`: 自然语言时间范围解析
- `errors/`
  - `exceptions.py`: 通用异常模型（LLM/MCP/Skill 等）

## 约束

- `utils/` 不承载业务编排逻辑，只提供基础能力。
- 渠道强耦合能力应逐步下沉到 `adapters/`（例如飞书专有调用）。
- 模块职责边界清晰后，优先通过子目录扩展而不是在根目录继续平铺文件。
