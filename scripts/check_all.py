"""一键门禁：本地一条命令复刻 CI 的全部门禁（后端全量 + 静态检查 + 契约 + 前端 + 任务 e2e + 冻结评测）。

用法：
    python scripts/check_all.py                 # 默认门禁（含任务中心 Playwright e2e）
    python scripts/check_all.py --e2e           # 追加全量 Playwright e2e（需后端已在 :8001 运行）
    python scripts/check_all.py --list          # 只列出门禁清单
    python scripts/check_all.py --only api,ruff # 只跑指定门禁
    python scripts/check_all.py --skip backend  # 跳过指定门禁
    python scripts/check_all.py --strict        # 缺工具（ruff/npm）按失败处理（默认只告警）

设计约束（见 docs/TESTING_AND_RELEASE.md §3.1）：
- **串行**执行：eslint / node --test / vite build 与 Playwright 并发会抢 dev server，
  制造 `connect ECONNREFUSED` 假失败，所以这里不用线程池。
- 每段输出实时打到终端，同时落 `.gate_logs/<stage>.log`；失败时摘要给日志路径。
- 退出码：0=全通过（含 SKIP/WARN），1=有失败，2=用法错误，130=用户中断。
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = ROOT_DIR / ".gate_logs"
EVAL_REPORT_NAME = "generation_eval_report.json"

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
WARNED = "warned"

EVAL_MIN_CASES = 100
EVAL_THRESHOLD = 0.95


@dataclass(frozen=True)
class Stage:
    """一个门禁：命令用字符串模板，运行时再替换解释器路径。"""

    id: str
    title: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout: int = 900
    requires: tuple[str, ...] = ()
    tool: str | None = None  # 依赖的外部工具：ruff / npm
    e2e_only: bool = False


STAGES: tuple[Stage, ...] = (
    Stage(
        id="ruff",
        title="后端静态检查（真实错误）",
        command=(
            "{python}", "-m", "ruff", "check",
            "--select", "E9,F821,F823,F632,F706,F811",
            "app", "scripts", "alembic", "tests", "eval",
        ),
        tool="ruff",
        timeout=300,
    ),
    Stage(
        id="migrations",
        title="迁移链静态校验",
        command=("{python}", "-B", "scripts/check_migrations.py"),
        tool="python",
        timeout=120,
    ),
    Stage(
        id="openapi",
        title="OpenAPI 契约快照",
        command=("{python}", "-B", "scripts/check_openapi_contract.py"),
        tool="python",
        timeout=300,
    ),
    Stage(
        id="eval",
        title="冻结生成评测（不走 LLM）",
        command=(
            "{python}", "-B", "eval/run_generation_eval.py",
            "--no-llm", "--output", f".gate_logs/{EVAL_REPORT_NAME}",
        ),
        tool="python",
        timeout=900,
    ),
    Stage(
        id="backend",
        title="后端全量回归（pytest tests/）",
        command=("{python}", "-m", "pytest", "tests/", "-q", "--no-header", "-p", "no:cacheprovider"),
        timeout=3600,
    ),
    Stage(
        id="frontend-lint",
        title="前端 lint（eslint src）",
        command=("{npm}", "run", "lint"),
        cwd="frontend",
        tool="npm",
        timeout=600,
    ),
    Stage(
        id="frontend-unit",
        title="前端单测（node --test）",
        command=("{npm}", "run", "test:unit"),
        cwd="frontend",
        tool="npm",
        timeout=600,
    ),
    Stage(
        id="frontend-e2e-tasks",
        title="Playwright e2e（任务中心 / 通知偏好，全 mock）",
        command=(
            "{npm}", "run", "test:e2e", "--",
            "tests/e2e/tasks-flow.spec.js",
            "tests/e2e/notification-preferences.spec.js",
            "--reporter=list",
        ),
        cwd="frontend",
        tool="npm",
        timeout=900,
    ),
    Stage(
        id="frontend-build",
        title="前端构建（vite build）",
        command=("{npm}", "run", "build"),
        cwd="frontend",
        tool="npm",
        timeout=900,
    ),
    Stage(
        id="frontend-types",
        title="API 类型新鲜度（types:check）",
        command=("{npm}", "run", "types:check"),
        cwd="frontend",
        tool="npm",
        timeout=300,
    ),
    Stage(
        id="frontend-bundle",
        title="包体预算（bundle:check）",
        command=("{npm}", "run", "bundle:check"),
        cwd="frontend",
        tool="npm",
        requires=("frontend-build",),
        timeout=300,
    ),
    Stage(
        id="e2e",
        title="Playwright e2e（全量，需后端 :8001）",
        command=("{npm}", "run", "test:e2e"),
        cwd="frontend",
        tool="npm",
        timeout=1800,
        e2e_only=True,
    ),
)


@dataclass
class Result:
    stage: Stage
    status: str
    duration: float = 0.0
    detail: str = ""
    log_path: Path | None = None


def _resolve_python() -> str:
    """优先用仓库自带 venv 的解释器，避免用系统 python 跑出一堆缺依赖的假失败。"""
    override = os.environ.get("CHECK_ALL_PYTHON")
    if override:
        return override
    candidates = [
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


def _external_python() -> str | None:
    """工作区外的解释器（由 .venv/pyvenv.cfg 的 home 推出），用于低完整性沙箱下编排。"""
    config = ROOT_DIR / ".venv" / "pyvenv.cfg"
    if not config.exists():
        return None
    for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        if key.strip() != "home":
            continue
        home = Path(value.strip())
        exe = home / ("python.exe" if os.name == "nt" else "python")
        return str(exe) if exe.exists() else None
    return None


def _is_low_integrity() -> bool:
    """Windows 沙箱会把工作区内的解释器降为 Low Integrity：写不了已有目录，npm 构建会 EPERM。"""
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(["whoami", "/groups"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    output = (completed.stdout or b"").decode("utf-8", errors="replace")
    return "S-1-16-4096" in output


def _mark_low_integrity(path: Path) -> None:
    """给临时目录打 Low 标签，让低完整性子进程也能写（仅 scratch 目录，失败忽略）。"""
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/setintegritylevel", "(OI)(CI)Low"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _resolve_npm() -> str | None:
    return shutil.which("npm")


def _tool_available(tool: str | None, python: str) -> bool:
    if tool in (None, "python"):
        return True
    if tool == "npm":
        return _resolve_npm() is not None
    if tool == "ruff":
        try:
            probe = subprocess.run(
                [python, "-m", "ruff", "--version"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return probe.returncode == 0
    return True


def _command_for(stage: Stage, python: str) -> list[str]:
    npm = _resolve_npm() or "npm"
    return [part.replace("{python}", python).replace("{npm}", npm) for part in stage.command]


def _child_env(tmp_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            # 统一把临时目录钉在仓库内（受限 ACL / 只读挂载的机器上系统 TEMP 可能不可写），
            # 顺带避免 chromadb 之类把索引写进用户 TEMP。
            "TEMP": str(tmp_dir),
            "TMP": str(tmp_dir),
            "TMPDIR": str(tmp_dir),
            "CHROMA_PERSIST_DIR": str(tmp_dir / "chroma"),
        }
    )
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("NO_COLOR", "1")  # 日志里不留 ANSI 转义
    env.setdefault("FORCE_COLOR", "0")
    return env


def _configure_stdio() -> None:
    """Windows 管道下 stdout 默认按 cp936 编码，中文会变乱码；统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _run_stage(stage: Stage, python: str, logs_dir: Path, tmp_dir: Path) -> Result:
    command = _command_for(stage, python)
    cwd = ROOT_DIR / stage.cwd
    log_path = logs_dir / f"{stage.id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n$ {' '.join(command)}")
    print(f"  (cwd: {cwd})  输出同时写入 {log_path.relative_to(ROOT_DIR)}", flush=True)

    started = time.monotonic()
    timed_out = threading.Event()
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        log.write(f"$ {' '.join(command)}\n(cwd: {cwd})\n\n")
        log.flush()
        try:
            proc = subprocess.Popen(  # noqa: S603 - 命令全部来自本文件的常量模板
                command,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                env=_child_env(tmp_dir),
            )
        except OSError as exc:
            return Result(stage, FAILED, 0.0, f"启动失败：{exc}", log_path)

        def _kill() -> None:
            timed_out.set()
            try:
                proc.kill()
            except OSError:
                pass

        watchdog = threading.Timer(stage.timeout, _kill)
        watchdog.start()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            assert proc.stdout is not None
            # 用原始字节流「有多少读多少」：pytest -q 的进度点没有换行，按行/按块读都会憋住。
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if not text:
                    continue
                sys.stdout.write(text)
                sys.stdout.flush()
                log.write(text)
                log.flush()
            tail = decoder.decode(b"", final=True)
            if tail:
                sys.stdout.write(tail)
                log.write(tail)
            returncode = proc.wait()
        except KeyboardInterrupt:
            proc.kill()  # 别把子进程（pytest/npm）留成孤儿
            raise
        finally:
            watchdog.cancel()

    duration = time.monotonic() - started
    if timed_out.is_set():
        return Result(stage, FAILED, duration, f"超时（>{stage.timeout}s）", log_path)
    if returncode != 0:
        return Result(stage, FAILED, duration, f"退出码 {returncode}", log_path)
    if stage.id == "eval":
        problems = _check_eval_report(logs_dir / EVAL_REPORT_NAME)
        if problems:
            for problem in problems:
                print(f"  [FAIL] {problem}")
            return Result(stage, FAILED, duration, "; ".join(problems), log_path)
    return Result(stage, PASSED, duration, "", log_path)


def validate_generation_eval(
    report: dict,
    *,
    threshold: float = EVAL_THRESHOLD,
    min_cases: int = EVAL_MIN_CASES,
) -> list[str]:
    """冻结评测门槛（与 CI eval-regression job 一致），返回未通过项。"""
    problems: list[str] = []
    summary = report.get("summary") or {}
    total = int(summary.get("total_cases") or 0)
    rate = float(summary.get("overall_pass_rate") or 0.0)
    if total < min_cases:
        problems.append(f"评测集规模不足 {min_cases} 题：{total}")
    if rate < threshold:
        problems.append(f"整体通过率 {rate:.4f} 低于门槛 {threshold}")
    regression = report.get("regression") or {}
    if regression.get("enabled") and int(regression.get("total_cases") or 0) > 0:
        reg_rate = float(regression.get("pass_rate") or 0.0)
        if reg_rate < threshold:
            problems.append(f"AI-2 回流回归通过率 {reg_rate:.4f} 低于门槛 {threshold}")
    return problems


def format_eval_summary(report: dict) -> str:
    summary = report.get("summary") or {}
    lines = [
        f"整体通过率 {summary.get('overall_pass_rate')} "
        f"({summary.get('total_pass')}/{summary.get('total_cases')})",
    ]
    consultation = report.get("consultation") or {}
    for layer in ("refusal", "inactive_source"):
        item = consultation.get(layer) or {}
        if item:
            lines.append(f"{layer}: {item.get('pass_rate')} ({item.get('total_cases')} 题)")
    regression = report.get("regression") or {}
    if regression.get("enabled") and int(regression.get("total_cases") or 0) > 0:
        lines.append(f"AI-2 回流回归: {regression.get('pass_rate')} ({regression.get('total_cases')} 题)")
    return " | ".join(lines)


def _check_eval_report(report_path: Path) -> list[str]:
    if not report_path.exists():
        return [f"评测报告未生成：{report_path.relative_to(ROOT_DIR)}"]
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"评测报告不可读：{exc}"]
    print(f"  {format_eval_summary(report)}")
    return validate_generation_eval(report)


def select_stages(
    *,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    e2e: bool = False,
) -> list[Stage]:
    known = {stage.id for stage in STAGES}
    for name in (only or []) + (skip or []):
        if name not in known:
            raise ValueError(f"未知门禁：{name}（可选：{', '.join(sorted(known))}）")
    if only:
        wanted = set(only)
        return [stage for stage in STAGES if stage.id in wanted]
    skipped = set(skip or [])
    return [
        stage
        for stage in STAGES
        if stage.id not in skipped and (e2e or not stage.e2e_only)
    ]


def _print_plan(stages: list[Stage]) -> None:
    print(f"共 {len(stages)} 个门禁：")
    for index, stage in enumerate(stages, start=1):
        print(f"  {index:>2}. {stage.id:<16} {stage.title}")


def _summary(results: list[Result], total_duration: float) -> str:
    lines = ["", "=" * 78, "门禁结果", "=" * 78]
    for result in results:
        mark = {PASSED: "[OK]  ", FAILED: "[FAIL]", SKIPPED: "[SKIP]", WARNED: "[WARN]"}[result.status]
        detail = f"  {result.detail}" if result.detail else ""
        lines.append(f"{mark} {result.stage.id:<16} {result.duration:>7.1f}s  {result.stage.title}{detail}")
    failed = [result for result in results if result.status == FAILED]
    warned = [result for result in results if result.status == WARNED]
    skipped = [result for result in results if result.status == SKIPPED]
    lines.append("-" * 78)
    lines.append(
        f"通过 {len(results) - len(failed) - len(warned) - len(skipped)} / "
        f"失败 {len(failed)} / 告警 {len(warned)} / 跳过 {len(skipped)}，"
        f"总耗时 {total_duration / 60:.1f} 分钟"
    )
    if failed:
        lines.append("失败门禁日志：")
        for result in failed:
            if result.log_path:
                lines.append(f"  - {result.stage.id}: {result.log_path.relative_to(ROOT_DIR)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="本地一键门禁（与 CI 对齐）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--list", action="store_true", help="只列出门禁，不执行")
    parser.add_argument("--only", default="", help="只跑这些门禁（逗号分隔）")
    parser.add_argument("--skip", default="", help="跳过这些门禁（逗号分隔）")
    parser.add_argument("--e2e", action="store_true", help="追加全量 Playwright e2e（需后端在 :8001 运行）")
    parser.add_argument("--strict", action="store_true", help="缺工具/告警按失败处理")
    parser.add_argument(
        "--allow-low-integrity",
        action="store_true",
        help="低完整性沙箱下仍然执行（前端构建大概率 EPERM，仅供排查）",
    )
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOG_DIR), help="日志目录（默认 .gate_logs/）")
    args = parser.parse_args(argv)

    only = [item.strip() for item in args.only.split(",") if item.strip()]
    skip = [item.strip() for item in args.skip.split(",") if item.strip()]
    if only and skip:
        parser.error("--only 与 --skip 不能同时使用")
    try:
        stages = select_stages(only=only, skip=skip, e2e=args.e2e)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    python = _resolve_python()
    print("仓库:", ROOT_DIR)
    print("解释器:", python)
    print("npm:", _resolve_npm() or "未找到")
    _print_plan(stages)
    if args.list:
        return 0

    if _is_low_integrity() and not args.allow_low_integrity:
        external = _external_python()
        print(
            "\n[WARN] 当前解释器处于 Low Integrity（Windows 沙箱把工作区内的 .venv 降权）：\n"
            "       npm/vite 会因写不了 frontend/ 而 EPERM，这不是代码问题。\n"
            "       请用工作区外的解释器运行同一脚本（它仍会用 .venv 跑 pytest）：",
            flush=True,
        )
        if external:
            print(f'       "{external}" "{Path(__file__).resolve()}"', flush=True)
        else:
            print("       （未找到 .venv/pyvenv.cfg 指向的外部解释器）", flush=True)
        print("       仅跑后端门禁：加 --skip frontend-build,frontend-bundle", flush=True)
        return 2

    logs_dir = Path(args.logs_dir)
    if not logs_dir.is_absolute():
        logs_dir = ROOT_DIR / logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = logs_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # 低完整性子进程（pytest 由 .venv 解释器启动）要能写 scratch，给这两个目录打 Low 标签。
    _mark_low_integrity(logs_dir)

    results: list[Result] = []
    started = time.monotonic()
    try:
        for index, stage in enumerate(stages, start=1):
            print(f"\n[{index}/{len(stages)}] {stage.id} — {stage.title}", flush=True)
            blocked = [
                result.stage.id
                for result in results
                if result.stage.id in stage.requires and result.status == FAILED
            ]
            if blocked:
                results.append(
                    Result(stage, SKIPPED, 0.0, f"依赖未通过：{', '.join(blocked)}")
                )
                continue
            if not _tool_available(stage.tool, python):
                status = FAILED if args.strict else WARNED
                hint = "未安装 ruff（pip install -r requirements-dev.txt）" if stage.tool == "ruff" else "未找到 npm/Node"
                results.append(Result(stage, status, 0.0, hint))
                print(f"  [{'FAIL' if status == FAILED else 'WARN'}] {hint}", flush=True)
                continue
            results.append(_run_stage(stage, python, logs_dir, tmp_dir))
    except KeyboardInterrupt:
        print("\n已中断。")
        results.append(Result(Stage("interrupted", "用户中断", ()), SKIPPED, 0.0, "Ctrl-C"))
        return 130
    finally:
        print(_summary(results, time.monotonic() - started))

    failed = [result for result in results if result.status == FAILED]
    warned = [result for result in results if result.status == WARNED]
    return 1 if failed or (args.strict and warned) else 0


if __name__ == "__main__":
    sys.exit(main())
