"""把人工抽检表渲染成一个自包含的点击式 HTML（离线可用，方便人工逐条判定）。

用法：
    python -B scripts/human_sheet_html.py --sheet eval/outputs/human_spotcheck_sheet.md \
        --out _human_spotcheck.html        # python 子进程写不了工作区子目录，先写仓库根再 Move-Item

产物特性：无外部依赖、无网络请求；每条三按钮（正确/错误/无法判断）；进度计数；
选择结果存 localStorage（可中途关掉）；底部实时生成可复制的结论行
（`1 正确, 2 正确, …`），贴回对话即可由 scripts/human_sheet_digest.py 写回原表。
**刻意不包含两位裁判的判定**，保持“先自己判、判完再看”。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from human_sheet_digest import split_sheet  # noqa: E402  （同目录脚本，复用解析逻辑）

DEFAULT_SHEET = Path("eval/outputs/human_spotcheck_sheet.md")
DEFAULT_OUT = Path("_human_spotcheck.html")

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>人工抽检：三层评审第三层（__COUNT__ 条）</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; font: 14px/1.7 "Noto Sans SC", "Plus Jakarta Sans", system-ui, sans-serif;
         background: #F6F7FB; color: #0F172A; }
  header { max-width: 900px; margin: 0 auto 16px; }
  h1 { font-size: 20px; margin: 0 0 6px; }
  .sub { color: #64748B; font-size: 13px; }
  .bar { position: sticky; top: 0; z-index: 5; max-width: 900px; margin: 0 auto 16px; padding: 12px 16px;
          background: rgba(255,255,255,.92); backdrop-filter: blur(8px); border: 1px solid #E6E9F0;
          border-radius: 12px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .bar b { font-variant-numeric: tabular-nums; }
  .bar button { margin-left: auto; padding: 7px 12px; border-radius: 9px; border: 1px solid #E6E9F0;
                 background: #fff; cursor: pointer; font: inherit; }
  .bar button + button { margin-left: 0; }
  .bar button:hover { border-color: #4F46E5; color: #4F46E5; }
  .card { max-width: 900px; margin: 0 auto 14px; background: #fff; border: 1px solid #E6E9F0;
           border-radius: 14px; padding: 16px 18px; }
  .card.done { border-color: #4F46E5; box-shadow: 0 1px 3px rgba(15,23,42,.05), 0 10px 28px -14px rgba(79,70,229,.28); }
  .head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
  .idx { font-weight: 700; }
  .pill { font-size: 11.5px; font-weight: 600; padding: 2px 8px; border-radius: 999px; background: #EEF0FE; color: #4338CA; }
  .pill.f { background: #D4F5EE; color: #0D9488; }
  .field { margin: 6px 0; }
  .field span { color: #64748B; font-size: 12.5px; }
  .ev { background: #FAFAFB; border: 1px solid #EEF1F6; border-radius: 10px; padding: 8px 10px;
         font-size: 12.5px; color: #4B5968; white-space: pre-wrap; }
  .acts { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  .acts button { padding: 7px 14px; border-radius: 9px; border: 1px solid #E6E9F0; background: #fff;
                  cursor: pointer; font: inherit; }
  .acts button.on { background: #4F46E5; border-color: #4F46E5; color: #fff; font-weight: 600; }
  .acts button.on.unknown { background: #64748B; border-color: #64748B; }
  textarea { width: 100%; min-height: 72px; font: 12.5px/1.6 ui-monospace, Consolas, monospace;
              border: 1px solid #E6E9F0; border-radius: 10px; padding: 8px 10px; }
  .hint { color: #94A3B8; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>人工抽检：三层评审第三层（__COUNT__ 条）</h1>
  <div class="sub">口径：<b>忠实度</b>条目「正确」= 答案的事实性断言都能在引用片段里找到依据；<b>质量</b>条目「正确」= 答案质量可接受（可判 pass）。
  「无法判断」与留空都不进一致率分母。两位裁判的判定<b>不在本页显示</b>（先自己判）。</div>
</header>

<div class="bar">
  <span>进度：<b id="progress">0 / __COUNT__</b></span>
  <span class="hint">选择自动存在本机浏览器，可中途关掉再来</span>
  <button id="copy">复制结论行</button>
  <button id="reset">清空</button>
</div>

<main id="cards"></main>

<div class="card">
  <div class="field"><span>结论行（贴回对话即可）：</span></div>
  <textarea id="out" readonly></textarea>
  <div class="hint">格式：<code>1 正确, 2 错误, 3 无法判断, …</code>；未判的条目不会出现。</div>
</div>

<script id="entries" type="application/json">__PAYLOAD__</script>
<script>
const entries = JSON.parse(document.getElementById('entries').textContent);
const VERDICTS = ['正确', '错误', '无法判断'];
const KEY = 'human-spotcheck-verdicts-v1';
const store = JSON.parse(localStorage.getItem(KEY) || '{}');
const cards = document.getElementById('cards');
const out = document.getElementById('out');

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

function render() {
  cards.innerHTML = '';
  entries.forEach((e) => {
    const card = document.createElement('section');
    card.className = 'card' + (store[e.index] ? ' done' : '');
    const parts = [];
    parts.push('<div class="field"><span>问题：</span>' + escapeHtml(e.question) + '</div>');
    if (e.reference) parts.push('<div class="field"><span>参考答案：</span>' + escapeHtml(e.reference) + '</div>');
    parts.push('<div class="field"><span>系统答案：</span>' + escapeHtml(e.answer) + '</div>');
    if (e.evidence) parts.push('<div class="field"><span>引用片段：</span><div class="ev">' + escapeHtml(e.evidence) + '</div></div>');
    const buttons = VERDICTS.map((v) => {
      const on = store[e.index] === v ? ' on' : '';
      const unknown = v === '无法判断' ? ' unknown' : '';
      return '<button data-v="' + v + '" class="' + (on + unknown).trim() + '">' + v + '</button>';
    }).join('');
    card.innerHTML =
      '<div class="head"><span class="idx">[' + e.index + ']</span>' +
      '<span class="pill' + (e.kind === 'faithfulness' ? ' f' : '') + '">' + (e.kind === 'faithfulness' ? '忠实度' : '质量') + '</span>' +
      '<span class="pill">' + (e.item === 'divergent' ? '分歧项' : '一致项') + '</span>' +
      '<span class="hint">' + escapeHtml(e.id) + '</span></div>' +
      parts.join('') +
      '<div class="acts">' + buttons + '</div>';
    card.querySelectorAll('button[data-v]').forEach((btn) => {
      btn.addEventListener('click', () => {
        store[e.index] = btn.dataset.v;
        localStorage.setItem(KEY, JSON.stringify(store));
        render();
      });
    });
    cards.appendChild(card);
  });
  update();
}

function update() {
  const answered = entries.filter((e) => store[e.index]).length;
  document.getElementById('progress').textContent = answered + ' / ' + entries.length;
  out.value = entries.filter((e) => store[e.index]).map((e) => e.index + ' ' + store[e.index]).join(', ');
}

document.getElementById('copy').addEventListener('click', async () => {
  const btn = document.getElementById('copy');
  try {
    await navigator.clipboard.writeText(out.value);
    btn.textContent = '已复制';
  } catch (err) {
    out.select();
    btn.textContent = '请手动复制';
  }
  setTimeout(() => { btn.textContent = '复制结论行'; }, 1500);
});

document.getElementById('reset').addEventListener('click', () => {
  Object.keys(store).forEach((k) => delete store[k]);
  localStorage.setItem(KEY, '{}');
  render();
});

render();
</script>
</body>
</html>
"""


def build_payload(entries) -> list[dict]:
    payload = []
    for entry in entries:
        payload.append(
            {
                "index": entry.index,
                "id": entry.id,
                "kind": entry.meta.get("kind", ""),
                "item": entry.meta.get("item", ""),
                # 存原文即可，页面上由 JS 统一做 HTML 转义
                "question": entry.fields.get("question", ""),
                "reference": entry.fields.get("reference", ""),
                "answer": entry.fields.get("answer", ""),
                "evidence": entry.fields.get("evidence", ""),
            }
        )
    return payload


def render_html(entries) -> str:
    payload = json.dumps(build_payload(entries), ensure_ascii=False)
    # JSON 里若出现 </script> 会提前结束脚本块，按 JSON 规范转义成 \u003c
    payload = payload.replace("</", "<\\/")
    return (
        TEMPLATE.replace("__COUNT__", str(len(entries))).replace("__PAYLOAD__", payload)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成点击式人工抽检页")
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.sheet.exists():
        print(f"找不到抽检表：{args.sheet}", file=sys.stderr)
        return 2
    _, entries = split_sheet(args.sheet.read_text(encoding="utf-8"))
    if not entries:
        print("表里没有解析到条目", file=sys.stderr)
        return 2
    args.out.write_text(render_html(entries), encoding="utf-8")
    print(f"已写 {args.out}（{len(entries)} 条）；Move-Item 到 docs/review/ 后用浏览器打开即可")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
