# 发布流程：Feature Flag / Canary / 可回滚迁移（阶段 6）

## 1. Feature Flag 服务（app/core/feature_flags.py）

- **能力**：运行时开关，`feature_flags.set(name, bool)` 后 `is_enabled` **立即生效、不重启**
  （进程内内存存储；线程安全；零新增依赖）。
- **初始化**：首次启动从静态配置 seed（`*_ENABLED` 布尔项，见模块 `_SEEDABLE_SETTINGS`），
  运行中可覆盖。
- **用法（灰度消费方范式）**：

  ```python
  from app.core.feature_flags import feature_flags

  FLAG = "legal-agentic-rag-v2"
  # 读取（默认关，灰度为 0% 起步 / 回滚即 false）
  if feature_flags.is_enabled(FLAG):
      return await legal_agentic_search(user)
  return await legacy_search(user)
  ```

- **切换**：`feature_flags.set(FLAG, True)`（管理脚本/REPL/灰度工具调用），
  无需发版、无需重启。测试见 `tests/test_feature_flags.py`。
- **多副本/云环境**：进程内存储不跨副本共享；如需全局灰度（按租户/百分比），
  在业务层叠加 DB/Redis 持久化（同一 `is_enabled` 接口语义，实现可替换）。

## 2. Canary 发布方案（文档为主，结合现成就绪探针）

现有健康探针：`GET /api/health/live`（进程级）、`GET /api/health/ready`（DB+Redis 依赖探针）
——可直接供编排平台做 rolling/canary 判定。**路由权重方案（文档化，不强制上云）**：

| 阶段 | 权重（新:旧） | 判定条件 | 回滚条件 |
|---|---|---|---|
| 灰度 1 | 1:99 | `/api/health/live` = ok | 错误率/延迟超阈值即整体回切旧版 |
| 灰度 2 | 25:75 | `/api/health/ready` = ok | 任一环 degraded 即暂停放量 |
| 灰度 3 | 50:50 | p95 延迟环比 +5% 内、错误率 <0.5% | 超阈值回切 |
| 全量 | 100:0 | 观察 24-48h | 迁移先建后删，随时可回滚 |

- **K8s 示例**：Service 指向两个 Deployment（old/new），
  `live/ready` 作为 pods readiness/liveness probe；发版 = 滚动调整副本比。
- **Nginx 示例**：`upstream` 两池 + `weight` 参数（1/99 → 25/75 → 100/0）。
- **组合 Feature Flag**：权重路由控制版本；`feature_flags` 控制功能放量（同版本内更细粒度）。

## 3. 可回滚迁移模板与检查清单

现状：alembic/versions 共 **84 个迁移全部含 downgrade()**、non-null 新增列普遍带
`server_default` 回填，`scripts/check_migrations.py`（CI `migration-check` job）守护：
head 唯一 / 全链可逆 / 无未收敛分叉。

### 新增迁移必须通过的两阶段检查清单

- [ ] **前向兼容（先加后删）**：新增列必须 `nullable=True`；为 API/服务新增字段用
      `server_default` 回填存量行（禁止无默认值的 NOT NULL 新列）。
- [ ] 新表必须在其 create 迁移里定义 `downgrade()` `op.drop_table(...)`。
- [ ] 列删除/约束变更：**不得在同一个迁移里先删后加同名列**；先加新列并双写，
      数据验证通过后，在**独立后续迁移**删旧列（两阶段发布）。
- [ ] 使用 `op.batch_alter_table(...)`（MySQL 重建表兼容，见既有 0078 范式）。
- [ ] 运行 `python -B scripts/check_migrations.py` 确认 head 唯一、无未收敛分叉。
- [ ] 本地 MySQL/PG 环境：`alembic upgrade head && alembic downgrade base && alembic upgrade head`
      三连可逆（SQL 层面；CI 以静态校验守护，SQLite 因 ALTER 限制不支持）。
- [ ] 每个迁移只做一件事（建表/加列/回填分开），便于按需回滚到指定 revision。

### 可回滚迁移模板（新文件加在 alembic/versions/ 沿用自动发现）

```python
"""{一句话：如 'Add X column with backfill'}

Revision ID: {YYYYMMDD}_{NNNN}
Revises: {parent_revision}
"""
from alembic import op
import sqlalchemy as sa

revision = "{YYYYMMDD}_{NNNN}"
down_revision = "{parent_revision}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("example_table") as batch_op:
        batch_op.add_column(sa.Column("x_column", sa.String(64), nullable=True,
                                      server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("example_table") as batch_op:
        batch_op.drop_column("x_column")
```

> 说明：新列 nullable + server_default（前向兼容）；downgrade drop_column 可逆。
> 若后续要收紧为 NOT NULL，请放在**第二个迁移**并先确认数据完整（两阶段）。
