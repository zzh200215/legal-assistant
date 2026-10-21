# 第二十五步交付说明：合同审查的「证据链」徽标与缺口筛选

> 一句话：把 step21 量出来的「53 条风险里只有 25 条带条款定位」做成产品里看得见的东西——风险面板顶部一个徽标、一个「无定位」筛选、每条缺口风险显式标注。缺证据不再静默。

- 日期：2026-10-21
- 对应：step17（咨询链路四档证据徽标）在合同审查链路上的落地；读数来自 step21 / step24
- 新增：`frontend/src/composables/useContractEvidence.js`、`docs/review/step25-evidence-chain.png`
- 改动：`RiskIssueItem.vue`（缺口 / 待核 chip）、`LegalContractTab.vue`（徽标 + 筛选 + 详情提示）、`useLegalWorkspacePresentation.js`（`located` 过滤）、`contract-upload.spec.js`（e2e 断言）

![合同审查证据链](step25-evidence-chain.png)

---

## 1. 为什么是这块

step21 的规则层读数：**53 条风险里只有 25 条带条款定位（47.2%）**，其中 3 条记录是 0 定位。但在这之前，产品页面上「有定位」和「没定位」的风险长得一模一样——用户看不出哪条结论有证据、哪条只是文字判断。

这正是四层可信体验里最容易被忽略的一层：**不是再算一个分数，而是让缺口可见**。咨询链路已经在答复顶部给了 `依据：引用已逐条核对 / 部分引用已核对 / 引用待核 / 未引用法源` 的四档徽标（step17），合同审查一直没有对应的东西。

---

## 2. 做了什么（四处）

| 位置 | 行为 |
| --- | --- |
| 风险面板头部 | 徽标 `1/2 有定位`，四档着色：strong 绿 / partial 琥珀 / weak·none 红；hover 显示档位说明 + `snippet 可核验 x/y` |
| 每条风险 | 有定位显示「第 N 段」；没有定位显示琥珀色「无条款定位」；snippet 在正文里匹配不到显示红色「定位待核」 |
| 筛选行 | 「无定位 N」一键只看缺口，再点恢复；没有缺口时按钮禁用 |
| 风险详情 | 选中缺口风险时给一行提示：「无定位：建议人工定位到具体条款后再确认」；snippet 不匹配时提示「定位待核」 |

空清单（0 条风险）不显示徽标——`0/0` 没有意义，也不该让用户以为「全都核对过了」。

---

## 3. 口径（与后端规则层同源）

- **有定位** = `source_location.paragraph` 非空；
- **snippet 可核验** = 去掉空白后是合同原文的子串（与后端 `_norm` 一致，截前 80 字匹配）；
- **分档**：全部有 = `strong`；≥50% = `partial`；>0 = `weak`；0 = `none`；空清单 = `none` 且不展示；
- 没有 snippet 的风险**不进 snippet 分母**，避免把「没有片段」算成核对通过。

实现方式是前端按同一口径**实时重算**（风险清单与合同正文都在手上），不另存一份可能过期的统计；离线评测的读数仍以 `scripts/run_contract_review_review.py` 为准。

---

## 4. 数字

- **e2e 夹具**：2 条风险 / 1 条有定位 → 徽标 `1/2 有定位`（partial），缺口 1，筛选后列表剩 1 条。
- **真实 7 条历史记录按同口径**：id1 3/3、id2 9/9、id3 9/9、id4–6 各 0/8、id7 4/8 → **3 条 strong、1 条 partial、3 条 none**。也就是说，在这个产品里点开那 3 条 18 字合同的审查结果，徽标会直接告诉用户「这些结论都没有定位」。

---

## 5. 验证

| 项 | 结果 |
| --- | --- |
| 单测（`useContractEvidence`） | **9 例**：空清单、全定位、2/3 与 1/3 与 1/2 分档、片段去空白匹配、匹配不到、无片段不计入分母、gaps、稳定 key、四档文案 |
| e2e（`contract-upload`） | 新增 4 条断言（徽标文本、缺口 chip 数量、详情提示、筛选后条目数），**1 passed** |
| lint | 0 error / 10 warning（既有基线） |
| build / bundle | 通过；总资产 1610.2 KB（预算 1700，+5 KB） |

---

## 6. 诚实边界

1. **两份实现仍在，但以后端为准**：服务端已在读接口下发 `evidence` / `evidence_summary`（见 §8），前端优先用；`useContractEvidence` 的本地计算只作为缺字段时的回退（老缓存、e2e 夹具、第三方调用）。彻底消除要删掉回退路径，代价是老数据不再有兜底。
2. **只覆盖合同审查**：咨询链路的四档徽标走的是 `evidence_json` 另一套实现，两者还没抽成同一个组件。
3. **snippet 是「包含」判断**：合同里出现重复句时会把另一处误判为可核验；真正的溯源需要段落锚点 + 字符偏移，当前 `source_location` 只有 paragraph + snippet。
4. **提示不是阻断**：缺口目前只做展示与筛选，没有接「无定位必须复核」的流程，也没有进入审核队列。

---

## 7. 下一步

1. ~~**后端下发 evidence summary**~~（已完成，见 §8）：审查结果里带 `evidence_summary` 与每条风险的 `evidence`，前端优先使用。
2. **缺口进入复核动作**：无定位的风险在 review-queue 里作为待办项，律师必须补定位或显式接受，才能通过审核。
3. **段落偏移**：给 `source_location` 补 `start/end` 偏移，把「包含判断」升级成真正的高亮定位。

---

## 8. 追加：证据链改由后端下发（2026-10-21 晚）

§6 第 1 条说的双份实现当天就收口了：**服务端在读接口现算**，前端不再重算。

**位置**：`app/services/legal/contract_evidence.py`（纯函数）+ `serialize_workspace_row`（列表/详情/上传/重提都走它）。

**返回什么**：

```json
{
  "risks": [ { "...": "...", "evidence": { "anchored": true, "paragraph": 1, "snippet_verified": true } } ],
  "evidence_summary": {
    "total": 2, "anchored": 1, "unanchored": 1,
    "snippets": 1, "verified": 1,
    "located_rate": 0.5, "snippet_rate": 1.0, "level": "partial"
  }
}
```

**为什么在读时算**：不写库、不做迁移，7 条历史记录立刻就有同一口径的结果；写时算反而要处理存量回填与版本漂移。

**前端**：`resolveEvidenceChain(risks, summary, content)` —— 有 `evidence_summary` 且每条风险都带 `evidence` 就用服务端口径；否则回退本地计算（e2e 夹具/老缓存）。e2e 的 mock 已补上这两个字段，覆盖服务端路径；单测 2 例覆盖优先级与回退。

**验证**：后端 9 例（空清单、全定位、片段缺失不入分母、片段不匹配、空白匹配、三档阈值、脏数据、不改原对象、序列化集成）；前端单测 95 passed；e2e `contract-upload` 1 passed；OpenAPI 契约无 drift（该接口无 response_model，字段为附加）。

**仍存在的边界**：snippet 仍是「包含」判断（重复句会误判）；缺口只提示不阻断（§6 第 4 条未变）。
