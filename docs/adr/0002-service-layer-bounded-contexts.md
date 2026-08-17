# ADR-0002：服务层按有界上下文分包

- **状态**：已采纳
- **日期**：2026

## 背景

`app/services/` 曾平铺 95 个文件，基础设施（storage/vector_store/feishu）与领域服务
（legal/billing）同级混杂，无内聚结构。

## 决策

将 `services/` 拆为 14 个有界上下文子包：`auth / org / legal / documents / billing /
agent / rag / llm / notification / observability / integration / storage / jobs / memory`；
`api/` 拆为 11 个域子包。

## 理由

- 纯 import 路径迁移 + 一次性脚本重写，低风险、可 review。
- 恢复「业务层 / 基础设施层」的分层语义，让 RAG/Agent/LLM 三块边界清晰。

## 后果

- 跨域引用必须显式 `from app.services.<domain> import <facade>`，禁止 import 内部实现文件。
- 迁移脚本须同时扫描 `alembic/`（迁移脚本中的 import 路径也会随包移动而失效）。
