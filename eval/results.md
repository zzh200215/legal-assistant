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
2. `AGENTIC_RAG_MULTI_HOP_ENABLED` **保持关闭，但原因不是分解没用**。分解本身在 `top_k=5` 把覆盖率 0.963→1.000、全覆盖 0.926→1.000，只多 1 次 LLM 往返——是这三项里唯一拿得出收益的。问题有两个：门槛只放行 3.7% 的自然提问，照现状打开约等于不生效（`multi_hop` 臂与基线完全相同）；`top_k=8` 时子问题的结果会把必需证据挤出 `RAG_CONTEXT_MAX_CHUNKS=8` 的上下文预算，覆盖率反降 16.7pt（三条臂在该点的片段数都顶到 8.00，可以看出是预算截断而不是召回变差）。要开就得先改门槛判定，并给子问题结果单独分配预算，否则宽 `top_k` 部署会变差。
3. `AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED` **保持关闭（无数据）**。它校验的是生成结果与证据是否一致，离线桩生成测不出来，需要真实 LLM 往返才能评。这是“没测”，不是“测过没用”。

限制：

- 只衡量送进生成节点的证据覆盖率，不衡量答案质量或忠实度。
- 嵌入是字符双字袋（确定性、离线可跑），不是真实嵌入模型。结论的方向（分解提覆盖、宽 `top_k` 下 fan-out 挤预算）可复现，具体交叉点会随嵌入变化。
- 分解与判分是脚本桩，总能给出正确的子问题与 missing，因此表里的收益是**上界**；真实 LLM 拆错或判错只会更低。
- 27 题全部是“两处条文”结构的多跳题，对多跳有利；单跳问题占比高的真实流量里，`multi_hop_forced` 那 1 次额外往返的性价比会更差。
