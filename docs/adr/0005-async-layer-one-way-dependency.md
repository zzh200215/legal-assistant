# ADR-0005：异步层单向依赖业务层

- **状态**：已采纳
- **日期**：2026

## 背景

`core.celery_app` ↔ `services.analytics_service` ↔ `services.document_service` ↔
`services.operational_alert_service` ↔ `tasks` 构成 5 节点循环依赖，任务层与业务层双向耦合。

## 决策

- `app/tasks` 依赖 `services`（异步层调用业务），但 `services` **绝不** import
  `app.tasks` / `core.celery_app`。
- Celery 应用不再在模块级 `autodiscover_tasks`/`import app.tasks`；任务模块由 `app.main`
  显式导入。
- 需要触发任务的业务代码改为「延迟 import」或依赖注入。

## 理由

- 打破最高风险环，任务层成为「编排入口」而非被业务层反向依赖的枢纽。

## 后果

- 新增任务后须在 `app/main.py`（或对应入口）显式导入，否则 Celery 不会注册该任务。
- `services` 内触发任务的代码必须保持延迟 import，防止环复活。
