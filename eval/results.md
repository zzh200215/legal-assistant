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
