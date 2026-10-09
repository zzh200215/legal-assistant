# D2 实施计划：注册自动创建个人组织（P0-1 根治）

## Context

自注册用户（`POST /auth/register`、`POST /auth/register-with-code`）只建 User 不建组织，`user.organization_id` 为 None，登录后所有组织上下文操作 401"不是该组织成员"（ux-audit P0-1）。前端兜底（D1，已上线 `673cc4a`）只是止血。D2 根治：注册成功时自动创建"个人组织"，将用户加入为 admin 并回写 `user.organization_id`；同时给 Organization 加 `org_type`（personal/team）字段，为管理端区分与后续"升级为团队"留口。

范围：仅覆盖两条自服务注册路径。OAuth/LDAP（`enterprise_auth_service`，按 organization_code 匹配）、微信/小程序登录不改。overview 端点不改（回写后自然生效）。存量不做 backfill（无组织存量仅测试账号 ux_audit，直接删除即可）。

## 实施步骤（6 步）

### Step 1 迁移：`alembic/versions/20261009_0094_personal_org.py`（新建）
- `revision="20261009_0094"`, `down_revision="20261006_0093"`（当前 head），单行 docstring
- upgrade：`sa.inspect(op.get_bind())` 检查 `organizations` 是否已有 `org_type` 列（幂等守卫，照抄 0093 风格），无则 `op.add_column("organizations", sa.Column("org_type", sa.String(16), nullable=False, server_default="team"))`
- downgrade：对称 drop_column（先确认列存在）
- 存量行为：server_default 使现有行自动填 `team`，无锁表风险

### Step 2 模型：`app/models/org.py`
- `Organization` 在 `code` 字段后加 `org_type = Column(String(16), nullable=False, server_default=text("team"))`
- 乐观锁 version 字段有 default，无需显式赋值

### Step 3 Schema：`app/schemas/org.py`
- `OrganizationOut` 加 `org_type: str = "team"`（管理端区分个人/团队的唯一入口，默认值保证旧调用方不破）
- `OrganizationCreate`/`OrganizationUpdate` **不加**（个人组织不经管理端创建，不污染手工建组织语义）

### Step 4 Service：`app/services/org/personal_org_service.py`（新建）
```python
def ensure_personal_org(db: Session, user: User) -> Organization
```
1. 幂等守卫：`user.organization_id` 非 None → 查回该 Organization 直接返回
2. `db.flush()` 确保 `user.id` 已分配
3. 建 `Organization(name=f"{user.full_name or user.username}的工作台", code=f"personal-{user.id}", org_type="personal")`，`db.add + db.flush()` 分配 id
4. 建 `OrganizationMember(organization_id, user_id, legal_role=LegalMemberRole.admin.value, joined_at=utcnow)`
5. `user.organization_id = org.id`，返回 org
- **全程不 commit**（事务由调用方统一控制）；code 用 `personal-{user.id}` 天然唯一

### Step 5 端点：`app/api/auth/auth_api.py` 两处同型改动
`register`（L248-262）与 `register-with-code`（L718-728）：
- `db.add(user)` 后**删除原 `db.commit()/db.refresh(user)`**
- 改为：`org = ensure_personal_org(db, user)` → `db.commit()` → `db.refresh(user)` → **审计**（`audit_log_service.log_org_action`，auth_api 已 import，L15）→ `_issue_token_response(db, user)`
- 审计必须放业务 commit **之后**：`log()` 内部 `db.commit()`（audit_log_service.py L78，已验证），放前面会提前提交半成品事务；放后面审计失败也不影响注册结果（try/except 包裹，参照 org_api 惯例）
- **组织名唯一冲突兜底**：两个用户同名时 `"{name}的工作台"` 撞 `organizations.name` unique 索引。端点捕获 `IntegrityError` 后回滚，改用 `f"{name}({user.id})的工作台"` 重试一次

### Step 6 测试：`tests/test_personal_org_registration.py`（新建）
结构照抄 `tests/test_auth_phase10.py`（unittest.TestCase + 内存 SQLite + `dependency_overrides[get_db]` + TestClient）。

## 测试用例清单

1. `test_register_creates_personal_org`：注册成功 → 断言 Organization(org_type="personal", code="personal-{user.id}")、OrganizationMember(legal_role="admin")、user.organization_id 回写
2. `test_register_org_name_falls_back_to_username`：full_name 为空时组织名用 username
3. `test_register_duplicate_org_name_recovers`：预置同名组织 → 注册仍成功且组织名带 ({user.id}) 后缀
4. `test_register_with_code_creates_personal_org`：patch `verify_email_code` 返回 True → 断言同 1
5. `test_ensure_personal_org_idempotent`：对已有组织用户二次调用 → 组织总数不变、返回同一 org
6. `test_register_then_me_has_org`：注册 token 调 `GET /api/auth/me`（organization_id 非 None）+ `GET /api/legal/overview` 200
7. 回归：`tests/test_auth_phase10.py` 及 auth 相关既有测试全过

## 验证步骤

1. 迁移对称性：`alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head`
2. 迁移链静态校验：`python -B scripts/check_migrations.py`
3. 单元/集成：`python -m pytest tests/test_personal_org_registration.py tests/test_auth_phase10.py -v`（PYTHONPATH=.）
4. 端到端：起服务 → `POST /api/auth/register` 新用户 → token 调 `GET /api/legal/overview`（原 401 应 200）→ 浏览器注册→工作台确认无 D1 引导条、可创建案件
5. 契约：`python scripts/check_openapi_contract.py`（OrganizationOut 加字段为 non-breaking，确认无 drift）

## 风险与对策（已验证）

| 风险 | 对策 |
| --- | --- |
| `audit_log_service.log()` 内部 commit（L78） | 审计放业务 commit 之后 + try/except |
| `organizations.name` unique 索引撞名 | IntegrityError 捕获后 `{name}({user.id})的工作台` 重试 |
| 事务中途失败留下脏 session | `get_db` 已带 except rollback + close（database.py L66-76，已验证） |
| `_issue_token_response` 依赖 user 持久化 | 统一 commit + refresh 后调用，无问题 |

## 关键文件

- 新建：`alembic/versions/20261009_0094_personal_org.py`、`app/services/org/personal_org_service.py`、`tests/test_personal_org_registration.py`
- 修改：`app/models/org.py`、`app/schemas/org.py`、`app/api/auth/auth_api.py`
- 收尾：删除测试账号 ux_audit（数据库直接删或小脚本）
