# Step 13：三层评审（A 项）——规则层 / 跨模型裁判层 / 人工抽检层

- 日期：2026-10-18
- 对应：诊断文档 A 项「三层评审（规则 → LLM-judge → 人工抽检，验收 AI 与人工一致率 ≥0.8）」
- 状态：**前两层已完成并有报告；第三层的表与评分脚本已就绪并验证跑通，等人工填表**（见 §3）

## 1. 第一层：规则层（确定性指标，无模型参与）

产物 `eval/outputs/qa_eval_repaired.json`（`evaluation_id = rag_eval_20261017T055653Z_53e1b280`），35 题（31 可答 / 4 拒答）：

| 指标 | 值 |
| --- | --- |
| hit@k / mrr / ndcg@5 | 1.0 / 1.0 / 1.0 |
| retrieval_evidence_coverage | 1.0 |
| citation_evidence_coverage | 1.0 |
| citation_accuracy | 1.0 |
| refusal_accuracy | 1.0 |
| badcase_count | 0 |
| average_latency_ms（p50 / p95） | 26238.86（16978.22 / 52348.98） |
| average_retrieval_rounds | 1.29 |

这一层不依赖任何模型裁判，是"能不能检索到、引用对不对、该拒答有没有拒答"的硬事实。

## 2. 第二层：跨模型裁判层

产物 `eval/outputs/cross_judge_report.json`（脚本 `scripts/run_cross_judge.py`，只读第一层的答案，**零生成调用**）：

| 指标 | glm-5.2（原口径） | qwen-plus（交叉口径） |
| --- | --- | --- |
| 忠实度率 | 1.0（31/31） | 1.0（31/31） |
| groundedness 均分 | 1.0 | 1.0 |
| answer_relevance 均分 | 1.0 | 1.0 |
| completeness 均分 | 0.9714 | 0.98 |
| pass_rate | 0.9429 | 0.9429 |
| 分歧题数 | — | 忠实度 0；三维差 ≥0.1 共 1 题（`plan_team_composition`，completeness 0.5 → 0.8），verdict 变化 0 |

**口径偏差（如实记录）**：诊断文档写的是 `deepseek-v4.1-flash`，实际用的是 **qwen-plus**（dashscope 兼容模式，走 `.env` 的 `EMBEDDING_API_BASE_URL/API_KEY`）——本机 `.env` 里**没有任何 deepseek 凭据**。用户已裁决沿用 qwen-plus 并在文档写明偏差（本表即该记录）。裁判脚本的忠实度 prompt 从 `app/services/rag/agentic_judge.check_faithfulness` **逐字复用**，三维 prompt 直接 import `eval/llm_judge._build_prompt`，因此换模型不改口径。

## 3. 第三层：人工抽检（已就绪，等填表）

**抽检表**：`eval/outputs/human_spotcheck_sheet.md`（644 行 / 25 条 = 1 条分歧项 + 12 条忠实度一致项 + 12 条质量一致项），生成命令（`--sheet-mode stratified` 是**零模型调用的确定性抽样**：分歧项全列 + 一致项分层等距取，同一命令两次生成同一张表）：

```
python -B scripts/run_cross_judge.py --sheet-mode stratified --sheet-sample 24 \
  --from-report eval/outputs/cross_judge_report.json --sheet-output <输出路径>
```

**填表口径**：每条只填 `- 人工结论：` 三选一（`正确` / `错误` / `无法判断`），`备注` 自由；`无法判断` 与留空都不进一致率分母；先自己判，再展开折叠的裁判判定。

**评分脚本**：`scripts/score_human_review.py`，用法

```
python -B scripts/score_human_review.py --sheet eval/outputs/human_spotcheck_sheet.md [--json out.json]
```

**流水线已验证跑通**（2026-10-18 用一份填了 4 条的临时副本试跑，试跑件已删）：脚本正确解析 `<!-- meta：id=… kind=… -->`、区分已表态/无法判断/留空，并输出两个基准的分子/分母与按类型拆分，例如：

```
条目总数 25｜已表态 3（正确 2 / 错误 1）｜无法判断 1｜留空 21
基准 glm：人工同意率 = 0.3333（1/3）
  按类型 faithfulness：glm 0.5000（1/2）｜按类型 quality：glm 0.0000（0/1）
```

即：**表一填完就能出验收数**，不存在"填完才发现脚本跑不通"的风险。

## 4. 决策记录

1. **忠实度与三维质量分开判**：忠实度是布尔（断言能否在引用片段里找到依据），质量是三维打分 + verdict；两者分母、口径都不同，不混算。
2. **裁判与被判答案同源要标注**：glm-5.2 既是这批答案的生成模型又是裁判 A，属"同源裁判"，因此必须有跨模型裁判 B（qwen-plus）与人工层兜底——这也是三层评审存在的理由。
3. **人工层非盲测**：两位裁判看同一批答案与同一批引用片段，只换模型；表里把裁判判定折叠在 `<details>` 里，鼓励先判后看。
4. **抽样确定而非随机**：`--sheet-mode stratified` 用 `eval/stratified_sampler.py`（seed=42），保证可复现、可复核。
5. **分母只算"双方都表了态"的条目**：人工写"无法判断"或裁判没给明确判罚的，都不进分母——宁可分母小，也不把模糊条目算成一致。
6. **验收线 ≥0.8**（诊断文档原口径）：以 qwen-plus（跨模型）为主基准记录，glm 基准同时列出以便对照同源偏差。

## 5. 开口项

1. **A-5（阻塞在人工）**：等 25 条人工结论填完 → 跑 `score_human_review.py` → 把一致率写进诊断文档。**当前未填，验收尚未达成**。
2. **deepseek-v4.1-flash 口径未落地**：本机无该凭据；若要严格按诊断文档口径，需要提供 deepseek 的 base_url / api_key 后重跑 `run_cross_judge.py --judge-model deepseek-v4.1-flash`。
3. **抽样规模 25/66**：忠实度 31 题里抽 12、质量 35 题里抽 12；样本小，一致率只能作为方向性证据，不能当精确估计。
4. **未覆盖产品主链路**：这套三层评审跑在 RAG QA 数据集上；法律咨询（copilot 主链路）的答案质量还没有同口径的三层评审（trace 已能提供过程证据，但缺答案级裁判）。
