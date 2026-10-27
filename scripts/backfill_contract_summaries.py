"""按合同重算台账审查摘要（物化表补数）。

用途：迁移 0108 已回填一次；若生产上发现台账「审查」列与审查记录不一致
（例如某条写入路径上线前产生、或手工改过 risks_json），跑这个脚本全量重算。
幂等：按合同逐批重算，没有审查的合同会删除摘要行（台账显示未审查）。

用法：
    python -B scripts/backfill_contract_summaries.py
    python -B scripts/backfill_contract_summaries.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="重算合同台账审查摘要")
    parser.add_argument("--batch-size", type=int, default=200, help="每批合同数（默认 200）")
    parser.add_argument("--dry-run", action="store_true", help="只统计合同数，不写库")
    args = parser.parse_args()

    from sqlalchemy import func

    from app.core.database import SessionLocal
    from app.models.legal_contract import LegalContract, LegalContractReviewSummary
    from app.services.legal.contract_review_summary import backfill_contract_summaries

    db = SessionLocal()
    try:
        total = db.query(func.count(LegalContract.id)).scalar() or 0
        if args.dry_run:
            print(f"待重算合同 {total} 份（dry-run，不写库）")
            return 0
        processed = backfill_contract_summaries(db, batch_size=max(1, args.batch_size))
        summaries = db.query(func.count(LegalContractReviewSummary.id)).scalar() or 0
        print(f"已重算 {processed} 份合同，摘要行 {summaries} 条（有审查的合同才建行）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
