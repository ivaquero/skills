#!/usr/bin/env python3
# ruff: noqa: PLW1510, S314, UP031
# 门控内联抑制（skill-draft 约定）：解析对象是本包自己生成的本地 .drawio/.vsdx，
# 不是不可信网络输入；包内只用标准库、不能安装 defusedxml，故 S314 不适用。
# 以下规则按各文件实际命中情况精确列出；逐项改写会引入行为漂移。
# 逐项改写会引入行为漂移。完整理由见 references/contract.md 第 8 节。
"""包自检：一致性、真实数据往返、外部规则表对照与反向测试。纯标准库、离线可跑。

python selfcheck.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PKG = Path(__file__).resolve().parents[1]
SCRIPTS = PKG / "scripts"
NS_RE = "{*}"


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PKG,
    )


def check(ok: bool, msg: str, failures: list[str]) -> None:
    print(("  OK   " if ok else "  FAIL ") + msg)
    if not ok:
        failures.append(msg)


def package_consistency(rules: dict, failures: list[str]) -> None:
    print("[1] 包内一致性")
    skill = (PKG / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^name:\s*(\S+)\s*$", skill, re.MULTILINE)
    check(
        m is not None and m.group(1) == PKG.name,
        f"SKILL.md 的 name 等于目录名 {PKG.name}",
        failures,
    )
    front = skill.split("---", 2)[1] if skill.startswith("---") else ""
    keys = re.findall(r"^([A-Za-z_-]+):", front, re.MULTILINE)
    check(
        set(keys)
        <= {"name", "description", "agent_created", "disable-model-invocation"},
        "frontmatter 只含允许的字段",
        failures,
    )
    refs = set(re.findall(r"(?:scripts|references|assets)/[\w.-]+", skill))
    missing = sorted(r for r in refs if not (PKG / r).exists())
    check(not missing, f"SKILL.md 引用的包内文件都存在（缺 {missing}）", failures)
    for field in ("schema", "ppi", "required_parts", "compat", "shapes", "document"):
        check(field in rules, f"规则表含字段 {field}", failures)


def roundtrip(rules: dict, failures: list[str]) -> Path:
    print("[2] 真实数据往返（assets/minimal.drawio）")
    tmp = Path(tempfile.mkdtemp(prefix="drawio2vsdx-selfcheck-"))
    shutil.copy2(PKG / "assets" / "minimal.drawio", tmp / "minimal.drawio")
    r = run("drawio2vsdx.py", "--in-dir", str(tmp), "--out-dir", str(tmp))
    check(
        r.returncode == 0,
        f"转换器退出码 0（{r.stdout.strip().splitlines()[-1:]}）",
        failures,
    )
    vsdx = tmp / "minimal.vsdx"
    check(vsdx.exists(), "产出 minimal.vsdx", failures)
    if not vsdx.exists():
        return tmp
    with zipfile.ZipFile(vsdx) as z:
        names = set(z.namelist())
        raw = z.read("visio/pages/page1.xml")
        pages = z.read("visio/pages/pages.xml").decode("utf-8")
    check(
        all(p in names for p in rules["required_parts"]), "OPC 必需部件齐全", failures
    )
    check("示例案例\n第二行" in raw.decode("utf-8"), "<br> 已归一化为真换行", failures)
    check(
        'V="9.375000"' in pages and 'V="5.208333"' in pages,
        "页面尺寸按 96 PPI 换算 (900x500 px)",
        failures,
    )
    root = ET.fromstring(raw)
    got_names = {
        e.get("NameU") for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Shape"
    }
    expected = set(rules["shapes"]) | {"Connector"}
    check(
        got_names == expected,
        f"产物形状集合与规则表一致（多 {sorted(got_names - expected)} / 少 {sorted(expected - got_names)}）",  # ty: ignore[invalid-argument-type]
        failures,
    )
    r = run("verify_vsdx.py", "--drawio-dir", str(tmp), "--vsdx-dir", str(tmp))
    check(r.returncode == 0, "静态校验器对正常产物退出码 0", failures)
    r = run(
        "vsdx_preview.py", "--in-dir", str(tmp), "--out-html", str(tmp / "preview.html")
    )
    html = (
        (tmp / "preview.html").read_text(encoding="utf-8") if r.returncode == 0 else ""
    )
    check(
        r.returncode == 0 and "<svg" in html and "中心概念" in html,
        "反向渲染器能读回 VSDX 并生成含中文的 SVG",
        failures,
    )
    return tmp


def negative_test(tmp: Path, failures: list[str]) -> None:
    print("[3] 外部规则表对照与反向测试")
    good = tmp / "minimal.vsdx"
    bad = tmp / "broken.vsdx"
    shutil.copy2(tmp / "minimal.drawio", tmp / "broken.drawio")
    with (
        zipfile.ZipFile(good) as src,
        zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for name in src.namelist():
            data = src.read(name)
            if name == "visio/pages/page1.xml":
                root = ET.fromstring(data)
                for shape in root.iter():
                    if shape.get("NameU") != "Document":
                        continue
                    rows = [
                        r for r in shape.iter() if r.tag.rsplit("}", 1)[-1] == "Row"
                    ]
                    for row in rows[2:-1]:  # 把底边压平成基线，破坏 S 形波
                        cy = row.find(NS_RE + 'Cell[@N="Y"]')
                        if cy is not None:
                            cy.set("V", "0.080000")
                data = ET.tostring(root, encoding="utf-8")
            dst.writestr(name, data)
    isolated = tmp / "negative"
    isolated.mkdir(exist_ok=True)
    shutil.copy2(tmp / "broken.drawio", isolated / "broken.drawio")
    shutil.copy2(bad, isolated / "broken.vsdx")
    r = run(
        "verify_vsdx.py", "--drawio-dir", str(isolated), "--vsdx-dir", str(isolated)
    )
    check(r.returncode != 0, "压平 document 底边后校验器报错（退出码非 0）", failures)
    check("不是 S 形波" in r.stdout, "报错定位到 document 几何规则", failures)


def main() -> int:
    rules = json.loads(
        (PKG / "references" / "shape-rules.json").read_text(encoding="utf-8")
    )
    failures: list[str] = []
    package_consistency(rules, failures)
    tmp = roundtrip(rules, failures)
    try:
        negative_test(tmp, failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(
        "\n自检：%s（失败 %d 项）" % ("PASS" if not failures else "FAIL", len(failures))
    )
    for f in failures:
        print("  - " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
