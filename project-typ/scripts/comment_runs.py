"""批量规整「被 read() 引用的代码」里的注释，并做终检。

三个子命令：

    python comment_runs.py scan   <路径...>          # 扫连续单行注释块，出 map 骨架
    python comment_runs.py apply  <map.json> [--apply] [--backup DIR]
    python comment_runs.py verify <backup_dir>       # 七项终检

`<路径>` 给 `.typ` 时会自动解析里面的 `read("...")` 引用；给 `.py` 就直接用它。

写回一律**字节级**并保留原换行符 —— 这些文件处在 `read()` 里，改注释就是改课件版式。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

REF_RE = re.compile(r'read\(\s*"([^"]+\.py)"\s*\)')
CJK = re.compile(r"[\u4e00-\u9fff]")


# ---------------------------------------------------------------- 基础工具


def resolve(paths: list[str]) -> list[str]:
    """把 .typ / .py 混合输入统一成被引用的 .py 列表（去重保序）；支持 `*` 通配。"""
    out: list[str] = []
    files: list[str] = []
    for raw in paths:
        if any(c in raw for c in "*?["):
            files += [p.as_posix() for p in sorted(Path(".").glob(raw))]
        else:
            files.append(raw)
    for raw in files:
        p = Path(raw)
        if p.suffix == ".typ":
            hits = REF_RE.findall(p.read_text(encoding="utf-8"))
        else:
            hits = [raw]
        for h in hits:
            if h not in out and Path(h).exists():
                out.append(h)
    return out


def comment_pos(line: str) -> int:
    """行内 `#` 的下标；跳过字符串字面量。无注释返回 -1。"""
    q = None
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "#":
            return i
        i += 1
    return -1


def newline_of(raw: str) -> str:
    return "\r\n" if "\r\n" in raw else "\n"


def lines_of(raw: str) -> list[str]:
    """行内容列表（含行尾 \\r，已统一保留），末位空串代表结尾换行。"""
    return raw.replace("\r\n", "\n").split("\n")


def nobreak(line: str) -> str:
    return line.removesuffix("\r")


def blocks(path: Path) -> list[tuple[int, int, list[str]]]:
    """返回 [(起, 止, 原文行列表)]，1-based 闭区间；只含 >=2 行的纯注释连续块。"""
    lines = lines_of(path.read_bytes().decode("utf-8"))
    in_doc = False
    flags: list[bool] = []
    for ln in lines:
        f = in_doc
        if ln.count('"""') % 2 or ln.count("'''") % 2:
            in_doc = not in_doc
        flags.append(f)

    out: list[tuple[int, int, list[str]]] = []
    i = 0
    n = len(lines)
    while i < n:
        if flags[i] or not lines[i].strip().startswith("#"):
            i += 1
            continue
        j = i
        while j + 1 < n and not flags[j + 1] and lines[j + 1].strip().startswith("#"):
            j += 1
        if j - i + 1 >= 2:
            out.append((i + 1, j + 1, [nobreak(x) for x in lines[i : j + 1]]))
        i = j + 1
    return out


def block_key(lines: list[str]) -> str:
    """块的查表键：各行 strip() 后以 \\n 连接。"""
    return "\n".join(x.strip() for x in lines)


def strip_code(text: str) -> list[str]:
    """去掉整行注释、行尾注释、docstring 行后的非空行（终检用）。"""
    out: list[str] = []
    in_doc = False
    for ln in text.replace("\r\n", "\n").split("\n"):
        if in_doc:
            if ln.count('"""') % 2 or ln.count("'''") % 2:
                in_doc = False
            continue
        s = ln.strip()
        if s.startswith(('"""', "'''")):
            if (ln.count('"""') % 2 or ln.count("'''") % 2) and s.count('"""') != 2:
                in_doc = True
            continue
        if s.startswith("#"):
            continue
        i = comment_pos(ln)
        if i >= 0:
            ln = ln[:i].rstrip()
        if ln.strip():
            out.append(ln)
    return out


def display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


# ---------------------------------------------------------------- 子命令


def cmd_scan(paths: list[str]) -> int:
    files = resolve(paths)
    skeleton: dict[str, dict[str, list[str]]] = {}
    total = 0
    for rel in files:
        bs = blocks(Path(rel))
        if not bs:
            continue
        total += len(bs)
        print(f"### {rel}  ({len(bs)} 块)")
        for a, b, ls in bs:
            widest = max(map(display_width, ls))
            print(f"  L{a}-{b}  ({len(ls)}行, 最宽 {widest} 列)  {ls[0].strip()[:64]}")
            skeleton[block_key(ls)] = []
    out = Path("comment-runs-map.json")
    out.write_text(
        json.dumps(
            {
                "_说明": "key = 块内各行 strip() 后的文本以 \\n 连接；"
                "value = 替换后的行列表（不含缩进）。"
                '空列表会被忽略，整块删除请填 ["DELETE"]。',
                "files": files,
                "map": skeleton,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(files)} 个被引用文件；连续注释块 {total} 个 -> 骨架 {out}")
    return 0


def cmd_apply(map_path: str, do_apply: bool, backup: str) -> int:
    spec = json.loads(Path(map_path).read_text(encoding="utf-8"))
    table: dict[str, list[str]] = spec["map"]
    if not spec.get("files"):
        raise SystemExit("map.json 缺少 files 字段（应为 scan 时记录的 .py 列表）")
    targets = spec["files"]

    bdir = Path(backup)
    miss: list[str] = []
    changed = 0
    for rel in targets:
        p = Path(rel)
        bs = blocks(p)
        if not bs:
            continue
        raw = p.read_bytes().decode("utf-8")
        nl = newline_of(raw)
        lines = lines_of(raw)
        notes = []
        for a, b, blk in reversed(bs):
            key = block_key(blk)
            if key not in table:
                miss.append(f"{rel} L{a}-{b}: {key.splitlines()[0][:50]}")
                continue
            rep = table[key]
            if rep == ["DELETE"]:
                rep = []
            indent = blk[0][: len(blk[0]) - len(blk[0].lstrip())]
            lines[a - 1 : b] = [indent + r for r in rep]
            head = (rep or ["(删)"])[0][:56]
            notes.append(f"  L{a}-{b} ({b - a + 1}行) -> {len(rep)}行 | {head}")
        if not notes:
            continue
        out = "\n".join(lines)
        # lines_of 还原：原结尾换行已在列表末位
        out = out if raw.replace("\r\n", "\n").endswith("\n") else out.rstrip("\n")
        out = out.replace("\n", nl)
        if out == raw:
            continue
        changed += 1
        print(f"### {rel}")
        for x in notes:
            print(x)
        if do_apply:
            if bdir:
                bdir.mkdir(parents=True, exist_ok=True)
                bak = bdir / p.name
                if not bak.exists():
                    shutil.copy2(p, bak)
                (bdir / f"{p.name}.sha256").write_text(
                    hashlib.sha256(raw.encode()).hexdigest(), encoding="utf-8"
                )
                idx = bdir / "_index.json"
                m = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else {}
                m[p.name] = p.as_posix()
                idx.write_text(
                    json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            if p.read_bytes().decode("utf-8") != raw:
                raise SystemExit(f"[并发改动] {rel} 内容已变，中止")
            p.write_bytes(out.encode("utf-8"))

    print(
        f"\n{'已写入' if do_apply else '干跑'}：{changed} 个文件；未覆盖 {len(miss)} 块"
    )
    for m in miss[:20]:
        print("  未覆盖", m)
    return 1 if miss else 0


def ruff(rel: str, src: str, mode: str) -> str:
    args = (
        [
            "ruff",
            "check",
            "--no-fix",
            "--no-fix-only",
            "--output-format",
            "concise",
            "--stdin-filename",
            rel,
            "-",
        ]
        if mode == "check"
        else ["ruff", "format", "--stdin-filename", rel, "-"]
    )
    # stdin 喂 CRLF 会让 ruff 插空行 -> 先归一为 LF
    r = subprocess.run(
        args,
        input=src.replace("\r\n", "\n"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return r.stdout


def count_codes(out: str) -> dict[str, int]:
    res: dict[str, int] = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) > 1 and ":" in ln:
            res[parts[1]] = res.get(parts[1], 0) + 1
    return res


def cmd_verify(backup: str) -> int:
    bdir = Path(backup)
    olds = sorted(p for p in bdir.glob("*") if p.suffix == ".py")
    if not olds:
        raise SystemExit(f"{backup} 里没有备份的 .py")
    # 优先用 apply 写下的 _index.json（name -> 仓库内相对路径）；
    # 否则回退到全仓 rglob —— 必须排除备份目录，否则会拿备份去比备份。
    idx_f = bdir / "_index.json"
    if idx_f.exists():
        here = {
            Path(v).name: Path(v)
            for v in json.loads(idx_f.read_text(encoding="utf-8")).values()
        }
    else:
        skip = {".git", ".workbuddy", ".tmp", "node_modules", "__pycache__"}
        here = {}
        for p in Path(".").rglob("*.py"):
            if skip & set(p.parts):
                continue
            here[p.name] = p
        print("[提示] 没有 _index.json，用全仓同名文件匹配（可能有歧义）")

    runs_left: list[str] = []
    bad_nl: list[str] = []
    long_lines: list[str] = []
    fmt_diff: list[str] = []
    check_new: list[str] = []
    code_diff: list[str] = []

    for bak in olds:
        cur_p = here.get(bak.name)
        if cur_p is None:
            print(f"[找不到当前文件] {bak.name}")
            continue
        rel = cur_p.as_posix()
        cur_b, old_b = cur_p.read_bytes(), bak.read_bytes()
        cur, old = cur_b.decode("utf-8"), old_b.decode("utf-8")

        if blocks(cur_p):
            runs_left.append(f"{rel}: {len(blocks(cur_p))} 块")

        def style(b: bytes) -> str:
            crlf = b.count(b"\r\n")
            lf = b.count(b"\n") - crlf
            return "CRLF" if crlf and not lf else "LF" if lf and not crlf else "MIXED"

        if style(cur_b) != style(old_b):
            bad_nl.append(f"{rel}: {style(old_b)} -> {style(cur_b)}")
        for n, ln in enumerate(cur.replace("\r\n", "\n").split("\n"), 1):
            if display_width(ln) > 88:
                long_lines.append(f"{rel}:{n} ({display_width(ln)} 列)")
        if (
            ruff(rel, cur, "format").splitlines()
            != cur.replace("\r\n", "\n").splitlines()
        ):
            fmt_diff.append(rel)
        a, b = (
            count_codes(ruff(rel, old, "check")),
            count_codes(ruff(rel, cur, "check")),
        )
        added = {k: b[k] - a.get(k, 0) for k in b if b[k] > a.get(k, 0)}
        if added:
            check_new.append(f"{rel}: {added}")
        if strip_code(cur) != strip_code(old):
            code_diff.append(rel)

    def show(title: str, items: list[str]) -> None:
        print(f"{'OK ' if not items else 'XX '} {title}: {len(items)}")
        for x in items[:12]:
            print(f"      {x}")

    show("残留连续注释块", runs_left)
    show("换行类型被改变", bad_nl)
    show("超宽行 >88", long_lines)
    show("ruff format 内容级差异", fmt_diff)
    show("ruff check 新增问题", check_new)
    show("剥注释后代码行变化", code_diff)
    hard = runs_left or bad_nl or fmt_diff or check_new
    print(f"\n硬门禁全过: {not hard}")
    print("超宽行与代码行变化需人工确认；渲染比对见 SKILL.md「编译与校验」")
    return 0 if not hard else 1


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "scan":
        return cmd_scan(rest)
    if cmd == "apply":
        do = "--apply" in rest
        rest = [x for x in rest if x != "--apply"]
        bdir = rest[rest.index("--backup") + 1] if "--backup" in rest else ""
        if "--backup" in rest:
            i = rest.index("--backup")
            rest = rest[:i] + rest[i + 2 :]
        return cmd_apply(rest[0], do, bdir)
    if cmd == "verify":
        return cmd_verify(rest[0])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
