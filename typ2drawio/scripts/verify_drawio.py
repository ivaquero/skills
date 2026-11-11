#!/usr/bin/env python3
"""校验 typ2drawio 产出的 .drawio：结构、引用、编号、噪声、重叠。

只读，不修改产物。退出码 0 表示全部通过，1 表示有文件不合格。
"""

import argparse
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

NOISE = re.compile(
    r"\\|frac|sum_|argmax|ex\d|__|…|<=|>=|δ|Σ|∑|#code|#figure|"
    r"^\s*[-+*/]\s|\bP\(|\bQ\(|\blog_"
)
DOCX_KEY = re.compile(r"-(\d+(?:-\d+)?)\.docx$")
OUT_KEY = re.compile(r"^(\d+(?:-\d+)?)-")
MIN_VERTS = 15
MIN_EDGES = 15


def boxes_of(cells: list[ET.Element]) -> list[tuple[float, float, float, float]]:
    """收集有边框节点的矩形，忽略无边框说明框。"""
    boxes: list[tuple[float, float, float, float]] = []
    for cell in cells:
        geom = cell.find("mxGeometry")
        if cell.get("vertex") != "1" or geom is None:
            continue
        if "fillColor=none" in (cell.get("style") or ""):
            continue
        boxes.append(
            (
                float(geom.get("x", "0")),
                float(geom.get("y", "0")),
                float(geom.get("width", "0")),
                float(geom.get("height", "0")),
            )
        )
    return boxes


def overlap_pairs(boxes: list[tuple[float, float, float, float]]) -> int:
    count = 0
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            dx = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            dy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if dx > 2 and dy > 2:
                count += 1
    return count


def vertices_of(
    cells: list[ET.Element],
) -> dict[str, tuple[float, float, float, float]]:
    out: dict[str, tuple[float, float, float, float]] = {}
    for cell in cells:
        geom = cell.find("mxGeometry")
        if cell.get("vertex") != "1" or geom is None:
            continue
        out[cell.get("id") or ""] = (
            float(geom.get("x", "0")),
            float(geom.get("y", "0")),
            float(geom.get("width", "0")),
            float(geom.get("height", "0")),
        )
    return out


def crosses(
    box_a: tuple[float, ...], box_b: tuple[float, ...], box: tuple[float, ...]
) -> bool:
    """两条中心连线是否穿过 box 内部；按比例采样，够判重叠，不引入依赖。"""
    ax, ay = box_a[0] + box_a[2] / 2, box_a[1] + box_a[3] / 2
    bx, by = box_b[0] + box_b[2] / 2, box_b[1] + box_b[3] / 2
    for step in range(1, 200):
        t = step / 200
        x, y = ax + (bx - ax) * t, ay + (by - ay) * t
        if (
            box[0] + 3 < x < box[0] + box[2] - 3
            and box[1] + 3 < y < box[1] + box[3] - 3
        ):
            return True
    return False


OUTWARD_LABELS = ("包含", "内容包含", "支持")


def check_edges(cells: list[ET.Element], edges: list[ET.Element]) -> list[str]:
    """连线不许穿过无关节点，标签不许压在节点上，包含关系只能由父指向子。"""
    problems: list[str] = []
    verts = vertices_of(cells)
    depths = depth_of(edges)
    for edge in edges:
        src, dst = (
            verts.get(edge.get("source") or ""),
            verts.get(edge.get("target") or ""),
        )
        if src is None or dst is None:
            continue
        hit = [
            vid
            for vid, box in verts.items()
            if vid not in (edge.get("source"), edge.get("target"))
            and crosses(src, dst, box)
        ]
        if hit:
            problems.append(f"连线 {edge.get('id')} 穿过节点 {hit[0]} 等 {len(hit)} 个")
        label = edge.get("value") or ""
        if label:
            geom = edge.find("mxGeometry")
            pos = float(geom.get("x", "0.5")) if geom is not None else 0.5
            lx = src[0] + src[2] / 2 + (dst[0] + dst[2] / 2 - src[0] - src[2] / 2) * pos
            ly = src[1] + src[3] / 2 + (dst[1] + dst[3] / 2 - src[1] - src[3] / 2) * pos
            half_w, half_h = (len(label) * 9 + 8) / 2, 6
            buried = [
                vid
                for vid, box in verts.items()
                if vid not in (edge.get("source"), edge.get("target"))
                and min(lx + half_w, box[0] + box[2]) - max(lx - half_w, box[0]) > 0
                and min(ly + half_h, box[1] + box[3]) - max(ly - half_h, box[1]) > 0
            ]
            if buried:
                problems.append(f"标签「{label}」压住节点 {buried[0]}")
        if label in OUTWARD_LABELS:
            src_id, dst_id = edge.get("source"), edge.get("target")
            # 唯一允许向内的是先修关系：先修知识点支撑本讲，箭头回指中心，
            # 方向由 check_prereq 单独判，这里跳过，免得被「父指向子」误报。
            if dst_id == "center":
                continue
            if src_id not in depths:
                problems.append(
                    f"「{label}」连线方向反了：{src_id} 从中心出发沿包含关系到不了，箭头须由父指向子"
                )
            elif dst_id in depths and depths[dst_id] <= depths[src_id]:
                problems.append(
                    f"「{label}」连线方向反了：{dst_id} 应比 {src_id} 更深一层，箭头须由父指向子"
                )
    return problems


def depth_of(edges: list[ET.Element]) -> dict[str, int]:
    """从中心沿包含关系逐层定层级，只用于校验箭头方向。"""
    tree = [e for e in edges if (e.get("value") or "") in OUTWARD_LABELS]
    depths: dict[str, int] = {"center": 0}
    for _ in range(12):
        grew = False
        for edge in tree:
            src, dst = edge.get("source"), edge.get("target")
            if src is None or dst is None:
                continue
            if src in depths and dst not in depths:
                depths[dst] = depths[src] + 1
                grew = True
        if not grew:
            break
    return depths


def check_prereq(cells: list[ET.Element], edges: list[ET.Element]) -> list[str]:
    """先修节点：绿色文字、只写知识点（不带「前置知识」前缀或讲名）、有一条指向中心的「支持」连线。"""
    problems: list[str] = []
    green = [
        cell
        for cell in cells
        if cell.get("vertex") == "1"
        and "fontColor=#008000" in (cell.get("style") or "")
    ]
    if len(green) != 1:
        problems.append(f"先修节点应有且仅有 1 个，实际 {len(green)} 个")
        return problems
    node = green[0]
    value = node.get("value") or ""
    if not value.strip():
        problems.append("先修节点文字为空")
    for banned in ("前置知识", "《", "》"):
        if banned in value:
            problems.append(f"先修节点文字不该含「{banned}」，只写知识点本身")
    linked = [
        edge
        for edge in edges
        if (edge.get("value") or "") == "支持" and edge.get("source") == node.get("id")
    ]
    if not linked:
        problems.append("先修节点缺少「支持」连线")
    elif linked[0].get("target") != "center":
        problems.append("「支持」连线应从先修节点指向中心节点 center")
    return problems


def check_file(path: Path, require_prereq: bool = False) -> list[str]:
    problems: list[str] = []
    try:
        # 产物由本包自己写出，不是外部不可信输入；包内禁止第三方依赖，
        # 所以 defusedxml 在这里不适用，就地抑制 S314 而不是放宽规则。
        root = ET.parse(path).getroot()  # noqa: S314
    except (ET.ParseError, OSError) as exc:
        return [f"XML 解析失败: {exc}"]
    if root.tag != "mxfile":
        problems.append(f"根元素应为 mxfile，实际为 {root.tag}")
    cells = list(root.iter("mxCell"))
    ids = [c.get("id") for c in cells]
    if len(ids) != len(set(ids)):
        problems.append("mxCell id 重复")
    verts = {c.get("id") for c in cells if c.get("vertex") == "1"}
    edges = [c for c in cells if c.get("edge") == "1"]
    layers = {
        c.get("id"): c.get("parent")
        for c in cells
        if c.get("vertex") is None and c.get("edge") is None
    }
    if layers.get("0") is not None or layers.get("1") != "0":
        problems.append("缺少 id=0 / id=1 两个层单元")
    problems.extend(
        f"边 {edge.get('id')} 的 source/target 指向不存在的节点"
        for edge in edges
        if edge.get("source") not in verts or edge.get("target") not in verts
    )
    if len(verts) < MIN_VERTS or len(edges) < MIN_EDGES:
        problems.append(f"内容过少: {len(verts)} 节点 / {len(edges)} 连线")
    hits = [
        (c.get("value") or "").replace("<br>", " ")
        for c in cells
        if NOISE.search((c.get("value") or "").replace("<br>", " "))
    ]
    if hits:
        problems.append(f"公式/示例标识符噪声 {len(hits)} 处，例如 {hits[0][:40]}")
    pairs = overlap_pairs(boxes_of(cells))
    if pairs:
        problems.append(f"节点矩形重叠 {pairs} 对")
    problems.extend(check_edges(cells, edges))
    if require_prereq:
        problems.extend(check_prereq(cells, edges))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 typ2drawio 产物")
    parser.add_argument("--out-dir", required=True, help="产物目录")
    parser.add_argument(
        "--docx-dir",
        help="可选，教案 docx 目录：用于编号对齐，并检查前置知识节点（教案是先决知识的唯一来源）",
    )
    args = parser.parse_args(argv)
    files = sorted(Path(args.out_dir).glob("*.drawio"))
    if not files:
        print(f"没有找到 .drawio: {args.out_dir}", file=sys.stderr)
        return 1

    failed = 0
    for path in files:
        problems = check_file(path, require_prereq=bool(args.docx_dir))
        if problems:
            failed += 1
            print(f"FAIL {path.name}")
            for problem in problems:
                print(f"     - {problem}")
        else:
            print(f"OK   {path.name}")

    keys = []
    for path in files:
        m = OUT_KEY.match(path.name)
        if m is None:
            print(f"FAIL 编号缺失: {path.name}", file=sys.stderr)
            failed += 1
        else:
            keys.append(m.group(1))
    if args.docx_dir:
        docs = sorted(Path(args.docx_dir).glob("*.docx"))
        expected = []
        for doc in docs:
            m = DOCX_KEY.search(doc.name)
            if m is None:
                print(f"FAIL docx 文件名不含编号: {doc.name}", file=sys.stderr)
                failed += 1
            else:
                expected.append(m.group(1))
        if sorted(keys) != sorted(expected):
            print(f"FAIL 编号与 docx 不一致: {sorted(keys)} != {sorted(expected)}")
            failed += 1
        else:
            print(f"编号对齐: {len(keys)} 个产物 <-> {len(expected)} 份 docx")

    print(f"校验 {len(files)} 个文件，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
