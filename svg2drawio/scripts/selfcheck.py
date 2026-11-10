#!/usr/bin/env python3
"""One-command self-check for the svg2drawio skill package.

Four groups of checks, all offline and standard-library only:

1. package consistency - ``name`` matches the directory, every path the docs
   promise exists, documented CLI flags exist in the converters;
2. round trip over real data - convert ``assets/sample.svg`` (embedded-shape
   pipeline) and re-parse the result, asserting the invariants that the draw.io
   renderer actually cares about (no semicolons inside a style value, group
   children relative to the group, the page transform re-applied inside every
   mini-SVG);
3. outline pipeline - convert ``assets/outline-sample.svg`` with
   ``assets/outline-glyphs.tsv`` and assert the rules that only matter for a
   rebuilt chart: blank glyph read as a space, every edge carrying ``edge="1"``
   plus explicit arrow flags, single waypoint kept, dash converted through the
   stroke width, document order equal to z-order;
4. baseline comparison - cell and unit counts against ``assets/baseline.json``.

Usage:
    python scripts/selfcheck.py            # exit 0 = all green
"""

import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_converter():
    """Load the sibling converter by path; a plain import does not type-resolve here."""
    spec = importlib.util.spec_from_file_location(
        "s2d_converter", HERE / "svg2drawio.py"
    )
    if spec is None or spec.loader is None:
        msg = f"cannot load the converter: {HERE / 'conv.py'}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_converter()


def load_module(name, filename):
    """Load a sibling script by path; a plain import does not type-resolve here."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        msg = f"cannot load {HERE / filename}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild = load_module("s2d_rebuild", "rebuild.py")

NS = "{http://www.w3.org/2000/svg}"
FRONTMATTER_KEYS = {"name", "description", "agent_created", "disable-model-invocation"}
PATH_REFS = (
    re.compile(r"<this skill dir>/([A-Za-z0-9_./-]+)"),
    re.compile(r"`((?:scripts|references|assets)/[A-Za-z0-9_./-]+)`"),
)

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    return ok


# --------------------------------------------------------------------------
# 1. package consistency
# --------------------------------------------------------------------------
def check_identity():
    skill_md = ROOT / "SKILL.md"
    if not skill_md.exists():
        return record("identity", False, "SKILL.md missing")
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return record("identity", False, "no frontmatter")
    keys = [ln.split(":", 1)[0].strip() for ln in m.group(1).splitlines() if ":" in ln]
    extra = [k for k in keys if k not in FRONTMATTER_KEYS]
    name = ""
    for ln in m.group(1).splitlines():
        if ln.startswith("name:"):
            name = ln.split(":", 1)[1].strip().strip("'\"")
    ok = name == ROOT.name and not extra
    return record(
        "identity",
        ok,
        "name={!r} dir={!r} extra={}".format(name, ROOT.name, extra or "-"),
    )


def check_doc_paths():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    refs = set()
    for rx in PATH_REFS:
        refs.update(rx.findall(text))
    missing = [r for r in sorted(refs) if not (ROOT / r).exists()]
    return record(
        "doc paths",
        not missing,
        f"{len(refs)} referenced, missing: {', '.join(missing) or 'none'}",
    )


def check_cli_docs():
    doc = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    lines = doc.splitlines()
    problems = []
    counts = []
    for script in ("svg2drawio.py", "rebuild.py"):
        src = (HERE / script).read_text(encoding="utf-8")
        flags = set(re.findall(r'ap\.add_argument\(\s*"(-[^"]+)"', src))
        # only lines that invoke that entry point, so flags of draw.io or
        # headless Chrome quoted in the docs are not counted here
        documented = {
            f
            for ln in lines
            if script in ln
            for f in re.findall(r"(?<![\w-])(--?[a-z][a-z-]*)", ln)
        }
        unknown = sorted(documented - flags)
        if unknown:
            problems.append(f"{script}: {unknown}")
        counts.append(len(documented))

    src = (HERE / "svg2drawio.py").read_text(encoding="utf-8")
    mode_values = set(re.findall(r"choices=\[([^\]]+)\]", src))
    modes = set()
    for group in mode_values:
        modes.update(v.strip().strip("\"'") for v in group.split(","))
    bad_modes = sorted(set(re.findall(r"--mode\s+([a-z]+)", doc)) - modes)

    ok = not problems and not bad_modes
    return record(
        "cli docs",
        ok,
        f"documented flags per script={counts} problems={problems or 'none'} "
        f"bad modes={bad_modes or 'none'}",
    )


# --------------------------------------------------------------------------
# 2. round trip
# --------------------------------------------------------------------------
def convert_sample(out_dir):
    src = ROOT / "assets" / "sample.svg"
    converter = mod.SvgConverter(src)
    cells = converter.convert()
    xml = converter.to_drawio(cells)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    dest = Path(out_dir) / "sample.drawio"
    dest.write_text(xml, encoding="utf-8")
    return dest, converter


def parse_cells(dest):
    root = ET.parse(dest).getroot()  # noqa: S314 - local file we just wrote
    model = root.find(".//mxGraphModel")
    if model is None:
        msg = f"{dest}: no mxGraphModel element"
        raise ValueError(msg)
    cells, seq = {}, []
    for c in model.iter("mxCell"):
        geo = c.find("mxGeometry")
        if geo is None:
            continue
        points = [
            (float(p.get("x", 0)), float(p.get("y", 0))) for p in geo.iter("mxPoint")
        ]
        cells[c.get("id")] = {
            "parent": c.get("parent"),
            "style": c.get("style") or "",
            "value": c.get("value") or "",
            "edge": c.get("edge") == "1",
            "source": c.get("source"),
            "target": c.get("target"),
            "points": points,
            "x": float(geo.get("x", 0)),
            "y": float(geo.get("y", 0)),
            "w": float(geo.get("width", 0)),
            "h": float(geo.get("height", 0)),
        }
        seq.append(c.get("id"))
    return model, cells, seq


def image_uri(style):
    m = re.search(r"image=(data:image/svg\+xml,[^;\"]+)", style)
    return m.group(1) if m else None


def abs_pos(cells, cid):
    """Absolute position: a group child's geometry is relative to its parent."""
    c = cells[cid]
    x, y = c["x"], c["y"]
    parent = c["parent"]
    while parent in cells:
        x += cells[parent]["x"]
        y += cells[parent]["y"]
        parent = cells[parent]["parent"]
    return x, y


def check_structure(cells):
    problems = []
    for cid in cells:
        style = cells[cid]["style"]
        uri = image_uri(style)
        if not uri:
            continue
        if ";" in uri:
            problems.append(f"cell {cid}: semicolon inside image value")
            continue
        inner = unquote(uri.split(",", 1)[1])
        try:
            svg = ET.fromstring(inner)  # noqa: S314 - our own serialised output
        except ET.ParseError as exc:
            problems.append(f"cell {cid}: inner SVG unparsable ({exc})")
            continue
        vb = [float(v) for v in re.findall(r"[-+0-9.eE]+", svg.get("viewBox") or "")]
        if not vb or len(vb) != 4:
            problems.append(f"cell {cid}: no viewBox")
            continue
        x, y = abs_pos(cells, cid)
        want = (x, y, cells[cid]["w"], cells[cid]["h"])
        if any(abs(vb[i] - want[i]) > 0.05 for i in range(4)):
            problems.append(f"cell {cid}: viewBox {vb} != geometry {want}")
    return record("style/viewBox", not problems, "; ".join(problems) or "ok")


def check_group_relative(cells):
    problems = []
    for cid, c in cells.items():
        if c["style"] != "group":
            continue
        for kid, k in cells.items():
            if k["parent"] != cid:
                continue
            if k["x"] < -0.01 or k["y"] < -0.01:
                problems.append(f"{cid}/{kid}: negative offset")
            if k["x"] + k["w"] > c["w"] + 0.02 or k["y"] + k["h"] > c["h"] + 0.02:
                problems.append(f"{cid}/{kid}: child outside group")
    if not any(c["style"] == "group" for c in cells.values()):
        problems.append("no group cell produced")
    return record("group coords", not problems, "; ".join(problems) or "ok")


def check_page_transform(cells, page_matrix):
    if page_matrix == mod.IDENT:
        return record("page transform", True, "identity page, nothing to re-apply")
    want = "matrix({})".format(",".join(mod.fmt(v) for v in page_matrix))
    missing = []
    for cid, c in cells.items():
        uri = image_uri(c["style"])
        if not uri:
            continue
        inner = unquote(uri.split(",", 1)[1])
        if want not in inner:
            missing.append(cid)
    return record(
        "page transform",
        not missing,
        f"missing in {len(missing)} image cell(s): {missing or 'none'}",
    )


def check_text_runs(cells):
    values = " | ".join(c["value"] for c in cells.values())
    want = ["Root node", "Branch one", "second line"]
    missing = [w for w in want if w not in values]
    baked = [
        c
        for c in cells.values()
        if image_uri(c["style"]) and "Rotated" in unquote(c["style"])
    ]
    if not baked:
        missing.append("rotated text not baked into any image")
    return record("text runs", not missing, "missing: %s" % (missing or "none"))


def check_bg_paint(cells):
    """The page frame in the sample comes from a CSS class, not an attribute.

    Reading only ``fill``/``stroke`` on the rect silently drops that frame, which
    no structural check would notice, so it gets its own assertion.
    """
    plain = [
        c
        for c in cells.values()
        if "shape=image" not in c["style"] and c["style"] != "group"
    ]
    framed = [c for c in plain if "strokeColor=#cccccc" in c["style"]]
    if not framed:
        return record("bg paint", False, "page frame lost (strokeColor=#cccccc absent)")
    return record("bg paint", True, "fillColor=#ffffff + strokeColor=#cccccc")


# --------------------------------------------------------------------------
# 3. outline pipeline
# --------------------------------------------------------------------------
def convert_outline(out_dir):
    src = ROOT / "assets" / "outline-sample.svg"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    dest = Path(out_dir) / "outline-sample.drawio"
    stats = {}
    rebuild.convert(
        src, dest, ROOT / "assets" / "outline-glyphs.tsv", "Arial", 0.716, stats
    )
    return dest, stats


def check_probe():
    """The probe must send each sample to its own pipeline."""
    want = {"sample.svg": "shapes", "outline-sample.svg": "rebuild"}
    bad = []
    for name, expect in want.items():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rebuild.probe(ROOT / "assets" / name)
        if expect not in buf.getvalue():
            bad.append(f"{name} not classified as {expect}")
    return record("probe", not bad, "; ".join(bad) or "shapes / rebuild")


def check_outline_structure(cells, seq):
    """Rules that only exist in the rebuilt pipeline; each one failed for real once."""
    problems = []
    edges = [c for c in seq if cells[c]["edge"]]
    boxes = [
        c for c in seq if not cells[c]["edge"] and "text;html" not in cells[c]["style"]
    ]
    texts = [c for c in seq if "text;html" in cells[c]["style"]]

    for cid in edges:
        st = cells[cid]["style"]
        if "endArrow=" not in st or "startArrow=" not in st:
            problems.append(f"{cid}: arrow flags not explicit")

    inner = [len(cells[c]["points"]) - 2 for c in edges if len(cells[c]["points"]) >= 2]
    if 1 not in inner:
        problems.append("no edge kept a single waypoint (quirk 9)")
    if 2 not in inner:
        problems.append("no edge kept two waypoints")

    dashed = [c for c in edges if "dashed=1" in cells[c]["style"]]
    if len(dashed) != 1:
        problems.append(f"dashed edges={len(dashed)}, want 1")
    elif "dashPattern=5.00 2.50" not in cells[dashed[0]]["style"]:
        problems.append("dashPattern not divided by stroke width (quirk 10)")

    arrowed = [c for c in edges if "endArrow=classic" in cells[c]["style"]]
    if len(arrowed) != 1:
        problems.append(f"arrowed edges={len(arrowed)}, want 1")

    values = sorted(cells[c]["value"] for c in boxes)
    if values != ["Ab", "C d"]:
        problems.append(f"box labels {values}, want ['Ab', 'C d'] (blank glyph rule)")

    if not (edges and boxes and texts):
        problems.append("one of edges/boxes/texts is empty")
    else:
        order = (seq.index(edges[0]), seq.index(boxes[0]), seq.index(texts[0]))
        if not order[0] < order[1] < order[2]:
            problems.append(f"z-order {order}, want edges < boxes < texts (quirk 12)")

    return record(
        "outline structure", not problems, "; ".join(problems) or f"{len(edges)} edges"
    )


def check_outline_baseline(stats, seq):
    base = json.loads((ROOT / "assets" / "baseline.json").read_text(encoding="utf-8"))
    want = base["outline-sample.svg"]
    got = {
        "cells": len(seq),
        "boxes": stats.get("boxes"),
        "edges": stats.get("edges"),
        "lines": stats.get("lines"),
        "free": stats.get("free"),
        "arrows": stats.get("arrows"),
        "unmapped": stats.get("unmapped"),
    }
    diff = [f"{k} {got[k]}!={want[k]}" for k in want if got.get(k) != want[k]]
    return record(
        "outline baseline", not diff, "; ".join(diff) or f"cells={got['cells']}"
    )


def check_outline_determinism(tmp):
    a, _ = convert_outline(Path(tmp) / "oa")
    b, _ = convert_outline(Path(tmp) / "ob")
    same = a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")
    return record(
        "outline determinism", same, "identical" if same else "two runs differ"
    )


# --------------------------------------------------------------------------
# 4. baseline
# --------------------------------------------------------------------------
def check_baseline(converter, cells):
    base = json.loads((ROOT / "assets" / "baseline.json").read_text(encoding="utf-8"))
    want = base["sample.svg"]
    got = {
        "cells": len(cells),
        "units": converter.stats["units"],
        "images": converter.stats["images"],
        "texts": converter.stats["texts"],
        "groups": converter.stats["groups"],
        "baked_text": converter.stats["baked_text"],
    }
    diff = [f"{k} {got[k]}!={want[k]}" for k in want if got[k] != want[k]]
    return record("baseline", not diff, "; ".join(diff) or f"cells={got['cells']}")


def check_determinism(tmp):
    a, _conv_a = convert_sample(Path(tmp) / "a")
    b, _ = convert_sample(Path(tmp) / "b")

    def strip(s):
        return re.sub(r'modified="[^"]*"', "", s)

    same = strip(a.read_text(encoding="utf-8")) == strip(b.read_text(encoding="utf-8"))
    return record(
        "determinism",
        same,
        f"{a.stat().st_size // 1024} KB, identical apart from the timestamp"
        if same
        else "two runs differ",
    )


# --------------------------------------------------------------------------
def main():
    with tempfile.TemporaryDirectory() as tmp:
        dest, converter = convert_sample(tmp)
        _model, cells, _seq = parse_cells(dest)
        page = ET.parse(ROOT / "assets" / "sample.svg").getroot()  # noqa: S314
        g = page.find(NS + "g")
        page_matrix = mod.parse_transform(g.get("transform") if g is not None else "")
        check_identity()
        check_doc_paths()
        check_cli_docs()
        check_structure(cells)
        check_group_relative(cells)
        check_page_transform(cells, page_matrix)
        check_text_runs(cells)
        check_bg_paint(cells)
        check_baseline(converter, cells)
        check_determinism(tmp)

        o_dest, o_stats = convert_outline(tmp)
        _m, o_cells, o_seq = parse_cells(o_dest)
        check_probe()
        check_outline_structure(o_cells, o_seq)
        check_outline_baseline(o_stats, o_seq)
        check_outline_determinism(tmp)

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += 0 if ok else 1
        print(f"{name:<{width}}  {'PASS' if ok else 'FAIL'}  {detail}")
    print("-" * (width + 8))
    print(f"{len(results) - failed}/{len(results)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
