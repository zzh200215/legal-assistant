# 试点周报（口径 #46，周起点 2026-08-04）

> 生成时间：2026-08-04T01:29:17.751674+00:00；已排除供给账号 20 个。

## 1. 漏斗

- 注册（累计，非供给）：9（周起点前 9）
- 本周首次咨询用户：0

## 2. 留存/北极星

- 北极星口径（每周完成 ≥1 次 AI 辅助法律任务的有活跃案件律师数）：见 /api/admin/north-star

## 3. AI-2 回流

- 本周审核动作：{}
- 回流游标 last_action_id：0

## 4. 成本

- 本周 LLM 成本：¥0.0579
- 按日/模型调用：[{"day": "2026-08-04", "model": "qwen-plus", "calls": 7, "prompt_tokens": 2656.0, "completion_tokens": 3937.0, "estimated_cost_cny": 0.0579}, {"day": "2026-08-04", "model": "text-embedding-v3", "calls": 2, "prompt_tokens": 9.0, "completion_tokens": 0.0, "estimated_cost_cny": 0.0}]

## 5. 质量反馈

- 端侧 👍/👎：null（生产库无 ai_output_feedback 表，属预期——试点启动前无端侧反馈；试点启动后确认表结构按需补迁移）

## 6. NPS 与退出问卷基线（#52）

- 问卷与 NPS 结构已就绪：`docs/pilot-success-playbook.md` 第 5 节（A. NPS：0-10 分 + 追问；B. 漏斗回溯；C. 场景偏好；D. 退出原因；E. 合规确认）。
- 基线口径：NPS ≥40 为达标（ai-sprightly-floyd.md 验证方式 #5）。
- 衔接：试点结束时问卷回收 → 与漏斗/反馈/AI-2 数据合并 → 产出《试点验证报告》（回答哪些假设被验证/证伪），作为 AI-3/AI-4/AI-6 商业化输入。
- 补充收集：飞书插件场景问卷并入每周周报（见 `docs/feishu-plugin-scenarios.md` C 节）。

## 7. 演练备注

- 本包为试点启动前演练（真实数据仅 9 非供给注册、0 咨询），下周（08-10 起）生成正式首周周报：`python -B scripts/pilot_weekly_report.py`（默认周一起点，输出 JSON + 本 Markdown）。
