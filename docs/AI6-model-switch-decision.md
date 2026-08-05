# AI-6 模型切换决策（#55 附录，2026-08-05）

## 结论

**当前维持 qwen-plus，不切换到 qwen-max。** 理由：串行真评下 qwen-plus 已达 99.2%（124 题，v2.1），高于 qwen-max 批量对比的 94.1%（119 题，v2.0），且 qwen-plus 定价更低；两者并非同题集同条件的公平对比，正式切换决策待 v3 冻结后同题集串行复测再定。

## 1. 对比数据现状（三份报告的差异务必区分）

| 来源 | 模型 | 题集 | 方式 | 通过率 | 可信度 |
|---|---|---|---|---|---|
| `model_compare_qwen-plus.json` | qwen-plus | v2.0 119 题 | 批量并发 | 72.3% | ❌ 受限流/降级路径污染，**弃用** |
| `model_compare_qwen-max.json` | qwen-max | v2.0 119 题 | 批量并发 | 94.1% | ⚠️ 参考（同批条件，qwen-plus 同受污染） |
| `model_compare_qwen-turbo.json` | qwen-turbo | v2.0 119 题 | 批量并发 | 79.8% | ⚠️ 参考 |
| `generation_eval_report_real_qwenplus.json` | qwen-plus | v2.1 124 题（含 5 条真实语料） | 串行真评 | 99.2% | ✅ **权威基准** |

关键：qwen-plus 的 72.3% 与 99.2% 差距全部来自**批量并发时 LLM 限流/超时导致部分用例走降级路径**（见 AI1-real-corpus-v2-report.md），不是模型能力差异。因此 qwen-plus vs qwen-max 的"94.1% vs 72.3%"对比是**无效对比**，不能作为切换依据。

## 2. 成本口径（M-1 单价）

- 定价常量：qwen-plus `input 0.004 / output 0.012`（元/千 token，`app/core/config.py:52`，LLM_MODEL_PRICING 可配）。
- M-1 实测单价：咨询 ≈ ¥0.012/次、合同审查全流程 ≈ ¥0.10-0.15/次；免费版 ≈ ¥0.34/人/月获客成本（ai-sprightly-floyd.md）。
- 团队版 999 元/月、上限 5000/2000/2000，最坏 ≈ ¥450 成本，毛利仍为正。
- qwen-max 定价**未配置**在 LLM_MODEL_PRICING 中，无法自动核算；按 dashscope 公开价估算约为 qwen-plus 的 3-10 倍（复测时需将价格加入 config 才能出准确毛利口径）。

## 3. 决策逻辑

1. **切换的唯一理由**是"更强模型显著提升质量"。但串行真评下 qwen-plus 已 99.2%（合同审查 F1 0.845、文书必填字段 100%、拒答 8/8、法条失效 4/4），提升空间极小，qwen-max 的边际收益不成立。
2. **成本**：qwen-max 贵数倍，若按全量切换，免费版获客成本与团队版毛利均会恶化，与 M-1 已核实的毛利为正前提冲突。
3. **剩余质量短板是 prompt/评测集问题而非模型问题**：cr_006（缺失条款召回 0.0）已通过 prompt 调优修复并真评验证（09-16，recall 0→1）；这类问题换模型不解决。
4. **高价值场景可选切换**：若未来出现"合同审查高风险条款场景质量不达标"且 qwen-plus 复测无法提升，可只对 contract_review 动作单独配置 qwen-max（LLM_MODEL 支持动作级覆盖），再评估毛利，而非全局切换。

## 4. 后续动作（触发条件）

- **前置**：v3 真实语料冻结（≥30 条）或试点数据到位后，用 `run_generation_eval.py`（串行）对 qwen-plus 与 qwen-max 跑**同题集**真评，输出同一张对比表（`compare_models.py` 仅限单模型串行跑，勿并发）。
- 若 qwen-max 同题集通过率 ≥ qwen-plus +2pct **且** 该动作毛利仍为正 → 对该动作切换；否则维持 qwen-plus。
- 复测前先把 qwen-max 定价加入 `LLM_MODEL_PRICING`，保证 cost 口径一致。

## 5. 关联

- #55 AI-1 真实语料评测 v2 报告（本决策的基准数据来源）
- AI-1 v3 冻结（待真实语料）→ 本决策的复测触发器
- M-1 成本核算（定价与毛利口径）
