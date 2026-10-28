# 第六十二步交付说明：咨询结果 Markdown 导出

> 一句话：新增 `GET /legal/consultations/{id}/export/markdown`，把结果卡渲染成可归档/转发的 Markdown；前端「导出 Markdown」直接下载 `.md`，并写一条导出审计。复制适合 IM，导出适合留档。

- 日期：2026-10-28
- 后端：`services/legal/consultation_export.py`、`api/legal/legal_api.py`
- 前端：`api/legalWorkspace.js`、`components/legal/LegalConsultationsTab.vue`
- 测试：Markdown 3 个单测 + 接口 2 个（含越权）；e2e 1 passed
- 无数据库迁移；OpenAPI 路径 **362**

---

## 1. 为什么补导出

step61 的「复制结果」是纯文本，适合贴 IM/邮件，但：

- 没有文件，无法归档到案件文件夹；
- 富文本/邮件客户端会丢换行层级；
- 没有服务端审计，不知道谁导出过。

所以补一个确定性 Markdown 导出：不调用模型、不写临时文件、不新增依赖。

## 2. 后端

### 2.1 Markdown 渲染

`app/services/legal/consultation_export.py`：

- 复用 `serialize_workspace_row(row)`，口径与结果卡一致；
- 标题、问题、状态、风险、生成/更新时间、关联案件、置信度；
- 结论与建议；
- 已知事实 / 待补充事实（空时写「暂无」「无缺失」）；
- 参考依据：标题 + 引用 + 已核验/条号未找到/法源缺条文标记；
- 证据可核验性：引用数、句级覆盖、忠实度、等级；
- 结尾固定 AI 辅助声明 + `content_version` 注释，便于追溯结果版本。

### 2.2 接口

```http
GET /api/legal/consultations/{id}/export/markdown
```

- 用 `legal_workspace_read_module.get_row`，沿用案件成员/组织/严格模式权限；
- 返回 `text/markdown; charset=utf-8`，文件名 `legal_consultation_{id}.md`；
- 写审计：`legal_consultation_export_markdown`，detail 带 status；
- 不存在/越权统一 404。

## 3. 前端

结果卡操作区新增「导出 Markdown」：

```js
const { data } = await legalWorkspace.exportLegalConsultationMarkdown(id)
const url = URL.createObjectURL(data)
anchor.download = `法律咨询-${id}.md`
```

- 与「复制结果」「进入合同审查」「生成文书」并列；
- 下载成功提示「Markdown 已导出」；
- 失败提示后端 detail 或「导出失败」。

![咨询结果：复制、导出 Markdown、状态标签](step62-consult-markdown-export.png)

## 4. 验证

后端：

- `tests/test_consultation_export.py`（3）：
  - 字段齐全、参考依据带「已核验」、含 AI 声明与 content_version；
  - 空事实/空引用输出「暂无」「无缺失」，证据段仍给 0 条；
  - 未知状态/风险回退原始值。
- `tests/test_legal_workspace_case_sharing.py`（+2）：
  - 案件成员可导出，响应 `text/markdown`、文件名、正文包含问题与建议；
  - 外部组织成员 404。

e2e：

- mock `GET /legal/consultations/11/export/markdown`；
- 点「导出 Markdown」，`waitForEvent('download')`，断言文件名 `.md`；
- 原有复制、状态标签、带入、刷新恢复断言保持。

构建：

- 入口 80.4 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1681.4 / 1700 KB（余 18.6 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，15.9 分钟，exit 0**（本树最终状态）。
- 后端全量：**2463 passed / 4 skipped**；OpenAPI **362**；前端单测 **96 passed**。
- 包体：入口 **80.4 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1681.4 / 1700 KB**。

## 6. 边界

1. **只导出 Markdown**：不生成 PDF/DOCX；需要 PDF 要另接渲染器。
2. **不落盘、不建下载记录表（step62 当时）**：响应即时生成；只有审计日志。step63 已补 `legal_consultation_exports` 表保存 Markdown 快照与导出记录，可回看/重新下载。
3. **内容口径与结果卡一致**：不含待补充事实以外的审核备注、trace、逐句溯源明细；引用只列标题与引用文本。
4. **状态是导出时的快照**：导出后服务端再变，文件不会自动更新；`content_version` 注释可用于对比。
5. **权限沿用详情接口**：案件成员可导出；外部组织/严格模式非成员 404；没有另开分享链接。
6. **文件名固定**：`legal_consultation_{id}.md`；前端下载名带中文「法律咨询-{id}.md」。
7. **Markdown 不做转义**：咨询正文里的 `#`、`|` 等会按 Markdown 语法解释；适合留档，不适合直接当富文本渲染。
8. **审计不含内容**：只记录动作、对象 id、status，不复制正文，避免审计表膨胀。
9. **无数据库迁移**：纯读接口。

## 7. 后续建议

- 若要“导出记录可回看”，step63 已新增 `legal_consultation_exports` 表保存 Markdown 快照，并提供列表/重新下载接口；
- 包体余量 18.6 KB，下一步继续做功能前先量产物。
