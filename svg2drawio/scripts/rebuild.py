#!/usr/bin/env python3
"""Rebuild an outline-only SVG as a fully editable draw.io diagram.

When the source has no ``<text>`` at all and thousands of
``<use xlink:href="#glyph-...">`` references, it was produced by cairo/Pango
from a PDF: every label is a vector outline.  Embedding the page as an image
would be pixel-perfect and useless, so this script does the other thing:

* boxes   -> ``rounded=1`` vertices, corner radius measured from the straight
             run of the top edge;
* lines   -> ``edgeStyle=none`` edges with explicit waypoints, dash patterns
             restored, arrowheads re-attached from the separate triangle paths;
* labels  -> real text cells, using a glyph-id to character table that the
             caller supplies (see references/outline-pipeline.md for how the
             table is produced).

The table is the only input this script cannot derive itself, so it is a
separate file: ``<glyph-id>\\t<char>\\t<family>`` per line, ``#`` comments allowed.

Standard library only (Python 3.10+).  No numpy: the hot paths are a grid
bucket instead of a KD-tree, and a hand-written Ramer-Douglas-Peucker.

Usage
-----
    python rebuild.py page.svg --probe
    python rebuild.py page.svg --glyphs glyphs.tsv -o page.drawio
"""

import argparse
import collections
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TypedDict

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
NS = f"{{{SVG_NS}}}"
XL = f"{{{XLINK_NS}}}"

NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
CMD_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]")

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

Point = tuple[float, float]
Box = tuple[float, float, float, float]


class Run(TypedDict):
    """One ``<use>`` sequence: a piece of a label in one font."""

    ids: list[str]
    x: list[float]
    y: float
    fill: str | None
    family: str
    text: str


class Line(TypedDict):
    """One label line after runs are joined and measured."""

    text: str
    x0: float
    baseline: float
    size: float
    color: str | None
    family: str
    bbox: Box
    w: float


# Characters whose outline height equals the cap height in every common text
# face; their median height per font subset is the most reliable em estimate
# available without reading the original font file.
CAP_CHARS = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# A glyph whose outline bbox is smaller than this is blank (the space glyph).
BLANK_GLYPH = 0.05


# --------------------------------------------------------------------------
# Affine transforms
# --------------------------------------------------------------------------
def mat_mul(m, n):
    """Return m after n (apply n first, then m)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(text):
    """Parse an SVG transform attribute into an (a, b, c, d, e, f) matrix."""
    m = IDENT
    if not text:
        return m
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", text):
        v = [float(t) for t in NUM_RE.findall(args)]
        n = name.lower()
        if n == "translate" and v:
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif n == "scale" and v:
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif n == "matrix" and len(v) >= 6:
            t = tuple(v[:6])
        elif n == "rotate" and v:
            ang = math.radians(v[0])
            ca, sa = math.cos(ang), math.sin(ang)
            t = (ca, sa, -sa, ca, 0.0, 0.0)
            if len(v) >= 3:
                cx, cy = v[1], v[2]
                t = mat_mul((1, 0, 0, 1, cx, cy), mat_mul(t, (1, 0, 0, 1, -cx, -cy)))
        else:
            continue
        m = mat_mul(m, t)
    return m


# --------------------------------------------------------------------------
# Path parsing: subpath aware, which the shapes pipeline does not need but
# this one does (one ``d`` can hold several unrelated lines; see quirk 8)
# --------------------------------------------------------------------------
def parse_path(d):
    """Split a path into (command, absolute parameter list) pairs."""
    cmds = [(m.group(0), m.start()) for m in CMD_RE.finditer(d)]
    if not cmds:
        return []
    nargs = {
        "m": 2,
        "l": 2,
        "h": 1,
        "v": 1,
        "c": 6,
        "s": 4,
        "q": 4,
        "t": 2,
        "a": 7,
        "z": 0,
    }
    out = []
    for k, (cmd, start) in enumerate(cmds):
        end = cmds[k + 1][1] if k + 1 < len(cmds) else len(d)
        nums = NUM_RE.findall(d[start + 1 : end])
        vals = [float(v) for v in nums]
        step = nargs[cmd.lower()]
        if step == 0:
            out.append((cmd, []))
            continue
        out.extend(
            (cmd, vals[j : j + step]) for j in range(0, len(vals) - step + 1, step)
        )
    return out


def _bezier(p0, p1, p2, p3, steps):
    out = []
    for k in range(1, steps + 1):
        t = k / steps
        u = 1.0 - t
        out.append(
            (
                u**3 * p0[0]
                + 3 * u * u * t * p1[0]
                + 3 * u * t * t * p2[0]
                + t**3 * p3[0],
                u**3 * p0[1]
                + 3 * u * u * t * p1[1]
                + 3 * u * t * t * p2[1]
                + t**3 * p3[1],
            )
        )
    return out


def _quad(p0, p1, p2, steps):
    out = []
    for k in range(1, steps + 1):
        t = k / steps
        u = 1.0 - t
        out.append(
            (
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            )
        )
    return out


def _abs_pt(vals, cur, rel, i):
    """Parameter pair ``i`` of a command, made absolute against ``cur``."""
    x, y = vals[i], vals[i + 1]
    return (cur[0] + x, cur[1] + y) if rel else (x, y)


def flatten_subpaths(d, steps=8):
    """Flatten one ``d`` into one point list per subpath.

    Splitting is not cosmetic: cairo writes every disconnected line of a
    connector set into a single ``d``.  Keeping them joined turns the whole
    page into one polyline with teleports across the canvas.
    """
    subs: list[list[tuple[float, float]]] = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    prev_ctrl = None
    for cmd, vals in parse_path(d or ""):
        rel = cmd.islower()
        c = cmd.upper()

        if c == "M":
            cur = _abs_pt(vals, cur, rel, 0)
            start = cur
            subs.append([cur])
            prev_ctrl = None
        elif not subs:
            continue
        elif c == "L":
            cur = _abs_pt(vals, cur, rel, 0)
            subs[-1].append(cur)
            prev_ctrl = None
        elif c == "H":
            cur = (cur[0] + vals[0] if rel else vals[0], cur[1])
            subs[-1].append(cur)
            prev_ctrl = None
        elif c == "V":
            cur = (cur[0], cur[1] + vals[0] if rel else vals[0])
            subs[-1].append(cur)
            prev_ctrl = None
        elif c == "C":
            c1, c2, p3 = (
                _abs_pt(vals, cur, rel, 0),
                _abs_pt(vals, cur, rel, 2),
                _abs_pt(vals, cur, rel, 4),
            )
            subs[-1].extend(_bezier(cur, c1, c2, p3, steps))
            prev_ctrl, cur = c2, p3
        elif c == "S":
            c2, p3 = _abs_pt(vals, cur, rel, 0), _abs_pt(vals, cur, rel, 2)
            c1 = (
                (2 * cur[0] - prev_ctrl[0], 2 * cur[1] - prev_ctrl[1])
                if prev_ctrl
                else cur
            )
            subs[-1].extend(_bezier(cur, c1, c2, p3, steps))
            prev_ctrl, cur = c2, p3
        elif c == "Q":
            c1, p2 = _abs_pt(vals, cur, rel, 0), _abs_pt(vals, cur, rel, 2)
            subs[-1].extend(_quad(cur, c1, p2, steps))
            prev_ctrl, cur = c1, p2
        elif c == "T":
            p2 = _abs_pt(vals, cur, rel, 0)
            c1 = (
                (2 * cur[0] - prev_ctrl[0], 2 * cur[1] - prev_ctrl[1])
                if prev_ctrl
                else cur
            )
            subs[-1].extend(_quad(cur, c1, p2, steps))
            prev_ctrl, cur = c1, p2
        elif c == "A":
            # arcs are rare in cairo connector output; a straight chord is within
            # the RDP tolerance for the radii that occur here
            cur = _abs_pt(vals, cur, rel, 5)
            subs[-1].append(cur)
            prev_ctrl = None
        elif c == "Z":
            cur = start
            subs[-1].append(cur)
    return [s for s in subs if len(s) >= 2]


def bbox(pts: list[Point]) -> Box:
    """Axis-aligned bounding box of a point list, as (x0, y0, x1, y1)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def rdp(pts, eps):
    """Ramer-Douglas-Peucker, iterative so deep zigzags cannot blow the stack."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        x1, y1 = pts[a]
        x2, y2 = pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            x, y = pts[i]
            if norm < 1e-9:
                dist = math.hypot(x - x1, y - y1)
            else:
                dist = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
            if dist > best:
                best, bi = dist, i
        if best > eps:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def corner_radius(pts, b):
    """Corner radius = how far in from the bbox the straight top/bottom edge starts.

    Taking ``pts[0].x - bbox.left`` instead (the naive reading of "the first
    point is where the outline begins") overshoots on every path that starts on
    the left side: the curve starts before the straight run does, so bubbles end
    up much rounder than the original.
    """
    cand = []
    for edge_y in (b[1], b[3]):
        xs = [p[0] for p in pts if abs(p[1] - edge_y) < 0.35]
        if len(xs) >= 2:
            cand.append(min(xs) - b[0])
    r = min(cand) if cand else 0.0
    return max(0.0, min(r, (b[2] - b[0]) / 2.0, (b[3] - b[1]) / 2.0))


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------
def hexcol(c, op=1.0):
    """SVG paint -> #rrggbb, with translucency flattened onto white.

    draw.io has no per-cell alpha for lines.  Dropping a 0.15-opacity outline
    loses the drop-shadow halo and the pale table rules; keeping it black turns
    them into hard grid lines.  Flattening is the only faithful option.
    """
    if not c or c == "none":
        return None
    c = c.strip()
    m = re.match(r"rgb\(\s*([\d.]+)%,\s*([\d.]+)%,\s*([\d.]+)%\s*\)", c)
    if m:
        r, g, b = (float(v) * 2.55 for v in m.groups())
    elif c.startswith("#") and len(c) == 7:
        r, g, b = (int(c[i : i + 2], 16) for i in (1, 3, 5))
    else:
        return None
    op = max(0.0, min(1.0, float(op or 1.0)))
    if op < 1.0:
        r, g, b = (v * op + 255.0 * (1.0 - op) for v in (r, g, b))
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}"


def parse_css(text):
    """Minimal class -> properties map, for exports that style through classes."""
    rules = {}
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", text or ""):
        props = {}
        for decl in body.split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                props[k.strip().lower()] = v.strip()
        for s in sel.split(","):
            s = s.strip()
            if s.startswith("."):
                rules[s[1:]] = props
    return rules


def paint(el, name, css):
    """Attribute first, then the element's classes; the presentation attribute wins."""
    v = el.get(name)
    if v is not None:
        return v
    for cls in (el.get("class") or "").split():
        if name in css.get(cls, {}):
            return css[cls][name]
    return None


# --------------------------------------------------------------------------
# Glyph outlines and the glyph -> character table
# --------------------------------------------------------------------------
def glyph_outlines(root):
    """id -> (path count, bbox) for every glyph definition.

    cairo nests them one level deeper than expected: ``<defs><g><g id="glyph-…">``.
    Looking only at ``<defs>``' direct children finds one anonymous wrapper and
    silently yields zero glyphs, which makes every label empty and every font
    size a guess.  Walk the whole subtree and keep the ``<g>`` elements that
    actually carry paths.
    """
    out = {}
    for defs in root.iter(NS + "defs"):
        for el in defs.iter(NS + "g"):
            gid = el.get("id")
            if not gid:
                continue
            paths = list(el.iter(NS + "path"))
            if not paths:
                continue
            pts = []
            for sub in paths:
                pts.extend(p for s in flatten_subpaths(sub.get("d"), 2) for p in s)
            out[gid] = (len(paths), bbox(pts) if pts else None)
    return out


def load_glyph_map(path):
    """TSV: ``glyph-id <tab> text [<tab> family]``; ``#`` starts a comment."""
    gmap, fam = {}, {}
    if not path:
        return gmap, fam
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 2:
            continue
        gid, ch = parts[0].strip(), parts[1]
        gmap[gid] = ch if ch else " "
        if len(parts) > 2 and parts[2].strip():
            fam[gid] = parts[2].strip()
    return gmap, fam


def subset_of(gid):
    """cairo names glyphs ``glyph-<subset>-<index>``; the subset carries the face."""
    bits = (gid or "").split("-")
    return bits[1] if len(bits) > 2 else (gid or "")


def apply_blank_rule(gmap, outlines):
    """Blank outlines are spaces, whatever the matcher thought.

    A glyph with no ink (the space glyph) matches almost anything: its bbox is
    a point, so a dot wins the shape comparison and every space in the document
    silently becomes ``.``.  That is the single most common corruption in this
    pipeline, and it is detectable without looking at a single image.
    """
    fixed = {}
    for gid, ch in gmap.items():
        ob = outlines.get(gid, (None, None))[1]
        if ob is None:
            fixed[gid] = ch
        elif (ob[2] - ob[0]) < BLANK_GLYPH and (ob[3] - ob[1]) < BLANK_GLYPH:
            fixed[gid] = " "
        else:
            fixed[gid] = ch
    return fixed


# --------------------------------------------------------------------------
# Text runs
# --------------------------------------------------------------------------
def text_runs(root, gmap, fam_of, cap_ratio, default_family, missing=None) -> list[Run]:
    """Group ``<use>`` sequences into lines with a baseline, bbox and font size."""
    runs: list[Run] = []
    for g in root:
        if g.tag != NS + "g":
            continue
        uses = [u for u in g.iter(NS + "use") if u.get(XL + "href")]
        if len(uses) < 1:
            continue
        seq = sorted(uses, key=lambda u: float(u.get("x") or 0))
        xs = [float(u.get("x") or 0) for u in seq]
        ys = [float(u.get("y") or 0) for u in seq]
        ids = [(u.get(XL + "href") or "").lstrip("#") for u in seq]
        runs.append(
            {
                "ids": ids,
                "x": xs,
                "y": ys[0] if ys else 0.0,
                "fill": paint(g, "fill", {}),
                "family": default_family,
                "text": "",
            }
        )
    if not runs:
        # a page whose <use> elements hang directly off the root
        uses = [u for u in root.iter(NS + "use") if u.get(XL + "href")]
        if uses:
            seq = sorted(
                uses, key=lambda u: (float(u.get("y") or 0), float(u.get("x") or 0))
            )
            runs.append(
                {
                    "ids": [(u.get(XL + "href") or "").lstrip("#") for u in seq],
                    "x": [float(u.get("x") or 0) for u in seq],
                    "y": float(seq[0].get("y") or 0),
                    "fill": None,
                    "family": default_family,
                    "text": "",
                }
            )
    for r in runs:
        if missing is not None:
            missing.update(i for i in r["ids"] if i not in gmap)
        r["text"] = "".join(gmap.get(i, " ") for i in r["ids"])
        fams = [fam_of[i] for i in r["ids"] if i in fam_of]
        r["family"] = fams[0] if fams else default_family
    return runs


def estimate_em(
    runs: list[Run], outlines, gmap, cap_ratio
) -> tuple[dict[str, float], float]:
    """Median cap height per font subset, converted to an em size."""
    heights = collections.defaultdict(list)
    for r in runs:
        for gid in r["ids"]:
            ch = gmap.get(gid, " ")
            ob = outlines.get(gid, (None, None))[1]
            if ob is None or ch not in CAP_CHARS:
                continue
            h = ob[3] - ob[1]
            if h > 0:
                heights[subset_of(gid)].append(h)
    em = {}
    for sub, hs in heights.items():
        hs.sort()
        em[sub] = hs[len(hs) // 2] / cap_ratio
    if em:
        allv = sorted(em.values())
        fallback = allv[len(allv) // 2]
    else:
        fallback = 12.0
    return em, fallback


def build_lines(
    runs: list[Run], outlines, gmap, em: dict[str, float], fallback_em: float
) -> list[Line]:
    """Turn runs into text lines with bbox, size and colour; merge adjacent runs."""
    lines: list[Line] = []
    for r in runs:
        if not r["text"].strip():
            continue
        sub = collections.Counter(subset_of(i) for i in r["ids"]).most_common(1)[0][0]
        size = em.get(sub, fallback_em)
        box: Box | None = None
        for ux, uy, gid in zip(r["x"], [r["y"]] * len(r["ids"]), r["ids"]):
            ob = outlines.get(gid, (None, None))[1]
            if ob is None:
                continue
            bb: Box = (ux + ob[0], uy + ob[1], ux + ob[2], uy + ob[3])
            box = (
                bb
                if box is None
                else (
                    min(box[0], bb[0]),
                    min(box[1], bb[1]),
                    max(box[2], bb[2]),
                    max(box[3], bb[3]),
                )
            )
        if box is None:
            continue
        lines.append(
            {
                "text": r["text"],
                "x0": box[0],
                "baseline": r["y"],
                "size": size,
                "color": hexcol(r["fill"]),
                "family": r["family"],
                "bbox": box,
                "w": box[2] - box[0],
            }
        )

    # cairo emits one <g> per font change; a label that switches face arrives as
    # two runs on the same baseline.  Joining them by gap, not by identity, is
    # what keeps "Count of Holstein-Rendsburg" in one cell.
    lines.sort(key=lambda d: (round(d["baseline"], 1), d["x0"]))
    merged: list[Line] = []
    for ln in lines:
        prev = merged[-1] if merged else None
        if (
            prev
            and abs(ln["baseline"] - prev["baseline"]) < 1.2
            and abs(ln["x0"] - prev["bbox"][2]) < max(ln["size"], prev["size"]) * 1.0
        ):
            prev["text"] += ln["text"]
            prev["bbox"] = (
                prev["bbox"][0],
                min(prev["bbox"][1], ln["bbox"][1]),
                ln["bbox"][2],
                max(prev["bbox"][3], ln["bbox"][3]),
            )
            prev["w"] = prev["bbox"][2] - prev["bbox"][0]
        else:
            merged.append(ln)
    return merged


# --------------------------------------------------------------------------
# Structure: classify the flattened path soup
# --------------------------------------------------------------------------
def classify(body, css, page_pad=(0.0, 0.0, 0.0, 0.0)):
    """Sort paths into boxes, arrowheads, shadows and connector strokes."""
    boxes, closed, arrows, shadows, strokes = [], [], [], [], []
    for el in body:
        tag = el.tag.replace(NS, "")
        if tag != "path":
            continue
        d = el.get("d")
        if not d:
            continue
        fill = paint(el, "fill", css)
        stroke = paint(el, "stroke", css)
        sw = float(paint(el, "stroke-width", css) or 0.7)
        fop = float(el.get("fill-opacity") or 1.0)
        sop = float(el.get("stroke-opacity") or 1.0)
        dash = el.get("stroke-dasharray")

        pt_list = [p for s in flatten_subpaths(d, 3) for p in s]
        if not pt_list:
            continue
        b = bbox(pt_list)
        w, h = b[2] - b[0], b[3] - b[1]
        is_fill = fill not in (None, "none")

        if is_fill and w < 12 and h < 12:
            # an arrowhead is its own filled path; the tip is the first point
            # because cairo starts the triangle there.  Do not use "farthest
            # point from the centroid": for a triangle that lands on the middle
            # of the base and every arrow ends up pointing backwards.
            cx = sum(p[0] for p in pt_list) / len(pt_list)
            cy = sum(p[1] for p in pt_list) / len(pt_list)
            arrows.append({"tip": pt_list[0], "cx": cx, "cy": cy})
            continue
        if is_fill and fop < 0.3:
            shadows.append(b)
            continue
        if is_fill:
            boxes.append(
                {
                    "x": b[0],
                    "y": b[1],
                    "w": w,
                    "h": h,
                    "r": corner_radius(pt_list, b),
                    "fill": hexcol(fill, fop),
                    "stroke": hexcol(stroke, sop),
                    "sw": sw,
                    "shadow": False,
                }
            )
            continue
        if w < 0.5 and h < 0.5:
            continue
        cmds = [c.upper() for c, _ in parse_path(d)]
        if (
            cmds.count("C") == 4
            and cmds.count("Z") == 1
            and w > 18
            and h > 10
            and cmds[0] == "M"
        ):
            # four cubic segments plus one close is a rounded rectangle drawn as
            # an outline: a card border, not a connector
            closed.append(
                {
                    "x": b[0],
                    "y": b[1],
                    "w": w,
                    "h": h,
                    "r": corner_radius(pt_list, b),
                    "fill": None,
                    "stroke": hexcol(stroke, sop),
                    "sw": sw,
                    "shadow": False,
                    "op": sop,
                }
            )
            continue
        strokes.extend(
            {
                "pts": rdp(sub, 1.6),
                "color": hexcol(stroke, sop),
                "sw": sw,
                "dash": dash,
            }
            for sub in flatten_subpaths(d, 8)
            if len(sub) >= 2
        )

    # a card is often drawn twice: a faint shadow halo plus the real border.
    # Sorting by opacity first and keeping the first of each coincident pair
    # keeps the border and drops the halo, instead of the other way round.
    kept = []
    for cb in sorted(closed, key=lambda d: -d.get("op", 1.0)):
        if any(
            abs(bx["x"] - cb["x"]) < 3
            and abs(bx["y"] - cb["y"]) < 3
            and abs(bx["w"] - cb["w"]) < 4
            and abs(bx["h"] - cb["h"]) < 4
            for bx in boxes
        ):
            continue
        if any(abs(k["x"] - cb["x"]) < 2 and abs(k["y"] - cb["y"]) < 2 for k in kept):
            continue
        kept.append(cb)
    boxes.extend(kept)

    for sb in shadows:
        best, bd = None, 1e9
        for bx in boxes:
            dd = (
                abs(bx["x"] - sb[0])
                + abs(bx["y"] - sb[1])
                + abs(bx["x"] + bx["w"] - sb[2])
                + abs(bx["y"] + bx["h"] - sb[3])
            )
            if dd < bd:
                bd, best = dd, bx
        if best is not None and bd < 6:
            best["shadow"] = True
    return boxes, arrows, strokes


# --------------------------------------------------------------------------
# Connector chains: segment graph, then edge-disjoint simple paths
# --------------------------------------------------------------------------
def cluster_nodes(pts, tol):
    """Union points within tol of each other, using a grid bucket (no scipy)."""
    parent = list(range(len(pts)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    buckets = collections.defaultdict(list)
    for i, (x, y) in enumerate(pts):
        buckets[(math.floor(x / tol), math.floor(y / tol))].append(i)
    for (gx, gy), members in buckets.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((gx + dx, gy + dy), ()):
                    for i in members:
                        if i < j and math.dist(pts[i], pts[j]) <= tol:
                            union(i, j)
    return [find(i) for i in range(len(pts))]


def _next_segment(adj, segs, used, node, prev_dir=None):
    """Pick the next unused segment at ``node``.

    Without ``prev_dir`` any unused segment will do.  With it, only a segment
    that keeps the current direction counts (dot product above 0.93), which is
    what lets a bus line stay one edge instead of being cut into a stub per
    branch.  Callers pass ``prev_dir`` only at real junctions, never at a
    degree-2 corner.
    """
    if prev_dir is None:
        for k in adj[node]:
            if k not in used:
                return k
        return None
    best, bd = None, 0.93
    for k in adj[node]:
        if k in used:
            continue
        a, _b, sp, _ = segs[k]
        p, q = (sp[0], sp[-1]) if a == node else (sp[-1], sp[0])
        vx, vy = q[0] - p[0], q[1] - p[1]
        n = math.hypot(vx, vy) or 1.0
        dn = math.hypot(*prev_dir) or 1.0
        dot = (vx / n) * (prev_dir[0] / dn) + (vy / n) * (prev_dir[1] / dn)
        if dot > bd:
            bd, best = dot, k
    return best


def chain_strokes(strokes, tol=1.6):
    """One draw.io edge per edge-disjoint path.

    Why segments and not strokes: a "bus" line that runs out to a box and
    doubles back is a single stroke in the PDF.  Emitting it as one polyline
    re-covers the same geometry twice; invisible on a solid line, but the two
    dash phases interleave and every dashed bus renders as a solid line.
    Decomposing at the segment level turns the doubling into an ordinary branch.
    """
    raw = []
    verts = []
    for s in strokes:
        for p, q in zip(s["pts"], s["pts"][1:]):
            if math.dist(p, q) < 0.2:
                continue
            raw.append((len(verts), len(verts) + 1, [p, q], _style_key(s)))
            verts += [p, q]
    if not raw:
        return []

    node = cluster_nodes(verts, tol)
    segs = [(node[a], node[b], pts, st) for a, b, pts, st in raw]

    uniq = {}
    for k, (a, b, _pts, st) in enumerate(segs):
        uniq.setdefault((a, b, st) if a <= b else (b, a, st), k)
    segs = [segs[k] for k in sorted(uniq.values())]

    chains = []
    by_style = collections.defaultdict(list)
    for k, seg in enumerate(segs):
        by_style[seg[3]].append(k)

    for key, members in by_style.items():
        adj = collections.defaultdict(list)
        for k in members:
            a, b = segs[k][0], segs[k][1]
            adj[a].append(k)
            if b != a:
                adj[b].append(k)
        deg = {n: len(v) for n, v in adj.items()}
        used: set[int] = set()

        starts = [n for n in adj if deg[n] != 2] + [n for n in adj if deg[n] == 2]
        for st in starts:
            while _next_segment(adj, segs, used, st) is not None:
                pts, cur = [], st
                prev_dir = None
                while True:
                    # A degree-2 node is a corner: there is exactly one way to go,
                    # so the direction test must not apply or every 90 degree bend
                    # ends the edge (and the edge count roughly doubles).  The
                    # test only earns its keep at a real junction.
                    k = (
                        _next_segment(adj, segs, used, cur, prev_dir)
                        if (prev_dir and deg[cur] != 2)
                        else _next_segment(adj, segs, used, cur)
                    )
                    if k is None:
                        break
                    used.add(k)
                    a, b, sp, _ = segs[k]
                    seg = list(sp) if a == cur else list(reversed(sp))
                    pts += seg if not pts else seg[1:]
                    cur = b if a == cur else a
                    if len(pts) >= 2:
                        prev_dir = (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
                if len(pts) >= 2:
                    chains.append(
                        {
                            "pts": rdp(pts, 1.2),
                            "color": key[0],
                            "sw": key[1],
                            "dash": key[2] or None,
                        }
                    )
    return chains


def _style_key(s):
    return (s["color"], round(s["sw"] or 0.0, 2), s.get("dash") or "")


def dash_pattern(dash, sw):
    """SVG dasharray -> draw.io dashPattern.

    draw.io multiplies ``dashPattern`` by the stroke width (verified by
    exporting one pattern at three widths), so the SVG value has to be divided
    back out, otherwise a 0.7px dash-dot comes out as a 1.7px solid-ish line.
    A dash phase cannot be expressed at all; offsets are dropped.
    """
    try:
        vals = [float(v) for v in dash.replace(",", " ").split()]
    except AttributeError:
        return "dashed=1"
    except ValueError:
        return "dashed=1"
    vals = [v for v in vals if v >= 0]
    if not vals:
        return "dashed=1"
    k = sw or 0.70875
    return "dashed=1;dashPattern=" + " ".join(f"{v / k:.2f}" for v in vals)


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------
def esc(t, quote=False):
    s = str(t)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if quote:
        s = s.replace('"', "&quot;").replace("\n", "&#10;")
    return s


def fmt(v):
    s = f"{float(v):.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class Emitter:
    def __init__(self, page_w, page_h):
        self.w = page_w
        self.h = page_h
        self.n = 2
        # document order is z-order in draw.io, so the three groups are emitted
        # bottom-up: edges, then boxes, then free text.  A 0.7px rule crossing a
        # label reads as a strikethrough if the text goes first.
        self.edges = []
        self.boxes = []
        self.texts = []

    def nid(self):
        self.n += 1
        return f"c{self.n}"

    def vertex(self, value, style, x, y, w, h, bucket):
        bucket.append(
            f'<mxCell id="{self.nid()}" value="{esc(value, quote=True)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" as="geometry"/></mxCell>'
        )

    def edge(self, chain, snap, arrows):
        pts = chain["pts"]
        s1 = snap(pts[0])
        s2 = snap(pts[-1])
        st = [
            "edgeStyle=none",
            "html=1",
            "rounded=1",
            "arcSize=6",
            f"strokeColor={chain['color'] or '#000000'}",
        ]
        if chain["sw"]:
            st.append(f"strokeWidth={fmt(chain['sw'])}")
        if chain.get("dash"):
            st.append(dash_pattern(chain["dash"], chain["sw"]))
        d_end = (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
        d_beg = (pts[0][0] - pts[1][0], pts[0][1] - pts[1][1])
        # geometry decides, not attachment: an arrowhead on a free-standing end
        # is just as real as one on a box border, and requiring a snapped end
        # silently drops those
        a_end = take_arrow(arrows, pts[-1], d_end)
        a_beg = take_arrow(arrows, pts[0], d_beg)
        # draw.io draws a classic arrow on every edge unless told otherwise; an
        # explicit "none" on both ends is mandatory or every plain line grows a
        # triangle that is not in the source
        st.append(
            "endArrow=classic;endFill=1;endSize=3.7" if a_end else "endArrow=none"
        )
        st.append(
            "startArrow=classic;startFill=1;startSize=3.7"
            if a_beg
            else "startArrow=none"
        )

        attrs = [
            f'id="{self.nid()}"',
            'value=""',
            'edge="1"',
            'parent="1"',
        ]
        geo = []
        ends = ""
        if s1:
            b, ex, ey = s1
            attrs.append(f'source="{b["id"]}"')
            ends += f"exitX={ex:.4f};exitY={ey:.4f};exitPerimeter=1;"
        geo.append(
            f'<mxPoint x="{fmt(pts[0][0])}" y="{fmt(pts[0][1])}" as="sourcePoint"/>'
        )
        if s2:
            b, ex, ey = s2
            attrs.append(f'target="{b["id"]}"')
            ends += f"entryX={ex:.4f};entryY={ey:.4f};entryPerimeter=1;"
        geo.append(
            f'<mxPoint x="{fmt(pts[-1][0])}" y="{fmt(pts[-1][1])}" as="targetPoint"/>'
        )
        inner = pts[1:-1]
        # one waypoint still has to go into <Array>: draw.io drops a single-point
        # Array silently and connects source to target with a straight line,
        # which is what paints long diagonals across the page
        if inner:
            geo.append('<Array as="points">')
            geo.extend(f'<mxPoint x="{fmt(x)}" y="{fmt(y)}"/>' for x, y in inner)
            geo.append("</Array>")
        attrs.append('style="{}{}"'.format(";".join(st) + ";", ends))
        self.edges.append(
            '<mxCell {}><mxGeometry relative="1" as="geometry">{}</mxGeometry></mxCell>'.format(
                " ".join(attrs), "".join(geo)
            )
        )

    def box(self, b, labels):
        style = [
            "rounded=1",
            f"whiteSpace={'nowrap' if len(labels) <= 1 else 'wrap'}",
            "html=1",
            # absoluteArcSize=1 makes arcSize a pixel value; without it draw.io
            # reads it as a percentage of the shorter side and every card turns
            # into a pill
            "absoluteArcSize=1",
            f"arcSize={max(b['r'], 1):.1f}",
            f"fillColor={b['fill'] or 'none'}",
            f"strokeColor={b['stroke'] or 'none'}",
        ]
        if b["sw"]:
            style.append(f"strokeWidth={b['sw']:.2f}")
        if b["shadow"]:
            style.append("shadow=1")
        value = ""
        if labels:
            base = max(label["size"] for label in labels)
            style.append(f"fontSize={base:.1f}")
            style.append(f"fontFamily={labels[0]['family']}")
            style.append(f"fontColor={labels[0]['color'] or '#000000'}")
            sizes = {round(label["size"], 1) for label in labels}
            parts = []
            for label in labels:
                tx = esc(label["text"])
                if len(sizes) > 1:
                    parts.append(
                        f'<span style="font-size:{label["size"]:.1f}px">{tx}</span>'
                    )
                else:
                    parts.append(tx)
            value = "<br>".join(parts)
            top = min(label["bbox"][1] for label in labels)
            bot = max(label["bbox"][3] for label in labels)
            dcy = (top + bot) / 2.0 - (b["y"] + b["h"] / 2.0)
            if abs(dcy) > max(2.0, b["h"] * 0.1):
                if dcy < 0:
                    style.append("verticalAlign=top")
                    style.append(f"spacingTop={max(0.0, top - b['y']):.1f}")
                else:
                    style.append("verticalAlign=bottom")
                    style.append(f"spacingBottom={max(0.0, b['y'] + b['h'] - bot):.1f}")
        self.vertex(
            value, ";".join(style) + ";", b["x"], b["y"], b["w"], b["h"], self.boxes
        )

    def text(self, line):
        style = (
            "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;"
            "spacing=0;whiteSpace=nowrap;fontSize={:.1f};fontColor={};fontFamily={};".format(
                line["size"], line["color"] or "#000000", line["family"]
            )
        )
        # position by baseline: the <use y> is the baseline, and draw.io puts the
        # first line's cap top about 1.31em below the cell top at this font size
        self.vertex(
            line["text"],
            style,
            line["bbox"][0] - 1.0,
            line["baseline"] - 1.31 * line["size"],
            line["w"] + line["size"],
            1.3 * line["size"],
            self.texts,
        )

    def render(self, name="Page-1"):
        head = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<mxfile host="app.diagrams.net" agent="svg2drawio-rebuild" version="24.0.0">',
            (
                f'<diagram id="d" name="{esc(name, quote=True)}"><mxGraphModel dx="1400" dy="900" '
                'grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
                f'page="1" pageScale="1" pageWidth="{fmt(self.w)}" pageHeight="{fmt(self.h)}" '
                'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            ),
        ]
        tail = ["</root></mxGraphModel></diagram></mxfile>"]
        return "\n".join(head + self.edges + self.boxes + self.texts + tail) + "\n"


def take_arrow(arrows, p, dvec, maxd=4.5):
    """Claim the arrowhead sitting at ``p`` and pointing along ``dvec``."""
    best, bd = None, maxd
    for a in arrows:
        d = math.dist(a["tip"], p)
        if d >= bd:
            continue
        ax, ay = a["tip"][0] - a["cx"], a["tip"][1] - a["cy"]
        n = math.hypot(ax, ay) or 1.0
        dn = math.hypot(*dvec) or 1.0
        if (ax / n) * (dvec[0] / dn) + (ay / n) * (dvec[1] / dn) < 0.3:
            continue
        bd, best = d, a
    if best is not None:
        arrows.remove(best)
    return best


def make_snap(boxes, reach=9.0):
    """Attach an endpoint to a box border, as a relative exit/entry point."""

    def snap(p):
        best, bd = None, 1e9
        for b in boxes:
            dx = max(b["x"] - p[0], 0.0, p[0] - (b["x"] + b["w"]))
            dy = max(b["y"] - p[1], 0.0, p[1] - (b["y"] + b["h"]))
            d = math.hypot(dx, dy)
            if d < bd:
                bd, best = d, b
        if best is None or bd > reach:
            return None
        ex = min(1.0, max(0.0, (p[0] - best["x"]) / best["w"]))
        ey = min(1.0, max(0.0, (p[1] - best["y"]) / best["h"]))
        if min(ex, 1.0 - ex) < min(ey, 1.0 - ey):
            ex = 0.0 if ex < 0.5 else 1.0
        else:
            ey = 0.0 if ey < 0.5 else 1.0
        return best, ex, ey

    return snap


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------
def probe(path):
    """Report which pipeline the file needs, and how much work it implies."""
    root = ET.parse(path).getroot()  # noqa: S314 - local file chosen by the caller
    vb = NUM_RE.findall(root.get("viewBox") or "")
    size = f"{float(vb[2]):.0f}x{float(vb[3]):.0f}" if len(vb) == 4 else "?"
    texts = len(list(root.iter(NS + "text")))
    uses = len(list(root.iter(NS + "use")))
    glyph_els = [el for el in root.iter() if (el.get("id") or "").startswith("glyph-")]
    glyphs = len(glyph_els)
    paths = [e for e in root.iter(NS + "path")]
    filled = sum(1 for e in paths if e.get("fill") not in (None, "none"))
    dashed = sum(1 for e in paths if e.get("stroke-dasharray"))
    multi = 0
    for e in paths:
        if e.get("fill") in (None, "none") and (e.get("d") or "").count("M") > 1:
            multi += 1
    subsets = {subset_of(el.get("id")) for el in glyph_els}

    if texts:
        mode = "shapes  (svg2drawio.py --mode shapes)"
    elif uses and glyphs:
        mode = f"rebuild (rebuild.py --glyphs <table>, {len(subsets)} font subsets)"
    else:
        mode = "image   (svg2drawio.py --mode image)"

    rows = [
        ("page", size),
        ("<text>", texts),
        ("<use>", uses),
        ("glyph defs", glyphs),
        ("font subsets", len(subsets)),
        ("paths", len(paths)),
        ("filled paths", filled),
        ("stroke-only paths", len(paths) - filled),
        ("multi-subpath strokes", multi),
        ("dashed paths", dashed),
        ("pipeline", mode),
    ]
    width = max(len(k) for k, _ in rows)
    print(f"file  {path}")
    for k, v in rows:
        print(f"  {k:<{width}}  {v}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def convert(
    svg_path, out_path, glyph_table, font_family="Arial", cap_ratio=0.716, stats=None
):
    root = ET.parse(svg_path).getroot()  # noqa: S314 - local file chosen by the caller
    vb = NUM_RE.findall(root.get("viewBox") or "")
    page_w = float(vb[2]) if len(vb) == 4 else 0.0
    page_h = float(vb[3]) if len(vb) == 4 else 0.0

    style_el = root.find(NS + "style")
    css = parse_css(style_el.text if style_el is not None else "")

    body = [
        e
        for e in root
        if e.tag.replace(NS, "") not in ("defs", "style", "title", "desc")
    ]
    # tolerate one level of page wrapper, which cairo never adds but editors do
    if (
        len(body) == 1
        and body[0].tag == NS + "g"
        and not list(body[0].iter(NS + "use"))
    ):
        body = list(body[0])

    boxes, arrows, strokes = classify(body, css)
    chains = chain_strokes(strokes)

    outlines = glyph_outlines(root)
    gmap, fam_of = load_glyph_map(glyph_table)
    gmap = apply_blank_rule(gmap, outlines)
    missing = set()
    runs = text_runs(root, gmap, fam_of, cap_ratio, font_family, missing)
    if missing:
        # an unmapped glyph becomes a space, which silently eats a character
        # instead of failing, so say it out loud
        sample = ", ".join(sorted(missing)[:6])
        print(
            f"warning: {len(missing)} glyph(s) absent from the table, rendered as space: "
            f"{sample}{' …' if len(missing) > 6 else ''}",
            file=sys.stderr,
        )
    em, fallback = estimate_em(runs, outlines, gmap, cap_ratio)
    lines = build_lines(runs, outlines, gmap, em, fallback)

    by_box = collections.defaultdict(list)
    free = []
    for ln in lines:
        cx = (ln["bbox"][0] + ln["bbox"][2]) / 2.0
        cy = (ln["bbox"][1] + ln["bbox"][3]) / 2.0
        owner = None
        for b in boxes:
            if (
                b["x"] - 1.5 <= cx <= b["x"] + b["w"] + 1.5
                and b["y"] - 1.5 <= cy <= b["y"] + b["h"] + 1.5
            ):
                owner = b
                break
        if owner is None:
            free.append(ln)
        else:
            by_box[id(owner)].append(ln)

    em_out = Emitter(page_w, page_h)
    for b in boxes:
        b["id"] = em_out.nid()
    snap = make_snap(boxes)
    arrows_left = [dict(a) for a in arrows]
    for c in chains:
        em_out.edge(c, snap, arrows_left)
    for b in boxes:
        em_out.box(b, sorted(by_box.get(id(b), []), key=lambda d: d["baseline"]))
    for ln in free:
        em_out.text(ln)

    out_path.write_text(em_out.render(Path(svg_path).stem), encoding="utf-8")
    if stats is not None:
        stats.update(
            boxes=len(boxes),
            edges=len(em_out.edges),
            lines=len(lines),
            in_boxes=len(lines) - len(free),
            free=len(free),
            arrows=len(arrows) - len(arrows_left),
            glyphs=len(gmap),
            unmapped=len(missing),
        )
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Rebuild an outline-only (no <text>) SVG as an editable draw.io file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  python rebuild.py page.svg --probe\n"
        "  python rebuild.py page.svg --glyphs glyphs.tsv -o page.drawio",
    )
    ap.add_argument("svg")
    ap.add_argument("-o", "--out")
    ap.add_argument("--glyphs", help="glyph-id <tab> char [<tab> family] table")
    ap.add_argument("--probe", action="store_true", help="report the shape, then stop")
    ap.add_argument(
        "--font-family", default="Arial", help="family when the table is silent"
    )
    ap.add_argument(
        "--cap-ratio",
        type=float,
        default=0.716,
        help="cap height / em (Arial 0.716, Times 0.662)",
    )
    args = ap.parse_args(argv)

    if args.probe:
        return probe(args.svg)

    out = Path(args.out) if args.out else Path(args.svg).with_suffix(".drawio")
    stats = {}
    convert(args.svg, out, args.glyphs, args.font_family, args.cap_ratio, stats)
    print(f"output: {out}  ({out.stat().st_size / 1024.0:.1f} KB)")
    for k, v in stats.items():
        print(f"  {k:<10} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
