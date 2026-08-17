"""依赖漏洞扫描门禁（P1-E）：基于 requirements.lock 运行 pip-audit。

策略（与 docs/dependency-supply-chain.md 一致）：
- 默认**任何已知漏洞即失败**（最严，符合"拒绝优先"原则）；如需按严重度分级，
  传 `--fail-on critical,high`（pip-audit 输出含 CVSS 时生效，否则全部按高危处理）。
- 豁免见 ``scripts/dependency_exemptions.json``（按漏洞 ID）：
      { "CVE-xxxx-xxxxx": { "reason": "等待上游修复/业务决策", "until": "2027-01-01", "who": "owner" } }
  - 必须带原因与截止日期；**过期未续即失败**；豁免只针对指定漏洞，不豁免整个包。
- 本地与 CI 同命令（requirements.lock 已含哈希，保证可复现）：
      python -B scripts/dependency_scan.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = ROOT / "requirements.lock"
EXEMPTIONS_FILE = ROOT / "scripts" / "dependency_exemptions.json"


def _load_exemptions() -> dict:
    if not EXEMPTIONS_FILE.exists():
        return {}
    try:
        data = json.loads(EXEMPTIONS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"[dependency-scan] exemptions 解析失败: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print("[dependency-scan] exemptions 必须是 JSON 对象", file=sys.stderr)
        sys.exit(2)
    return data


def _is_exemption_valid(item: dict, today: date) -> tuple[bool, str]:
    reason = str(item.get("reason") or "").strip()
    until_raw = str(item.get("until") or "").strip()
    if not reason:
        return False, "缺少 reason"
    if not until_raw:
        return False, "缺少 until（截止日期）"
    try:
        until = date.fromisoformat(until_raw)
    except ValueError:
        return False, f"until 格式非法（{until_raw}，应为 YYYY-MM-DD）"
    if until < today:
        return False, f"豁免已过期（{until_raw} < {today.isoformat()}），需续期或修复"
    return True, ""


def run_audit(fail_on: list[str]) -> int:
    today = date.today()
    exemptions = _load_exemptions()

    if not LOCK_FILE.exists():
        print(f"[dependency-scan] 缺少 {LOCK_FILE}，先执行 pip-compile 生成锁文件", file=sys.stderr)
        return 2

    cmd = [
        sys.executable, "-m", "pip_audit",
        "-r", str(LOCK_FILE),
        "--disable-pip",
        "--format", "json",
        "--no-deps",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # pip-audit 以 0/1 表示无/有漏洞；其他退出码表示工具故障
    try:
        report = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        print("[dependency-scan] pip-audit 输出无法解析；stderr:", proc.stderr[:2000], file=sys.stderr)
        return 2

    vulnerabilities = report.get("vulnerabilities") or []
    failures: list[dict] = []
    warnings: list[dict] = []
    for entry in vulnerabilities:
        vuln = entry.get("vulnerability") or {}
        vuln_id = vuln.get("id") or entry.get("id") or "unknown"
        severity = str(vuln.get("severity") or "").lower() or "unknown"
        summary = str(vuln.get("description") or "")[:180]
        name = entry.get("name") or ""
        fix_versions = (vuln.get("fix_versions") or []) or []

        if vuln_id in exemptions:
            valid, reason = _is_exemption_valid(exemptions[vuln_id], today)
            if valid:
                warnings.append({"id": vuln_id, "name": name, "status": f"豁免（{reason}）"})
                continue
            failures.append({"id": vuln_id, "name": name,
                             "status": f"豁免无效：{reason}", "summary": summary})
            continue

        # 分级：仅当存在明确严重度且不在 fail_on 名单内才降级为警告；
        # 未知严重度一律按失败处理（fail-closed）。
        if severity in {"low", "medium", "moderate"} and severity not in fail_on and "critical" not in fail_on:
            warnings.append({"id": vuln_id, "name": name,
                             "status": f"严重度 {severity}（未达门禁 {fail_on}）", "summary": summary})
            continue
        failures.append({"id": vuln_id, "name": name,
                         "status": f"严重度 {severity}；修复版本: {fix_versions or '无'}", "summary": summary})

    print(f"[dependency-scan] 漏洞总数={len(vulnerabilities)}；"
          f"门禁失败={len(failures)}；警告={len(warnings)}")
    for item in warnings:
        print(f"  [warn] {item['id']} {item['name']}: {item['status']}")
    for item in failures:
        print(f"  [FAIL] {item['id']} {item['name']}: {item['status']}")
        if item.get("summary"):
            print(f"         {item['summary']}")

    if failures:
        print("[dependency-scan] 门禁失败：存在未豁免漏洞（豁免过期/缺失），见上", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="依赖漏洞扫描门禁（pip-audit + 豁免）")
    parser.add_argument("--fail-on", default="critical,high",
                        help="视为失败的严重度（逗号分隔）；默认 critical,high；"
                             "pip-audit 无 CVSS 时按全部失败处理（fail-closed）")
    args = parser.parse_args()
    fail_on = [item.strip().lower() for item in args.fail_on.split(",") if item.strip()]
    return run_audit(fail_on)


if __name__ == "__main__":
    sys.exit(main())