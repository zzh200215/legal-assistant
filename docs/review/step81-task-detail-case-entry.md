# 第八十一步交付说明：任务详情面板加案件入口

> 一句话：任务详情对话框新增「所属案件」一行，显示 `case_title` 并提供「打开案件」按钮，直达 `/legal-workspace?case_id=N`；不在列表中的深链任务（`GET /tasks/{id}`）同样带标题。

- 日期：2026-10-30
- 前端：`components/tasks/TaskDetailDialog.vue`、`views/Tasks.vue`
- 后端：无接口变更（复用 step80 的 `case_title`；补一条详情接口断言）
- 测试：共享/任务单测 18 passed；任务 e2e 4 passed；前端单测 96 passed
- 无迁移；OpenAPI **370**（路径与 schema 均不变）

---

## 1. 为什么补

step79/step80 让任务带上 `case_id` 并显示案件名，但任务详情面板里看不到案件：

- 从列表点进详情后，案件信息消失，只剩来源标签；
- 用户想核对案件上下文，只能关掉对话框回列表找；
- 咨询跟进生成的任务已经带案件，却没有下钻入口。

## 2. 前端

`TaskDetailDialog.vue`：

- 描述区新增「所属案件」，紧跟在标题之后（跨两列）；
- 有 `case_id` 时显示 `case_title`（缺失则回退 `案件 #id`），右侧「打开案件」文字按钮；
- 无 `case_id` 时显示「未关联案件」；
- 新增 `open-case` 事件，不直接依赖 router，保持组件可复用。

`Tasks.vue`：

```js
const openCase = (task) => {
  if (!task?.case_id) return
  router.push({ path: '/legal-workspace', query: { case_id: String(task.case_id) } })
}
```

打开详情时 URL 已写入 `taskId`，浏览器后退会回到同一任务并重新打开详情。

## 3. 验证

后端（`test_legal_workspace_case_sharing.py`）：

- 列表 `case_title == "共享案件"`（step80 已有）；
- 新增：`GET /tasks/{id}` 返回 `case_id` 与 `case_title`，深链打开详情也能显示入口。

任务 e2e（`tasks-flow.spec.js` 案件用例）：

- `/tasks?case_id=7` 点开「案件专属任务」→ 详情显示「所属案件 / 试点案件」；
- 点「打开案件」→ URL 变为 `/legal-workspace?case_id=7`；
- 整页切回任务页后「仅看案件 #7 ×」清除过滤仍正常。

| 项 | 结果 |
| --- | --- |
| 共享/任务单测 | 18 passed |
| 任务 e2e | 4 passed |
| 前端单测 | 96 passed |
| 迁移 | 无（head 仍 `20261030_0114`，115 个） |
| OpenAPI | 370（无变更） |
| 包体 | 入口 81.1 / 100，最大页面 chunk 85.3 / 130，总资产 **1686.7 / 1700**（余 13.3 KB） |

## 4. 门禁

- `scripts/check_all.py`：**10/10，16.8 分钟，exit 0**（本树最终状态）。
- 后端全量：**2473 passed / 4 skipped**；OpenAPI **370**；前端单测 **96 passed**。
- 包体：入口 **81.1 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1686.7 / 1700 KB**。

## 5. 边界

1. **入口只做跳转**：不带 `tab` 参数，落到案件工作台默认概览；需要任务 tab 时可从案件页再进。
2. **不做能力判断**：`/legal-workspace` 有 `WORKSPACE_MANAGE` 路由门禁，无该能力的用户会看到 403 状态页；与通知中心现有的案件跳转行为一致，后续可统一收口。
3. **标题来自任务载荷**：详情不额外拉案件详情，案件改名后需刷新任务列表/详情才更新。
4. **已删/无权案件**：`case_id` 存在但 `case_title` 为空时显示 `案件 #id`，点击后由案件页自行提示无权限或不存在。
5. **无案件任务**：显示「未关联案件」，不渲染按钮，避免死链。
6. **只有任务详情有入口**：看板卡片、表格行未加案件跳转，避免与卡片点击（开详情）冲突。
7. **深链同样生效**：`GET /tasks/{id}` 已补 `case_title`，`/tasks?taskId=N` 打开详情能看到案件名。
8. **无迁移/无接口变更**：纯前端接线 + 一条后端断言。

## 6. 后续建议

- 历史任务回填 `case_id` 后再统一显示；
- 若产品允许，从任务详情跳到案件的「任务」tab（`tab=tasks`）并预置筛选；
- 包体余量 13.3 KB，下一步优先做产物治理再叠加 UI。
