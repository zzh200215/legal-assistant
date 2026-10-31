# 第八十三步交付说明：历史任务 case_id 回填与来源继承

> 一句话：新增可 dry-run 的历史任务回填脚本，按「咨询 → 案件、文档 metadata → 案件、拆解父任务 → 案件」三条链路补 `case_id`；同时让新产生的文档任务/拆解子任务自动继承案件，并把任务里的 `case_title` 改为按案件访问权过滤。

- 日期：2026-10-31
- 后端：`services/jobs/task_case_backfill.py`、`services/documents/case_link.py`、`services/jobs/task_service.py`、`api/tasks/task_api.py`
- 脚本：`scripts/backfill_task_case_id.py`
- 测试：新增回填 5 条 + 任务服务 2 条 + 案件可见性 1 条；相关子集 192 passed
- 无迁移；OpenAPI **370**（纯内部改动，无 schema 变化）
- 前端无改动，包体维持 **1687.0 / 1700 KB**

---

## 1. 为什么补

step79 才加 `tasks.case_id`，step80–82 补齐了显示、详情入口和新建链路，但历史数据仍是空的：

- 旧任务即使来自某个案件的咨询/文档，也不会出现在案件任务视图；
- 拆解出来的子任务不继承父任务案件，越拆越散；
- 文档提取任务即使文档本身挂在案件下，也不带案件；
- 一旦回填扩大 `case_id` 的覆盖，`case_title` 的原有读取逻辑会把严格案件名带给非成员。

## 2. 回填规则（只认可证明的来源）

| 规则 | 来源 | 说明 |
| --- | --- | --- |
| `consultation` | `source_type=legal_consultation` → `legal_consultations.case_id` | 咨询本身挂在案件下才补 |
| `document` | `source_type=document` → `documents.metadata_json.case_id` | 兼容 int / 数字字符串；文档无案件则跳过 |
| `parent` | 任意有 `parent_id` 的任务 → 沿父链继承 | 支持多级拆解；父链最多 12 层，环直接跳过 |

保守边界：

- 只处理 `case_id IS NULL`，**绝不覆盖**已有值；
- 案件必须存在（不做悬空引用）；
- meeting / chat / legal_review 等没有可靠案件来源的任务保持为空，不猜；
- 来源对象或父任务已删除的跳过。

## 3. 脚本

```bash
python -B scripts/backfill_task_case_id.py                  # dry-run，只出报告
python -B scripts/backfill_task_case_id.py --limit 500      # 分批扫描
python -B scripts/backfill_task_case_id.py --apply          # 写库
```

报告字段：`total_without_case / scanned / planned / applied / by_rule / skipped{no_source, source_without_case, missing_source, missing_case} / samples`。

本机开发库实测（dry-run）：

```
无 case_id 任务 6 条，本次扫描 6 条，可回填 0 条
跳过：无可靠来源 5 / 来源无案件 1 / 来源缺失 0 / 案件不存在 0
```

也就是说：**这份演示库没有可推导的历史案件任务，脚本不会改动任何行**；5 条是 meeting/无来源，1 条来源对象本身没有案件。规则有效性由种子测试数据覆盖。

## 4. 向前继承（避免问题复发）

- `decompose`：子任务写入 `case_id=parent.case_id`；
- `extract_from_document`：从 `documents.metadata_json.case_id` 继承，且校验案件存在；
- `extract_from_chat`：聊天是自由文本，没有案件上下文，继续不写 `case_id`。

## 5. 标题访问权过滤

`_attach_case_titles` 增加 `current_user`，按 `verify_case_access` 的口径批量过滤：

- 案件必须在用户所属组织（`OrganizationMember`）；
- 严格案件必须是未撤销的 `LegalCaseMember`；
- 无权限时 `case_title=null`，前端回退到 `案件 #N` / `—`，不泄露案件名。

这改变了 step80/81 对严格案件的行为（之前任何能看到任务的人都能看到案件名），属于有意的收紧。

## 6. 验证

回填单测（`tests/test_task_case_backfill.py`）：

- dry-run 只规划不写库；
- apply 后咨询/文档/多级拆解全部落到正确案件，已有值不变；
- 二次运行扫描 0、幂等；
- 来源缺失/来源无案件/案件已删/环/limit 各自计数正确。

任务服务单测：

- `decompose` 子任务继承案件；
- `extract_from_document` 继承文档 metadata 的案件，聊天任务仍为空。

案件可见性单测：

- 严格案件：组织成员能看到任务但 `case_title=null`；
- 加入 `LegalCaseMember` 后同一接口返回案件名。

| 项 | 结果 |
| --- | --- |
| 新增后端测试 | 8 passed（回填 5 / 任务服务 2 / 可见性 1） |
| 相关子集 | 192 passed |
| 迁移 | 无（head 仍 `20261030_0114`，115 个） |
| OpenAPI | 370（无变更） |
| 包体 | 无前端改动，维持 1687.0 / 1700 KB（余 13.0 KB） |

## 7. 门禁

- `scripts/check_all.py`：**10/10，13.1 分钟，exit 0**（本树最终状态）。
- 后端全量：**2482 passed / 4 skipped**；OpenAPI **370**；前端单测 **96 passed**。
- 包体：入口 **81.1 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1687.0 / 1700 KB**。

## 8. 边界

1. **不猜来源**：meeting/chat/legal_review 等没有可靠案件字段的任务保持为空；开发库因此回填 0 条。
2. **只补空值**：已有 `case_id` 永远不动；重复执行安全。
3. **父链上限 12 层**：超过按缺失来源跳过；环不会死循环。
4. **案件必须存在**：来源指向已删案件时不写，避免 SQLite 下的悬空引用。
5. **脚本不自动执行**：部署后需显式 `--apply`；建议先 dry-run 看报告再分批。
6. **全表扫描成本**：无 `case_id` 任务多时用 `--limit` 分批；写库是一个事务，失败整体回滚。
7. **文档案件来自 metadata**：`metadata_json.case_id` 是弱关联，非法 JSON/字符串数字都按无案件处理。
8. **聊天提取仍无案件**：自由文本没有可信案件来源，不通过页面筛选反推。
9. **标题过滤是收紧**：严格案件非成员现在只看到 `案件 #N`/`—`，与 `verify_case_access` 一致。
10. **无迁移/无 OpenAPI 变化**：回填是数据操作，不改表结构；前端零改动。

## 9. 后续建议

- 任务详情的来源标签补 `legal_consultation` / `legal_review` 中文名，并加「查看来源咨询」入口；
- 案件任务视图标题从 `#N` 换成案件名；
- 如需常态修复，可把回填挂到管理端一次性操作，而非只靠脚本。
