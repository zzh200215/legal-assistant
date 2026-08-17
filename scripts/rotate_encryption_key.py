"""E-2 法律数据加密密钥轮换 / 存量加密。

对应用中所有 EncryptedText 列做一次全量重写：
- 仍为明文的存量行 -> 加密为当前激活版本（收尾惰性迁移遗留）
- 旧版本密文行 -> 重加密为当前激活版本
密钥以版本化密钥环 LEGAL_DATA_ENCRYPTION_KEYS_JSON 管理（经统一 SecretProvider 读取），
轮换时新旧密钥同时保留在环中，直到确认全量重写完成、且所有进程已切换到新版本后，
再用 --retire 受控摘除旧密钥。

安全约束（本脚本严格遵守）：
- 不修改 .env；只在 stdout 打印需要由运维写入 .env 的新密钥环值。
- 密钥原文不写日志/审计：轮换各阶段写 security_audit_event（key_rotation），
  只记录版本号/行数等元数据（app/core/secrets/audit.py）。
- --retire 受控摘除：先校验全表可解密 + 无该版本密文残留 + 非激活版本，
  任一不满足即拒绝（fail-closed）。
- 执行前应先用 scripts/create_pilot_backup.py 备份。

用法:
    python -B scripts/rotate_encryption_key.py --dry-run
        # 只报告各加密列当前版本分布（按密文前缀统计，不需要密钥）
    python -B scripts/rotate_encryption_key.py --new-key <32B urlsafe-b64>
        # 首次加密（无存量密钥环）或轮换到指定新版本密钥
    python -B scripts/rotate_encryption_key.py --verify
        # 校验所有行可解密，并报告版本分布
    python -B scripts/rotate_encryption_key.py --retire v1
        # 受控摘除旧版本（需先完成轮换与全量重加密）

首次加密: 没有存量密钥时运行 `--new-key`，等价于建立 v1 并加密所有明文。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 允许用命令行环境变量覆盖 .env（不修改 .env 文件本身）
from sqlalchemy import text as sa_text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, engine  # noqa: E402
from app.core.encryption import EncryptedText, decrypt_text, encrypt_text  # noqa: E402
from app.core.secrets.audit import write_key_rotation_audit  # noqa: E402
from app.core.secrets.rotation import validate_key_retirement  # noqa: E402


def _decode_key(configured: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(configured.encode("ascii"))
    except Exception as exc:
        raise ValueError("密钥必须是 32 字节 URL-safe Base64") from exc
    if len(decoded) != 32:
        raise ValueError("密钥必须解码为 32 字节")
    return decoded


def _load_current_ring() -> dict[str, str]:
    """从当前环境读取密钥环：优先 KEYS_JSON，其次单密钥视为 v1。"""
    ring_json = os.getenv("LEGAL_DATA_ENCRYPTION_KEYS_JSON", "").strip()
    if ring_json:
        ring = json.loads(ring_json)
        return {str(k): str(v) for k, v in ring.items()}
    single = os.getenv("LEGAL_DATA_ENCRYPTION_KEY", "").strip()
    if single:
        _decode_key(single)
        return {"v1": single}
    return {}


def _next_version(ring: dict[str, str]) -> str:
    numeric = [
        int(name[1:]) for name in ring if name.startswith("v") and name[1:].isdigit()
    ]
    return f"v{max(numeric, default=0) + 1}"


def _encrypted_columns() -> list[tuple[str, str, str]]:
    """返回 (表名, 列名, 主键列名)。需要模型已注册到 Base.metadata。"""
    import app.models  # noqa: F401

    found: list[tuple[str, str, str]] = []
    for table in Base.metadata.tables.values():
        pk_cols = list(table.primary_key.columns)
        if len(pk_cols) != 1:
            continue
        pk_name = pk_cols[0].name
        for col in table.columns:
            if isinstance(col.type, EncryptedText):
                found.append((table.name, col.name, pk_name))
    return sorted(found)


def _version_of(raw: str | None) -> str | None:
    """按密文前缀返回版本号；明文返回 None；空返回 None。"""
    if not raw or not raw.startswith("enc:"):
        return None
    parts = raw.split(":", 2)
    if len(parts) != 3:
        return "unknown"
    return parts[1]


def report_state() -> dict:
    """只读统计各加密列版本分布（按前缀），不需要密钥。"""
    state = {}
    with engine.connect() as conn:
        for tname, cname, pk_name in _encrypted_columns():
            rows = conn.execute(
                sa_text(f"SELECT `{cname}` FROM `{tname}` WHERE `{cname}` IS NOT NULL")
            ).fetchall()
            counts: dict[str, int] = {"plaintext": 0}
            for (raw,) in rows:
                version = _version_of(raw)
                if version is None:
                    counts["plaintext"] += 1
                else:
                    counts[version] = counts.get(version, 0) + 1
            state[f"{tname}.{cname}"] = {"rows": len(rows), "versions": counts}
    return state


def verify_decryptable() -> tuple[int, dict[str, int], list[str]]:
    """校验所有非空行可解密，返回 (成功数, 版本分布, 失败样本)。"""
    ok = 0
    distribution: dict[str, int] = {}
    failures: list[str] = []
    with engine.connect() as conn:
        for tname, cname, pk_name in _encrypted_columns():
            rows = conn.execute(
                sa_text(f"SELECT `{pk_name}`, `{cname}` FROM `{tname}` WHERE `{cname}` IS NOT NULL")
            ).fetchall()
            for pk_val, raw in rows:
                version = _version_of(raw)
                if version is None:
                    distribution["plaintext"] = distribution.get("plaintext", 0) + 1
                    ok += 1
                    continue
                try:
                    decrypt_text(raw)
                    distribution[version] = distribution.get(version, 0) + 1
                    ok += 1
                except Exception as exc:
                    failures.append(f"{tname}#{pk_val} ({cname}): {exc}")
    return ok, distribution, failures


def reencrypt_all() -> dict:
    """全量重写 EncryptedText 列：明文->激活版本，旧版本->激活版本。"""
    changed: dict[str, int] = {}
    with engine.begin() as conn:
        for tname, cname, pk_name in _encrypted_columns():
            count = 0
            last_pk: object | None = None
            while True:
                where = " WHERE `%s` > :last" % pk_name if last_pk is not None else ""
                params = {"last": last_pk} if last_pk is not None else {}
                rows = conn.execute(
                    sa_text(
                        f"SELECT `{pk_name}`, `{cname}` FROM `{tname}`{where}"
                        f" ORDER BY `{pk_name}` LIMIT 200"
                    ),
                    params,
                ).fetchall()
                if not rows:
                    break
                for pk_val, raw in rows:
                    if raw is None:
                        continue
                    plain = decrypt_text(raw)  # 明文原样返回；旧版本用环内密钥解密
                    new_cipher = encrypt_text(plain)  # 写为激活版本
                    conn.execute(
                        sa_text(f"UPDATE `{tname}` SET `{cname}` = :v WHERE `{pk_name}` = :pk"),
                        {"v": new_cipher, "pk": pk_val},
                    )
                    count += 1
                last_pk = rows[-1][0]
            if count:
                changed[f"{tname}.{cname}"] = count
    return changed


def do_retire(version: str) -> int:
    """受控摘除旧版本：门禁全部通过才输出新密钥环值（不修改 .env），并写审计。"""
    ring = _load_current_ring()
    active_version = (os.getenv("LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION", "") or "v1").strip() or "v1"
    column_state = report_state()
    ok, _distribution, failures = verify_decryptable()
    reasons = validate_key_retirement(
        version=version,
        ring=ring,
        active_version=active_version,
        column_state=column_state,
        decrypt_failures=failures,
    )
    if reasons:
        write_key_rotation_audit(
            action="retire",
            result="failure",
            target_version=version,
            reason_code="retire_rejected",
            sanitized_metadata={"reasons": reasons},
        )
        print(
            json.dumps(
                {"mode": "retire", "retired_version": version, "rejected": reasons},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    new_ring = {k: v for k, v in ring.items() if k != version}
    write_key_rotation_audit(
        action="retire",
        result="success",
        target_version=version,
        sanitized_metadata={"remaining_versions": sorted(new_ring), "decryptable": ok},
    )
    print(
        json.dumps(
            {
                "mode": "retire",
                "retired_version": version,
                "decryptable": ok,
                "remaining_versions": sorted(new_ring),
                # 由运维写入 .env 的值（本脚本不修改 .env）
                "env_to_set": {
                    "LEGAL_DATA_ENCRYPTION_KEYS_JSON": json.dumps(new_ring),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="E-2 法律数据加密密钥轮换")
    parser.add_argument("--dry-run", action="store_true", help="只统计版本分布，不改写数据")
    parser.add_argument("--verify", action="store_true", help="校验所有行可解密")
    parser.add_argument("--new-key", type=str, help="目标新密钥（32 字节 URL-safe Base64）；缺省自动生成")
    parser.add_argument("--retire", type=str, metavar="VERSION", help="受控摘除旧版本（需先轮换并全量重加密）")
    args = parser.parse_args()

    if args.retire:
        return do_retire(args.retire)

    if args.dry_run:
        state = report_state()
        print(json.dumps({"mode": "dry-run", "state": state}, ensure_ascii=False, indent=2))
        return 0

    if args.verify:
        ok, distribution, failures = verify_decryptable()
        if failures:
            write_key_rotation_audit(
                action="verify",
                result="failure",
                target_version="*",
                reason_code="decrypt_failed",
                sanitized_metadata={"failures_count": len(failures)},
            )
        print(
            json.dumps(
                {"mode": "verify", "decryptable": ok, "distribution": distribution,
                 "failures": failures},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    ring = _load_current_ring()
    if args.new_key:
        _decode_key(args.new_key)
        new_key = args.new_key
    else:
        new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    new_version = _next_version(ring)
    new_ring = {**ring, new_version: new_key}

    # 激活新版本：os.environ 优先于 .env，仅本进程内生效。
    # get_settings 有 lru_cache，必须在设置后清缓存，否则加密仍走旧版本。
    os.environ["LEGAL_DATA_ENCRYPTION_KEYS_JSON"] = json.dumps(new_ring)
    os.environ["LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION"] = new_version
    get_settings.cache_clear()

    changed = reencrypt_all()
    ok, distribution, failures = verify_decryptable()

    if failures:
        # 轮换后校验失败：不把新环写入生产（本进程内环境变量已临时指向新版本，
        # 但未写入 .env）；运维须排查失败行后重跑。
        write_key_rotation_audit(
            action="rotate",
            result="failure",
            target_version=new_version,
            reason_code="verify_failed",
            sanitized_metadata={
                "old_versions": sorted(ring.keys()),
                "failures_count": len(failures),
            },
        )
    else:
        write_key_rotation_audit(
            action="rotate",
            result="success",
            target_version=new_version,
            sanitized_metadata={
                "old_versions": sorted(ring.keys()),
                "new_version": new_version,
                "rewritten_columns": changed,
                "decryptable_after": ok,
            },
        )

    print(
        json.dumps(
            {
                "mode": "rotate",
                "old_versions": sorted(ring.keys()),
                "new_version": new_version,
                "rewritten_columns": changed,
                "decryptable_after": ok,
                "distribution_after": distribution,
                "failures": failures,
                # 由运维写入 .env 的值（本脚本不修改 .env）
                "env_to_set": {
                    "LEGAL_DATA_ENCRYPTION_KEYS_JSON": json.dumps(new_ring),
                    "LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION": new_version,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
