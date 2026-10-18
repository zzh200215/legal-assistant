"""人工抽检一致率：读填好的 `divergence_review_sheet.md`，算「AI 裁判 vs 人工」同意率。

分母/分子口径（两个基准各算一个数，都打印）：
- **分母** = 该条目上「人工表了态（`正确` / `错误`）」且「该侧裁判有明确判罚」的条目数；
- **分子** = 人工结论与该侧判罚同向的条目数（`正确` ↔ 通过/忠实，`错误` ↔ 不通过/不忠实）；
- `无法判断` 与留空**不进分母**，单独计数打印；因此表格没填完时一致率不会虚高成 1.0。

判罚来源优先读每条标题下的 `<!-- meta：... -->`（由 `scripts/run_cross_judge.py` 生成），
读不到 meta 时退回解析 `- **<裁判名>**：<判罚>` 这类正文行。

用法：
  python -B scripts/score_human_review.py --sheet eval/outputs/divergence_review_sheet.md
  python -B scripts/score_human_review.py --sheet <填好的副本> --json human_review_score.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

_SECTION = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*[｜|]\s*(.+)$")
_META = re.compile(r"<!--\s*meta\s*[：:]?\s*(.*?)\s*-->")
_HUMAN = re.compile(r"^\s*[-*]?\s*\**人工结论\**\s*[:：]\s*(.*)$")
_NOTE = re.compile(r"^\s*[-*]?\s*\**备注\**\s*[:：]\s*(.*)$")
_SIDE_LINE = re.compile(r"^-\s+\*\*(?P<name>[^*]+)\*\*\s*[：:]\s*(?P<body>.+)$")

_PASS_LABELS = ("pass", "忠实", "true", "通过", "合格")
_FAIL_LABELS = ("fail", "review", "不忠实", "false", "不通过", "不合格")
_SKIP_KEYS = {"id", "kind", "类型"}


def normalize_human(value: str) -> str | None:
    """把人工填的一行归一成 correct / wrong / unknown；空行返回 None。"""
    text = re.sub(r"[*_`（）()\s]", "", value or "")
    if not text:
        return None
    if re.search(r"无法|不确定|不知道|存疑|待定", text):
        return "unknown"
    if re.search(r"错误|不正确|不忠实|不通过|不合格|有问题", text):
        return "wrong"
    if re.search(r"正确|忠实|通过|合格|没问题", text):
        return "correct"
    return None


def normalize_side(value: str) -> str | None:
    """把裁判侧判罚归一成 pass / fail；识别不了返回 None（不进分母）。"""
    text = re.sub(r"[*_`\s]", "", value or "").lower()
    if not text:
        return None
    if "不忠实" in text or "不通过" in text or "false" in text:
        return "fail"
    if text in {"fail", "review"} or text.startswith(("fail", "review")):
        return "fail"
    if "忠实" in text or "true" in text or "通过" in text or text.startswith("pass"):
        return "pass"
    return None


def parse_sheet(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        section = _SECTION.match(raw_line)
        if section:
            current = {
                "ordinal": int(section.group(1)),
                "title": section.group(2).strip(),
                "headline": section.group(3).strip(),
                "kind": None,
                "id": None,
                "sides": {},
                "meta_sides": False,
                "human_raw": "",
                "note_raw": "",
                "body_lines": [],
            }
            entries.append(current)
            continue
        if current is None:
            continue
        current["body_lines"].append(raw_line)
        meta = _META.search(raw_line)
        if meta:
            for token in meta.group(1).replace("，", " ").split():
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                key = key.strip()
                if key in _SKIP_KEYS:
                    current[key] = value.strip()
                else:
                    current["sides"][key] = normalize_side(value)
                    current["meta_sides"] = True
            continue
        human = _HUMAN.match(raw_line)
        if human:
            current["human_raw"] = human.group(1).strip()
            continue
        note = _NOTE.match(raw_line)
        if note:
            current["note_raw"] = note.group(1).strip()
            continue
        side_line = _SIDE_LINE.match(raw_line)
        if side_line and "｜" in raw_line and not current["meta_sides"]:
            name = side_line.group("name").strip()
            body = side_line.group("body").split("｜")[0].split("（")[0].strip()
            current["sides"].setdefault(name, normalize_side(body))

    for entry in entries:
        entry["human"] = normalize_human(entry["human_raw"])
        for name in list(entry["sides"]):
            if entry["sides"][name] is None:
                del entry["sides"][name]
    return entries


def summarize(entries: list[dict[str, Any]], side: str) -> dict[str, Any]:
    filled = [entry for entry in entries if entry["human"] in {"correct", "wrong"} and side in entry["sides"]]
    agree = [
        entry
        for entry in filled
        if (entry["human"] == "correct") == (entry["sides"][side] == "pass")
    ]
    return {
        "side": side,
        "numerator": len(agree),
        "denominator": len(filled),
        "rate": round(len(agree) / len(filled), 4) if filled else None,
        "disagreed_entries": [entry["id"] or entry["title"] for entry in filled if entry not in agree],
    }


def _rate_text(summary: dict[str, Any]) -> str:
    rate = summary["rate"]
    if summary["denominator"] == 0:
        return "n/a（分母 0：该基准下还没有人工表态的条目）"
    return f"{rate:.4f}（{summary['numerator']}/{summary['denominator']}）"


def build_result(entries: list[dict[str, Any]]) -> dict[str, Any]:
    sides = sorted({side for entry in entries for side in entry["sides"]})
    filled = [entry for entry in entries if entry["human"] in {"correct", "wrong"}]
    unknown = [entry for entry in entries if entry["human"] == "unknown"]
    blank = [entry for entry in entries if entry["human"] is None]
    result: dict[str, Any] = {
        "entries_total": len(entries),
        "filled": len(filled),
        "filled_correct": sum(1 for entry in filled if entry["human"] == "correct"),
        "filled_wrong": sum(1 for entry in filled if entry["human"] == "wrong"),
        "unknown": len(unknown),
        "blank": len(blank),
        "notes_filled": sum(1 for entry in entries if entry["note_raw"]),
        "by_kind": {},
        "sides": sides,
        "agreement": {side: summarize(entries, side) for side in sides},
        "agreement_per_kind": {
            kind: {side: summarize([entry for entry in entries if entry["kind"] == kind], side) for side in sides}
            for kind in sorted({entry["kind"] for entry in entries if entry["kind"]})
        },
        "unparsed_sides_entries": [entry["title"] for entry in entries if not entry["sides"]],
    }
    for entry in entries:
        kind = entry["kind"] or "unknown"
        bucket = result["by_kind"].setdefault(kind, {"total": 0, "filled": 0, "unknown": 0, "blank": 0})
        bucket["total"] += 1
        bucket["filled"] += 1 if entry["human"] in {"correct", "wrong"} else 0
        bucket["unknown"] += 1 if entry["human"] == "unknown" else 0
        bucket["blank"] += 1 if entry["human"] is None else 0
    return result


def print_report(result: dict[str, Any]) -> None:
    print("=== 人工抽检一致率（AI 裁判 vs 人工）===")
    print(
        f"条目总数 {result['entries_total']}｜已表态 {result['filled']}"
        f"（正确 {result['filled_correct']} / 错误 {result['filled_wrong']}）"
        f"｜无法判断 {result['unknown']}｜留空 {result['blank']}｜填了备注 {result['notes_filled']}"
    )
    for kind, bucket in result["by_kind"].items():
        print(
            f"  [{kind}] 共 {bucket['total']} 条：已表态 {bucket['filled']}、无法判断 {bucket['unknown']}、留空 {bucket['blank']}"
        )
    for side in result["sides"]:
        summary = result["agreement"][side]
        print(f"基准 {side}：人工同意率 = {_rate_text(summary)}（分子 {summary['numerator']} / 分母 {summary['denominator']}）")
        if summary["disagreed_entries"]:
            print(f"    与 {side} 不同向的条目（{len(summary['disagreed_entries'])} 条）：" + "、".join(summary["disagreed_entries"]))
    for kind, per_side in result["agreement_per_kind"].items():
        cells = "｜".join(f"{side} {_rate_text(summary)}" for side, summary in per_side.items())
        print(f"  按类型 {kind}：{cells}")
    if result["unparsed_sides_entries"]:
        print("  警告：以下条目读不到裁判判罚，已排除在分母外：" + "、".join(result["unparsed_sides_entries"]))
    if result["filled"] == 0:
        print("提示：表里还没有人工表态，两个基准的分母都是 0，一致率均为 n/a。")


def main() -> int:
    parser = argparse.ArgumentParser(description="计算人工抽检与 AI 裁判的一致率")
    parser.add_argument("--sheet", default="eval/outputs/divergence_review_sheet.md")
    parser.add_argument("--json", default=None, help="把结果同时写成 JSON")
    args = parser.parse_args()

    path = ROOT / args.sheet if not Path(args.sheet).is_absolute() else Path(args.sheet)
    if not path.exists():
        print(f"找不到清单文件：{path}")
        return 1
    entries = parse_sheet(path.read_text(encoding="utf-8"))
    if not entries:
        print(f"{path} 里没有解析到任何 `### N. <题名>｜…` 条目")
        return 2
    result = build_result(entries)
    result["sheet"] = str(path)
    print_report(result)
    if args.json:
        out = Path(args.json) if Path(args.json).is_absolute() else ROOT / args.json
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
