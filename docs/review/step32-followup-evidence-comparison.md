# 第三十二步交付说明：补充后自动对比引用变化

> 一句话：提交人按「去补充」追问后，结果卡上自动出现「补充前后对比」——引用从几条到几条、新增/移除了哪些法源、句级覆盖与忠实度怎么变，不用自己回头对照。

- 日期：2026-10-23
- 迁移：`alembic/versions/20261023_0103_consultation_parent.py`（MySQL 已执行，head `0102 → 0103`）
- 后端：`app/models/legal.py`（`parent_id`）、`app/services/legal/consultation_evidence.py`（对比函数 + 挂载）、`legal_workspace_service.py`（追问时写 `parent_id`）、`legal_api.py`（详情/追问响应挂 `comparison`）
- 前端：`LegalConsultationsTab.vue` 结果卡「补充前后对比」块
- 测试：`tests/test_consultation_evidence.py`（+4 例）、`frontend/tests/e2e/u3-workflow.spec.js`

---

## 1. 数据链路

- 追问创建新咨询时写入 `parent_id = 原咨询 id`（`legal_consultations.parent_id`，可空、带索引）；
- 详情接口 `GET /legal/consultations/{id}` 与追问接口 `POST /consultations/{id}/followup` 在返回前调用 `attach_consultation_comparison`：有 parent 就现场算对比；
- 列表接口不挂对比（避免 N+1），列表只看 `evidence_summary`。

## 2. 对比口径（`compare_consultation_evidence`）

| 维度 | 规则 |
| --- | --- |
| 引用 | 按 `source_id`（缺失时用 `title`）做集合差，给出 `added` / `removed` |
| 句级覆盖 | 取 `attribution.total_sentences / matched_sentences`，换算成百分比；无溯源为 `—` |
| 等级 | `summarize_consultation_evidence` 的 `level` 前后值 |
| 忠实度 | `pass / fail / unchecked` 前后值 |

摘要行示例：`引用 0 → 2（+2/-0） · 句级覆盖 —% → 75% · 等级 none → partial`。

## 3. 前端

结果卡「参考依据」上方新增**补充前后对比**：摘要行 + 新增引用（绿色标签）/ 移除引用（中性标签）+ 忠实度变化。追问成功后接口响应直接带 `comparison`，无需额外请求；从历史重新打开追问记录也能看到（详情接口同样挂）。

## 4. 验证

| 项 | 结果 |
| --- | --- |
| 迁移 | `alembic upgrade head` 成功，head `20261023_0103`，`parent_id` 列与索引存在 |
| 单测 | 新增引用 + 覆盖 50%→100%（等级 partial→strong）、移除引用（strong→none）、忠实度 unchecked→fail、摘要文案 |
| 接口 | 追问记录详情返回 `comparison`（before 1 / after 2、added=实施条例、等级 partial→strong） |
| e2e（u3） | 「去补充」预填追问框 → 点「追问」→ 断言请求体 → 结果卡出现「补充前后对比」、`+2/-0`、新增法源、忠实度「未校验 → 通过」 |
| 门禁 | 见提交说明 |

## 5. 边界

1. **只对比有 `parent_id` 的追问**：迁移前的历史追问没有 parent，不显示对比。
2. **引用按来源/标题匹配**：同一法源不同版本算同一条，不会标成「新增」。
3. **不比较正文措辞**：只比引用、覆盖、等级、忠实度，不做文本 diff。
4. **列表不展示**：对比只在详情/追问响应里算，列表保持轻量。
5. **对比是读时派生**：不落库；原咨询后来被修改（当前没有编辑入口）也不会改变历史对比结果。
