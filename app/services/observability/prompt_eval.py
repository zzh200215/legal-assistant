"""提示词与实验簇：提示词灰度/流量概览、离线实验总览。"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.llm_call_log import LLMCallLog
from app.models.prompt import PromptTemplate
from eval.bundle_utils import DEFAULT_BASELINE_SNAPSHOT_PATH, DEFAULT_OUTPUT_DIR


class PromptEvalMixin:
    @staticmethod
    def _load_json_artifact(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _artifact_meta(path: Path) -> dict:
        if not path.exists():
            return {
                "exists": False,
                "path": str(path),
                "updated_at": None,
            }
        return {
            "exists": True,
            "path": str(path),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }

    def get_prompt_rollout_overview(self, db: Session) -> dict:
        rows = db.query(PromptTemplate).order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc()).all()
        items = []
        active_rollout_count = 0
        for row in rows:
            rollout = None
            if row.rollout_version_id and row.rollout_version and int(row.rollout_percentage or 0) > 0:
                active_rollout_count += 1
                rollout = {
                    "version_id": row.rollout_version.id,
                    "version_number": row.rollout_version.version,
                    "percentage": int(row.rollout_percentage or 0),
                    "started_at": row.rollout_started_at,
                }
            items.append(
                {
                    "template_id": row.id,
                    "name": row.name,
                    "active_version_id": row.active_version_id,
                    "active_version_number": row.active_version.version if row.active_version else None,
                    "previous_active_version_id": row.previous_active_version_id,
                    "previous_active_version_number": row.previous_active_version.version if row.previous_active_version else None,
                    "rollout": rollout,
                    "updated_at": row.updated_at,
                }
            )
        return {
            "total_templates": len(items),
            "active_rollout_count": active_rollout_count,
            "items": items,
        }

    def get_prompt_traffic_overview(self, db: Session, days: int = 30, limit: int = 100) -> dict:
        since = utc_now() - timedelta(days=days)
        rows = (
            db.query(LLMCallLog)
            .filter(
                LLMCallLog.created_at >= since,
                LLMCallLog.prompt_template.isnot(None),
                LLMCallLog.prompt_version.isnot(None),
            )
            .order_by(LLMCallLog.created_at.desc(), LLMCallLog.id.desc())
            .limit(5000)
            .all()
        )
        by_prompt: dict[tuple[str, int], dict] = {}
        for row in rows:
            key = (row.prompt_template or "unknown", int(row.prompt_version or 0))
            item = by_prompt.setdefault(
                key,
                {
                    "prompt_template": key[0],
                    "prompt_version": key[1],
                    "calls": 0,
                    "failed_calls": 0,
                    "last_called_at": row.created_at,
                },
            )
            item["calls"] += 1
            if row.status != "success":
                item["failed_calls"] += 1
            if row.created_at and (item["last_called_at"] is None or row.created_at > item["last_called_at"]):
                item["last_called_at"] = row.created_at
        items = sorted(by_prompt.values(), key=lambda item: (item["calls"], item["last_called_at"] or datetime.min), reverse=True)
        return {
            "days": days,
            "total_prompt_versions": len(items),
            "items": items[:limit],
        }

    def get_experiment_overview(self, db: Session, days: int = 30) -> dict:
        output_dir = DEFAULT_OUTPUT_DIR
        summary_path = output_dir / "summary.json"
        baseline_path = DEFAULT_BASELINE_SNAPSHOT_PATH
        summary_payload = self._load_json_artifact(summary_path)
        baseline_payload = self._load_json_artifact(baseline_path)
        summary_rows = summary_payload.get("experiments") if isinstance(summary_payload.get("experiments"), list) else []
        baseline_config = ((baseline_payload.get("baseline") or {}).get("effective_config") or {}) if baseline_payload else {}

        experiments = []
        degraded_experiment_count = 0
        for row in summary_rows:
            effective_config = row.get("effective_config") or {}
            summary = row.get("summary") or {}
            baseline_delta = row.get("baseline_delta") or {}
            regression_metrics = []
            for metric in ("hit_at_k", "citation_accuracy", "refusal_accuracy"):
                delta = baseline_delta.get(metric)
                if delta is not None and delta < 0:
                    regression_metrics.append(metric)
            badcase_delta = baseline_delta.get("badcase_count")
            if badcase_delta is not None and badcase_delta > 0:
                regression_metrics.append("badcase_count")
            config_drift = []
            for field in (
                "top_k",
                "confidence_threshold",
                "min_recall_candidates",
                "recall_multiplier",
                "query_variant_limit",
                "context_neighbor_window",
                "context_max_chunks",
                "prompt_template",
                "prompt_version",
            ):
                if baseline_config and effective_config.get(field) != baseline_config.get(field):
                    config_drift.append(
                        {
                            "field": field,
                            "baseline": baseline_config.get(field),
                            "current": effective_config.get(field),
                        }
                    )
            if regression_metrics:
                degraded_experiment_count += 1
            experiments.append(
                {
                    "name": row.get("name") or "unnamed",
                    "effective_config": effective_config,
                    "summary": summary,
                    "baseline_delta": baseline_delta,
                    "badcase_count": int(row.get("badcase_count") or 0),
                    "badcase_path": row.get("badcase_path"),
                    "regression_metrics": regression_metrics,
                    "config_drift": config_drift,
                }
            )

        experiments.sort(
            key=lambda item: (
                -float((item.get("summary") or {}).get("citation_accuracy") or 0),
                -float((item.get("summary") or {}).get("hit_at_k") or 0),
                item.get("name") or "",
            )
        )
        rollout_overview = self.get_prompt_rollout_overview(db)
        prompt_traffic = self.get_prompt_traffic_overview(db, days=days, limit=50)
        return {
            "artifact_status": {
                "output_dir": str(output_dir),
                "summary": self._artifact_meta(summary_path),
                "baseline_snapshot": self._artifact_meta(baseline_path),
            },
            "summary": {
                "dataset_size": int(summary_payload.get("dataset_size") or 0),
                "experiment_count": int(summary_payload.get("experiment_count") or len(experiments)),
                "baseline_experiment": summary_payload.get("baseline_experiment"),
                "bundle_meta": summary_payload.get("bundle_meta") or {},
                "baseline_snapshot": baseline_payload.get("baseline") or {},
                "degraded_experiment_count": degraded_experiment_count,
            },
            "experiments": experiments,
            "rollouts": rollout_overview,
            "prompt_traffic": prompt_traffic,
        }

