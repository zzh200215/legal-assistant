"""供应链可审计报告（P1-E）：SBOM + 许可证 + 过期依赖。

- CycloneDX SBOM：``cyclonedx-py -r requirements.txt --format json`` → sbom/backend-cyclonedx.json
- 许可证报告：``pip-licenses --format json`` → sbom/licenses.json + 简化 CSV
- 过期依赖：``pip list --outdated --format json`` → sbom/outdated.json（仅报告，不设门禁）
- 产物不含任何密钥/环境配置；SBOM 与许可证属于可审计产物，随锁文件一起提交。
- 本地与 CI 同命令：
      python -B scripts/dependency_report.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SBOM_DIR = ROOT / "sbom"
LOCK_FILE = ROOT / "requirements.lock"


def _run(cmd: list[str], *, cwd: Path | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)


def _require_tool(name: str) -> None:
    # 工具缺失时给出明确指引（CI 由 requirements-dev.lock 保证安装）
    if sys.platform == "win32":
        probe = ["where", name]
    else:
        probe = ["which", name]
    if _run(probe).returncode != 0:
        print(f"[dependency-report] 缺少工具 {name}：请安装 requirements-dev 依赖"
              f"（pip install -r requirements-dev.lock）", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    SBOM_DIR.mkdir(parents=True, exist_ok=True)

    # 1) CycloneDX SBOM（标准格式，产物无密钥/环境配置）
    #    基于 requirements.lock（精确锁定版本）而非 requirements.txt（范围约束），
    #    保证 SBOM 与 CI/部署实际安装的依赖集合一致。
    _require_tool("cyclonedx-py")
    sbom_out = SBOM_DIR / "backend-cyclonedx.json"
    # PYTHONUTF8=1：Windows 本地与 CI（Linux/UTF-8）行为一致，避免
    # cyclonedx-py 按 locale（GBK）解码含中文注释的 requirements 文件。
    utf8_env = dict(os.environ)
    utf8_env["PYTHONUTF8"] = "1"
    proc = _run([
        "cyclonedx-py", "requirements",
        "--of", "json", "-o", str(sbom_out),
        str(LOCK_FILE),
    ], cwd=ROOT, env=utf8_env)
    if proc.returncode != 0:
        print("[dependency-report] SBOM 生成失败:", proc.stderr[:2000], file=sys.stderr)
        return 2
    print(f"[dependency-report] SBOM -> {sbom_out.relative_to(ROOT)}")

    # 2) 许可证报告（JSON + 简化 CSV）
    _require_tool("pip-licenses")
    proc = _run(["pip-licenses", "--format", "json"], cwd=ROOT)
    if proc.returncode != 0:
        print("[dependency-report] 许可证采集失败:", proc.stderr[:2000], file=sys.stderr)
        return 2
    licenses = json.loads(proc.stdout or "[]")
    (SBOM_DIR / "licenses.json").write_text(
        json.dumps(licenses, ensure_ascii=False, indent=2), encoding="utf-8")
    copyleft = [item for item in licenses
                if any(mark in str(item.get("License", ""))
                       for mark in ("GPL", "AGPL", "LGPL", "MPL", "EPL", "SSPL"))]
    csv_lines = ["Package,Version,License"]
    for item in sorted(licenses, key=lambda x: str(x.get("Name", ""))):
        csv_lines.append(f"{item.get('Name', '')},{item.get('Version', '')},{item.get('License', '')}")
    (SBOM_DIR / "licenses.csv").write_text("\n".join(csv_lines), encoding="utf-8")
    print(f"[dependency-report] 许可证 -> sbom/licenses.csv（共 {len(licenses)} 个包，"
          f"copyleft/传染性标记 {len(copyleft)} 个）")
    for item in copyleft:
        print(f"    [license] {item.get('Name')} {item.get('Version')}: {item.get('License')}")

    # 3) 过期依赖（仅报告）
    proc = _run([sys.executable, "-m", "pip", "list", "--outdated", "--format", "json"])
    outdated = json.loads(proc.stdout or "[]") if proc.returncode == 0 else []
    (SBOM_DIR / "outdated.json").write_text(
        json.dumps(outdated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dependency-report] 过期依赖 -> sbom/outdated.json（{len(outdated)} 个，仅报告不设门禁）")
    return 0


if __name__ == "__main__":
    sys.exit(main())