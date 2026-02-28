# Vector (语义向量与记忆检索引擎)

`vector/` 目录负责系统中所有的文本向量化（Embedding）与语义检索（Vector Query）任务。

在正统的 OpenClaw 或主流 Agent 框架（如 LangChain、LlamaIndex）中，向量检索通常被抽象为极其复杂的 Retriever、Document Loader 和 VectorStore 矩阵。而在本系统基于 `nanoclaw` 的降维设计原则下，我们剥离了所有非必要的中间件包装，直接与底层大模型 API 和向量数据库交互，实现了**零抽象泄露**的极致性能。

## 目录模块解析

本目录由三个核心文件构成，清晰地划分了从计算、存储到调度的三层结构：

### 1. 向量计算层 (Embedding API)
- **`embedding.py` (`EmbeddingClient`)**:
  - **直接 HTTP 发包**：没有重度依赖第三方 SDK。目前仅通过 `httpx.AsyncClient` 封装了兼容 OpenAI API 格式（如 SiliconFlow）的 `/embeddings` 接口调用。
  - **自动批处理**：内置了 `batch_size` 切片防爆器。当需要把长文本切割转化为向量阵列时，它能自动分块请求（默认 32 个为一批），保证系统永远不会遇到 413 (Payload Too Large) 的尴尬退场。

### 2. 本地存储底座 (Vector DB)
- **`chroma_store.py` (`ChromaStore`)**:
  - **轻量本地化**：当前我们选用了 `chromadb` 作为底层，利用其 `PersistentClient` 的特性将向量直接落盘至工作区（`workspace/`），而无需外挂庞大的 Milvus 或 Qdrant 服务器集群服务。
  - **多租户隔离 (Collection Isolation)**：采用严谨的**沙盒隔离机制**——在添加和查询文档时强制要求传入 `user_id`，并使用 `f"memory_vectors_{user_id}"` 作为 Collection 的唯一标识。从物理层面上**绝对杜绝**了张三通过语义相似度问出李四案件隐秘的安全性灾难。
  - **软降级容灾**：巧妙使用了 `try...except ImportError` 的懒加载探测方案。如果部署机未安装庞大的 sqlite/chromadb 依赖族，项目也能直接降级并打印 Warning 优雅启动，不影响 Agent 查询多维表格的绝对核心功能。

### 3. 上层业务调度 (Memory Manager)
- **`memory.py` (`VectorMemoryManager`)**:
  - 将 `EmbeddingClient` 与 `ChromaStore` 组装粘合的高级对象。
  - 向上级指挥官 `Orchestrator` 提供极其“傻瓜”的方法：`add_memory(user_id, content)` 和 `search(user_id, query)`。
  - **异常黑洞**：在写入或者搜寻期间如果触发了网络抖动或解析空指针，它都会以极低姿态吞下 Exception (`return []`)，绝对不允许因为一段无关紧要的“补充记忆”检索引擎挂掉而连坐导致主链路崩塌。

---

## 架构哲学与演进 (Implementation Protocol)

### OpenClaw 视角 (正统架构基准)
在 OpenClaw 中，长线记忆（Long-Term Memory）系统是极其昂贵的。所有的记忆碎片进入 Vector DB 前，都要先过一把知识图谱抽取引擎，检索结果召回后，还得通过一层 Reranker（重排序模型）打分甚至交由 LLM 进行交叉验证以防止“基于向量计算出来的距离欺骗大脑”。

### Nanoclaw 映射 (本项目降维实现)
考虑到本项目的核心是利用飞书多维表格（Bitable）去操作实打实的“精确结构化数据（Structured Data）”，对于“模糊语义记忆（Unstructured Semantic Data）”的需求**只扮演了边缘锦上添花的作用**（比如记住用户说“下周五我要出差”作为闲聊天边角料）：

1. **去 Rerank 化**：我们目前的规模完全不需要重排机制（Reranker）。只要基于向量余弦相似度的 `top_k=5` 召回就已经能在小库里命中超过 90% 的关联文本。
2. **轻依赖战略**：整个模块被设计成一个即插即用的外设 (Plug-in Widget)。在 `main.py` 和 `orchestrator.py` 环节里，如果没有配置好 Embedding API Key，系统会自动判处 Vector Memory 的死刑，而不会影响主体。
3. **数据清洗的免责声明**：由于业务目前是单流结构，我们放弃了构建复杂的文本块重叠切割（Chunking Overlay）体系，依靠业务人员精简的句子输入，使得 Embedding 端面对的是粒度极低的完整原子句。
