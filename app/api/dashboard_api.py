"""管理员数据仪表盘 API"""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import inspect, func, and_
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.api_response import api_error
from app.core.database import get_db
from app.core.time import utc_now
from app.models.user import User, UserStatus
from app.models.legal import LegalCase, LegalConsultation, ContractReview, LegalDraft
from app.models.legal_billing import LegalPaymentRecord, LegalInvoice
from app.models.subscription import SubscriptionPlan, UserSubscription, QuotaUsage, SubscriptionStatus
from app.services.subscription_service import subscription_service

router = APIRouter()

FUNNEL_LABELS = {
    "registered": "注册",
    "first_consultation": "首次咨询",
    "first_contract_review": "首次审查",
    "first_draft": "首次文书",
    "first_review_approved": "首次审核通过",
    "upgraded": "升级付费",
}

PAID_TIERS = ("pro", "team")


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise api_error(403, "需要系统管理员权限", code="ADMIN_REQUIRED")
    return current_user


def _days_ago(n: int) -> datetime:
    return utc_now() - timedelta(days=n)


def _db_has_table(db: Session, name: str) -> bool:
    try:
        return name in inspect(db.bind).get_table_names()
    except Exception:
        return False


@router.get("/dashboard")
def get_dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """
    管理员全局仪表盘。
    返回：用户总览、订阅分布、法律业务统计、近30天趋势、配额预警。
    """
    since_30d = _days_ago(30)
    since_7d = _days_ago(7)

    # ── 用户总览 ──
    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.status == UserStatus.active.value).count()
    new_users_30d = db.query(User).filter(User.created_at >= since_30d).count()
    new_users_7d = db.query(User).filter(User.created_at >= since_7d).count()

    # ── 订阅分布（订阅表缺失时按全部免费计，避免生产库无订阅表而 500）──
    has_subscription = _db_has_table(db, "subscription_plans") and _db_has_table(db, "user_subscriptions")
    sub_dist = {"free": total_users, "pro": 0, "team": 0}
    subscribed_user_ids: set[int] = set()
    if has_subscription:
        subscription_service.ensure_default_plans(db)
        plans = db.query(SubscriptionPlan).all()
        plan_map = {p.id: p.tier for p in plans}

        active_subs = db.query(UserSubscription).filter(
            UserSubscription.status == SubscriptionStatus.active.value
        ).all()
        sub_dist = {"free": 0, "pro": 0, "team": 0}
        for sub in active_subs:
            tier = plan_map.get(sub.plan_id, "free")
            sub_dist[tier] = sub_dist.get(tier, 0) + 1
            subscribed_user_ids.add(sub.user_id)
        sub_dist["free"] += total_users - len(subscribed_user_ids)

    # ── 法律业务统计（近30天）──
    consultations_30d = db.query(LegalConsultation).filter(
        LegalConsultation.created_at >= since_30d
    ).count()
    reviews_30d = db.query(ContractReview).filter(
        ContractReview.created_at >= since_30d
    ).count()
    drafts_30d = db.query(LegalDraft).filter(
        LegalDraft.created_at >= since_30d
    ).count()

    # 总计（历史全量）
    total_consultations = db.query(LegalConsultation).count()
    total_reviews = db.query(ContractReview).count()
    total_drafts = db.query(LegalDraft).count()

    # ── 近7天每日趋势 ──
    daily_trend = []
    for i in range(6, -1, -1):
        day_start = _days_ago(i).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        daily_trend.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "consultations": db.query(LegalConsultation).filter(
                LegalConsultation.created_at >= day_start,
                LegalConsultation.created_at < day_end,
            ).count(),
            "reviews": db.query(ContractReview).filter(
                ContractReview.created_at >= day_start,
                ContractReview.created_at < day_end,
            ).count(),
            "drafts": db.query(LegalDraft).filter(
                LegalDraft.created_at >= day_start,
                LegalDraft.created_at < day_end,
            ).count(),
        })

    # ── 配额预警（当月已用 ≥ 80% 的免费用户，仅订阅库支持）──
    quota_warnings = []
    if has_subscription:
        current_month = utc_now().strftime("%Y-%m")
        free_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "free").first()
        if free_plan:
            # 免费用户 = 全部用户 - 活跃订阅用户（不假设 id 连续）
            all_user_ids = {row[0] for row in db.query(User.id).all()}
            free_user_ids = list(all_user_ids - subscribed_user_ids)
            quota_threshold = int(free_plan.quota_consultation * 0.8)
            at_risk_usages = []
            if free_user_ids and quota_threshold > 0:
                at_risk_usages = db.query(QuotaUsage).filter(
                    QuotaUsage.year_month == current_month,
                    QuotaUsage.consultation_count >= quota_threshold,
                    QuotaUsage.user_id.in_(free_user_ids),
                ).limit(50).all()

            for u in at_risk_usages:
                user = db.query(User).filter(User.id == u.user_id).first()
                if user:
                    quota_warnings.append({
                        "user_id": u.user_id,
                        "username": user.username,
                        "email": user.email,
                        "consultation_used": u.consultation_count,
                        "consultation_quota": free_plan.quota_consultation,
                    })

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "new_30d": new_users_30d,
            "new_7d": new_users_7d,
        },
        "subscriptions": sub_dist,
        "legal_stats": {
            "total": {
                "consultations": total_consultations,
                "reviews": total_reviews,
                "drafts": total_drafts,
            },
            "last_30d": {
                "consultations": consultations_30d,
                "reviews": reviews_30d,
                "drafts": drafts_30d,
            },
        },
        "daily_trend": daily_trend,
        "quota_warnings": quota_warnings,
    }


@router.get("/funnel")
def get_funnel(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """
    试点用户漏斗（P-1）：注册 → 首次咨询 → 首次审查 → 首次文书 → 首次审核通过 → 升级付费。

    从既有业务表按「用户是否已发生首次行为」推导，可追溯历史数据，无需额外事件表。
    口径：
      - 队列 = 近 days 天内注册、且非系统管理员（role != admin）的用户
      - 首次审核通过 = 该用户存在任一 status == 'lawyer_approved' 的咨询/审查/文书记录
      - 升级付费 = 有订阅表的库按 pro/team 订阅；无订阅表（法律计费库）按
        用户所在组织存在已确认收款或已付发票推导
    """
    since = _days_ago(days)
    now = utc_now()

    cohort_rows = db.query(User.id, User.created_at).filter(
        User.role != "admin",
        User.created_at >= since,
    ).all()
    cohort_ids = {r[0] for r in cohort_rows}

    def _in_cohort(ids) -> int:
        return len(cohort_ids & ids)

    consult_ids = {r[0] for r in db.query(LegalConsultation.user_id).distinct().all()}
    review_ids = {r[0] for r in db.query(ContractReview.user_id).distinct().all()}
    draft_ids = {r[0] for r in db.query(LegalDraft.user_id).distinct().all()}

    approved_ids: set[int] = set()
    for model in (LegalConsultation, ContractReview, LegalDraft):
        approved_ids |= {
            r[0] for r in db.query(model.user_id)
            .filter(model.status == "lawyer_approved").distinct().all()
        }

    # 双轨口径：订阅库 vs 法律计费库
    billing_source = "subscription"
    upgraded_ids: set[int] = set()
    if _db_has_table(db, "subscription_plans") and _db_has_table(db, "user_subscriptions"):
        paid_plan_ids = {
            p.id for p in db.query(SubscriptionPlan).filter(
                SubscriptionPlan.tier.in_(PAID_TIERS)
            ).all()
        }
        if paid_plan_ids:
            upgraded_ids = {
                r[0] for r in db.query(UserSubscription.user_id).filter(
                    UserSubscription.plan_id.in_(paid_plan_ids)
                ).distinct().all()
            }
    else:
        billing_source = "legal_billing"
        paid_org_ids: set[int] = set()
        paid_org_ids |= {
            r[0] for r in db.query(LegalPaymentRecord.organization_id).filter(
                LegalPaymentRecord.status == "confirmed"
            ).distinct().all()
        }
        paid_org_ids |= {
            r[0] for r in db.query(LegalInvoice.organization_id).filter(
                LegalInvoice.status == "paid"
            ).distinct().all()
        }
        if paid_org_ids:
            user_org = {
                uid: org for uid, org in db.query(
                    User.id, User.organization_id
                ).filter(User.id.in_(cohort_ids)).all() if org
            }
            upgraded_ids = {uid for uid, org in user_org.items() if org in paid_org_ids}

    stage_counts = [
        ("registered", len(cohort_ids)),
        ("first_consultation", _in_cohort(consult_ids)),
        ("first_contract_review", _in_cohort(review_ids)),
        ("first_draft", _in_cohort(draft_ids)),
        ("first_review_approved", _in_cohort(approved_ids)),
        ("upgraded", _in_cohort(upgraded_ids)),
    ]

    funnel = []
    prev = None
    for idx, (stage, users) in enumerate(stage_counts):
        overall_rate = round(users / len(cohort_ids), 4) if cohort_ids else 0.0
        hop_rate = round(users / prev, 4) if (idx > 0 and prev) else overall_rate
        funnel.append({
            "stage": stage,
            "label": FUNNEL_LABELS.get(stage, stage),
            "users": users,
            "overall_rate": overall_rate,
            "hop_rate": hop_rate,
        })
        prev = users

    first_consult = {
        r[0]: r[1] for r in db.query(
            LegalConsultation.user_id, func.min(LegalConsultation.created_at)
        ).group_by(LegalConsultation.user_id).all()
    }
    days_to_consult = []
    for uid, reg_at in cohort_rows:
        first_at = first_consult.get(uid)
        if reg_at and first_at and first_at >= reg_at:
            days_to_consult.append((first_at - reg_at).total_seconds() / 86400)

    return {
        "days": days,
        "cohort": {
            "start_date": since.strftime("%Y-%m-%d"),
            "end_date": now.strftime("%Y-%m-%d"),
            "registered": len(cohort_ids),
            "scope": "role != admin",
            "billing_source": billing_source,
        },
        "funnel": funnel,
        "activation": {
            "avg_days_reg_to_first_consult": round(
                sum(days_to_consult) / len(days_to_consult), 2
            ) if days_to_consult else None,
            "cohort_users_with_consultation": _in_cohort(consult_ids),
        },
    }


def _week_start(dt: datetime) -> datetime:
    """本周一 0 点（周一为周首），naive UTC。"""
    d = dt.date()
    return datetime(d.year, d.month, d.day) - timedelta(days=d.weekday())


def _task_rows_since(db: Session, since: datetime) -> list[tuple[int, Optional[int], datetime]]:
    """三张业务表自 since 起的 (user_id, case_id, created_at) 任务行。"""
    rows: list[tuple[int, Optional[int], datetime]] = []
    for model in (LegalConsultation, ContractReview, LegalDraft):
        rows.extend(
            db.query(model.user_id, model.case_id, model.created_at)
            .filter(model.created_at >= since)
            .all()
        )
    return rows


@router.get("/retention")
def get_retention(
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """
    试点用户留存（P-5）：按注册周（周一为周首）分群的新用户 7 日 / 30 日留存。

    口径：
      - 群 = 近 days 天内注册、且非系统管理员（role != admin）的用户，按注册所在周分群
      - 回归 = 在窗口内完成 ≥1 次咨询/审查/文书（三表任一记录）
      - D7 窗口 = [注册+7天, 注册+14天)，D30 窗口 = [注册+28天, 注册+35天)
      - 窗口尚未完全经历（now < 窗口结束）的用户不计入分母；分母为 0 时该项返回 None
    """
    since = _days_ago(days)
    now = utc_now()

    cohort_rows = db.query(User.id, User.created_at).filter(
        User.role != "admin",
        User.created_at >= since,
    ).all()

    tasks_by_user: dict[int, list[datetime]] = defaultdict(list)
    for uid, _, created_at in _task_rows_since(db, since):
        if created_at:
            tasks_by_user[uid].append(created_at)

    def _window_hit(dates: list[datetime], lo: datetime, hi: datetime) -> bool:
        return any(lo <= d < hi for d in dates)

    cohorts: dict[datetime, list[tuple[int, datetime]]] = defaultdict(list)
    for uid, reg_at in cohort_rows:
        if reg_at:
            cohorts[_week_start(reg_at)].append((uid, reg_at))

    result = []
    for week_start in sorted(cohorts):
        cohort = cohorts[week_start]
        d7_active = d7_observed = 0
        d30_active = d30_observed = 0
        for uid, reg_at in cohort:
            dates = tasks_by_user.get(uid, [])
            if now >= reg_at + timedelta(days=14):
                d7_observed += 1
                if _window_hit(dates, reg_at + timedelta(days=7), reg_at + timedelta(days=14)):
                    d7_active += 1
            if now >= reg_at + timedelta(days=35):
                d30_observed += 1
                if _window_hit(dates, reg_at + timedelta(days=28), reg_at + timedelta(days=35)):
                    d30_active += 1
        result.append({
            "week_start": week_start.strftime("%Y-%m-%d"),
            "cohort_size": len(cohort),
            "d7": {
                "rate": round(d7_active / d7_observed, 4) if d7_observed else None,
                "active": d7_active,
                "observed": d7_observed,
            },
            "d30": {
                "rate": round(d30_active / d30_observed, 4) if d30_observed else None,
                "active": d30_active,
                "observed": d30_observed,
            },
        })

    def _pooled(metric: str) -> dict:
        observed = sum(c[metric]["observed"] for c in result)
        active = sum(c[metric]["active"] for c in result)
        return {
            "rate": round(active / observed, 4) if observed else None,
            "active": active,
            "observed": observed,
        }

    return {
        "days": days,
        "cohort": {
            "start_date": since.strftime("%Y-%m-%d"),
            "end_date": now.strftime("%Y-%m-%d"),
            "scope": "role != admin, weekly cohorts (Monday start)",
        },
        "cohorts": result,
        "summary": {
            "d7": _pooled("d7"),
            "d30": _pooled("d30"),
        },
    }


@router.get("/north-star")
def get_north_star(
    weeks: int = Query(12, ge=4, le=52),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """
    北极星指标看板（P-5）：按周统计活跃律师数与案件闭环信号。

    口径：
      - active_lawyers = 该周内完成 ≥1 次咨询/审查/文书的非 admin 用户
      - with_active_case = 该周活跃 且 当前持有 status='in_progress' 案件（U-3 案件闭环）的用户
      - tasks = 该周三表任务总数；case_tasks = 其中关联 case_id（挂在案件下）的数量
      - weekly_change_pct = 最近一周相对前一周的变化百分比
    """
    now = utc_now()
    start = _week_start(now) - timedelta(weeks=weeks - 1)

    rows = _task_rows_since(db, start)

    # 与漏斗/留存口径一致：排除系统管理员
    admin_ids = {r[0] for r in db.query(User.id).filter(User.role == "admin").all()}
    rows = [(uid, case_id, created_at) for uid, case_id, created_at in rows if uid not in admin_ids]

    open_case_user_ids = {
        r[0] for r in db.query(LegalCase.user_id).filter(
            LegalCase.status == "in_progress"
        ).distinct().all()
    }

    buckets = []
    for i in range(weeks):
        ws = start + timedelta(weeks=i)
        we = ws + timedelta(weeks=1)
        active_users: set[int] = set()
        tasks = 0
        case_tasks = 0
        for uid, case_id, created_at in rows:
            if created_at and ws <= created_at < we:
                active_users.add(uid)
                tasks += 1
                if case_id is not None:
                    case_tasks += 1
        buckets.append({
            "week_start": ws.strftime("%Y-%m-%d"),
            "active_lawyers": len(active_users),
            "with_active_case": len(active_users & open_case_user_ids),
            "tasks": tasks,
            "case_tasks": case_tasks,
        })

    def _change(cur: int, prev: int) -> Optional[float]:
        if prev == 0:
            return None
        return round((cur - prev) / prev * 100, 2)

    prev = buckets[-2] if len(buckets) >= 2 else None
    return {
        "weeks": weeks,
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": now.strftime("%Y-%m-%d"),
        "weekly": buckets,
        "current": buckets[-1],
        "weekly_change_pct": {
            k: (_change(buckets[-1][k], prev[k]) if prev else None)
            for k in ("active_lawyers", "with_active_case", "tasks", "case_tasks")
        },
    }


@router.get("/users-stats")
def get_users_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """按天统计注册用户数（管理员）"""
    since = _days_ago(days)
    rows = db.query(
        func.date(User.created_at).label("date"),
        func.count(User.id).label("count"),
    ).filter(
        User.created_at >= since
    ).group_by(
        func.date(User.created_at)
    ).order_by("date").all()

    return [{"date": r.date, "new_users": r.count} for r in rows]


@router.get("/subscription-revenue")
def get_subscription_revenue(
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """订阅收入估算（基于当前活跃订阅的计划单价；无订阅表时返回空）"""
    if not (_db_has_table(db, "subscription_plans") and _db_has_table(db, "user_subscriptions")):
        return {
            "monthly_revenue_estimate": 0.0,
            "currency": "CNY",
            "breakdown": {},
            "active_paid_subscriptions": 0,
        }
    subscription_service.ensure_default_plans(db)
    plans = {p.id: p for p in db.query(SubscriptionPlan).all()}

    active_subs = db.query(UserSubscription).filter(
        UserSubscription.status == SubscriptionStatus.active.value
    ).all()

    monthly_revenue = 0.0
    breakdown = {}
    for sub in active_subs:
        plan = plans.get(sub.plan_id)
        if plan:
            price = float(plan.price_monthly)
            monthly_revenue += price
            breakdown[plan.tier] = breakdown.get(plan.tier, 0) + price

    return {
        "monthly_revenue_estimate": round(monthly_revenue, 2),
        "currency": "CNY",
        "breakdown": breakdown,
        "active_paid_subscriptions": len([s for s in active_subs if plans.get(s.plan_id) and float(plans[s.plan_id].price_monthly) > 0]),
    }


# ── 等保差距 #2：集中日志检索（双轨 + 登录日志）──────────────────────────────

def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


@router.get("/logs/search")
def search_logs(
    source: Optional[str] = Query(None, description="operation_log / audit_log / login_log；空=全部"),
    keyword: Optional[str] = Query(None, description="跨字段模糊匹配（action/module/detail/actor 等）"),
    action: Optional[str] = Query(None),
    module: Optional[str] = Query(None, description="仅 operation_log"),
    user_id: Optional[int] = Query(None),
    days: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(_require_admin),
):
    """集中检索三轨日志（操作/审计/登录），时间倒序合并分页。供等保审计/集中排查。"""
    since = _days_ago(days)

    def _match_keyword(*fields) -> bool:
        if not keyword:
            return True
        return any(keyword in str(field or "") for field in fields)

    rows = []
    if source in (None, "operation_log"):
        from app.models.operation_log import OperationLog

        q = db.query(OperationLog).filter(OperationLog.created_at >= since)
        if action:
            q = q.filter(OperationLog.action == action)
        if module:
            q = q.filter(OperationLog.module == module)
        if user_id:
            q = q.filter(OperationLog.user_id == user_id)
        for r in q.order_by(OperationLog.created_at.desc()).limit(500).all():
            if _match_keyword(r.action, r.module, r.detail or ""):
                rows.append({
                    "source": "operation_log", "id": r.id, "user_id": r.user_id, "module": r.module,
                    "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
                    "detail": r.detail, "ip_address": r.ip_address, "created_at": _iso(r.created_at),
                })

    if source in (None, "audit_log"):
        from app.models.auth_log import AdminAuditLog

        q = db.query(AdminAuditLog).filter(AdminAuditLog.created_at >= since)
        if action:
            q = q.filter(AdminAuditLog.action == action)
        if user_id:
            q = q.filter(AdminAuditLog.operator_id == user_id)
        for r in q.order_by(AdminAuditLog.created_at.desc()).limit(500).all():
            if _match_keyword(r.action, r.operator_name, r.target_name or "", r.detail or ""):
                rows.append({
                    "source": "audit_log", "id": r.id, "operator_id": r.operator_id,
                    "operator_name": r.operator_name, "action": r.action,
                    "target_type": r.target_type, "target_id": r.target_id, "target_name": r.target_name,
                    "detail": r.detail, "ip_address": r.ip_address, "created_at": _iso(r.created_at),
                })

    if source in (None, "login_log"):
        from app.models.auth_log import LoginLog

        q = db.query(LoginLog).filter(LoginLog.created_at >= since)
        if action:
            q = q.filter(LoginLog.event_type == action)
        if user_id:
            q = q.filter(LoginLog.user_id == user_id)
        for r in q.order_by(LoginLog.created_at.desc()).limit(500).all():
            if _match_keyword(r.event_type, r.username or "", r.detail or ""):
                rows.append({
                    "source": "login_log", "id": r.id, "user_id": r.user_id, "username": r.username,
                    "action": r.event_type, "target_type": None, "target_id": None,
                    "detail": r.detail, "ip_address": r.ip_address, "created_at": _iso(r.created_at),
                })

    rows.sort(key=lambda x: x["created_at"] or "", reverse=True)
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
