#!/usr/bin/env python3
"""typ2drawio 包内一键自检：包内一致性、样例往返、外部规则表对照。

只用标准库、离线运行、不写包内文件（产物落在临时目录）。
退出码 0 表示三项全过。
"""

import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PKG = Path(__file__).resolve().parent.parent
SKILL = PKG / "SKILL.md"
LAYOUT = PKG / "references/layout.md"
RULES = PKG / "references/mxgraph-rules.json"
ENTRY = PKG / "scripts/typ2drawio.py"
VERIFY = PKG / "scripts/verify_drawio.py"
ASSETS = PKG / "assets"
SAMPLE = ASSETS / "v01-示例讲次.typ"
EXPECTED_NAME = "typ2drawio"
FIXED_STAMP = "2026-01-01T00:00:00"

CODE_CONST = re.compile(r"^([A-Z_0-9]+) = ([0-9.]+)$", re.MULTILINE)
# 列宽可能被 markdown 格式化工具对齐，不能假设单元格之间只有一个空格。
DOC_CONST = re.compile(r"^\|\s*`([A-Z_0-9]+)`\s*\|\s*([0-9.]+)\s*\|", re.MULTILINE)
DOC_REF = re.compile(r"(?:references|scripts)/[\w.-]+\.(?:md|py|json)")
NAME_FIELD = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)

MUST_APPEAR = ("通道索引", "欧氏距离", "内容包含", "支持")
PREREQ_SENTENCE = (
    "《上一讲章名》相关概念（理解、运用）；"
    "Python 与 NumPy 基础（熟悉、运用）；OpenCV-Python 开发环境（运用）"
)
MUST_NOT_APPEAR = (
    "教学目标",
    "本讲小结",
    "课后作业",
    "上节回顾",
    "读这张表",
    "ex1.1",
    "sum_",
    "frac",
    "#code",
    "…",
)


def check_consistency() -> list[str]:
    """文档与代码对照：name、引用路径、几何常量、规则表可解析。"""
    problems: list[str] = []
    skill_text = SKILL.read_text(encoding="utf-8")
    m = NAME_FIELD.search(skill_text)
    if m is None or m.group(1) != EXPECTED_NAME or PKG.name != EXPECTED_NAME:
        problems.append(
            f"目录名 / SKILL.md 的 name 不一致: {PKG.name} vs {m.group(1) if m else '缺失'}"
        )
    problems.extend(
        f"SKILL.md 引用了不存在的文件: {rel}"
        for rel in sorted(set(DOC_REF.findall(skill_text)))
        if not (PKG / rel).exists()
    )
    if not SAMPLE.exists():
        problems.append(f"样例文件不存在: {SAMPLE.name}")
    code = dict(CODE_CONST.findall(ENTRY.read_text(encoding="utf-8")))
    doc = dict(DOC_CONST.findall(LAYOUT.read_text(encoding="utf-8")))
    if not doc:
        problems.append("layout.md 里没有解析到常量表")
    problems.extend(
        f"常量 {key} 文档值 {value} 与代码值 {code.get(key)} 不一致"
        for key, value in doc.items()
        if code.get(key) != value
    )
    try:
        json.loads(RULES.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        problems.append(f"规则表无法解析: {exc}")
    return problems


def run_entry(out_dir: Path, extra: list[str]) -> None:
    cmd = [
        sys.executable,
        str(ENTRY),
        "--typ-dir",
        str(ASSETS),
        "--out-dir",
        str(out_dir),
        "--modified",
        FIXED_STAMP,
        *extra,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def write_docx(path: Path, paragraphs: list[str]) -> None:
    """写一个只含 word/document.xml 的最小 docx，够本包读取正文即可。"""
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


def check_prereq_node(path: Path) -> list[str]:
    """前置知识节点：唯一、绿色、文案取到章名、有指向中心的「先修」连线。"""
    problems: list[str] = []
    cells = list(ET.fromstring(path.read_text(encoding="utf-8")).iter("mxCell"))  # noqa: S314
    green = [c for c in cells if "fontColor=#008000" in (c.get("style") or "")]
    if len(green) != 1:
        return [f"前置知识节点应唯一，实际 {len(green)} 个"]
    value = green[0].get("value") or ""
    if "《上一讲章名》" not in value:
        problems.append(f"前置知识未取到章名: {value!r}")
    if "Python 与 NumPy 基础" not in value:
        problems.append("前置知识第二行丢了同栏条目")
    if not any(
        (c.get("value") or "") == "先修"
        and c.get("source") == green[0].get("id")
        and c.get("target") == "center"
        for c in cells
        if c.get("edge") == "1"
    ):
        problems.append("缺少从前置知识指向中心的「先修」连线")
    return problems


def check_roundtrip(tmp: Path) -> tuple[list[str], Path | None]:
    """真实样例跑两次：确定性、知识点往返、噪声过滤、docx 编号对齐、产物校验。"""
    problems: list[str] = []
    first, second = tmp / "first", tmp / "second"
    try:
        run_entry(first, [])
        run_entry(second, [])
    except (subprocess.CalledProcessError, OSError) as exc:
        return [f"入口脚本运行失败: {exc}"], None
    generated = sorted(first.glob("*.drawio"))
    if len(generated) != 1:
        problems.append(f"样例应产出 1 个 .drawio，实际 {len(generated)} 个")
        return problems, None
    path = generated[0]
    twin = second / path.name
    if not twin.exists() or path.read_bytes() != twin.read_bytes():
        problems.append("固定时间戳后两次生成结果不一致（往返不确定）")

    text = path.read_text(encoding="utf-8")
    cells = list(ET.fromstring(text).iter("mxCell"))  # noqa: S314 - 本包自产 XML，非外部输入
    joined = " ".join(cell.get("value") or "" for cell in cells)
    problems.extend(
        f"知识点往返丢失: {word}" for word in MUST_APPEAR if word not in joined
    )
    problems.extend(f"噪声未过滤: {word}" for word in MUST_NOT_APPEAR if word in joined)
    centre = next((cell for cell in cells if cell.get("id") == "center"), None)
    if centre is None:
        problems.append("缺少 id=center 的中心节点")
    elif centre.get("value") != "示例讲次":
        problems.append(f"中心节点应只写章名，实际 {centre.get('value')!r}")

    docx_dir = tmp / "docx"
    docx_dir.mkdir()
    doc = docx_dir / "图像处理与识别-07-1.docx"
    write_docx(doc, ["学习先决知识", "知识点（学习水平）", PREREQ_SENTENCE])
    aligned = tmp / "aligned"
    try:
        run_entry(aligned, ["--docx-dir", str(docx_dir)])
    except (subprocess.CalledProcessError, OSError) as exc:
        problems.append(f"docx 编号对齐运行失败: {exc}")
    else:
        names = [p.name for p in sorted(aligned.glob("*.drawio"))]
        if not names or not names[0].startswith("07-1-"):
            problems.append(f"docx 编号未生效: {names}")
        else:
            problems.extend(check_prereq_node(aligned / names[0]))
            try:
                subprocess.run(
                    [
                        sys.executable,
                        str(VERIFY),
                        "--out-dir",
                        str(aligned),
                        "--docx-dir",
                        str(docx_dir),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (subprocess.CalledProcessError, OSError) as exc:
                problems.append(f"带前置知识校验的产物校验失败: {exc}")

    bare_dir = tmp / "docx-bare"
    bare_dir.mkdir()
    write_docx(
        bare_dir / "图像处理与识别-07-1.docx", ["学习先决知识", "知识点（学习水平）"]
    )
    bare = tmp / "bare"
    try:
        run_entry(bare, ["--docx-dir", str(bare_dir)])
    except (subprocess.CalledProcessError, OSError) as exc:
        problems.append(f"缺先决栏的降级运行失败: {exc}")
    else:
        hits = [
            p.name
            for p in bare.glob("*.drawio")
            if "fontColor=#008000" in p.read_text(encoding="utf-8")
        ]
        if hits:
            problems.append(f"缺先决栏时不应生成前置知识节点: {hits}")

    try:
        subprocess.run(
            [sys.executable, str(VERIFY), "--out-dir", str(first)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        problems.append(f"产物校验脚本失败: {exc}")
    return problems, path


def check_baseline(path: Path) -> list[str]:
    """把产物逐项对照 references/mxgraph-rules.json 这条外部基线。"""
    problems: list[str] = []
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    raw = path.read_text(encoding="utf-8")
    root = ET.fromstring(raw)  # noqa: S314 - 本包自产 XML，非外部输入
    if root.tag != rules["root_tag"]:
        problems.append(f"根元素应为 {rules['root_tag']}，实际 {root.tag}")
    problems.extend(
        f"缺少 {tag} 元素"
        for tag in (rules["diagram_tag"], rules["model_tag"], rules["root_cell_tag"])
        if root.find(f".//{tag}") is None
    )
    cells = list(root.iter("mxCell"))
    layers = {
        c.get("id"): c.get("parent")
        for c in cells
        if c.get("vertex") is None and c.get("edge") is None
    }
    problems.extend(
        f"层单元 {layer['id']} 的 parent 不符合规则表"
        for layer in rules["required_layer_cells"]
        if layers.get(layer["id"]) != layer["parent"]
    )
    verts = [c for c in cells if c.get("vertex") == "1"]
    edges = [c for c in cells if c.get("edge") == "1"]
    problems.extend(
        f"节点 {c.get('id')} 缺少属性 {attr} 或 parent 不对"
        for c in verts
        for attr in rules["required_vertex_attrs"]
        if c.get(attr) is None
        or (attr == "parent" and c.get("parent") != rules["vertex_parent"])
    )
    problems.extend(
        f"边 {c.get('id')} 缺少属性 {attr}"
        for c in edges
        for attr in rules["required_edge_attrs"]
        if c.get(attr) is None
    )
    problems.extend(
        f"基线要求的样式片段缺失: {frag}"
        for frag in rules["baseline_style_fragments"]
        if frag not in raw
    )
    problems.extend(
        f"规则表要求的关系标签缺失: {label}"
        for label in rules["required_relations"]
        if label not in raw
    )
    if len(verts) < rules["min_vertices"] or len(edges) < rules["min_edges"]:
        problems.append(f"低于规则表密度下限: {len(verts)} 节点 / {len(edges)} 连线")
    return problems


def main() -> int:
    checks = [
        ("包内一致性", check_consistency()),
    ]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        problems, produced = check_roundtrip(tmp)
        checks.append(("样例往返", problems))
        checks.append(
            ("外部规则表对照", check_baseline(produced) if produced else ["无产物可比"])
        )
    failed = 0
    for i, (label, issues) in enumerate(checks, start=1):
        if issues:
            failed += 1
            print(f"[{i}/3] {label}: FAIL")
            for issue in issues:
                print(f"       - {issue}")
        else:
            print(f"[{i}/3] {label}: PASS")
    print(f"自检结果: {len(checks) - failed}/{len(checks)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
