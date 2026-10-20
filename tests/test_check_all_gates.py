"""scripts/check_all.py（一键门禁）的清单定义与门槛逻辑。

只测纯逻辑（门禁表、选择规则、冻结评测门槛、--list/用法错误），
不真的跑 pytest/npm —— 那属于门禁自身，由一键脚本执行时验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.check_all as check_all
from scripts.check_all import (
    STAGES,
    main,
    select_stages,
    validate_generation_eval,
)


def _report(*, total=120, rate=1.0, regression=None) -> dict:
    report = {
        "summary": {"total_cases": total, "total_pass": int(total * rate), "overall_pass_rate": rate},
        "consultation": {
            "refusal": {"pass_rate": 1.0, "total_cases": 20},
            "inactive_source": {"pass_rate": 1.0, "total_cases": 10},
        },
    }
    if regression is not None:
        report["regression"] = regression
    return report


def test_stage_ids_are_unique_and_commands_well_formed():
    ids = [stage.id for stage in STAGES]
    assert len(ids) == len(set(ids))
    for stage in STAGES:
        assert stage.command, f"{stage.id} 缺少命令"
        assert stage.command[0].startswith("{"), f"{stage.id} 的命令应以 {{python}}/{{npm}} 开头"
        for requirement in stage.requires:
            assert requirement in ids, f"{stage.id} 依赖了不存在的门禁 {requirement}"


def test_default_selection_excludes_e2e_until_opted_in():
    default_ids = [stage.id for stage in select_stages()]
    assert "backend" in default_ids
    assert "e2e" not in default_ids

    with_e2e = [stage.id for stage in select_stages(e2e=True)]
    assert "e2e" in with_e2e


def test_only_and_skip_filtering():
    only = [stage.id for stage in select_stages(only=["ruff", "openapi"])]
    assert only == ["ruff", "openapi"]

    skipped = {stage.id for stage in select_stages(skip=["backend", "e2e"])}
    assert "backend" not in skipped
    assert "ruff" in skipped


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError, match="未知门禁"):
        select_stages(only=["nope"])


def test_eval_thresholds_pass():
    assert validate_generation_eval(_report()) == []
    assert (
        validate_generation_eval(
            _report(regression={"enabled": True, "total_cases": 10, "pass_rate": 0.96})
        )
        == []
    )


def test_eval_thresholds_flag_low_rate_and_small_dataset():
    low_rate = validate_generation_eval(_report(rate=0.5))
    assert any("整体通过率" in problem for problem in low_rate)

    small = validate_generation_eval(_report(total=10))
    assert any("规模不足" in problem for problem in small)


def test_eval_thresholds_flag_regression_layer():
    problems = validate_generation_eval(
        _report(regression={"enabled": True, "total_cases": 10, "pass_rate": 0.8})
    )
    assert any("回流回归" in problem for problem in problems)


def test_list_flag_prints_stages_without_running(capsys):
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    for stage in STAGES:
        if stage.e2e_only:
            continue
        assert stage.id in out
    assert "e2e" not in out  # e2e 需显式 --e2e

    assert main(["--list", "--e2e"]) == 0
    assert "e2e" in capsys.readouterr().out


def test_usage_error_for_unknown_only(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--only", "nope"])
    assert excinfo.value.code == 2
    assert "未知门禁" in capsys.readouterr().err


def test_external_python_resolution_is_safe():
    external = check_all._external_python()
    assert external is None or Path(external).exists()


def test_low_integrity_preflight_blocks_with_hint(monkeypatch, capsys):
    monkeypatch.setattr(check_all, "_is_low_integrity", lambda: True)
    monkeypatch.setattr(check_all, "_external_python", lambda: r"C:\Python311\python.exe")

    assert check_all.main(["--only", "openapi"]) == 2
    out = capsys.readouterr().out
    assert "Low Integrity" in out
    assert "check_all.py" in out

    # 显式放行时继续执行（这里用未知门禁让它在真正跑之前就退出，避免真的执行构建）
    monkeypatch.setattr(check_all, "select_stages", lambda **_: [])
    assert check_all.main(["--only", "openapi", "--allow-low-integrity"]) == 0
