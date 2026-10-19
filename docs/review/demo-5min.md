# 5 分钟 demo 脚本（含录制兜底与简历 bullet）

> 用途：面试/评审现场演示「AI 法律助手」主链路，5 分钟讲完；断网或后端不可用时直接放录制视频。
> 录制兜底：`docs/review/demo/demo-run.webm`（3.1 MB，真实后端 + 真实 LLM 跑出的 56.3s 轨迹）与 8 张逐拍截图 `docs/review/demo/0N-*.png`。
> 最近一次重录：**2026-10-19**（含第四层「生成后忠实度校验」——第 3/6 拍提问后判官会补判，画面里能看到「忠实度校验中 → 通过」的翻转，因此单次录制比首版长约 20s）。

## 0. 前置条件（演示前 5 分钟检查）

| 项 | 要求 | 检查命令 |
| --- | --- | --- |
| 后端 | `127.0.0.1:8001` 健康（DB/Redis ok） | `curl http://127.0.0.1:8001/api/health/ready` |
| 前端 | `npm run dev`（5173） | 打开 `http://localhost:5173` |
| 账号 | `demo_reviewer` / `Review@123456`（部门管理员，有组织与案件） | 登录后应落在「工作台」 |
| 演示数据 | 案件 #4「劳动争议·张某诉某科技（演示）」、合同审查 #1「技术服务合同（演示）」、文书草稿 #2「劳动争议仲裁申请书」 | 见 §4 复现命令 |
| 咨询配额 | `quota_usages.consultation_count < 5`（否则第 3 拍会 429） | 见 §4 重置 SQL |

> 注意：演示**必须用真实后端**（页面上的「服务端步骤 / 逐句依据 / 条级核验」都是真数据）；mock 演示会丢掉全部亮点。

## 1. 时间轴（8 拍，总计 ≈5 分钟）

| # | 画面（截图） | 说什么（要点） | 停留 |
| --- | --- | --- | --- |
| 1 | `01-workbench.png` 工作台 | 「这是律师的日常工作台：进行中案件、待处理审核、常用入口。左侧是收敛后的工作区导航——注意 Agent 对普通用户也开放。」 | 30s |
| 2 | `02-case-overview.png` 案件工作台 | 「案件工作台把案情、材料、法条、时间线放在一屏；右栏常驻助手，上下文跟着你看的页面走。」 | 30s |
| 3 | `03-copilot-trace.png` 提问后的助手面板 | **核心亮点 1**：提问后展示「处理过程」——检索法源 / 生成建议 / 抽取事实 / 核验引用四步，每步带状态与耗时，且标注「服务端步骤」（服务端 trace，不是前端事后回放）。 | 60s |
| 4 | `04-sentence-attribution.png` 点句子看依据 | **核心亮点 2**：点答案里的任意一句，只高亮支撑它的那几条证据（句级引用映射）；再点开证据卡看条文原文与「已核验 / 未核验」徽标（条级核验真的去比对法源条文）。 | 60s |
| 5 | `05-contract-context.png` 合同审查 tab | **核心亮点 3**：切到合同审查，上下文条变成「合同审查 · 案件 #4 · 合同 #1」——助手知道你正在看哪份合同。 | 30s |
| 6 | `06-contract-answer.png` 就合同提问 | 问「这份合同的付款期限是怎么约定的？」，答案逐条引用合同正文（签约后 5 日 100 万 / 阶段验收后 108 万 / 最终验收后 60 万）——上下文注入的是**后端按 id 读取并做权限校验**的正文。 | 60s |
| 7 | `07-draft-context.png` 文书 tab | 切到文书，上下文条变成「文书 #2」；同样可问「这份草稿还缺什么」。 | 30s |
| 8 | `08-full-page.png` 案件助手整页 | 「面板适合随手问，需要长对话就点『整页』进深度视图——三个入口（面板 / 常用入口 / 整页）收敛到同一套状态。」 | 30s |

## 2. 现场提问的备用问题（避免和录制完全重复）

- 案件工作台：「这份合同有哪些高风险条款？」「下一步该做什么？」
- 合同审查：「违约责任条款对我方是否不利？」
- 文书：「这份仲裁申请书还缺哪些信息？」

## 3. 断网 / 后端挂了的兜底

1. 直接播放 `docs/review/demo/demo-run.webm`（33.6s，含真实 LLM 往返）。
2. 用 §1 的 8 张截图逐拍讲——每张都是真实后端渲染，不是设计稿。
3. 录制的复现命令见 §4（一条命令重新录一遍）。

## 4. 复现命令

```powershell
# 演示数据体检（案件 / 合同审查 / 文书草稿 / 配额）—— 直连只读 SQL，随时可复跑
# 账号取自 .env 的 DATABASE_URL（mysql+pymysql://root:<pwd>@localhost:3306/aibg）
$M = 'D:\AI\Mysql\bin\mysql.exe'; $U = 'root'; $P = '<.env 里的数据库密码>'
& $M -u $U -p"$P" aibg -e "select id, title, status from legal_cases where id = 4"
& $M -u $U -p"$P" aibg -e "select id, title, status from contract_reviews order by id desc limit 3"
& $M -u $U -p"$P" aibg -e "select id, title, document_type, status from legal_drafts order by id desc limit 3"
& $M -u $U -p"$P" aibg -e "select user_id, consultation_count from quota_usages where user_id = 37"

# 配额重置（demo_reviewer = user 37；只影响本地演示）
& $M -u $U -p"$P" aibg -e "update quota_usages set consultation_count = 0 where user_id = 37"

# 重新录制轨迹（真实后端 + 真实 LLM，约 40s）
cd frontend
$env:E2E_RUN_INTEGRATION='true'; $env:E2E_DEMO_USERNAME='demo_reviewer'; $env:E2E_DEMO_PASSWORD='Review@123456'
$env:E2E_SHOT_DIR='D:\AI\llmXM\AI法律助手\frontend\.visual\demo'
npx playwright test demo-record --workers=1 --reporter=list --timeout=600000
# 产物：8 张 .visual/demo/0N-*.png + test-results/**/video.webm
```

## 5. 简历 bullet（每个数字都能一条命令复现）

> 溯源命令：`python -B scripts/demo_metrics.py`（现读 `eval/outputs/*.json` 与构建产物，不手抄）

- **法律咨询 Agentic-RAG 评测闭环**：35 题规则层（确定性）指标 hit@5 = 100%、证据覆盖率 = 100%、引用准确率 = 100%、拒答准确率 = 100%、badcase = 0；跨模型裁判（glm-5.2 × qwen-plus）忠实度 31/31 一致、分歧 0；延迟 p50 17.0s / p95 52.3s。
- **端到端可解释性（四层）**：服务端 trace（检索 / 生成 / 抽取 / 核验四步 + 每步耗时与状态）→ 逐句引用映射（句级命中 6/9）→ 条级核验（真比对法源条文，能报出「第 83 条未在已收录的 17 条条文中找到」）→ 忠实度标记。
- **上下文注入**：助手自动带上「当前案件 + 当前合同审查 / 文书草稿」，正文由后端按 id 读取并做归属校验（非本人需案件访问权，越权按不存在处理）。
- **前端包体治理**：入口 JS 145.6 → 102.6 KB raw（gzip 50.0 → 36.0 KB），最大页面 chunk 121.8 KB，总资产 1580.3/1600 KB，四项预算全绿（根因：element-plus 桶导入 + 零使用全局注册 + 残留栅格 CSS）。
- **质量门禁**：后端相关子集 588 项通过、前端单测 74 项、e2e 48 passed / 5 skipped（含 5 条真实后端集成用例：系统页 15 tab、trace 契约、引用弹窗 3 条）。

## 6. 可能被追问的问题（Q&A 预案）

| 问题 | 回答要点 |
| --- | --- |
| 「这些指标是不是自己写死的？」 | 全部来自 `eval/outputs/*.json` 评测产物，`scripts/demo_metrics.py` 现读现打印；规则层是确定性计算，裁判层可换模型复跑。 |
| 「忠实度 100% 可信吗？」 | 有两点如实说明：① 单模型自评有同源偏差，所以做了跨模型裁判（分歧 0）；② 人工抽检 25 条已由本人逐条判定，AI 裁判与人工**同向率 0.88（22/25，95% CI [0.700, 0.958]）**，3 条分歧恰好暴露裁判口径的薄弱面（完整性偏严、因果对应偏宽）。 |
| 「为什么延迟 50 秒？」 | 多跳检索 + 长上下文生成；p50 17s、p95 52s 是真实分布。生产上会做流式与缓存（面板已是流式占位 + 阶段提示）。 |
| 「trace 是前端编的吗？」 | 不是：`trace_json` 落库（`legal_consultations` 表），REST 详情与列表返回同一份；有契约测试 `frontend/tests/e2e/verify-trace-real.spec.js` 断言列表与详情一致。 |
| 「模型用的是哪家？」 | 对话/推理走商汤 `glm-5.2`（免费额度），嵌入走 dashscope `text-embedding-v3`，裁判走 `qwen-plus`（跨模型）。 |
| 「还有什么没做完？」 | 30/90 天项已全部收口（三层评审 0.88、入口收敛、逐句溯源、包体四项全绿、网关加固、文书/合同上下文、5 分钟 demo、baseline 快照、混合检索消融）；剩余如实项：人工抽检样本 25/66 偏小、未覆盖咨询主链路、BM25 回退路径偶发慢、LLM 网关长尾继续观察。 |
