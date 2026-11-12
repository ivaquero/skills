#!/usr/bin/env python3
# ruff: noqa: B023, S314, UP031
# 门控内联抑制（skill-draft 约定）：解析对象是本包自己生成的本地 .drawio/.vsdx，
# 不是不可信网络输入；包内只用标准库、不能安装 defusedxml，故 S314 不适用。
# 以下规则按各文件实际命中情况精确列出；逐项改写会引入行为漂移。
# 逐项改写会引入行为漂移。完整理由见 references/contract.md 第 8 节。
"""drawio2vsdx.py — draw.io (.drawio / mxGraphModel) -> Visio (.vsdx) 保真转换器。

用法
----
    python drawio2vsdx.py                                   # 转换项目根目录下全部 .drawio
    python drawio2vsdx.py --in-dir DIR [--out-dir DIR]     # 指定输入/输出目录
    python drawio2vsdx.py --only 09-KNN与SVM --ppi 96      # 只转一张并指定像素密度
    python drawio2vsdx.py --in-dir DIR --out-dir DIR --no-verify

默认 --in-dir 为当前工作目录，--out-dir 与 --in-dir 相同；所有目录都由
命令行参数给出，包内不写本机绝对路径。输出文件名与源文件同名
（`<编号>-<讲名>.drawio` -> `<编号>-<讲名>.vsdx`）。

转换规则
--------
1. 单位：draw.io 坐标按 --ppi（默认 96 px/inch）等比换算为英寸；字号 px 同样
   换算（Visio Char.Size 单位为英寸）。Visio 页面按每个 diagram 的
   pageWidth/pageHeight 自适应，因此不同尺寸的图保持原始相对布局。
2. 坐标：draw.io 原点在左上、y 向下；Visio 原点在左下、y 向上，做翻转变换。
3. 形状：所有节点统一用「归一化折线多边形」(RelMoveTo/RelLineTo) 表达，
   刻意避开 RelEllipticalArcTo — 该行的 A/B 若传英寸绝对值会生成巨型弧面覆盖
   整页。圆角/胶囊、圆柱体、波浪底文档均以多段折线近似，Visio 渲染确定。
   其中 document（mxgraph.flowchart.document）按参考图还原为「顶/左/右三边
   平直、左右下角为直角、底边一条 S 形波浪」，波浪由 style 的 size 控制。
4. 连线：从源/目标矩形边界裁剪后画 1-D Connector；箭头另画成实心三角
   几何形状 ArrowHead（不用 EndArrow 单元格，见 contract.md 第 9 节）；
   关系标签做成白底独立文本框，按 mxGeometry/@x 沿连线插值定位。
5. 样式：颜色直接写 #RRGGBB（写 RGB(r,g,b) 在 Visio 会导出全黑）；中文显式
   AsianFont=Microsoft YaHei；Shape 上不写 Master="-1"（会让 Visio 静默丢弃整页）。

已验证的边界
------------
支持明文与 deflate 压缩的 diagram、多个 diagram（-> 多页）、父子顶点坐标累加、
fillColor/strokeColor=none。不支持 waypoints 折线路由、嵌入图片、超链接、
组容器形状本身（会当成普通节点）与单元格公式。

产物校验：脚本生成后会重新打开 .vsdx，核对 ZIP 完整性、每页 XML 可解析、
Shape 数，以及非空文本多重集与源 .drawio 完全一致。
"""

from __future__ import annotations

import argparse
import base64
import collections
import html
import math
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

# ---------------------------------------------------------------- OPC 骨架
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
NS = "http://schemas.microsoft.com/office/visio/2012/main"
FONT = "Microsoft YaHei"

CONTENT_TYPES = XML_HEAD + (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>'
    '<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>'
    "{page_overrides}"
    '<Override PartName="/visio/windows.xml" ContentType="application/vnd.ms-visio.windows+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    "</Types>"
)

ROOT_RELS = XML_HEAD + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    "</Relationships>"
)
DOC_RELS = XML_HEAD + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.microsoft.com/visio/2010/relationships/windows" Target="windows.xml"/>'
    "</Relationships>"
)
CORE_PROPS = XML_HEAD + (
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<dc:creator>drawio2vsdx</dc:creator><cp:lastModifiedBy>drawio2vsdx</cp:lastModifiedBy>"
    "</cp:coreProperties>"
)
APP_PROPS = XML_HEAD + (
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    "<Application>drawio2vsdx</Application></Properties>"
)
WINDOWS = XML_HEAD + (
    f'<Windows ClientWidth="1" ClientHeight="1" xmlns="{NS}">'
    '<Window ID="0" WindowType="Drawing" StencilGroup="-1" StencilParentID="-1"/>'
    "</Windows>"
)
DOCUMENT = XML_HEAD + (
    f'<VisioDocument xmlns="{NS}" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xml:space="preserve">'
    '<DocumentSettings TopPage="0" DefaultTextStyle="0" DefaultLineStyle="0" '
    'DefaultFillStyle="0" DefaultGuideStyle="0"/>'
    f'<FaceNames><FaceName ID="1" Name="{FONT}" UnicodeRanges="-1 -1 -1 -1"/></FaceNames>'
    "</VisioDocument>"
)


# ---------------------------------------------------------------- 小工具
def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def visio_text(text: str) -> str:
    """draw.io 靠 <br> 换行，Visio 不解析 HTML，必须换成真正的换行符。"""
    return re.sub(r"<br\s*/?>", "\n", str(text), flags=re.IGNORECASE)


def color(value: str | None, default: str) -> str:
    """只要 #RRGGBB；draw.io 的 none / default 归一为缺省色。"""
    if not value or value.lower() in ("none", "default", "transparent"):
        return default
    value = value.strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return value.upper()
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", value):
        return ("#" + "".join(c * 2 for c in value[1:])).upper()
    return default


def parse_style(style: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_width_px(text: str, font_px: float) -> float:
    """按中英文字符宽度估算标签框宽度。"""
    w = sum(font_px * (1.0 if ord(c) > 0x2E80 else 0.58) for c in text)
    return max(20.0, w + 10.0)


# ---------------------------------------------------------------- 折线几何
def rect_poly() -> list[tuple[float, float]]:
    return [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]


def rounded_poly(rx: float, ry: float, seg: int = 6) -> list[tuple[float, float]]:
    """归一化圆角矩形/胶囊；rx=r/w, ry=r/h，坐标为左上原点 (u,v)。"""
    pts: list[tuple[float, float]] = [(rx, 0.0), (1 - rx, 0.0)]
    corners = [
        (1 - rx, ry, -90.0),
        (1 - rx, 1 - ry, 0.0),
        (rx, 1 - ry, 90.0),
        (rx, ry, 180.0),
    ]
    for cx, cy, start in corners:
        for i in range(seg + 1):
            a = math.radians(start + 90.0 * i / seg)
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    pts.append((rx, 0.0))
    return pts


def cylinder_poly(e: float = 0.22, seg: int = 12) -> list[tuple[float, float]]:
    """圆柱体轮廓：顶椭圆上半弧 + 侧线 + 底椭圆下半弧。e=椭圆高占比。"""
    ry = e / 2.0
    y_top, y_bot = ry, 1.0 - ry
    pts: list[tuple[float, float]] = []
    for i in range(seg + 1):  # 左端 -> 顶端 -> 右端
        a = math.radians(180.0 - 180.0 * i / seg)
        pts.append((0.5 + 0.5 * math.cos(a), y_top - ry * math.sin(a)))
    pts.append((1.0, y_bot))
    for i in range(1, seg + 1):  # 右端 -> 底端 -> 左端
        a = math.radians(-180.0 * i / seg)
        pts.append((0.5 + 0.5 * math.cos(a), y_bot - ry * math.sin(a)))
    pts.append((0.0, y_top))
    pts.append(pts[0])
    return pts


def document_poly(
    wave: float = 0.16, cycles: float = 1.0, seg: int = 24
) -> list[tuple[float, float]]:
    """波浪底文档：顶/左/右三边平直，底边是一条完整的 S 形波浪。

    对照参考图（图片02-1.png 的「…案例」框）与 mxGraph flowchart.Document
    stencil：左右两个下角必须保持在基线上（竖边直落到底、呈直角），波浪只在
    基线上下起伏；从左到右依次为「下凹 -> 上凸」，即 cycles=1 的一个完整正弦
    波，而不是多团向下凸出的扇贝边。

    wave 为波浪总高占形状高度的比例（对应 draw.io 的 size，本项目均为 0.16）；
    base = 1 - wave/2 让波谷恰好落在框底 (v=1)、波峰落在 v=1-wave，全程不越界。
    """
    amp = wave / 2.0
    base = 1.0 - amp
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, base)]
    for i in range(1, seg + 1):
        u = 1.0 - i / seg
        # 从右下角 (u=1) 走向左下角 (u=0)：先上凸，再下凹，两端回到基线
        v = base - amp * math.sin(2 * math.pi * cycles * (1.0 - u))
        pts.append((u, v))
    pts.append((0.0, 0.0))
    return pts


def poly_to_geometry(poly: list[tuple[float, float]]) -> str:
    """归一化 (u, 0=顶) -> Visio 相对几何 (u, 1-v，y 向上)。"""
    rows = ['<Section N="Geometry" IX="0">']
    for i, (u, v) in enumerate(poly, start=1):
        t = "RelMoveTo" if i == 1 else "RelLineTo"
        rows.append(
            '<Row T="%s" IX="%d"><Cell N="X" V="%.6f"/><Cell N="Y" V="%.6f"/></Row>'
            % (t, i, u, 1.0 - v)
        )
    rows.append("</Section>")
    return "".join(rows)


# ---------------------------------------------------------------- Shape XML
def shape_xml(
    sid: int,
    name: str,
    box: tuple[float, float, float, float],
    poly,
    fill: str,
    line: str,
    line_w_in: float,
    text: str,
    size_in: float,
    text_color: str,
    has_fill: bool = True,
    has_line: bool = True,
) -> str:
    """box=(x,y,w,h) 英寸、左下原点；size_in 是 Visio Char.Size（英寸）。"""
    x, y, w, h = box
    cells = [
        f'<Cell N="PinX" V="{x + w / 2:.6f}"/><Cell N="PinY" V="{y + h / 2:.6f}"/>',
        f'<Cell N="Width" V="{w:.6f}"/><Cell N="Height" V="{h:.6f}"/>',
        f'<Cell N="LocPinX" V="{w / 2:.6f}" VUnit="IN_F"/><Cell N="LocPinY" V="{h / 2:.6f}" VUnit="IN_F"/>',
        '<Cell N="Angle" V="0"/>',
    ]
    if has_fill:
        cells.append(
            f'<Cell N="FillBkgnd" V="{fill}"/><Cell N="FillForegnd" V="{fill}"/>'
            '<Cell N="FillPattern" V="1"/>'
        )
    else:
        cells.append('<Cell N="FillPattern" V="0"/>')
    if has_line:
        cells.append(
            f'<Cell N="LineColor" V="{line}"/><Cell N="LineWeight" V="{line_w_in:.6f}"/>'
        )
    else:
        cells.append(f'<Cell N="LineColor" V="{line}"/><Cell N="LineWeight" V="0"/>')
    cells.append(f'<Cell N="Char.Color" V="{text_color}"/>')
    if text:
        cells.append(
            '<Section N="Character"><Row IX="0"><Cell N="Font" V="1"/>'
            f'<Cell N="AsianFont" V="{FONT}"/><Cell N="Size" V="{size_in:.6f}"/>'
            f'<Cell N="Color" V="{text_color}"/><Cell N="Case" V="0"/></Row></Section>'
        )
        cells.append(
            '<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="1"/></Row></Section>'
        )
    return '<Shape ID="%d" Type="Shape" NameU="%s" Name="%s">%s%s%s</Shape>' % (
        sid,
        name,
        name,
        "".join(cells),
        poly_to_geometry(poly),
        f"<Text>{esc(visio_text(text))}</Text>" if text else "",
    )


def arrow_head_xml(
    sid: int,
    p1_px: tuple[float, float],
    p2_px: tuple[float, float],
    ppi: float,
    page_h_px: float,
    color_hex: str,
    length_px: float,
    half_px: float,
) -> str:
    """把 draw.io 的 endArrow=block 画成实心三角形 Shape（NameU=ArrowHead）。

    尖端落在连线终点 p2，底边沿线段反向退 length_px；三角形完全落在目标
    节点框外，不会被上层节点遮住。刻意不写 EndArrow 单元格：Visio 对自建
    1-D 形状（无 Master、无 BeginX/EndX、无 EndArrowSize）不保证渲染该单元格，
    实测表现为「连线在、箭头没了」。几何三角形是普通填充多边形，任何渲染器
    都会画，且尺寸由源样式的 endSize 决定。
    """
    dx, dy = p2_px[0] - p1_px[0], p2_px[1] - p1_px[1]
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm
    nx, ny = -uy, ux
    bx, by = p2_px[0] - ux * length_px, p2_px[1] - uy * length_px
    tri_px = [
        p2_px,
        (bx + nx * half_px, by + ny * half_px),
        (bx - nx * half_px, by - ny * half_px),
    ]
    # 像素左上原点 -> 英寸左下原点
    tri = [(x / ppi, (page_h_px - y) / ppi) for x, y in tri_px]
    xs, ys = [p[0] for p in tri], [p[1] for p in tri]
    left, bottom = min(xs), min(ys)
    w, h = (max(xs) - left) or 1e-4, (max(ys) - bottom) or 1e-4
    poly = [((x - left) / w, 1.0 - (y - bottom) / h) for x, y in tri]
    poly.append(poly[0])  # 闭合，4 点
    return shape_xml(
        sid,
        "ArrowHead",
        (left, bottom, w, h),
        poly,
        color_hex,
        color_hex,
        0.0,
        "",
        0.0,
        color_hex,
        has_fill=True,
        has_line=False,
    )


def connector_xml(
    sid: int,
    p1: tuple[float, float],
    p2: tuple[float, float],
    line: str,
    line_w_in: float,
) -> str:
    """沿用 visio skill 中已被 Visio 实测打开的连接线骨架。

    形状框以线段中点为 Pin，Width/Height 取 |dx|,|dy|；Geometry 的起止点按
    dx/dy 符号换算到局部坐标，从而正确表达四个方向（原骨架固定 (0,0)->(w,h)，
    遇到 dx<0 或 dy<0 会把线画反）。箭头由 arrow_head_xml 单独生成。
    """
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    w = abs(dx) or 1e-4
    h = abs(dy) or 1e-4
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    sx, sy = w / 2 - dx / 2, h / 2 - dy / 2
    ex, ey = w / 2 + dx / 2, h / 2 + dy / 2
    cells = [
        f'<Cell N="PinX" V="{mx:.6f}"/><Cell N="PinY" V="{my:.6f}"/>',
        f'<Cell N="Width" V="{w:.6f}"/><Cell N="Height" V="{h:.6f}"/>',
        f'<Cell N="LocPinX" V="{w / 2:.6f}" VUnit="IN_F"/><Cell N="LocPinY" V="{h / 2:.6f}" VUnit="IN_F"/>',
        '<Cell N="Angle" V="0"/>',
        '<Cell N="FillPattern" V="0"/>',
        f'<Cell N="LineColor" V="{line}"/><Cell N="LineWeight" V="{line_w_in:.6f}"/>',
    ]
    geom = (
        '<Section N="Geometry" IX="0">'
        f'<Row T="MoveTo" IX="1"><Cell N="X" V="{sx:.6f}"/><Cell N="Y" V="{sy:.6f}"/></Row>'
        f'<Row T="LineTo" IX="2"><Cell N="X" V="{ex:.6f}"/><Cell N="Y" V="{ey:.6f}"/></Row>'
        "</Section>"
    )
    # 不写 BeginX/EndX 等 1-D 端点单元格：本转换器不含 glue 连接关系，
    # 线段位置完全由 Geometry 决定，避免 Visio 打开时按端点重算而错位。
    return (
        '<Shape ID="%d" Type="Shape" NameU="Connector" Name="Connector">%s%s</Shape>'
        % (sid, "".join(cells), geom)
    )


# ---------------------------------------------------------------- 解析 draw.io
def decompress_diagram(diagram: ET.Element) -> ET.Element:
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
    text = data.decode("utf-8")
    from urllib.parse import unquote

    return ET.fromstring(unquote(text))


def parse_drawio(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    diagrams = root.findall("diagram") if root.tag == "mxfile" else [root]
    if not diagrams:
        raise ValueError("未找到 diagram")

    out = []
    for diagram in diagrams:
        model = decompress_diagram(diagram)
        cells = {c.get("id"): c for c in model.findall(".//mxCell")}

        def geometry_of(cell: ET.Element) -> tuple[float, float, float, float]:
            g = cell.find("mxGeometry")
            if g is None:
                return (0.0, 0.0, 0.0, 0.0)
            x, y = float(g.get("x", 0)), float(g.get("y", 0))
            w, h = float(g.get("width", 0)), float(g.get("height", 0))
            parent = cells.get(cell.get("parent"))
            if parent is not None and parent.get("vertex") == "1":
                px, py, _, _ = geometry_of(parent)
                x, y = x + px, y + py
            return (x, y, w, h)

        nodes = []
        for c in model.findall(".//mxCell"):
            if c.get("vertex") != "1":
                continue
            x, y, w, h = geometry_of(c)
            st = parse_style(c.get("style"))
            nodes.append(
                {
                    "id": c.get("id"),
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "text": c.get("value") or "",
                    "style": st,
                }
            )
        by_id = {n["id"]: n for n in nodes}

        edges = []
        for c in model.findall(".//mxCell"):
            if c.get("edge") != "1":
                continue
            src, dst = c.get("source"), c.get("target")
            if src not in by_id or dst not in by_id:
                continue  # 悬空边：无两端顶点，无法绘制
            g = c.find("mxGeometry")
            edges.append(
                {
                    "from": src,
                    "to": dst,
                    "label": c.get("value") or "",
                    "t": float(g.get("x", 0.5)) if g is not None else 0.5,
                    "style": parse_style(c.get("style")),
                }
            )

        pw, ph = model.get("pageWidth"), model.get("pageHeight")
        if pw and ph:
            page_w, page_h = float(pw), float(ph)
        else:
            xs = [n["x"] for n in nodes] + [n["x"] + n["w"] for n in nodes]  # ty: ignore[unsupported-operator]
            ys = [n["y"] for n in nodes] + [n["y"] + n["h"] for n in nodes]  # ty: ignore[unsupported-operator]
            pad = 60.0
            page_w = (max(xs) - min(xs) + 2 * pad) if xs else 800.0
            page_h = (max(ys) - min(ys) + 2 * pad) if ys else 600.0
        name = diagram.get("name") or "Page-%d" % (len(out) + 1)
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        out.append(
            {
                "name": name,
                "page_w": page_w,
                "page_h": page_h,
                "nodes": nodes,
                "edges": edges,
                "by_id": by_id,
            }
        )
    return out


# ---------------------------------------------------------------- 页面构建
def clip_to_box(
    center: tuple[float, float],
    half_w: float,
    half_h: float,
    toward: tuple[float, float],
    gap: float,
) -> tuple[float, float]:
    dx, dy = toward[0] - center[0], toward[1] - center[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return center
    tx = (half_w + gap) / abs(dx) if dx else float("inf")
    ty = (half_h + gap) / abs(dy) if dy else float("inf")
    t = min(tx, ty, 1.0)
    return (center[0] + dx * t, center[1] + dy * t)


def node_polygon(node: dict) -> tuple[str, list[tuple[float, float]]]:
    st, w, h = node["style"], node["w"], node["h"]
    shape = st.get("shape", "")

    def frac(key: str, default: float) -> float:
        """draw.io 的 size 既可能是 0.16，也可能是百分数 22。"""
        try:
            v = float(st.get(key, default))
        except TypeError:
            return default
        except ValueError:
            return default
        if v > 1.0:
            v /= 100.0
        return min(max(v, 0.03), 0.45)

    if shape == "cylinder3":
        return "Cylinder", cylinder_poly(frac("size", 0.22))
    if "document" in shape:
        return "Document", document_poly(frac("size", 0.16))
    if st.get("rounded") == "1":
        arc = float(st.get("arcSize", 10))
        r = min(w, h) * arc / 100.0
        r = max(0.0, min(r, min(w, h) / 2.0))
        return "Capsule", rounded_poly(r / w if w else 0, r / h if h else 0)
    return "Rect", rect_poly()


def build_page(diagram: dict, ppi: float) -> dict:
    """像素坐标里做裁剪/插值，最后统一换算成英寸并翻转 y。"""
    page_w_px, page_h_px = diagram["page_w"], diagram["page_h"]
    page_w, page_h = page_w_px / ppi, page_h_px / ppi
    shapes: list[str] = []

    def to_in(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] / ppi, (page_h_px - point[1]) / ppi)

    # 1) 连线置于底层；同时记录关系标签的落点
    label_boxes: list[tuple[str, float, float, float, float, float, str]] = []
    for e in diagram["edges"]:
        a, b = diagram["by_id"][e["from"]], diagram["by_id"][e["to"]]
        ca = (a["x"] + a["w"] / 2, a["y"] + a["h"] / 2)
        cb = (b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        p1 = clip_to_box(ca, a["w"] / 2, a["h"] / 2, cb, 2.0)
        p2 = clip_to_box(cb, b["w"] / 2, b["h"] / 2, ca, 3.0)
        st = e["style"]
        line = color(st.get("strokeColor"), "#666666")
        lw = float(st.get("strokeWidth", 1)) / ppi
        arrow = st.get("endArrow", "block").lower() not in ("none", "")
        shapes.append(connector_xml(len(shapes) + 1, to_in(p1), to_in(p2), line, lw))
        if arrow:
            size = max(1.0, float(st.get("endSize", 7)))
            shapes.append(
                arrow_head_xml(
                    len(shapes) + 1,
                    p1,
                    p2,
                    ppi,
                    page_h_px,
                    line,
                    size,
                    size * 0.5,
                )
            )
        if e["label"].strip():
            t = e["t"]
            lx = p1[0] + (p2[0] - p1[0]) * t
            ly = p1[1] + (p2[1] - p1[1]) * t
            fs = float(st.get("fontSize", 9))
            label_boxes.append(
                (
                    e["label"],
                    lx,
                    ly,
                    text_width_px(e["label"], fs),
                    fs * 1.7,
                    fs,
                    color(st.get("fontColor"), "#333333"),
                )
            )

    # 2) 节点
    for n in diagram["nodes"]:
        st = n["style"]
        name, poly = node_polygon(n)
        fs = float(st.get("fontSize", 10))
        box = (
            n["x"] / ppi,
            (page_h_px - n["y"] - n["h"]) / ppi,
            n["w"] / ppi,
            n["h"] / ppi,
        )
        shapes.append(
            shape_xml(
                len(shapes) + 1,
                name,
                box,
                poly,
                color(st.get("fillColor"), "#F0F0F0"),
                color(st.get("strokeColor"), "#B1B1B1"),
                float(st.get("strokeWidth", 1)) / ppi,
                n["text"],
                fs / ppi,
                color(st.get("fontColor"), "#000000"),
                st.get("fillColor", "").lower() != "none",
                st.get("strokeColor", "").lower() != "none",
            )
        )

    # 3) 关系标签置于顶层（白底盖住连线）
    for text, lx, ly, tw, th, fs, tc in label_boxes:
        box = ((lx - tw / 2) / ppi, (page_h_px - ly - th / 2) / ppi, tw / ppi, th / ppi)
        shapes.append(
            shape_xml(
                len(shapes) + 1,
                "Label",
                box,
                rect_poly(),
                "#FFFFFF",
                "#FFFFFF",
                0.0,
                text,
                fs / ppi,
                tc,
                True,
                False,
            )
        )

    page_xml = XML_HEAD + (
        '<PageContents xmlns="{}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve"><Shapes>{}</Shapes><Connects/></PageContents>'.format(
            NS, "".join(shapes)
        )
    )
    return {
        "name": diagram["name"],
        "w": page_w,
        "h": page_h,
        "xml": page_xml,
        "shapes": len(shapes),
    }


def build_vsdx(diagrams: list[dict], out_path: Path, ppi: float) -> dict:
    """打包 OPC/ZIP；每个 draw.io diagram 对应 Visio 的一页。"""
    pages = [build_page(d, ppi) for d in diagrams]
    n = len(pages)
    overrides = "".join(
        '<Override PartName="/visio/pages/page%d.xml" '
        'ContentType="application/vnd.ms-visio.page+xml"/>' % (i + 1)
        for i in range(n)
    )
    page_elements = []
    for i, p in enumerate(pages):
        nm = esc(p["name"])
        page_elements.append(
            '<Page ID="%d" NameU="%s" Name="%s" Background="0" BackPage="-1" '
            'ViewScale="-1" ViewCenterX="%.6f" ViewCenterY="%.6f">'
            '<PageSheet LineStyle="0" FillStyle="0" TextStyle="0">'
            '<Cell N="PageWidth" V="%.6f"/><Cell N="PageHeight" V="%.6f"/>'
            '<Cell N="ShdwOffsetX" V="0.125"/><Cell N="ShdwOffsetY" V="-0.125"/>'
            '</PageSheet><Rel r:id="rId%d"/></Page>'
            % (i, nm, nm, p["w"] / 2, p["h"] / 2, p["w"], p["h"], i + 1)
        )
    pages_xml = XML_HEAD + (
        '<Pages xmlns="{}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve">{}</Pages>'.format(NS, "".join(page_elements))
    )
    rels = "".join(
        '<Relationship Id="rId%d" '
        'Type="http://schemas.microsoft.com/visio/2010/relationships/page" '
        'Target="page%d.xml"/>' % (i + 1, i + 1)
        for i in range(n)
    )
    pages_rels = XML_HEAD + (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml", CONTENT_TYPES.format(page_overrides=overrides)
        )
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("docProps/core.xml", CORE_PROPS)
        z.writestr("docProps/app.xml", APP_PROPS)
        z.writestr("visio/document.xml", DOCUMENT)
        z.writestr("visio/_rels/document.xml.rels", DOC_RELS)
        z.writestr("visio/windows.xml", WINDOWS)
        z.writestr("visio/pages/pages.xml", pages_xml)
        z.writestr("visio/pages/_rels/pages.xml.rels", pages_rels)
        for i, p in enumerate(pages):
            z.writestr("visio/pages/page%d.xml" % (i + 1), p["xml"])
    return {"pages": n, "shapes": sum(p["shapes"] for p in pages)}


def verify(vsdx: Path, diagrams: list[dict]) -> tuple[bool, str]:
    """ZIP 完整性 + XML 可解析 + 非空文本多重集与源完全一致。"""
    expected: collections.Counter = collections.Counter()
    for d in diagrams:
        for n in d["nodes"]:
            if n["text"].strip():
                expected[visio_text(n["text"])] += 1
        for e in d["edges"]:
            if e["label"].strip():
                expected[visio_text(e["label"])] += 1
    with zipfile.ZipFile(vsdx) as z:
        if z.testzip() is not None:
            return False, "ZIP CRC 校验失败"
        parts = sorted(
            (
                name
                for name in z.namelist()
                if re.fullmatch(r"visio/pages/page\d+\.xml", name)
            ),
            key=lambda s: int(re.search(r"page(\d+)", s).group(1)),  # ty: ignore[missing-union-attribute]
        )
        if len(parts) != len(diagrams):
            return False, "页数 %d != %d" % (len(parts), len(diagrams))
        got: collections.Counter = collections.Counter()
        total = 0
        for part in parts:
            root = ET.fromstring(z.read(part))
            for el in root.iter():
                tag = localname(el.tag)
                if tag == "Shape":
                    total += 1
                elif tag == "Text" and (el.text or "").strip():
                    got[el.text] += 1
    if got != expected:
        missing = expected - got
        extra = got - expected
        return False, "文本不符 (缺 %d / 多 %d): %s %s" % (
            sum(missing.values()),
            sum(extra.values()),
            list(missing.items())[:2],
            list(extra.items())[:2],
        )
    return True, "%d 页 / %d 形状 / %d 文本" % (len(parts), total, sum(got.values()))


def main() -> int:
    ap = argparse.ArgumentParser(description="draw.io -> Visio (.vsdx) 转换器")
    ap.add_argument(
        "--in-dir", default=str(Path.cwd()), help="输入目录（默认当前工作目录）"
    )
    ap.add_argument("--out-dir", default=None, help="输出目录（默认与输入相同）")
    ap.add_argument("--ppi", type=float, default=96.0, help="像素/英寸，默认 96")
    ap.add_argument("--only", nargs="*", default=None, help="只转文件名包含这些串的图")
    ap.add_argument("--no-verify", action="store_true", help="跳过生成后自检")
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir) if args.out_dir else in_dir
    files = sorted(in_dir.glob("*.drawio"))
    if args.only:
        files = [f for f in files if any(k in f.stem for k in args.only)]
    if not files:
        print(f"未找到 .drawio：{in_dir}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    print("输入 %d 个文件 | PPI=%.0f | 输出 -> %s" % (len(files), args.ppi, out_dir))
    print("-" * 96)
    print(
        "%-28s %11s %6s %6s %6s %9s  %s"
        % ("文件", "页面(px)", "节点", "连线", "标签", "大小", "校验")
    )
    failed = 0
    for f in files:
        diagrams = parse_drawio(f)
        out = out_dir / (f.stem + ".vsdx")
        build_vsdx(diagrams, out, args.ppi)
        if args.no_verify:
            status = "跳过"
        else:
            ok, msg = verify(out, diagrams)
            status = ("OK  " + msg) if ok else ("FAIL " + msg)
            if not ok:
                failed += 1
        d0 = diagrams[0]
        labels = sum(1 for d in diagrams for e in d["edges"] if e["label"].strip())
        print(
            "%-28s %5.0fx%-5.0f %6d %6d %6d %8.1fKB  %s"
            % (
                f.stem[:28],
                d0["page_w"],
                d0["page_h"],
                sum(len(d["nodes"]) for d in diagrams),
                sum(len(d["edges"]) for d in diagrams),
                labels,
                out.stat().st_size / 1024.0,
                status,
            )
        )
    print("-" * 96)
    print("完成：%d 个 .vsdx，失败 %d 个" % (len(files) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
