# Step 11：右栏助手带上「当前材料」（合同审查 / 文书草稿上下文注入）

- 日期：2026-10-18
- 对应诊断文档：`docs/review/2026-10-project-diagnosis.md` 90 天项「copilot 扩到文书审查 + 文书草稿（各自上下文注入）」
- 前置：`docs/review/step8-entry-convergence.md`（三入口归一，面板常驻）、`docs/review/step9-sentence-attribution.md`（逐句溯源）

## 0. 做什么 / 不做什么

**做**：用户在案件工作台的「合同审查」或「文书」tab 里选中某一份材料时，右栏助手知道"用户此刻在看哪一份"，提问时把它作为**参考材料**交给后端；后端按 id 读取正文、做权限校验，并把"本次实际参考了哪份、多少字"如实写进 `trace.contexts`。

**不做**：
- 不让前端上传正文（前端只传 id，正文永远由后端读）——否则客户端可以伪造上下文。
- 不给「案件台账 / 法律检索 / 审核队列」加当前对象：这些视图没有"单一选中材料"的语义，不硬凑。
- 不做多材料同时参考：一次只带一份，两份同时给返回 400（`LEGAL_CONTEXT_CONFLICT`）。
- 不做"自动把整个案件材料都塞给模型"：上下文是**用户显式选中**的那一份。

## 1. 交付物

| 文件 | 改动 |
| --- | --- |
| `frontend/src/composables/useCopilotContext.js` | 新增 `ITEM_KINDS`（URL 键 `contract_id`/`draft_id` → 接口字段 `contract_review_id`/`draft_id`）、`itemKind`/`itemId`/`hasItem`/`itemLabel`，新增 `copilotContextPayload(context)` |
| `frontend/src/components/CopilotDock.vue` | 上下文条显示当前材料（`合同 #12` / `文书 #7`）；把 `copilotContextPayload` 交给 `useLegalConsultations` |
| `frontend/src/composables/useLegalConsultations.js` | `submitConsultation` 合并 `context` 字段（**没有当前对象时不加任何字段**，保持 `{question, case_id}` 原样） |
| `frontend/src/components/legal/LegalContractTab.vue` | 当前审查的合同写进 URL（`contract_id`），刷新/直达按 id 恢复 |
| `frontend/src/components/legal/LegalDraftsTab.vue` | 当前草稿写进 URL（`draft_id`），刷新/直达按 id 恢复 |
| `frontend/src/views/LegalWorkspace.vue` | 切换案件时清掉 `consultation_id` / `contract_id` / `draft_id`，焦点不跨案件残留 |
| `app/api/legal/legal_api.py` | `ConsultationIn` 增加 `contract_review_id` / `draft_id`；`create_consultation` 透传；新增 `LEGAL_CONTEXT_CONFLICT`(400) 与 `LEGAL_CONTEXT_NOT_FOUND`(404) 错误映射 |
| `app/services/legal/legal_workspace_service.py` | `_load_consult_context(db, user, *, contract_review_id, draft_id)`：按 id 查、按 `user_id` 校验归属、两者互斥抛 `LEGAL_CONTEXT_CONFLICT`、查不到抛 `LEGAL_CONTEXT_NOT_FOUND`；把纯文本 + 元数据传给 `consultation_payload` |
| `app/services/legal/legal_service.py` | `_prepare_extra_context()`（截断、`chars` 计数）与 `_record_context_used()`（写 `trace.contexts`）；`consultation_payload(..., extra_context=...)` 把它接进提示词 |
| `tests/test_legal_consult_context.py` | 新增 14 例：权限（本人/协作者/他人/不存在）、互斥、`metadata` 透传、只接进问题不接进排序、拒答路径不记上下文、序列化 |
| `frontend/src/composables/__tests__/useCopilotContext.test.js` | 新增 3 例：当前对象识别、只在对应 tab 生效、payload 只带一个 id 字段 |
| `frontend/tests/e2e/copilot-case-context.spec.js` | 新增 3 例：合同 tab 发布 id 并带进提问、文书 tab 发布 id、咨询 tab 请求体形状不变 |
| `docs/review/step11-context-*.png` | 真机截图 3 张（面板上下文条 / 整页 / 答复） |

## 2. 数据形状（真机读回）

提问请求（前端只传 id）：

```json
{ "question": "这份合同的付款期限是怎么约定的？", "case_id": 4, "contract_review_id": 1 }
```

响应里的 `trace.contexts`（与 `steps` / `metrics` 同级，如实记录"这次真的参考了哪份"）：

```json
[{ "type": "contract_review", "id": 1, "title": "技术服务合同（演示）", "chars": 85 }]
```

文书：`[{ "type": "draft", "id": 2, "title": "劳动争议仲裁申请书", "chars": 317 }]`。

## 3. 决策

1. **前端只传 id**：正文由后端读，权限在后端校验；客户端塞正文没有入口（塞了也被忽略）。
2. **上下文与法源分开**：参考材料只接进提示词的「用户当前材料」段，不参与法源排序，也不会被当成法源引用（引用仍来自 `legal_sources`）。
3. **URL 是唯一状态源**：tab 把选中项写进 query，助手只读 URL——与 `consultation_id` 直达、`useCopilotContext` 只从 URL 解析案件的做法一致；刷新/直达可复现。
4. **恢复靠已加载列表筛**：合同审查/文书都没有"按 id 取单条"的接口，恢复时从列表里筛；筛不到就退回默认（最新一条），不额外造接口。
5. **互斥而不是合并**：一次只带一份材料，两份同时给返回 400——避免"参考了两份但 trace 只记一条"的糊涂账。
6. **查不到就报错，不静默降级**：不属于当前用户或不存在 → 404 `LEGAL_CONTEXT_NOT_FOUND`（与案件 404 同一口径）。
7. **接口字段名不统一是既有约定**：URL 用 `contract_id`，咨询接口用 `contract_review_id`；在 `ITEM_KINDS` 里显式区分 `queryKey` / `requestKey` 并写明理由，不在前端"顺手统一"成同一个名字。
8. **上下文进 trace**：`trace.contexts` 让"这次答的是哪份材料"可回放，也是面板将来显示"已参考：xxx"的依据。

## 4. 验收证据

| 项 | 结果 |
| --- | --- |
| 后端单测（新文件） | `tests/test_legal_consult_context.py` **14 passed** |
| 后端回归子集 | `pytest tests -k "legal or consult or reference or refusal or prompt or subscription or feishu or miniapp"` → **588 passed / 1 failed**（失败为既有环境不一致：`test_legal_encryption.py::test_aes_gcm_ciphertext_does_not_expose_plaintext` 断言密文前缀 `enc:v1:`，而本机 `.env` 为 `LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION=v2`，与本次改动无关） |
| 前端单测 | `npm run test:unit` → **74 passed**（原 71 + 3） |
| 前端 lint | `npx eslint src --ext .vue,.js` → **0 error / 10 warning**（均既有 `vue/attributes-order`） |
| 新增 e2e | `npx playwright test copilot-case-context` → **3 passed** |
| 相关 e2e 回归 | `copilot-dock / contract-upload / contracts-flow / draft-export / entry-convergence / pilot-workflow / u3-workflow` → **22 passed** |
| 真机（合同） | `POST /api/legal/consultations {case_id:4, contract_review_id:1}` → 200，`trace.contexts` 记 `技术服务合同（演示） chars 85`，答复实际引用了合同付款条款（签约后 5 日内 100 万 / 阶段验收 108 万 / 最终验收 60 万） |
| 真机（文书） | `{draft_id:2}` → 200，`trace.contexts` 记 `劳动争议仲裁申请书 chars 317`，答复说出草稿里的仲裁请求 |
| 真机（越权/不存在） | `contract_review_id: 999999` → **404 `LEGAL_CONTEXT_NOT_FOUND`** |
| 真机（两份互斥） | `contract_review_id + draft_id` → **400 `LEGAL_CONTEXT_CONFLICT`** |
| 真机截图 | `docs/review/step11-context-dock.png`（面板：当前 合同审查 · 案件 #4 · 合同 #1）、`step11-context-page.png`、`step11-context-answer.png` |

## 5. 边界与如实说明

1. **参考材料是"原文节选"不是"全量"**：`_prepare_extra_context` 对正文做长度截断，`chars` 记录的是**实际送进提示词的字符数**（真机 85 / 317 即截断后的值），不是原文全长。
2. **草稿正文可能为空**：草稿只有字段没有正文时，`text` 为空串 → 不记 `contexts`（不谎称参考了材料）。
3. **只覆盖两个 tab**：合同审查、文书草稿；合同台账（列表页）不发布当前对象。
4. **恢复依赖列表接口**：若该材料不在当前案件的列表里（例如列表分页/过滤掉了），恢复会退回默认最新一条，URL 里的 id 也会随之被改写。
5. **配额限制会挡住真机复验**：`free` 计划每月 5 次咨询，复验前需重置 `quota_usages.consultation_count`（本地核查用，不属于产品行为）。
6. **本机 LLM 网关偶发降级**（`Event loop is closed` 根因已在 step7 修掉，但网关侧超时/限流仍是外部风险）：trace 会如实标 `degraded`。

## 6. 下一步

- 面板显示「已参考：合同《xxx》」的可点入口（`trace.contexts` 已具备数据）。
- 参考材料支持多份（现在是互斥一份）。
- 合同台账列表页也发布"当前选中合同"（需先定义"选中"语义）。
- 参考材料的截断策略可配置（现在固定上限）。
