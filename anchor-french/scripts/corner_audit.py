"""一致性守卫：本技能包「文档 ↔ 配置 ↔ 身份」三者对齐，且不外泄他语言内容。

设计原则：**只看本包**。审计不做任何跨技能包比对（每个语言包是自包含单元），
只保证：

1. 身份：包内**恰好一个**配置，且文件名 == `DEFAULT_CONFIG_NAME`；
   `SKILL.md` frontmatter `name` == `SKILL_NAME` == `meta.skill_name`；
   frontmatter 只写 `name` / `description`（外加平台键 `agent_created` /
   `disable-model-invocation`）——展示字段与 `version` 都是启动时的冗余注入；
2. 编辑器 schema：`$schema` 指向包内真实存在的 JSON Schema，且配置满足其 `required`；
3. 文档 ↔ 配置：`SKILL.md` 中出现的取值必须与 JSON 一致（约束 / 时间
   分配 / 词性分组 / 词汇量 / 等级标签 / 提问编排 / 话题机制）；
   版本号反过来——唯一真源是配置 `meta.version`，`SKILL.md` 不许再写一份；
4. 内容纯度：包内不得出现其它语言的配置文件名（防止误拷他语言资产）。

四段**全部跑完才收尾**，一次报全（某一段失败不截断后续段）。失败一律以一条 `✗` 报出，
不抛栈 —— 读不了的文件（非 UTF-8 / 不可读）与不合法的配置 JSON 也算一类发现；
`$schema` 指向远端时**不校验**，改以一条 `○` 说明（**跳过 ≠ 通过**）。

退出码：`0` 干净；`1` 有 finding（文档 ↔ 配置 ↔ 身份对不上）；
`2` **自检自身跑不下去** —— 包布局不对，或配置 / `SKILL.md` 正文读不了。两类必须分得开：
`1` 要你去改包，`2` 要你先修好自检的输入。后者直接收尾，因为配置与正文是其余各段的公共
输入，报一条比连带出一串无意义的假发现好。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from corner_config import (
    DEFAULT_CONFIG_NAME,
    LANG,
    SKILL_MD_NAME,
    SKILL_NAME,
    ConfigError,
    assets_dir,
    read_text_or_error,
    skill_root,
)

# 有序元组：成员判定用它，报错文案里的顺序也用它
FRONTMATTER_KEYS = ("name", "description", "agent_created", "disable-model-invocation")


def frontmatter_keys(md: str) -> list[str]:
    """frontmatter 块的顶层键；没有 frontmatter 时返回空表。

    只认顶格键——折叠块（如 `description: >`）里的缩进行不算键。
    """
    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---", md, re.DOTALL)
    if not match:
        return []
    return re.findall(r"^([A-Za-z][A-Za-z0-9_-]*):", match.group(1), re.MULTILINE)


def config_path() -> Path:
    """本包唯一配置：`<skill>/assets/<DEFAULT_CONFIG_NAME>`。"""
    return assets_dir() / DEFAULT_CONFIG_NAME


def _is_hint_range(value: object) -> bool:
    """`hint_words_range` 形态判定：两元素整数数组，且 1 ≤ 下限 ≤ 上限。

    审计直读原始 JSON（不经过解析层的 `Constraint`），故自带一层形态校验；
    非法值不给 `need()` 喂 —— 倒置的 `5–3` 会因是词汇量区间 `25–35` 的子串
    而假报通过。取值合法性由解析层与 schema 两处也各管一遍。
    """
    if not isinstance(value, list) or len(value) != 2:
        return False
    low, high = value
    if not (isinstance(low, int) and isinstance(high, int)):
        return False
    return 1 <= low <= high


def _read(path: Path, what: str) -> tuple[str, str]:
    """读文本；失败时返回 `("", 原因)`，由调用方折成一条 `✗`。

    审计器不抛栈 —— 「这个文件读不了」本身就是要报出来的一条发现。失败必须
    立即返回：空串参与后续逐条比对会连带出一串假发现。

    返回 `str` 而不是 `str | None` 是刻意的：类型检查器推不出「`err` 非空 ⇒
    文本为 None」，写成可选类型会让下游十几个调用点集体报 `str | None` 不接受。
    代价是失败时文本为空串 —— **调用方必须先看 `err`**。
    """
    try:
        return read_text_or_error(path, what), ""
    except ConfigError as exc:
        return "", str(exc)


def audit_identity(cfg: dict, md: str) -> list[str]:
    """包身份校验：一个配置、文件名正确、SKILL.md 与 meta 的名称一致。

    `md` 是 `main()` 读好的正文。正文读不了时 `main()` 就收尾了，所以这里能假定它
    是有效文本；也因此这一段不再自己去读 —— 各段各读一遍，同一个读失败会被报两次。
    """
    fails: list[str] = []
    found = sorted(p.name for p in assets_dir().glob("*-corner-config.json"))
    if found != [DEFAULT_CONFIG_NAME]:
        fails.append(
            f"[{SKILL_NAME}] assets/ 应恰好包含 {DEFAULT_CONFIG_NAME}，实际: {found or '（无）'}"
        )
        if DEFAULT_CONFIG_NAME not in found:
            return fails  # 缺本包配置，后续检查无意义

    m = re.search(r"^name:\s*(\S+)", md, re.MULTILINE)
    fm_name = m.group(1) if m else "<none>"
    if fm_name != SKILL_NAME:
        fails.append(
            f"[{SKILL_NAME}] {SKILL_MD_NAME} frontmatter name={fm_name!r} != {SKILL_NAME!r}"
        )
    meta_name = cfg["meta"]["skill_name"]
    if meta_name != SKILL_NAME:
        fails.append(f"[{SKILL_NAME}] meta.skill_name={meta_name!r} != {SKILL_NAME!r}")

    extra = [k for k in frontmatter_keys(md) if k not in FRONTMATTER_KEYS]
    if extra:
        allowed = " / ".join(FRONTMATTER_KEYS)
        fails.append(
            f"[{SKILL_NAME}] {SKILL_MD_NAME} frontmatter 多出键 {', '.join(extra)}"
            f"（只允许 {allowed}）；版本号请只留在 meta.version"
        )
    return fails


def _remote_schema_ref(cfg: dict) -> str | None:
    """`$schema` 指向远端 URL 时返回该 URL，否则 `None`（含缺失与本地相对路径）。

    判定只此一处：`audit_schema()` 用它决定跳不跳，`main()` 用它决定打不打那条 `○`。
    """
    ref = cfg.get("$schema")
    return ref if isinstance(ref, str) and "://" in ref else None


def audit_schema(cfg: dict) -> list[str]:
    """编辑器 schema：`$schema` 指向包内真实存在的 JSON Schema，且配置满足其 `required`。

    只做「引用可解析 + 顶层必填键齐备」这一层轻量校验（脚本仅依赖标准库，不引入
    JSON Schema 校验器）；取值域的完整校验交给编辑器的 schema 支持。远端 URL 本包
    不校验，由 `main()` 打一条 `○` 说明 —— 静默 `return []` 会让人把「跳过」读成
    「校验过」。
    """
    tag = f"[{SKILL_NAME}/{LANG}]"
    ref = cfg.get("$schema")
    if not ref:
        return [f"{tag} 配置缺少 $schema，编辑器无法补全 / 校验"]
    if _remote_schema_ref(cfg):
        return []
    target = (config_path().parent / ref).resolve()
    if not target.is_file():
        return [f"{tag} $schema 指向的文件不存在: {ref!r}"]
    text, err = _read(target, "$schema 目标")
    if err:
        return [f"{tag} {err}"]
    try:
        sch = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"{tag} $schema 目标不是合法 JSON: {ref!r}（{exc.msg}）"]
    if not isinstance(sch, dict) or not sch.get("title"):
        return [f"{tag} $schema 目标缺少 title: {ref!r}"]
    missing = [k for k in sch.get("required", []) if k not in cfg]
    if missing:
        return [f"{tag} 配置缺少 schema 要求的顶层键: {', '.join(missing)}"]
    return []


def audit(cfg: dict, md: str) -> list[str]:
    """本包「SKILL.md ↔ 配置」逐项比对；`md` 由 `main()` 读好传进来。"""
    tag = f"[{SKILL_NAME}/{LANG}]"
    fails: list[str] = []

    def need(token: str, what: str) -> None:
        if token not in md:
            fails.append(f"{tag} {what}: 配置值 {token!r} 未出现在 {SKILL_MD_NAME}")

    # 1) 版本号：唯一真源是配置 meta.version（frontmatter 已不再写一份），
    #    没有第二处可对照，就自证形态。
    ver = str(cfg["meta"]["version"])
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        fails.append(f"{tag} meta.version={ver!r} 不是 X.Y.Z 形态")

    # 2) meta
    need(cfg["meta"]["brief_filename"], "meta.brief_filename")

    # 3) constraints
    c = cfg["constraints"]
    need(str(c["max_participants"]), "constraints.max_participants")
    need(str(c["duration_minutes"]), "constraints.duration_minutes")
    need(f"（{c['ask_options_per_question']}）", "constraints.ask_options_per_question")
    hint = c.get("hint_words_range")
    if _is_hint_range(hint):
        need(f"{hint[0]}–{hint[1]}", "constraints.hint_words_range")
    else:
        fails.append(
            f"{tag} constraints.hint_words_range 应为 [下限, 上限]"
            f"（1 ≤ 下限 ≤ 上限），实际 {hint!r}"
        )

    # 4) time_allocation：分钟 + 占比都必须在表里出现，且自身守恒
    minutes_sum = sum(s["minutes"] for s in cfg["time_allocation"])
    pct_sum = sum(s["pct"] for s in cfg["time_allocation"])
    if minutes_sum != c["duration_minutes"]:
        want = c["duration_minutes"]
        fails.append(
            f"{tag} time_allocation 分钟合计 {minutes_sum} != duration_minutes {want}"
        )
    if abs(pct_sum - 1.0) > 0.02:
        fails.append(f"{tag} time_allocation pct 合计 {pct_sum:.3f} 偏离 1.0")
    for slot in cfg["time_allocation"]:
        pct_txt = f"{slot['pct'] * 100:.1f}%"
        stem = slot["label"].split("（")[0]  # 「讨论（含 30 题 · 3 部分）」→「讨论」
        row = re.search(
            rf"^\|\s*{re.escape(stem)}[^|]*\|\s*{re.escape(pct_txt)}\s*\|\s*{slot['minutes']}\s*\|",
            md,
            re.MULTILINE,
        )
        if not row:
            fails.append(
                f"{tag} 「环节时间分配与规模备注」缺行或数值不符: {stem} / {pct_txt} / {slot['minutes']} min"
            )

    # 5) style
    st = cfg["style"]
    need(st["output_path_template"], "style.output_path_template")
    for g in st["pos_groups"]:
        need(g, "style.pos_groups")
    for v in st["phase_labels"].values():
        need(v, "style.phase_labels")

    # 6) vocab_targets
    for lv, (lo, hi) in cfg["vocab_targets"].items():
        if f"{lo}–{hi}" not in md:
            fails.append(
                f"{tag} vocab_targets[{lv}] = {lo}–{hi} 未出现在 {SKILL_MD_NAME}"
            )

    # 7) exam：等级标签
    for lv in cfg["exam"]["levels"]:
        need(f"({lv})", "exam.levels")

    # 8) question_plan：ask=false 的题不得被描述为「询问」
    for q in cfg["question_plan"]:
        row = re.search(rf"^\|\s*{q['id']}\s*\|([^\n]*)$", md, re.MULTILINE)
        if not row:
            fails.append(f"{tag} 「交互契约」表缺 {q['id']} 行")
            continue
        line = row.group(1)
        if q["ask"] is False and "不询问" not in line:
            fails.append(f"{tag} {q['id']} ask=false，但「交互契约」表未标注「不询问」")
        if q["ask"] is True and "不询问" in line:
            fails.append(f"{tag} {q['id']} ask=true，但「交互契约」表标注了「不询问」")

    # 9) 话题机制：题库 + 随机 + 自定义
    pool = cfg.get("topic_pool", [])
    if not pool:
        fails.append(f"{tag} topic_pool 为空，Q4 无题可选")
    else:
        if "topic_pool" not in md:
            fails.append(f"{tag} 配置了 topic_pool，但 {SKILL_MD_NAME} 未提及该键")
        if "题库" not in md:
            fails.append(
                f"{tag} 配置了 topic_pool，但 {SKILL_MD_NAME} 未出现「题库」说明"
            )
    for q in cfg["question_plan"]:
        if q.get("allow_random"):
            label = cfg["style"]["i18n"].get("topic_random_label", "随机")
            if label not in md:
                fails.append(
                    f"{tag} {q['id']} 开启 allow_random，但 {SKILL_MD_NAME} 未出现"
                    f"随机项文案 {label!r}"
                )
        if q.get("allow_custom") and "自定义" not in md:
            fails.append(
                f"{tag} {q['id']} 开启 allow_custom，但 {SKILL_MD_NAME} 未出现「自定义」说明"
            )

    # 10) 内容纯度：包内不得引用其它语言的配置文件名
    aliens: list[tuple[str, str]] = []
    for f in sorted(skill_root().rglob("*")):
        if not f.is_file() or f.suffix not in {".md", ".py", ".json"}:
            continue
        if ".rumdl_cache" in f.as_posix():
            continue
        text, err = _read(f, "包内文件")
        if err:
            fails.append(f"{tag} {err}")
            continue
        rel = f.relative_to(skill_root()).as_posix()
        aliens.extend(
            (rel, name)
            for name in sorted(set(re.findall(r"[\w-]+-corner-config\.json", text)))
            if name != DEFAULT_CONFIG_NAME
        )
    for rel, name in aliens:
        fails.append(f"{tag} {rel} 引用了其它语言的配置 {name!r}")

    return fails


def _report(
    fails: list[str], notes: list[str] | None = None, *, code: int | None = None
) -> int:
    """统一收尾：逐条打 `✗`，再逐条打 `○` 说明；返回退出码。

    `✗` 是发现的不一致，决定退出码；`○` 是「这一段没验」的告知，不影响退出码 ——
    两种行必须看得出区别，否则「跳过」会被读成「通过」。
    `code=None` 时按「有 fails 即 1」推；`2` 留给「自检自身跑不下去」，那一档
    连表头也换掉，免得被读成「只是有 1 处不一致」。
    """
    if code is None:
        code = 1 if fails else 0
    if code == 2:
        head = "自检无法完成"
    elif fails:
        head = f"{len(fails)} 处不一致"
    else:
        head = "OK"
    print(f"{SKILL_NAME} ({LANG}): {head}")
    for line in fails:
        print("  ✗", line)
    for line in notes or []:
        print("  ○", line)
    return code


def main() -> int:
    """三段闸门：schema（引用 + 必填顶层键）→ 身份 → 文档 ↔ 配置 / 内容纯度。

    **三段全部跑完再收尾**，一次报全所有不一致（某段失败不截断后续段）—— 否则使用者
    要经历「改一处 → 重跑 → 又冒出一类新问题」。退出码见模块 docstring：
    `1` 是包内容的 finding，`2` 是自检自己跑不下去。
    """
    # 以下五处都是「自检自身跑不下去」（退出码 2），不是包内容的 finding（1）：
    # 配置与正文是各段的公共输入，读不到就无从审起。
    try:
        cfg_path = config_path()
    except ConfigError as exc:
        return _report([str(exc)], code=2)
    if not cfg_path.is_file():
        return _report([f"缺少 {cfg_path.as_posix()}"], code=2)
    try:
        cfg = json.loads(read_text_or_error(cfg_path, "配置文件"))
    except ConfigError as exc:
        return _report([str(exc)], code=2)
    except json.JSONDecodeError as exc:
        return _report([f"配置不是合法 JSON（{exc.msg}）"], code=2)
    if not isinstance(cfg, dict):
        return _report(
            [f"配置顶层必须是 JSON 对象，实际为 {type(cfg).__name__}"], code=2
        )
    md, err = _read(skill_root() / SKILL_MD_NAME, "技能包正文")
    if err:
        return _report([f"[{SKILL_NAME}] {err}"], code=2)
    fails: list[str] = []
    fails.extend(audit_schema(cfg))
    fails.extend(audit_identity(cfg, md))
    fails.extend(audit(cfg, md))
    notes: list[str] = []
    remote = _remote_schema_ref(cfg)
    if remote:
        notes.append(
            f"[{SKILL_NAME}/{LANG}] 未校验 schema 引用：$schema 指向远端 {remote}，"
            "本包只校验包内相对引用"
        )
    return _report(fails, notes)


if __name__ == "__main__":
    sys.exit(main())
