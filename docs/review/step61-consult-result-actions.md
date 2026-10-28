# 第六十一步交付说明：咨询结果状态标签与一键复制

> 一句话：结果卡头部补上当前状态标签（待审核/需律师审核/律师通过等），状态同步后用户能直接看到；同时加「复制结果」，把问题、状态、结论、已知事实、参考依据拼成纯文本写入剪贴板，方便转到邮件/IM/工单。

- 日期：2026-10-28
- 前端：`components/legal/LegalConsultationsTab.vue`、`tests/e2e/workspace-prefill.spec.js`
- 测试：e2e 1 passed（含剪贴板断言）；门禁见 §5
- 无后端/数据库/API 变化

---

## 1. 为什么补这两个

step58–60 做了状态/内容一致性同步，但结果卡本身**不显示状态**：

- 同步后「提交律师审核」按钮消失了，用户不知道是“已通过”还是“已归档”；
- 结果卡只能在页面里看，想发给客户/同事/工单，需要手动选中复制；
- 复制时容易漏掉“已知事实”或“参考依据”。

所以补两件小事：

1. 头部状态标签：复用已有 `statusLabel` / `statusTagType`；
2. 一键复制：结构化纯文本，不依赖后端导出。

## 2. 改法

### 2.1 状态标签

```html
<el-tag :type="statusTagType(consultResult.status)" size="small" effect="plain">
  {{ statusLabel(consultResult.status) }}
</el-tag>
```

- `pending_review` → 待审核（warning）
- `needs_lawyer_review` → 需律师审核（danger）
- `lawyer_approved` → 律师通过（success）
- `archived` → 已归档（info）

状态同步后标签跟着变，用户不需要猜按钮为什么消失。

### 2.2 复制结果

`resultToText()` 生成：

```text
咨询问题：…
当前状态：律师通过
结论与建议：…
已知事实：2021年3月入职；2024年5月被辞退
参考依据：
劳动合同法 劳动合同法第19条
```

复制流程：

- 优先 `navigator.clipboard.writeText`；
- 不可用或权限拒绝时，退回隐藏 `textarea + document.execCommand('copy')`；
- 成功 `咨询结果已复制`，失败提示手动选择；
- 空结果不复制。

## 3. 用户看到什么

- 结果卡头部多一个状态标签；
- 状态同步后标签从「待审核」变成「律师通过」；
- 点「复制结果」得到一段可直接粘贴的纯文本；
- 复制内容包含状态，避免把已通过的结论当成待审稿发出去。

![咨询结果：状态标签、复制结果、结果已同步](step61-consult-result-actions.png)

## 4. 验证

`workspace-prefill.spec.js`：

- 页面初始化时覆盖 `navigator.clipboard.writeText`，记录 `window.__copiedConsult`；
- 状态同步后断言「律师通过」标签可见；
- 点「复制结果」；
- 断言剪贴板文本包含 `当前状态：律师通过` 与 `2021年3月入职；2024年5月被辞退`；
- 原有生成文书、合同审查带入、刷新恢复、无未处理 API 断言保持。

构建：

- 入口 80.3 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB（SystemAnalyticsTabs）；
- 总资产 1680.8 / 1700 KB（余 19.2 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，15.3 分钟，exit 0**（本树最终状态）。
- 后端全量：**2458 passed / 4 skipped**；OpenAPI **361**；前端单测 **96 passed**。
- 包体：入口 **80.3 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1680.8 / 1700 KB**。

## 6. 边界

1. **复制是纯文本**：不保留 Markdown 结构、不加引用链接，粘贴到富文本编辑器会丢格式。
2. **剪贴板受浏览器权限约束**：`navigator.clipboard` 不可用或拒绝时走 `execCommand` 兜底；两者都失败只提示手动复制。
3. **只复制结果卡字段**：问题、状态、结论、已知事实、参考依据；不含待补充事实、忠实度、证据核验明细、审核备注。
4. **参考依据只拼标题与引用文本**：不校验是否已失效，也不展开条文正文。
5. **状态标签读的是当前 `consultResult.status`**：同步后更新；未同步的旧对象仍显示旧状态（step58–60 负责触发同步）。
6. **不落库、不生成文件**：没有“导出记录”审计，只是本地剪贴板操作。
7. **无接口变化**：纯前端交互。
8. **无新增依赖**：用浏览器原生 Clipboard API + textarea 兜底。

## 7. 后续建议

- 如果要把“复制结果”变成可审计的导出，需要后端生成 Markdown/PDF 并落下载记录；
- 包体余量 19.2 KB，下一步继续做功能前先量产物。
