# 适配器契约文档（Adapter Contracts）

> 范围：本系统对 6 类外部依赖**承诺的调用协议**（请求结构/响应处理/错误码），
> 不测三方实现。配套契约测试见 `tests/contract/`（+ 既有 `tests/test_vector_store_contracts.py`、
> `tests/test_llm_provider_adapter.py`、`tests/test_payment_webhook_reliability.py`、
> `tests/test_webhook_verifier.py`）。
>
> 原则：全部测试用内存替身（fakeredis / httpx.MockTransport / fake SDK），
> **不依赖真实外部服务**；改动本协议的任何一侧（本系统代码或文档）都必须同步更新测试。

---

## 1. LLM（core/llm_client.py / llm_provider_adapter.py / model_policy.py）

| 项 | 契约 |
|---|---|
| 请求结构 | OpenAI 兼容 chat.completions：`{model, messages, temperature, stream, max_tokens?}`；出站 DLP/审计门禁在发送前执行 |
| 超时 | `model_policy.py` 定义 timeout/retries/fallback；ReadTimeout 等网络错误按可重试分类走降级路由 |
| 失败回退 | provider 不可用 → 按 `LLM_MODEL_ROUTING`/`LLM_MODEL_FALLBACK` 路由到备选模型；熔断半开（circuit_breaker） |
| Provider 路由 | `llm_provider_adapter.py` 按 provider 分派（openai/ollama/...）；路由决策可观测（llm_call_log） |
| 错误码 | 业务错误经 `ExternalError` 分类：NETWORK/TIMEOUT/SERVER_5XX/RATE_LIMITED 可重试；AUTH/PERMISSION/PARAMS 不可重试；`CIRCUIT_OPEN` 直接降级 |
| 契约测试 | `tests/test_llm_provider_adapter.py`、`tests/test_llm_model_routing.py`、`tests/test_model_gateway_pool.py`、`tests/test_external_resilience.py` |

## 2. Redis（core 各模块 / tasks/runtime.py）

| 项 | 契约 |
|---|---|
| 分布式锁 | `SET key token NX PX ttl`；键 `aibg:tasklock:{task_name}[:{scope}][:{window}]`；互斥（二次获取返回 None） |
| CAS 释放/续租 | Lua：`if get(KEYS[1])==ARGV[1] then del/pexpire`；错 token 不删/不续（防误删他人锁） |
| 幂等键原语 | `SET key fingerprint NX EX ttl`：同 key 重放 → 拒绝；同 key 异指纹 → 拒绝（409 语义来源）；DB 唯一约束兜底 |
| 心跳 | `SET aibg:operations:beat:last_tick <iso> EX 180` |
| 故障语义 | **fail-open**：Redis 不可用放行（DB 唯一约束/乐观锁兜底），不阻断任务 |
| 契约测试 | `tests/contract/test_redis_contract.py`（fakeredis + Lua 语义 stub）、`tests/test_distributed_lock.py` |

## 3. 向量库（services/rag/vector_store.py）

| 项 | 契约 |
|---|---|
| 接口 | `VectorStoreCollection` 协议（写入/检索/删除/元数据过滤）；`build_vector_store()` 按 provider 返回 Chroma/Qdrant 实现 |
| 写入 | chunk 写入 collection（含 document_id/user_id 元数据）；同文档重复索引幂等（按 chunk hash 去重） |
| 检索 | `search_async`：top_k / where 过滤（组织/权限元数据）；返回 {chunk_text, document_id, score, ...} |
| 删除/更新 | 按 document_id 删除集合内全部 chunk；重索引覆盖旧版本 |
| 元数据过滤 | 权限过滤字段（document_id、user_id、authorized_document_ids）作为 where 条件下推 |
| 契约测试 | `tests/test_vector_store_contracts.py`（Chroma/Qdrant 双实现契约）、`tests/test_vector_store.py` |

## 4. 存储（services/storage/）

| 项 | 契约 |
|---|---|
| 协议 | `StorageAdapter`：`provider` + put_stream/get_stream/delete/exists/get_metadata/generate_presigned_url |
| 上传 | 流式（禁止整体 read），返回 `{"size": int, "content_hash": str}`；put_stream(key, source, content_type=) |
| 云 SDK 调用面 | MinIO：`put_object(bucket,key,length=-1,content_type)`/`stat_object`/`presigned_get_object(expires)`；S3：`upload_fileobj(Fileobj,Bucket,Key,ExtraArgs)`/`head_object`/`generate_presigned_url("get_object",Params,ExpiresIn)`；OSS：`put_object(key,source,headers)`/`head_object`/`sign_url("GET",key,expires)` |
| 删除 | 幂等（SDK 异常静默） |
| 签名 URL | 默认 900s；`generate_presigned_url(key, expires_in=900)` |
| 错误码 | SDK 缺失/配置缺失 → `StorageBackendUnavailable`（构造期抛，不阻塞本地启动） |
| 契约测试 | `tests/contract/test_storage_adapter_contract.py`（fake SDK 记录调用参数）、`tests/test_document_storage.py`（Local 真实往返） |

## 5. OAuth / 飞书（services/integration/feishu_service.py + core/webhook_verifier.py）

| 项 | 契约 |
|---|---|
| token 交换 | `POST {base}/auth/v3/tenant_access_token/internal`，body `{app_id, app_secret}`；`code==0` 才接受；无 scope 参数（企业自建应用模式） |
| token 缓存/刷新 | 内存缓存；到期前 60s 内复用，过期重新交换；失败返回 None（不抛） |
| 发送 | `POST {base}/im/v1/messages?receive_id_type=open_id`，`Authorization: Bearer {token}`，body `{receive_id, msg_type, content(json字符串)}`；业务 `code!=0` → `{"configured":True,"sent":False,"code":..}`；HTTP 错误经韧性层 → `sent=False` 不抛 |
| 下载 | `GET {base}/im/v1/files/{file_key}?type=file` + Bearer；JSON 错误响应 → None |
| 回调校验 | 统一 `WebhookVerifier`：签名/时间窗/重放（幂等键 + nonce），并发重放仅一成功（test_webhook_verifier） |
| 未配置 | 不发请求，返回 `{"configured": False}`（占位禁用） |
| 契约测试 | `tests/contract/test_feishu_messenger_contract.py`（MockTransport 钉死请求结构）、`tests/test_webhook_verifier.py`、`tests/test_feishu_m1..m4.py` |

## 6. 支付（services/billing/payment_event_service.py + core/webhook_verifier.py）

| 项 | 契约 |
|---|---|
| webhook 入口 | 验签 fail-closed（签名无效直接拒绝）；`Idempotency-Key` + DB 唯一约束双重幂等 |
| 事件处理 | claim → 处理 → 终态；可重试错误按指数退避重试；终态后重复事件不产生副作用 |
| 乱序事件 | 旧事件不逆转新状态（按时间戳/序号判定，乱序标记 anomalous） |
| 对账 | reconciliation：按 provider/日期对账，4 类差异分类；租约 + 陈旧事件去重 |
| 退款/撤销 | 退款幂等（同来源事件去重）；状态机终态不可逆（billing_state_machines） |
| 错误码 | 验签失败/重复事件/状态非法均返回稳定错误码（envelope + error_codes 注册表） |
| 契约测试 | `tests/test_payment_webhook_reliability.py`、`tests/test_payment_state_machines.py`、`tests/test_reconciliation_service.py`、`tests/test_legal_v3_signing.py` |
