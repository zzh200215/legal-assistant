"""人工抽检表的读取/填表小工具（配合 scripts/run_cross_judge.py 与 scripts/score_human_review.py）。

用法：
    # 1) 看进度
    python -B scripts/human_sheet_digest.py
    # 2) 打印某几条的精简摘要（方便在聊天里逐批人工判定）
    python -B scripts/human_sheet_digest.py --digest 1 9
    # 3) 把结论写回表（python 子进程写不了工作区子目录，故先写仓库根，再用 pwsh Move-Item 归位）
    python -B scripts/human_sheet_digest.py --fill cross-001=正确 --fill cross-004=错误 --out _sheet_filled.md

表的约定（由 run_cross_judge.py 生成）：每条以 `### ` 开头，紧跟
`<!-- meta：id=… kind=… item=… -->`，正文里有 `- 人工结论：` 与 `- 备注：` 两行待填。
人工结论只接受 正确 / 错误 / 无法判断（其余值会被拒绝，避免写进脏数据）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_SHEET = Path("eval/outputs/human_spotcheck_sheet.md")
VERDICTS = ("正确", "错误", "无法判断")
META_RE = re.compile(r"<!--\s*meta：(?P<body>[^>]*?)-->")
HEADING = "### "

FIELD_PATTERNS = {
    "question": re.compile(r"- 问题：(?P<v>.*?)(?=\n- |\Z)", re.S),
    "reference": re.compile(r"- 参考答案：(?P<v>.*?)(?=\n- |\Z)", re.S),
    "answer": re.compile(r"- 系统答案：(?P<v>.*?)(?=\n- |\Z)", re.S),
    "evidence": re.compile(r"- 写进答案的引用片段[^：\n]*：(?P<v>.*?)(?=\n- 人工结论：|\Z)", re.S),
    "verdict": re.compile(r"- 人工结论：(?P<v>.*?)(?=\n- 备注：|\Z)", re.S),
    "note": re.compile(r"- 备注：(?P<v>.*?)(?=\n\n|\n<details>|\Z)", re.S),
}
VERDICT_LINE = re.compile(r"^(?P<indent>\s*)- 人工结论：.*$", re.M)
NOTE_LINE = re.compile(r"^(?P<indent>\s*)- 备注：.*$", re.M)


class Entry:
    def __init__(self, index: int, block: str):
        self.index = index
        self.block = block
        lines = block.splitlines()
        self.title = lines[0][len(HEADING) :].strip() if lines else ""
        meta_match = META_RE.search(block)
        self.meta = _parse_meta(meta_match.group("body")) if meta_match else {}
        self.fields = {
            key: (pattern.search(block).group("v").strip() if pattern.search(block) else "")
            for key, pattern in FIELD_PATTERNS.items()
        }

    @property
    def id(self) -> str:
        return self.meta.get("id", f"entry-{self.index:03d}")

    @property
    def verdict(self) -> str:
        return self.fields.get("verdict", "").strip()


def _parse_meta(body: str) -> dict:
    meta: dict[str, str] = {}
    for chunk in body.replace("\n", " ").split():
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            meta[key.strip()] = value.strip()
    return meta


def split_sheet(text: str) -> tuple[str, list[Entry]]:
    """返回 (前言, 条目列表)。前言 = 第一个 `### ` 之前的所有内容。"""
    parts = text.split(f"\n{HEADING}")
    head = parts[0]
    blocks = []
    for part in parts[1:]:
        block = HEADING + part
        blocks.append(block.rstrip("\n"))
    entries = [Entry(index + 1, block) for index, block in enumerate(blocks)]
    return head, entries


def clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def print_digest(entries: list[Entry], start: int, end: int, widths: tuple[int, int, int, int]) -> None:
    q_w, r_w, a_w, e_w = widths
    for entry in entries:
        if not (start <= entry.index <= end):
            continue
        print(f"[{entry.index}] {entry.id}｜{entry.meta.get('kind', '?')}｜{entry.meta.get('item', '')}｜当前：{entry.verdict or '未填'}")
        print(f"  问题：{clip(entry.fields.get('question', ''), q_w)}")
        if entry.fields.get("reference"):
            print(f"  参考答案：{clip(entry.fields['reference'], r_w)}")
        print(f"  系统答案：{clip(entry.fields.get('answer', ''), a_w)}")
        if entry.fields.get("evidence"):
            print(f"  引用片段：{clip(entry.fields['evidence'], e_w)}")
        print()


def apply_fills(entries: list[Entry], verdicts: dict[str, str], notes: dict[str, str]) -> list[str]:
    blocks: list[str] = []
    for entry in entries:
        block = entry.block
        if entry.id in verdicts:
            block = VERDICT_LINE.sub(lambda m: f"{m.group('indent')}- 人工结论：{verdicts[entry.id]}", block, count=1)
        if entry.id in notes:
            block = NOTE_LINE.sub(lambda m: f"{m.group('indent')}- 备注：{notes[entry.id]}", block, count=1)
        blocks.append(block)
    return blocks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="人工抽检表摘要 / 填表")
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--digest", nargs=2, type=int, metavar=("FROM", "TO"))
    parser.add_argument("--fill", action="append", default=[], metavar="ID=结论")
    parser.add_argument("--note", action="append", default=[], metavar="ID=备注")
    parser.add_argument("--out", type=Path, default=Path("_sheet_filled.md"))
    parser.add_argument("--widths", type=int, nargs=4, default=(120, 200, 420, 520))
    args = parser.parse_args(argv)

    if not args.sheet.exists():
        print(f"找不到抽检表：{args.sheet}", file=sys.stderr)
        return 2
    text = args.sheet.read_text(encoding="utf-8")
    head, entries = split_sheet(text)

    if args.digest:
        print_digest(entries, args.digest[0], args.digest[1], tuple(args.widths))
        return 0

    if not args.fill and not args.note:
        filled = sum(1 for entry in entries if entry.verdict)
        print(f"共 {len(entries)} 条，已填 {filled} 条，未填 {len(entries) - filled} 条")
        for entry in entries:
            print(f"  [{entry.index}] {entry.id}｜{entry.meta.get('kind', '?')}｜{entry.verdict or '未填'}")
        return 0

    known = {entry.id for entry in entries}
    verdicts: dict[str, str] = {}
    for item in args.fill:
        key, _, value = item.partition("=")
        key = key.strip()
        if value not in VERDICTS:
            print(f"结论只能是 {'/'.join(VERDICTS)}：{item}", file=sys.stderr)
            return 2
        if key not in known:
            print(f"表里没有这个 id：{key}", file=sys.stderr)
            return 2
        verdicts[key] = value
    notes: dict[str, str] = {}
    for item in args.note:
        key, _, value = item.partition("=")
        key = key.strip()
        if key not in known:
            print(f"表里没有这个 id：{key}", file=sys.stderr)
            return 2
        notes[key] = value

    blocks = apply_fills(entries, verdicts, notes)
    updated = head + "\n" + "\n\n".join(blocks) + "\n"
    args.out.write_text(updated, encoding="utf-8")
    print(f"已写 {args.out}（填 {len(verdicts)} 条结论、{len(notes)} 条备注）；把它 Move-Item 回 {args.sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
