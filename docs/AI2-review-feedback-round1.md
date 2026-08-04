# AI-2 回流首轮复盘（#54，2026-08-04）

## 结论

- 生产库真实审核决策：**0 条**（legal_review_actions 仅有 1 条 submit_review 动作，无 approve/return/offline 决策）——试点未启动，属预期。
- 回流管线完整可用：`export_review_feedback.py` 真实执行返回 `{"status": "no_new_cases"}`，幂等；游标 `review_feedback_state.json` last_action_id=0。
- 回归基准：冻结集 **124/124 通过**（v2.1，含 5 条真实语料），AI-2 回流回归层 **12/12**。

## 首轮真实回流（08-17 试点首周后执行）

1. 试点启动后，律师审核动作（approve/return/offline）落 legal_review_actions → 每日巡检顺带报告游标。
2. 每周一跑 `export_review_feedback.py`（真实导出）→ 追加 `eval/review_feedback_eval.jsonl` → `run_generation_eval.py --review-feedback` 出对比。
3. 改善幅度口径（ai-sprightly-floyd.md 验证方式 #2）：律师通过率 / 引用正确率 / 退回原因分布，与本次基线 124/124 对比。

## 卡点预警

- 若首周审核决策 <5 条，回流对比延迟到第 3 周（数据量不足时对比无统计意义）。
- 端侧 👍/👎（ai_output_feedback 表）生产库缺失——若试点功能依赖该表，需先补迁移（当前未阻塞：回流走审核动作表）。
