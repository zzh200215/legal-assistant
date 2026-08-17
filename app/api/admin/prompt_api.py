from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.api_response import api_error
from app.core.database import get_db
from app.core.auth import require_admin_user
from app.models.user import User
from app.services.observability.oplog_service import oplog_service
from app.services.llm.prompt_service import prompt_service
from app.schemas.prompt import PromptTemplateCreate, PromptTemplateOut

router = APIRouter()


class RenderRequest(BaseModel):
    variables: dict[str, str]


class ActivateVersionRequest(BaseModel):
    version_id: int


class RolloutVersionRequest(BaseModel):
    version_id: int
    rollout_percentage: int


class RollbackVersionRequest(BaseModel):
    target_version_id: int | None = None


@router.post("/", response_model=PromptTemplateOut)
def create_template(req: PromptTemplateCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    if prompt_service.get_by_name(req.name, db):
        raise api_error(400, f"模板名称 '{req.name}' 已存在", code="PROMPT_NAME_ALREADY_EXISTS")
    try:
        tmpl = prompt_service.create(**req.model_dump(), db=db)
        oplog_service.log(
            module="prompt",
            action="prompt_template_created",
            db=db,
            user_id=current_user.id,
            target_type="prompt_template",
            target_id=tmpl.id,
            detail=f"name={tmpl.name}; version={tmpl.active_version.version if tmpl.active_version else 1}",
        )
        return prompt_service.serialize_template(tmpl)
    except ValueError as e:
        raise api_error(400, "Prompt 模板变量声明与内容不一致", code="PROMPT_VARIABLE_SCHEMA_INVALID", detail=str(e))
    except Exception as e:
        raise api_error(500, "创建 Prompt 模板失败", code="PROMPT_CREATE_FAILED", detail=str(e))


@router.get("/", response_model=list[PromptTemplateOut])
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    return [prompt_service.serialize_template(item) for item in prompt_service.list_all(db)]


@router.get("/{template_id}", response_model=PromptTemplateOut)
def get_template(template_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    tmpl = prompt_service.get(template_id, db)
    if not tmpl:
        raise api_error(404, "Prompt 模板不存在", code="PROMPT_NOT_FOUND")
    return prompt_service.serialize_template(tmpl)


@router.put("/{template_id}", response_model=PromptTemplateOut)
def update_template(template_id: int, req: PromptTemplateCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    try:
        tmpl = prompt_service.update(template_id, db, **req.model_dump(exclude_unset=True))
        oplog_service.log(
            module="prompt",
            action="prompt_template_updated",
            db=db,
            user_id=current_user.id,
            target_type="prompt_template",
            target_id=tmpl.id,
            detail=f"name={tmpl.name}; active_version={tmpl.active_version.version if tmpl.active_version else 'n/a'}",
        )
        return prompt_service.serialize_template(tmpl)
    except ValueError as e:
        detail = str(e)
        if detail == "Template not found":
            raise api_error(404, "Prompt 模板不存在", code="PROMPT_NOT_FOUND", detail=detail)
        raise api_error(400, "Prompt 模板变量声明与内容不一致", code="PROMPT_VARIABLE_SCHEMA_INVALID", detail=detail)
    except Exception as e:
        raise api_error(500, "更新 Prompt 模板失败", code="PROMPT_UPDATE_FAILED", detail=str(e))


@router.post("/{template_id}/activate", response_model=PromptTemplateOut)
def activate_template_version(
    template_id: int,
    req: ActivateVersionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        tmpl = prompt_service.activate_version(template_id, req.version_id, db)
        oplog_service.log(
            module="prompt",
            action="prompt_version_activated",
            db=db,
            user_id=current_user.id,
            target_type="prompt_template",
            target_id=tmpl.id,
            detail=f"version_id={req.version_id}; active_version={tmpl.active_version.version if tmpl.active_version else 'n/a'}",
        )
        return prompt_service.serialize_template(tmpl)
    except ValueError as e:
        detail = str(e)
        if detail == "Template version not found":
            raise api_error(404, "Prompt 版本不存在", code="PROMPT_VERSION_NOT_FOUND", detail=detail)
        raise api_error(404, "Prompt 模板不存在", code="PROMPT_NOT_FOUND", detail=detail)


@router.post("/{template_id}/rollout", response_model=PromptTemplateOut)
def rollout_template_version(
    template_id: int,
    req: RolloutVersionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        tmpl = prompt_service.start_rollout(template_id, req.version_id, req.rollout_percentage, db)
        oplog_service.log(
            module="prompt",
            action="prompt_version_rollout_started",
            db=db,
            user_id=current_user.id,
            target_type="prompt_template",
            target_id=tmpl.id,
            detail=f"version_id={req.version_id}; rollout_percentage={req.rollout_percentage}",
        )
        return prompt_service.serialize_template(tmpl)
    except ValueError as e:
        detail = str(e)
        if detail == "Template version not found":
            raise api_error(404, "Prompt 版本不存在", code="PROMPT_VERSION_NOT_FOUND", detail=detail)
        if detail == "Template not found":
            raise api_error(404, "Prompt 模板不存在", code="PROMPT_NOT_FOUND", detail=detail)
        raise api_error(400, "Prompt 灰度发布参数不合法", code="PROMPT_ROLLOUT_INVALID", detail=detail)


@router.post("/{template_id}/rollback", response_model=PromptTemplateOut)
def rollback_template_version(
    template_id: int,
    req: RollbackVersionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        tmpl = prompt_service.rollback(template_id, db, target_version_id=req.target_version_id)
        oplog_service.log(
            module="prompt",
            action="prompt_version_rollback",
            db=db,
            user_id=current_user.id,
            target_type="prompt_template",
            target_id=tmpl.id,
            detail=f"target_version_id={req.target_version_id}; active_version={tmpl.active_version.version if tmpl.active_version else 'n/a'}; rollout_version_id={tmpl.rollout_version_id}",
        )
        return prompt_service.serialize_template(tmpl)
    except ValueError as e:
        detail = str(e)
        if detail == "Template version not found":
            raise api_error(404, "Prompt 版本不存在", code="PROMPT_VERSION_NOT_FOUND", detail=detail)
        if detail == "Template not found":
            raise api_error(404, "Prompt 模板不存在", code="PROMPT_NOT_FOUND", detail=detail)
        raise api_error(400, "没有可回滚的 Prompt 版本", code="PROMPT_ROLLBACK_NOT_AVAILABLE", detail=detail)


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    try:
        prompt_service.delete(template_id, db)
        oplog_service.log(
            module="prompt",
            action="prompt_template_deleted",
            db=db,
            user_id=current_user.id,
            target_type="prompt_template",
            target_id=template_id,
            detail="deleted",
        )
        return {"detail": "已删除"}
    except ValueError as e:
        raise api_error(404, "Prompt 模板不存在", code="PROMPT_NOT_FOUND", detail=str(e))


@router.post("/{template_id}/render")
def render_template(template_id: int, req: RenderRequest, db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    try:
        rendered = prompt_service.render(template_id, db, **req.variables)
        return {"rendered": rendered}
    except ValueError as e:
        detail = str(e)
        if detail == "Template version not found":
            raise api_error(404, "Prompt 版本不存在", code="PROMPT_VERSION_NOT_FOUND", detail=detail)
        raise api_error(404, "Prompt 模板不存在", code="PROMPT_NOT_FOUND", detail=detail)


@router.post("/seed")
def seed_defaults(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    count = prompt_service.seed_defaults(db)
    return {"detail": f"已初始化 {count} 个默认模板"}
