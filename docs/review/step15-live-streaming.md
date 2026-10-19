# step15：copilot 时间线改成真流式（WS 步骤推送）

> 目标：把右侧面板的「处理过程」从「答复回来后由响应字段重建 / 服务端记录」升级为**边生成边推送**——用户提问后，答复还没出来就能看到服务端正在做哪一步、用了多久。
> 范围：法律咨询（案件工作台「法律咨询」tab + 右栏助手）。追问链路、Agent 执行链路不在本轮。

## 0 做什么 / 不做什么

**做**
- 复用既有 WS 通道 `/api/ws/chat`，新增客户端消息类型 `consult` 与服务端事件 `consult_step` / `consult_done`。
- 管线每完成一步就推送一次：检索法源 → 生成建议 → 抽取事实 → 核验引用（带 `status` / `duration_ms` / `detail`）。
- 面板在答复到达前显示「实时步骤」块与实时说明；答复到达后让位给正式 trace（同一批步骤，来自响应）。
- 前端**优先 WS、失败回退 REST**：连不上（4s 未建连）或通道报错且**一个步骤都没收到**时，退回原来的 REST 一次性请求，行为与之前逐字相同。

**不做**
- 不做 token 级（逐字）流式：结果仍由服务端落库后整包下发，流式的是**步骤**而不是正文。
- 不给追问链路补步骤推送（与 `trace` 同口径：追问不埋点）。
- 不新开 WebSocket 端点、不改鉴权方式、不改结果字段形状。

## 1 交付物

| 层 | 文件 | 说明 |
| --- | --- | --- |
| 后端 | `app/api/conversation/ws_api.py` | 新增 `_handle_consult`：校验 `content`（≤ `MAX_WS_MESSAGE_LENGTH`）与可选 `case_id` / `contract_review_id` / `draft_id`；`step_callback` 把每步包成 `consult_step` 推送；错误映射 `QUOTA_EXCEEDED` / `LEGAL_CONTEXT_CONFLICT` / `LookupError`；结束推 `consult_done` |
| 后端 | `app/services/legal/legal_service.py` | `consultation_payload(..., step_callback=None)`；`_record` 改 async 并在 8 个步骤点推送；`trace_out` 仍是同一份数据（推送与落库同源） |
| 后端 | `app/services/legal/legal_workspace_service.py` | `create_consultation(..., step_callback=None)` 透传 |
| 契约 | `docs/websocket-protocol.md`、`docs/ws-events.schema.json` | 新增 `consult` 客户端消息与 `consult_step` / `consult_done` 事件定义 |
| 测试 | `tests/test_ws_consult_stream.py` | 8 例：逐帧顺序与字段、空内容拒绝、配额/上下文冲突/不存在映射、非法 `case_id`、旧 `chat` 类型兼容 |
| 前端 | `frontend/src/utils/consultSocket.js` | `streamConsultation({payload, onStep, …})`：连上后逐帧回调，`consult_done` 解析为结果；错误带 `.code` 与 `.streamed` |
| 前端 | `frontend/src/components/CopilotDock.vue` | 实时步骤块 `.cop-live` / `.cop-live-step`；说明文案在实时阶段切换为「实时推送」；`runConsultation` 先 WS 后 REST |
| 前端 | `frontend/src/composables/copilotLayers.js` | 导出 `traceStatusLabel(status)`（复用状态词表）；trace 说明改为「这里展示的是最终记录，不是实时推送的事件流」 |
| 测试 | `frontend/src/utils/__tests__/consultSocket.test.js`、`frontend/tests/e2e/copilot-dock.spec.js`、`frontend/tests/e2e/copilot-case-context.spec.js` | 6 例单测（FakeWebSocket）；e2e 新增「WS 逐帧」「WS 不可用回退 REST」两条，并给既有 mock 加默认 `routeWebSocket(...→close())` 保持 REST 断言不变 |
| 证据 | `docs/review/step15-stream-live.png`、`step15-stream-done.png` | 真后端实拍：生成中（只有步骤、没有答复）/ 完成后（答复 + 4 步 trace） |

## 2 真机数据形状

生成中推送（逐帧，`consult_step`）：

```
检索法源 | 完成 | 467ms | 带回 4 条候选法源
生成建议 | 完成 | 18.4s | 模型返回结构化结果 · 模型 glm-5.2 · 提示词 v1
抽取事实 | 完成 | 已确认 1 项 · 待补充 3 项
核验引用 | 完成 | 2ms  | 1 条已核验 · 0 条未核验 · 1 条无核验字段
```

结束帧（`consult_done`）携带的 `trace` 与 REST 的 `trace` 形状完全一致：

```json
{
  "trace_version": 1,
  "source": "legal_consultation",
  "steps": [
    {"id": "retrieve", "label": "检索法源", "status": "success", "duration_ms": 467, "detail": "带回 4 条候选法源"},
    {"id": "generate", "label": "生成建议", "status": "success", "duration_ms": 18400, "detail": "模型返回结构化结果 · 模型 glm-5.2 · 提示词 v1"},
    {"id": "facts", "label": "抽取事实", "status": "success", "duration_ms": 0, "detail": "已确认 1 项 · 待补充 3 项"},
    {"id": "verify", "label": "核验引用", "status": "success", "duration_ms": 2, "detail": "1 条已核验 · 0 条未核验 · 1 条无核验字段"}
  ],
  "metrics": {"rounds": 1, "candidate_sources": 4, "references": 2, "verified": 1, "unverified": 0, "unmarked": 1}
}
```

## 3 决策记录

1. **复用 `/api/ws/chat` 而不新开端点**：既有通道已有鉴权（`?token=` / `Authorization` / `Sec-WebSocket-Protocol`）、心跳、重连与 e2e mock 套路，新增消息类型比新端点便宜且不增加运维面。
2. **流式的是步骤，不是正文**：逐字流式要求把 LLM 调用改成 `stream=True` 并重做解析/校验/落库，收益（打字机效果）与风险（半截 JSON、失败重试语义）不成比例；步骤流式已经解决了「等待 15s 不知道在干什么」的真实痛点。
3. **结果仍整包下发且必须落库**：`consult_done` 里的 `row` 与 REST 的序列化结果同源（`serialize_workspace_row`），保证「流式路径」与「回退路径」渲染完全一致，也让 trace/attribution/faithfulness 的既有契约不变。
4. **前端优先 WS、失败回退 REST**：回退判据是「**一个步骤都没收到**」（`error.streamed === false`）。已经收到步骤再失败时不回退——否则会出现「实时步骤演到一半，又从头来一遍」的错乱。
5. **实时块在答复到达后消失**：最终 trace 与实时步骤是同一批数据（同源同序），同时显示两遍只会让人怀疑哪个是真的。
6. **连不上不重试**：`createWsClient(..., maxRetries: 0)` + 4s 建连超时，超时即回退 REST；咨询是低频交互，快速回退比静默等待更符合直觉。

## 4 验收证据

| 项 | 结果 |
| --- | --- |
| 后端单测 | `pytest tests/test_ws_consult_stream.py -q` → 8 passed |
| 前端单测 | `npm run test:unit` → **80 passed**（新增 consultSocket 6 例） |
| 前端 e2e | `npx playwright test copilot-dock copilot-case-context entry-convergence --workers=1` → **23 passed**（含新增「WS 逐帧」「WS 不可用回退 REST」） |
| lint / build | `npx eslint src --ext .vue,.js` → 0 error / 10 warning；`npm run build` → exit 0（入口 `dist/assets/index-MQjqTI2q.js` 112.35 kB raw / 41.74 gzip） |
| 包体预算 | `npm run bundle:check` → 四项全绿（入口 raw 113.8/120、gzip 40.8/42、最大页面 chunk 121.8/130、总资产 1585.3/1600） |
| 真机流式 | 临时 spec `zz-stream-real.spec.js`（真后端 8001，demo_reviewer / 案件 #4）：生成中 `.cop-live-step` 已可见且 `.cop-answer` 计数为 0，说明文案含「实时推送」；答复到达后实时块让位、trace 显示 4 步 —— **1 passed (22.6s)**；截图 `docs/review/step15-stream-live.png` / `step15-stream-done.png` |

## 5 如实边界

1. **不是逐字流式**：正文仍是一次性到达；「生成建议」这一步的耗时（真机 18.4s）期间，面板只能显示「进行中」，没有中间文本。
2. **追问不推步骤**：`create_consultation_followup` 未埋点，追问只有结果。
3. **回退后没有实时步骤**：WS 不可用（代理/网关拦截、token 失效、4s 未建连）时退回 REST，用户看不到过程（答复与 trace 仍完整）。
4. **实时块的耗时与服务端记录一致但不含排队时间**：`duration_ms` 由服务端测量，不含前端与网络耗时。
5. **首次运行需重启后端**：WS 新消息类型是代码级变更，热重载的后端不会自动生效（本次真机验证就踩到一次：未重启时前端静默回退 REST，面板没有实时步骤）。

## 6 下一步

- 若要真正的打字机效果，需要把 `generate` 改成流式并在前端做增量渲染（成本明显，收益待评估）。
- 把实时步骤接进「追问」与 Agent 执行链路（复用 `step_callback` 通道即可）。
- 面板在 WS 回退时给出可见提示（当前只在控制台/网络层可见），避免用户以为「本来就没有过程」。
