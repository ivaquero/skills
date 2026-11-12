#!/usr/bin/env python3
# ruff: noqa: PERF401, S314
# 门控内联抑制（skill-draft 约定）：解析对象是本包自己生成的本地 .drawio/.vsdx，
# 不是不可信网络输入；包内只用标准库、不能安装 defusedxml，故 S314 不适用。
# 以下规则按各文件实际命中情况精确列出；逐项改写会引入行为漂移。
# 逐项改写会引入行为漂移。完整理由见 references/contract.md 第 8 节。
"""校验一批 .drawio -> .vsdx 转换结果（结构、文本、形状数、几何不变量）。

    python verify_vsdx.py --drawio-dir DIR --vsdx-dir DIR
    python verify_vsdx.py --drawio-dir DIR --vsdx-dir DIR --rules PATH

规则表默认是同包 references/shape-rules.json。退出码 0 表示全部通过。
本脚本刻意不 import 转换器：校验器必须能独立审计任意实现产出的 VSDX。
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import math
import re
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

EPS = 1e-5


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def visio_text(text: str) -> str:
    """draw.io 的 <br> 在 VSDX 里是真换行；比对前必须归一化。"""
    return re.sub(r"<br\s*/?>", "\n", str(text), flags=re.IGNORECASE)


def model_of(diagram: ET.Element) -> ET.Element:
    child = diagram.find("mxGraphModel")
    if child is not None:
        return child
    raw = (diagram.text or "").strip()
    if not raw:
        raise ValueError("diagram 无 mxGraphModel 且无可解压内容")
    data = base64.b64decode(raw)
    try:
        data = zlib.decompress(data, -15)
    except zlib.error:
        data = zlib.decompress(data)
    return ET.fromstring(unquote(data.decode("utf-8")))


def has_arrow(cell: ET.Element) -> bool:
    """与转换器一致：endArrow 缺省视为 block（有箭头）。"""
    st = dict(p.split("=", 1) for p in (cell.get("style") or "").split(";") if "=" in p)
    return st.get("endArrow", "block").lower() not in ("none", "")


def source_stats(path: Path) -> tuple[collections.Counter, int, int, int]:
    """从源 .drawio 直接数文本、形状数、箭头数与页数，不依赖转换器。"""
    root = ET.parse(path).getroot()
    diagrams = root.findall("diagram") if root.tag == "mxfile" else [root]
    if not diagrams:
        raise ValueError("未找到 diagram")
    texts: collections.Counter = collections.Counter()
    n_shapes = 0
    n_arrows = 0
    for diagram in diagrams:
        for cell in model_of(diagram).findall(".//mxCell"):
            value = cell.get("value") or ""
            if cell.get("edge") == "1":
                n_shapes += 1
                if has_arrow(cell):
                    n_shapes += 1  # 每条带箭头的边另有一个 ArrowHead 形状
                    n_arrows += 1
                if value.strip():
                    texts[visio_text(value)] += 1
                    n_shapes += 1
            elif cell.get("vertex") == "1":
                n_shapes += 1
                if value.strip():
                    texts[visio_text(value)] += 1
    return texts, n_shapes, len(diagrams), n_arrows


def shape_points(shape: ET.Element) -> list[tuple[float, float]]:
    """返回归一化 (u, v)；v = 1 - Y，左上原点。"""
    pts: list[tuple[float, float]] = []
    for row in shape.iter():
        if localname(row.tag) != "Row":
            continue
        cx = row.find('{*}Cell[@N="X"]')
        cy = row.find('{*}Cell[@N="Y"]')
        if cx is not None and cy is not None:
            pts.append((float(cx.get("V", 0)), 1.0 - float(cy.get("V", 0))))
    return pts


def check_geometry(root: ET.Element, rules: dict, label: str) -> list[str]:
    errs: list[str] = []
    shapes = rules["shapes"]
    doc = rules["document"]
    base = doc["baseline"]
    for s in root.iter():
        if localname(s.tag) != "Shape":
            continue
        name = s.get("NameU")
        spec = shapes.get(name)
        if spec is None:
            continue  # Connector 用绝对局部坐标，不参与归一化断言
        pts = shape_points(s)
        sid = s.get("ID")
        if len(pts) != spec["points"]:
            errs.append(
                f"{label} shape {sid} ({name}): {len(pts)} 点 != {spec['points']}"
            )
            continue
        vs = [v for _, v in pts]
        if min(vs) < spec["v_min"] - EPS or max(vs) > spec["v_max"] + EPS:
            errs.append(
                f"{label} shape {sid} ({name}): v 越界 {min(vs):.4f}..{max(vs):.4f}"
            )
        if name != "Document":
            continue
        bottom = [v for _, v in pts[2:-1]]  # 排除最后的闭合点
        if abs(bottom[0] - base) > EPS or abs(bottom[-1] - base) > EPS:
            errs.append(f"{label} shape {sid}: 下角不在基线 {base:.2f}")
        if (
            min(bottom) < doc["bottom_v_min"] - EPS
            or max(bottom) > doc["bottom_v_max"] + EPS
        ):
            errs.append(
                f"{label} shape {sid}: 波浪越界 {min(bottom):.4f}..{max(bottom):.4f}"
            )
        if doc["s_wave"] and not (
            any(v < base - EPS for v in bottom) and any(v > base + EPS for v in bottom)
        ):
            errs.append(f"{label} shape {sid}: 底边不是 S 形波")
    return errs


def cells_of(shape: ET.Element) -> dict[str, str]:
    return {
        e.get("N", ""): e.get("V", "")
        for e in shape.iter()
        if localname(e.tag) == "Cell"
    }


def geometry_points(shape: ET.Element) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for row in shape.iter():
        if localname(row.tag) != "Row":
            continue
        cx = row.find('{*}Cell[@N="X"]')
        cy = row.find('{*}Cell[@N="Y"]')
        if cx is not None and cy is not None:
            pts.append((float(cx.get("V", 0)), float(cy.get("V", 0))))
    return pts


def check_arrows(root: ET.Element, rules: dict, label: str) -> tuple[list[str], int]:
    """箭头必须是 VSDX 里的几何三角形：数量、闭合、填充色、尖端对齐连线终点。"""
    errs: list[str] = []
    ar = rules["arrows"]
    tol = ar["tip_tolerance_in"]
    shapes = [s for s in root.iter() if localname(s.tag) == "Shape"]
    connectors = [s for s in shapes if s.get("NameU") == "Connector"]
    heads = [s for s in shapes if s.get("NameU") == "ArrowHead"]
    line_colors = {cells_of(s).get("LineColor") for s in connectors}
    ends: list[tuple[float, float]] = []
    for s in connectors:
        c = cells_of(s)
        pts = geometry_points(s)
        if len(pts) < 2:
            errs.append(f"{label}: Connector {s.get('ID')} 几何点数不足")
            continue
        ox = float(c["PinX"]) - float(c["Width"]) / 2
        oy = float(c["PinY"]) - float(c["Height"]) / 2
        ends.append((ox + pts[-1][0], oy + pts[-1][1]))
    for s in heads:
        c = cells_of(s)
        pts = geometry_points(s)
        sid = s.get("ID")
        if len(pts) != ar["points"] or pts[0] != pts[-1]:
            errs.append(f"{label}: 箭头 {sid} 不是 {ar['points']} 点闭合三角")
            continue
        if c.get("FillPattern") != "1":
            errs.append(
                f"{label}: 箭头 {sid} 未填充（FillPattern={c.get('FillPattern')}）"
            )
        if c.get("FillForegnd") not in line_colors:
            errs.append(f"{label}: 箭头 {sid} 填充色与连线不一致")
        ox = float(c["PinX"]) - float(c["Width"]) / 2
        oy = float(c["PinY"]) - float(c["Height"]) / 2
        tip = (
            ox + pts[0][0] * float(c["Width"]),
            oy + pts[0][1] * float(c["Height"]),
        )
        if not any(math.hypot(tip[0] - e[0], tip[1] - e[1]) <= tol for e in ends):
            errs.append(f"{label}: 箭头 {sid} 尖端未落在任何连线终点（方向可能反了）")
    return errs, len(heads)


def check_pair(drawio: Path, vsdx: Path, rules: dict) -> tuple[list[str], dict]:
    errs: list[str] = []
    label = vsdx.name
    try:
        texts, n_shapes, n_pages, n_arrows = source_stats(drawio)
    except (ET.ParseError, ValueError) as exc:
        return [f"{label}: 源 drawio 解析失败 {exc}"], {}

    with zipfile.ZipFile(vsdx) as z:
        if z.testzip() is not None:
            return [f"{label}: ZIP CRC 失败"], {}
        names = set(z.namelist())
        for part in rules["required_parts"]:
            if part not in names:
                errs.append(f"{label}: 缺部件 {part}")
        if errs:
            return errs, {}
        pages = sorted(
            (n for n in names if re.fullmatch(r"visio/pages/page\d+\.xml", n)),
            key=lambda n: int(re.search(r"page(\d+)", n).group(1)),  # ty: ignore[missing-union-attribute]
        )
        if len(pages) != n_pages:
            errs.append(f"{label}: 页数 {len(pages)} != {n_pages}")
        got: collections.Counter = collections.Counter()
        total = 0
        n_heads = 0
        for part in pages:
            raw = z.read(part)
            for token in rules["compat"]["forbidden_bytes"]:
                if token.encode() in raw:
                    errs.append(f"{label}: 含禁用写法 {token}")
            for token in rules["compat"]["required_bytes"]:
                if token.encode() not in raw:
                    errs.append(f"{label}: 缺兼容项 {token}")
            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                errs.append(f"{label}: {part} XML 解析失败 {exc}")
                continue
            for el in root.iter():
                tag = localname(el.tag)
                if tag == "Shape":
                    total += 1
                elif tag == "Text" and (el.text or "").strip():
                    got[el.text] += 1
            errs.extend(check_geometry(root, rules, label))
            arrow_errs, heads = check_arrows(root, rules, label)
            errs.extend(arrow_errs)
            n_heads += heads

    if n_heads != n_arrows:
        errs.append(f"{label}: 箭头数 {n_heads} != 源中带箭头连线数 {n_arrows}")
    if got != texts:
        errs.append(
            f"{label}: 文本不符 缺{sum((texts - got).values())}/多{sum((got - texts).values())}"
        )
    if total != n_shapes:
        errs.append(f"{label}: 形状数 {total} != {n_shapes}")
    return errs, {
        "shapes": total,
        "texts": sum(got.values()),
        "pages": len(pages),
        "arrows": n_heads,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="校验 .drawio -> .vsdx 转换结果")
    ap.add_argument("--drawio-dir", default=str(Path.cwd()))
    ap.add_argument("--vsdx-dir", default=str(Path.cwd()))
    ap.add_argument(
        "--rules",
        default=str(
            Path(__file__).resolve().parents[1] / "references" / "shape-rules.json"
        ),
    )
    args = ap.parse_args()

    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    ddir, vdir = Path(args.drawio_dir), Path(args.vsdx_dir)
    draws = {f.stem: f for f in sorted(ddir.glob("*.drawio"))}
    vsdxx = {f.stem: f for f in sorted(vdir.glob("*.vsdx"))}
    errs = [f"缺产物: {stem}.vsdx" for stem in sorted(set(draws) - set(vsdxx))]
    errs += [f"多余产物: {stem}.vsdx" for stem in sorted(set(vsdxx) - set(draws))]

    n_shape = n_text = n_arrow = 0
    for stem in sorted(set(draws) & set(vsdxx)):
        e, stat = check_pair(draws[stem], vsdxx[stem], rules)
        errs.extend(e)
        n_shape += stat.get("shapes", 0)
        n_text += stat.get("texts", 0)
        n_arrow += stat.get("arrows", 0)

    print(
        f"配对 {len(set(draws) & set(vsdxx))} 组 | {n_shape} 形状 | {n_text} 文本 | "
        f"{n_arrow} 箭头 | 失败 {len(errs)} 项"
    )
    for e in errs[:20]:
        print("  - " + e)
    if len(errs) > 20:
        print(f"  ... 另有 {len(errs) - 20} 项")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
