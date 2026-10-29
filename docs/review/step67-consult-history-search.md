# 第六十七步交付说明：历史咨询关键字搜索

> 一句话：历史咨询列表新增「搜索问题/答复/法源」输入框，与原有依据筛选叠加；匹配条数显示在卡片头，空结果明确提示。纯前端过滤，无新接口、无新依赖。

- 日期：2026-10-29
- 前端：`components/legal/LegalConsultationsTab.vue`、`tests/e2e/workspace-prefill.spec.js`
- 测试：e2e 1 passed；门禁见 §5
- 无后端/数据库/接口变化

---

## 1. 为什么补

历史咨询已有：

- 依据筛选（无引用/弱/部分/强）；
- 查看结果、风险/状态标签。

但列表只能按依据筛，不能按内容找。咨询多了以后：

- 想找“之前问过经济补偿的那条”只能翻；
- 想找引用过某部法源的咨询没有入口；
- 客户端已有完整列表，再加一个搜索框的成本很低。

## 2. 改法

`LegalConsultationsTab.vue`：

```js
const historyKeyword = ref('')
const filteredConsultations = computed(() => {
  let rows = consultations.value
  if (historyChainFilter.value) {
    rows = rows.filter((row) => chainLevel(row) === historyChainFilter.value)
  }
  const keyword = historyKeyword.value.trim().toLocaleLowerCase()
  if (!keyword) return rows
  return rows.filter((row) => {
    const references = Array.isArray(row.references)
      ? row.references.map((ref) => `${ref.title || ''} ${ref.citation || ''}`).join(' ')
      : ''
    return [row.question, row.advice, references]
      .some((value) => String(value || '').toLocaleLowerCase().includes(keyword))
  })
})
```

- 搜索字段：问题、答复正文、引用标题/引用文本；
- 与依据筛选是 **AND** 关系；
- 大小写折叠；
- 卡片头显示「共 N 条 · 匹配 M 条」；
- 表内空状态区分「没有符合筛选条件的咨询」与「暂无咨询记录」；
- 输入框用原生 `<input type="search">`，不引入新组件。

![历史咨询：关键字搜索 + 依据筛选](step67-consult-history-search.png)

## 3. 验证

e2e：

- mock 历史列表含问题、答复、风险、依据；
- 搜索「经济补偿」→ 1 行；
- 搜索不存在的关键字 → 显示「没有符合筛选条件的咨询」；
- 清空搜索 → 恢复 1 行；
- 原有审核记录、导出记录、复制、状态同步、带入等断言保持。

构建：

- 入口 80.6 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1687.6 / 1700 KB（余 12.4 KB）。

全量门禁见 §4。

## 4. 门禁

- `scripts/check_all.py`：**10/10，18.1 分钟，exit 0**（本树最终状态）。
- 后端全量：**2467 passed / 4 skipped**；OpenAPI **364**；前端单测 **96 passed**。
- 包体：入口 **80.6 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1687.6 / 1700 KB**。

## 5. 边界

1. **纯前端过滤**：只搜当前已加载的历史列表；后端分页/截断后，未加载的记录搜不到。
2. **字段范围固定**：问题、答复、引用标题/引用文本；不搜分类、状态、审核备注、导出的 Markdown。
3. **大小写折叠**：英文/数字忽略大小写，中文无影响；不做分词/模糊纠错。
4. **与依据筛选 AND**：搜索“经济补偿”同时选“无引用”，只返回两者都满足的记录。
5. **不改变排序**：仍按列表原顺序（后端返回顺序），不按相关度排序。
6. **无高亮**：命中词不高亮；需要高亮可复用合同台账的 HighlightText，但会增加包体。
7. **空状态文案**：有搜索词或依据筛选时统一显示「没有符合筛选条件的咨询」。
8. **无接口/数据库变化**：纯视图层。

## 6. 后续建议

- 如果历史咨询量继续增长，应做后端搜索/分页，而不是继续在前端全量过滤；
- 包体余量 12.4 KB，下一步继续加前端功能前先量产物。
