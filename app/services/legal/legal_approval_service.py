"""Phase 9 Week 3 — 多级审批工作流服务

支持两种审批模式：
- serial：串行，逐级审批（实习律师 → 主办律师 → 合伙人）
- parallel：并行，所有人同时审批，全部通过才算完成

超时策略：每步可设置 due_at；run_timeout_check() 由调度任务周期调用。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.legal import LegalApprovalChain, LegalApprovalStep


class LegalApprovalService:

    # ── Chain creation ────────────────────────────────────────────────────────

    def create_chain(
        self,
        *,
        db: Session,
        org_id: int,
        target_type: str,
        target_id: int,
        chain_type: str,                    # "serial" | "parallel"
        approvers: list[dict],              # [{"user_id": n, "role": "reviewer"}, ...]
        timeout_hours: int | None = None,
        created_by: int,
    ) -> LegalApprovalChain:
        """创建审批链并立即激活第一步。

        approvers 格式：
          serial  -> [{"user_id": 1}, {"user_id": 2}, ...]  (按顺序逐步审批)
          parallel -> [{"user_id": 1}, {"user_id": 2}, ...]  (所有人同时收到)
        """
        if chain_type not in ("serial", "parallel"):
            raise ValueError(f"chain_type 必须是 serial 或 parallel，实际: {chain_type!r}")
        if not approvers:
            raise ValueError("审批链至少需要一个审批人")

        chain = LegalApprovalChain(
            organization_id=org_id,
            target_type=target_type,
            target_id=target_id,
            chain_type=chain_type,
            status="in_progress",
            current_step=0,
            timeout_hours=timeout_hours,
            created_by=created_by,
        )
        db.add(chain)
        db.flush()  # get chain.id

        due_at = self._calc_due(timeout_hours)

        if chain_type == "parallel":
            # 所有 approver 同在 step_order=0
            for approver in approvers:
                db.add(LegalApprovalStep(
                    chain_id=chain.id,
                    step_order=0,
                    approver_id=approver["user_id"],
                    approver_role=approver.get("role"),
                    status="pending",
                    due_at=due_at,
                ))
        else:
            # serial: 第 0 步 pending，其余 step_order>0 的保持 pending（等待前置通过后激活）
            for idx, approver in enumerate(approvers):
                db.add(LegalApprovalStep(
                    chain_id=chain.id,
                    step_order=idx,
                    approver_id=approver["user_id"],
                    approver_role=approver.get("role"),
                    status="pending" if idx == 0 else "waiting",
                    due_at=due_at if idx == 0 else None,
                ))

        db.commit()
        db.refresh(chain)
        return chain

    # ── Take action ───────────────────────────────────────────────────────────

    def take_action(
        self,
        *,
        db: Session,
        chain_id: int,
        approver_id: int,
        action: str,        # "approve" | "reject"
        note: str | None = None,
    ) -> LegalApprovalChain:
        """审批人执行通过/退回操作。"""
        # 先对审批链行加锁（FOR UPDATE）：串行化同一链上的并发审批，
        # 防止并行链两个审批人并发 approve 时各自读到对方未提交的 pending
        # 计数，导致链永久停在 in_progress。锁定读必须早于任何一致性快照读。
        chain = (
            db.query(LegalApprovalChain)
            .filter(LegalApprovalChain.id == chain_id)
            .with_for_update()
            .first()
        )
        if not chain:
            raise ValueError(f"审批链不存在: {chain_id}")
        if chain.status not in ("in_progress",):
            raise ValueError(f"审批链已结束，当前状态: {chain.status}")
        if action not in ("approve", "reject"):
            raise ValueError(f"action 必须是 approve 或 reject")

        # 找到该审批人在当前 active 步骤中的 step 记录
        step = (
            db.query(LegalApprovalStep)
            .filter(
                LegalApprovalStep.chain_id == chain_id,
                LegalApprovalStep.approver_id == approver_id,
                LegalApprovalStep.status == "pending",
            )
            .first()
        )
        if not step:
            raise ValueError("找不到该审批人的待处理步骤，可能已处理或无权限")

        now = datetime.now(timezone.utc)
        step.status = "approved" if action == "approve" else "rejected"
        step.note = note
        step.acted_at = now

        db.flush()

        if action == "reject":
            chain.status = "rejected"
            db.commit()
            db.refresh(chain)
            return chain

        # action == "approve": 检查是否可以推进
        self._advance_chain(db, chain, step.step_order)
        db.commit()
        db.refresh(chain)
        return chain

    def _advance_chain(
        self, db: Session, chain: LegalApprovalChain, completed_step_order: int
    ) -> None:
        if chain.chain_type == "parallel":
            pending = (
                db.query(LegalApprovalStep)
                .filter(
                    LegalApprovalStep.chain_id == chain.id,
                    LegalApprovalStep.status == "pending",
                )
                .count()
            )
            if pending == 0:
                chain.status = "approved"
        else:
            # serial: 检查当前 step_order 是否全部通过
            step_order = completed_step_order
            remaining_in_step = (
                db.query(LegalApprovalStep)
                .filter(
                    LegalApprovalStep.chain_id == chain.id,
                    LegalApprovalStep.step_order == step_order,
                    LegalApprovalStep.status == "pending",
                )
                .count()
            )
            if remaining_in_step > 0:
                return  # 当前步骤还有人没批

            # 激活下一步
            next_steps = (
                db.query(LegalApprovalStep)
                .filter(
                    LegalApprovalStep.chain_id == chain.id,
                    LegalApprovalStep.step_order == step_order + 1,
                    LegalApprovalStep.status == "waiting",
                )
                .all()
            )
            if next_steps:
                due_at = self._calc_due(chain.timeout_hours)
                for s in next_steps:
                    s.status = "pending"
                    s.due_at = due_at
                chain.current_step = step_order + 1
            else:
                # 没有下一步 → 全部完成
                chain.status = "approved"

    # ── Timeout check ─────────────────────────────────────────────────────────

    def run_timeout_check(self, *, db: Session) -> int:
        """检查并标记超时步骤，返回超时数量。由调度任务周期调用。"""
        now = datetime.now(timezone.utc)
        expired_steps = (
            db.query(LegalApprovalStep)
            .filter(
                LegalApprovalStep.status == "pending",
                LegalApprovalStep.due_at.isnot(None),
                LegalApprovalStep.due_at < now,
            )
            .all()
        )
        for step in expired_steps:
            step.status = "timeout"
            chain = db.get(LegalApprovalChain, step.chain_id)
            if chain:
                chain.status = "timeout"

        db.commit()
        return len(expired_steps)

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_chain(self, *, db: Session, chain_id: int) -> LegalApprovalChain | None:
        return db.get(LegalApprovalChain, chain_id)

    def get_pending_for_user(
        self, *, db: Session, user_id: int
    ) -> list[LegalApprovalChain]:
        """返回该用户有待审批步骤的所有审批链。"""
        chain_ids_stmt = (
            select(LegalApprovalStep.chain_id)
            .where(
                LegalApprovalStep.approver_id == user_id,
                LegalApprovalStep.status == "pending",
            )
            .distinct()
        )
        return (
            db.query(LegalApprovalChain)
            .filter(
                LegalApprovalChain.id.in_(chain_ids_stmt),
                LegalApprovalChain.status == "in_progress",
            )
            .all()
        )

    def get_chain_steps(
        self, *, db: Session, chain_id: int
    ) -> list[LegalApprovalStep]:
        return (
            db.query(LegalApprovalStep)
            .filter(LegalApprovalStep.chain_id == chain_id)
            .order_by(LegalApprovalStep.step_order, LegalApprovalStep.id)
            .all()
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_due(timeout_hours: int | None) -> datetime | None:
        if timeout_hours is None:
            return None
        return datetime.now(timezone.utc) + timedelta(hours=timeout_hours)


legal_approval_service = LegalApprovalService()
