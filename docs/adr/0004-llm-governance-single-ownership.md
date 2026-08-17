# ADR-0004：LLM「治理」单一归属

- **状态**：已采纳
- **日期**：2026

## 背景

`core/llm_governance.py` 与 `services/llm/llm_governance_service.py` 并存，职责边界不清
（核心层出现业务治理异常类型）。

## 决策

治理逻辑（额度/速率/预算策略、Redis 计数、封禁判定）与治理异常类型统一归属
`app/services/llm/llm_governance_service.py`（单一门面 `llm_governance_service`），
核心层不再持有业务治理实现。

## 理由

- 治理是 LLM 基础设施域的业务能力，应随 `services/llm/` 内聚，而非散落在 `core/`。
- 单一门面便于限流/预算策略的演进与测试。

## 后果

- 调用方统一 `from app.services.llm.llm_governance_service import llm_governance_service`。
- `LLMGovernanceError` 从该模块导出，不再单独存在于 `core/`。
