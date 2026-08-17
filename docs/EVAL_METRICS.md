# 评估指标定义与口径（阶段 4）

> 本文档是评测指标的**单一事实来源**：指标定义、计算口径、数据来源与验收阈值。
> 原则：确定性优先（temperature=0 + dataset fingerprint + `--seed`），
> 同版本同种子重跑结果一致（DoD #4）。

## 1. 指标清单

| 指标 | 定义/口径 | 数据来源 | 产出位置 |
|---|---|---|---|
| `hit_at_k` | 检索命中：期望 chunk 关键词命中 top-k 候选 | run_eval keyword_hit | summary |
| `citation_accuracy` | 引用正确率：含引用的答案中引用命中期望法源/条款 | citation_hit | summary |
| `refusal_accuracy` | 拒答准确率：应拒答样本中正确拒答（can_answer=False）比例 | run_eval | summary |
| `answer_accuracy` | 仅配 expected_answer_keywords 样本的答案关键词命中率 | answer_hit | summary |
| `average_latency_ms` / `latency_p50_ms` / `latency_p95_ms` | 端到端单例延迟均值 / P50 / P95（线性插值分位） | 每例 `time.perf_counter` | summary |
| `average_retrieval_rounds` | Agentic RAG 平均检索轮次 | agentic_trace | summary |
| `badcase_count` | 非 pass/correct_refusal 用例数（回归门禁阈值 0.95 相关） | collect_badcases | summary/badcase 文件 |
| LREC 检索侧 `hit_at_5` / `mrr` | 独立检索评测（legal_retrieval_report.json） | run_legal_retrieval_eval | outputs/ |
| 生成评测 `overall_pass_rate` | 咨询/拒答/失效法源分层通过率（CI 阈值 ≥0.95） | run_generation_eval | outputs/ |

## 2. 阶段 4 新增指标口径

### 成本（token / 单价）
- 定义：单次评测的 LLM 成本 = Σ(模型单价 × token 数)。单价来自
  `settings.LLM_MODEL_PRICING`（`{model: {input_per_1k, output_per_1k}}`），
  Decimal 六位精度（与 `token_service.compute_cost` 同一实现）。
- 数据来源：`llm_call_log` 持久化记录（`token_service.record` 已落库）；
  评测输出侧的**成本口径见 `token_service` 契约**（docs/CONTRACT.md §6）。
- 校验：单位是「元 / 千 token；总额以分入账」；成本仅来自具名模型调用，
  不含 embedding/rerank 之外的推理。
- 请注意：run_eval 不内嵌 token 计数（agentic 链路封装），成本聚合由
  `llm_call_log` + 台账负责；本指标在评测报告里以「按评测 user_id 的
  `cost_ledger` 聚合」提供，不参与 pass/fail 判定。

### 延迟分位（新增落地于 run_eval summary）
- `latency_p50_ms` / `latency_p95_ms`：对全部用例 latency 排序后线性插值分位
  （`_percentile`）。口径与 average 一致（端到端含检索+生成）。
- 验收：不被用于 CI 硬门禁（延迟受本机/网络影响大），仅做报告与环比参考。

### 审核通过率
- 定义：`lawyer_approved / total_reviewed`（人工审核通过率），仅统计
  `status=lawyer_approved` 或等价终态（见 `legal_workspace` 状态机）。
- 数据来源：`legal_contract_reviews` / `legal_drafts` 的 status 字段 +
  AI-2 回流（`eval/review_feedback_eval.jsonl`）。
- 校验：分母 = 进入审核队列且被律师处置的记录数；未处置的 `pending_review`
  不计入分母（避免分母膨胀）。

## 3. 分层采样（eval/stratified_sampler.py）

- 分层键：`(category, refusal, difficulty)` —— 案件/文书类型、是否拒答、
  难度代理（expected_answer_keywords 数量 + 拒答=high）。
- 方法：按层**成比例配额**（无放回抽样），配额取整后从剩余池补齐；
  `seed` 固定（`random.Random(seed)`），同 seed + 同输入 → 同输出。
- 验收：采样前后各层占比偏差 ≤ 每组 1 例级别；`--seed` 保持一致可复现。
- 用法：
  ```python
  from eval.stratified_sampler import stratified_sample, strata_counts
  sampled, stats = stratified_sample(cases, n=100, seed=42)
  ```

## 4. PII 脱敏（eval/redact.py + eval/redact_check.py）

- 规则与占位符见 eval/redact.py docstring（姓名/手机/证件/卡号/金额/邮箱/案号/律所名）。
- 校验：`eval/redact_check.py` 作 **fail-closed** 门禁——对导出的真实语料
  （`scripts/export_real_corpus_eval.py`）跑 `detect_pii`，残留即失败。
- 验收（DoD #3）：脱敏后 `redact_check.py --dataset <导出集>` 退出码 0。

## 5. 可复现性（DoD #4）

- 种子：`run_experiments.py --seed <n>`（默认 42）固定采样/随机化；
- 指纹：`dataset_fingerprint`（sha256）记录在结果 config，漂移即换 fingerprint；
- 输出：`outputs/summary.json` + `baseline_snapshot.json` 存档，随仓库提交可对比。
