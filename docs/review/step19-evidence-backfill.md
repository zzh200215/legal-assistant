# step19：历史咨询的证据可核验性回填（让指标分母有意义）

2026-10-19 · 承接 [step17](step17-evidence-verifiability.md)（在线指标）与 [step18](step18-evidence-and-index-observability.md)（趋势与语料缺口）

## 0 这一轮解决什么 / 不解决什么

**做**：给 `evidence_json` 之前生成的老咨询补算同一口径的摘要，让系统页那张卡片的
「指标覆盖面」从 4% 变成真实数字（否则趋势、通过率都建立在 2 条样本上）。

**不做**：不改写 `references_json[].verification`——那是**生成当时**的快照，
改它等于改历史（用户 2026-10-19 已明确选择保留历史快照，见
[step16](step16-consultation-review.md) §9.4）。

## 1 交付物

| 类型 | 位置 | 说明 |
| --- | --- | --- |
| 回填脚本 | `scripts/backfill_consultation_evidence.py` | `--apply` / `--dry-run`（默认）/ `--user-id` / `--limit` / `--output` |
| 聚合拆分 | `app/services/observability/consultation_evidence.py` | 新增 `with_evidence_generated` / `with_evidence_recomputed`，趋势按天同样拆分 |
| 前端标注 | `frontend/src/composables/copilotLayers.js` | `deriveVerifiability()` 返回 `recomputed` 与 `title`（带「按当前法源库事后重算，非生成当时结论」） |
| 前端展示 | `SystemExperimentsTab.vue` / `CopilotAnswer.vue` / `LegalConsultationsTab.vue` | 卡头拆分文案、趋势表新增「事后重算」列、徽标 `title` 带口径说明 |
| 测试 | `tests/test_backfill_consultation_evidence.py`（5 例）、`tests/test_consultation_evidence_stats.py`（+1 例）、`frontend/src/composables/__tests__/copilotLayers.test.js`（+1 例） | 回填路径 / 拆分计数 / 标注文案 |
| 证据 | `docs/review/step19-evidence-split-card.png`、`step19-evidence-recomputed-card.png`、`step19-evidence-backfill-report.json` | 真机截图与回填报告 |

## 2 口径：什么叫「事后重算」

回填的摘要带两个额外字段：

```json
{ "level": "strong", "label": "引用已逐条核对", "basis": "recomputed",
  "computed_at": "2026-10-19T14:52:31+00:00", "...": "其余与生成时同口径" }
```

- `basis: "recomputed"` —— 这条摘要是**事后按当前法源库**重算的，回答的是
  「以今天的语料看，这些引用能不能核到」，**不是**「生成当时核过没有」；
- 没有 `basis` 字段的 = 生成当时算的。

聚合与界面都据此分开说：卡片写「51 条带指标（生成时 3 / 事后重算 48）」，
趋势表有独立的「事后重算」列，徽标 `title` 追加「（按当前法源库事后重算，非生成当时结论）」。

回填走**与生成时同一条代码路径**：句级条号合并（`_sentence_citation_numbers`）→
引用按标题补 `source_id`（`match_reference_to_source`）→ `enrich_references` → `evidence_summary`。
材料计数从 `trace_json.contexts` 读（生成时写入的「本次参考了哪份材料」）。

## 3 实测（真机，2026-10-19）

回填（`python -B scripts/backfill_consultation_evidence.py --apply`）：

```
待回填 57 条（有引用 54、无引用跳过 3）｜带材料上下文 8 条
四档分布：{strong: 26, partial: 14, weak: 14}
按用户：{3: {strong: 5}, 10: {strong: 6, weak: 6}, 15: {strong: 3}, 37: {strong: 12, partial: 14, weak: 8}}
已写回 54 条（basis=recomputed，不改 references_json）
```

回填前后的管理员聚合（近 30 天，同一窗口）：

| 读数 | 回填前 | 回填后 |
| --- | --- | --- |
| 带指标答复 | 2 | **51** |
| 指标覆盖面 | 4% | **100%** |
| 其中生成时 / 事后重算 | — | 3 / 48 |
| 逐条核对通过率 | 100%（2/2） | 100%（51/51） |
| 引用条数 | 3 | 87（未标条号 33、未挂法源 0、法源缺条文 3） |
| 四档分布 | strong 1 / partial 1 | strong 21 / partial 15 / weak 15 / none 0 |
| 法源引用榜 | 2 个法源 | 12 个（含 2 个模板类「缺条文」，即语料缺口清单） |

真机截图：[step19-evidence-split-card.png](step19-evidence-split-card.png)（卡头「生成时 3 / 事后重算 48」、
趋势表带「事后重算」列、法源表「有条文 / 缺条文」）；
[step19-evidence-recomputed-card.png](step19-evidence-recomputed-card.png)（老记录 id 55 的结果卡：
徽标「依据：引用已逐条核对」，`title` = 「2 条引用已与法源条文核对一致（按当前法源库事后重算，非生成当时结论）」）。

## 4 关键决策

1. **只补新列，不动历史快照**：`evidence_json` 是派生指标，写它不改写「生成当时核过没有」；
   `references_json[].verification` 一个字都没动（回填脚本里没有任何写它的代码路径）。
2. **口径必须可见**：如果只把数字做大而不标 `basis`，那张卡片就从「观测」变成「粉饰」。
   所以后端拆分计数、前端写进 `title`、文档写进口径，三层都留痕。
3. **没有引用的老记录不硬塞摘要**：`references_json` 为 `[]` 时返回 `None`（跳过），
   而不是记一个「未引用法源」——那是「当时没引用」而不是「统计过没有依据」。
4. **回填可重跑、可识别**：脚本只处理 `evidence_json IS NULL` 的行，重复执行不会覆盖；
   需要撤销时按 `basis = 'recomputed'` 清空即可（一条 SQL）。

## 5 如实说明（边界）

1. **回填数字不是历史事实**：51/51 通过率说的是「按今天 98 条劳动合同法 + 54 条劳动争议调解仲裁法
   的语料看，这些引用都核得到」，不是「当时的答复都核验过」。界面上已标注。
2. **通过率 100% 的成因**：分母 `article_checked` 只含**真的做过条文级核对**的引用（51/87），
   未标条号的 33 条不进分母——这是既有口径（step17 §2），不是这一轮新引入的宽松。
3. **覆盖面 100% 是窗口效应**：近 30 天窗口内只有 3 条「生成时」记录没有指标、其余都回填了；
   窗口内全部 51 条都有指标，所以是 100%。更早的记录不在窗口内，未统计。
4. **`weak` 15 条的含义**：这些答复的引用没标条号或所在法源没有条文级数据，
   回填后依然是「待核」——回填不会把「无法核验」变成「已核验」。

## 6 下一步

- 撤销/重跑口径：需要时按 `basis='recomputed'` 清空后重跑（`--apply`）；
- 若要让「生成时」样本变多，只能靠新咨询自然累积——卡片上的拆分列会自己显示这个迁移过程；
- 语料缺口清单现在只剩 2 个模板类法源（本就不该有条文），可以视为已收口。
