# RAG Eval Results

将 `python eval/run_eval.py --pretty` 的输出结果整理到这里。

如果仓库里还没有真实上传文档，可先用内置样例跑通：

```bash
python eval/index_eval_corpus.py --pretty
python eval/run_eval.py --user-id 9000 --pretty
python eval/run_experiments.py --user-id 9000 --pretty
python eval/run_experiments.py --user-id 9000 --write-artifacts --pretty
```

运行前确认 `.env` 里的 `LLM_API_KEY` 不是示例占位值。当前仓库默认 `.env` 仍是 `your-dashscope-api-key` 这类占位内容时，索引和实验脚本会直接终止并提示修正配置。

内置样例语料位于：

- `eval/fixtures/contract_service_agreement.md`
- `eval/fixtures/project_delivery_plan.md`
- `eval/fixtures/vendor_due_diligence_report.md`

如果要切到真实业务评测集，建议不要直接覆盖根目录样例文件，而是创建独立 bundle：

```bash
python eval/create_eval_bundle.py --bundle-name real_contracts_q3 --pretty
python eval/index_eval_corpus.py --bundle-dir eval/bundles/real_contracts_q3 --pretty
python eval/run_eval.py --bundle-dir eval/bundles/real_contracts_q3 --user-id 9000 --pretty
python eval/run_experiments.py --bundle-dir eval/bundles/real_contracts_q3 --user-id 9000 --write-artifacts --pretty
python eval/run_experiments.py --bundle-dir eval/bundles/real_contracts_q3 --user-id 9000 --check-regression --baseline-path eval/bundles/real_contracts_q3/outputs/baseline_snapshot.json --pretty
```

bundle 目录建议包含：

- `bundle_meta.json`：数据集说明
- `corpus_manifest.json`：参与索引的真实业务文档
- `qa_dataset.json`：评测题集
- `experiment_matrix.json`：实验矩阵
- `outputs/baseline_snapshot.json`：baseline 快照，沉淀 Prompt 版本和 RAG 参数
- `docs/`：脱敏后的真实文档

建议实验前先把 `eval/qa_dataset.json` 补到 25-30 题，至少覆盖：

- 合同金额、日期、付款条款、违约责任
- 方案/报告里的范围、里程碑、风险、待办
- 3-5 个文档中没有答案的问题，用于拒答验证

建议每条样本包含：

- `question`
- `reference_answer`
- `expected_chunk_keywords`
- `should_refuse`
- `expected_answer_keywords`：人工标注的答案关键字；仅包含该字段的可回答题会进入 Answer Accuracy，避免用模型自评替代人工评估

建议 baseline 至少固定：

- `top_k`
- `confidence_threshold`
- `min_recall_candidates`
- `recall_multiplier`
- `query_variant_limit`
- `context_neighbor_window`
- `context_max_chunks`
- `prompt_template` / `prompt_version`

建议至少记录以下实验：

| Experiment | top_k | confidence_threshold | Hit@5 | Citation Accuracy | Refusal Accuracy | Notes |
|------------|-------|----------------------|-------|-------------------|------------------|-------|
| baseline | 5 | 0.35 | 1.0000 | 0.9310 | 1.0000 | 32 题样例集，29 题可回答、3 题拒答 |
| topk_3 | 3 | 0.35 | 1.0000 | 0.9310 | 1.0000 | 与 baseline 持平，当前样例集对 top_k 不敏感 |
| topk_8 | 8 | 0.35 | 1.0000 | 0.9310 | 1.0000 | 与 baseline 持平，噪声尚未明显影响结果 |
| threshold_050 | 5 | 0.50 | 1.0000 | 0.4828 | 1.0000 | 阈值过高导致大量本可回答问题被拒答 |
| chunk_500 | 5 | 0.35 | 1.0000 | 0.9310 | 1.0000 | 与 800/100 持平，当前样例集未体现切分差异 |

结果记录建议补三项说明：

1. 测试文档范围：用了哪几份文档，文档类型和页数
2. 数据集规模：总题数、可回答题数、拒答题数
3. 结论：为什么最后选当前 `top_k` 和 `confidence_threshold`

建议最终面试口径只保留真实生产或真实个人评测文档的实验结果。当前内置样例更适合演练流程、验证脚本和展示方法。

运行结果还会输出 `average_latency_ms`，以及仅针对人工标注答案关键词样本的 `answer_accuracy`。两项均应在替换为脱敏业务语料后记录到实验结果中。

本轮样例实验记录：

1. 测试文档范围：3 份 Markdown 样例文档，分别是合同、实施方案、供应商尽调报告
2. 数据集规模：32 题，其中 29 题可回答，3 题应拒答
3. 当前结论：
   - 在这套样例数据上，`top_k=3/5/8` 差异不明显
   - `confidence_threshold=0.5` 明显过严，会把可回答问题压成拒答
   - 当前默认配置 `top_k=5`、`confidence_threshold=0.35` 可以保留

## Graph RAG 回归夹具

运行命令：

```powershell
python eval/run_graph_rag_eval.py --pretty --output eval/outputs/graph_rag_ablation_report.json
```

`graph_rag_eval_cases.json` 包含法规修订关系和同领域关系两组近似候选夹具。最近一次报告显示：

- 候选集保持率：`100%`；
- 预期条文排序提升准确率：`100%`；
- 图谱增益：每条支持关系 `0.001`，最多累计 3 条。

该评测仅验证“图谱不扩展召回集且能有界调整近似候选排序”的算法约束，不等同于真实法律语料效果。真实 Neo4j 数据同步后，应补充修订版本、失效法规、领域歧义和无关问题，并记录图谱开关前后的检索指标。

## Agentic RAG 深度开关消融（真实法规语料）

运行命令：

```powershell
python -B eval/run_agentic_rag_eval.py --corpus-kind statutes --top-k 3 5 8 --output eval/outputs/agentic_rag_legal_ablation.json
```

数据集 `eval/agentic_rag_legal_cases.json`：27 题，每题的答案必须同时命中两处条文（跨法规或同一法规内的远距离条文）。语料是 `scripts/legal_corpus/*.json` 6 部法规切成 84 个片段——默认的 4 篇短文档语料在 `top_k>=2` 时单跳基线就已经全覆盖，消融在那里没有区分度。每条 `must_include` 在开跑前校验必须落在某个切片内，否则直接终止：匹配不到时覆盖率恒为 0，评测会自欺。

`multi_hop` 与 `multi_hop_forced` 的开关完全相同，区别只是后者绕开 `_looks_multi_hop` 前置门槛。门槛和分解是两件事，合成一条臂会把“门槛没放行”误读成“分解没用”。

| top_k | 臂 | 证据覆盖率 | 全覆盖率 | 相对基线 | 检索次数 | 额外 LLM 往返 | 送进生成的片段 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | single_hop | 0.8704 | 0.7407 | — | 1.26 | 0 | 3.15 |
| 3 | multi_hop | 0.8704 | 0.7407 | +0.0000 | 1.33 | 0.04 | 3.19 |
| 3 | multi_hop_forced | 0.9815 | 0.9630 | **+0.1111** | 2.37 | 1.00 | 5.30 |
| 3 | single_hop_judge_refine | 0.9259 | 0.8519 | +0.0555 | 2.00 | 2.00 | 3.30 |
| 5 | single_hop | 0.9630 | 0.9259 | — | 1.26 | 0 | 5.22 |
| 5 | multi_hop | 0.9630 | 0.9259 | +0.0000 | 1.33 | 0.04 | 5.33 |
| 5 | multi_hop_forced | 1.0000 | 1.0000 | **+0.0370** | 2.33 | 1.00 | 7.74 |
| 5 | single_hop_judge_refine | 0.9630 | 0.9259 | +0.0000 | 2.00 | 2.00 | 5.81 |
| 8 | single_hop | 0.9815 | 0.9630 | — | 1.30 | 0 | 8.00 |
| 8 | multi_hop | 0.9815 | 0.9630 | +0.0000 | 1.37 | 0.04 | 8.00 |
| 8 | multi_hop_forced | 0.8148 | 0.6296 | **−0.1667** | 2.33 | 1.00 | 8.00 |
| 8 | single_hop_judge_refine | 0.9815 | 0.9630 | +0.0000 | 2.00 | 2.00 | 8.00 |

`_looks_multi_hop` 门槛放行率：**3.7%（27 题里 1 题）**。门槛要求出现“以及/并且/同时/分别/还有/另外/；”这类连接词或两个问号，而自然提问多数是用逗号把两问连起来（“试用期最长多久，经济补偿怎么算”），因此不放行。

对照：默认那份 4 题语料（`--corpus-kind documents`）的放行率是 **100%**，4 道题全部写成“……，以及……”。所以原来那份消融看到的“多跳有效”（`top_k=1` 时 +50pt）测的是**门槛必然放行时**的分解收益；换成自然提问后，门槛才是真正的约束。这也是把 `multi_hop` 和 `multi_hop_forced` 分成两条臂的原因。

结论（这三个开关的默认值据此保持关闭）：

1. `AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED` **保持关闭**。在生产默认 `top_k=5` 和更宽的 8 上零收益，只在 `top_k=3` 提 5.6pt，而代价是每次问答固定多 2 次 LLM 往返（判分每轮一次）。想省钱就调宽 `top_k`，比多两次判分便宜。
2. `AGENTIC_RAG_MULTI_HOP_ENABLED` **已翻为开启（2026-10-17，见下节）**。上表是修复前口径：门槛只放行 3.7% 的自然提问（`multi_hop` 臂与基线完全相同），`top_k=8` 时子问题结果被挤出 `RAG_CONTEXT_MAX_CHUNKS=8` 预算、覆盖率反降 16.7pt。两者均已修复——门槛改为统计「疑问子句」个数（逗号串起的两问也放行），合并改为按子问题轮转取片（每个子问题预留 1/N 预算）。
3. `AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED` **保持关闭（无数据）**。它校验的是生成结果与证据是否一致，离线桩生成测不出来，需要真实 LLM 往返才能评。这是“没测”，不是“测过没用”。

限制：

- 只衡量送进生成节点的证据覆盖率，不衡量答案质量或忠实度。
- 嵌入是字符双字袋（确定性、离线可跑），不是真实嵌入模型。结论的方向（分解提覆盖、宽 `top_k` 下 fan-out 挤预算）可复现，具体交叉点会随嵌入变化。
- 分解与判分是脚本桩，总能给出正确的子问题与 missing，因此表里的收益是**上界**；真实 LLM 拆错或判错只会更低。
- 27 题全部是“两处条文”结构的多跳题，对多跳有利；单跳问题占比高的真实流量里，`multi_hop_forced` 那 1 次额外往返的性价比会更差。

## 真实嵌入复测：门槛 / 预算修复验证（2026-10-17）

```powershell
python -B eval/run_agentic_rag_eval.py --corpus-kind statutes --top-k 5 8 --real-embedding --output eval/outputs/agentic_rag_statutes_real_k5_k8.json
```

`--real-embedding` 让脚本改用应用的嵌入配置（`EMBEDDING_PROVIDER=openai_compatible`、`EMBEDDING_MODEL=text-embedding-v3`，1024 维）对 84 个切片做真实嵌入往返；不加该旗标时用字符双字袋桩（离线、零成本、确定性）。产物标记 `embedding="real"`、`mode="real_embedding_with_stubbed_judges"`、`reranker="disabled"`。

| top_k | 臂 | 证据覆盖率 | 全覆盖率 | 相对基线 | 检索次数 | 额外 LLM 往返 | 送进生成的片段 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | single_hop | 0.9815 | 0.9630 | — | 1.00 | 0.00 | 5.00 |
| 5 | multi_hop | 1.0000 | 1.0000 | **+0.0185** | 2.07 | 1.00 | 7.56 |
| 5 | multi_hop_forced | 1.0000 | 1.0000 | **+0.0185** | 2.07 | 1.00 | 7.56 |
| 5 | single_hop_judge_refine | 0.9815 | 0.9630 | +0.0000 | 2.00 | 2.00 | 5.48 |
| 8 | single_hop | 1.0000 | 1.0000 | — | 1.00 | 0.00 | 8.00 |
| 8 | multi_hop | 1.0000 | 1.0000 | +0.0000 | 2.07 | 1.00 | 8.00 |
| 8 | multi_hop_forced | 1.0000 | 1.0000 | +0.0000 | 2.07 | 1.00 | 8.00 |
| 8 | single_hop_judge_refine | 1.0000 | 1.0000 | +0.0000 | 2.00 | 2.00 | 8.00 |

`multi_hop` 与 `multi_hop_forced` 在这一轮完全重合，说明门槛已不再拦截任何一题。验收对照（`docs/review/2026-10-project-diagnosis.md` §6 七天第 1 条）：

| 验收项 | 修复前 | 修复后 |
| --- | --- | --- |
| `_looks_multi_hop` 放行率 | 3.7%（27 题放行 1 题） | **100%**（产物字段 `multi_hop_gate_hit_rate=1`） |
| `multi_hop` vs `single_hop`，top_k=5 | 0.9630 vs 0.9630（门槛不放行，两臂相同） | 1.0000 vs 0.9815（**不劣**） |
| `multi_hop` vs `single_hop`，top_k=8 | 0.9815 vs 0.8148（**劣 16.7pt**） | 1.0000 vs 1.0000（**不劣**） |
| 额外 LLM 往返 | — | 仅门槛放行时（本语料 100%，混合语料 10.4%） |

门槛误判抽查（96 题，跨 6 个评测集：`qa_dataset` 32 / `generation_eval_dataset` 35 / `legal_retrieval_qa` 17 / `document_rag_qa` 6 / `agentic_rag_cases` 4 / `graph_rag_eval_cases` 2）：放行 10 题 = **10.4%**，其中 9 题确实是两问（如“经济补偿金按工作年限怎么计算？工作三年半应该拿多少个月工资？”），唯一误判是“今天北京的天气怎么样，适合出门吗？”这类域外闲聊。

真实嵌入 vs 桩嵌入（同为修复后代码、同为 `top_k=5`，桩产物 `eval/outputs/agentic_rag_legal_ablation_stub_k5.json`）：`single_hop` 逐题覆盖 23/27 相同，覆盖率 **0.9444（桩）→ 0.9815（真实）**——嵌入质量确实改变召回结果，但结论方向不变（`multi_hop` 两臂在两种嵌入下都是 1.0000）。注意这与上表的桩数字（0.9630）不同：上表是修复前跑的老口径，本节两行才是同代码对比。

结论与默认值：

1. `AGENTIC_RAG_MULTI_HOP_ENABLED` **默认值翻为 True**（`app/core/config/rag.py`）。两个阻塞点（门槛、预算）均已解除，`top_k=5/8` 都不劣于单跳，代价只在门槛放行时多 1 次 LLM 往返（混合语料 10.4%）。**评测里的分解是脚本桩，因此上表收益是上界**；接真实分解器后需按同一口径复测一次。
2. `AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED` **保持关闭**：真实嵌入下 `top_k=5` 与 8 均为零收益（0.9815 / 1.0000，与单跳基线逐题相同），恒定 2 次 LLM 往返。
3. `AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED` **保持关闭**：要真实生成才能测，离线口径测不了（“没测”，不是“测过没用”）。

已知现象（不影响结论）：某次 `--top-k 5 8` 运行打印了 3 条 `Vector store dense recall unavailable; continuing with other recall routes (KeyError)`（唯一来源 `app/services/rag/retrieval.py` 的密集召回汇合处），同一命令另一次运行 0 条；两次产物在 8 个“臂 × top_k”组合、27 题上逐题 coverage / missed 完全一致，故按噪声处理，成因未定。

## 法律检索评测（17 题，纯规则）

```powershell
python -B eval/run_legal_retrieval_eval.py --top-k 5 --output eval/outputs/legal_retrieval_report.json
```

结果：`Hit@5 = 1.0`、`MRR = 0.8562`、`拒答准确率 = 1.0`、badcase 0。17 题标注在 `eval/legal_retrieval_qa.json`（含精确引用、类别、拒答题），语料 `eval/legal_sources_corpus.json`。这条链路只衡量 `_rank_sources_by_relevance` 的规则排序，不调 LLM 也不走向量检索，因此与嵌入质量无关，可随时零成本重跑。

## 样例 QA 真实生成 + 判分评测（32 题，2026-10-17；标签失效，见下节修复）

```powershell
$env:PYTHONPATH="D:\AI\llmXM\AI法律助手"
python -B scripts/run_qa_eval_real.py --dataset eval/qa_dataset.json --top-k 5 `
  --min-interval 10 --backoff 30,60,90,120,180 --output qa_eval_real_generation_judge.json
```

真实链路：`text-embedding-v3` 索引 `eval/fixtures/` 三篇（9001 服务合同 / 9002 实施方案 / 9003 尽调报告）→ `top_k=5` → rerank 生效（每例 5 个候选）→ `glm-5.2` 生成 → `glm-5.2` 逐题判分。节流 10 s/次（供应商 7 次/分钟后返回 `rpm exhausted`），耗时：生成 1009.4 s、总 1659.3 s（27.7 分钟）。产物 `eval/outputs/qa_eval_real_generation_judge.json`（379 KB）。

全量指标（32 题 = 29 可答 + 3 应拒答）：

| 指标 | 值 |
| --- | --- |
| hit@5 / MRR / nDCG@5 | 0.7931 / 0.7759 / 0.7804 |
| retrieval_evidence_coverage / citation_accuracy | 0.7931 / 0.7586 |
| refusal_accuracy | 1.0（3/3） |
| average_retrieval_rounds | 1.41 |
| 延迟 avg / p50 / p95 | 31529 ms / 20901 ms / 63550 ms |
| LLM 裁判（32 题全覆盖） | groundedness 0.9375、answer_relevance 0.8438、completeness 0.7812、pass_rate 0.8125 |
| badcase_count | 7 |

⚠️ **这批标签有 9/32 题对不上语料，因此上表的检索类指标不能当 KPI 用**（2026-10-17 已修复并重跑，见本节末「标签修复记录」）。

### 标签修复记录（2026-10-17）

**取舍**：不恢复旧 fixture——`git log --oneline -- eval/fixtures/` 全仓只有一个提交 `b2ffc7f`，且 9002 fixture 是那次提交**新增**的（41 insertions）；即不存在"被改写的旧版本"可回滚，而是题面/关键词从一开始就照另一个未提交的样例写的。故改为按现有正文重写标签。

**修复脚本** `scripts/repair_qa_dataset_labels.py`（可重复执行、支持 `--dry-run`）：

- 9002 整块按正文重写：`plan_target_users`（面向个人用户与小型律所）、`plan_goal_capabilities`、`plan_scope_modules`、`plan_milestone_core_features`（2026-07-05 + 合同审查与法律咨询核心功能）、`plan_risk_stale_statutes`、`plan_risk_hallucination`、`plan_risk_incomplete_facts`、`plan_acceptance_materials`；沿用 3 题（`plan_milestone_go_live`、`plan_team_composition`、`plan_training_schedule`）；原 `plan_scope_exclusion` 改为拒答题 `refusal_plan_scope_exclusion`（正文没有"不在交付范围内"的清单）。
- 9001 `contract_scope_modules`、9003 `vendor_delivery_history` / `vendor_overall_conclusion` 按正文原串改措辞（正文是「合同文档解析与**条款**结构化抽取」「12 个**法律**AI应用项目」「具备**法律**AI项目交付经验」）。
- 三份文档 `document_name` 与 `eval/corpus_manifest.json` 同步为正文标题（9001 技术服务合同审查样本 / 9002 律智检平台实施方案 / 9003 法律服务供应商尽调报告）。
- 规模：**35 题 = 31 可答 + 4 应拒答**（原 32 = 29 + 3）。复测 `scripts/probe_qa_dataset_alignment.py`：35 题全部「缺失关键词 0 个」。

**注意**：重写是在已看过上一轮模型输出的前提下完成的，这 8 道新题**不是盲测**；指标判定用的是正文原串，与模型输出无关，但若要声称"盲测"需另出一套题。

**重跑命令**（`--min-interval 10` 是必须的，`rpm exhausted` 上限 7 次/分钟）：

```powershell
$env:PYTHONPATH="D:\AI\llmXM\AI法律助手"
# 必须先索引夹具并沿用同一个向量库目录，否则检索恒空、指标恒 0
$env:CHROMA_PERSIST_DIR="D:\AI\llmXM\AI法律助手\chroma_eval_store"
python -B scripts/index_eval_fixtures.py
python -B scripts/run_qa_eval_real.py --dataset eval/qa_dataset.json --top-k 5 `
  --min-interval 10 --backoff 30,60,90,120,180 --output qa_eval_repaired.json
```

### 修复后重跑结果（35 题，2026-10-17）

产物 `eval/outputs/qa_eval_repaired.json`（340 KB，35 题全跑 + 35 题全判分）：

| 指标 | 值 |
| --- | --- |
| hit@5 / MRR / nDCG@5 | **1.0 / 1.0 / 1.0** |
| retrieval_evidence_coverage / citation_evidence_coverage | **1.0 / 1.0** |
| citation_accuracy | **1.0** |
| refusal_accuracy | **1.0**（4/4） |
| badcase_count | **0** |
| average_retrieval_rounds | 1.29 |
| 延迟 avg / p50 / p95 | 26239 ms / 16978 ms / **52349 ms** |
| LLM 裁判（35 题全覆盖） | groundedness **1.0**、answer_relevance **1.0**、completeness 0.9714、pass_rate 0.9429 |

对照修复前那版（32 题、9 题标签失效）：hit@5 0.7931 → 1.0、引用准确率 0.7586 → 1.0、badcase 7 → 0。**低分的成因确实全在标注**，产品侧没有检索或引用失败。

**注意**：重写是在已看过上一轮模型输出的前提下完成的，这 8 道新题**不是盲测**；指标判定用的是正文原串，与模型输出无关，但若要声称"盲测"需另出一套题。

### 顺带修掉的真缺陷：评估节点的隐藏门槛

`app/services/rag/agentic_rag_service.py` 的 `_workflow_assess_evidence` 原写作
`threshold = max(float(runtime_config["confidence_threshold"]), 0.45)` —— 把低于 0.45 的
`RAG_CONFIDENCE_THRESHOLD` **静默抬高 0.1**，于是同一条问答链路上图节点与
`answer_from_chunks_async`（`rag_service.py:415`）用两套口径。探针
`scripts/probe_refusal_confidence.py` 实测：旧题面（"核心页面原型与接口联调…"）置信度 **0.4420**，
正好落在 `[0.35, 0.45)` 里被判 `refine_or_refuse` 而拒答；改成正文用词后同一题 **0.6312** 顺利生成；
文档确实没有的预算问题 **0.3077** 仍被正确拒答。现改为只取配置值，并补两个单测
（`tests/test_agentic_rag_depth.py::AssessEvidenceThresholdTests`：0.40/0.35 → generate，0.30/0.35 → refine_or_refuse）。

### 评测集失效：9 题标签与 fixture 正文不符

判据：逐题把 `expected_chunk_keywords` 与其目标 fixture 正文做子串比对（脚本 `scripts/probe_qa_dataset_alignment.py`、`scripts/analyze_qa_eval_validity.py`）。缺失关键词如下（括号内为标签期望值）：

- 9001 `contract_scope_modules`：合同文档解析与结构化抽取 / 文档问答与引用展示 / 会议纪要生成与行动项提取
- 9002 `plan_target_users`：行政与法务团队
- 9002 `plan_scope_modules`：文档上传与解析中心 / RAG 问答与引用展示模块 / 系统监控与模型调用统计面板
- 9002 `plan_scope_exclusion`：邮件营销模块 / 外部 CRM 集成
- 9002 `plan_milestone_prototype`：原型与接口联调
- 9002 `plan_risk_latency`：延迟波动超过 30% / 会议摘要接口会出现超时风险
- 9002 `plan_risk_permission`：权限边界定义不清 / 跨部门数据可见性问题
- 9003 `vendor_delivery_history`：12 个 AI 应用项目（正文实为「12 个法律AI应用项目」）
- 9003 `vendor_overall_conclusion`：具备 AI 项目交付经验

根因：`eval/fixtures/project_delivery_plan.md` 正文标题已是「律智检平台实施方案」，交付范围是「法律知识库 / 法律咨询辅助 / 合同智能审查 / 法律文书草稿生成 / 律师审核与版本留痕」，里程碑是 2026-06-20 / 07-05 / 07-25 / 08-05，风险是「法规版本 / 模型幻觉 / 事实不完整」；而题面与关键词仍在描述旧样例「AI办公助手」（面向行政与法务团队、模块含文档上传与解析中心、排除邮件营销与外部 CRM、风险是 API 延迟与权限边界）。`eval/corpus_manifest.json` 里 9002 的 `document_name` 至今写着「AI办公助手实施方案」——fixture 被改写时，manifest 与 `eval/qa_dataset.json` 都没跟。

重算口径（`scripts/analyze_qa_eval_validity.py`）：

| 口径 | 可答题 | hit@5 | MRR | 证据覆盖率 | 引用准确率 |
| --- | --- | --- | --- | --- | --- |
| 全量 | 29 | 0.7931 | 0.7759 | 0.7931 | 0.7586 |
| 有效题（23/32） | 20 | **1.0** | **0.975** | **1.0** | **1.0** |
| 失效题（9） | 9 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |

7 个 badcase 逐条归因：

- 3 个 `retrieval_miss`（合同服务范围 / 项目面向哪些团队 / 交付范围五个模块）：标签关键词在正文里根本不存在；而这三次生成的答案其实都正确且逐字对应正文（「合同文档解析与**条款**结构化抽取」、「面向个人用户与小型律所」、「法律知识库、法律咨询辅助、合同智能审查、法律文书草稿生成、律师审核与版本留痕」）→ 标注失效，不是产品失败。
- 4 个 `false_refusal`：其中 3 题（哪些不在本期交付范围 / 模型 API 延迟风险 / 用户权限风险）问的正是正文里没有的内容，拒答是**正确行为**；只有 1 题是真失败——`plan_milestone_prototype`（「核心页面原型与接口联调的计划完成时间」）证据已排在第 1 位、覆盖率 1.0，模型仍以 confidence 0.3 < 拒答阈值 0.35 拒答。
- 因此 32 题里真实的产品失误只有这 1 题，其余低分来自评测集。

### 待办（已并入上面的「标签修复记录」）

本节 32 题口径自 2026-10-17 标签修复起只作历史留档；新口径按修复后的 35 题重跑，见本节末。

### 复现前置

- 供应商限流：chat 侧 7 次/分钟即 `rpm exhausted`（探针 `scripts/probe_chat_429.py`），必须节流；嵌入侧 60 s 内 20 次触发 `LLM_RATE_LIMIT_EXCEEDED`，且当日额度计数会被历史消耗吃掉，脚本里需在进程内抬高上限（`eval/run_agentic_rag_eval.py` 的 `_lift_llm_limits()`）。
- 跨事件循环复用 httpx 客户端会让第二次真实嵌入抛 `RuntimeError: Event loop is closed`（`index_document` 内部用 `run_async` 换循环，客户端却被缓存在 `llm_client._clients`）→ `scripts/run_qa_eval_real.py` 逐题前后清空 `_clients` 规避。

### 忠实度真模型校验（31 题，2026-10-17）

判据：不重跑检索与生成，直接吃上面那份 35 题产物里「可答且已作答」的 31 个答案，把答案与该题的引用片段（`citations[].source_text`）交给 `app/services/rag/agentic_judge.py` 的 `check_faithfulness` 逐题判分，问的是「答案是否只用了证据里的内容、有没有编造法条或数字」。

```powershell
$env:PYTHONPATH="D:\AI\llmXM\AI法律助手"
python -B scripts/run_faithfulness_eval.py --result eval/outputs/qa_eval_repaired.json `
  --output faithfulness_report.json --min-interval 10
```

产物 `eval/outputs/faithfulness_report.json`（8.3 KB，31 题逐题留档）：

| 指标 | 值 |
| --- | --- |
| cases / judged / unavailable | 31 / 31 / 0 |
| faithfulness_rate | **1.0**（31/31 faithful） |
| 含不支持陈述的题数 | **0** |
| 不支持陈述总条数 | 0 |

口径与限制（照实写，别把它当独立验证）：

- **裁判与被评对象是同一个模型**（`glm-5.2`），不是交叉模型。自家判自家偏宽松，1.0 只说明「按该裁判口径没发现编造」。
- 证据只喂了 `citations[].source_text`（写进答案的引用片段），不是当时完整的 top-5 检索上下文；答案若靠未被引用的块推出内容，这里判不出来。
- 因此 `AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED` 仍保持默认 `False`：开着它等于给每个可答题多加一次 LLM 往返（7 次/分钟口径下 ≈ +10 s）。
- 要拿它当上线依据，先按诊断文档 §8 接交叉模型（deepseek-v4.1-flash）复测同一批答案，两家差异才是真实的「裁判口径差」。

**未入库法条能否标出**：`tests/test_legal_reference_verification.py` 13 passed，含 `:136` 对
`verification_note` 含「无法核验」的断言；`app/services/legal/legal_reference_service.py:23` 的
`_UNVERIFIED_NOTE = "未关联法源库条目，无法核验"` 会给未命中 `legal_sources` 的引用打上
`verification.verified = false`——首轮真实咨询响应（形状 A）已实测带这条 note。

### 交叉模型裁判（qwen-plus）一轮结果（2026-10-17）

判据：不重跑检索与生成，把上面两份产物原样喂给**另一个模型家族**的裁判逐题复判。忠实度复用
`app/services/rag/agentic_judge.py:137` 的 `check_faithfulness` prompt 原文（只换模型与 HTTP 出口，输入仍是
问题 + 系统答案 + 写进答案的引用片段）；三维质量复用 `eval/llm_judge.py` 的 groundedness / answer_relevance /
completeness prompt 原文。两边都**不改措辞**，否则口径不可比。

**对诊断文档 §8 的替代**：§8 计划用 `deepseek-v4.1-flash`，但仓库里没有任何 deepseek 凭据（`.env` 只有
sensenova 的 `LLM_*` 与 dashscope 的 `EMBEDDING_*`）。探针 `scripts/probe_cross_judge.py` 实测 `EMBEDDING_API_KEY`
可直接调 dashscope 兼容模式的 chat 模型（`qwen-plus` 200、`qwen-flash` 200、`qwen-turbo` 429、`qwen3-max` 不存在），
故本轮裁判用 **`qwen-plus`**——与 glm-5.2 不同模型家族，满足「交叉」目的。拿到 deepseek key 后加
`--judge-model` / `--judge-base-url` / `--judge-api-key` 三个参数即可对同一批答案复跑。

```powershell
$env:PYTHONPATH="D:\AI\llmXM\AI法律助手"
python -B scripts/run_cross_judge.py --min-interval 10 `
  --output cross_judge_report.json --sheet-output divergence_review_sheet.md
```

真实调用与限流：`qwen-plus` 共 **66 次真实请求**（31 忠实度 + 35 三维），**429 = 0、退避重试 = 0、失败 = 0**，
耗时 **652.5 s**（≈9.9 s/次，节流 10 s），prompt 35,110 / completion 2,846 tokens；这些数字都落在产物的 `run`
字段里（`ok_calls` / `rate_limit_429_hits` / `retries` / `elapsed_seconds`）。

产物 `eval/outputs/cross_judge_report.json`（60.5 KB，逐题留档）+ `eval/outputs/divergence_review_sheet.md`
（人工抽检表）：

| 指标 | glm-5.2（原口径） | qwen-plus（交叉口径） |
| --- | --- | --- |
| 忠实度率 | 1.0（31/31） | **1.0（31/31）** |
| groundedness 均分 | 1.0 | 1.0 |
| answer_relevance 均分 | 1.0 | 1.0 |
| completeness 均分 | 0.9714 | 0.98 |
| pass_rate | 0.9429（33 pass / 2 review） | 0.9429（33 pass / 2 review） |

分歧：忠实度 **0/31**，三维差 ≥0.1 档 **1/35**，verdict 变化 **0/35**。

- 唯一分歧 `plan_team_composition`（completeness 0.5 → 0.8，两家都判 `review`）：glm 认为漏掉各角色人数就是
  「完整性不足」，qwen-plus 认为人数缺失只是「略欠缺」；判罚方向一致，差在扣分力度。
- 两家都点名 `plan_risk_incomplete_facts` 与 `plan_team_composition` 判 `review`（其余 33 题 `pass`），
  本批没有出现「一边过、一边不过」。

限制与诚实说明：

- **同一批答案、同一份证据、非盲测**：只换了裁判模型，答案与引用片段完全相同。结论只能说明「本批 66 题上
  换裁判家族后口径差很小」，**不能**说明答案本身正确——两家都是 LLM 裁判，且都偏宽松（平均分几乎顶格）。
- glm 侧数字来自已有产物（`qa_eval_repaired.json` / `faithfulness_report.json`，未重跑）；qwen 侧 66 次调用
  为本轮新发生。
- 忠实度复测仍只喂 `citations[].source_text`，不是当时完整的 top-5 检索上下文：与 glm 口径可比，也一致地
  留着同一个盲区。
- 人工抽检表本轮只列到 **1 条**分歧（分歧确实只有 1 条），表里每条都留了空的 `人工结论：` 与 `备注：`。
  `scripts/score_human_review.py` 读填好的表算「AI 裁判 vs 人工」同意率：分母 = 人工表了态（`正确`/`错误`）
  且该侧裁判有明确判罚的条目，`无法判断` 与留空不进分母；表没填时分母为 0，脚本打印 `n/a` 而不是 1.0。
  当前尚无真人填表，因此**人工一致率还没有数**。

```powershell
python -B scripts/score_human_review.py --sheet eval/outputs/divergence_review_sheet.md
```

## baseline 快照（2026-10-19 固化）

90 天项「baseline 快照」：`eval/run_experiments.py` 早就有 `--check-regression` 与快照比对逻辑，
但**快照文件从来没有被固化过**（`eval/outputs/baseline_snapshot.json` 一直缺失），于是「评测与审计」页的
baseline 区块长期为空，也没有人能一键判断改动是否让检索/回答退化。本轮把它补上。

```powershell
$env:CHROMA_PERSIST_DIR='./.tmp_chroma_eval'      # 默认 data/ 目录对 python 子进程不可写（本机 ACL）
$env:EVAL_CASE_PAUSE_SECONDS='12'                 # 免费额度网关 rpm/tpm 上限很低，按题节流
python -B scripts/eval_baseline.py --write --output-dir .tmp_baseline_out
# 产物先落在仓库根目录下的临时目录，再 Move-Item 到 eval/outputs/（同上 ACL 原因）
python -B scripts/eval_baseline.py --check        # 回归门禁：退出码 2 = 检出回归或配置漂移
```

| 项 | 值 |
| --- | --- |
| 数据集 | 35 题（`eval/qa_dataset.json`，3 篇 fixture 语料） |
| hit_at_k | 1.0 |
| mrr | 1.0 |
| citation_accuracy | 1.0 |
| refusal_accuracy | 1.0 |
| retrieval_evidence_coverage | 1.0 |
| citation_evidence_coverage | 1.0 |
| badcase_count | 0 |
| 锚点 | git `78c8514`、prompt_version 1、top_k 5、confidence_threshold 0.35、`glm-5.2` + `text-embedding-v3` |

**如实说明**：这批指标在 fixture 语料上已经**饱和**（全 1.0），所以快照的价值是「防回归」
（任何一次掉分都会在 `--check` 里显形），而不是区分不同方案；有区分度的结论要看下面的混合检索消融
与 `qa_eval_repaired.json`（35 题真实语料）。

## 混合检索消融（2026-10-19）

90 天项「混合检索调优」：把 RRF 融合常数、稠密候选宽度、LLM 重排三个旋钮放到同一套 17 题上对照，
走的是**生产检索路径** `LegalRetrievalService.search`（不是评测专用实现）。

```powershell
python -B scripts/run_hybrid_retrieval_ablation.py --pretty --output .tmp_hybrid_ablation.json
```

语料 18 部法规 / 17 题 / top_k 5 / 真实 `text-embedding-v3` 向量 / git `78c8514` / `2026-10-19T03:12:51Z`。

| 臂 | Hit@5 | MRR | 拒答准确率 | 避让违规 | badcase | 耗时(s) |
| --- | --- | --- | --- | --- | --- | --- |
| 词级 only（无向量） | 1.0 | 0.8802 | 1.0 | 0 | 0 | 0.45 |
| 混合 RRF k=60（当前默认） | 1.0 | 0.8802 | 1.0 | 0 | 0 | 0.78 |
| 混合 RRF k=10 | 1.0 | 0.8802 | 1.0 | 0 | 0 | 0.13 |
| 混合 RRF k=200 | 1.0 | 0.8802 | 1.0 | 0 | 0 | 0.12 |
| 混合 + 稠密候选 10（默认 30） | 1.0 | 0.8802 | 1.0 | 0 | 0 | 0.11 |
| 混合 + LLM 重排 top5 | 1.0 | **0.9531** | 1.0 | 0 | 0 | 209.38 |

结论与决策：

1. **RRF 常数不是敏感旋钮**：k=10 / 60 / 200 三档的 Hit@5 与 MRR **完全一致**（0.8802），
   所以 `RAG_RRF_K` 保持默认 60（RRF 论文口径），不需要调参——这一点由消融数据支撑，而不是凭感觉。
2. **稠密候选宽度也不敏感**：30 → 10 同样没有变化（0.8802），默认 30 保留。
3. **LLM 重排是唯一有效杠杆，但代价不可接受**：MRR 0.8802 → 0.9531（+0.073），
   耗时 0.1–0.8s → **209.4s**（≈12.3s/题，约 270×）。因此**不设为默认路径**，保留为高价值查询的可选档。
4. **本套评测已饱和**：Hit@5 六臂全 1.0，说明 17 题/18 部法规的语料太容易，**不足以支撑更细的调优结论**；
   要继续调优得先做更有区分度的检索评测集（含同法条干扰项、跨法条多跳题）。
5. 拒答准确率与「避让失效法源」违规在六臂上都是 1.0 / 0——没有出现「为了召回而牺牲合规」的退化。

产物：`eval/outputs/hybrid_retrieval_ablation.json`（含每题的 default/arm 排名对照，便于定位翻转题）。

**顺带发现的开口项（2026-10-19 已修）**：跑 baseline 时日志出现过一次
`BM25 keyword recall error; falling back to scan (AttributeError)`——BM25 索引路径偶发 `AttributeError`
后回退到 jieba 全表扫描（功能正确、只是慢）。

**根因（已复现并修复）**：`app/services/rag/retrieval.py` 的构建/打分存在 TOCTOU 竞态——`_build_bm25_index()`
在锁内**先把线上索引置空**（旧 `self._bm25_index = None`、`self._bm25_items = []`）再重建，而并发打分线程在
`_bm25_keyword_recall()` 里通过 `is None` 判断之后才调用 `self._bm25_index.get_scores(...)`，正好撞上这个空窗 →
`AttributeError: 'NoneType' object has no attribute 'get_scores'`（`retrieval.py:190`），被上层 `except` 兜住并回退全表扫描。
**修法**：① 构建结果先在局部变量里攒好、锁内**一次性发布**（`items` 与 `index` 成对赋值，中途不置空、不逐条追加）；
② 打分前把 `index` / `items` 快照到局部变量，打分过程中不再读 `self.*`；③ 构建失败（Chroma 取数异常）时**保留上一版索引**
并继续标记 `stale` 下轮重试，而不是把可用索引清空。
**回归测试**（`tests/test_rag_and_observability.py::BM25IndexTests`）：`test_concurrent_rebuild_during_scoring_does_not_crash`
（用 patch 在打分中途换掉索引，模拟并发重建——旧代码上正是 `AttributeError: 'NoneType' object has no attribute 'get_scores'`）、
`test_build_failure_keeps_previous_index`。RAG/检索相关 8 个测试文件 **166 passed**。

