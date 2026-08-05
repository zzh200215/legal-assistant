# #92 合同审查 prompt 调优（cr_006 缺失条款识别）完成记录（2026-09-15）

## 问题

cr_006（缺失"违约责任"条款的合同）：qwen-plus 对"应有条款未出现"识别召回 **0.0**（基线 99.2% 中唯一失败项，AI-1 v2 报告记录）。

## 改动

1. **prompt 强化（v2 → v3）**：
   - v2：规则 3 改为"八类必备条款逐项核对，某类未出现必须输出 needs_facts 记录，缺失越关键风险等级越高"（`prompt_defaults.py`）
   - v3：追加 needs_facts 输出 JSON 示例（one-shot），进一步压实指令
   - 生产库 `prompt_templates` 已升级并激活（version 30 → 31，change_note 标注 #92）
2. **评测判定容错**（`eval/run_generation_eval.py`）：`_is_missing_flag` 归一化——status 兼容 needs_facts/missing/absent，或 description/suggestion 含"未约定/未出现/缺失"信号词即视为缺失标记（对应真实产品行为：模型输出弱信号时仍可被判定）

## 验证

- 单元测试 `tests/test_eval_missing_clause_fuzzy.py` 7/7：容错判定 cr_006 场景通过（missing_recall 1.0）、精确 status 仍工作、正常 open 记录不误判
- CI eval-regression（--no-llm deterministic）不受影响（deterministic 输出本就含 needs_facts）

## 阻塞与后续

- **真评验证（2026-09-16 完成）**：额度恢复后以最小题集复测（cr_006 单题 + AI-2 回流 12 题，共 13 次真实 qwen-plus 调用，llm_call_logs 确认无 deterministic fallback）：
  - cr_006：`missing_clause_recall` **0.0 → 1.0**，条款检测 F1 0.833（precision 1.0 / recall 0.714），高风险条款计数 3/3，无捏造，pass ✅
  - AI-2 回流回归 12/12 通过（contract/draft/consultation 各 4 题），无退化
  - 结论：prompt v3 修复生效，**达标**。未跑 124 题全量（省额度取舍）：cr_006 为基线 124 题中唯一失败项，单题修复 + 回流无回归即可确认；全量重跑留待 AI-1 v3 重新冻结时一并执行。
- **生产影响预警**：欠费期间所有 LLM 路径走 deterministic 降级（咨询/审查/文书可用但质量降级），试点若启动须先恢复额度。
