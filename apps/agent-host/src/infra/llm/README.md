# LLM (大模型驱动与适配层)

`llm/` 目录是整个 Agent 的大模型交互基座。

在 OpenClaw 的正统架构中，LLM Provider 层往往有着极其重度且抽象的设计，涵盖了对多种异构大模型平台（如 AWS Bedrock、Azure OpenAI、Anthropic 等）的复杂适配基类与插件化装载机制。而在当前项目中，我们基于 `nanoclaw` 的极简降维思想，实现了轻便、克制但足够健壮的大模型适配器。

## 目录模块解析

- **`client.py`**: **LLM 客户端统一封装容器**。
  - **核心链路**：基于官方推荐的 `openai` 包（即 OpenAI 兼容格式）搭建。利用其高度标准化的接口形式，仅需修改 `Base URL` 和 `API Key`，即可无缝切换到例如 MiniMax、Qwen（通义千问）或 DeepSeek 等第三方兼容平台。
  - **异常与重试**：封装了对网络超时 (`LLMTimeoutError`)、HTTP 响应码异常以及 JSON 解析灾难逃逸的健壮处理。
  - **打点与计费基石**：深度挂载了针对成本与耗时的结构化指标收集（借助 `record_llm_call` 和请求周期的 `_capture_usage` 方法），供上层 `cost_monitor` 无感探测。
  - **实用工具封装**：提供了譬如 `chat_json`（内部强行刮掉多余的 Markdown 格式壳子以确保 JSON 可用）等便捷接口。

- **`provider.py`**: **工厂创建算子**。
  - 提供 `create_llm_client` 极简工厂方法，基于 `LLMSettings` 构造全局可用的 LLMClient 单例/实例对象。

---

## 架构提议与执行逻辑链 (Implementation Protocol)

### OpenClaw 视角 (正统架构基准)
在 OpenClaw 语境下，系统拥有极为复杂的 LLM Registry、Token Window 预测器以及多路并发熔断网关（Circuit Breaker），且所有 LLM 都必须通过实现抽象基类 `BaseLLMProvider` 来完成插件化接入。它是为了支撑复杂多智能体矩阵 (Multi-Agent Swarm) 而生的。

### Nanoclaw 映射 (本项目降维实现)
在日常高频迭代的协同平台（如当前基于飞书群聊构建的具体业务引擎）中，盲目套用重量级 Provider 将导致调试深渊。因此，本项目在 `src/llm` 层遵循以下实践：
1. **统一协议战胜异构基类**：由于目前市面上超过 90% 的优质大模型 API 皆主动靠拢 OpenAI 的 RESTful 契约，我们放弃编写繁痛的多端 Adapter 接口层，而是坚守一个纯粹的 `AsyncOpenAI` 客户端底座进行泛用请求转发。
2. **Context Tracker 轻量化**：通过 Python 原生的上下文变量 (`ContextVar`) 注入请求时的环境信息（`route_context`），取代了 OpenClaw 中沉痛的 Tracing 拦截器链模式。
3. **强 JSON 容错能力**：保留 `chat_json` 方法，专门用于兜底应对各种国内 LLM 在输出结构化数据时随意附赠的啰嗦提示词和首尾 Markdown 格式控制符。

这样，“大脑”在指挥 LLM 时便既具备监控上的可见性，又能享受到无额外开销的极致性能调用体验。
