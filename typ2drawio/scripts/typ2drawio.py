#!/usr/bin/env python3
"""把一批 Typst 讲义批量生成为四向放射式 draw.io 概念图。

输入目录、输出目录、教案 docx 目录全部由命令行参数给出；包内不写本机绝对路径。
几何与配色见 references/layout.md，解析规则见 references/typ-parsing.md，
XML 契约见 references/output-contract.md。
"""

import argparse
import datetime
import html
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 几何常量：与 references/layout.md 的常量表逐项对应，selfcheck.py 会核对。
ROOT_W = 300
ROOT_H = 78
BRANCH_W = 176
BRANCH_H = 52
KID_W = 150
KID_H = 40
DOC_W = 160
DOC_H = 56
CYL_W = 152
CYL_H = 74
MAX_KIDS = 4
MAX_BRANCH = 9
GAP_ROOT = 100
GAP_BRANCH = 90
GAP_DOC = 80
STEP_H = 62
STEP_V = 172
ROW_GAP = 36
PAD = 60
CORNER_SHIFT = 48

# 派生半径：中心→主干、主干→知识点、知识点→案例，横竖两向分别计算。
D1H = ROOT_W / 2 + GAP_ROOT + BRANCH_W / 2
D2H = D1H + BRANCH_W / 2 + GAP_BRANCH + KID_W / 2
D3H = D2H + KID_W / 2 + GAP_DOC + DOC_W / 2
D1V = ROOT_H / 2 + GAP_ROOT + BRANCH_H / 2
D2V = D1V + BRANCH_H / 2 + GAP_BRANCH + KID_H / 2
D3V = D2V + KID_H / 2 + GAP_DOC + DOC_H / 2

FILL, STROKE, INK = "#F0F0F0", "#B1B1B1", "#000000"
CASE_FILL, CASE_STROKE = "#FFFFFF", "#BFA9C8"
API_FILL, API_STROKE = "#DAE8FC", "#6C8EBF"
RED, BLUE, LABEL = "#FF0000", "#0000FF", "#333333"

BASE = (
    "whiteSpace=wrap;html=1;align=center;verticalAlign=middle;"
    "fontFamily=Microsoft YaHei;"
)
GRAY_BOX = f"rounded=0;fillColor={FILL};strokeColor={STROKE};"
STYLES = {
    "root": f"{BASE}{GRAY_BOX}fontColor={INK};fontSize=14;",
    "branch": f"{BASE}{GRAY_BOX}fontColor={INK};fontSize=12;",
    "branch_red": f"{BASE}{GRAY_BOX}fontColor={RED};fontSize=12;",
    "branch_blue": f"{BASE}{GRAY_BOX}fontColor={BLUE};fontSize=12;",
    "kid": f"{BASE}{GRAY_BOX}fontColor={INK};fontSize=10;",
    "pill": f"{BASE}{GRAY_BOX}rounded=1;arcSize=50;fontColor={INK};fontSize=10;",
    "api": (
        f"{BASE}rounded=0;fillColor={API_FILL};strokeColor={API_STROKE};"
        f"fontColor={INK};fontSize=10;"
    ),
    "doc": (
        f"{BASE}shape=mxgraph.flowchart.document;size=0.16;"
        f"fillColor={CASE_FILL};strokeColor={CASE_STROKE};"
        f"fontColor={INK};fontSize=10;"
    ),
    "cyl": (
        f"{BASE}shape=cylinder3;boundedLbl=1;fillColor={FILL};"
        f"strokeColor={STROKE};fontColor={INK};fontSize=11;"
    ),
}
EDGE_STYLE = (
    "edgeStyle=noneExpr;rounded=0;html=1;strokeColor=#666666;strokeWidth=1;"
    "endArrow=block;endFill=1;endSize=7;startArrow=none;"
    f"fontSize=9;fontColor={LABEL};labelBackgroundColor=#FFFFFF;"
)

# 扇区顺序：右、左、上、下。主干按讲义顺序轮流落位，多则在同一扇区竖排/横排。
ZONES = ("R", "L", "T", "B")
BLUE_BRANCH = re.compile(r"识别|检测|应用|实战|实践|搜图|部署")
PILL_KID = re.compile(r"(方法|规范|流程|步骤|技巧|策略|准则|操作)$")
API_KID = re.compile(r"^(cv2?\.|np\.|dlib\.|sklearn\.|torch\.)|OpenCV|NumPy|scikit")

NOISE = re.compile(
    r"\\|frac|sum_|ctext\(|approx|times|norm\(|8/17|9/17|\u00d7|^\s*[-+*/]|\blog_"
    r"|max_|argmax|<=|>=|\u03b4|\u03bc_|\u03b8|\u2211|\u03a3|\bP\(|\bQ\(|\$"
)
SKIP_L1 = ("课后作业", "教学目标", "上节回顾")
SKIP_L2 = (
    "教学目标",
    "本讲小结",
    "课后作业",
    "课后",
    "上节回顾",
    "这一节在做什么",
    "读结果",
    "读这张表",
    "这一讲要回答的问题",
    "读这组数字",
)
TYP_NAME = re.compile(r"^v?(\d+)-(.+)\.typ$")
DOCX_KEY = re.compile(r"-(\d+(?:-\d+)?)\.docx$")

PREVIEW_HEAD = (
    '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
    "<title>思维导图集 · 自检预览</title>"
    "<style>body{margin:0;padding:28px;background:#f7f8fa;color:#222;"
    'font-family:"Microsoft YaHei",system-ui,sans-serif}'
    "h1{font-size:20px;margin:0 0 6px}"
    "h2{font-size:13px;font-weight:400;color:#666;margin:0 0 22px}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(620px,1fr));gap:18px}"
    ".card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:14px;"
    "box-shadow:0 1px 3px rgba(0,0,0,.05)}"
    ".card h3{font-size:13px;margin:0 0 4px;color:#185FA5}"
    ".meta{font-size:11px;color:#777;margin:0 0 10px}</style></head><body>"
    "<h1>思维导图集：四向放射式概念图</h1>"
    "<h2>讲义顺序 · 编号对齐教案 docx · 此页为同一几何的 SVG 自检渲染，"
    "正式交付为 .drawio</h2>"
    '<div class="grid">'
)
PREVIEW_TAIL = "</div></body></html>"


@dataclass(frozen=True)
class Labels:
    """可替换的课程专有词，让同一套版式能用于别的讲义。"""

    manual: str = "OpenCV 官方手册"


@dataclass
class Group:
    l1: str
    kids: list[str] = field(default_factory=list)
    ex: list[str] = field(default_factory=list)


@dataclass
class Branch:
    l1: str
    kids: list[str]
    ex: list[str]


@dataclass
class Vertex:
    id: str
    kind: str
    text: str
    x: float
    y: float
    w: float
    h: float
    cx: float
    cy: float


@dataclass
class Edge:
    src: str
    dst: str
    label: str
    pos: float | None = None


def tidy(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text).strip())


def clean_example(raw: str) -> str:
    """'示例 ex27.1_dlib_face_detect·dlib 摄像头检测' -> 'dlib 摄像头检测'。"""
    s = tidy(raw)
    s = re.sub(r"^示例\s*", "", s)
    s = re.sub(r"^案例[^·]*·\s*", "", s)
    if "·" in s:
        s = s.split("·")[-1].strip()
    s = re.sub(r"^ex[\w.]+\s*", "", s, flags=re.IGNORECASE)
    s = s.strip(" ·-–")
    if len(s) < 2 or re.fullmatch(r"[A-Za-z0-9_.\-]+", s):
        return ""
    return s[:28].strip()


def dedupe(seq: list[str], limit: int | None = None) -> list[str]:
    out: list[str] = []
    for item in seq:
        if item and item not in out:
            out.append(item)
    return out[:limit] if limit else out


def parse_typ(path: Path) -> tuple[list[Group], list[str]]:
    """解析标题行；公式行以 = 开头，跳过但保留当前分组。"""
    groups: list[Group] = []
    examples: list[str] = []
    cur: Group | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(={1,3})\s+(.+?)\s*$", line)
        if not m:
            continue
        level = len(m.group(1))
        raw = tidy(m.group(2))
        if not raw or NOISE.search(raw):
            continue
        if level == 1:
            if raw.startswith(SKIP_L1):
                cur = None
                continue
            cur = Group(l1=raw)
            groups.append(cur)
        elif cur is not None:
            if raw.startswith(("示例", "案例")):
                ex = clean_example(raw)
                if ex:
                    cur.ex.append(ex)
                    examples.append(ex)
                continue
            if not raw.startswith(SKIP_L2):
                cur.kids.append(raw)
    return groups, examples


def make_branches(groups: list[Group]) -> list[Branch]:
    out: list[Branch] = []
    seen: set[str] = set()
    for group in groups:
        l1 = re.sub(r"\d+$", "", group.l1).strip() or group.l1
        while l1 in seen:
            l1 += "（进阶）"
        seen.add(l1)
        ex = dedupe(group.ex, 2)
        kids = dedupe(group.kids, MAX_KIDS)
        if not kids and ex:
            kids, ex = dedupe(group.ex, MAX_KIDS), []
        out.append(Branch(l1=l1, kids=kids, ex=ex))
    return out


def esc(text: str) -> str:
    """换行写成 <br>，并转义 XML 属性值。"""
    return html.escape(text.replace("\n", "<br>"), quote=True)


def kid_kind(text: str) -> str:
    if API_KID.search(text):
        return "api"
    if PILL_KID.search(text):
        return "pill"
    return "kid"


def ring(
    zone: str, lateral: float, dist_h: float, dist_v: float
) -> tuple[float, float]:
    """把扇区与切向偏移换算成中心相对坐标。"""
    if zone == "R":
        return dist_h, lateral
    if zone == "L":
        return -dist_h, lateral
    if zone == "T":
        return lateral, -dist_v
    return lateral, dist_v


def offsets(count: int, step: float) -> list[float]:
    if count <= 1:
        return [0.0]
    return [(j - (count - 1) / 2) * step for j in range(count)]


@dataclass
class Diagram:
    name: str
    verts: list[Vertex] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    seq: int = 0
    page_w: int = 0
    page_h: int = 0

    def vertex(
        self,
        kind: str,
        text: str,
        cx: float,
        cy: float,
        w: float,
        h: float,
        vid: str | None = None,
    ) -> str:
        self.seq += 1
        cell_id = vid or f"v{self.seq}"
        self.verts.append(
            Vertex(
                id=cell_id,
                kind=kind,
                text=text,
                x=cx - w / 2,
                y=cy - h / 2,
                w=w,
                h=h,
                cx=cx,
                cy=cy,
            )
        )
        return cell_id

    def edge(self, src: str, dst: str, label: str, pos: float | None = None) -> None:
        self.edges.append(Edge(src=src, dst=dst, label=label, pos=pos))

    def normalize(self, pad: float) -> None:
        """把几何平移到正坐标，并按内容定画布尺寸。"""
        min_x = min(v.x for v in self.verts)
        min_y = min(v.y for v in self.verts)
        max_x = max(v.x + v.w for v in self.verts)
        max_y = max(v.y + v.h for v in self.verts)
        dx, dy = pad - min_x, pad - min_y
        for v in self.verts:
            v.x += dx
            v.y += dy
            v.cx += dx
            v.cy += dy
        self.page_w = int(max_x - min_x + 2 * pad)
        self.page_h = int(max_y - min_y + 2 * pad)

    def xml(self, modified: str) -> str:
        diagram_id = esc(self.name).replace(" ", "-")
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<mxfile host="app.diagrams.net" modified="{modified}" agent="typ2drawio" '
                'version="24.7.17" type="device">'
            ),
            f'<diagram id="{diagram_id}" name="{esc(self.name)}">',
            (
                f'<mxGraphModel dx="{self.page_w}" dy="{self.page_h}" grid="0" gridSize="10" '
                'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
                f'pageWidth="{self.page_w}" pageHeight="{self.page_h}" math="0" shadow="0">'
            ),
            "<root>",
            '<mxCell id="0"/>',
            '<mxCell id="1" parent="0"/>',
        ]
        parts.extend(
            f'<mxCell id="{v.id}" value="{esc(v.text)}" style="{STYLES[v.kind]}" '
            'vertex="1" parent="1">'
            f'<mxGeometry x="{round(v.x)}" y="{round(v.y)}" width="{round(v.w)}" '
            f'height="{round(v.h)}" as="geometry"/></mxCell>'
            for v in self.verts
        )
        for i, e in enumerate(self.edges, start=1):
            geom = (
                f'<mxGeometry x="{e.pos}" relative="1" as="geometry"/>'
                if e.pos is not None
                else '<mxGeometry relative="1" as="geometry"/>'
            )
            parts.append(
                f'<mxCell id="e{i}" value="{esc(e.label)}" style="{EDGE_STYLE}" edge="1" '
                f'parent="1" source="{e.src}" target="{e.dst}">{geom}</mxCell>'
            )
        parts.extend(["</root>", "</mxGraphModel>", "</diagram>", "</mxfile>"])
        return "".join(parts)


def build_diagram(
    key: str, title: str, branches: list[Branch], labels: Labels
) -> Diagram:
    d = Diagram(name=title)
    root = d.vertex("root", title, 0, 0, ROOT_W, ROOT_H, "center")

    items: list[tuple[str, str | Branch]] = [("branch", b) for b in branches]
    zones: dict[str, list[int]] = {z: [] for z in ZONES}
    for i in range(len(items)):
        zones[ZONES[i % 4]].append(i)
    # 手册圆柱体放进最空的扇区，避免它挤在别的分支旁边显得像是下级节点。
    spare = min(ZONES[::-1], key=lambda z: (len(zones[z]), ZONES[::-1].index(z)))
    items.append(("cyl", labels.manual))
    zones[spare].append(len(items) - 1)

    groups: dict[int, list[str]] = {}
    zone_of: dict[int, str] = {}
    for zone, idxs in zones.items():
        if not idxs:
            continue
        horizontal = zone in ("R", "L")
        step = STEP_H if horizontal else STEP_V
        across = KID_H if horizontal else KID_W
        extents: list[float] = []
        for i in idxs:
            payload = items[i][1]
            own = footprint(items[i][0], horizontal)
            fan = 0.0
            doc_span = 0.0
            if isinstance(payload, Branch):
                if payload.kids:
                    fan = (len(payload.kids[:MAX_KIDS]) - 1) * step + across
                if payload.ex:
                    n_doc = len(payload.ex[:2])
                    doc_step = DOC_H + 22 if horizontal else DOC_W + 22
                    doc_size = DOC_H if horizontal else DOC_W
                    doc_span = (n_doc - 1) * doc_step + doc_size
            extents.append(max(own, fan, doc_span))
        total = sum(extents) + ROW_GAP * (len(idxs) - 1)
        cursor = -total / 2
        for i, ext in zip(idxs, extents, strict=True):
            lat = cursor + ext / 2
            cursor += ext + ROW_GAP
            groups[i] = place_item(d, items[i], i, zone, lat, root, step)
            zone_of[i] = zone
    resolve_corners(d, items, groups, zone_of)
    d.normalize(PAD)
    return d


def footprint(kind: str, horizontal: bool) -> float:
    """节点在扇区切向上的自身尺寸。"""
    if kind == "cyl":
        return CYL_H if horizontal else CYL_W
    return BRANCH_H if horizontal else BRANCH_W


def place_item(
    d: Diagram,
    item: tuple[str, str | Branch],
    index: int,
    zone: str,
    lat: float,
    root: str,
    step: float,
) -> list[str]:
    kind, payload = item
    horizontal = zone in ("R", "L")
    ids: list[str] = []
    if kind == "cyl":
        cx, cy = ring(zone, lat, D1H, D1V)
        ids.append(d.vertex("cyl", str(payload), cx, cy, CYL_W, CYL_H))
        d.edge(root, ids[0], "包含", 0.62)
        return ids

    branch = payload
    assert isinstance(branch, Branch)
    bx, by = ring(zone, lat, D1H, D1V)
    accent = "red" if index == 0 else ("blue" if BLUE_BRANCH.search(branch.l1) else "")
    bid = d.vertex(
        f"branch_{accent}" if accent else "branch",
        branch.l1,
        bx,
        by,
        BRANCH_W,
        BRANCH_H,
    )
    ids.append(bid)
    d.edge(root, bid, "内容包含", 0.62)

    kids = branch.kids[:MAX_KIDS]
    kid_ids: list[tuple[str, float]] = []
    for kid, off in zip(kids, offsets(len(kids), step), strict=False):
        kx, ky = ring(zone, lat + off, D2H, D2V)
        kvid = d.vertex(kid_kind(kid), kid, kx, ky, KID_W, KID_H)
        kid_ids.append((kvid, off))
        ids.append(kvid)
        d.edge(bid, kvid, "包含", 0.58)

    if not branch.ex:
        return ids
    doc_step = DOC_H + 22 if horizontal else DOC_W + 22
    for doc, off in zip(
        branch.ex[:2], offsets(len(branch.ex[:2]), doc_step), strict=False
    ):
        dx, dy = ring(zone, lat + off, D3H, D3V)
        did = d.vertex("doc", doc, dx, dy, DOC_W, DOC_H)
        ids.append(did)
        host = min(kid_ids, key=lambda entry: abs(entry[1] - off))[0]
        d.edge(did, host, "支持", 0.45)
    return ids


def bbox(by_id: dict[str, Vertex], ids: list[str]) -> tuple[float, float, float, float]:
    vs = [by_id[i] for i in ids]
    return (
        min(v.x for v in vs),
        min(v.y for v in vs),
        max(v.x + v.w for v in vs),
        max(v.y + v.h for v in vs),
    )


def intersects(a: tuple[float, ...], b: tuple[float, ...], margin: float = 4) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > -margin) and (
        min(a[3], b[3]) - max(a[1], b[1]) > -margin
    )


def resolve_corners(
    d: Diagram,
    items: list[tuple[str, str | Branch]],
    groups: dict[int, list[str]],
    zones_of: dict[int, str],
) -> None:
    """上下扇区与左右扇区在四角会互相压住：把起冲突的上下项整体沿轴向外推。"""
    by_id = {v.id: v for v in d.verts}
    for _ in range(120):
        moved = False
        for i in sorted(groups):
            zone = zones_of[i]
            if zone not in ("T", "B"):
                continue
            here = bbox(by_id, groups[i])
            for j in sorted(groups):
                if j == i or not intersects(here, bbox(by_id, groups[j])):
                    continue
                dy = -CORNER_SHIFT if zone == "T" else CORNER_SHIFT
                for vid in groups[i]:
                    by_id[vid].y += dy
                    by_id[vid].cy += dy
                moved = True
                break
        if not moved:
            return


def svg_wrap(text: str, limit: int) -> list[str]:
    lines: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= limit:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    return lines


SVG_FONT = {
    "root": 14,
    "branch": 12,
    "branch_red": 12,
    "branch_blue": 12,
    "kid": 10,
    "pill": 10,
    "api": 10,
    "doc": 10,
    "cyl": 11,
}
SVG_SHAPE = {"doc": "document", "cyl": "cylinder", "pill": "pill"}


def svg_paint(kind: str) -> tuple[str, str, str]:
    if kind == "api":
        return API_FILL, API_STROKE, INK
    if kind == "doc":
        return CASE_FILL, CASE_STROKE, INK
    if kind == "branch_red":
        return FILL, STROKE, RED
    if kind == "branch_blue":
        return FILL, STROKE, BLUE
    return FILL, STROKE, INK


def render_svg(d: Diagram) -> str:
    out = [
        (
            f'<svg viewBox="0 0 {d.page_w} {d.page_h}" xmlns="http://www.w3.org/2000/svg" '
            'style="width:100%;height:auto;background:#fff">'
        ),
        f'<rect width="{d.page_w}" height="{d.page_h}" fill="#FFFFFF"/>',
        (
            '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#666666"/>'
            "</marker></defs>"
        ),
    ]
    pos = {v.id: v for v in d.verts}
    for e in d.edges:
        a, b = pos.get(e.src), pos.get(e.dst)
        if a is None or b is None:
            continue
        out.append(
            f'<line x1="{a.cx}" y1="{a.cy}" x2="{b.cx}" y2="{b.cy}" '
            f'stroke="#666666" stroke-width="1" marker-end="url(#ar)"/>'
        )
        if e.label:
            t = 0.5 if e.pos is None else e.pos
            mx = a.cx + (b.cx - a.cx) * t
            my = a.cy + (b.cy - a.cy) * t - 4
            out.append(
                f'<text x="{mx}" y="{my}" font-size="9" fill="{LABEL}" '
                f'paint-order="stroke" stroke="#FFFFFF" stroke-width="3" '
                f'text-anchor="middle" font-family="Microsoft YaHei">'
                f"{html.escape(e.label)}</text>"
            )
    for v in d.verts:
        x, y, w, h, k = v.x, v.y, v.w, v.h, v.kind
        fs = SVG_FONT[k]
        fill, stroke, color = svg_paint(k)
        shape = SVG_SHAPE.get(k, "rect")
        if shape == "document":
            out.append(
                f'<path d="M{x} {y} H{x + w} V{y + h - 14} '
                f"C{x + w * 0.72} {y + h - 1} {x + w * 0.28} {y + h - 22} {x} {y + h - 12} "
                f'V{y} Z" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
            )
        elif shape == "cylinder":
            ry = 9
            out.append(
                f'<path d="M{x} {y + ry} V{y + h - ry} '
                f"A{w / 2} {ry} 0 0 0 {x + w} {y + h - ry} V{y + ry} "
                f'A{w / 2} {ry} 0 0 0 {x} {y + ry} Z" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
            )
            out.append(
                f'<ellipse cx="{x + w / 2}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" '
                f'fill="none" stroke="{stroke}" stroke-width="1"/>'
            )
        else:
            rx = h / 2 if shape == "pill" else 0
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
            )
        lines = svg_wrap(v.text.replace("\n", " "), max(5, int(w / (fs * 0.95))))
        line_h = fs * 1.3
        cy0 = y + h / 2 - (len(lines) - 1) * line_h / 2
        out.extend(
            f'<text x="{x + w / 2}" y="{cy0 + i * line_h + fs * 0.35}" '
            f'font-size="{fs}" fill="{color}" text-anchor="middle" '
            f'font-family="Microsoft YaHei">{html.escape(ln)}</text>'
            for i, ln in enumerate(lines[:4])
        )
    out.append("</svg>")
    return "".join(out)


def collect_jobs(
    typ_dir: str,
    pattern: str,
    first: str,
    last: str,
    docx_dir: str | None,
) -> list[tuple[str, Path, int, str]]:
    """返回 [(编号, 讲义路径, 讲义序号, 讲名)]。"""
    typs = sorted(Path(typ_dir).glob(pattern))
    if first or last:
        typs = [
            p
            for p in typs
            if (not first or p.name[:3] >= first) and (not last or p.name[:3] <= last)
        ]
    keys: list[str] = []
    if docx_dir:
        for doc in sorted(Path(docx_dir).glob("*.docx")):
            m = DOCX_KEY.search(doc.name)
            if m is None:
                raise ValueError(f"docx 文件名不含 -<编号>: {doc.name}")
            keys.append(m.group(1))
        if len(keys) != len(typs):
            raise ValueError(f"docx 数量 {len(keys)} 与讲义数量 {len(typs)} 不一致")
    jobs: list[tuple[str, Path, int, str]] = []
    prev = 0
    for i, path in enumerate(typs):
        m = TYP_NAME.match(path.name)
        if m is None:
            raise ValueError(f"讲义文件名不符合 vNN-<讲名>.typ: {path.name}")
        vnum = int(m.group(1))
        if prev and vnum != prev + 1:
            raise ValueError(f"讲义序号不连续: {path.name}")
        prev = vnum
        key = keys[i] if keys else f"{vnum:02d}"
        jobs.append((key, path, vnum, m.group(2)))
    if not jobs:
        raise ValueError(f"没有找到讲义文件: {typ_dir} / {pattern}")
    return jobs


def build_preview(cards: list[str]) -> str:
    return PREVIEW_HEAD + "".join(cards) + PREVIEW_TAIL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Typst 讲义批量生成 draw.io 四向放射式概念图"
    )
    parser.add_argument("--typ-dir", required=True, help="讲义目录")
    parser.add_argument("--out-dir", required=True, help="输出目录")
    parser.add_argument("--docx-dir", help="教案 docx 目录，用于编号对齐")
    parser.add_argument(
        "--pattern", default="v[0-9][0-9]-*.typ", help="讲义文件名匹配式"
    )
    parser.add_argument("--first", default="", help="起始讲义前缀，如 v01")
    parser.add_argument("--last", default="", help="结束讲义前缀，如 v24")
    parser.add_argument("--preview", help="可选，写自检预览 HTML 的路径")
    parser.add_argument("--modified", help="固定 mxfile 的 modified 时间戳，便于复现")
    parser.add_argument("--manual-label", default="OpenCV 官方手册")
    args = parser.parse_args(argv)

    labels = Labels(manual=args.manual_label)
    modified = args.modified or datetime.datetime.now(datetime.UTC).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        jobs = collect_jobs(
            args.typ_dir, args.pattern, args.first, args.last, args.docx_dir
        )
    except ValueError as exc:
        print(f"输入错误: {exc}", file=sys.stderr)
        return 2

    rows: list[tuple[str, str, str, int, int, int, int]] = []
    cards: list[str] = []
    for key, path, _vnum, title in jobs:
        groups, examples = parse_typ(path)
        branches = make_branches(groups)
        if len(branches) < 3:
            used = {item for b in branches for item in b.ex}
            spare_ex = [e for e in dedupe(examples) if e not in used]
            branches.append(
                Branch(
                    l1="实践案例",
                    kids=spare_ex[:MAX_KIDS] or dedupe(examples, MAX_KIDS),
                    ex=[],
                )
            )
        if len(branches) > MAX_BRANCH:
            print(
                f"提示: {path.name} 有 {len(branches)} 个主干，超过上限 {MAX_BRANCH}，已截断",
                file=sys.stderr,
            )
            branches = branches[:MAX_BRANCH]
        d = build_diagram(key, title, branches, labels)
        name = f"{key}-{title}.drawio"
        (out_dir / name).write_text(d.xml(modified), encoding="utf-8")
        n_kids = sum(len(b.kids[:MAX_KIDS]) for b in branches)
        rows.append(
            (key, path.name, name, len(branches), n_kids, len(d.verts), len(d.edges))
        )
        if args.preview:
            cards.append(
                f'<section class="card"><h3>{html.escape(name)}</h3>'
                f'<p class="meta">{html.escape(path.name)} → {html.escape(key)} · '
                f"{len(branches)} 个主干 / {n_kids} 个知识点 / {len(d.verts)} 个形状</p>"
                f"{render_svg(d)}</section>"
            )
    if args.preview:
        preview = Path(args.preview)
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(build_preview(cards), encoding="utf-8")

    print(
        f"{'序号':<7} {'讲义':<26} {'输出文件':<34} {'主干':>6} {'知识点':>8} "
        f"{'形状':>6} {'连线':>6}"
    )
    for key, base, name, nb, nk, nn, ne in rows:
        print(f"{key:<7} {base:<26} {name:<34} {nb:>6} {nk:>8} {nn:>6} {ne:>6}")
    print(f"已生成 {len(rows)} 个 .drawio -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
