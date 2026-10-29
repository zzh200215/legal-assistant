# 第六十五步交付说明：导出保留配置化 + 定时清理任务

> 一句话：把「90 天 / 20 份」从我写死的常量改成 settings 配置（`LEGAL_CONSULTATION_EXPORT_*`），新增 Celery beat 任务 `prune_legal_consultation_exports` 每天 03:23 全表清理；前端摘要改为读接口返回的 `retention_days/max_records`，不再硬编码。

- 日期：2026-10-29
- 后端：`core/config/reliability.py`、`services/legal/consultation_export.py`、`tasks/legal_tasks.py`、`core/celery_app.py`
- 前端：`components/legal/LegalConsultationsTab.vue`
- 测试：后端 17 passed（含 prune_all + beat 注册）；e2e 1 passed
- 无新迁移；OpenAPI 路径仍 **364**

---

## 1. 为什么补

step64 的保留策略有两个运维问题：

- 90/20 写死在代码里，不同部署想改只能改代码重发；
- 清理只在“有人导出/打开列表”时触发，长期无人访问的记录不会到期删除。

这一轮把它变成“可配置 + 每天定时兜底”。

## 2. 配置

`app/core/config/reliability.py`：

```python
LEGAL_CONSULTATION_EXPORT_RETENTION_DAYS: int = Field(default=90, ge=7, le=3650)
LEGAL_CONSULTATION_EXPORT_MAX_PER_CONSULTATION: int = Field(default=20, ge=1, le=200)
```

- 仍可通过环境变量覆盖；
- `export_retention_limits()` 统一读取，导出/列表/任务三处口径一致；
- 前端不再写死 90/20，读列表响应里的 `retention_days/max_records`。

## 3. 定时任务

`app/tasks/legal_tasks.py`：

```python
@celery_app.task(name="prune_legal_consultation_exports")
@_beat_lock(task_name="prune_legal_consultation_exports", ttl_seconds=3600)
def prune_legal_consultation_exports_task():
    result = prune_all_consultation_exports(db)
    db.commit()
    return result
```

- beat 每天 **03:23** 触发（`crontab(minute=23, hour=3)`）；
- `prune_all_consultation_exports()`：
  1. 全表删除超过保留天数的记录；
  2. 按 `consultation_id` 分组，对超过份数上限的咨询删掉旧记录；
  3. 返回 `deleted / consultations_over_limit / retention_days / max_records`。
- 写入/列表仍保留 step64 的即时清理，定时任务是兜底，不是替代。

## 4. 前端

- 摘要从「保留 90 天 · 最多 20 条」改为 `保留 {{ exportRetentionDays }} 天 · 最多 {{ exportMaxRecords }} 条`；
- 首次加载列表时从响应更新；配置改了不需要前端发版；
- 其余分页/下载逻辑不变。

![导出记录：保留策略由接口驱动](docs/review/step63-consult-export-history.png)

## 5. 验证

后端：

- `prune_all_consultation_exports`：25 条 + 1 条 120 天前旧记录 → 清理后 20 条、旧记录不在；
- 返回的 `retention_days/max_records` 与传入一致；
- beat schedule 注册 `prune-legal-consultation-exports` → task 名正确；
- 原 API 分页/保留测试继续通过。

e2e：

- mock 列表返回 `retention_days=90/max_records=20`；
- 摘要显示「保留 90 天 · 最多 20 条」；
- 展开/下载/分页断言保持。

| 项 | 结果 |
| --- | --- |
| 后端单测 | 17 passed（导出/保留/任务） |
| e2e | 1 passed / 17.0s |
| 构建 | 入口 80.6 / 100；最大页面 chunk 85.3 / 130；总资产 **1684.6 / 1700**（余 15.4 KB） |
| 门禁 | 见 §6 |

## 6. 门禁

- `scripts/check_all.py`：**10/10，16.3 分钟，exit 0**（本树最终状态）。
- 后端全量：**2467 passed / 4 skipped**；OpenAPI **364**；前端单测 **96 passed**。
- 包体：入口 **80.6 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1684.6 / 1700 KB**。

## 7. 边界

1. **仍是全局配置，不是组织级**：所有组织共用一组环境变量；要做组织级得加配置表/管理界面。
2. **定时任务依赖 beat 进程**：beat 没跑/挂了，只剩写入/列表的即时清理；不会自动补跑。
3. **硬删除**：到期/超量直接删，无归档、无回收站、无删除审计。
4. **全表扫描分组**：`GROUP BY consultation_id HAVING count > N` 在导出记录量很大时需要索引支撑；当前有 `consultation_id` 索引，但没做分批/限流。
5. **任务无 dry-run**：直接删；要演练只能改配置或手动调用 service 传参。
6. **配置校验范围**：天数 7–3650、份数 1–200；超过范围启动即报错。
7. **前端只在拿到列表响应后更新摘要**：没展开记录时仍显示默认 90/20；配置不同且用户没展开，会短暂显示默认值。
8. **无新迁移/接口路径**：只改配置读取与任务注册。

## 8. 后续建议

- 组织级保留配置 + 管理界面；
- 任务分批/限流与删除审计；
- 包体余量 15.4 KB，继续加前端功能前先量产物。
