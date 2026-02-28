# Infra (基础设施接入层)

`infra/` 统一承载模型与外部能力基础设施，避免在 `src/` 根目录继续平铺。

## 子目录

- `llm/`: 大模型客户端与工厂
- `mcp/`: MCP 协议客户端与调用重试策略
- `vector/`: 向量配置、Embedding、向量存储与记忆检索

## 兼容策略

- 现保留 `src/llm`、`src/mcp`、`src/vector` 兼容 shim（re-export）。
- 项目内新代码优先使用 `src.infra.*` 路径。

## 去 Shim 时间表

1. 现在：shim 正常工作，并在旧路径导入时发出 `DeprecationWarning`。
2. 下一个功能迭代完成后：检查是否仍有外部代码引用 `src/llm`、`src/mcp`、`src/vector`。
3. 再下一个迭代：删除 shim 目录（`src/llm/`、`src/mcp/`、`src/vector/`）。
