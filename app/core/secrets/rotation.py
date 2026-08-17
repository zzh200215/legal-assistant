"""密钥轮换受控摘除门禁（P1-A，纯函数，无 DB/IO）。

``validate_key_retirement`` 用于 `scripts/rotate_encryption_key.py --retire`：
任一条件不满足即返回拒绝原因（fail-closed），防止直接导致历史数据不可解密。

门禁：
1. 版本必须存在于密钥环；
2. 不能摘除当前激活版本；
3. 全表必须可解密（无失败样本）；
4. 不得有该版本密文残留（须先全量重加密到激活版本）。
"""

from __future__ import annotations

from typing import Any


def validate_key_retirement(
    *,
    version: str,
    ring: dict[str, str],
    active_version: str,
    column_state: dict[str, dict[str, Any]],
    decrypt_failures: list[str],
) -> list[str]:
    """返回拒绝原因列表；空列表 = 可安全摘除该版本。"""
    reasons: list[str] = []
    if version not in ring:
        reasons.append(f"版本 {version} 不在当前密钥环中：{sorted(ring)}")
    if version == active_version:
        reasons.append(f"不能摘除激活版本 {version}：请先轮换到新版本")
    if decrypt_failures:
        reasons.append(f"存在不可解密行（{len(decrypt_failures)} 条），拒绝摘除")
    remaining = [
        f"{col_key}:{info.get('versions', {}).get(version, 0)}"
        for col_key, info in column_state.items()
        if int(info.get("versions", {}).get(version, 0)) > 0
    ]
    if remaining:
        reasons.append(
            f"仍有 {version} 版本密文残留（{len(remaining)} 列），请先重加密：{remaining[:5]}"
        )
    return reasons
