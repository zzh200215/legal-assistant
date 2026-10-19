# step17：证据可核验性在线指标（C 项）

> 起因：`docs/review/step16-consultation-review.md` 的结论是「主链路的短板首先在**证据可得性**」。
> 离线评审能看出这件事，但**用户在提问时看不到**——他看到的是一段流畅的答复，不知道里面的引用
> 到底核没核到条文。这一轮把「引用能不能被核验」做成**答复自带的在线指标**。

## 0 目标与边界

**做**：咨询生成时把「本次引用有多少条真的核到了法源条文」算成确定性统计，随答复返回；
结果卡与 copilot 面板显示徽标与说明；老记录不假装有结论。

**不做**：
- 不做全文语义核对——`verified` 的语义是「引用里写的条号在已收录条文中**存在**」，不是「条文内容支持这句话」；
- 不回溯历史记录（历史 `verification` 是生成时快照，见 step16 §9）；
- 不覆盖用户材料（合同/文书正文的核验走忠实度那条链路）；
- 不新增模型调用——指标是纯数据库核对，零额外成本、不会失败。

## 1 交付物

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 口径 | `app/services/legal/legal_reference_service.py` | `_article_verification()` 第三返回值 `article_scope`（`verified` / `missing` / `no_corpus` / `no_numbers` / `no_db`）；`enrich_references()` 给没挂到法源的引用写 `article_scope = "no_source"`；新增 `evidence_summary(refs, *, materials=0)` |
| 落库 | `alembic/versions/20261019_0100_consultation_evidence.py` | `legal_consultations.evidence_json`（Text，可空）；`revision 20261019_0100` ← `20261019_0099` |
| 生成 | `app/services/legal/legal_service.py:1019 / :1062` | 两条返回路径（LLM 分支 / 确定性降级分支）都 `evidence_out.update(evidence_summary(refs, materials=1 if context_meta else 0))` |
| 接口 | `app/services/legal/legal_workspace_service.py:149 / :541` | `serialize_workspace_row` 返回 `evidence`；`create_consultation` 落 `row.evidence_json`；WS `consult_done` 与 REST 同形状 |
| 前端 | `frontend/src/composables/copilotLayers.js:270` | `deriveVerifiability(result)` → `{level, label, note, tone, references, articleVerified, articleMissing, noCorpus, noNumbers, unmatched, materials}`；`deriveCopilotLayers()` 一并返回 |
| 呈现 | `frontend/src/components/copilot/CopilotAnswer.vue:13-17`、`frontend/src/components/legal/LegalConsultationsTab.vue:28-34` | 面板 meta 行 `.cop-tag-*`、结果卡 `el-tag`，文案 `依据：{{ label }}`，`title` 为后端 `note` 原文 |
| 测试 | `tests/test_legal_evidence_summary.py`、`frontend/src/composables/__tests__/copilotLayers.test.js`、`frontend/tests/e2e/copilot-dock.spec.js` | 后端 16 例、前端单元（`deriveVerifiability` 4 例）、面板 e2e 2 例 |
| 证据 | `docs/review/step17-evidence-card.png`、`step17-evidence-dock.png` | 真机历史记录结果卡 / 真机新答复面板 |

## 2 判定口径

**每条引用的 `article_scope`（机器可读，落进 `references_json` 的 `verification` 里）**

| scope | 含义 | 是否计入「核过」 |
| --- | --- | --- |
| `verified` | 引用条号全部在已收录条文中找到 | ✅ 通过 |
| `missing` | 法源有条文库，但引用条号没找到 | ✅ 核过（结论=未通过） |
| `no_corpus` | 该法源没有条文级数据 | ❌ 没核过 |
| `no_numbers` | 引用没写条号 | ❌ 没核过 |
| `no_source` | 引用没挂到法源库（或核对环境无库 `no_db`） | ❌ 没核过 |

**整条答复的 `level`（给用户看的四档）**

| level | 条件 | 徽标文案 | 色调 |
| --- | --- | --- | --- |
| `strong` | 有引用，且**全部** `verified` | 引用已逐条核对 | success |
| `partial` | 有引用，`verified` 至少一条但不全 | 部分引用已核对 | neutral |
| `weak` | 有引用，但一条都没核到 | 引用待核 | warning |
| `none` | 本次答复没有引用 | 未引用法源 | neutral |

**关键口径**：`article_checked = verified + missing`——「核过」的分母只算**真的做了条文级核对**的引用；
`no_corpus` / `no_numbers` / `no_source` 单列，不摊进分母。这样「没核验过」不会被算成「核验失败」，
和 step16 里那条结论（`faithful=false` 常常是「无法核验」而不是「答错」）保持一致。

## 3 验收

| 验收项 | 命令 / 对象 | 结果 |
| --- | --- | --- |
| 历史记录（真机读回） | `GET /api/legal/consultations/63`（demo_reviewer / 案件 #4） | `evidence = {references: 2, article_verified: 1, article_missing: 0, no_corpus: 1, level: "partial", label: "部分引用已核对"}`，结果卡实拍 `step17-evidence-card.png` |
| 新答复（真机 + 真实 LLM 一次调用） | 面板提问「试用期最长可以约定多久？违法约定有什么后果？」 | `evidence = {references: 1, article_verified: 1, article_checked: 1, level: "strong"}`，徽标「依据：引用已逐条核对」，`title`「1 条引用已与法源条文核对一致」，实拍 `step17-evidence-dock.png` |
| 老记录不假装 | 无 `evidence` 字段的历史答复 | `deriveVerifiability()` 返回 `null`，前端不渲染徽标 |
| 后端 | `pytest tests/test_legal_evidence_summary.py` | **16 passed** |
| 后端子集 | 9 个 legal 相关测试文件 | **135 passed** |
| 前端单元 | `npm run test:unit` | **83 passed** |
| 面板 e2e | `copilot-dock` + `copilot-case-context` + `entry-convergence` | **25 passed** |
| 静态门禁 | `npx eslint src` / `npm run build` / `npm run bundle:check` | 0 error / 10 warning；build exit 0；预算四项全绿（入口 raw 114.5/120、gzip 41.0/42、最大 chunk 121.8/130、总资产 1586.4/1600） |

## 4 关键决策

1. **在生成时算，不在前端猜**。指标是确定性数据库核对的结果，跟着答复一起落库；前端只做
   `level → 文案/色调` 的展示映射。前端拿不到 `evidence` 就不显示，绝不自己推断。
2. **老记录不回溯**。历史答复的 `verification` 是生成时的事实（step16 §9 已决定不写回），
   因此老记录没有 `evidence_json`；显示「依据：未知」比显示一个事后算出来的数字更诚实。
3. **`no_corpus` 与 `missing` 分开**。前者是「我们没收录这部法的条文」，后者是「条号没找到」——
   文案上分别说「该法源没有条文级数据」和「未在已收录的 N 条条文中找到（可能是该条未收录，
   也可能引用有误）」，不把「没收录」说成「引用错误」。
4. **零模型成本**。忠实度（第四层）要额外一次判官调用、可能不可用；可核验性是纯 DB 核对，
   两者互补：一个回答「依据本身能不能被核验」，一个回答「断言有没有依据」。

## 5 如实说明

1. `verified` 是**条号级**核对：只证明「引用的条号在这部法里存在」，不证明「这条法条支持这句话」。
   真正的语义核对在裁判层（step16）与忠实度（step14），两者都需要模型，因此都有成本与失败率。
2. 指标只覆盖**法源引用**，不含用户材料；材料进证据的核验在 step14 的判官证据里（`materials` 字段
   目前只做计数展示，`evidence_summary(materials=1 if context_meta else 0)` 表示本次带材料）。
3. `no_db`（无库环境）会落进 `no_source` 桶——两个都是「没核」，但文案上会显示「未挂到法源库」，
   在无库环境下略有偏差（当前只有测试环境会遇到）。
4. 历史 50 条真实咨询里只有 3 条能核到条文（step16），所以**现阶段这个徽标在老记录上多数显示
   「引用待核」**——这不是新缺陷，正是补语料（step16 §8/§9）之后要观察的指标。

## 6 观测 tab（管理员视角）

同一个指标接进系统页「实验观测」tab，用来回答「**语料覆盖到底够不够用**」——离线评审只能事后抽样，
这里能按窗口看分布。

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 聚合 | `app/services/observability/consultation_evidence.py`（新 mixin） | `get_consultation_evidence_stats(db, *, days, organization_id)`：按窗口聚合 `evidence_json`，输出四档分布 + 引用三态计数 + `checked_rate` + `evidence_coverage` |
| 接口 | `app/api/admin/analytics_api.py` | `GET /api/analytics/consultations/evidence-stats?days=30`（`require_admin_user`；管理员有组织时只统计本组织成员） |
| 前端 | `frontend/src/composables/useSystemObservability.js`、`frontend/src/components/system/SystemExperimentsTab.vue`、`frontend/src/api/analytics.js` | 与实验观测**共用同一个流量窗口**（7/30/90 天，一次刷新两块）；卡片 `证据可核验性（在线指标）` = 四个 `.app-stat`（逐条核对通过率 / 指标覆盖面 / 引用条数 / 语料缺口引用）+ 四档分布表 |

**真机读数（2026-10-19，近 30 天）**：50 条咨询答复、**2 条带指标**（指标当天上线），
`checked_rate = 100%`（2/2 条引用核到条文），`evidence_coverage = 4%`，四档 = `strong 1 / partial 1 / weak 0 / none 0`，
实拍 `docs/review/step17-evidence-admin-card.png`。

**口径细节**：`checked_rate` 的分母是 `article_checked`（只算真的做过条文级核对的引用），
`evidence_coverage` 才是「带指标的答复 / 窗口内全部咨询」——**4% 不是缺陷，是指标刚上线**；
两个比率分母不同，卡片上分行显示就是为了不让读者把两者混为一谈。

**测试**：`tests/test_consultation_evidence_stats.py` 6 例——四档标签与
`legal_reference_service._EVIDENCE_LEVELS` 逐字一致（漂移守卫）、分布与比率、无 `evidence`/坏 JSON
的行不摊进分母、窗口过滤、组织过滤、空窗口返回 `None` 而非 0%；`system-orgs` + `system-feedback-tasks`
+ `frontend-engineering` → **6 passed**。

## 7 下一步

- 观察项：新咨询的 `level` 分布（`strong` 占比）作为「语料覆盖是否够用」的在线信号；
- 可选：把 `evidence` 汇总进系统页的评测/观测 tab（现在只有离线评审能看到分布）；
- 未修开口项：`app/services/rag/retrieval.py:245` 的 BM25 索引路径偶发 `AttributeError`
  回退全表扫描（功能正确、只是慢）。
