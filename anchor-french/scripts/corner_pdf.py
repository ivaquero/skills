#!/usr/bin/env python3
"""corner_pdf — 把语言角主持脚本（Markdown）排成朗读用 A4 PDF。

只做版式转换，不改一个字：md 是唯一真源（先过 `corner_bank.py verify`）。
本脚本是产出链的最后一环 —— 题库 → md → PDF，题文与提示词始终一致。

用法（`src` 按当前工作目录解析，故任意目录执行均可）::

    python scripts/corner_pdf.py docs/fr-vie-urbaine.md
    python scripts/corner_pdf.py docs/fr-vie-urbaine.md --keep-typ
    python scripts/corner_pdf.py docs/fr-vie-urbaine.md -o /tmp/x.pdf --lang fr

依赖：`typst`（0.13+）在 PATH 上；缺它时直接报错退出，不影响 md 产出。

识别的 Markdown 约定（与本 skill 的产出格式一致）::

    # 标题
    - 键 : 值                     → 元信息行（首个 ## 之前的列表）
    ## 章节 / ### 小节            → 一、二级小标题
    1. 题干 (DELF B1)\\           → 题目（行尾反斜杠 + 等级标签）
       标签 : a (b) · c (d)       → 上一题的提示词（缩进续行）
    1. 题干                        → 问题索引条目（无行尾反斜杠）
    - 词头 → 例句                  → 生词条目（现行两段式）
    - 词头 → 释义 → 例句           → 生词条目（旧三段式，兼容）
    | a | b | c |                 → 表格

语言由文件名前缀推断（fr / es / de / ru / uk / en），决定断词语言与页眉小字；
脚本本身与语言无关，同一份可服务其它语言角技能包。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ── 词法 ──────────────────────────────────────────────────────

SPECIAL = re.compile(r"([\\#$\[\]*_@<>~`])")
Q_RE = re.compile(r"^(\d+)\.\s+(.*?)\\$")  # 题：行尾反斜杠
IDX_RE = re.compile(r"^(\d+)\.\s+(.+)$")  # 索引：无行尾反斜杠
HINT_RE = re.compile(
    r"^\s+([^:\s][^:]*?)(\s*:)\s*(.+)$"
)  # 缩进续行：标签 + 冒号 + 内容
LVL_RE = re.compile(
    r"\s*\(\s*(?:(?:DELF|DALF|DELE|SIELE|CEFR|HSK)\s+)?([ABC][12]|\d)\s*\)\s*$"
)
TABLE_SEP = re.compile(r":?-{3,}:?")
INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*([^*]+)\*")
META_BOLD = re.compile(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$")

KICKER = {
    "fr": "CERCLE DE CONVERSATION FRANÇAISE",
    "es": "TERCÍO DE CONVERSACIÓN ESPAÑOLA",
    "en": "ENGLISH CONVERSATION CIRCLE",
    "de": "DEUTSCHER GESPRÄCHSKREIS",
    "ru": "КРУЖОК РУССКОГО ЯЗЫКА",
    "uk": "ГУРТОК УКРАЇНСЬКОЇ МОВИ",
}
LANGS = set(KICKER)


def esc(s: str) -> str:
    """转义 Typst 标记字符。"""
    return SPECIAL.sub(r"\\\1", s)


def rich(s: str) -> str:
    """行内标记转换：**粗体** -> *粗体*，*斜体* -> _斜体_，其余字符转义。"""
    out: list[str] = []
    pos = 0
    for m in INLINE_RE.finditer(s):
        out.append(esc(s[pos : m.start()]))
        if m.group(1) is not None:
            out.append(f"*{esc(m.group(1))}*")
        else:
            out.append(f"_{esc(m.group(2))}_")
        pos = m.end()
    out.append(esc(s[pos:]))
    return "".join(out)


# ── 解析 ──────────────────────────────────────────────────────


def parse(lines: list[str]):
    head: list[tuple[str, str]] = []
    blocks: list[tuple[str, object]] = []
    seen_section = False
    i = 0

    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        if s.startswith("# "):
            head.append(("title", s[2:].strip()))
            i += 1
            continue

        if s.startswith("## "):
            seen_section = True
            blocks.append(("h1", s[3:].strip()))
            i += 1
            continue

        if s.startswith("### "):
            blocks.append(("h2", s[4:].strip()))
            i += 1
            continue

        m = Q_RE.match(s)
        if m:
            num, body = m.group(1), m.group(2)
            label, colon, hints = "", "", ""
            if i + 1 < len(lines):
                mh = HINT_RE.match(lines[i + 1])
                if mh:
                    label, colon = mh.group(1).strip(), mh.group(2)
                    hints = mh.group(3).strip()
                    i += 1
            lvl = None
            ml = LVL_RE.search(body)
            if ml:
                lvl = ml.group(1)
                body = body[: ml.start()].rstrip()
            blocks.append(("q", (num, body, lvl, label, colon, hints)))
            i += 1
            continue

        mi = IDX_RE.match(s)
        if mi:
            blocks.append(("idx", (mi.group(1), mi.group(2))))
            i += 1
            continue

        if s.startswith("- "):
            if seen_section:
                blocks.append(("li", s[2:].strip()))
            else:
                head.append(("meta", s[2:].strip()))
            i += 1
            continue

        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(TABLE_SEP.fullmatch(c) for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue

        blocks.append(("p", s))
        i += 1

    return head, blocks


# ── 渲染 ──────────────────────────────────────────────────────

PREAMBLE = """
#set page(
  paper: "a4",
  margin: (x: 2.1cm, top: 2.0cm, bottom: 1.9cm),
  numbering: "1 / 1",
  number-align: center,
)
#set document(title: "__DOCTITLE__")
#set text(font: (__FONT__), size: 10pt, lang: "__LANG__")
#set par(justify: true, leading: 0.72em)
#set block(spacing: 1.05em)

#let accent = rgb("#1f4e63")
#let muted = rgb("#5b6770")
#let hair = rgb("#c8d2d8")

#let title-page(size, title) = align(center)[
  #text(size: 8pt, fill: muted, tracking: 1.6pt)[__KICKER__]
  #v(0.5em)
  #text(size: size, weight: "semibold", fill: accent, hyphenate: false)[#title]
  #v(0.6em)
  #line(length: 28%, stroke: 0.6pt + accent)
]

#let meta-row(k, v) = grid(
  columns: (5.4em, 1fr),
  column-gutter: 0.5em,
  align: (left, left),
  [#text(size: 8.8pt, fill: muted, weight: "semibold", hyphenate: false)[#k]],
  [#text(size: 9.4pt)[#v]],
)

#let h1(body) = block(above: 1.9em, below: 0.9em)[
  #text(size: 12.5pt, weight: "semibold", fill: accent)[#body]
  #v(-0.35em)
  #line(length: 100%, stroke: 0.5pt + hair)
]

#let h2(body) = block(above: 1.7em, below: 0.75em)[
  #text(size: 10.5pt, weight: "semibold", fill: accent.lighten(25%))[#body]
]

#let qitem(num, body, lvl, hline) = block(above: 1.05em, below: 1.0em, breakable: false)[
  #grid(
    columns: (1.5em, 1fr),
    column-gutter: 0.55em,
    align: (right + top, left + top),
    text(size: 9pt, fill: accent, weight: "semibold")[#num],
    [
      #if lvl != none [
        #text(size: 10pt, weight: "medium")[#body]
        #h(0.25em)
        #text(size: 7.4pt, fill: muted, tracking: 0.4pt)[#lvl]
      ] else [
        #text(size: 10pt, weight: "medium")[#body]
      ]
      #if hline != none [
        #v(0.28em)
        #text(size: 8.2pt, fill: muted, style: "italic")[#hline]
      ]
    ],
  )
]

#let idxitem(num, body) = block(above: 0.42em, below: 0.1em)[
  #grid(
    columns: (1.5em, 1fr),
    column-gutter: 0.5em,
    align: (right + top, left + top),
    text(size: 8pt, fill: accent, weight: "medium", hyphenate: false)[#num],
    text(size: 8.8pt, hyphenate: false)[#body],
  )
]

#let vocabitem(w, d, e) = block(above: 0.45em, below: 0.1em)[
  #grid(
    columns: (1em, 1fr),
    column-gutter: 0.2em,
    align: (left + top, left + top),
    text(size: 8pt, fill: hair.darken(30%))[–],
    [
      #text(size: 9.2pt, weight: "semibold")[#w]
      #if d != none [
        #h(0.18em) #text(size: 8.4pt, fill: muted)[→] #h(0.18em)
        #text(size: 9pt)[#d]
      ]
      #h(0.18em) #text(size: 8.4pt, fill: muted)[→] #h(0.18em)
      #text(size: 8.8pt, fill: muted, style: "italic")[#e]
    ],
  )
]

#let datatable(rows, cols, aligns) = table(
  columns: cols,
  align: aligns,
  stroke: none,
  inset: (x: 0.4em, y: 0.34em),
  table.hline(stroke: 0.6pt + accent),
  ..rows.first().map(c => text(size: 8.6pt, weight: "semibold", fill: accent)[#c]),
  table.hline(stroke: 0.4pt + hair),
  ..rows.slice(1).map(r => range(r.len()).map(i => {
    let c = r.at(i)
    if i == 0 { text(size: 9pt)[#c] }
    else if i == r.len() - 1 and r.len() >= 3 { text(size: 9pt, fill: muted)[#c] }
    else { text(size: 9pt)[#c] }
  })).flatten(),
  table.hline(stroke: 0.6pt + accent),
)
"""


def table_cols(rows) -> tuple[str, str]:
    """按列数给出 (columns, align) 的 Typst 表达式。"""
    n = max(len(r) for r in rows)
    if n <= 1:
        return "1fr", "left"
    if n == 2:
        return "(7em, 1fr)", "(left, left)"
    widths = ["5.6em"] + ["1fr"] * (n - 2) + ["5.0em"]
    aligns = ["left"] * (n - 1) + ["right"]
    return "(" + ", ".join(widths) + ")", "(" + ", ".join(aligns) + ")"


def one(kind: str, payload):
    """单个块 -> Typst 片段。"""
    if kind == "h1":
        return f"#h1[{rich(payload)}]"
    if kind == "h2":
        return f"#h2[{rich(payload)}]"
    if kind == "p":
        return rich(payload)
    if kind == "li":
        bits = payload.split(" → ")
        if len(bits) == 2:  # 词条只有「词 -> 例句」两段（现行规范）
            return f"#vocabitem([{rich(bits[0])}], none, [{rich(bits[1])}])"
        if len(bits) == 3:
            return (
                f"#vocabitem([{rich(bits[0])}], [{rich(bits[1])}], [{rich(bits[2])}])"
            )
        return f"#vocabitem([{rich(payload)}], [], [])"
    if kind == "q":
        num, body, lvl, label, colon, hints = payload
        lvl_s = "none" if lvl is None else f'"{lvl}"'
        hline = "none"
        if hints:
            hline = f"[{rich(label)}{colon} {rich(hints)}]"
        return f'#qitem("{num}.", [{rich(body)}], {lvl_s}, {hline})'
    if kind == "idx":
        num, ibody = payload
        return f"#idxitem[{num}.][{rich(ibody)}]"
    if kind == "table":
        rows = ",\n    ".join(
            "(" + ", ".join(f"[{rich(c)}]" for c in r) + ")" for r in payload
        )
        cols, aligns = table_cols(payload)
        return f"#datatable((\n    {rows},\n  ), {cols}, {aligns})"
    raise ValueError(kind)


def render(blocks) -> str:
    body: list[str] = []
    n = 0
    while n < len(blocks):
        kind, payload = blocks[n]
        nxt = blocks[n + 1] if n + 1 < len(blocks) else None

        # 问题总览：单独起页 + 双栏，末页不至于只剩几行
        if kind == "h1" and nxt and nxt[0] == "idx":
            items = []
            k = n + 1
            while k < len(blocks) and blocks[k][0] == "idx":
                items.append(one(*blocks[k]))
                k += 1
            half = (len(items) + 1) // 2
            body.append(
                "\n"
                "#pagebreak(weak: true)\n"
                f"#h1[{rich(payload)}]\n"
                "#block(above: 0.8em)[\n"
                "  #set par(justify: false)\n"
                "  #grid(columns: (1fr, 1fr), column-gutter: 2.0em,\n"
                f"    [\n  " + "\n  ".join(items[:half]) + "\n    ],\n"
                "    [\n  " + "\n  ".join(items[half:]) + "\n    ],\n"
                "  )\n]"
            )
            n = k
            continue

        # 小节标题 + 紧随表格：同块，避免标题与表分离
        if kind == "h1" and nxt and nxt[0] == "table":
            body.append(
                "#block(above: 2.0em, below: 1.0em, breakable: false)[\n"
                f'  #text(size: 12.5pt, weight: "semibold", fill: accent)[{rich(payload)}]\n'
                "  #v(-0.35em)\n  #line(length: 100%, stroke: 0.5pt + hair)\n"
                "  #v(0.9em)\n"
                f"  {one(*nxt)}\n]"
            )
            n += 2
            continue

        # 小节标题 + 引导句 / 词条组：同块，避免标题落单、组被拆页
        if kind == "h2":
            group = [("h2", payload)]
            k = n + 1
            while k < len(blocks) and blocks[k][0] in ("p", "li"):
                group.append(blocks[k])
                k += 1
                if blocks[k - 1][0] == "p":
                    break
            if len(group) == 1:
                body.append(f"#h2[{rich(payload)}]")
            else:
                inner = "\n  #v(0.55em)\n  ".join(one(g, v) for g, v in group[1:])
                body.append(
                    "#block(above: 1.8em, below: 1.1em, breakable: false)[\n"
                    '  #text(size: 10.5pt, weight: "semibold", '
                    "fill: accent.lighten(25%))["
                    + rich(payload)
                    + "]\n  #v(0.55em)\n  "
                    + inner
                    + "\n]"
                )
            n = k
            continue

        body.append(one(kind, payload))
        n += 1

    return "\n\n".join(body)


# ── 入口 ──────────────────────────────────────────────────────


def guess_lang(stem: str) -> str:
    for tok in stem.split("-"):
        if tok in LANGS:
            return tok
    return "en"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="corner_pdf", description="语言角主持脚本 Markdown -> 朗读版 PDF"
    )
    ap.add_argument("src", type=Path, help="输入的 .md 文件")
    ap.add_argument("-o", "--out", type=Path, help="输出的 .pdf（默认与 md 同名）")
    ap.add_argument("--lang", help="语言代码，用于断词与页眉（默认从文件名推断）")
    ap.add_argument("--kicker", help="页眉小字（默认按语言给出）")
    ap.add_argument("--font", default="Georgia, Segoe UI", help="字体族，逗号分隔")
    ap.add_argument("--typ-out", type=Path, help="中间 .typ 的落点（默认与 pdf 同名）")
    ap.add_argument("--keep-typ", action="store_true", help="保留中间 .typ")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    src: Path = a.src
    if not src.is_file():
        print(f"✗ 找不到输入文件：{src}", file=sys.stderr)
        return 2

    pdf: Path = a.out or src.with_suffix(".pdf")
    if not pdf.is_absolute():
        pdf = pdf.resolve()
    typ = a.typ_out or pdf.with_suffix(".typ")
    drop_typ = not a.keep_typ and a.typ_out is None

    lang = a.lang or guess_lang(src.stem)
    kicker = a.kicker or KICKER.get(lang, "CONVERSATION CIRCLE")
    fonts = ", ".join(f'"{f.strip()}"' for f in a.font.split(","))

    head, blocks = parse(src.read_text(encoding="utf-8").splitlines())
    if not any(k == "title" for k, _ in head):
        print("✗ md 缺少一级标题（# ...）", file=sys.stderr)
        return 2

    title = next(p for k, p in head if k == "title")
    short = re.sub(r"^[^—–]*\bCorner\b[^—–]*[—–]\s*", "", title).strip()
    metas = [p for k, p in head if k == "meta"]
    meta_lines = []
    for m in metas:
        mb = META_BOLD.match(m)  # - **键:** 值
        if mb:
            k, v = mb.group(1).rstrip(":").strip(), mb.group(2)
        else:
            parts = re.split(r"\s*:\s*", m, maxsplit=1)
            k, v = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")
        meta_lines.append(f"#meta-row[{rich(k)}][{rich(v)}]")

    pre = (
        PREAMBLE.replace("__DOCTITLE__", short.replace('"', '\\"'))
        .replace("__LANG__", lang)
        .replace("__FONT__", fonts)
        .replace("__KICKER__", kicker)
    )

    title_size = 19 if len(short) <= 46 else (16 if len(short) <= 60 else 14)
    doc = "\n".join(
        [
            pre,
            f"#title-page({title_size}pt)[{rich(short)}]",
            "#v(0.9em)",
            "\n".join(meta_lines),
            "#v(0.4em)",
            render(blocks),
        ]
    )
    typ.parent.mkdir(parents=True, exist_ok=True)
    typ.write_text(doc, encoding="utf-8")

    pdf.parent.mkdir(parents=True, exist_ok=True)
    # typst 由 PATH 提供，安装位置随机器而异；包内禁止写死本机绝对路径，
    # 故按名称调用，就地抑制 S607（不是放宽全仓规则）。
    r = subprocess.run(
        ["typst", "compile", str(typ), str(pdf)],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=str(src.parent),  # 让 md 里的相对路径（图片等）能解析
        check=False,
    )
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)
        print(f"✗ typst 编译失败（中间文件：{typ}）", file=sys.stderr)
        return r.returncode

    if drop_typ:
        typ.unlink(missing_ok=True)
    if not a.quiet:
        print(f"✓ {pdf}  ({pdf.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
