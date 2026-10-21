# 第二十一步交付说明：合同审查链路的三层评审

> 一句话：把 step13 / step16 的三层评审口径第一次搬到合同审查，量出的不是「答得对不对」，而是「证据颗粒度够不够」——53 条风险里只有 25 条带条款定位；同族判官认为 7/7 有据，跨模型判官只认 4/7，质量 pass 5/7 vs 0/7。

- 日期：2026-10-20
- 对应：诊断文档 90 天清单之后的链路延伸；step16「法律咨询三层评审」的合同审查版
- 新增：`scripts/run_contract_review_review.py`（规则层 + 跨模型裁判 + 人工抽检表）
- 改动：`scripts/run_cross_judge.py` 的 `JudgeClient` 增加 `max_tokens` 参数（默认 1024 不变）
- 产物：`eval/outputs/contract_review_review.json`、`eval/outputs/contract_review_spotcheck_sheet.md`（均在 `.gitignore` 内，可按第 6 节重建）
- 后续：第三层（人工/独立裁决）由 [step24](step24-contract-review-adjudication.md) 以 **AI 独立复核**完成（非人工），一致率 glm 13/14、qwen 7/14

---

## 1. 为什么是合同审查

法律咨询主链路已经有四层可信体验与三层评审（step13 → step16）；合同审查是第二条真实链路，输出结构是 `summary + risks_json`，每条风险带 `clause_type / risk_level / source_location / suggestion`——这正好能用规则层量，也正好能问裁判「这条风险在合同原文里有没有依据」。

在此之前，这条链路没有任何同口径的质量读数：条款定位率、snippet 是否真的来自原文、建议是否可执行、引用是否有效，都没量过。合同审查与法律咨询的证据口径不同：

| | 法律咨询（step16） | 合同审查（本文） |
| --- | --- | --- |
| 证据 | 法源条文 + 用户材料 | 合同原文 + 条款定位 |
| 规则层 | 条级核验 / 句级映射 / trace | 条款定位 / snippet 可核验 / 建议 / 引用状态 |
| 裁判层 | 忠实度（同 prompt）+ 质量评分卡 | **重写**有据性 prompt + 合同质量评分卡 |
| 结论指向 | 证据可得性（语料缺条文） | 证据颗粒度（风险缺定位） |

因此裁判 prompt 不能复用 `check_faithfulness` 的问答口径，只能复用「只依据给定证据、不补外部知识」的原则。

---

## 2. 方法

### 2.1 规则层（零模型，7 条全量）

- 风险条数与等级分布；条款类型覆盖（对照 `CLAUSE_RULES` 的 8 类标准条款）。
- **条款定位率**：`source_location.paragraph` 非空的风险占比。
- **snippet 可核验率**：`snippet` 去掉空白后是否为合同原文的子串（防编造定位）。
- 建议完整度：`suggestion` 非空占比。
- 引用与法源状态：条数、带 `source_id`、`verification.current_effective`、`superseded`、无核验字段。
- 审查策略快照与模型快照是否存在。

### 2.2 裁判层（跨模型，7 条全量）

两个模型对 7 条记录各判两次（每个模型 14 次调用、0 失败、8s 节流）：

1. **有据性**：只给合同原文 + 审查摘要 + 风险清单，判断事实性主张能否在原文找到依据。规则写死：①把合同没有的事实说成有/把有的说成没有 → 无依据；②「合同未约定 X」若原文确实没有 X，算有依据；③修改建议本身不判有据/无据。
2. **质量**：三维评分卡 `coverage / grounding / actionability` + `pass|review|fail`。actionability 明确要求「具体、可落地」，泛泛的「建议完善」不算。

裁判模型：`glm-5.2`（与产品生成同族）与 `qwen-plus`（跨模型，沿用 dashscope 凭据）。实现注记：glm-5.2 是推理模型，`max_tokens` 为默认 1024 时首轮 14 次里有 5 次不可用（4 次只返回 `reasoning_content`、`content` 为空，1 次 JSON 被截断）；调到 4096 后 14/14 成功。该参数已作为 `JudgeClient` 的可选项，默认值不变。

### 2.3 人工层

生成 14 条目抽检表（每条审查记录 2 条：有据性 / 质量），格式与 `scripts/score_human_review.py` 兼容；本轮**未判定**，所以没有「AI vs 人工」一致率。

---

## 3. 结果

### 3.1 规则层：定位只有一半，但有定位的都真

| 指标 | 读数 |
| --- | --- |
| 审查记录 | **7 条**（2 个用户；2026-07-24 → 2026-08-08；全部 `needs_lawyer_review`） |
| 风险条目 | **53 条**（high 24 / medium 22 / low 2 / needs_facts 5），均值 7.57 条/次 |
| 条款定位率 | **25/53 = 47.2%**；3 条记录（id 4/5/6）**0 定位** |
| snippet 可核验率 | **25/25 = 100%**（有 snippet 的都能在原文找到） |
| 建议完整度 | **53/53 = 100%** |
| 条款类型覆盖 | 8 类全覆盖，均值 94.6% |
| 引用 | 19 条；带 `source_id` 18、当前有效 12、已废止 0、**无核验字段 7** |
| 策略 / 模型快照 | **0/7、0/7** |
| 正文 / 摘要长度 | 均值 53.7 / 154.3 字 |

读法：**有定位的都真，但近一半风险没有定位**。这说明问题不在「定位造假」，而在「定位缺失」——合同审查目前只到「条款类型 + 文字描述」，没有像咨询引用那样可点开、可核对的证据锚点。建议 100% 有，但裁判的 actionability 只有 0.76 / 0.44（见下），说明「有建议」不等于「建议能落地」。

### 3.2 裁判层：同族偏宽，跨模型更严

| 裁判 | 有据性 | 质量 pass | coverage | grounding | actionability |
| --- | --- | --- | --- | --- | --- |
| glm-5.2（同族） | **7/7（1.0）** | **5/7（0.7143）** | 0.9714 | 0.9857 | 0.7571 |
| qwen-plus（跨模型） | **4/7（0.5714）** | **0/7（0.0）** | 0.9143 | 0.8000 | 0.4429 |

- 两模型分歧 **6/7**：有据性分歧 3 条（id 1/2/3），质量分歧 5 条（id 2/3/5/6/7），其中 id 2/3 两类都分歧。
- qwen-plus 给出的 4 条不支持断言，正好覆盖三类「不是合同原文事实」的表述：
  1. **推断**：id 1「违约金上限较低可能不足以覆盖实际损失」——合同只写了上限 10%，没写实际损失；
  2. **评价**：id 2「所有 needs_facts 项表明合同不具备可执行基础」——这是结论，不是原文事实；
  3. **外部知识**：id 3「远超司法实践通常支持的 LPR 四倍（当前约 14.8%）」——法条判断本身可能对，但合同原文没有这个数。
- 质量分歧集中在 **actionability**：glm 认为「补充具体要素」就算可落地；qwen 要求给出条款草案/措辞，故 0/7 pass。id 6 最极端：glm 判 pass（0.9），qwen 判 fail（grounding 0 / actionability 0），理由是 18 字合同配 8 条「无条款定位」的缺失判断，无法支撑。

**关键提醒**：qwen 的 `false` 不等于「审查结论错」，而是「合同原文没有直接支持」；glm 与生成同族，倾向接受法律推理。两者都不是 ground truth，只能当两盏方向不同的灯。

### 3.3 人工层：表已生成，结论待填

- `eval/outputs/contract_review_spotcheck_sheet.md`：14 条目（7 条有据性 + 7 条质量），每条含合同原文节选、摘要、风险清单与两侧裁判意见（折叠，填完再看）。
- `score_human_review.py` 已 dry-run 验证可解析：显示 14 条目、已表态 0、一致率 n/a，不会把「没填」算成 0。

---

## 4. 与咨询链路相比，合同审查缺什么

1. **缺条级证据**：咨询引用能核到「第 X 条」，合同审查的 `references_json` 只有法源级 `verification`，没有「这条风险对应哪一条法条」。
2. **缺句级溯源**：风险描述与合同条款之间没有稳定的 `risk → clause` 映射（只有自然语言 `snippet`，且 47% 缺失）。
3. **缺生成快照**：7 条记录的 `model_snapshot_json` 全为空，无法回答「这条结论是哪个模型/哪版 prompt 生成的」。
4. **缺在线指标**：没有像咨询 `evidence_json` 那样的四档徽标；本次评审是离线脚本，不是产品内可见。

这四条是下一步比「再加一个评分维度」更高杠杆的改动。

---

## 5. 诚实边界

1. **样本 7 条 / 2 个用户**，其中 3 条（id 4/5/6）是同一份 18 字合同的重复输出，只能作方向性证据，不能当趋势。
2. **历史记录**（2026-07-24 → 08-08）不是当前 prompt 的复跑；与 step16 同口径但链路不同，数字不可直接相加或对比。
3. **同族判官偏宽**：glm 7/7 vs qwen 4/7；同一模型在两个 prompt 下也会自相矛盾（qwen 对 id 6 的有据性判 true，质量却判 fail / grounding 0），说明裁判结论依赖问题框架。
4. **人工层未判**：本文只完成规则层 + 裁判层，不能写成「合同审查达到 0.88」；0.88 是 step13 的 QA 数据集口径，与本文无关。
5. **有据性 prompt 只认合同原文**：qwen 把 LPR 这类外部法律知识判为无依据是 prompt 使然，不代表该判断在法律上错误；若要让裁判接受法条，需要把法源条文一并作为证据传入（当前未做）。
6. **未审「条款 → 法条」映射**：合同审查的引用只有 source 级，本文没有能力评价「引用的法条对不对」。

---

## 6. 复现

```powershell
# 1) 只跑规则层（零模型，秒级）
.\.venv\Scripts\python.exe -B scripts\run_contract_review_review.py --dry-run --output .tmp_cr.json --no-sheet

# 2) 全量三层（规则 + 两模型裁判 + 抽检表；约 3–4 分钟，14 次/模型）
.\.venv\Scripts\python.exe -B scripts\run_contract_review_review.py `
    --output .tmp_contract_review_review.json `
    --sheet-output .tmp_contract_review_spotcheck_sheet.md
Move-Item .tmp_contract_review_review.json eval\outputs\contract_review_review.json
Move-Item .tmp_contract_review_spotcheck_sheet.md eval\outputs\contract_review_spotcheck_sheet.md

# 3) 人工填完后评分（未填时显示 n/a，不会虚高）
.\.venv\Scripts\python.exe -B scripts\score_human_review.py `
    --sheet eval\outputs\contract_review_spotcheck_sheet.md --threshold 0.8
```

裁判凭据沿用 `.env` 的 `LLM_*`（glm-5.2）与 `EMBEDDING_*`（qwen-plus）；`--judge-model` 可换模型，`--min-interval` 默认 8s。

---

## 7. 下一步（按杠杆排序）

1. 人工判 14 条，算一次「有据性 / 质量」与人工的一致率；样本小，按方向性证据标注。
2. 给合同审查补「条款 → 法条」条级映射与核验，让 `references_json` 从 source 级走到 article 级（复用咨询的 `_article_verification`）。
3. 用当前 prompt 重新生成 5–8 条合同审查，与这 7 条历史记录**分开报**，避免把旧 prompt 的结论当现状。
4. 把 actionability 做成规则检查：建议里是否包含数字 / 期限 / 条款草案，而不是只靠裁判打分。
