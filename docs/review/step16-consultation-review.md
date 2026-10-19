# 三层评审扩到产品主链路：法律咨询（copilot）

> 生成时间：2026-10-19。上游：`docs/review/step13-three-layer-review.md`（同一套三层口径跑在 RAG QA 数据集上）。
> 本文记录把**同一套评审口径搬到真实咨询记录**的结果：规则层跑全量、裁判层跨模型抽样、人工层沿用同一张抽检表格式。

## 0. 做什么 / 不做什么

**做**：对 `legal_consultations` 里**真实产生过的咨询**做三层评审——① 规则层（零模型调用，全量）；② 跨模型裁判层（glm-5.2 与 qwen-plus 各判一次忠实度与质量，抽样）；③ 产出与 `scripts/score_human_review.py` 兼容的人工抽检表。

**不做**：不新增产品接口、不改咨询链路行为、不做在线监控、不覆盖追问链路、不判「答案在法律上是否正确」（判官只判「能否被本次证据支持」）。

## 1. 交付物

| 交付物 | 说明 |
| --- | --- |
| `scripts/run_consultation_review.py` | 三层评审脚本（DB 直读真实咨询；`--sample` 抽样；输出 JSON + 人工抽检表 + 抽样自证） |
| `eval/outputs/consultation_review.json` | 评审产物（32 KB）：规则层全量聚合 + 裁判层逐条判罚 + 抽样自证 + 客户端统计 |
| `eval/outputs/consultation_spotcheck_sheet.md` | 人工抽检表（16 条目 = 8 条咨询 × 忠实度/质量） |
| `eval/outputs/consultation_spotcheck_sheet.build.json` | 抽样自证（seed / 分层大小 / 选中 id） |
| `docs/review/step16-consultation-review.md` | 本文 |

复现：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -B scripts\run_consultation_review.py --sample 8 --min-interval 8 `
    --output .tmp_consultation_review.json --sheet-output .tmp_consultation_spotcheck_sheet.md
# 本机 python 子进程写不了 eval/outputs，先写仓库根再 Move-Item
Move-Item .tmp_consultation_review.json eval\outputs\consultation_review.json -Force
```

## 2. 数据形状

`consultation_review.json` 顶层：`generated_at` / `source` / `models`（两个裁判的模型与出口）/ `sampling` / `coverage` / `rule_layer` / `judge_layer` / `divergence` / `cases[]`。

- `rule_layer`：`consultations` / `with_advice` / `references_total` / `verification{verified,unverified,unmarked,verified_rate}` / `attribution{sentences_total,sentences_matched,matched_rate,consultations_with_mapping}` / `trace{consultations_with_trace,generate_graded,generate_degraded,generate_degraded_rate,generate_ms_avg}` / `material{consultations_with_material,by_type}`。
- `cases[]`：每条含 `question` / `advice` / `evidence[]`（每个片段带 `kind`：`article` 法源条文 / `reference` 仅条号 / `material` 用户材料）/ `evidence_kinds` / `rule` / `faithfulness{glm,judge}` / `quality{glm,judge}`。
- 人工抽检表：每条咨询两个条目（`consult-<id>-faith` / `consult-<id>-quality`），meta 行写两侧裁判判罚（`glm=忠实|不忠实|不可用`、`qwen-plus=pass|review|fail`），正文给「问题 / 系统答复 / 本次证据（带片段类型）/ 人工结论 / 备注」，裁判理由折叠在 `<details>` 里（先判后看，避免锚定）。

## 3. 三层结果

### 3.1 规则层（全量 50 条真实咨询，零模型调用）

| 指标 | 数值 |
| --- | --- |
| 咨询总数 / 去重后 | 50 / 50（同问同答的重复为 0） |
| 引用条目总数 | 77 |
| 条级核验：通过 / 未通过 / **无核验字段** | 3 / 15 / **59**（通过率 3.9%） |
| 句级映射覆盖 | 32 条咨询有映射；句子命中 76 / 190（40.0%） |
| trace 覆盖 | 37 / 50 条（13 条早于 trace 落地） |
| 生成降级 | 5 / 37（13.5%）；生成平均耗时 13.96 s |
| 带用户材料 | 8 条（合同 7 / 文书 1） |

**读法**：规则层暴露的第一件事不是「答案好不好」，而是**核验能力本身不足**——77 条引用里 59 条连 `verification` 字段都没有（多来自没有条文级数据的演示法源），只有 3 条真正核到条文。

### 3.2 裁判层（抽样 8 条，每条 4 次裁判调用）

| 裁判 | 忠实度（忠实/可判） | 质量 pass | groundedness | relevance | completeness |
| --- | --- | --- | --- | --- | --- |
| glm-5.2（产品判官同 prompt） | **4 / 7 = 0.571** | 3 / 7 = 0.429 | 0.571 | 0.800 | 0.757 |
| qwen-plus（跨模型） | **6 / 8 = 0.750** | 5 / 8 = 0.625 | 0.813 | 1.000 | 0.700 |

glm 侧一次判官返回空内容（`empty_content`，记为不可用，不进分母）；glm 累计标出 9 条无依据断言，qwen-plus 标出 3 条。

**两模型分歧 5 条**（`divergence`）：consult-2（glm 不忠实 / qwen 忠实）、consult-42（glm 忠实 / qwen 不忠实，质量 pass vs review）、consult-5（质量 review vs pass）、consult-7（忠实度不一致）、consult-10（质量 fail vs pass）。分歧本身就是产出：说明「只给了条号、没有条文正文」时两位裁判会走向相反结论。

### 3.3 证据口径（解释上面所有数字的关键）

| 抽样条目 | 证据片段构成 |
| --- | --- |
| consult-42 | 1 条法源条文 + 1 条用户材料 |
| consult-39 / consult-40 | 1 条仅条号 + 1 条用户材料 |
| consult-1 / 7 / 10 | 仅条号（1–3 条） |
| consult-2 / 5 | **无任何证据片段** |

抽样 8 条里**只有 1 条**能看到法源条文正文，**2 条完全没有证据**。因此裁判在这些条目上的 `faithful=false` 含义是「**无法核验**」而不是「答错」——这也是为什么 qwen-plus 在 consult-2（无证据）判「忠实」、glm 判「不忠实」：前者把无证据视为不可判，后者把无法核验的断言直接算作缺依据。

## 4. 决策

1. **扩到主链路**：QA 数据集上指标已饱和（规则层全 1.0、裁判层分歧 0），而产品真实链路从没有同口径数字——评审的价值在后者。
2. **规则层跑全量、裁判层只跑抽样**：规则层零成本可全量；裁判层每条 4 次模型调用（两模型 × 忠实度/质量），抽样才跑得起。
3. **抽样按「是否带用户材料」分层**：材料上下文是新能力（8/50 条），等距抽样保证它不会被淹没，也不给它超额权重。
4. **证据片段带 `kind` 标签**：判官与人工都必须能区分「看到了条文正文」「只看到条号」「什么都没看到」，否则同一个 `false` 会被误读成同一种结论。
5. **忠实度用产品判官的 prompt 原文**（`agentic_judge.check_faithfulness` 口径），质量用咨询专用评分卡（无标准答案，只能依据「问题 + 本次证据」判断）。
6. **人工层复用 `scripts/score_human_review.py`**：同一张表格式、同一个评分器、同一套一致率与 Wilson 区间口径，两轮结果可比。

## 5. 验收证据

| 项目 | 结果 |
| --- | --- |
| 脚本运行 | `python -B scripts/run_consultation_review.py --sample 8 --min-interval 8` → exit 0，耗时 **188.5 s** |
| 裁判调用 | glm 16 次 HTTP 尝试 / 15 次成功、qwen-plus 16 / 16；无 429 重试 |
| 规则层 | 50 条咨询、77 条引用、76/190 句映射、37 条 trace、5/37 降级 |
| 裁判层 | 见 §3.2；分歧 5 条 |
| 产物 | `eval/outputs/consultation_review.json`（32006 B）、`consultation_spotcheck_sheet.md`（27666 B）、`.build.json`（408 B） |
| 评分器兼容性 | 用 2 条试填（正确/错误）跑 `scripts/score_human_review.py --threshold 0.8` → 正常解析 meta、给出两侧一致率与 95% CI |

## 6. 如实边界

1. **证据可得性是本结果的主变量**：8 条抽样里 1 条有条文正文、3 条有材料、2 条无任何证据；「忠实度 0.571/0.750」主要反映**可核验性**，不是答案正确率。
2. **语料缺口**：演示法源普遍没有条文级数据（规则层 59/77 条引用无核验字段），要提升任何一层数字，最有效的动作是**把法源条文补进库**，而不是调 prompt。
3. **抽样 8/50**：每条 4 次调用，样本小、CI 宽；结论只用于**定性**（「主链路明显低于 QA 数据集」），不用于精确比率。
4. **13/50 条早于 trace 落地**：这些记录没有 `trace`（也没有 `attribution`），规则层已按缺失统计，不参与裁判层抽样（抽样按 id 等距，实际选中 1/2/5/7/10/39/40/42）。
5. **判官与被测同源**：glm-5.2 既生成答案又判忠实度（跨模型侧用 qwen-plus 对冲）；两个裁判都不是法律专家，判的是「证据支持度」。
6. **人工层未做**：本轮没有人工判定（表已生成），因此**没有主链路的一致率**；QA 数据集上的一致率 0.88（22/25）不可直接外推到这里。

## 7. 下一步

1. ~~**补法源条文**（最高杠杆）：把常用法规条文导入 `legal_articles`，规则层核验率与裁判层可判率会同步上升。~~ **已于同日执行并复测，见 §8。**
2. **可选：人工抽检这 16 个条目**（`eval/outputs/consultation_spotcheck_sheet.md`），得到主链路的一致率，与 QA 数据集的 0.88 对比。
3. **把「证据可得性」做成在线指标**：目前只能离线算；若在响应里带一个「本次证据可核验性」摘要，前端就能提示用户「本答复的事实断言缺少可核验依据」。
4. **裁判口径收敛**：对「无证据」这一情形给两位裁判统一规则（判「不可判」而非「不忠实」），可显著减少假分歧。

## 8. 补语料与重跑（2026-10-19，第二轮）

§7.1 判定「补法源条文是最高杠杆」——本节就是执行它，并用同一套评审复测。

### 8.1 改了什么

| 改动 | 说明 |
| --- | --- |
| `scripts/legal_corpus/劳动争议调解仲裁法.json` | 新增 **54 条**（原 6 部语料缺这一部，而真实咨询里高频引用「第 27 条 仲裁时效」） |
| `app/services/legal/legal_service.py` | 演示法源条目加 `corpus` 字段 + `_load_demo_articles()`；`ensure_demo_sources()` 现在**同时写条文**——新环境建库即带条级数据 |
| `scripts/backfill_demo_articles.py` | 新增维护脚本：把语料回填到**已存在**的演示法源，幂等、支持 `--dry-run`。实测回填 **20 个法源 / 416 条条文**（user 3 / 10 / 15 / 37 各 5 部） |

### 8.2 引用→条文覆盖率（全库 77 条引用）

| 用户 | 引用条数 | 补语料前能查到条文正文 | 补语料后 | 其中无 `source_id` |
| --- | --- | --- | --- | --- |
| 3（admin） | 10 | 0 | **8** | 2 |
| 10（demo_lawyer） | 18 | 1 | **9** | 8 |
| 15（demo_lawyer 组织内） | 3 | 0 | 0 | 3 |
| 37（demo_reviewer） | 46 | 20 | **41** | 3 |

### 8.3 重跑同一抽样（8 条，确定性 seed 不变，可直接对照）

| 指标 | 第一轮 | 第二轮 |
| --- | --- | --- |
| 抽样里能看到法源条文正文 | 1 / 8 | **3 / 8** |
| glm-5.2 忠实度（忠实/可判） | 4 / 7 = 0.571 | **4 / 4 = 1.000**（可判率 7/8 → 8/8） |
| glm-5.2 质量 pass | 3 / 7 = 0.429 | 3 / 8 = 0.375 |
| qwen-plus 忠实度 | 6 / 8 = 0.750 | 6 / 8 = 0.750 |
| qwen-plus 质量 pass | 5 / 8 = 0.625 | 5 / 8 = 0.625 |
| 两模型分歧 | 5 | 4 |
| 规则层（历史记录，见边界 1） | 3 通过 / 15 未通过 / 59 无字段 | 不变 |

**读法**：glm 侧 4/7 → 4/4 **不是「变好了」，而是「原来判不了的现在能判了」**（分母补齐）；质量 pass 3/7 → 3/8 是分母扩大、分子不变。qwen-plus 侧两轮完全一致，说明它的判罚对证据可得性不敏感（它本来就倾向把无证据当不可判）。

### 8.4 新咨询实测：条级核验真的会变

补语料后新建一条真实咨询（`id 56`，`demo_reviewer`，案件 #4，真实 LLM，生成 24.2 s）：

- `trace.metrics = {rounds: 1, candidate_sources: 4, references: 2, verified: 1, unverified: 0, unmarked: 1}`
- 引用 1《劳动合同法》→ `verified=null`，依据「引用未标注条号，未做逐条核对」（**诚实降级**：只给了法条名，无法逐条核）
- 引用 2《劳动争议调解仲裁法》第 27 条 → **`verified=true`**，依据「引用的第 27 条已与法源条文核对一致」——补语料前这类引用只会得到「该法源未收录条文级数据」
- 句级映射 5 / 6 句命中；`generate` 步 24.2 s、`retrieve` 9 ms、`verify` 3 ms

### 8.5 如实边界

1. **历史记录不回溯**：`references_json.verification` 是生成时的快照，本轮**没有**回填老记录，所以规则层 3/15/59 不变。补语料的效果体现在**新咨询**（§8.4）与**评审侧的证据重建**（§8.3）。要让老记录也显示条级核验，需要一个显式的「重算核验」维护任务（未做）。
2. **仍有 16/77 条引用没有 `source_id`**（LLM 直接给法条名、没命中法源库；user 15 的 3 条全是这种）。回填语料帮不到这类引用，只能靠条号文本核验——正是 §7.4 要处理的情形。
3. 裁判层仍是 8 条抽样、CI 宽；本轮只证明**方向**（证据可得性 ↑ → 可判率 ↑），不给精确比率。
4. 演示库里同一部法现在有两份（老的「演示依据」摘要法源 + 早期手工导入的《劳动合同法》源 71/72），标题不同、条文重复：不影响检索正确性，但列表里看着重复。**未清理**，因为已有引用指向它们，删掉会让那些引用失去 `source_id`。

### 8.6 复现

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -B scripts\backfill_demo_articles.py --dry-run   # 先看会写什么
.\.venv\Scripts\python.exe -B scripts\backfill_demo_articles.py             # 幂等回填
.\.venv\Scripts\python.exe -B scripts\run_consultation_review.py --sample 8 --min-interval 8 `
    --output .tmp_consultation_review2.json --sheet-output .tmp_consultation_spotcheck_sheet2.md
# 本机 python 子进程写不了 eval/outputs：产物先落仓库根，再 Move-Item
```
