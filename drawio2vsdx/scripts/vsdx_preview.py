#!/usr/bin/env python3
# ruff: noqa: B023, PLW1510, S314, S607, UP031
# 门控内联抑制（skill-draft 约定）：解析对象是本包自己生成的本地 .drawio/.vsdx，
# 不是不可信网络输入；包内只用标准库、不能安装 defusedxml，故 S314 不适用。
# 以下规则按各文件实际命中情况精确列出；逐项改写会引入行为漂移。
# 逐项改写会引入行为漂移。完整理由见 references/contract.md 第 8 节。
"""vsdx_preview.py — 不打开 Visio，直接把 .vsdx 反向渲染成 SVG/HTML/PNG 预览。

用途：drawio2vsdx.py 的产物校验与目视核对（本机若不便启动 Visio COM 时尤其有用）。
渲染器只读取 VSDX 的 page*.xml（Shape/Cell/Geometry/Text），因此它验证的是
「写进 .vsdx 的几何与文本」，不是 Visio 自身的排版引擎。

用法
----
    python vsdx_preview.py                                  # 全部 .vsdx -> HTML
    python vsdx_preview.py --png-dir DIR                    # 额外导出 PNG（需 rsvg-convert）
    python vsdx_preview.py --only 09-KNN与SVM 16-2-DNN推理
"""

from __future__ import annotations

import argparse
import html
import math
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

PPI = 96.0
L = lambda tag: tag.rsplit("}", 1)[-1]  # noqa: E731


def cells_of(shape: ET.Element) -> dict[str, str]:
    return {e.get("N"): e.get("V") for e in shape.iter() if L(e.tag) == "Cell"}  # ty: ignore[invalid-return-type]


def svg_of(vsdx: Path) -> tuple[str, float, float, int]:
    with zipfile.ZipFile(vsdx) as z:
        pages = ET.fromstring(z.read("visio/pages/pages.xml"))
        root = ET.fromstring(z.read("visio/pages/page1.xml"))
    pw = float(pages.find('.//{*}Cell[@N="PageWidth"]').get("V"))  # ty: ignore[invalid-argument-type, missing-union-attribute]
    ph = float(pages.find('.//{*}Cell[@N="PageHeight"]').get("V"))  # ty: ignore[invalid-argument-type, missing-union-attribute]
    w_px, h_px = pw * PPI, ph * PPI
    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px:.0f}" height="{h_px:.0f}" '
            f'viewBox="0 0 {w_px:.0f} {h_px:.0f}" style="width:100%;height:auto;background:#fff">'
        ),
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
    ]
    count = 0
    for shape in root.iter():
        if L(shape.tag) != "Shape":
            continue
        count += 1
        c = cells_of(shape)

        def num(key: str, default: float = 0.0) -> float:
            try:
                return float(c.get(key, default))
            except TypeError:
                return default
            except ValueError:
                return default

        x, y, w, h = num("PinX"), num("PinY"), num("Width"), num("Height")
        left, top = (x - w / 2) * PPI, (ph - y - h / 2) * PPI
        fill = c.get("FillForegnd", "#FFFFFF")
        line = c.get("LineColor", "#B1B1B1")
        lw = num("LineWeight", 0.01) * PPI
        pattern = c.get("FillPattern", "1")

        pts: list[tuple[float, float]] = []
        for row in shape.iter():
            if L(row.tag) != "Row":
                continue
            cx, cy = row.find('{*}Cell[@N="X"]'), row.find('{*}Cell[@N="Y"]')
            if cx is None or cy is None:
                continue
            u, v = float(cx.get("V")), float(cy.get("V"))  # ty: ignore[invalid-argument-type]
            if row.get("T", "").startswith("Rel"):
                pts.append((left + u * w * PPI, top + v * h * PPI))
            else:
                pts.append((left + u * PPI, top + h * PPI - v * PPI))
        if len(pts) < 2:
            continue
        closed = (
            abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6
        )
        d = (
            "M "
            + " L ".join("{:.2f} {:.2f}".format(*p) for p in pts)
            + (" Z" if closed else "")
        )
        fill_v = fill if (pattern != "0" and closed) else "none"
        line_v = "none" if lw < 1e-6 else line
        out.append(
            f'<path d="{d}" fill="{fill_v}" stroke="{line_v}" stroke-width="{max(lw, 0.6):.2f}" '
            'stroke-linejoin="round"/>'
        )
        if "EndArrow" in c and not closed:
            (ax, ay), (bx, by) = pts[-2], pts[-1]
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy) or 1.0
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux
            tri = [
                (bx, by),
                (bx - ux * 8 + nx * 3.6, by - uy * 8 + ny * 3.6),
                (bx - ux * 8 - nx * 3.6, by - uy * 8 - ny * 3.6),
            ]
            out.append(
                '<polygon points="{}" fill="{}"/>'.format(
                    " ".join("{:.2f},{:.2f}".format(*p) for p in tri), line
                )
            )
        text = shape.find("{*}Text")
        if text is not None and (text.text or "").strip():
            fs = num("Size", 10 / 72.0) * PPI
            color = c.get("Color", "#000000")
            cx, cy = left + w * PPI / 2, top + h * PPI / 2
            lines = text.text.split("\n")  # ty: ignore[missing-union-attribute]
            if len(lines) == 1:
                out.append(
                    f'<text x="{cx:.2f}" y="{cy:.2f}" font-size="{fs:.2f}" fill="{color}" '
                    'text-anchor="middle" font-family="Microsoft YaHei, sans-serif" '
                    f'dy=".35em">{html.escape(lines[0])}</text>'
                )
            else:
                # Visio 里换行是真实的多行，这里按行拆成 tspan，预览才与 Visio 一致。
                spans = []
                for i, line in enumerate(lines):
                    offset = (i - (len(lines) - 1) / 2) * fs * 1.25 + fs * 0.35
                    spans.append(
                        f'<tspan x="{cx:.2f}" dy="{offset:.2f}">{html.escape(line)}</tspan>'
                    )
                out.append(
                    '<text y="{:.2f}" font-size="{:.2f}" fill="{}" text-anchor="middle" '
                    'font-family="Microsoft YaHei, sans-serif">{}</text>'.format(
                        cy, fs, color, "".join(spans)
                    )
                )
    out.append("</svg>")
    return "\n".join(out), w_px, h_px, count


def main() -> int:
    ap = argparse.ArgumentParser(description=".vsdx -> SVG/HTML/PNG 反向预览")
    ap.add_argument("--in-dir", default=str(Path.cwd()))
    ap.add_argument(
        "--out-html", default=None, help="默认 <in-dir>/.workbuddy/vsdx-preview.html"
    )
    ap.add_argument("--png-dir", default=None, help="可选：用 rsvg-convert 导出 PNG")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_html = (
        Path(args.out_html)
        if args.out_html
        else in_dir / ".workbuddy/vsdx-preview.html"
    )
    files = sorted(in_dir.glob("*.vsdx"))
    if args.only:
        files = [f for f in files if any(k in f.stem for k in args.only)]
    if not files:
        print("未找到 .vsdx：" + str(in_dir))
        return 1
    png_dir = Path(args.png_dir) if args.png_dir else None
    if png_dir:
        png_dir.mkdir(parents=True, exist_ok=True)

    cards, png_ok = [], 0
    for f in files:
        svg, w, h, n = svg_of(f)
        cards.append(
            '<section class="card"><h3>%s</h3>'
            '<p class="meta">%.0f x %.0f px · %d 形状</p>%s</section>'
            % (html.escape(f.stem), w, h, n, svg)
        )
        if png_dir:
            tmp = png_dir / (f.stem + ".svg")
            tmp.write_text(svg, encoding="utf-8")
            r = subprocess.run(
                ["rsvg-convert", "-o", str(png_dir / (f.stem + ".png")), str(tmp)],
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                png_ok += 1
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>VSDX 转换结果预览</title><style>"
        "body{{margin:0;padding:24px;background:#f7f8fa;color:#222;"
        'font-family:"Microsoft YaHei",system-ui,sans-serif}}'
        "h1{{font-size:20px;margin:0 0 4px}}"
        "p.sub{{font-size:12px;color:#666;margin:0 0 20px}}"
        ".grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(560px,1fr));gap:16px}}"
        ".card{{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:12px}}"
        ".card h3{{font-size:13px;margin:0 0 2px;color:#185FA5}}"
        ".meta{{font-size:11px;color:#888;margin:0 0 8px}}"
        "</style></head><body><h1>VSDX 转换结果预览</h1>"
        '<p class="sub">由 .vsdx 内的 Shape / Geometry / Text 反向渲染，用于核对转换数据；'
        '非 Visio 自身排版。</p><div class="grid">{}</div></body></html>'.format(
            "".join(cards)
        ),
        encoding="utf-8",
    )
    print("预览：%d 张 -> %s" % (len(files), out_html))
    if png_dir:
        print("PNG：%d/%d 张 -> %s" % (png_ok, len(files), png_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
