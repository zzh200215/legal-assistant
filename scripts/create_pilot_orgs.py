"""#40 试点账号供给：为每家试点律所创建独立组织 + 律师账号 + 团队版配额。

试点要求（pilot-runbook.md）：每个试点团队建立独立组织，敏感案件仅添加必要成员。
每家所 2 个账号：
  - <code>-lawyer  系统 role=dept_admin（审核律师）：可执行律师审核队列动作
  - <code>-assistant 系统 role=user（律师助理）：起草/检索/初稿，无审核权
账号通过 OrganizationMember 绑定到组织（legal_role=admin / editor）。
配额：挂团队版固定上限（咨询 5000 / 审查 2000 / 文书 2000 每月，PLAN_QUOTAS）。

幂等：组织 code 或账号 username 已存在则跳过，不覆盖密码。
用法:
    python -B scripts/create_pilot_orgs.py --dry-run          # 预览将创建的账号
    python -B scripts/create_pilot_orgs.py --orgs 10          # 创建 10 家所
    python -B scripts/create_pilot_orgs.py --password <pwd>   # 指定统一初始密码
输出账号清单 JSON（含初始密码），只打印一次，之后不再可读。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.auth import hash_password  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.org import LegalMemberRole, Organization, OrganizationMember  # noqa: E402
from app.models.subscription import PlanTier, SubscriptionPlan, SubscriptionStatus, UserSubscription  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.subscription_service import subscription_service  # noqa: E402

DEFAULT_PASSWORD = "Pilot@2026"


def _plan_for_team(db) -> SubscriptionPlan:
    subscription_service.ensure_default_plans(db)
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == PlanTier.team.value).first()
    if not plan:
        raise RuntimeError("团队版计划缺失，无法分配配额")
    return plan


def _attach_team_subscription(db, user: User) -> None:
    existing = (
        db.query(UserSubscription)
        .filter(UserSubscription.user_id == user.id, UserSubscription.status == SubscriptionStatus.active.value)
        .first()
    )
    if existing:
        return
    plan = _plan_for_team(db)
    db.add(UserSubscription(user_id=user.id, plan_id=plan.id, status=SubscriptionStatus.active.value))


def _provision_org(db, index: int, password: str) -> dict:
    code = f"pilot{index:02d}"
    org = db.query(Organization).filter(Organization.code == code).first()
    if org is None:
        org = Organization(name=f"试点律所{index:02d}", code=code, description="2026-08 封闭内测试点律所")
        db.add(org)
        db.flush()
    created_accounts: list[dict] = []
    specs = [
        (f"{code}-lawyer", "dept_admin", "审核律师", LegalMemberRole.admin.value),
        (f"{code}-assistant", "user", "律师助理", LegalMemberRole.editor.value),
    ]
    for username, role, full_name, legal_role in specs:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@pilot.example.com",
                hashed_password=hash_password(password),
                role=role,
                status="active",
                full_name=f"{org.name}-{full_name}",
                organization_id=org.id,
            )
            db.add(user)
            db.flush()
            created_accounts.append({"username": username, "password": password, "role": role, "legal_role": legal_role})
        member = (
            db.query(OrganizationMember)
            .filter(OrganizationMember.organization_id == org.id, OrganizationMember.user_id == user.id)
            .first()
        )
        if member is None:
            db.add(OrganizationMember(organization_id=org.id, user_id=user.id, legal_role=legal_role, joined_at=None))
        _attach_team_subscription(db, user)
    db.commit()
    return {"org": code, "org_name": org.name, "org_id": org.id, "created_accounts": created_accounts}


def main() -> int:
    parser = argparse.ArgumentParser(description="#40 试点组织与账号供给")
    parser.add_argument("--orgs", type=int, default=10, help="试点所数量（默认 10）")
    parser.add_argument("--password", type=str, default=DEFAULT_PASSWORD, help="统一初始密码")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    args = parser.parse_args()

    if args.dry_run:
        preview = [
            {
                "org": f"pilot{index:02d}",
                "accounts": [
                    {"username": f"pilot{index:02d}-lawyer", "role": "dept_admin", "legal_role": "admin"},
                    {"username": f"pilot{index:02d}-assistant", "role": "user", "legal_role": "editor"},
                ],
            }
            for index in range(1, args.orgs + 1)
        ]
        print(json.dumps({"mode": "dry-run", "orgs": preview, "password": args.password}, ensure_ascii=False, indent=2))
        return 0

    db = SessionLocal()
    try:
        results = []
        for index in range(1, args.orgs + 1):
            results.append(_provision_org(db, index, args.password))
    finally:
        db.close()

    summary = {
        "mode": "create",
        "orgs": args.orgs,
        "created_accounts": sum(len(r["created_accounts"]) for r in results),
        "details": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
