# 步骤 9：逐句溯源（服务端句级引用映射 + 条级核验）

日期：2026-10-18。对应诊断文档 30 天项 ①「copilot 四层体验」里最后一处如实降级——② 逐句溯源退化成整篇答案粒度。

## 0. 做了什么 / 不做什么

**做了什么**

- 后端在咨询**写入时**计算「答案句 → 引用条目」映射并落库（新列 `legal_consultations.attribution_json`，迁移 `alembic/versions/20261018_0098_consultation_attribution.py`），随 REST 以 `attribution` 字段返回。
- 引用核验从「条」级落到实处：解析引用条号（阿拉伯数字 + 中文数字），按 `legal_articles` 里**实际收录的条文**核对，`verification.verified` 现在对「条号对不对」表态；答案正文里出现的条号会并入该引用的核验范围。
- 引用 → 法源匹配放宽（标题归一化相等 → 包含 → 条号交集 + 标题亲和），修掉「模型给出形状 A 引用（无 `source_id`）时一条都匹配不上」的真缺陷。
- 同一次咨询内的重复引用去重（模型常把同一法源写两遍）。
- 前端面板优先使用服务端句级映射：点某一句只高亮该句真正的依据；服务端声明了句级但与本地分句对不齐时，回退整篇粒度并说明原因（不把句子指到错的证据）。
- 顺带修掉 E 项（LLM 网关偶发秒级失败）的根因：`ModelGateway` 的连接池跨事件循环复用（见 §3 决策 8）。

**不做什么**

- 不做语义/向量级句句匹配（当前是词面重叠打分），不为这一层引入 embedding 依赖。
- 不改 LLM 提示词让它自己输出句级引用映射（成本与稳定性风险高于收益）。
- 不做句级忠实度标记（忠实度仍是整篇粒度 + 引用核验）。

## 1. 交付物

| 文件 | 作用 |
| --- | --- |
| `app/services/legal/legal_service.py` | `split_answer_sentences()` / `attribute_sentences_to_references()` / `_reference_source_text()` / `_sentence_citation_numbers()` / `match_reference_to_source()` / `_dedupe_references()`；`consultation_payload(..., trace_out=None, attribution_out=None)` 两条返回路径都写入 attribution |
| `app/services/legal/legal_reference_service.py` | `parse_citation_numbers()`（阿拉伯 + 中文数字归一）、`_article_verification()`（按已收录条文核对并给出 basis）、`enrich_references(db, refs, *, sentence_numbers=None)` |
| `app/models/legal.py` | `LegalConsultation.attribution_json`（`Text`，JSON） |
| `alembic/versions/20261018_0098_consultation_attribution.py` | 加列迁移（`revision 20261018_0098`，`down_revision 20261018_0097`） |
| `app/services/legal/legal_workspace_service.py` | 写入 `attribution_json`；`serialize_workspace_row` 咨询分支返回 `attribution` |
| `app/core/llm_client.py` | 连接池按事件循环隔离（`_clients: dict[loop, client]`），`close()` 跳过已关闭循环的客户端 |
| `frontend/src/composables/copilotLayers.js` | `normalizeServerAttribution(raw, sentences, evidence)`；`deriveAttribution` 优先服务端句级映射，否则回退整篇粒度 |
| `frontend/src/components/copilot/CopilotAnswer.vue` | `highlightedKeys`（句级只高亮本句依据）、`activeSentenceNote`（含「本次没有匹配到依据」） |
| `tests/test_legal_attribution.py` | 13 例：分句、匹配/留空、无 `source_id` 回退、越界与上限、payload 写入、序列化三态 |
| `tests/test_legal_reference_verification.py` | 条级核验、中文数字条号、正文条号并入、去重等用例 |
| `tests/test_llm_client_compat.py` | 3 例连接池按事件循环隔离（同循环复用 / 新循环新建 / close 跳过已关循环） |
| `frontend/tests/e2e/copilot-dock.spec.js` | 新增 2 例：句级映射只高亮本句依据、对不齐回退整篇粒度 |
| `docs/review/step9-attribution-panel.png` / `step9-attribution-sentence.png` | 真机实拍（面板四层 + 点句高亮） |

## 2. 数据形状（真机读回，逐字）

```json
{
  "granularity": "sentence",
  "attribution_version": 1,
  "method": "lexical_overlap",
  "min_score": 0.34,
  "total_sentences": 9,
  "matched_sentences": 7,
  "sentences": [
    {"index": 0, "text": "根据《中华人民共和国劳动合同法》第十九条规定……", "matches": [
      {"reference_index": 0, "source_id": 72, "title": "《中华人民共和国劳动合同法》", "score": 0.94, "shared_terms": ["试用期", "劳动合同"]}
    ]}
  ]
}
```

核验步（trace 内）与引用字段：

```
核验引用 完成 2ms 0 条已核验 · 1 条未核验
references[0].verification = {verified: false, verification_basis: "引用的第83条未在已收录的 17 条条文中找到（可能是该条未收录，也可能引用有误）"}
```

## 3. 关键决策

1. **句级映射在写入时算**：`serialize_workspace_row` 是纯序列化（无 db 参数），不能在读时算；写入时算一次、读时直出，与 `trace_json` 同模式（`attribution_out` 出口参数）。
2. **打分 = 本句与法源文本的 jieba 词交集 / 本句词数**，阈值 `ATTRIBUTION_MIN_SCORE = 0.34`，每句最多 `ATTRIBUTION_MAX_MATCHES = 2` 条依据；低于阈值就如实留空，不硬凑。
3. **前端必须校验对齐**：句数相等 + 逐句文本与本地分句一致 + `reference_index` 在范围内，任一不满足就回退整篇粒度（并区分「未返回」与「与本地分句对不齐」两种说明）。
4. **条级核验用「条号解析 + 条文存在性」，不用 LLM 判定**：可复现、可解释、零成本。
5. **答案正文里的条号并入核验范围**：模型常在正文写「根据第八十二条规定」而引用字段只给「劳动合同法」简称，只核引用字段会漏。
6. **匹配放宽三级**：归一化标题相等 → 标题互相包含（≥4 字）→ 条号交集 + 标题亲和（≥3 字）；形状 A 引用因此能落到法源上。
7. **缺失条号的文案区分两种可能**：「未在已收录的 N 条条文中找到（可能是该条未收录，也可能引用有误）」——不把语料覆盖不足说成引用错误。
8. **连接池按事件循环隔离**（E 项根因）：临时循环（如 `asyncio.run`）关闭后，池里的 keep-alive 连接被主循环复用时抛 `RuntimeError: Event loop is closed`，表现为「第二次咨询直接降级为规则兜底」。改为每循环一个 client，`close()` 跳过已关闭循环的客户端。

## 4. 验收

| 项 | 结果 |
| --- | --- |
| 后端单测（引用/溯源/域服务等） | `tests/test_legal_reference_verification.py` + `tests/test_legal_attribution.py` → **50 passed**；含溯源与域服务的子集 → **75 passed** |
| 连接池回归 | `tests/test_llm_client_compat.py` → **4 passed** |
| 前端单测 | **71 passed**（新增 5 例句级映射） |
| 前端 lint | **0 error / 13 warning**（均既有 `vue/attributes-order`） |
| 前端构建 | exit 0（入口 `dist/assets/index-BNNoVk1N.js` 146.21 kB raw / 51.16 kB gzip） |
| copilot e2e | `npx playwright test copilot-dock --workers=1` → **13 passed**（含 2 条新增句级用例） |
| 全套 e2e | 不带集成开关 **45 passed / 5 skipped / 0 failed**（2.2m）；带 `E2E_RUN_INTEGRATION=true` **50 passed / 0 failed**（2.7m，含 5 条真后端用例：系统页 15 tab、trace 读回、U-2/U-4 ×3） |
| 真机（demo_reviewer / 案件 #4，consultation 36） | trace 4 步（`生成建议 完成 16.9s`、`核验引用 完成 2ms 0 条已核验 · 1 条未核验`）；句级映射 **7/9 句**匹配；点第 2 句只高亮 1 张证据卡；`attribution` 与本地分句完全对齐 |

## 5. 开口项与边界

1. **语料覆盖是硬边界**：demo 法源《中华人民共和国劳动合同法》（`source_id=72`）只收录 **17 条**（第 1、2、3、7、10、19、36、37、38、39、40、42、46、47、48、85、87 条）。模型引用的第 83 条在真法里存在，但库里没有 → 显示「未核验」。要消除这种「合法引用被判未核验」，需扩语料（当前仓库自带语料文件只有这 17 条）。复现环境的两条命令：导入语料 `.\.venv\Scripts\python.exe -B scripts\import_legal_sources.py --data scripts/legal_corpus/劳动合同法.json --user-id 37`（法源按用户隔离，`legal_sources.user_id` 必须与登录账号一致）；重置演示账号配额 `update quota_usages set consultation_count = 0 where user_id = 37`。
2. **句级映射是词面重叠，不是语义匹配**：本次 2/9 句没匹配到依据（多为「综上/建议」类总结句），转述式表达（用词与法条差异大）也可能匹配不上。
3. **追问链路未埋点**：`create_consultation_followup` 不走 `consultation_payload`，trace 与 attribution 均为空（前端自动回退「事后回放 + 整篇粒度」）。
4. **老记录不回填**：迁移只加列，历史咨询的 `trace_json` / `attribution_json` 保持 NULL。
5. **E 项根因已修但需观察**：跨循环复用已消除；网关侧的超时/限流（长响应 16.9s、偶发秒级失败）仍属外部依赖风险。
6. 未做句级忠实度；面板不展示 `metrics`（机读计数已在 trace 内）。

## 6. 下一步

- A 三层评审：~~25 条人工结论待填~~ **已收口（2026-10-19）**：用户逐条判定 25/25（24 正确 / 1 错误），AI 裁判与人工一致率 **0.88（22/25，95% CI [0.700, 0.958]）** ≥0.8，3 条不同向见 `docs/review/step13-three-layer-review.md` §3。
- D bundle 预算：~~`npm run bundle:check` 三项红线（入口 raw 146.2/120、gzip 51.2/42、总资产）待决策（降体积 / 重标定 / 暂缓）~~ **已解决（2026-10-18，step10）**：入口 raw 102.0/120、gzip 35.8/42、最大页面 chunk 121.8/130、总资产 1579.0/1600，四项全绿；红线未放宽。详见 `docs/review/step10-bundle-budget.md`。
- E 观察：真实咨询连跑若干次，确认不再出现「第二次降级」。
