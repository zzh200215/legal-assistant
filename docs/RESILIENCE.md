# 韧性设计：故障模式 × 恢复机制对照表

> 本文档是韧性测试的**单一事实来源**（阶段 3 产出）：每种故障模式的检测信号、
> 恢复机制、兜底语义与测试证据位置。新增故障模式必须同步补表 + 补测试。

## 对照表

| # | 故障模式 | 检测信号 | 恢复机制 | 兜底语义 | 测试证据 |
|---|---|---|---|---|---|
| 1 | **任务执行中进程崩溃**（文档/连接器/支付/通知） | 租约（lease_expires_at）过期 | `recover_stale_*` 8 个 beat 任务回收 claim 回置 pending，幂等重跑 | 阶段函数幂等（parse/chunk/index 版本守卫）；DB 唯一约束 | `tests/task/test_document_task_orchestration.py`（recover 重投+job 重绑）、`tests/test_outbox_delivery_task.py`、`tests/test_notification_outbox.py`、`tests/resilience/test_reconciliation_resilience.py` |
| 2 | **并发写同一资源**（任务/组织/文档） | `StaleDataError`（version_id_col） | 全局 handler → `409 CONCURRENT_UPDATE_CONFLICT`；前端版本冲突对话框 | If-Match 前置校验 + 乐观锁双保险 | `tests/test_optimistic_lock.py`、`tests/api/test_if_match_endpoints_409.py`、`tests/test_obs_api_p1.py` |
| 3 | **重复消息 / 重复 webhook / 重复 beat** | 幂等键（Idempotency-Key + DB 唯一约束）、webhook 验签+nonce | 重放返回原结果；同 key 异指纹 409；并发重放仅一成功 | Redis SET NX EX 快速路径 + DB 唯一兜底 | `tests/test_idempotency_service.py`、`tests/test_webhook_verifier.py`、`tests/contract/test_redis_contract.py`、`tests/test_atomic_quota.py` |
| 4 | **乱序事件**（支付/签署/通知） | 时间戳/序号判定 | 旧事件不逆转新状态；乱序标记 anomalous；outbox 各事件独立幂等投递 | 状态机终态不可逆（billing_state_machines） | `tests/test_payment_webhook_reliability.py`、`tests/test_legal_v3_signing.py`、`tests/resilience/test_outbox_out_of_order.py` |
| 5 | **Redis 不可用** | 连接异常 | 锁/心跳/冷却 **fail-open 放行** | DB 唯一约束/乐观锁/状态机兜底 | `tests/contract/test_redis_contract.py`（fail-open）、`tests/test_distributed_lock.py` |
| 6 | **LLM 超时 / 5xx / 限流** | ExternalError 分类（NETWORK/TIMEOUT/SERVER_5XX/RATE_LIMITED） | 指数退避重试 → 熔断半开 → provider 回退路由 | 写超时 AMBIGUOUS 不盲目重试；出站 DLP 门禁 | `tests/test_external_resilience.py`、`tests/test_llm_model_routing.py`、`tests/test_circuit_breaker.py` |
| 7 | **对象存储不可用**（MinIO/S3/OSS） | `StorageBackendUnavailable`（构造期） | 任务按可重试错误处理；local 适配器零依赖 | 上传失败不产生半成品（流式+hash） | `tests/contract/test_storage_adapter_contract.py`、`tests/test_document_storage.py` |
| 8 | **邮件/飞书投递失败** | HTTP 错误经韧性层分类 | 退避重试（`30*4^n`，上限 3）→ dead letter → 人工重试保留幂等键 | 写超时不盲目重试（AMBIGUOUS） | `tests/resilience/test_webhook_delivery_retry_task.py`、`tests/test_notification_outbox.py`、`tests/contract/test_feishu_messenger_contract.py` |
| 9 | **对账中断 / 重复调度** | run 台账游标 + 租约 | `recover_stale_runs` 回置 pending；成功 run 幂等跳过；跨 provider 游标独立 | 不自动静默修改财务记录（仅报告差异） | `tests/resilience/test_reconciliation_resilience.py`、`tests/test_reconciliation_service.py` |
| 10 | **配额并发扣减** | 原子预留（UsageReservation） | 并发预留不超额；重复 usage 事件不重复扣减 | DB 事务原子性 | `tests/test_atomic_quota.py`、`tests/test_subscription_phase10.py` |
| 11 | **Beat 多实例 / 任务重叠** | 分布式锁 SET NX PX | `_beat_lock` 装饰器：未获锁安全跳过；token CAS 续租 | 锁 TTL 兜底（worker 崩溃后自动过期） | `tests/test_distributed_lock.py`、`tests/contract/test_redis_contract.py` |
| 12 | **支付事件卡住 / 发票状态异常** | 对账差异分类（webhook_pending/payment_stuck/status|amount_mismatch/refund_mismatch） | 结构化差异报告 + 人工处置；事件补处理后下一轮差异消失 | 差异只读不静默修复 | `tests/resilience/test_reconciliation_resilience.py` |
| 13 | **外部写操作歧义**（超时后结果未知） | 写超时 → AMBIGUOUS_SIDE_EFFECT | 不盲目重试；状态机/幂等键允许后续安全重放 | 查询态确认后再决策 | `tests/test_external_resilience.py`（write_timeout 不盲目重试） |
| 14 | **签名密钥未轮换 / 安全红线** | webhook_secret_ciphertext 缺失 | 投递置 failed（拒绝发送） | 失败显式化（不静默降级） | `tests/resilience/test_webhook_delivery_retry_task.py` |

## 韧性测试目录（阶段 3 新增）

| 文件 | 覆盖 |
|---|---|
| `tests/resilience/test_reconciliation_resilience.py` | 对账断电恢复闭环 / 跨 provider 游标独立 / refund、overpaid 差异 / 最终一致闭环 / 失败记账 |
| `tests/resilience/test_webhook_delivery_retry_task.py` | webhook 指数退避 / 上限 / HMAC 签名头 / 密钥轮换红线 / 不活跃跳过 / 失败脱敏 |
| `tests/resilience/test_outbox_out_of_order.py` | 通知 outbox 乱序投递最终一致 / dispatch 幂等 / 断电 reclaim 不重复投递 |

## 韧性验收口径（DoD #2）

- 阶段 1–3 合计：关键路径覆盖率 ≥80%（当前 64.94% → 阶段 2/3 契约+韧性测试持续补齐，
  见 docs/TESTING_AND_RELEASE.md §2.3 追踪表）；
- 全部韧性测试可独立运行、可重复、不依赖真实外部服务（SQLite 内存库 + mock/fake）。
