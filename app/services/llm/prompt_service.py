import json
import hashlib
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models.prompt import PromptTemplate, PromptTemplateVersion
from app.services.llm.prompt_defaults import DEFAULT_PROMPT_TEMPLATES

EVAL_OUTPUT_SUMMARY_PATH = Path(__file__).resolve().parents[2] / "eval" / "outputs" / "summary.json"


class PromptService:
    @staticmethod
    def _stable_bucket(template_name: str, user_id: int) -> int:
        key = f"{template_name}:{user_id}".encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        return int(digest[:8], 16) % 100

    @staticmethod
    def _parse_variables_schema(variables: str | None) -> list[dict]:
        if not variables or not variables.strip():
            return []
        raw = variables.strip()
        try:
            payload = json.loads(raw)
            if isinstance(payload, list):
                parsed = []
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    if not name:
                        continue
                    parsed.append(
                        {
                            "name": name,
                            "required": bool(item.get("required", True)),
                            "description": str(item.get("description") or "").strip() or None,
                        }
                    )
                if parsed:
                    return parsed
        except json.JSONDecodeError:
            pass
        return [{"name": item.strip(), "required": True, "description": None} for item in raw.split(",") if item.strip()]

    def _validate_template_variables(self, template: str, variables: str | None) -> None:
        declared = {item["name"] for item in self._parse_variables_schema(variables)}
        # JSON examples often contain literal braces. Only {identifier} tokens
        # are template variables; JSON object keys such as {"overview": ...}
        # must not be interpreted as placeholders.
        referenced = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template))
        missing = sorted(referenced - declared)
        extra = sorted(declared - referenced)
        if missing:
            raise ValueError(f"Template variables missing declarations: {', '.join(missing)}")
        if extra:
            raise ValueError(f"Declared variables not used in template: {', '.join(extra)}")

    @staticmethod
    def _normalize_change_note(change_note: str | None, fallback: str) -> str:
        normalized = (change_note or "").strip()
        return normalized or fallback

    def _load_prompt_experiment_refs(self, prompt_name: str, version_number: int) -> list[dict]:
        if not EVAL_OUTPUT_SUMMARY_PATH.exists():
            return []
        try:
            payload = json.loads(EVAL_OUTPUT_SUMMARY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        experiments = payload.get("experiments")
        if not isinstance(experiments, list):
            return []
        refs = []
        for experiment in experiments:
            effective_config = experiment.get("effective_config") or {}
            if effective_config.get("prompt_template") != prompt_name:
                continue
            if effective_config.get("prompt_version") != version_number:
                continue
            refs.append(
                {
                    "experiment_name": experiment.get("name"),
                    "summary": experiment.get("summary") or {},
                    "baseline_delta": experiment.get("baseline_delta") or {},
                }
            )
        return refs

    def create(
        self,
        name: str,
        template: str,
        db: Session,
        description: str | None = None,
        variables: str | None = None,
        change_note: str | None = None,
    ) -> PromptTemplate:
        self._validate_template_variables(template, variables)
        tmpl = PromptTemplate(name=name, description=description, variables=variables)
        db.add(tmpl)
        db.commit()
        db.refresh(tmpl)
        version = PromptTemplateVersion(
            template_id=tmpl.id,
            version=1,
            template=template,
            is_active=True,
            change_note=self._normalize_change_note(change_note, "初始化版本"),
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        tmpl.active_version_id = version.id
        db.add(tmpl)
        db.commit()
        db.refresh(tmpl)
        return tmpl

    def get(self, template_id: int, db: Session) -> PromptTemplate | None:
        return db.query(PromptTemplate).filter(PromptTemplate.id == template_id).first()

    def get_by_name(self, name: str, db: Session) -> PromptTemplate | None:
        return db.query(PromptTemplate).filter(PromptTemplate.name == name).first()

    def get_default_template(self, name: str) -> dict | None:
        for item in DEFAULT_PROMPT_TEMPLATES:
            if item["name"] == name:
                return item
        return None

    def _active_version_number(self, tmpl: PromptTemplate) -> int | None:
        if not tmpl.active_version:
            return None
        return tmpl.active_version.version

    @staticmethod
    def _rollout_enabled(tmpl: PromptTemplate) -> bool:
        return bool(tmpl.rollout_version_id and tmpl.rollout_version and int(tmpl.rollout_percentage or 0) > 0)

    def _resolve_effective_version(
        self,
        tmpl: PromptTemplate,
        *,
        user_id: int | None = None,
    ) -> PromptTemplateVersion | None:
        active_version = tmpl.active_version
        if not self._rollout_enabled(tmpl):
            return active_version
        if user_id is None:
            return active_version
        rollout_percentage = max(0, min(int(tmpl.rollout_percentage or 0), 100))
        if rollout_percentage <= 0:
            return active_version
        bucket = self._stable_bucket(tmpl.name, user_id)
        if bucket < rollout_percentage:
            return tmpl.rollout_version
        return active_version

    def list_all(self, db: Session) -> list[PromptTemplate]:
        return db.query(PromptTemplate).order_by(PromptTemplate.created_at.desc()).all()

    def update(self, template_id: int, db: Session, **kwargs) -> PromptTemplate:
        tmpl = self.get(template_id, db)
        if not tmpl:
            raise ValueError("Template not found")
        change_note = kwargs.pop("change_note", None)
        template_text = kwargs.pop("template", None)
        next_variables = kwargs.get("variables", tmpl.variables)
        for key, value in kwargs.items():
            if hasattr(tmpl, key) and key not in {"active_version_id"}:
                setattr(tmpl, key, value)
        if template_text is not None:
            self._validate_template_variables(template_text, next_variables)
            latest_version = (
                db.query(PromptTemplateVersion)
                .filter(PromptTemplateVersion.template_id == tmpl.id)
                .order_by(PromptTemplateVersion.version.desc())
                .first()
            )
            next_version = (latest_version.version if latest_version else 0) + 1
            if latest_version and latest_version.is_active:
                latest_version.is_active = False
                db.add(latest_version)
            version = PromptTemplateVersion(
                template_id=tmpl.id,
                version=next_version,
                template=template_text,
                is_active=True,
                change_note=self._normalize_change_note(change_note, f"更新为 v{next_version}"),
            )
            db.add(version)
            db.commit()
            db.refresh(version)
            tmpl.active_version_id = version.id
        elif "variables" in kwargs and tmpl.active_version:
            self._validate_template_variables(tmpl.active_version.template, next_variables)
        db.commit()
        db.refresh(tmpl)
        return tmpl

    def list_versions(self, template_id: int, db: Session) -> list[PromptTemplateVersion]:
        return (
            db.query(PromptTemplateVersion)
            .filter(PromptTemplateVersion.template_id == template_id)
            .order_by(PromptTemplateVersion.version.desc())
            .all()
        )

    def activate_version(self, template_id: int, version_id: int, db: Session) -> PromptTemplate:
        tmpl = self.get(template_id, db)
        if not tmpl:
            raise ValueError("Template not found")
        target = (
            db.query(PromptTemplateVersion)
            .filter(
                PromptTemplateVersion.id == version_id,
                PromptTemplateVersion.template_id == template_id,
            )
            .first()
        )
        if not target:
            raise ValueError("Template version not found")
        versions = self.list_versions(template_id, db)
        current_active_id = tmpl.active_version_id
        for version in versions:
            version.is_active = version.id == target.id
            db.add(version)
        if current_active_id and current_active_id != target.id:
            tmpl.previous_active_version_id = current_active_id
        tmpl.active_version_id = target.id
        tmpl.rollout_version_id = None
        tmpl.rollout_percentage = 0
        tmpl.rollout_started_at = None
        db.add(tmpl)
        db.commit()
        db.refresh(tmpl)
        return tmpl

    def start_rollout(
        self,
        template_id: int,
        version_id: int,
        rollout_percentage: int,
        db: Session,
    ) -> PromptTemplate:
        tmpl = self.get(template_id, db)
        if not tmpl:
            raise ValueError("Template not found")
        if not tmpl.active_version_id:
            raise ValueError("Active template version not found")
        target = (
            db.query(PromptTemplateVersion)
            .filter(
                PromptTemplateVersion.id == version_id,
                PromptTemplateVersion.template_id == template_id,
            )
            .first()
        )
        if not target:
            raise ValueError("Template version not found")
        if target.id == tmpl.active_version_id:
            raise ValueError("Cannot rollout current active version")
        if rollout_percentage < 1 or rollout_percentage > 99:
            raise ValueError("Rollout percentage must be between 1 and 99")
        tmpl.rollout_version_id = target.id
        tmpl.rollout_percentage = rollout_percentage
        tmpl.rollout_started_at = utc_now()
        db.add(tmpl)
        db.commit()
        db.refresh(tmpl)
        return tmpl

    def rollback(
        self,
        template_id: int,
        db: Session,
        target_version_id: int | None = None,
    ) -> PromptTemplate:
        tmpl = self.get(template_id, db)
        if not tmpl:
            raise ValueError("Template not found")

        if target_version_id is not None:
            return self.activate_version(template_id, target_version_id, db)

        if self._rollout_enabled(tmpl):
            tmpl.rollout_version_id = None
            tmpl.rollout_percentage = 0
            tmpl.rollout_started_at = None
            db.add(tmpl)
            db.commit()
            db.refresh(tmpl)
            return tmpl

        if tmpl.previous_active_version_id is None:
            raise ValueError("Rollback target not found")
        return self.activate_version(template_id, tmpl.previous_active_version_id, db)

    def delete(self, template_id: int, db: Session) -> bool:
        tmpl = self.get(template_id, db)
        if not tmpl:
            raise ValueError("Template not found")
        db.delete(tmpl)
        db.commit()
        return True

    def render(self, template_id: int, db: Session, **variables) -> str:
        tmpl = self.get(template_id, db)
        if not tmpl:
            raise ValueError("Template not found")
        user_id = variables.pop("user_id", None)
        resolved_version = self._resolve_effective_version(tmpl, user_id=user_id)
        template_text = resolved_version.template if resolved_version else None
        if template_text is None:
            raise ValueError("Template version not found")
        return self.render_text(template_text, **variables)

    def render_text(self, template: str, **variables) -> str:
        rendered = template
        for key, value in variables.items():
            rendered = rendered.replace(f"{{{key}}}", str(value))
        return rendered

    def render_by_name(self, name: str, db: Session | None = None, user_id: int | None = None, **variables) -> str:
        template_text = None
        if db is not None:
            tmpl = self.get_by_name(name, db)
            if tmpl:
                resolved_version = self._resolve_effective_version(tmpl, user_id=user_id)
                if resolved_version:
                    template_text = resolved_version.template
        else:
            try:
                runtime_db = SessionLocal()
                try:
                    tmpl = self.get_by_name(name, runtime_db)
                    if tmpl:
                        resolved_version = self._resolve_effective_version(tmpl, user_id=user_id)
                        if resolved_version:
                            template_text = resolved_version.template
                finally:
                    runtime_db.close()
            except Exception:
                pass
        if template_text is None:
            default_template = self.get_default_template(name)
            if not default_template:
                raise ValueError(f"Prompt template not found: {name}")
            template_text = default_template["template"]
        return self.render_text(template_text, **variables)

    def seed_defaults(self, db: Session) -> int:
        count = 0
        for template_data in DEFAULT_PROMPT_TEMPLATES:
            if not self.get_by_name(template_data["name"], db):
                self.create(**template_data, db=db)
                count += 1
        return count

    def get_template_metadata(self, name: str, user_id: int | None = None) -> dict:
        default_template = self.get_default_template(name)
        metadata = {
            "prompt_template": name if default_template else None,
            "prompt_version": 1 if default_template else None,
            "variables_schema": self._parse_variables_schema(default_template.get("variables")) if default_template else [],
            "is_rollout": False,
        }

        try:
            db = SessionLocal()
            try:
                tmpl = self.get_by_name(name, db)
                if tmpl:
                    resolved_version = self._resolve_effective_version(tmpl, user_id=user_id)
                else:
                    resolved_version = None
                if tmpl and resolved_version:
                    metadata["prompt_template"] = tmpl.name
                    metadata["prompt_version"] = resolved_version.version
                    metadata["variables_schema"] = self._parse_variables_schema(tmpl.variables)
                    metadata["is_rollout"] = bool(tmpl.rollout_version_id and resolved_version.id == tmpl.rollout_version_id)
            finally:
                db.close()
        except Exception:
            pass
        return metadata

    def serialize_template(self, tmpl: PromptTemplate) -> dict:
        active_version = tmpl.active_version
        previous_active_version = tmpl.previous_active_version
        rollout_version = tmpl.rollout_version if self._rollout_enabled(tmpl) else None
        versions = sorted(tmpl.versions, key=lambda item: item.version, reverse=True)
        variables_schema = self._parse_variables_schema(tmpl.variables)
        return {
            "id": tmpl.id,
            "name": tmpl.name,
            "description": tmpl.description,
            "variables": tmpl.variables,
            "variables_schema": variables_schema,
            "template": active_version.template if active_version else "",
            "change_note": active_version.change_note if active_version else None,
            "active_version_id": active_version.id if active_version else None,
            "active_version_number": active_version.version if active_version else None,
            "previous_active_version_id": previous_active_version.id if previous_active_version else None,
            "previous_active_version_number": previous_active_version.version if previous_active_version else None,
            "rollout": (
                {
                    "version_id": rollout_version.id,
                    "version_number": rollout_version.version,
                    "percentage": int(tmpl.rollout_percentage or 0),
                    "started_at": tmpl.rollout_started_at,
                }
                if rollout_version
                else None
            ),
            "created_at": tmpl.created_at,
            "updated_at": tmpl.updated_at,
            "versions": [
                {
                    "id": version.id,
                    "template_id": version.template_id,
                    "version": version.version,
                    "template": version.template,
                    "is_active": version.is_active,
                    "is_rollout": bool(rollout_version and version.id == rollout_version.id),
                    "traffic_percentage": int(tmpl.rollout_percentage or 0) if rollout_version and version.id == rollout_version.id else 0,
                    "change_note": version.change_note,
                    "variables_schema": variables_schema,
                    "experiment_refs": self._load_prompt_experiment_refs(tmpl.name, version.version),
                    "created_at": version.created_at,
                    "updated_at": version.updated_at,
                }
                for version in versions
            ],
        }


prompt_service = PromptService()
