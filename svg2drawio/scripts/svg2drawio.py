#!/usr/bin/env python3
"""Convert SVG files into draw.io (.drawio / mxGraphModel) documents.

Why this shape
--------------
mxGraphModel has no primitive for an arbitrary Bezier path. It only offers
rectangles, ellipses, labels and stencils, so replaying every SVG ``path`` as an
editable mxCell is not possible. This script therefore does three things:

* graphics: each drawing unit is embedded as one SVG image cell
  (``shape=image``) and stays pixel-identical;
* text: pulled out of the image and emitted as a genuinely editable text cell;
* both are wrapped in a ``group`` so the unit still drags as a whole.

Two modes:

* ``shapes`` (default) - split by unit: image cell + editable text + group.
* ``image`` - the whole page as a single image cell: fully faithful, nothing
  editable.

Usage
-----
    python svg2drawio.py <svg-or-dir> [more...]
    python svg2drawio.py <dir> --mode image
    python svg2drawio.py a.svg b.svg -o out_dir
    python svg2drawio.py <dir> --recursive

Standard library only (Python 3.9+).
"""

import argparse
import copy
import hashlib
import math
import re
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
NS = f"{{{SVG_NS}}}"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

GRAPHICS_TAGS = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
CONTAINER_TAGS = {"g", "svg", "a"}
SKIP_TAGS = {"style", "defs", "title", "desc", "metadata"}

NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
PATH_TOKEN_RE = re.compile(
    r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
)

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# Affine transforms, matrix form (a, b, c, d, e, f):
#   x' = a*x + c*y + e
#   y' = b*x + d*y + f
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
    """Parse an SVG transform attribute (translate/scale/matrix/rotate/skew)."""
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
        elif n == "skewx" and v:
            t = (1.0, 0.0, math.tan(math.radians(v[0])), 1.0, 0.0, 0.0)
        elif n == "skewy" and v:
            t = (1.0, math.tan(math.radians(v[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = mat_mul(m, t)
    return m


def mat_rotation_deg(m):
    """Rotation angle in degrees, decomposed from the matrix."""
    return math.degrees(math.atan2(m[1], m[0]))


# --------------------------------------------------------------------------
# Path parsing and bounding boxes
# --------------------------------------------------------------------------
def _cubic(p0, p1, p2, p3, steps):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def _quad(p0, p1, p2, steps):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        out.append(
            (
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            )
        )
    return out


def _arc(p0, rx, ry, phi_deg, laf, sf, p1, steps):
    """Endpoint form to centre form, then sample."""
    if rx == 0 or ry == 0:
        return [p1]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cosp, sinp = math.cos(phi), math.sin(phi)
    dx2, dy2 = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1p = cosp * dx2 + sinp * dy2
    y1p = -sinp * dx2 + cosp * dy2
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if laf == sf:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cosp * cxp - sinp * cyp + (p0[0] + p1[0]) / 2.0
    cy = sinp * cxp + cosp * cyp + (p0[1] + p1[1]) / 2.0

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    th1 = ang(1.0, 0.0, ux, uy)
    dth = ang(ux, uy, vx, vy)
    if not sf and dth > 0:
        dth -= 2 * math.pi
    elif sf and dth < 0:
        dth += 2 * math.pi
    out = []
    for i in range(1, steps + 1):
        t = th1 + dth * i / steps
        ct, st = math.cos(t), math.sin(t)
        out.append(
            (
                cx + rx * ct * cosp - ry * st * sinp,
                cy + rx * ct * sinp + ry * st * cosp,
            )
        )
    return out


def path_points(d, steps=16):
    """Flatten a path's ``d`` into a polyline in the element's own coordinates."""
    toks = PATH_TOKEN_RE.findall(d or "")
    if not toks:
        return []
    i, n = 0, len(toks)
    cmd = None
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    prev_ctrl = None
    pts = []

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < n:
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                cur = start
                pts.append(cur)
                prev_ctrl = None
                continue
        elif cmd is None:
            i += 1
            continue

        rel = cmd.islower()
        c = cmd.upper()

        if c == "M":
            x, y = num(), num()
            if rel:
                x, y = x + cur[0], y + cur[1]
            cur = start = (x, y)
            pts.append(cur)
            prev_ctrl = None
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = num(), num()
            if rel:
                x, y = x + cur[0], y + cur[1]
            cur = (x, y)
            pts.append(cur)
            prev_ctrl = None
        elif c == "H":
            x = num()
            x = x + cur[0] if rel else x
            cur = (x, cur[1])
            pts.append(cur)
            prev_ctrl = None
        elif c == "V":
            y = num()
            y = y + cur[1] if rel else y
            cur = (cur[0], y)
            pts.append(cur)
            prev_ctrl = None
        elif c == "C":
            x1, y1, x2, y2, x, y = (num() for _ in range(6))
            if rel:
                x1, y1 = x1 + cur[0], y1 + cur[1]
                x2, y2 = x2 + cur[0], y2 + cur[1]
                x, y = x + cur[0], y + cur[1]
            pts.extend(_cubic(cur, (x1, y1), (x2, y2), (x, y), steps))
            prev_ctrl = (x2, y2)
            cur = (x, y)
        elif c == "S":
            x2, y2, x, y = (num() for _ in range(4))
            if rel:
                x2, y2 = x2 + cur[0], y2 + cur[1]
                x, y = x + cur[0], y + cur[1]
            if prev_ctrl is not None:
                x1, y1 = 2 * cur[0] - prev_ctrl[0], 2 * cur[1] - prev_ctrl[1]
            else:
                x1, y1 = cur
            pts.extend(_cubic(cur, (x1, y1), (x2, y2), (x, y), steps))
            prev_ctrl = (x2, y2)
            cur = (x, y)
        elif c == "Q":
            x1, y1, x, y = (num() for _ in range(4))
            if rel:
                x1, y1 = x1 + cur[0], y1 + cur[1]
                x, y = x + cur[0], y + cur[1]
            pts.extend(_quad(cur, (x1, y1), (x, y), steps))
            prev_ctrl = (x1, y1)
            cur = (x, y)
        elif c == "T":
            x, y = num(), num()
            if rel:
                x, y = x + cur[0], y + cur[1]
            if prev_ctrl is not None:
                x1, y1 = 2 * cur[0] - prev_ctrl[0], 2 * cur[1] - prev_ctrl[1]
            else:
                x1, y1 = cur
            pts.extend(_quad(cur, (x1, y1), (x, y), steps))
            prev_ctrl = (x1, y1)
            cur = (x, y)
        elif c == "A":
            rx, ry, phi, laf, sf, x, y = (num() for _ in range(7))
            if rel:
                x, y = x + cur[0], y + cur[1]
            pts.extend(_arc(cur, rx, ry, phi, int(laf), int(sf), (x, y), max(8, steps)))
            prev_ctrl = None
            cur = (x, y)
        else:
            i += 1

    return pts


def _floats(el, *names):
    out = []
    for nm in names:
        v = el.get(nm)
        out.append(float(v) if v is not None else None)
    return out


def element_points(el, tag, steps):
    """Sample points for one element, in that element's own coordinates."""
    try:
        if tag == "path":
            return path_points(el.get("d"), steps)
        if tag == "rect":
            x, y, w, h = _floats(el, "x", "y", "width", "height")
            x = x or 0.0
            y = y or 0.0
            w = w or 0.0
            h = h or 0.0
            return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        if tag == "line":
            x1, y1, x2, y2 = _floats(el, "x1", "y1", "x2", "y2")
            return [(x1 or 0.0, y1 or 0.0), (x2 or 0.0, y2 or 0.0)]
        if tag in ("polyline", "polygon"):
            nums = NUM_RE.findall(el.get("points") or "")
            pts = [
                (float(nums[k]), float(nums[k + 1])) for k in range(0, len(nums) - 1, 2)
            ]
            if tag == "polygon" and pts:
                pts.append(pts[0])
            return pts
        if tag in ("circle", "ellipse"):
            cx, cy = _floats(el, "cx", "cy")
            cx, cy = cx or 0.0, cy or 0.0
            if tag == "circle":
                r = float(el.get("r") or 0.0)
                rx = ry = r
            else:
                rx, ry = _floats(el, "rx", "ry")
                rx = rx or 0.0
                ry = ry or 0.0
            return [
                (cx + rx * math.cos(a), cy + ry * math.sin(a))
                for a in [k * math.pi / 18 for k in range(36)]
            ]
    except TypeError:
        # a malformed attribute is not worth aborting the file for
        return []
    except ValueError:
        return []
    return []


def stroke_pad(el):
    """Half the stroke width, used to grow the bbox so strokes are not clipped."""
    stroke = (el.get("stroke") or "").strip().lower()
    if stroke in ("", "none") or stroke.startswith("url("):
        return 0.0
    try:
        sw = float(el.get("stroke-width") or 1.0)
    except ValueError:
        sw = 1.0
    return sw / 2.0


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------
def parse_css(text):
    rules = {}
    if not text:
        return rules
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", text):
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


def resolve_style(css, *classes):
    """Merge class styles in order; later wins."""
    style = {}
    for cls in classes:
        if not cls:
            continue
        for key in cls.split():
            style.update(css.get(key, {}))
    return style


def css_px(value, default):
    """Convert pt / px / unitless to px (1 SVG user unit == 1 px)."""
    if not value:
        return default
    v = value.strip().lower()
    try:
        if v.endswith("pt"):
            return float(v[:-2]) * 96.0 / 72.0
        if v.endswith("px"):
            return float(v[:-2])
        if v.endswith("em"):
            return float(v[:-2]) * 16.0
        return float(v)
    except ValueError:
        return default


def char_width(ch, fs):
    """Rough advance width: full-width/CJK 1.0em, everything else 0.55em."""
    o = ord(ch)
    if o > 0x2E7F or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFF60:
        return fs
    if ch in "iljt.,:;'!|":
        return fs * 0.30
    if ch in "fIr-()[] ":
        return fs * 0.38
    if ch in "mwMW":
        return fs * 0.85
    return fs * 0.56


def text_width(s, fs):
    return sum(char_width(c, fs) for c in s)


# --------------------------------------------------------------------------
# Text runs
# --------------------------------------------------------------------------
def collect_text_runs(text_el, parent_matrix, css):
    """Split one <text> into runs of one line and one font style.

    Returns [(text, x, y, style_dict, rotation_deg)] in global coordinates.
    """
    base_cls = text_el.get("class")
    tspans = [c for c in text_el if c.tag == NS + "tspan"]
    items = tspans if tspans else [text_el]

    runs = []
    cursor_x = None
    cur_y = None

    for sp in items:
        raw = sp.text or ""
        if not raw.strip():
            continue
        cls = sp.get("class") or base_cls
        style = resolve_style(css, base_cls, cls)
        fs = css_px(style.get("font-size"), 13.333)
        x = sp.get("x")
        y = sp.get("y")
        if x is not None:
            cursor_x = float(NUM_RE.findall(x)[0])
        elif cursor_x is None:
            cursor_x = 0.0
        if y is not None:
            cur_y = float(NUM_RE.findall(y)[0])
        elif cur_y is None:
            cur_y = fs

        gx, gy = mat_apply(parent_matrix, cursor_x, cur_y)
        sig = (
            round(gy, 2),
            style.get("font-family", ""),
            style.get("font-weight", ""),
            round(fs, 2),
            style.get("fill", ""),
        )

        if runs and runs[-1][5] == sig:
            prev = runs[-1]
            runs[-1] = (prev[0] + raw, prev[1], prev[2], prev[3], prev[4], sig)
        else:
            runs.append((raw, gx, gy, style, mat_rotation_deg(parent_matrix), sig))

        cursor_x += text_width(raw, fs)

    return runs


# --------------------------------------------------------------------------
# One drawing unit to a mini-SVG
# --------------------------------------------------------------------------
def unit_primitives(el, matrix, out, steps):
    """Collect sample points of every primitive inside a unit (global coords)."""
    for ch in el:
        tag = ch.tag.replace(NS, "")
        m = mat_mul(matrix, parse_transform(ch.get("transform")))
        if tag in CONTAINER_TAGS:
            unit_primitives(ch, m, out, steps)
        elif tag in GRAPHICS_TAGS:
            pad = stroke_pad(ch)
            out.extend(
                (mat_apply(m, p[0], p[1]), pad) for p in element_points(ch, tag, steps)
            )


def referenced_ids(root):
    """Ids reachable through url(#id) or xlink:href anywhere in the tree."""
    ids = set()
    for el in root.iter():
        for v in el.attrib.values():
            for m in re.findall(r"url\(#([^)]+)\)|#([\w:.-]+)", v):
                hits = [g for g in m if g]
                if hits and (
                    v.startswith("url(")
                    or el.tag == NS + "use"
                    or "href" in str(el.tag)
                ):
                    ids.add(hits[0])
    return ids


def build_unit_svg(unit_el, bbox, style_el, defs_el, base_matrix=IDENT):
    """Wrap a text-stripped unit into a standalone mini-SVG matching its bbox.

    ``base_matrix`` is the root-to-unit transform (usually the page translate).
    The bbox was computed in absolute coordinates, so the same transform has to
    be re-applied here; otherwise the content shifts and gets clipped by the
    viewBox, and line ends and arrowheads are the first casualties.
    """
    svg = ET.Element(NS + "svg")  # namespace is declared by ET; never set xmlns by hand
    x, y, w, h = bbox
    svg.set("viewBox", f"{x:.3f} {y:.3f} {max(w, 0.01):.3f} {max(h, 0.01):.3f}")
    svg.set("width", f"{max(w, 0.01):.3f}")
    svg.set("height", f"{max(h, 0.01):.3f}")

    if style_el is not None and (style_el.text or "").strip():
        st = ET.SubElement(svg, NS + "style")
        st.set("type", "text/css")
        st.text = style_el.text

    if defs_el is not None and len(defs_el):
        used = referenced_ids(unit_el)
        keep = []
        for d in defs_el:
            did = d.get("id")
            if did and did in used:
                keep.append(copy.deepcopy(d))
        # gradients may reference each other through xlink:href; close the chain
        changed = True
        while changed:
            changed = False
            have = {k.get("id") for k in keep}
            for d in defs_el:
                did = d.get("id")
                if did in have:
                    continue
                for k in keep:
                    if k.get("id") in used and _href_of(k) == did:
                        keep.append(copy.deepcopy(d))
                        changed = True
                        break
        if keep:
            nd = ET.SubElement(svg, NS + "defs")
            for d in keep:
                nd.append(d)

    if base_matrix != IDENT:
        holder = ET.SubElement(svg, NS + "g")
        holder.set(
            "transform", "matrix({})".format(",".join(fmt(v) for v in base_matrix))
        )
        holder.append(copy.deepcopy(unit_el))
    else:
        svg.append(copy.deepcopy(unit_el))
    return svg


def _href_of(el):
    for key in (f"{{{XLINK_NS}}}href", "href"):
        v = el.get(key)
        if v and v.startswith("#"):
            return v[1:]
    return None


def svg_to_data_uri(svg_el):
    """Serialise to a percent-encoded data:image/svg+xml URI.

    Base64 must not be used: the semicolon in ``data:image/svg+xml;base64,`` is
    read by draw.io's style parser as a key/value separator, the image value is
    truncated to ``data:image/svg+xml``, and every image cell renders broken.
    Percent-encoded output contains no semicolon, so the whole value survives.
    """
    data = ET.tostring(svg_el, encoding="utf-8").decode("utf-8")
    return "data:image/svg+xml," + quote(data, safe="/:")


# --------------------------------------------------------------------------
# Converter
# --------------------------------------------------------------------------
class Cell:
    __slots__ = ("h", "id", "parent", "style", "value", "vertex", "w", "x", "y")

    def __init__(self, cid, value, style, parent, x, y, w, h, vertex=True):
        self.id = cid
        self.value = value
        self.style = style
        self.parent = parent
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.vertex = vertex

    def to_xml(self, indent="        "):
        attrs = [("id", self.id), ("value", self.value)]
        if self.style:
            attrs.append(("style", self.style))
        attrs += [("vertex", "1" if self.vertex else "0"), ("parent", self.parent)]
        head = " ".join(f'{k}="{_esc(v)}"' for k, v in attrs)
        geo = f'{indent}  <mxGeometry x="{fmt(self.x)}" y="{fmt(self.y)}" width="{fmt(self.w)}" height="{fmt(self.h)}" as="geometry" />'
        return f"{indent}<mxCell {head}>\n{geo}\n{indent}</mxCell>"


def _esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "&#10;")
    )


def fmt(v):
    if v is None:
        return "0"
    s = f"{float(v):.2f}"
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class SvgConverter:
    def __init__(self, path, mode="shapes", steps=16, keep_bg=True, split_groups=True):
        self.path = Path(path)
        self.mode = mode
        self.steps = steps
        self.keep_bg = keep_bg
        self.split_groups = split_groups
        self._next_id = 2
        self._base = IDENT
        self.page_w = 0.0
        self.page_h = 0.0
        self.stats = {"units": 0, "images": 0, "texts": 0, "groups": 0, "baked_text": 0}

    def nid(self):
        v = str(self._next_id)
        self._next_id += 1
        return v

    # -- main flow -------------------------------------------------------
    def convert(self):
        # local input chosen by the caller, not network data
        root = ET.parse(self.path).getroot()  # noqa: S314
        self._warn_outline_only(root)
        vb = root.get("viewBox")
        if vb:
            _, _, pw, ph = [float(t) for t in NUM_RE.findall(vb)[:4]]
        else:
            pw = float(NUM_RE.findall(root.get("width") or "0")[0] or 0)
            ph = float(NUM_RE.findall(root.get("height") or "0")[0] or 0)
        self.page_w, self.page_h = pw, ph

        style_el = root.find(NS + "style")
        defs_el = root.find(NS + "defs")
        css = parse_css(style_el.text if style_el is not None else "")

        page = self._find_page(root)
        if self.mode == "image":
            return self._whole_image(root, style_el, page)
        return self._split_units(page, root, style_el, defs_el, css)

    def _warn_outline_only(self, root):
        """Say out loud when the file needs the other pipeline.

        A cairo/Pango export has no ``<text>`` at all, so every unit converts to
        an image with no editable label and the batch looks like a success.  The
        tell is the glyph-reference count; pointing at rebuild.py here is cheaper
        than discovering it after delivery.
        """
        if any(True for _ in root.iter(NS + "text")):
            return
        uses = sum(1 for _ in root.iter(NS + "use"))
        if uses >= 200:
            print(
                f"warning: {self.path.name}: no <text>, {uses} glyph references — an "
                "outline export. Use rebuild.py to restore editable labels; this "
                "script can only embed the page.",
                file=sys.stderr,
            )

    def _find_page(self, root):
        """Locate the container holding the artwork (usually g#page1)."""
        for ch in root:
            if ch.tag == NS + "g":
                return ch
        return root

    # -- whole page ------------------------------------------------------
    def _whole_image(self, root, style_el, page):
        svg = ET.Element(NS + "svg")
        svg.set("viewBox", f"0 0 {fmt(self.page_w)} {fmt(self.page_h)}")
        svg.set("width", fmt(self.page_w))
        svg.set("height", fmt(self.page_h))
        for ch in root:
            if ch.tag in (NS + "style", NS + "defs"):
                svg.append(copy.deepcopy(ch))
        if page is root:
            for ch in root:
                if ch.tag not in (NS + "style", NS + "defs"):
                    svg.append(copy.deepcopy(ch))
        else:
            svg.append(copy.deepcopy(page))
        uri = svg_to_data_uri(svg)
        self.stats["images"] = 1
        self.stats["units"] = 1
        return [
            Cell(
                self.nid(),
                "",
                _image_style(uri),
                "1",
                0.0,
                0.0,
                self.page_w,
                self.page_h,
            )
        ]

    # -- per unit --------------------------------------------------------
    def _split_units(self, page, root, style_el, defs_el, css):
        cells = []
        base = parse_transform(page.get("transform")) if page is not root else IDENT
        self._base = base

        for child in page:
            tag = child.tag.replace(NS, "")
            if tag in SKIP_TAGS:
                continue
            m = mat_mul(base, parse_transform(child.get("transform")))

            # full-page background rect: emit as a plain shape, not an image
            if tag == "rect" and self.keep_bg and self._is_full_page(child, m):
                cells.append(self._bg_cell(child, m, css))
                continue

            if tag in CONTAINER_TAGS or tag in GRAPHICS_TAGS:
                unit_cells, _bbox = self._emit_unit(
                    child, tag, m, style_el, defs_el, css
                )
                cells.extend(unit_cells)
        return cells

    def _is_full_page(self, rect, m):
        try:
            w = float(rect.get("width") or 0) * abs(m[0])
            h = float(rect.get("height") or 0) * abs(m[3])
        except TypeError:
            return False
        except ValueError:
            return False
        # 0.95, not 0.98: Edraw exports inset the frame by the page translate
        return w >= self.page_w * 0.95 and h >= self.page_h * 0.95

    def _bg_cell(self, rect, m, css):
        x0, y0 = mat_apply(m, 0.0, 0.0)
        w = float(rect.get("width") or 0) * abs(m[0])
        h = float(rect.get("height") or 0) * abs(m[3])
        # the class carries the real paint; a presentation attribute overrides it
        cls = resolve_style(css, rect.get("class"))
        fill = rect.get("fill") or cls.get("fill") or "#ffffff"
        stroke = rect.get("stroke") or cls.get("stroke") or "none"
        try:
            sw = float(rect.get("stroke-width") or cls.get("stroke-width") or 1.0)
        except ValueError:
            sw = 1.0
        style = (
            f"rounded=0;whiteSpace=wrap;html=1;fillColor={_color(fill)};"
            f"strokeColor={_color(stroke)};strokeWidth={fmt(sw)};"
            "movable=1;resizable=1;"
        )
        self.stats["units"] += 1
        return Cell(self.nid(), "", style, "1", x0, y0, w, h)

    def _emit_unit(self, unit, tag, m, style_el, defs_el, css):
        """One unit to (cells, bbox)."""
        texts = [e for e in unit.iter() if e.tag == NS + "text"]

        # Rotated text is unreliable as an mxCell, so bake it into the image.
        extract = abs(mat_rotation_deg(m)) < 0.5
        runs = []
        if texts and extract:
            for t in texts:
                tm = self._matrix_for(t, unit, m)
                runs.extend(collect_text_runs(t, tm, css))
        if texts and not extract:
            self.stats["baked_text"] += len(texts)

        # geometry bbox, including the case where the unit *is* a primitive
        pts = []
        if tag in GRAPHICS_TAGS:
            pad = stroke_pad(unit)
            pts.extend(
                (mat_apply(m, p[0], p[1]), pad)
                for p in element_points(unit, tag, self.steps)
            )
        unit_primitives(unit, m, pts, self.steps)
        if not pts:
            return self._text_cells(runs, parent="1"), None

        xs = [p[0][0] for p in pts]
        ys = [p[0][1] for p in pts]
        pad = max([p[1] for p in pts] + [0.0]) + 0.5
        bx, by = min(xs) - pad, min(ys) - pad
        bw, bh = (max(xs) + pad) - bx, (max(ys) + pad) - by
        bw = max(bw, 0.2)
        bh = max(bh, 0.2)

        unit_copy = copy.deepcopy(unit)
        if extract:
            self._strip_text(unit_copy)

        mini = build_unit_svg(
            unit_copy, (bx, by, bw, bh), style_el, defs_el, self._base
        )
        uri = svg_to_data_uri(mini)

        group_id = None
        if runs and self.split_groups:
            group_id = self.nid()
            self.stats["groups"] += 1

        parent = group_id or "1"
        cells = [Cell(self.nid(), "", _image_style(uri), parent, bx, by, bw, bh)]
        self.stats["images"] += 1
        self.stats["units"] += 1
        cells.extend(self._text_cells(runs, parent=parent))

        if group_id:
            gx0 = min(c.x for c in cells)
            gy0 = min(c.y for c in cells)
            gx1 = max(c.x + c.w for c in cells)
            gy1 = max(c.y + c.h for c in cells)
            # draw.io group children are positioned relative to the group origin
            for c in cells:
                c.x -= gx0
                c.y -= gy0
            cells.insert(
                0, Cell(group_id, "", "group", "1", gx0, gy0, gx1 - gx0, gy1 - gy0)
            )
        return cells, (bx, by, bw, bh)

    def _matrix_for(self, el, unit, unit_matrix):
        """Accumulate transforms from the unit down to ``el``."""
        found = []

        def walk(node, acc):
            mm = mat_mul(acc, parse_transform(node.get("transform")))
            if node is el:
                found.append(mm)
                return True
            return any(walk(c, mm) for c in node if c.tag in CONTAINER_TAGS)

        walk(unit, unit_matrix)
        return found[0] if found else unit_matrix

    def _strip_text(self, el):
        for parent in list(el.iter()):
            for child in list(parent):
                if child.tag == NS + "text":
                    parent.remove(child)

    def _text_cells(self, runs, parent):
        cells = []
        for text, gx, gy, style, rot, _sig in runs:
            if not text.strip():
                continue
            fs = css_px(style.get("font-size"), 13.333)
            color = _color(style.get("fill") or "#000000")
            fam = (
                (style.get("font-family") or "Arial").split(",")[0].strip().strip("'\"")
            )
            weight = (style.get("font-weight") or "").lower()
            fstyle = 0
            if weight in ("bold", "600", "700", "800", "900"):
                fstyle |= 1
            if (style.get("font-style") or "").lower() in ("italic", "oblique"):
                fstyle |= 2
            shift = (style.get("baseline-shift") or "").lower()
            baseline = gy
            if shift.startswith("super"):
                baseline -= fs * 0.34
            elif shift.startswith("sub"):
                baseline += fs * 0.20

            w = max(text_width(text, fs) + 4.0, fs * 1.2)
            h = fs * 1.5
            x = gx - 2.0
            y = baseline - 0.34 * fs - h / 2.0
            st = (
                "text;html=0;strokeColor=none;fillColor=none;align=left;"
                "verticalAlign=middle;whiteSpace=nowrap;spacing=0;spacingTop=0;"
                f"fontSize={fmt(fs)};fontColor={color};"
                f"fontFamily={_style_safe(fam)};fontStyle={fstyle};"
            )
            if rot:
                st += f"rotation={fmt(rot)};"
            cells.append(Cell(self.nid(), text, st, parent, x, y, w, h))
            self.stats["texts"] += 1
        return cells

    # -- output ----------------------------------------------------------
    def to_drawio(self, cells):
        out = [
            (
                f'<mxfile host="svg2drawio" modified="{_now()}" agent="svg2drawio.py" '
                'version="24.0.0">'
            ),
            f'  <diagram id="{_diagram_id(self.path.stem)}" name="{_esc(self.path.stem)}">',
            (
                '    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" '
                'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
                f'pageWidth="{fmt(self.page_w)}" pageHeight="{fmt(self.page_h)}" math="0" shadow="0">'
            ),
            "      <root>",
            '        <mxCell id="0" />',
            '        <mxCell id="1" parent="0" />',
        ]
        out.extend(c.to_xml() for c in cells)
        out.extend(
            ["      </root>", "    </mxGraphModel>", "  </diagram>", "</mxfile>"]
        )
        return "\n".join(out) + "\n"


def _image_style(uri):
    return (
        "shape=image;verticalLabelPosition=bottom;verticalAlign=top;"
        f"aspect=fixed;html=1;image={uri};"
    )


def _style_safe(s):
    return re.sub(r"[;\"\n\r]", "", s)


def _color(c):
    c = (c or "").strip()
    if c.lower() in ("", "none"):
        return "none"
    named = {
        "white": "#ffffff",
        "black": "#000000",
        "red": "#ff0000",
        "blue": "#0000ff",
        "green": "#008000",
        "gray": "#808080",
        "grey": "#808080",
    }
    return named.get(c.lower(), c)


def _now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _diagram_id(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def iter_svgs(targets, recursive):
    files = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            files.extend(
                sorted(p.rglob("*.svg")) if recursive else sorted(p.glob("*.svg"))
            )
        elif p.suffix.lower() == ".svg" and p.exists():
            files.append(p)
        else:
            print(f"skip (not an SVG, or missing): {p}", file=sys.stderr)
    seen, uniq = set(), []
    for f in files:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(f)
    return uniq


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert SVG files into draw.io (.drawio) documents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  python svg2drawio.py <dir> -o <dir>/drawio\n"
        "  python svg2drawio.py <dir> --mode image",
    )
    ap.add_argument("targets", nargs="+", help="SVG files or directories")
    ap.add_argument(
        "-o", "--out", help="output directory (default: drawio/ beside the source)"
    )
    ap.add_argument(
        "--mode",
        choices=["shapes", "image"],
        default="shapes",
        help="shapes = split by unit, text editable (default); image = one cell",
    )
    ap.add_argument("--recursive", action="store_true", help="walk subdirectories")
    ap.add_argument(
        "--steps", type=int, default=16, help="curve sampling steps (default 16)"
    )
    ap.add_argument(
        "--no-bg", action="store_true", help="do not emit the page background"
    )
    args = ap.parse_args(argv)

    files = iter_svgs(args.targets, args.recursive)
    if not files:
        print("no SVG files found.", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else (files[0].parent / "drawio")
    out_dir.mkdir(parents=True, exist_ok=True)

    total = {"units": 0, "images": 0, "texts": 0, "groups": 0}
    rows = []
    for f in files:
        conv = SvgConverter(f, mode=args.mode, steps=args.steps, keep_bg=not args.no_bg)
        try:
            cells = conv.convert()
        except (
            ET.ParseError,
            IndexError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            # one malformed file must not stop the batch
            print(f"failed {f.name}: {exc}", file=sys.stderr)
            continue
        xml = conv.to_drawio(cells)
        dest = out_dir / (f.stem + ".drawio")
        dest.write_text(xml, encoding="utf-8")
        for k in total:
            total[k] += conv.stats[k]
        rows.append(
            (
                f.name,
                len(cells),
                conv.stats["units"],
                conv.stats["texts"],
                dest.stat().st_size / 1024.0,
            )
        )

    print(f"output: {out_dir.resolve()}")
    print(f"{'file':<38} {'cells':>6} {'shapes':>6} {'texts':>6} {'KB':>9}")
    print("-" * 70)
    for name, cells_n, units, texts, kb in rows:
        print(f"{name:<38} {cells_n:>6} {units:>6} {texts:>6} {kb:>9.1f}")
    print("-" * 70)
    print(f"{'total':<38} {'':>6} {total['units']:>6} {total['texts']:>6}")
    print(f"done: {len(rows)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
