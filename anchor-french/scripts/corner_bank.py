"""题库闸门：问题先落 JSON，md 再从 JSON 取用并回查（不调用任何 LLM）。

题库文件固定放 `assets/questions/<slug>.json`，一个话题一份；schema 是同目录的
`question-bank.schema.v1.json`（本模块只做结构 / 取值校验，不依赖第三方库）。

子命令（`python scripts/corner_bank.py ...`）：

- `check [PATH ...]`：校验题库结构、取值、与话题池对齐；缺省校验全部题库。
- `dump BANK [--part N]`：按 md 语法输出题目块（题文 + 等级 + 提示词）。
- `verify BANK MD`：回查 md 的题目与题库逐条一致（题文 / 等级 / 提示词 / 顺序）。

退出码：`0` 通过；`1` 有发现；`2` 用法错或文件读不了。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from corner_config import LANG, SKILL_NAME, assets_dir, load_config

__all__ = ["BankError", "dump_questions", "load_bank", "validate_bank", "verify_md"]

QUESTIONS_DIRNAME = "questions"

# 与 schema 同步的取值域（schema 是给人看的，这里是给闸门用的）。
KINDS = frozenset({"corner_script", "drill"})
RANK = {"B1": 1, "B2": 2, "C1": 3, "C2": 4}
LEVEL_BANDS = re.compile(r"[A-C][12]")
LEVEL_TAG = re.compile(
    r"[（(]\s*((?:DELF|DALF|DELE)\s+[A-C][12](?:\s*(?:→|->)\s*[A-C][12])?)\s*[）)]"
)
CJK = re.compile(
    r"[\u2e80-\u2fff\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
QUESTION_LINE = re.compile(r"^(\d+)\.\s+(.*?)\s*$")
QUOTE_LINE = re.compile(r"^>\s+(.*?)\s*$")
FENCE = re.compile(r"^\s*(?:```|~~~)")

# 文档里「问题总览」小节的标题：其后是纯题文索引，不该参与逐条比对。
INDEX_HEADING = {
    "fr": re.compile(r"^##+\s+.*(?:Aperçu|Résumé).*questions?\s*$", re.IGNORECASE),
    "es": re.compile(r"^##+\s+.*(?:Resumen|Índice).*preguntas?\s*$", re.IGNORECASE),
}

META_KEYS = (
    "skill_name",
    "lang",
    "kind",
    "slug",
    "topic_id",
    "topic_in_pool",
    "topic_label",
    "title",
    "source_doc",
    "source_title",
    "extracted_on",
    "exam_family",
    "levels",
    "level_span",
    "difficulty_span",
    "grammar",
    "hint_label",
    "hint_count_range",
    "question_count",
    "part_count",
)
QUESTION_KEYS = (
    "id",
    "src_no",
    "phase",
    "part",
    "text",
    "level",
    "difficulty",
    "hints",
)
DIFFICULTY_KEYS = ("cefr", "source")


class BankError(Exception):
    """题库读不了 / 解析不了；由 `main()` 折成退出码 2。"""


# ─────────────────────────── 读取 ───────────────────────────


def bank_paths() -> list[Path]:
    """`assets/questions/` 下全部题库，按文件名排序。"""
    d = assets_dir() / QUESTIONS_DIRNAME
    return sorted(d.glob("*.json")) if d.is_dir() else []


def load_bank(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise BankError(f"题库不存在: {p}")
    text = p.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise BankError(f"{p.name} 不是合法 JSON: {e}") from e
    if not isinstance(doc, dict):
        raise BankError(f"{p.name} 顶层应为对象")
    doc["_text"] = text  # 供「整篇不得有中文」与路径提示复用
    return doc


# ─────────────────────────── 校验 ───────────────────────────


def _norm(text: str) -> str:
    """比对用归一：去零宽、压空白、去行尾硬换行符。"""
    s = text.replace("\u200b", "").rstrip("\\").strip()
    return re.sub(r"\s+", " ", s)


def _pool_of(cfg) -> dict[str, str]:
    """话题池 id → label；`cfg` 可以是 `SkillConfig`，也可以是裸配置 dict。

    审计那一段手上只有裸 dict。
    """
    if cfg is None:
        return {}
    pool = getattr(cfg, "topic_pool", None)
    if pool is not None:
        return {o.id: o.label for o in pool}
    if isinstance(cfg, dict):
        return {
            str(o.get("id")): str(o.get("label"))
            for o in cfg.get("topic_pool", [])
            if isinstance(o, dict)
        }
    return {}


def _validate_meta(meta: dict, path: Path, doc: dict, cfg, fails: list[str]) -> None:
    tag = path.name
    missing = [k for k in META_KEYS if k not in meta]
    if missing:
        fails.append(f"{tag} meta 缺键: {', '.join(missing)}")
    if meta.get("skill_name") != SKILL_NAME:
        fails.append(
            f"{tag} meta.skill_name={meta.get('skill_name')!r} 应为 {SKILL_NAME!r}"
        )
    if meta.get("lang") != LANG:
        fails.append(f"{tag} meta.lang={meta.get('lang')!r} 应为 {LANG!r}")
    if meta.get("kind") not in KINDS:
        fails.append(f"{tag} meta.kind={meta.get('kind')!r} 不在 {sorted(KINDS)}")
    if meta.get("slug") != path.stem:
        fails.append(f"{tag} meta.slug={meta.get('slug')!r} 应与文件名一致")
    if not DATE.fullmatch(str(meta.get("extracted_on", ""))):
        fails.append(f"{tag} meta.extracted_on 应为 YYYY-MM-DD")
    if meta.get("topic_in_pool") is not (meta.get("topic_id") is not None):
        fails.append(f"{tag} meta.topic_in_pool 与 topic_id 是否为 null 不一致")
    fails.extend(
        f"{tag} meta.{k} 为空"
        for k in ("topic_label", "title", "source_doc")
        if not str(meta.get(k) or "").strip()
    )
    if not meta.get("levels") and meta.get("kind") == "corner_script":
        fails.append(f"{tag} corner_script 应有 meta.levels")
    # 话题池对齐：入池话题的 id 必须在配置里，label 也应能对上配置那一项的文案。
    tid = meta.get("topic_id")
    if tid is not None and cfg is not None:
        pool = _pool_of(cfg)
        if tid not in pool:
            fails.append(f"{tag} topic_id={tid!r} 不在配置 topic_pool")
        elif str(meta.get("topic_label")) not in pool[tid]:
            fails.append(
                f"{tag} topic_label={meta.get('topic_label')!r} 与配置 label "
                f"{pool[tid]!r} 对不上（应为其中本语言那一段）"
            )
    # 整篇不得出现中文：题库只存目标语言。
    hits = CJK.findall(doc.get("_text", ""))
    if hits:
        fails.append(f"{tag} 含 {len(hits)} 个中日韩字符/全角标点，题库应为纯目标语言")


def _validate_questions(bank: dict, path: Path, fails: list[str]) -> None:
    tag = path.name
    qs = bank.get("questions")
    if not isinstance(qs, list) or not qs:
        fails.append(f"{tag} questions 应为非空数组")
        return
    phases = {p.get("id") for p in bank.get("phases", [])}
    parts = {p.get("index") for p in bank.get("parts", [])}
    meta = bank.get("meta", {})
    hint_label = meta.get("hint_label")
    rng = meta.get("hint_count_range")
    families = {
        f.strip() for f in str(meta.get("exam_family", "")).split("/") if f.strip()
    }
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict):
            fails.append(f"{tag} 第 {i} 题不是对象")
            continue
        missing = [k for k in QUESTION_KEYS if k not in q]
        if missing:
            fails.append(f"{tag} 第 {i} 题缺键: {', '.join(missing)}")
            continue
        if q["id"] != i:
            fails.append(f"{tag} 第 {i} 题 id={q['id']} 不连续")
        if not str(q["text"]).strip():
            fails.append(f"{tag} 第 {i} 题正文为空")
        if q["phase"] not in phases:
            fails.append(f"{tag} 第 {i} 题 phase={q['phase']!r} 不在 phases")
        if q["part"] is not None and q["part"] not in parts:
            fails.append(f"{tag} 第 {i} 题 part={q['part']} 不在 parts")
        if q["level"] is not None and not LEVEL_TAG.fullmatch(f"({q['level']})"):
            fails.append(f"{tag} 第 {i} 题 level={q['level']!r} 不是等级标签")
        elif q["level"] and families and q["level"].split()[0] not in families:
            fails.append(
                f"{tag} 第 {i} 题 level={q['level']!r} 的考试家族不在 "
                f"meta.exam_family={meta.get('exam_family')!r} 内"
            )
        d = q["difficulty"]
        if not isinstance(d, dict):
            fails.append(f"{tag} 第 {i} 题 difficulty 缺失")
        else:
            miss_d = [k for k in DIFFICULTY_KEYS if k not in d]
            if miss_d:
                fails.append(f"{tag} 第 {i} 题 difficulty 缺键: {', '.join(miss_d)}")
            else:
                cefr = d["cefr"]
                if cefr not in RANK:
                    fails.append(f"{tag} 第 {i} 题 difficulty 的 cefr 非法: {cefr!r}")
                elif q["level"]:
                    bands = LEVEL_BANDS.findall(str(q["level"]))
                    if bands and bands[0] != cefr:
                        fails.append(
                            f"{tag} 第 {i} 题 level={q['level']!r} 与 cefr={cefr} 不符"
                        )
                if d["source"] == "tag" and not q["level"]:
                    fails.append(f"{tag} 第 {i} 题 source=tag 但无等级标注")
                if d["source"] == "section" and q["level"]:
                    fails.append(f"{tag} 第 {i} 题 source=section 却有等级标注")
        if not isinstance(q["hints"], list):
            fails.append(f"{tag} 第 {i} 题 hints 应为数组")
        elif q["hints"] and not hint_label:
            fails.append(f"{tag} 第 {i} 题有提示词，但 meta.hint_label 为空")
        elif (
            q["hints"]
            and isinstance(rng, list)
            and len(rng) == 2
            and not rng[0] <= len(q["hints"]) <= rng[1]
        ):
            fails.append(
                f"{tag} 第 {i} 题提示词 {len(q['hints'])} 条，越界 {rng[0]}–{rng[1]}"
            )
    ids = {q.get("id") for q in qs if isinstance(q, dict)}
    for p in bank.get("parts", []):
        ghost = [i for i in p.get("question_ids", []) if i not in ids]
        if ghost:
            fails.append(f"{tag} part {p.get('index')} 引用不存在的题号 {ghost}")
    counts = {pid: sum(1 for q in qs if q.get("phase") == pid) for pid in phases}
    fails.extend(
        f"{tag} phase {p.get('id')} 的 question_count 与实题数不符"
        for p in bank.get("phases", [])
        if p.get("question_count") != counts.get(p.get("id"))
    )


def validate_bank(path: str | Path, cfg=None) -> list[str]:
    """校验单个题库；返回不一致条目（空列表即通过）。"""
    p = Path(path)
    try:
        bank = load_bank(p)
    except BankError as e:
        return [str(e)]
    fails: list[str] = []
    missing = [k for k in ("meta", "phases", "parts", "questions") if k not in bank]
    fails.extend(f"{p.name} 缺顶层键 {k}" for k in missing)
    if fails:
        return fails
    _validate_meta(bank["meta"], p, bank, cfg, fails)
    _validate_questions(bank, p, fails)
    meta = bank["meta"]
    if meta.get("question_count") != len(bank["questions"]):
        fails.append(f"{p.name} meta.question_count 与题数不符")
    if meta.get("part_count") != len(bank["parts"]):
        fails.append(f"{p.name} meta.part_count 与 parts 数不符")
    return fails


# ─────────────────────────── 输出题目块 ───────────────────────────


def _question_lines(q: dict, hint_label: str | None) -> list[str]:
    """一条题按 md 语法展开：题文 + 等级标注 + 下一行提示词。"""
    num = f"{q['id']}."
    tag = f" ({q['level']})" if q.get("level") else ""
    out = [f"{num} {q['text']}{tag}\\"]
    if q.get("hints") and hint_label:
        pad = " " * (len(num) + 1)
        out.append(f"{pad}{hint_label} : {' · '.join(q['hints'])}")
    return out


def dump_questions(bank: dict, part: int | None = None) -> str:
    """把题库（可只取一部分）渲染成可直接粘进主持脚本的 md 片段。"""
    meta = bank["meta"]
    hint_label = meta.get("hint_label")
    label_of_part = {
        p["index"]: p["heading"].lstrip("#").strip() for p in bank["parts"]
    }
    out: list[str] = []
    current_phase = None
    for q in bank["questions"]:
        if part is not None and q["part"] != part:
            continue
        if q["phase"] != current_phase:
            current_phase = q["phase"]
            if q["part"] is not None:
                out.append("### " + label_of_part.get(q["part"], q["phase"]))
            else:
                out.append("## " + q["phase"])
        out.extend(_question_lines(q, hint_label))
    return "\n".join(out) + "\n"


# ─────────────────────────── 回查 md ───────────────────────────


def _parse_md_questions(md: str, hint_label: str | None) -> list[dict]:
    """抽出 md 里的题目条目：编号 / 题文 / 等级 / 提示词。

    跳过代码块与「问题总览」索引段；破冰题允许写成引用块（无编号）。
    """
    stop = INDEX_HEADING.get(LANG)
    items: list[dict] = []
    lines = md.splitlines()
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if FENCE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        if stop and stop.match(line):
            break
        numbered = None if line.startswith((" ", "\t")) else QUESTION_LINE.match(line)
        quoted = QUOTE_LINE.match(line)
        body = None
        if numbered:
            body = numbered.group(2)
        elif quoted and LEVEL_TAG.search(quoted.group(1)):
            body = quoted.group(1)
        if body is None:
            i += 1
            continue
        tag = LEVEL_TAG.search(body)
        level = re.sub(r"\s*(?:→|->)\s*", " → ", tag.group(1)) if tag else None
        text = LEVEL_TAG.sub("", body).replace("`", "")
        hints: list[str] = []
        j = i + 1
        while j < len(lines) and lines[j].strip():
            nxt = lines[j]
            if not nxt.startswith((" ", "\t")) or QUESTION_LINE.match(nxt.lstrip()):
                break
            chunk = nxt.strip()
            if hint_label and chunk.startswith(hint_label):
                chunk = chunk.split(":", 1)[-1] if ":" in chunk else chunk
                hints.extend(h.strip() for h in chunk.split("·") if h.strip())
            elif hints:
                hints.extend(h.strip() for h in chunk.split("·") if h.strip())
            j += 1
        items.append(
            {
                "no": int(numbered.group(1)) if numbered else None,
                "text": _norm(text),
                "level": level,
                "hints": hints,
            }
        )
        i = j
    return items


def verify_md(bank: dict, md_path: str | Path) -> list[str]:
    """md 的题目必须逐条来自题库：题文、等级、提示词、顺序全对齐。"""
    p = Path(md_path)
    if not p.exists():
        return [f"md 不存在: {p}"]
    md = p.read_text(encoding="utf-8")
    meta = bank["meta"]
    items = _parse_md_questions(md, meta.get("hint_label"))
    qs = bank["questions"]
    fails: list[str] = []
    if len(items) != len(qs):
        fails.append(
            f"{p.name} 有 {len(items)} 条题目，"
            f"题库 {bank['meta']['slug']} 有 {len(qs)} 条"
            f"（md 的问题必须与题库一致，多出或缺失都不行）"
        )
    for idx, (item, q) in enumerate(zip(items, qs), 1):
        where = f"{p.name} 第 {idx} 条"
        if item["text"] != _norm(q["text"]):
            fails.append(
                f"{where}题文与题库不符\n"
                f"    md  : {item['text']}\n"
                f"    题库: {_norm(q['text'])}"
            )
        if q["level"] and item["level"] != q["level"]:
            fails.append(f"{where}等级标注={item['level']!r}，题库为 {q['level']!r}")
        if q["hints"] and item["hints"] != q["hints"]:
            fails.append(
                f"{where}提示词与题库不符\n    md  : {' · '.join(item['hints'])}\n"
                f"    题库: {' · '.join(q['hints'])}"
            )
        prev_no = items[idx - 2]["no"] if idx > 1 else None
        if item["no"] is not None and prev_no is not None and item["no"] != prev_no + 1:
            fails.append(f"{where}编号 {item['no']} 与上一条不连续")
    numbers = [it["no"] for it in items if it["no"] is not None]
    if numbers and numbers[0] != 1:
        fails.append(f"{p.name} 题目编号应从 1 起，实际从 {numbers[0]} 起")
    return fails


# ─────────────────────────── CLI ───────────────────────────


def _report(fails: list[str], ok_line: str, *, code: int | None = None) -> int:
    if code is None:
        code = 1 if fails else 0
    if code == 2:
        head = "无法完成"
    elif fails:
        head = f"{len(fails)} 处不一致"
    else:
        head = ok_line
    print(f"[{SKILL_NAME}/{LANG}] {head}")
    for f in fails:
        print(f"  ✗ {f}")
    return code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "check"
    rest = args[1:]

    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    try:
        cfg = load_config()
    except Exception as e:  # noqa: BLE001 —— 配置坏了就没法比对话题池，直接报
        return _report([f"配置读不了，无法校验题库: {e}"], "", code=2)

    if cmd == "check":
        targets = [Path(a) for a in rest] or bank_paths()
        if not targets:
            return _report([f"assets/{QUESTIONS_DIRNAME}/ 下没有题库"], "", code=2)
        fails: list[str] = []
        for t in targets:
            fails.extend(validate_bank(t, cfg))
        return _report(fails, f"题库 {len(targets)} 份全部通过")

    if cmd == "dump":
        if not rest:
            return _report(["用法: dump BANK [--part N]"], "", code=2)
        bank = load_bank(rest[0])
        part = None
        if "--part" in rest:
            idx = rest.index("--part")
            raw = rest[idx + 1] if idx + 1 < len(rest) else ""
            try:
                part = int(raw)
            except ValueError:
                return _report(["--part 需要一个整数"], "", code=2)
        sys.stdout.write(dump_questions(bank, part))
        return 0

    if cmd == "verify":
        if len(rest) != 2:
            return _report(["用法: verify BANK MD"], "", code=2)
        bank = load_bank(rest[0])
        fails = verify_md(bank, rest[1])
        return _report(
            fails, f"{Path(rest[1]).name} 与题库 {bank['meta']['slug']} 逐条一致"
        )

    return _report([f"未知子命令: {cmd}"], "", code=2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BankError as exc:
        raise SystemExit(_report([str(exc)], "", code=2)) from exc
