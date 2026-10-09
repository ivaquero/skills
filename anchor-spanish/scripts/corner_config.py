"""本包解析层：JSON → `SkillConfig` + 查询 API + 身份常量（仅标准库）。

这一层只做「读 JSON → 校验 → 归一化为 dataclass」，不硬编码任何面向人的文案，
也不调用 LLM。身份常量（`SKILL_NAME` / `LANG` / `DEFAULT_CONFIG_NAME`）是本包与
同族其它语言包**唯一允许的差异**。

退出码（`python scripts/corner_config.py [配置路径]`，演示入口）：
`0` 加载并打印成功；`2` 包布局不对或配置读不了。作为库被 `corner_audit.py` /
`corner_skill.py` 导入时本模块不自行决定退出码 —— 错误一律以 `ConfigError` 抛出，
由调用方翻译成上面同一套约定。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

# AskUserQuestion 规范要求 header ≤ 12 字符（与宿主 UI 渲染一致）；
# 提为常量便于将来调整一处即可。
HEADER_MAX_LEN = 12

# ─────────────────── 本技能包身份（语言专属） ───────────────────
# 本模块只服务一种语言，不做多语言分派。以下三项是本技能包与同族其它语言包
# **唯一允许的差异**，也是开发期守卫比对时的归一化锚点。
SKILL_NAME = "anchor-spanish"  # 必须等于包目录名与 SKILL.md frontmatter 的 name
LANG = "es"  # 语言键，仅用于日志与错误信息
DEFAULT_CONFIG_NAME = "es-corner-config.json"  # 本包资产的唯一配置文件名

__all__ = [
    "DEFAULT_CONFIG_NAME",
    "HEADER_MAX_LEN",
    "LANG",
    "RANDOM_SENTINEL",
    "SKILL_NAME",
    "ConfigError",
    "Constraint",
    "GrammarPoint",
    "Option",
    "QuestionSpec",
    "Scale",
    "SkillConfig",
    "TimeSlot",
    "assets_dir",
    "load_config",
    "read_text_or_error",
    "skill_root",
]


class ConfigError(Exception):
    """JSON 结构或取值非法时抛出，由调用方（skill 主体）捕获处理。"""


# 话题「随机选一个」哨兵：作为选项 value 出现时，由解析层从题库随机抽取替换。
RANDOM_SENTINEL = "__random__"

# ─────────────────── 技能包标准布局与路径解析 ───────────────────
# 标准布局（skill-creator 约定）：
#   <skill>/SKILL.md         工作流正文
#   <skill>/scripts/         本包脚本（本模块等）
#   <skill>/references/      按需加载的规范文档（corner-architecture.md）
#   <skill>/assets/          非上下文资产（DEFAULT_CONFIG_NAME）
# 本包只认这一种布局：脚本必须位于 <skill>/scripts/、与 SKILL.md 同级。

SKILL_MD_NAME = "SKILL.md"
SCRIPTS_DIRNAME = "scripts"
ASSETS_DIRNAME = "assets"


def skill_root() -> Path:
    """返回本技能包根目录（`<skill>/scripts/corner_*.py` → `<skill>`）。

    布局不符合标准（脚本不在 `<skill>/scripts/` 下，或同级缺 `SKILL.md`）
    直接抛 ConfigError —— 宁可失败也不静默降级到错误目录。
    """
    here = Path(__file__).resolve().parent
    root = here.parent
    if here.name != SCRIPTS_DIRNAME or not (root / SKILL_MD_NAME).is_file():
        raise ConfigError(
            f"非标准技能包布局：{here} 不是 <skill>/scripts/。"
            f"请把脚本放在 {SKILL_NAME}/scripts/ 下，并与 {SKILL_MD_NAME} 同级。"
        )
    return root


def assets_dir() -> Path:
    """返回资产目录 `<skill>/assets/`（唯一配置 DEFAULT_CONFIG_NAME 所在处）。"""
    return skill_root() / ASSETS_DIRNAME


def read_text_or_error(path: Path, what: str) -> str:
    """以 UTF-8 读文本；读不了时统一抛 `ConfigError`。

    包内所有读文件都走这里，把失败面收成一种异常：裸 `read_text` 会抛 `OSError`
    （目录 / 权限 / 文件已删）或 `UnicodeDecodeError`（非 UTF-8 字节），调用方按
    「任何失败都是 ConfigError」的契约只捕捉后者就会漏接。

    两个 `except` 分开写：既为给出可区分的报错，也避开一个格式化器陷阱 ——
    `target-version = "py314"` 下，**不带** `as` 的 `except (A, B):` 会被改写成
    PEP 758 的裸形式 `except A, B:`，那种语法只在 3.14+ 能解析。
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{what}读取失败: {path}（{exc}）") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{what}不是 UTF-8 文本: {path}（{exc}）") from exc


# ───────────────────────────── 值对象 ─────────────────────────────


def _require_id_label(d: dict, kind: str) -> None:
    """值对象通用校验：dict 必须含 id 与 label。"""
    if "id" not in d or "label" not in d:
        raise ConfigError(f"{kind} 缺少 id/label: {d!r}")


@dataclass(frozen=True)
class Option:
    """通用可选项（值/标签/备注/描述）。"""

    id: str
    label: str
    note: str | None = None
    desc: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Option:
        _require_id_label(d, "Option")
        return cls(
            id=str(d["id"]),
            label=str(d["label"]),
            note=d.get("note"),
            desc=d.get("desc"),
        )


@dataclass(frozen=True)
class GrammarPoint:
    """一级语法点 + 其下二级条目。"""

    id: str
    label: str
    children: tuple[Option, ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> GrammarPoint:
        _require_id_label(d, "GrammarPoint")
        kids = tuple(Option.from_dict(c) for c in d.get("children", []))
        return cls(id=str(d["id"]), label=str(d["label"]), children=kids)


@dataclass(frozen=True)
class Scale:
    """规模选项，含人数区间与总时长（供校验/呈现）。"""

    id: str
    label: str
    note: str | None = None
    people_min: int = 0
    people_max: int = 0
    minutes: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> Scale:
        _require_id_label(d, "Scale")
        return cls(
            id=str(d["id"]),
            label=str(d["label"]),
            note=d.get("note"),
            people_min=int(d.get("people_min", 0)),
            people_max=int(d.get("people_max", 0)),
            minutes=int(d.get("minutes", 0)),
        )


@dataclass(frozen=True)
class Constraint:
    max_participants: int
    duration_minutes: int
    max_grammar_primary: int
    max_topics: int
    level_range: tuple[str, ...]
    level_mixed_label: str
    ask_options_per_question: int
    questions_max_per_call: int
    hint_words_range: tuple[int, ...]

    @classmethod
    def from_dict(cls, d: dict) -> Constraint:
        required = {
            "max_participants",
            "duration_minutes",
            "max_grammar_primary",
            "max_topics",
            "level_range",
            "level_mixed_label",
            "ask_options_per_question",
            "questions_max_per_call",
            "hint_words_range",
        }
        missing = required - set(d)
        if missing:
            raise ConfigError(f"constraints 缺失字段: {sorted(missing)}")
        hint = tuple(int(x) for x in d["hint_words_range"])
        if len(hint) != 2 or not (1 <= hint[0] <= hint[1]):
            raise ConfigError(
                f"constraints.hint_words_range 应为 [下限, 上限]（1 ≤ 下限 ≤ 上限），"
                f"实际 {list(hint)}"
            )
        return cls(
            max_participants=int(d["max_participants"]),
            duration_minutes=int(d["duration_minutes"]),
            max_grammar_primary=int(d["max_grammar_primary"]),
            max_topics=int(d["max_topics"]),
            level_range=tuple(str(x) for x in d["level_range"]),
            level_mixed_label=str(d["level_mixed_label"]),
            ask_options_per_question=int(d["ask_options_per_question"]),
            questions_max_per_call=int(d["questions_max_per_call"]),
            hint_words_range=hint,
        )


@dataclass(frozen=True)
class TimeSlot:
    phase: str
    label: str
    pct: float
    minutes: int


@dataclass(frozen=True)
class QuestionSpec:
    id: str
    key: str
    order: int
    ask: bool
    type: str  # "multi" | "single"
    title: str
    header: str
    source: str
    max_choices: int | None
    depends_on: str | None
    allow_custom: bool
    allow_random: bool = False  # 选项末尾追加「随机选一个」哨兵项
    mode: str = "select"  # "select" | "auto_recommend"


# ─────────────────────────── SkillConfig ───────────────────────────


@dataclass(frozen=True)
class SkillConfig:
    meta: dict
    constraints: Constraint
    grammar_points: tuple[GrammarPoint, ...]
    participant_levels: tuple[Option, ...]
    scales: tuple[Scale, ...]
    topic_dimensions: tuple[Option, ...]
    topic_pool: tuple[Option, ...]  # Q4 话题题库（可选题库 / 随机 / 自定义）
    time_allocation: tuple[TimeSlot, ...]
    vocab_targets: dict
    exam: dict  # 本包的评分/等级体系（levels / rules / rubric），值由 assets 提供
    question_plan: tuple[QuestionSpec, ...]
    style: dict

    @cached_property
    def _grammar_index(self) -> dict:
        """一级语法点 id -> GrammarPoint 索引（首次访问构建，后续 O(1) 复用）。

        改用 cached_property 的动机：secondary_grammar_options() 在循环内
        反复 _grammar_index.get(pid)，原本作为 @property 每次调用都重建整个
        dict。语法点条目很少，但语义上「重建 vs 缓存」的区别更清晰：派生数据
        不属于 SkillConfig 的不可变状态，独立缓存更符合 frozen dataclass 语义。
        """
        return {g.id: g for g in self.grammar_points}

    # ─────────────── 选项查询 API ───────────────

    def primary_grammar_options(self) -> list[Option]:
        """Q1：一级语法点选项（最多 max_grammar_primary 个）。"""
        return [Option(g.id, g.label) for g in self.grammar_points]

    def secondary_grammar_options(self, primary_ids: list[str]) -> list[Option]:
        """Q2：依据 Q1 所选一级点，汇集其下二级条目（去重）。

        全部命中失败时回退到**全部**二级条目，避免「Q1 全为自定义输入」导致
        Q2 无选项，进而让 ask_next() 返回空与 is_intake_done() 互相矛盾的
        死锁（详见 build_ask_payload 与 skill 主体约束）。
        """
        valid = [pid for pid in (primary_ids or []) if pid in self._grammar_index]
        if not valid:
            return [c for gp in self.grammar_points for c in gp.children]
        out: list[Option] = []
        seen = set()
        for pid in valid:
            for child in self._grammar_index[pid].children:
                if child.id not in seen:
                    seen.add(child.id)
                    out.append(child)
        return out

    def level_options(self) -> list[Option]:
        return list(self.participant_levels)

    def scale_options(self) -> list[Option]:
        """规模选项。把人数区间与总时长并入 note，便于用户直观选择。"""
        out: list[Option] = []
        for s in self.scales:
            cap = f"{s.people_min}–{s.people_max} 人 / {s.minutes} 分钟"
            note = f"{cap}｜{s.note}" if s.note else cap
            out.append(Option(s.id, s.label, note))
        return out

    def topic_options(self) -> list[Option]:
        """话题**维度**选项（区别于 `topic_pool` 的具体话题题库）。

        - 作为公共 API：配置作者可直接取 `topic_dimensions` 数据（如「旅行 / 工作 /
          文化」等维度，而非具体话题），用于自定义展示或校验；
        - 作为选项来源：question_plan 中某题设 `source: "topic_dimensions"` 时，
          `_resolve_options` 会分派到本方法（已在 `_VALID_SOURCES` 登记）。

        与 `topic_pool_options()` 互补：后者是 Q4 用的具体话题题库，前者是维度层。
        本包 assets 保留了 `topic_dimensions` 数据，故本方法恒可用。
        """
        return list(self.topic_dimensions)

    def topic_pool_options(self) -> list[Option]:
        """Q4 题库选项（topic_pool）。随机哨兵项由 _resolve_options 按需追加。"""
        return list(self.topic_pool)

    def random_topics(
        self, count: int = 1, exclude: list[str] | None = None
    ) -> list[str]:
        """从题库随机取 count 个话题标签（不重复，尽量避开 exclude 中的标签）。"""
        if not self.topic_pool:
            return []
        banned = set(exclude or ())
        candidates = [o for o in self.topic_pool if o.label not in banned]
        if not candidates:
            candidates = list(self.topic_pool)
        k = max(1, min(count, len(candidates)))
        return [o.label for o in random.sample(candidates, k)]

    def expand_choices(self, choices: list[str]) -> list[str]:
        """把含随机哨兵的选择解析为最终话题文本列表（去重保序）。

        - RANDOM_SENTINEL  -> 从题库随机抽取（避开同批已明确选中的话题）
        - 题库中的 id      -> 转成对应 label
        - 其它文本         -> 视为用户自定义话题，原样保留
        """
        picked: list[str] = []
        n_random = 0
        for c in choices:
            if c == RANDOM_SENTINEL:
                n_random += 1
            else:
                picked.append(self._label_of_topic(c))
        if n_random:
            picked.extend(self.random_topics(n_random, exclude=picked))
        return list(dict.fromkeys(picked))

    def vocab_range(self, level: str) -> tuple[int, int]:
        if level not in self.vocab_targets:
            raise ConfigError(f"未知水平 '{level}'，可选: {list(self.vocab_targets)}")
        lo, hi = self.vocab_targets[level]
        return int(lo), int(hi)

    def time_slots(self) -> list[TimeSlot]:
        return list(self.time_allocation)

    # ─────────────── 流程 / 依赖 API ───────────────

    def next_question(self, state: dict) -> QuestionSpec | None:
        """依据已答 state（{语义key: [choice_ids]}），返回下一个待问问题；
        全部完成（含 auto_recommend）则返回 None。

        注意：state 以 question_plan 的 `key`（语义键）为索引，而非 `id`，
        与 submit() 写入的键保持一致。depends_on 也引用语义键。
        """
        for q in sorted(self.question_plan, key=lambda x: x.order):
            if q.ask is False:
                # auto_recommend 仅在依赖已答且自身未生成时视为「待产出」
                if q.depends_on and q.depends_on not in state:
                    continue
                if q.key in state:
                    continue
                return q
            if q.key not in state:
                return q
        return None

    def build_ask_payload(self, state: dict) -> list[dict]:
        """构造当前「下一题」的 AskUserQuestion 兼容负载（含 ≤5 选项分组）。

        返回结构（每元素对应一个子问题，最多 questions_max_per_call 个）：
            {
              "id": str,                 # 主体据此回填 state
              "question": str,
              "header": str,
              "multiSelect": bool,
              "options": [{"label": str, "description": str}, ...]
            }
        注意：当选项数 > ask_options_per_question 时，按 ask_options_per_question 个一组拆成多个
        子问题，子问题共享同一父 key（父 id 形如 "Q1#1"、"Q1#2"），由主体
        合并回 Q1 的 state 条目。
        """
        q = self.next_question(state)
        if q is None:
            return []
        options = self._resolve_options(q, state)
        # 兜底：next_question 返回了非空问题，但解析器算不出选项。这通常意味着
        # 运行时输入全为非法自定义、或 source/depends_on 配置错误。返回 [] 会被
        # 宿主读作「intake 完成」，与 is_intake_done() 互相矛盾 → 死锁；显式抛错
        # 让宿主立刻知道卡在哪一题、如何恢复。
        if not options:
            raise ConfigError(
                f"{q.id}（{q.title}）无可选项；"
                "通常是 Q1/前置题选到了全自定义输入，或 question_plan.source 配置错误。"
            )

        chunks = self._chunk(options, self.constraints.ask_options_per_question)
        # 分组数超出单次提问上限会导致尾部分组被丢弃、其选项永远不可达且无任何提示。
        # 与其静默截断，不如让配置作者立刻看见问题。
        if len(chunks) > self.constraints.questions_max_per_call:
            raise ConfigError(
                f"{q.id} 共 {len(options)} 个选项需拆成 {len(chunks)} 组，"
                f"超过 questions_max_per_call={self.constraints.questions_max_per_call}；"
                "尾部选项将无法触达。请提高 questions_max_per_call 或减少选项数。"
            )
        payload = []
        for i, chunk in enumerate(chunks):
            sub_id = q.id if len(chunks) == 1 else f"{q.id}#{i + 1}"
            group_hint = "" if len(chunks) == 1 else f"（{i + 1}/{len(chunks)}）"
            payload.append(
                {
                    "id": sub_id,
                    "parent_key": q.key,
                    "question": f"{q.title}{group_hint}",
                    "header": q.header[:HEADER_MAX_LEN],
                    "multiSelect": (q.type == "multi"),
                    "maxChoices": q.max_choices,
                    "allowCustom": q.allow_custom,
                    "options": [
                        {
                            "value": o.id,
                            "label": o.label,
                            "description": o.desc or o.note or "",
                        }
                        for o in chunk
                    ],
                }
            )
        return payload

    def auto_recommend_choices(self, q: QuestionSpec, state: dict) -> list[str]:
        """为 auto_recommend 节点（ask=false）生成自动推荐值，作为「无需询问」的填充。

        与 `ask_next()` 共用：节点被 `next_question()` 以 `ask is False` 触发后，
        skill 主体调用本方法填值并继续。返回值形态与用户正常选择完全一致：

        - topic_pool / allow_random 来源：走随机话题推荐（`random_topics`），
          返回可读话题标签（与正常流程里 submit→expand_choices 落库的标签形态一致）；
        - 其它来源：取 `_resolve_options` 解析出的前 N 个候选（返回选项 id）。

        N 的选择：单选题固定 1 个；多选题取该题自身 `max_choices`，回退全局
        `max_topics`。单选/多选依据 `q.type` 判定，而非依赖 `max_choices` 是否设置，
        避免单选项漏设 max_choices 时误取多个。
        """
        is_single = q.type == "single"
        if q.source == "topic_pool" or q.allow_random:
            n = 1 if is_single else (q.max_choices or self.constraints.max_topics)
            return self.random_topics(count=n)
        n = 1 if is_single else (q.max_choices or self.constraints.max_topics)
        return [o.id for o in self._resolve_options(q, state)[:n]]

    def _label_of_topic(self, tid: str) -> str:
        """话题 id -> 题库 label；不在题库中则视为自定义话题，原样返回。"""
        for o in self.topic_pool:
            if o.id == tid:
                return o.label
        return tid

    def _label_of_option_id(self, oid: str) -> str:
        """按 id 在全部一级语法点下查找二级条目标签（供简报渲染）。"""
        for gp in self.grammar_points:
            for child in gp.children:
                if child.id == oid:
                    return child.label
        return oid

    def _resolve_answers(self, answers: dict) -> dict:
        """把原始 answers（{语义key: [choice_id]}）解析为可读标签字典，供简报渲染。"""
        levels = answers.get("level", [])
        level = levels[0] if levels else "mixed"
        grammar_primary = answers.get("grammar_primary", [])
        grammar_secondary = answers.get("grammar_secondary", [])
        topics = [self._label_of_topic(t) for t in answers.get("topics", [])]
        scale = answers.get("scale", [])
        vocab = self.vocab_range(level if level in self.vocab_targets else "mixed")
        return {
            "level_label": self.label_of_level(level),
            "grammar_primary_labels": [
                self._label_of_grammar(g) for g in grammar_primary
            ],
            "grammar_secondary_labels": [
                self._label_of_option_id(g) for g in grammar_secondary
            ],
            "topics": topics,
            "scale_label": self.label_of_scale(scale[0]) if scale else "—",
            "vocab": vocab,
            "phases": list(self.style.get("phase_labels", {}).values()),
            "pos_groups": self.style.get("pos_groups", []),
            "exam_levels": self.exam.get("levels", []),
            "exam_rules": self.exam.get("rules", ""),
            "exam_rubric": self.exam.get("rubric", []),
        }

    def build_markdown_brief(self, answers: dict) -> str:
        """把 intake 收集的参数整理为清晰的 Markdown 简报，交给 skill 主体读取。

        返回 Markdown 字符串（不调用任何 LLM / openai）。所有展示文本均取自
        `style.i18n`，本模块不硬编码任何语言文案。

        简报由 skill 主体读取后，据此产出完整主持脚本，把「参数收集」与
        「内容生成」彻底解耦。
        """
        i18n = self.style.get("i18n", {}) or {}
        t = i18n.get

        r = self._resolve_answers(answers)
        L: list[str] = []
        L.extend((t("brief_title", "# Corner 主持简报（brief）"), ""))
        intro = t("brief_intro")
        if intro:
            L.extend((intro, ""))
        L.append(t("constraints_header", "## 基础约束"))
        form = t("constraint_form")
        if form:
            L.append(form)
        lang = t("constraint_output_lang")
        if lang:
            L.append(lang)
        exam_levels = "、".join(r["exam_levels"]) or "—"
        L.extend(
            (
                f"- {t('exam_label', '评分体系标签')}：{exam_levels}",
                "",
                t("collected_header", "## 已收集参数"),
            )
        )
        L.append(t("level_line", "- **水平**：{v}").format(v=r["level_label"]))
        gp1 = ", ".join(r["grammar_primary_labels"]) or "—"
        L.append(t("grammar1_line", "- **一级语法点**：{v}").format(v=gp1))
        gp2 = ", ".join(r["grammar_secondary_labels"]) or "—"
        L.append(t("grammar2_line", "- **二级语法点**：{v}").format(v=gp2))
        sujets = ", ".join(r["topics"]) or "—"
        L.extend(
            (
                t("topics_line", "- **话题**：{v}").format(v=sujets),
                t("scale_line", "- **规模**：{v}").format(v=r["scale_label"]),
            )
        )
        L.append(
            t("vocab_line", "- **词汇量目标**：{lo}–{hi} 词").format(
                lo=r["vocab"][0], hi=r["vocab"][1]
            )
        )
        L.extend(
            (
                t("phases_line", "- **阶段**：{v}").format(
                    v=", ".join(r["phases"]) or "—"
                ),
                t("pos_line", "- **词性分组**：{v}").format(
                    v=", ".join(r["pos_groups"]) or "—"
                ),
                "",
                t("output_req_header", "## 产出要求（交给 skill 主体）"),
            )
        )
        ori = t("output_req_intro")
        if ori:
            L.append(ori)
        structure = t("structure_line")
        if structure:
            L.append(f"- {structure}")
        vocab_note = t("vocab_note_line")
        if vocab_note:
            L.append(f"- {vocab_note}")
        rules = r.get("exam_rules", "")
        if rules:
            # 前缀以全角「：」结尾，其后不再补空格（避免渲染出「： 」）
            L.append(f"{t('exam_rules_prefix', '- **评分标注规则**：')}{rules}")
        L.extend(
            t("rubric_line", "- **{level}**：{abilities}（{feature}）").format(
                level=rb.get("level", ""),
                abilities=rb.get("abilities", ""),
                feature=rb.get("feature", ""),
            )
            for rb in r.get("exam_rubric", [])
        )
        no_hr = t("no_hr_rule")
        if no_hr:
            L.append(no_hr)
        return "\n".join(L) + "\n"

    # ─────────────── 内部工具 ───────────────

    def _resolve_options(self, q: QuestionSpec, state: dict) -> list[Option]:
        if q.source == "grammar_points(keys)":
            return self.primary_grammar_options()
        if q.source == "grammar_points[chosen].children":
            dep = q.depends_on
            chosen = state.get(dep, []) if dep else []
            return self.secondary_grammar_options(chosen)
        if q.source == "participant_levels":
            return self.level_options()
        if q.source == "scales":
            return self.scale_options()
        if q.source == "topic_dimensions":
            return self.topic_options()
        if q.source == "topic_pool":
            opts = self.topic_pool_options()
            if q.allow_random:
                i18n = self.style.get("i18n", {}) or {}
                # 随机项置顶：题库超出单屏上限会拆成分组展示，置顶可保证首组即可见
                opts.insert(
                    0,
                    Option(
                        RANDOM_SENTINEL,
                        i18n.get("topic_random_label", "随机选一个"),
                        i18n.get("topic_random_desc", ""),
                    ),
                )
            return opts
        # 未知 source 不再静默返回空列表：那会被 build_ask_payload 当作「无选项」
        # 进而被 ask_next() 判定为 intake 完成，最终导出缺字段的简报。
        raise ConfigError(
            f"question_plan[{q.id}].source = {q.source!r} 无法解析选项；"
            f"可选: {sorted(_VALID_SOURCES)}"
        )

    @staticmethod
    def _chunk(items: list, size: int) -> list[list]:
        size = max(1, size)
        return [items[i : i + size] for i in range(0, len(items), size)]

    def _label_of_grammar(self, gid: str) -> str:
        gp = self._grammar_index.get(gid)
        return gp.label if gp else gid

    def label_of_level(self, lid: str) -> str:
        for o in self.participant_levels:
            if o.id == lid:
                return o.label
        return lid

    def label_of_scale(self, sid: str) -> str:
        for o in self.scales:
            if o.id == sid:
                return o.label
        return sid


# ─────────────────────────── 加载器 ───────────────────────────


def _validate_consistency(config: SkillConfig) -> None:
    """跨字段一致性校验（结构解析之外的语义约束）。

    JSON 是唯一数据源，但字段之间仍可能互相矛盾——例如 time_allocation 的
    minutes 合计与 scales 声明的总时长不一致、pct 合计不为 1。这类错误不会
    触发结构解析失败，若不拦截会一路静默传导到最终脚本。
    """
    slots = config.time_allocation
    if not slots:
        return

    total = sum(s.minutes for s in slots)
    declared = {s.minutes for s in config.scales if s.minutes}
    if len(declared) == 0:
        pass  # 无规模声明时长，跳过分钟校验（仍校验 pct）
    elif len(declared) == 1:
        want = next(iter(declared))
        if total != want:
            raise ConfigError(
                f"time_allocation 分钟合计 {total}，与 scales 声明的总时长 {want} 不符"
            )
    else:
        # 各规模时长不一致时，原「取唯一声明值」分支会整体跳过分钟校验，
        # 导致 time_allocation 与总时长脱节却无人发现——此处显式报错。
        raise ConfigError(
            f"scales 各规模时长不一致（{sorted(declared)}）；本格式假设固定总时长，"
            "请让所有 scale.minutes 相同，否则 time_allocation 分钟校验失效"
        )

    pct = sum(s.pct for s in slots)
    if abs(pct - 1.0) > 0.005:
        raise ConfigError(f"time_allocation 的 pct 合计 {pct:.4f}，应 ≈ 1.0")


def _validate_required_data(config: SkillConfig) -> None:
    """必填集合非空校验：intake 依赖的数组若为空，收集流程会静默产出空选项
    （如 Q4 话题在 topic_pool 为空时无题可选却仍视为「完成」）。"""
    required = {
        "grammar_points": config.grammar_points,
        "participant_levels": config.participant_levels,
        "scales": config.scales,
        "topic_pool": config.topic_pool,
    }
    for name, val in required.items():
        if not val:
            raise ConfigError(f"{name} 为空，intake 无法生成选项；请至少配置一项")


# i18n 模板渲染用占位符超集；验证时全部提供即可，多余键会被 str.format 忽略
_I18N_DUMMY: dict = {
    "v": "X",
    "lo": 0,
    "hi": 1,
    "level": "X",
    "abilities": "X",
    "feature": "X",
}


def _validate_i18n(config: SkillConfig) -> None:
    """i18n 模板占位符校验：含花括号的文案必须经 str.format 可解析，否则运行期
    生成简报时会抛异常。配置里若出现未转义的字面 { 或未知占位符，在此提前报错。
    """
    i18n = config.style.get("i18n", {}) or {}
    for key, val in i18n.items():
        if "{" not in val:
            continue
        try:
            val.format(**_I18N_DUMMY)
        except (KeyError, IndexError, ValueError) as e:
            raise ConfigError(
                f"style.i18n['{key}'] 占位符无法解析（字面花括号须写成 {{ 与 }}）：{e}"
            ) from e


# question_plan.source 合法取值白名单（_resolve_options 据此分派选项来源）
_VALID_SOURCES = frozenset(
    {
        "grammar_points(keys)",
        "grammar_points[chosen].children",
        "participant_levels",
        "scales",
        "topic_dimensions",
        "topic_pool",
    }
)


def _validate_question_plan(config: SkillConfig) -> None:
    """question_plan 校验：source 必须落在解析器支持的分派白名单内；
    `ask` 与 `mode` 必须一致（ask=True ↔ mode=select；ask=False ↔ auto_recommend）。

    未知 source 会让 _resolve_options 静默返回空选项，进而被误判为「intake 完成」，
    最终导出缺字段的简报——属于必须拦截的静默失败。ask/mode 不一致则走错分支，
    同样产出坏简报。载入期校验可让错误提前暴露。
    """
    for q in config.question_plan:
        if q.source not in _VALID_SOURCES:
            raise ConfigError(
                f"question_plan[{q.id}].source = {q.source!r} 不受支持；"
                f"可选: {sorted(_VALID_SOURCES)}"
            )
        is_auto = q.mode == "auto_recommend"
        if q.ask is is_auto:
            raise ConfigError(
                f"question_plan[{q.id}] ask={q.ask} 与 mode={q.mode!r} 不一致；"
                "应为 ask=True ↔ mode='select'，或 ask=False ↔ mode='auto_recommend'。"
            )


def load_config(path: str | Path | None = None) -> SkillConfig:
    """输入：JSON 文件路径；输出：校验后的 SkillConfig。

    解析步骤：读文件 → json.loads → 结构校验 → 归一化为 dataclass。
    任何读取 / JSON / 结构 / 取值错误统一包装为 ConfigError。

    `path=None` 时固定读取本包资产目录下的 `DEFAULT_CONFIG_NAME`
    （即 `<skill>/assets/` 下唯一那份配置），不做任何跨语言探测 ——
    本包只承载一种语言，误读到别的语言配置本身就是错误。
    """
    path = assets_dir() / DEFAULT_CONFIG_NAME if path is None else Path(path)

    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        raw: dict = json.loads(read_text_or_error(path, "配置文件"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"JSON 解析失败: {e}") from e

    try:
        grammar = tuple(
            GrammarPoint.from_dict(g) for g in raw.get("grammar_points", {}).values()
        )
        constraints = Constraint.from_dict(raw.get("constraints", {}))
        time_alloc = tuple(
            TimeSlot(
                phase=t["phase"],
                label=t["label"],
                pct=float(t["pct"]),
                minutes=int(t["minutes"]),
            )
            for t in raw.get("time_allocation", [])
        )
        plan = tuple(
            QuestionSpec(
                id=q["id"],
                key=q["key"],
                order=int(q["order"]),
                ask=bool(q.get("ask", True)),
                type=q["type"],
                title=q["title"],
                header=q["header"],
                source=q["source"],
                max_choices=q.get("max_choices"),
                depends_on=q.get("depends_on"),
                allow_custom=bool(q.get("allow_custom", False)),
                allow_random=bool(q.get("allow_random", False)),
                mode=q.get("mode", "select"),
            )
            for q in raw.get("question_plan", [])
        )
        config = SkillConfig(
            meta=raw.get("meta", {}),
            constraints=constraints,
            grammar_points=grammar,
            participant_levels=tuple(
                Option.from_dict(o) for o in raw.get("participant_levels", [])
            ),
            scales=tuple(Scale.from_dict(o) for o in raw.get("scales", [])),
            topic_dimensions=tuple(
                Option.from_dict(o) for o in raw.get("topic_dimensions", [])
            ),
            topic_pool=tuple(Option.from_dict(o) for o in raw.get("topic_pool", [])),
            time_allocation=time_alloc,
            vocab_targets=raw.get("vocab_targets", {}),
            exam=raw.get("exam", {}),
            question_plan=plan,
            style=raw.get("style", {}),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"字段结构非法: {e}") from e

    # 跨字段一致性校验 + 必填集合 + i18n 模板占位符
    _validate_consistency(config)
    _validate_required_data(config)
    _validate_i18n(config)
    _validate_question_plan(config)
    return config


def _demo() -> None:
    # 自检：加载并打印关键派生结果（默认在资产目录内探测配置）
    import sys

    print("技能包根目录:", skill_root())
    print("资产目录:", assets_dir())
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = load_config(cfg_path)
    print("✓ 加载成功:", cfg.meta.get("skill_name"), cfg.meta.get("version"))
    print("一级语法点:", [o.label for o in cfg.primary_grammar_options()])
    print(
        "二级(首章):",
        [o.label for o in cfg.secondary_grammar_options([cfg.grammar_points[0].id])],
    )
    print("水平选项:", [o.label for o in cfg.level_options()])
    print("词汇量 B2:", cfg.vocab_range("B2"))
    print("时间分配:", [(t.label, t.minutes) for t in cfg.time_slots()])
    print("每题提示词条数:", cfg.constraints.hint_words_range)
    print("Q1 负载(≤分组):", [p["id"] for p in cfg.build_ask_payload({})])
    print("题库话题:", [o.label for o in cfg.topic_pool_options()])
    print("话题维度(topic_options):", [o.label for o in cfg.topic_options()])
    print("随机话题:", cfg.random_topics())
    print("随机展开:", cfg.expand_choices([RANDOM_SENTINEL]))
    print(
        "混合展开:",
        cfg.expand_choices([cfg.topic_pool[0].id, RANDOM_SENTINEL, "自定义话题X"]),
    )
    sample = {
        "grammar_primary": [g.id for g in cfg.grammar_points[:2]],
        "grammar_secondary": [
            c.id for g in cfg.grammar_points[:2] for c in g.children[:1]
        ],
        "level": ["B1"],
        "topics": [d.id for d in cfg.topic_pool[:2]],
        "scale": [cfg.scales[0].id],
    }
    print("=== Markdown 简报预览 ===")
    print(cfg.build_markdown_brief(sample))


if __name__ == "__main__":
    # 演示入口；加载失败以一条 ✗ + 退出码 2 收尾（与另两个脚本同一约定），不抛栈。
    try:
        _demo()
    except ConfigError as exc:
        print(f"{SKILL_NAME} ({LANG}): 加载失败")
        print("  ✗", exc)
        raise SystemExit(2) from None
