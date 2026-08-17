# WebSocket 协议（P1 统一化）

端点：`/api/ws/chat`、`/api/ws/agent`（均可订阅同一协议；chat 保留 RAG 会话语义，agent 保留运行/审批语义）。
认证：`sec-websocket-protocol: ["json", "bearer.<access_token>"]`（与 REST `get_current_user` 同套校验：签名/过期/jti 撤销/token_version/用户状态；认证失败 close `1008`）。

版本：`v1`（本文档与 `docs/ws-events.schema.json` 为契约基准，CI 校验与 `app/services/ws_session_service.py` 常量一致）。

## 1. 消息 envelope（服务端 → 客户端）

所有事件统一为：

```json
{ "type": "<event_type>", "seq": 12, "ts": "2026-10-20T08:00:00.000Z", "trace_id": "...", "...payload": "..." }
```

- `seq`：会话内单调递增序号（从 0 开始，`welcome` 为第 1 条）。resume 补发的事件沿用原序号，后续新事件继续递增。
- `ts`：UTC ISO-8601 时间戳；`trace_id`：当前请求链路 ID。
- 事件大小上限 64 KB（超限丢弃并记录日志）。

### 事件类型（type）

| type | 方向 | volatile | 说明 |
|---|---|---|---|
| `welcome` | S→C | 是 | `{session_id, resume_token, last_seq, resumed}` 连接建立首条 |
| `ping` / `pong` | 双向 | 是 | 心跳（服务端 30s 一次；客户端回 `{"type":"pong"}` 或任意消息） |
| `ack` | C→S | — | `{ack_seq}` 客户端确认已收到 ≤ ack_seq 的全部事件 |
| `resume` | C→S | — | `{resume_token, ack_seq}` 连接级恢复（首条消息） |
| `resync_required` | S→C | 是 | `{reason, last_seq}` 无法恢复（token 无效/过期/越权），客户端应重新订阅 |
| `subscribed` | S→C | 是 | `{channels}` 订阅确认 |
| `error` | S→C | 是 | `{code, message}` 稳定错误码（不泄露内部细节） |
| `session` | S→C | 否 | `{session_id}` 聊天会话建立 |
| `chunk` | S→C | 是 | `{content}` 流式片段 |
| `done` | S→C | 否 | `{content, citations?, confidence?, ...}` 完成事件（可恢复） |
| `run_snapshot` | S→C | 否 | `{run, logs}` Agent 运行快照（可恢复） |
| `cancelled` | S→C | 否 | `{kind, id, cancelled, job?/status?}` 取消结果（可恢复） |
| `subscribe` / `unsubscribe` | C→S | — | `{channels: [...]}` 订阅通道（chat/agent/jobs/notifications） |

## 2. 客户端消息（C → S）

旧格式（向后兼容，无 `type` 字段）：
- chat：`{"content": "...", "document_id"?: int, "session_id"?: int}`（content ≤ 8000 字符）
- agent：`{"action"?: "run"|"resume_approval", "goal"?, "max_steps"? (1-10), "session_id"?, "approval_id"?}`

新格式（`type` 字段）：
- `{"type":"chat", ...}` / `{"type":"agent_run", ...}`：同上
- `{"type":"ack", "ack_seq": N}`
- `{"type":"resume", "resume_token": "...", "ack_seq": N}`（仅首条消息）
- `{"type":"subscribe"/"unsubscribe", "channels": ["jobs", ...]}`
- `{"type":"cancel", "kind": "job"|"agent_run", "id": N}`

## 3. 恢复语义

- 状态事件（`session`/`done`/`run_snapshot`/`cancelled`）持久化到 `ws_event_logs`（脱敏负载，24h 过期）；`chunk`/`ping` 等 volatile 事件不落库。
- 断线重连：携带 `{"type":"resume", "resume_token", "ack_seq"}` 作为首条消息 → 服务端校验 token 绑定 user/org/有效期 → 补发 `seq > ack_seq` 的持久化事件（沿用原 seq）→ `welcome` 的 `last_seq` 为恢复点。
- 恢复失败（token 无效/过期/属于他人）→ 明确下发 `resync_required`，绝不静默丢事件；客户端应重新 `subscribe` 并接受可能的事件缺口。
- resume token 是能力令牌：绑定 user_id/org/channel/过期时间，凭任意 sequence 无法读取他人事件。

## 4. 心跳与超时

- 服务端每 30s 发 `ping`（seq 递增）；客户端回 `pong` 或任何消息视为活跃。
- 客户端 120s 无任何消息 → close `4001`（idle timeout）。
- 首条消息 30s 未收到 → close `4003`（protocol error）。

## 5. 背压与慢客户端

- 每连接出站队列上限 500 条；队列满时先丢弃最旧 volatile 事件。
- 队列内无可丢弃事件（全为状态事件）且仍超限 → close `1013`（overloaded，客户端应重新连接并 resume）。
- 单次发送超时 5s；发送失败且事件为 volatile 时丢弃该事件。
- 客户端消息大小上限 128 KB。

## 6. 取消与资源释放

- `{"type":"cancel","kind":"job","id":N}`：与 REST 取消端点同权限（组织管理员），幂等；queued→cancelled、processing→cancel_requested（消费者检查）、已终态返回当前状态。
- `{"type":"cancel","kind":"agent_run","id":N}`：走 `agent_service.request_cancel`（本人/管理员），幂等。
- 断线/关闭：清理出站队列、撤销会话、写安全审计（`ws_session` 事件：open/resume/close/idle_timeout/backpressure 均可观测）。

## 7. 关闭码

| code | 含义 |
|---|---|
| 1000 | 正常关闭 |
| 1008 | 认证失败 |
| 1013 | 背压过载（需重连 + resume） |
| 4001 | 空闲超时 |
| 4002 | resume token 无效（配合 `resync_required`） |
| 4003 | 协议错误（非 JSON / 首条缺失） |

## 8. 与 API/Job 状态一致性

WebSocket 事件不维护第二套状态：`done`/`cancelled`/`run_snapshot` 均来自业务状态（ChatMessage/DocumentQARecord、LegalAsyncJob、AgentRun）的同一持久化来源；高风险写操作（审批恢复、取消）复用与 REST 相同的服务与权限校验，不因来自 WebSocket 而绕过。
