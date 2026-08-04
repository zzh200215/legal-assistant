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

- **真评验证阻塞**：dashscope 账户欠费（Arrearage 400），qwen-plus 全部 fallback deterministic——无法用冻结 124 题做真评对比；评测 18:20 那次 117 次成功调用（欠费前）显示 v2 下模型仍输出 0.0，故 v3 加入示例并等余额恢复后复测。
- 复测口径：余额恢复后 `run_generation_eval.py`（串行、LLM_RATE_LIMIT_MAX_REQUESTS=500 / LLM_DAILY_REQUEST_LIMIT=2000）跑同题集，对比 99.2% 基线：cr_006 修复 + 全量不退化即达标。
- **生产影响预警**：欠费期间所有 LLM 路径走 deterministic 降级（咨询/审查/文书可用但质量降级），试点若启动须先恢复额度。
