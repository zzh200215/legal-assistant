# ADR-0003：RAG 统一门面

- **状态**：已采纳
- **日期**：2026

## 背景

检索能力曾分散在 ~10 个文件（`rag_service / rag_runtime / rag_cache / query_expansion /
rerank / vector_store / document_indexing` 等），调用方需了解内部拓扑。

## 决策

收敛为 `app/services/rag/` 包，对外只暴露一个门面单例 `rag_service = RAGService()`；
检索/答案/索引/查询改写等实现按关注点拆为 mixin 与纯函数模块
（`_helpers.py`、`_scoring.py`、`retrieval.py`、`answer.py`、`indexing.py`、`query_rewrite.py`）。

## 理由

- 调用方只需面对一个稳定门面，内部实现可独立演进。
- 上帝类（1577 行）拆分后每模块职责单一、可独立测试。

## 后果

- 门面方法名视为契约，不得随意删除/改名（测试与外部 `patch` 依赖其名称）。
- 新增检索能力优先在对应 mixin/模块内实现，避免门面再次膨胀。
