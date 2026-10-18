# Step 7：copilot 时间线从「事后回放」升级为服务端 trace

> 承接 `docs/review/2026-10-project-diagnosis.md` 主线（copilot 四层可解释性）里的第 ① 层。
> 这一步只解决「过程从哪来」：面板上的处理步骤由后端真实记录并随结果返回，不再是前端按响应字段猜出来的。
> 完成时间：2026-10-18。

## 0. 目标与边界

**做**

- 咨询链路（`POST /api/legal/consultations`）在生成过程中记录结构化步骤：检索法源 / 生成建议 / 抽取事实 / 核验引用，每步带 `status`、`duration_ms`、`detail`。
- 随结果持久化（新列 `legal_consultations.trace_json`），并在 `serialize_workspace_row` 里以 `trace` 字段返回。
- 面板有 `trace` 时显示服务端真实步骤（含状态与耗时，标题「服务端步骤」）；没有 `trace` 时保持原有降级（「事后回放」+ 原有文案）。
- 补后端单测、前端单测、e2e 用例，并起真实后端实拍。

**不做（本轮明确不碰）**

- 追问（followup）链路不埋点：它复用同一序列化出口，但没有过程记录，继续走事后回放。
- 不改 `/api/ws/agent`（agentic RAG 的 WebSocket 事件流）——那是另一条链路，与法律咨询无关。
- 不做多轮/多跳检索：咨询管线是单跳（一次检索 + 一次生成），trace 里不编造轮次与子问题。
- 历史记录（`trace_json` 为空）不回填：老数据继续事后回放，这是有意的——回填等于伪造过程。

## 1. 交付物

| 文件 | 改动 |
| --- | --- |
| `app/models/legal.py` | `LegalConsultation` 新增 `trace_json`（`Text`，可空，注释「处理过程 trace，JSON」） |
| `alembic/versions/20261018_0097_consultation_trace.py` | 迁移：`revision="20261018_0097"` / `down_revision="20261010_0096"`，带 `sa.inspect` 幂等守卫，加列 / 反向删列 |
| `app/services/legal/legal_service.py` | `consultation_payload(..., trace_out=None)` 埋点；新增 `_trace_step()` / `_elapsed_ms()` / `_reference_verification_counts()` / `_attach_trace_metrics()`；LLM 失败或 JSON 不可解析时 `generate` 步标 `degraded`，拒答时只记一步 `refused` |
| `app/services/legal/legal_workspace_service.py` | `create_consultation` 建 trace 容器 → 传给 `consultation_payload` → 落库 `trace_json`；`serialize_workspace_row` 咨询分支返回 `trace`（解析失败返回 `None`，不抛错） |
| `app/services/legal/legal_domain_service.py` | `persist_consultation_artifacts(..., trace=...)` 写入 `trace_json` |
| `frontend/src/composables/copilotLayers.js` | `normalizeServerTrace()` / `formatStepDuration()`；`deriveTraceReplay()` 入口分支：有 trace 走 `mode='trace'`（标题「服务端步骤」），否则原样回退 |
| `frontend/src/components/copilot/CopilotTrace.vue` | 渲染步骤状态与耗时（新类 `.cop-step-status` / `.cop-step-ms`，不复用 `.cop-mode`） |
| `frontend/src/components/CopilotDock.vue` | 加载期说明改为「服务端会记录处理步骤，答复回来后展示」——**保留「没有实时步骤事件」**（`frontend/tests/e2e/copilot-dock.spec.js:139` 断言该文案） |
| `tests/test_legal_reference_verification.py` | 6 条 trace 用例：四步齐全且有序、无 LLM 时标 `degraded`、核验计数与文案同源且 metrics 自洽、模型作答时标 `success`、拒答只记一步 `refused`、不传 `trace_out` 时返回与旧行为一致 |
| `tests/test_legal_workspace_module.py` / `tests/test_legal_domain_service.py` | 序列化透出 `trace`、落库写入 `trace_json` |
| `frontend/src/composables/__tests__/copilotLayers.test.js` | trace 优先 / 畸形 trace 回退 / 空数组回退 |
| `frontend/tests/e2e/copilot-dock.spec.js` | 2 条新用例：带 `trace` 的 mock 显示服务端步骤与耗时；无 `trace` 仍是事后回放 |
| `frontend/tests/e2e/developer-apps.spec.js` | 顺手修掉历史 flake：两条请求断言改为 `expect.poll`（原先在 UI 渲染后立即断言并发请求是否已被拦截，与 Vite 冷启动转换抢时间） |
| `frontend/tests/e2e/verify-trace-real.spec.js` | 新增真后端只读集成断言：列表与详情给出同一份 trace（证明是落库读回）、`trace_version`/`source`、状态取值合法、四步顺序或拒答单步、`metrics` 自洽；不触发 LLM 调用，库里无带 trace 的记录时按跳过处理 |

## 2. trace 形状（真实后端逐字读回，`GET /api/legal/consultations/23`）

```json
{
  "trace_version": 1,
  "source": "legal_consultation",
  "steps": [
    {"id": "retrieve", "label": "检索法源", "status": "success", "duration_ms": 0, "detail": "带回 2 条候选法源"},
    {"id": "generate", "label": "生成建议", "status": "success", "duration_ms": 16840, "detail": "模型返回结构化结果 · 模型 glm-5.2 · 提示词 v1"},
    {"id": "facts", "label": "抽取事实", "status": "success", "duration_ms": 0, "detail": "已确认 2 项 · 待补充 3 项"},
    {"id": "verify", "label": "核验引用", "status": "success", "duration_ms": 1, "detail": "0 条已核验 · 0 条未核验 · 2 条无核验字段"}
  ]
}
```

后加的 `metrics`（同日 17:5x 之后的记录才有；老记录没有该字段，前端不依赖它）：

```json
{"rounds": 1, "candidate_sources": 2, "references": 2, "verified": 0, "unverified": 2, "unmarked": 0}
```

`status` 取值：`success`（完成）/ `degraded`（降级到确定性逻辑，或检索无结果）/ `skipped`（无需执行，如无法源引用时跳过核验）/ `refused`（合规拦截，只有一步）。前端映射为中文徽标：完成 / 降级 / 跳过 / 已拦截（`frontend/src/composables/copilotLayers.js:97`）。`metrics.rounds` 恒为 `1`，因为咨询是单跳链路——写一个不存在的轮次数才是失真。

## 3. 决策

1. **trace 存 JSON 列，不建新表**：`legal_consultations` 已有 `known_facts_json` / `missing_facts_json` / `references_json` / `model_snapshot_json` 四个 TEXT 列，列 JSON blob 是既有范式，第 5 个列比一张关联表更省事、更好删。
2. **埋点放在 `consultation_payload()` 内部，而不是 API 层**：过程发生在管线里，API 层只能测出总耗时；放管线里才能给出「检索命中几条 / 是否走了 LLM / 核验几条」这种有意义的分步信息。
3. **`detail` 用中文短句，`metrics` 用机读数字**：面板只需要文案，评测与接口调试需要能直接取数，两者同源计算、互不推导。
4. **有 trace 优先、没有就回退**：不做版本协商、不做灰度开关。老记录、追问链路、未来新增的调用方一律自动落到原路径，前端零配置。
5. **降级要显式写进 trace**：LLM 调用失败或 JSON 不可解析时，`generate` 步标 `degraded` 并把原因写进 `detail`——这正是「可解释性」要暴露的东西（实测就抓到一次网关秒级失败，见 §5）。
6. **不复用 `.cop-mode` 承载状态/耗时**：新增独立类名，避免把「模式标签」和「步骤状态」耦合成一个断言目标。

## 4. 验收证据（2026-10-18）

| 项 | 结果 |
| --- | --- |
| 迁移 | `alembic upgrade head` 通过，MySQL 侧确认 `legal_consultations.trace_json` 存在（`text`，可空） |
| 后端测试 | `pytest tests/test_legal_workspace_module.py tests/test_legal_domain_service.py tests/test_legal_reference_verification.py tests/test_legal_refusal.py tests/test_legal_e2e_smoke.py` → **56 passed** |
| 真后端接口 | `POST /api/legal/consultations` 返回 4 步 trace；`GET /api/legal/consultations/23` 从库里读回同结构（§2 即该次读回逐字）；DB 侧 `trace_json` 实测 497–520 字符（`scripts/_tmp_probe_trace_db.py`，id 19–23 为本轮真机创建），改动前的老记录（id ≤ 18）该列为空、不回填 |
| 真后端面板 | 实拍 `docs/review/step7-trace-real-panel.png`、`docs/review/step7-trace-real-dock.png`：面板标题「服务端步骤」，四步显示状态与耗时 |
| 前端单测 | `npm run test:unit` → **66 passed**（含 3 条 trace 归一化用例） |
| e2e | `npx playwright test copilot-dock --workers=1` → **11 passed**（9 条既有 + 2 条 trace 用例）；全量 `E2E_RUN_INTEGRATION=true npx playwright test --workers=1` → **44 passed / 0 failed**（含 5 条真后端集成用例：系统页 15 tab、trace 只读核查、U-2/U-4 三条） |
| lint / build | `npx eslint src --ext .vue,.js` → 0 error / 13 warning；`npm run build` → exit 0（入口 `dist/assets/index-CayFjLqX.js` 145.83 kB raw / 51.00 kB gzip） |

## 5. 开口项

1. **LLM 网关偶发秒级失败或长时间不返回**：连续 3 次真实请求里 1 次在 18ms 内返回 `LLM call failed, falling back to deterministic logic: Event loop is closed`（另两次分别 7.9s / 16.8s 正常）；全量 e2e 里临时实拍用例还遇到一次 200s 内没有任何响应（该次是 demo_lawyer 配额耗尽 429 之外的另一类）。trace 如实把降级那次标成「降级」，但这是产品可靠性问题，本轮未修，需要单独排查（客户端连接复用 / 事件循环生命周期 / 超时与重试）。
2. **`demo_lawyer` 月度咨询配额已耗尽**（`本月咨询配额已用完，请升级订阅`，429）：真实后端的咨询实拍改用 `admin` 账号完成（本轮真机记录 id 19–23 均为 admin 创建）；`verify-u2-u4` 集成用例不受影响（它 mock 咨询接口，只对真实法源库取条文）。
3. **追问链路未埋点**：`POST /legal/consultations/{id}/followup` 仍走事后回放。补埋点成本不高（同一容器），但需要先确认追问是否也要按步展示。
4. **`trace_json` 无清理策略**：按现有 `*_json` 列的做法长期保留，量级可控（每行几百字节），暂不加 TTL。
5. **`detail` 文案未做 i18n**：与全站一致（中文硬编码），不单独处理。
6. **面板未展示 `metrics`**：目前只渲染 `label` / `detail` / 状态 / 耗时；`metrics` 供接口与评测使用，若要在面板显示「命中 2 条」这类徽标，需要另加渲染位。
7. **（与 trace 无关的既有失败，本轮验证时发现）** `tests/test_legal_encryption.py::LegalEncryptionTests::test_aes_gcm_ciphertext_does_not_expose_plaintext` 断言密文以 `enc:v1:` 开头，但本机 `.env` 的 `LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION=v2`（做过密钥轮换），该用例没像同文件其它用例那样 patch `_settings()`，因此恒失败。属测试与环境不一致，未在本轮修改；宽范围后端回归 `pytest tests -k "legal or consult or reference or refusal or generation_eval"` 结果为 **394 passed / 1 failed（即该条）**。

## 6. 下一步

- 三层评审（规则 → LLM-judge → 人工抽检，验收一致率 ≥ 0.8）——诊断文档 30 天项 ③。
- 入口收敛：Chat 并入 copilot、保留「展开为整页」、agent 入口对非管理员可见——30 天项 ④。
- 逐句溯源：现在引用核验到「条」级，逐句定位需要 LLM 返回句子级 span，属独立工作量。
- `npm run bundle:check` 预算决策（当前入口 145.8/120 KB raw、51.0/42 KB gzip、总资产超 1600 KB）。
