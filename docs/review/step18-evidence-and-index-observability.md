# step18：证据可核验性的趋势/语料缺口 + 检索索引可观测

> 承接 `docs/review/step17-evidence-verifiability.md`（在线指标本体）与 `docs/review/step10-bundle-budget.md` 之后的开口项。
> 两项都来自用户在同一次多选里的选择（A 证据可核验性做深、B BM25 索引可观测）。

## 0 这一轮解决什么 / 不解决什么

**做**

- **A**：把「证据可核验性」从一张口径分布表扩成可判断运营动作的视图——**按天趋势**（指标覆盖面在动吗）+ **法源引用榜与语料缺口**（该补哪部法）+ 未挂法源引用的计数。
- **B**：给内存 BM25 索引加**只读运行计数**（构建 / 失败 / 陈旧丢弃 / TTL 重建 / 回退扫描 / 最近构建耗时），并在系统页「健康检查」tab 出卡片。BM25 那条偶发 `AttributeError` 已在提交 `2b4bf78` 修掉（构建改为「局部攒好、锁内一次性发布」，打分前把索引快照到局部变量），本轮补的是**它还会不会再发生**的观测手段。

**不做**

- 不做历史回溯：老咨询记录没有 `evidence_json` 就不进比率分母（口径与 step17 一致）。
- 不做跨进程聚合：BM25 计数是**进程内**状态，多 worker 部署时每个 worker 各一份（如实标注在卡片说明里）。
- 不改检索行为本身：计数只读，不参与召回或排序决策。

## 1 交付物

| 层 | 文件 | 说明 |
| --- | --- | --- |
| 聚合 | `app/services/observability/consultation_evidence.py` | 新增 `trend`（按天：答复数 / 带指标 / 四档计数 / 条文核对分子分母）、`sources`（法源引用榜：被引用数 / 涉及答复 / 条文数 / 核验通过 / `articles == 0` 即缺口）、`sources_total`、`corpus_gaps`、`unmatched_references` |
| 接口 | `app/api/admin/analytics_api.py` | 既有 `/analytics/consultations/evidence-stats` 扩字段；新增 `GET /analytics/retrieval-index/stats`（管理员） |
| 计数 | `app/services/rag/rag_service.py`、`app/services/rag/retrieval.py` | `_bm25_stats` 字典 + `bm25_stats()` 只读访问器；构建/失败/陈旧丢弃/TTL 重建/回退扫描各自计数 |
| 前端 | `frontend/src/components/system/SystemExperimentsTab.vue` | 证据卡新增「按天趋势」与「法源引用与语料缺口」两个二级分区（不另起卡片） |
| 前端 | `frontend/src/components/system/SystemHealthTab.vue` | 新增「检索索引」卡片（四个读数 + 一行结论） |
| 前端 | `frontend/src/composables/useSystemObservability.js`、`frontend/src/api/analytics.js` | `indexStats` / `fetchIndexStats` / `retrievalIndexStats()`；`emptyEvidence()` 补新字段 |
| 测试 | `tests/test_consultation_evidence_stats.py`、`tests/test_bm25_recall.py` | 趋势与法源榜断言；5 例计数断言（构建成功 / 建不起来 / 未装依赖 / TTL 重建 / JSON 可序列化） |

## 2 真机读数（2026-10-19，demo 库）

**证据可核验性（近 30 天）**

- 51 条咨询答复，其中 **3 条带指标**（覆盖面 5.9%），48 条是加指标之前的老记录（单列，不摊进分母）。
- 口径分布：逐条核对 1 / 部分核对 1 / 待核 1 / 未引用 0，各 33%。
- 逐条核对通过率 **100%（2/2 条引用核到条文）**。
- 趋势：`2026-10-09` 1 条、`10-16` 1 条、`10-18` 26 条（0 条带指标）、`10-19` 21 条（3 条带指标，1/1/1 三档）。
- 法源引用榜：窗口内 **12 个法源**被引用（表内列前 10）。头三名：《劳动合同法》29 次引用 / 98 条条文 / 9 次核验通过；《劳动争议调解仲裁法》21 次 / 54 条 / 3 次；《劳动合同法（演示依据）》6 次 / 98 条 / 1 次。
- 语料缺口：**2 个**法源（均为「服务合同审查要点摘要（演示模板）」，`template` 类，本就不该有条文）共 3 条引用。
- 未挂法源库的引用：**8 条**（涉及 5 条答复），单独计数、不进上表。

**检索索引（同一次真实文档问答前后）**

- 调用前：`builds 0 / index_size 0 / fallback_scans 0`。
- 跑一次文档 RAG 问答后：`builds 2 / build_failures 0 / stale_discarded 0 / ttl_rebuilds 0 / fallback_scans 2 / last_build_ms 2.26 / index_size 0`。
- `index_size 0` 是**如实读数**：本地 demo 文档没有进入该 Chroma collection，索引建了但没有语料，于是每次召回都走 jieba 全表扫描（`fallback_scans` 随之增长）。卡片把这一层因果关系写在说明里，而不是只显示一个「正常」。

截图：`docs/review/step18-evidence-admin.png`（证据卡全貌）、`docs/review/step18-index-card.png`（检索索引卡）。

## 3 验收

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 后端计数与聚合 | `pytest tests/test_bm25_recall.py tests/test_consultation_evidence_stats.py -q` | **18 passed** |
| 真机接口 | 登录 admin → `GET /analytics/consultations/evidence-stats?days=30` / `GET /analytics/retrieval-index/stats` | 返回上文读数，无 5xx |
| 真机界面 | `npx playwright test zz-ab-shot`（临时用例，真后端） | 2 passed；卡片数值与接口逐字一致，无 `#runtime-error-panel` |
| 前端静态 | `npx eslint src --ext .vue,.js` / `npm run test:unit` | 0 error / 10 warning；83 passed |

## 4 关键决策

1. **趋势按「天」而不是按「小时」**：指标刚上线，样本以天为单位才有意义；且后端按 `date(created_at)` 分组，不做时区换算（与既有统计口径一致）。
2. **法源榜按「被引用次数」排序并限 10 条**：运营动作是「补哪部法」，引用次数是最直接的代理指标；被截断时在标题里明说「下表只列引用最多的 10 个」。
3. **缺口判定用 `articles == 0` 而不是 `no_corpus` 计数**：`no_corpus` 只统计「引用里真的带了条号、但法源没有条文」的情形；模板类法源往往引用时就没写条号，用 `articles` 才能把它们捞出来。
4. **BM25 计数只读、不参与决策**：避免观测代码改变检索行为；`bm25_stats()` 只读 `_bm25_stats` 与三个既有状态位。
5. **索引卡片的结论行写因果**：「已发生 N 次回退扫描；若同时有构建失败，说明索引没能建立，检索退化为全表扫描」——把「数字」翻译成「所以会怎样」，否则运维看到 `fallback_scans > 0` 无法判断严重性。

## 5 如实说明（边界）

- 覆盖面 5.9% 低，是因为 `evidence_json` 是 2026-10-19 才加的列，**老记录不回溯**；这张表要看的是趋势斜率，不是绝对值。
- 法源榜里的「核验通过」是**条号级**核对（引用条号在已收录条文里存在），不是全文语义核对（同 step17 边界）。
- BM25 计数是进程内状态：多 worker 或多实例部署时，看到的是**单个进程**的计数，重启即清零；卡片说明里已注明「进程内共享」。
- 本地 demo 库的向量集合为空（文档未入库），所以索引条目为 0、回退扫描增长；这反映的是**本地数据现状**，不是索引逻辑缺陷。
- 未挂法源库的 8 条引用没有进法源榜，只在脚注计数——它们不是「语料缺口」，而是「引用没带 `source_id`」。

## 6 下一步

- 让 demo 文档真正入库（`document_pipeline` 索引），使索引条目与回退扫描回到「有语料」的常态读数。
- 把「语料缺口」做成可点击清单，直接跳到法律资料页对应法源（当前只在脚注列出标题）。
- 若后续要按组织维度看证据可核验性，把 `organization_id` 过滤补进趋势与法源榜（现在只有总量口径按组织过滤）。
