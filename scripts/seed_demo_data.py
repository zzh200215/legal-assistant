"""Create idempotent local data for the legal workspace demo flows."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.models.document import Document, DocumentChunk
from app.models.task import Task
from app.models.user import User
from app.models.org import Organization, OrganizationMember
from app.models.legal import LegalCase
from app.models.legal_portal import LegalCaseMember, LegalDeadline
from app.models.legal import LegalConsultation, ContractReview, LegalDraft


DEMO_EMAIL = "demo@legal-ai.example.com"
DEMO_REVIEWER_EMAIL = "reviewer@legal-ai.example.com"
DEMO_ORG_CODE = "demo-legal"
UPLOADS = Path("uploads")


def ensure_file(name: str, content: str) -> str:
    UPLOADS.mkdir(exist_ok=True)
    path = UPLOADS / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def ensure_document(db, user: User, title: str, filename: str, content: str,
                    organization_id: int | None = None) -> Document:
    document = db.query(Document).filter(Document.user_id == user.id, Document.title == title).first()
    path = ensure_file(filename, content)
    if not document:
        document = Document(
            user_id=user.id,
            organization_id=organization_id,
            title=title,
            file_path=path,
            file_type="md",
            status="indexed",
        )
        db.add(document)
        db.commit()
        db.refresh(document)
    elif organization_id and document.organization_id != organization_id:
        document.organization_id = organization_id
        db.commit()
    if not db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).first():
        db.add(DocumentChunk(document_id=document.id, chunk_index=0, content=content, page_number=1, section_title="演示正文", section_path="演示正文"))
        db.commit()
    return document


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if not user:
            user = User(
                username="demo_lawyer", email=DEMO_EMAIL, full_name="演示律师",
                hashed_password=hash_password("Demo@123456"), role="user",
            )
            db.add(user); db.commit(); db.refresh(user)

        reviewer = db.query(User).filter(User.email == DEMO_REVIEWER_EMAIL).first()
        if not reviewer:
            reviewer = User(
                username="demo_reviewer", email=DEMO_REVIEWER_EMAIL, full_name="演示审核律师",
                hashed_password=hash_password("Review@123456"), role="dept_admin",
            )
            db.add(reviewer); db.commit(); db.refresh(reviewer)

        organization = db.query(Organization).filter(Organization.code == DEMO_ORG_CODE).first()
        if not organization:
            organization = Organization(
                name="律智检演示团队", code=DEMO_ORG_CODE,
                description="用于案件、合同审查、文书和审核流程的本地演示组织",
            )
            db.add(organization); db.commit(); db.refresh(organization)

        for member_user, legal_role in (
            (user, "editor"),
            (reviewer, "reviewer"),
        ):
            if member_user.organization_id != organization.id:
                member_user.organization_id = organization.id
            member = db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == organization.id,
                OrganizationMember.user_id == member_user.id,
            ).first()
            if not member:
                db.add(OrganizationMember(
                    organization_id=organization.id,
                    user_id=member_user.id,
                    legal_role=legal_role,
                    joined_at=datetime.now(timezone.utc),
                ))
            elif member.legal_role != legal_role:
                member.legal_role = legal_role
        db.commit()

        case = db.query(LegalCase).filter(
            LegalCase.organization_id == organization.id,
            LegalCase.title == "劳动争议·张某诉某科技（演示）",
        ).first()
        if not case:
            case = LegalCase(
                organization_id=organization.id,
                user_id=user.id,
                title="劳动争议·张某诉某科技（演示）",
                case_type="labor_dispute",
                status="in_progress",
                client_name="张某",
                opposing_party="某科技有限公司",
                description="员工因业务调整被单方解除，需核验解除程序、经济补偿和证据链。",
                is_strict_mode=0,
            )
            db.add(case); db.commit(); db.refresh(case)
        else:
            case.user_id = user.id
            case.status = "in_progress"
            db.commit()

        for member_user, case_role in ((user, "owner"), (reviewer, "collaborator")):
            case_member = db.query(LegalCaseMember).filter(
                LegalCaseMember.case_id == case.id,
                LegalCaseMember.user_id == member_user.id,
            ).first()
            if not case_member:
                db.add(LegalCaseMember(
                    case_id=case.id, organization_id=organization.id,
                    user_id=member_user.id, case_role=case_role,
                    granted_by=user.id,
                ))
        deadline = db.query(LegalDeadline).filter(
            LegalDeadline.case_id == case.id,
            LegalDeadline.deadline_type == "custom",
            LegalDeadline.description == "劳动争议材料补充截止（演示）",
        ).first()
        if not deadline:
            db.add(LegalDeadline(
                organization_id=organization.id, case_id=case.id,
                deadline_type="custom",
                deadline_at=datetime.now(timezone.utc) + timedelta(days=14),
                owner_id=user.id, created_by=user.id,
                description="劳动争议材料补充截止（演示）",
                reminder_offsets_json="[7,3,1]",
            ))
        db.commit()

        # 法律知识库演示文档
        contract = ensure_document(
            db, user, "技术服务合同审查依据（演示）",
            "demo_legal_contract.md",
            "# 技术服务合同\n甲方：星河制造（上海）有限公司\n乙方：云帆智能科技（杭州）有限公司\n"
            "合同签订日期为 2026 年 7 月 1 日。\n合同总金额为 268 万元。\n"
            "付款方式：签约后5个工作日内付100万，阶段验收后付108万，最终验收后付60万。\n"
            "违约责任：逾期交付按日0.3%支付违约金，上限为合同总额10%。\n"
            "争议解决：提交上海仲裁委员会仲裁。\n",
            organization_id=organization.id,
        )
        labor_case = ensure_document(
            db, user, "劳动争议咨询案例（演示）",
            "demo_legal_labor_case.md",
            "# 劳动争议咨询\n申请人：张三\n被申请人：XX科技有限公司\n"
            "入职时间：2023 年 3 月 1 日\n解除时间：2026 年 6 月 30 日\n月工资：25,000 元\n"
            "争议事项：公司以业务调整为由单方解除，未提前30日通知，未支付经济补偿金。\n"
            "法律依据：劳动合同法第40条、第46条、第47条。\n",
            organization_id=organization.id,
        )
        lending = ensure_document(
            db, user, "民间借贷纠纷案例（演示）",
            "demo_legal_lending.md",
            "# 民间借贷纠纷\n原告：王五\n被告：赵六\n"
            "借款金额：50 万元\n借款日期：2025 年 1 月 15 日\n约定年利率：18%\n"
            "争议事项：超过法定利率上限部分是否应予支持。\n",
            organization_id=organization.id,
        )

        # 法律审查任务演示
        if not db.query(Task).filter(Task.user_id == user.id, Task.title == "审查技术服务合同付款条款（演示）").first():
            db.add(Task(user_id=user.id, organization_id=organization.id, title="审查技术服务合同付款条款（演示）", priority="high", status="todo", source_type="legal_review", source_id=contract.id))
            db.commit()
        if not db.query(Task).filter(Task.user_id == user.id, Task.title == "确认劳动争议经济补偿计算（演示）").first():
            db.add(Task(user_id=user.id, organization_id=organization.id, title="确认劳动争议经济补偿计算（演示）", priority="medium", status="in_progress", source_type="legal_consultation", source_id=labor_case.id))
            db.commit()

        # FL.md 10: 可复现 Demo — 劳动争议咨询 + 合同审查 + 文书草稿
        if not db.query(LegalConsultation).filter(LegalConsultation.user_id == user.id, LegalConsultation.category == "labor_dispute").first():
            db.add(LegalConsultation(
                user_id=user.id, case_id=case.id,
                question="公司以业务调整为由单方解除劳动合同，未提前30日通知，也未支付经济补偿金，我该怎么办？",
                category="labor_dispute",
                known_facts_json=json.dumps(["入职时间2023年3月1日", "解除时间2026年6月30日", "月工资25,000元", "公司以业务调整为由单方解除"], ensure_ascii=False),
                missing_facts_json=json.dumps(["是否签署书面劳动合同", "是否处于医疗期/孕期/产期", "公司是否有工会且已通知工会"], ensure_ascii=False),
                references_json=json.dumps([{"title": "劳动合同法第40条", "citation": "第四十条", "version": "现行"}, {"title": "劳动合同法第46条", "citation": "第四十六条", "version": "现行"}, {"title": "劳动合同法第47条", "citation": "第四十七条", "version": "现行"}], ensure_ascii=False),
                advice="根据劳动合同法第40条，用人单位单方解除应提前30日通知或额外支付一个月工资。第46条规定的情形应支付经济补偿金。按第47条，经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。3年3个月≈3.5个月工资，即87,500元。建议先与公司协商，协商不成可向劳动争议仲裁委员会申请仲裁。AI 辅助结果，不构成正式法律意见。",
                risk_level="medium", status="pending_review",
            ))
            db.commit()
        consultation = db.query(LegalConsultation).filter(
            LegalConsultation.user_id == user.id,
            LegalConsultation.category == "labor_dispute",
        ).first()
        if consultation and consultation.case_id != case.id:
            consultation.case_id = case.id
            db.commit()
        if consultation and consultation.status != "pending_review":
            consultation.status = "pending_review"
            consultation.reviewer_id = None
            consultation.review_note = None
            consultation.reviewed_at = None
            db.commit()

        if not db.query(ContractReview).filter(ContractReview.user_id == user.id, ContractReview.title.like("%技术服务%")).first():
            db.add(ContractReview(
                user_id=user.id, case_id=case.id, document_id=contract.id,
                title="技术服务合同（演示）",
                content="甲方：星河制造 乙方：云帆智能 合同金额268万元 付款：签约后5日内付100万，阶段验收后付108万，最终验收后付60万 违约：逾期交付按日0.3%支付违约金，上限为合同总额10% 争议：提交上海仲裁委员会仲裁",
                summary="共识别8类条款，其中付款条款存在阶段验收时间未明确风险，违约金上限较低可能不足以覆盖实际损失。",
                risks_json=json.dumps([
                    {"clause_type": "payment", "risk_level": "high", "description": "阶段验收时间未明确，108万付款条件模糊", "suggestion": "明确各阶段验收标准、时间节点和付款触发条件", "source_location": {"paragraph": 1, "snippet": "阶段验收后付108万"}, "status": "open"},
                    {"clause_type": "breach", "risk_level": "medium", "description": "违约金上限为合同总额10%（26.8万），可能不足以覆盖实际损失", "suggestion": "考虑提高违约金上限或取消上限，改为按实际损失赔偿", "source_location": {"paragraph": 1, "snippet": "上限为合同总额10%"}, "status": "open"},
                    {"clause_type": "dispute_resolution", "risk_level": "low", "description": "仲裁机构已明确为上海仲裁委员会", "suggestion": "确认双方对仲裁地无异议", "source_location": {"paragraph": 1, "snippet": "提交上海仲裁委员会仲裁"}, "status": "open"},
                ], ensure_ascii=False),
                references_json=json.dumps([{"title": "民法典合同编", "citation": "合同编", "version": "现行"}], ensure_ascii=False),
                status="needs_lawyer_review",
            ))
            db.commit()
        review = db.query(ContractReview).filter(
            ContractReview.user_id == user.id,
            ContractReview.title.like("%技术服务%"),
        ).first()
        if review:
            changed = review.case_id != case.id or review.document_id != contract.id
            review.case_id = case.id
            review.document_id = contract.id
            review.status = "needs_lawyer_review"
            review.reviewer_id = None
            review.review_note = None
            review.reviewed_at = None
            review.reviewed_version = None
            review.is_final = 0
            if changed:
                db.commit()
            else:
                db.commit()

        if not db.query(LegalDraft).filter(LegalDraft.user_id == user.id, LegalDraft.document_type == "labor_arbitration_application").first():
            db.add(LegalDraft(
                user_id=user.id, case_id=case.id,
                document_type="labor_arbitration_application", title="劳动争议仲裁申请书（演示）",
                fields_json=json.dumps({"申请人": "张三", "被申请人": "XX科技有限公司", "劳动关系起止时间": "2023年3月1日至2026年6月30日", "仲裁请求": "支付违法解除劳动合同赔偿金87,500元", "事实与理由": "公司以业务调整为由单方解除，未提前30日通知，未支付经济补偿金", "证据清单": "劳动合同、工资流水、解除通知"}, ensure_ascii=False),
                missing_fields_json="[]", references_json=json.dumps([{"title": "劳动合同法第40条", "citation": "第四十条", "version": "现行"}], ensure_ascii=False),
                content="劳动人事争议仲裁申请书\n\n申请人：张三\n被申请人：XX科技有限公司\n\n仲裁请求：\n1. 支付违法解除劳动合同赔偿金87,500元\n\n事实与理由：\n申请人于2023年3月1日入职被申请人处，月工资25,000元。2026年6月30日，被申请人以业务调整为由单方解除劳动合同，未提前30日书面通知，亦未支付经济补偿金。\n\n根据《劳动合同法》第40条、第46条、第47条之规定，被申请人应当支付经济补偿金。\n\n证据清单：\n1. 劳动合同\n2. 工资银行流水\n3. 解除劳动合同通知书\n\nAI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。",
                status="pending_review",
            ))
            db.commit()
        draft = db.query(LegalDraft).filter(
            LegalDraft.user_id == user.id,
            LegalDraft.document_type == "labor_arbitration_application",
        ).first()
        if draft and draft.case_id != case.id:
            draft.case_id = case.id
            db.commit()
        if draft:
            draft.status = "pending_review"
            draft.reviewer_id = None
            draft.review_note = None
            draft.reviewed_at = None
            draft.reviewed_version = None
            draft.is_final = 0
            db.commit()

        print(json.dumps({
            "demo_user": DEMO_EMAIL,
            "demo_username": "demo_lawyer",
            "password": "Demo@123456",
            "reviewer_user": DEMO_REVIEWER_EMAIL,
            "reviewer_username": "demo_reviewer",
            "reviewer_password": "Review@123456",
            "organization_id": organization.id,
            "case_id": case.id,
            "document_ids": [contract.id, labor_case.id, lending.id],
        }, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
