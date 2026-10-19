# 第四层落地：生成后忠实度校验（copilot）

> 2026-10-19。目标：把「忠实度未校验」这条常态降级补上——答案生成后由判官逐条核对
> 答复中的事实性断言是否有证据支持，结论落库并在面板上翻转标记。
> 前置：`docs/review/2026-10-project-diagnosis.md` 30 天项①（copilot 四层体验）里的
> 「忠实度常态显示未校验」，以及 step7/step9/step11 已落地的 trace、逐句溯源、材料上下文。

## 0 做什么 / 不做什么

**做**：
- 后端新增「生成后补判」接口 `POST /api/legal/consultations/{id}/faithfulness`（`refresh` 可重判）。
- 新列 `legal_consultations.faithfulness_json`（迁移 `20261019_0099`），结论随详情/列表透出。
- 判官证据 = 本次引用的法源条文 **+ 本次带进来的材料正文**（合同审查 / 文书草稿）。
- 前端：答案先渲染，随后自动补判；标记经历「忠实度校验中… → 通过 / 未通过」，未通过时列出未被支持的断言。

**不做**：
- 不把判官塞进创建咨询的请求里（判官真机约 10s，会让「提问→答案」多等一个数量级）。
- 判官不可用时不落库（留空下次可重试），也不覆盖响应里已有的结论。
- 不改判官 prompt、不引入第二个裁判模型（跨模型一致性另见 step13）。

## 1 交付物

| 层 | 文件 | 说明 |
| --- | --- | --- |
| 模型/迁移 | `app/models/legal.py`、`alembic/versions/20261019_0099_consultation_faithfulness.py` | `faithfulness_json TEXT`，`down_revision = 20261018_0098` |
| 服务 | `app/services/legal/legal_workspace_service.py` | `check_consultation_faithfulness()`、`_faithfulness_evidence()`、`_material_context_evidence()`、`_material_readable()`；`serialize_workspace_row` 透出 `faithfulness` |
| 接口 | `app/api/legal/legal_api.py` | `POST /consultations/{item_id}/faithfulness`，404 → `LEGAL_CONSULTATION_NOT_FOUND` |
| 前端 API | `frontend/src/api/legalWorkspace.js` | `checkConsultationFaithfulness(id, { refresh })` |
| 面板 | `frontend/src/components/CopilotDock.vue`、`frontend/src/components/copilot/CopilotAnswer.vue` | 答案后自动补判；`faithfulness-state` 驱动「校验中」态 |
| 测试 | `tests/test_legal_faithfulness.py`（18 例）、`frontend/tests/e2e/copilot-dock.spec.js`（新增 1 例） | 证据构造 / 落库 / 回读 / 重判 / 不可用不落库 / 权限 / 材料进证据 / 标记翻转 |
| 证据 | `docs/review/step14-faith-checking.png`、`step14-faith-pass.png`、`step14-faith-page.png` | 真后端实拍（demo_reviewer / 案件 #4 / 合同审查 tab） |

## 2 真机数据形状（逐字）

```json
{
  "available": true,
  "faithful": true,
  "unsupported": [],
  "reason": "付款金额、期限及仲裁机构均与片段3一致，无缺依据断言。",
  "evidence_count": 2,
  "checked_at": "2026-10-19T04:12:xx"
}
```

- 首次调用真机耗时 **10.8s**（判官一次完整 LLM 往返）；第二次调用 **0.0s**（命中已存结论，不再调判官）。
- 判官不可用时返回 `{"available": false, "faithful": null, "unsupported": [], "reason": "faithfulness_unavailable"}`，**不落库**。
- 他人/不存在的咨询 → `404 LEGAL_CONSULTATION_NOT_FOUND`。

## 3 关键决策

1. **判官不在生成路径上**：答案先返回，前端拿到 `id` 后再触发补判；面板用「校验中」占位，用户不必等判官。
2. **不落「判官挂了」**：`available=false` 不写库，留空以便下次重试——把不可用固化成结论比不判更误导。
3. **不覆盖已有结论**：前端只在 `verdict.available` 为真时替换响应里的 `faithfulness`（历史结论可能来自库）。
4. **证据 = 法源条文 + 材料正文**：真机实测发现，只给法源条文时，**带合同/文书提问的答复会被判成「未通过」**——因为付款金额、仲裁请求这些细节本来就在用户材料里。补上材料证据后同一份答复从 `faithful=false`（3 条 unsupported）翻成 `faithful=true`（`evidence_count` 1→2）。
5. **材料读权限与提问共用一套口径**：抽出 `_material_readable()`，注入上下文与判官取证走同一判断（本人 / 管理员·审核人 / 案件成员），避免「能拿来提问、却不算证据」的自相矛盾。
6. **材料 id 从 `trace_json.contexts` 读回**：不新增列，复用 step11 已落库的上下文元数据；读不到就跳过，不伪造证据。

## 4 验收证据

| 项 | 结果 |
| --- | --- |
| 后端单测 | `pytest tests/test_legal_faithfulness.py tests/test_legal_consult_context.py -q` → **32 passed** |
| 前端单测 | `npm run test:unit` → **74 passed** |
| e2e（copilot 相关） | `npx playwright test copilot-dock copilot-case-context --workers=1` → **17 passed**（含新增「生成后补判忠实度」） |
| 真机接口 | 首次 10.8s 出结论并落库；二次 0.0s 回读；`GET /consultations/46` 详情带同一份结论；越权 404 |
| 真机 UI | 面板经历「校验中 → 忠实度校验通过」，0 failed response / 0 pageerror；截图 3 张 |
| 材料进证据 | 同一份合同答复：修复前 `faithful=false`（3 条 unsupported，`evidence_count=1`）→ 修复后 `faithful=true`（`evidence_count=2`） |

## 5 如实边界

1. **判官只看本次引用 + 本次材料**：答复里超出这两者的常识性表述可能被判 unsupported（真机里判官会把「建议保留书面凭证」这类建议判为常识而放过，但口径仍取决于判官）。
2. **判官与生成同源**（`glm-5.2`），存在同族偏好；跨模型裁判只在离线评测里做过（step13），在线补判未接第二模型。
3. **一次判官一次成本**：每条咨询只在首次补判时花钱，之后回读；`refresh=true` 才会重判。
4. **面板只在当前答复上展示**：历史列表不带该标记（列表接口已透出字段，未在 UI 呈现）。
5. **demo 账号配额**：真机取证前需把 `quota_usages.consultation_count` 归零（free 计划 5 次/月）。

## 6 下一步

- B1：实时流式时间线（WS）——把「服务端记录后回放」升级为「边处理边推送」。
- 忠实度标记接入历史列表与整页视图（字段已透出，只差 UI）。
- 若接第二裁判模型，在线补判的 `reason` 需标注裁判来源。
