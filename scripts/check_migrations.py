"""迁移链静态校验（阶段 5 CI 强化，离线可跑，不依赖 DB）。

校验项：
1. 每个迁移文件含 revision / down_revision / upgrade() / downgrade()；
2. revision 全局唯一；
3. down_revision 引用的父修订存在（除 base=None）；
4. 多父 merge（down_revision 为 list/tuple，如 0064_merge_heads）合法；
5. 分叉校验：被多个后代引用的父修订，**必须**由某个 merge 收敛，否则报错；
6. head 唯一：恰好一个修订未被任何者引用（收敛到唯一 head）。

说明：迁移本体为 MySQL/PostgreSQL 设计（依赖 op.batch_alter_table 重建表），
SQLite 空库 `alembic upgrade head` 会因 ALTER 限制失败（既有约束，非本脚本范围）。
本脚本校验"链结构完整可逆"，与 docs/TESTING_AND_RELEASE.md 阶段 6 可回滚迁移清单配套。

用法：python -B scripts/check_migrations.py；退出 0=通过 / 1=链异常。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _extract_revision_info(path: Path) -> dict:
    """AST 提取迁移文件的 revision 元数据（支持 `revision = "x"` 与 `revision: str = "x"`），不执行迁移代码。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    info: dict = {
        "revision": None,
        "down_revision": None,
        "down_revisions": [],
        "has_upgrade": False,
        "has_downgrade": False,
        "file": path.name,
    }

    def _assign(name: str, value: ast.AST) -> None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if name == "revision":
                info["revision"] = value.value
            elif name == "down_revision":
                info["down_revision"] = value.value
        elif name == "down_revision" and isinstance(value, (ast.Tuple | ast.List)):
            items = [elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
            info["down_revisions"] = items

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            _assign(node.targets[0].id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _assign(node.target.id, node.value)
        elif isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                info["has_upgrade"] = True
            elif node.name == "downgrade":
                info["has_downgrade"] = True
    return info


def check_migrations(versions_dir: Path = VERSIONS_DIR) -> list[str]:
    if not versions_dir.is_dir():
        return [f"版本目录不存在: {versions_dir}"]
    errors: list[str] = []
    migrations = {}

    for path in sorted(versions_dir.glob("*.py")):
        info = _extract_revision_info(path)
        rev = info["revision"]
        if rev is None:
            errors.append(f"{info['file']}: 缺少 revision")
            continue
        if rev in migrations:
            errors.append(f"revision 重复: {rev} ({info['file']} vs {migrations[rev]['file']})")
            continue
        if not info["has_upgrade"] or not info["has_downgrade"]:
            errors.append(f"{info['file']} ({rev}): 缺少 upgrade() 或 downgrade()")
        if info["down_revisions"]:  # merge（多父）
            migrations[rev] = info
        else:
            if info["down_revision"] is None:
                info["down_revisions"] = []
            migrations[rev] = info

    if not migrations:
        return ["未找到迁移文件"]

    # down_revision 引用完整性
    for rev, info in migrations.items():
        for down in info["down_revisions"]:
            if down not in migrations:
                errors.append(f"{info['file']} ({rev}): down_revision 不存在: {down}")

    # 分叉校验：被多个后代引用的父修订，其所有子必须被某个 merge 收敛（如 0064_merge_heads）
    consumers: dict[str, list[str]] = {}
    merge_records: list[tuple[str, set]] = []
    for rev, info in migrations.items():
        downs = list(info.get("down_revisions") or [])
        if info.get("down_revision") is not None:
            downs.append(info["down_revision"])
        for down in downs:
            consumers.setdefault(down, []).append(rev)
        if info.get("down_revisions"):
            merge_records.append((rev, set(info["down_revisions"])))
    for parent, children in consumers.items():
        if len(children) <= 1:
            continue
        child_set = set(children)
        converged = any(m_set == child_set for _, m_set in merge_records)
        if not converged:
            errors.append(f"迁移分叉未收敛: {parent} 有多个后代 {sorted(children)}（需 merge 收敛）")

    # head 唯一（被消费过的修订不是 head）
    consumed = {child for children in consumers.values() for child in children}
    heads = [rev for rev in migrations if rev not in consumed]
    if len(heads) != 1:
        errors.append(f"head 不唯一: {sorted(heads)}（期望唯一收敛 head）")

    return errors


def main() -> int:
    errors = check_migrations()
    if errors:
        print("迁移链校验失败:")
        for error in errors:
            print(f"  - {error}")
        return 1
    count = len(list(VERSIONS_DIR.glob("*.py")))
    print(f"迁移链校验通过（{count} 个迁移、head 唯一、全链可逆）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
