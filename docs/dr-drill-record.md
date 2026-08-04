# DR 演练记录（E-2 收尾）

- 演练日期：2026-08-04
- 数据库：MySQL 8（localhost:3306），源库 `aibg`（94 张表）
- 备份工具：`scripts/create_pilot_backup.py`（mysqldump --single-transaction + 应用数据目录归档）

## 一、RTO/RPO 实测结果

| 指标 | 目标 | 实测 | 结论 |
|---|---|---|---|
| RTO（恢复到可用） | ≤ 30 min | **3 秒**（2.3MB dump 恢复 + 校验） | ✅ 达标 |
| RPO（数据丢失窗口） | ≤ 4 h | **2 秒**（备份窗口，单次 mysqldump） | ✅ 达标 |
| 数据一致性 | — | 94/94 表存在，行数全部一致 | ✅ 达标 |

实测流程（2026-08-04）：
1. 备份：`python -B scripts/create_pilot_backup.py --confirm --output-dir data/backups`（2s，产物含 database.sql + application-data.tar.gz + manifest.json，带 SHA-256）
2. 恢复：`mysql --default-character-set=utf8mb4 -uroot -p*** aibg_dr_test < database.sql`（3s，隔离库 aibg_dr_test）
3. 校验：源库 vs 恢复库逐表 COUNT(*) 对比，94 表全部一致

注意：Windows 下恢复必须加 `--default-character-set=utf8mb4`，否则 UTF-8 dump 在默认字符集客户端下报 `Unknown command`。

## 二、加密密钥轮换实测（隔离库 aibg_dr_test）

场景：存量明文 → 首次加密（v1）→ 轮换（v2）→ 回滚可读性验证。

| 步骤 | 命令 | 结果 |
|---|---|---|
| 基线校验 | `rotate_encryption_key.py --verify` | 5 行明文，0 失败 |
| 首次加密 + 轮换 | `rotate_encryption_key.py --new-key <k2>` | 明文 5 行 → 全部重写为 v2 密文（contract_reviews 3 + drafts 2），verify 5/5 可解密 |
| 回滚可读 | `ACTIVE_VERSION=v1` + 完整密钥环 `--verify` | v2 密文经环内旧密钥解密 5/5 成功 |

要点：
- 轮换脚本不修改 `.env`，只输出 `env_to_set`（LEGAL_DATA_ENCRYPTION_KEYS_JSON + ACTIVE_VERSION），由运维写入。
- 密钥环（KEYS_JSON）保留新旧密钥，密文内嵌版本号，回滚/双版本并行读取安全。
- 轮换前先跑 `create_pilot_backup.py` 备份。

## 三、生产库状态与待办

- 生产库 `aibg` 加密列当前 5 行明文（legal_contract_reviews.content ×3、legal_drafts.content ×2），**尚未首次加密**。
- 待办（需运维/用户确认后执行）：生成 v1 密钥 → 写入 `.env`（LEGAL_DATA_ENCRYPTION_KEY 或密钥环）→ 对生产库执行 `rotate_encryption_key.py --new-key` → `--verify` → 移除旧明文。
- 演练库 `aibg_dr_test` 为隔离环境，可随时删除。
