# 第八十四步交付说明：任务来源中文化 + 查看来源咨询 + 案件任务标题

> 一句话：任务来源标签统一走中文映射（咨询/会议/合同审查不再露原始枚举），详情面板补「查看来源咨询」直达咨询结果；任务中心按案件筛选时，chip 与空态显示案件名，取不到再回退 `#id`。

- 日期：2026-10-31
- 前端：`utils/taskSource.js`、`components/tasks/TaskDetailDialog.vue`、`views/Tasks.vue`
- 测试：任务 e2e **5 passed**；前端单测 **100 passed**（新增 4 条来源映射用例）
- 后端/接口/迁移：无改动；OpenAPI 仍 **370**
- 包体：总资产 **1687.9 / 1700 KB**（余 12.1 KB）

---

## 1. 来源标签中文化

新增 `utils/taskSource.js`，任务中心看板/表格与详情面板共用同一张表：

| 枚举 | 标签 |
| --- | --- |
| `document` | 文档 |
| `chat` | 聊天 |
| `meeting` | 会议 |
| `decompose` | 拆解 |
| `legal_consultation` | 咨询 |
| `legal_review` / `contract_review` | 合同审查 |

未知枚举**保留原值**，不吞信息；空值返回空串，不渲染占位标签。

之前两处各有一份 `sourceTypeLabelMap`，都缺 `legal_consultation`/`meeting`，跟进任务在列表里直接显示 `legal_consultation`。现在统一为一个模块，避免再次漂移。

## 2. 查看来源咨询

- 详情「来源」行显示 `咨询 #77`；
- `source_type=legal_consultation` 且 `source_id`、`case_id` 都在时，动作区出现「查看来源咨询」；
- 点击跳 `/legal-workspace?case_id=7&tab=consultation&consultation_id=77`，案件工作台按 `consultation_id` 恢复结果卡（该深链是既有能力，step57/66 已在用）；
- 文档来源链接同时带上 `case_id`（有案件时），文档页可显示案件面包屑与「返回案件」。

## 3. 案件任务视图标题

- 进入 `/tasks?case_id=7` 时请求 `GET /legal/matters/7/summary`；
- chip 从「仅看案件 #7」变成「仅看案件 试点案件」；
- 空态从「案件 #7 暂无任务」变成「案件「试点案件」暂无任务」；
- 请求失败（404/403/网络）静默回退 `#7`，不弹错、不阻塞任务列表；
- 切换/清除 `case_id` 时同步刷新/清空标题，并用当前 case_id 校验异步返回，避免串案件。

## 4. 验证

任务 e2e：

- 案件用例：chip 显示「仅看案件 试点案件 ×」，`/legal/matters/7/summary` 被请求；清除筛选后 chip 消失；
- 新增来源用例：
  - 看板任务卡显示「咨询」「会议」，不再出现 `legal_consultation` / `meeting` 原始值；
  - 详情显示 `咨询 #77` 与「查看来源咨询」；
  - 点击后 URL 带 `case_id=7`、`tab=consultation`、`consultation_id=77`。

前端单测：

- 已知枚举 → 中文；
- 未知枚举 → 原值；
- 空值 → 空串；
- 映射表冻结（防止调用方就地改坏）。

| 项 | 结果 |
| --- | --- |
| 任务 e2e | 5 passed |
| 前端单测 | 100 passed |
| OpenAPI | 370（无变更） |
| 迁移 | 无（head 仍 `20261030_0114`，115 个） |
| 包体 | 入口 81.1 / 100（gzip 28.1 / 35），最大页面 chunk 85.3 / 130，总资产 **1687.9 / 1700** |

## 5. 门禁

- `scripts/check_all.py`：**10/10，13.6 分钟，exit 0**（本树最终状态）。
- 后端全量：**2482 passed / 4 skipped**；OpenAPI **370**；前端单测 **100 passed**。
- 包体：入口 **81.1 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1687.9 / 1700 KB**。

## 6. 边界

1. **咨询入口依赖案件**：`legal_consultation` 任务没有 `case_id` 时只显示 `咨询 #id`，不出按钮（无法安全深链）。
2. **标签只是显示层**：后端仍返回原始枚举；未知枚举按原值显示，不做猜测翻译。
3. **`legal_review` 语义来自任务**：演示种子数据里它的 `source_id` 实际指向文档；标签按「合同审查」显示，不据此回填案件。
4. **案件名是额外请求**：有 `case_id` 才请求 summary；没有缓存，案件改名后需刷新任务页才更新。
5. **失败静默回退**：summary 403/404/网络错误都回退 `#id`，不打断任务列表，也不提示「案件不存在」。
6. **文档链接行为变化**：有 `case_id` 时文档页会多带案件上下文（面包屑/返回案件），无案件时与之前一致。
7. **无后端改动**：深链与 summary 都是既有接口，不新增路径、不改 schema。
8. **包体余量 12.1 KB**：总资产已用 99%，继续加 UI 前先量产物。

## 7. 后续建议

- 时间允许时把 `getMatterSummary` 接进 `useQuery` 缓存，避免每次进案件任务视图都请求；
- 详情「来源」行可给 `legal_consultation` 加「已归入案件」小标签；
- 剩余包体治理优先于新增页面级功能。
