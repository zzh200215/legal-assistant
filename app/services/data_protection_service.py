from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class SensitiveRule:
    code: str
    label: str
    severity: str
    pattern: re.Pattern
    mask: callable


def _mask_middle(value: str, *, keep_start: int = 3, keep_end: int = 4) -> str:
    value = str(value)
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return f"{value[:keep_start]}{'*' * max(4, len(value) - keep_start - keep_end)}{value[-keep_end:]}"


def _mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _mask_secret(value: str) -> str:
    prefix = value.split("_", 1)[0] if "_" in value else "secret"
    return f"{prefix}_***"


class DataProtectionService:
    """Rule-based PII/secret detector shared by model output and outbound DLP checks."""

    RULES = (
        SensitiveRule("cn_id_card", "身份证号", "high", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), lambda value: _mask_middle(value, keep_start=4, keep_end=4)),
        SensitiveRule("bank_card", "银行卡号", "high", re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)"), lambda value: _mask_middle(re.sub(r"[ -]", "", value), keep_start=0, keep_end=4)),
        SensitiveRule("mobile_phone", "手机号", "medium", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), lambda value: _mask_middle(value, keep_start=3, keep_end=4)),
        SensitiveRule("email_address", "邮箱地址", "medium", re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"), _mask_email),
        SensitiveRule("api_token", "访问令牌", "critical", re.compile(r"\b(?:sk|rk|AKIA|ghp|xoxb|Bearer)[_-]?[A-Za-z0-9]{16,}\b", re.IGNORECASE), _mask_secret),
        SensitiveRule("password_assignment", "密码字段", "critical", re.compile(r"(?i)\b(?:password|passwd|pwd|client_secret)\s*[:=]\s*[^\s,;]{6,}"), _mask_secret),
    )
    _SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}

    def inspect(self, text: str | None) -> dict:
        source = str(text or "")
        findings: list[dict] = []
        for rule in self.RULES:
            matches = list(rule.pattern.finditer(source))
            if not matches:
                continue
            examples: list[str] = []
            for match in matches[:3]:
                masked = rule.mask(match.group(0))
                if masked not in examples:
                    examples.append(masked)
            findings.append({
                "code": rule.code,
                "label": rule.label,
                "severity": rule.severity,
                "count": len(matches),
                "masked_examples": examples,
            })
        findings.sort(key=lambda item: (-self._SEVERITY_ORDER[item["severity"]], item["code"]))
        return {
            "findings": findings,
            "total_count": sum(item["count"] for item in findings),
            "max_severity": findings[0]["severity"] if findings else None,
        }

    def redact(self, text: str | None) -> dict:
        source = str(text or "")
        inspection = self.inspect(source)
        replacements: list[tuple[int, int, str]] = []
        for rule in self.RULES:
            for match in rule.pattern.finditer(source):
                replacements.append((match.start(), match.end(), rule.mask(match.group(0))))
        # Keep the longest match at an overlapping position; apply from right to left to preserve offsets.
        selected: list[tuple[int, int, str]] = []
        for candidate in sorted(replacements, key=lambda item: (item[0], -(item[1] - item[0]))):
            if any(not (candidate[1] <= item[0] or candidate[0] >= item[1]) for item in selected):
                continue
            selected.append(candidate)
        redacted = source
        for start, end, value in sorted(selected, key=lambda item: item[0], reverse=True):
            redacted = f"{redacted[:start]}{value}{redacted[end:]}"
        return {**inspection, "text": redacted, "redacted": bool(selected)}

    def should_block(self, inspection: dict, *, action: str) -> bool:
        if action != "block":
            return False
        return any(item.get("severity") in {"high", "critical"} for item in inspection.get("findings", []))

    @staticmethod
    def audit_summary(inspection: dict) -> str:
        counts = defaultdict(int)
        for item in inspection.get("findings", []):
            counts[str(item.get("code") or "unknown")] += int(item.get("count") or 0)
        return "; ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"


data_protection_service = DataProtectionService()
