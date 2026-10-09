# anchor-spanish 配置架构与数据接口规范

> 本技能包的权威 schema 与接口文档，随包分发、按需查阅。
>
> - 配置数据：`assets/es-corner-config.json`（唯一数据源）
> - 解析层：`scripts/corner_config.py`
> - skill 主体：`scripts/corner_skill.py`（`init_skill()` 从 assets 加载本包配置）
> - 一致性守卫：`scripts/corner_audit.py`（文档 ↔ 配置 + 包身份 / 内容纯度）
> - 工作流正文：`SKILL.md`；生成框架与方法见其「附录 A · 生成框架与方法」
>
> **本包只服务西班牙语。** 其中不含任何其它语言的配置、分支或文案。
> 其它语言角是各自独立的技能包，彼此不共享文件、不做交叉校验。

## 技能包标准布局

本包是**自包含技能包**（skill-creator 标准结构），可整体复制到 `~/.workbuddy/skills/` 使用：

```text
anchor-spanish/
├── SKILL.md                           工作流正文
├── scripts/                           确定性代码（仅 stdlib，不调用 LLM）
│   ├── corner_config.py               本包解析层：JSON → SkillConfig + 查询 API + 身份常量
│   ├── corner_skill.py                skill 主体：intake 状态机 + 简报导出
│   └── corner_audit.py                一致性守卫：schema / 身份 / 文档 ↔ 配置 / 纯度
├── references/
│   └── corner-architecture.md         字段 schema 与接口契约（本文件）
└── assets/
    ├── es-corner-config.json          唯一数据源（可选项 + 提问编排 + 风格 + i18n）
    └── corner-config.schema.v1.json   配置的 JSON Schema（编辑器补全 / 校验，跨包一致）
```

解析层用 `skill_root()` / `assets_dir()` 定位文件。脚本**必须**位于 `<skill>/scripts/`、与 `SKILL.md` 同级，
否则直接报错（已不再兼容历史上的扁平布局）。

运行命令（在技能包根目录执行）：

```bash
python scripts/corner_config.py      # 加载并校验 assets/es-corner-config.json
python scripts/corner_skill.py       # intake 状态机 + 简报导出
python scripts/corner_skill.py selftest   # 自检：auto_recommend 配对 + 公共 API
python scripts/corner_audit.py       # 文档 ↔ 配置 + 包身份 / 纯度审计
```

## 设计动机

早期把「可选项 / 参数 / 配置项」硬编码进 Markdown 正文，正文一改就要全文核对，且数据无法被程序读取。现统一为：

- **单一结构真源**：所有可选项、提问编排、风格与文案都在 `assets/es-corner-config.json`。
  增删选项只改 JSON，`SKILL.md` 无需改动；
- **单一解析层**：`scripts/corner_config.py` 只做「JSON → `SkillConfig` + 查询 API」。
  展示文案全部来自 `style.i18n`，模块内不硬编码任何文案；
- **语言专一**：本包只认 `es-corner-config.json`，函数签名里没有 `lang` 参数，也不做多语言探测。
  误读到别的语言配置本身就是错误；
- **可测试**：`SkillConfig` 是纯数据对象，可单测、可回显；
- **编辑器友好**：`assets/` 内附 `corner-config.schema.v1.json`（JSON Schema draft 2020-12），
  编辑器据此对配置做补全与实时校验；该 schema 与语言无关，各包逐字节一致；
- **主体与生成解耦**：intake 完成后由 `export_brief()` 导出 Markdown 简报（`es-corner-brief.md`）。
  skill 主体读取该文件产出主持脚本，不依赖任何 LLM / openai。

## 运行时管线（谁负责什么）

```text
assets/es-corner-config.json   唯一数据源（可选项 + 提问编排 + 风格 + i18n 文案）
        │
        ▼  scripts/corner_config.py（本包解析层，仅 stdlib，不硬编码任何文案）
   SkillConfig 类型化对象 + 查询 API
        │
        ▼  scripts/corner_skill.py（skill 主体，驱动 intake 状态机）
   es-corner-brief.md（Markdown 简报，落盘到当前工作目录）
        │
        ▼  本 skill（读入简报）
   docs/es-<topic>.md（最终主持脚本）
```

| 环节                           | 职责                                             | 不做什么                     |
| ------------------------------ | ------------------------------------------------ | ---------------------------- |
| `assets/es-corner-config.json` | 存所有可选项与文案                               | —                            |
| `scripts/corner_config.py`     | 解析、校验、派生（推荐话题、提问负载、简报渲染） | 不调用 LLM、不硬编码任何文案 |
| `scripts/corner_skill.py`      | 按 `question_plan` 逐题收集、导出简报            | 不生成内容                   |
| `scripts/corner_audit.py`      | 校验 schema 引用、包身份与配置取值               | 不改任何文件                 |
| 本 skill（`SKILL.md`）         | 读简报 → 产出西文主持脚本                        | 不重新询问已收集的参数       |

> 工作流正文见 `SKILL.md`；表中「本 skill」一行即指它。文档 ↔ 配置的取值比对也以它为准。

## JSON 字段结构

顶层键（除 `$schema` 外均为 schema 的 `required`）：

| 键                   | 类型                              | 说明                                                                                                                                                  |
| -------------------- | --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `$schema`            | string                            | 本目录内 JSON Schema（`corner-config.schema.v1.json`）的相对路径，供编辑器做补全与实时校验；审计会校验它能解析到真实文件                              |
| `meta`               | object                            | 元信息：`skill_name` / `version` / `display_name` / `config_owner` / `source_of_truth` / `brief_filename` / `note`                                    |
| `constraints`        | object                            | 全局硬约束（见下）                                                                                                                                    |
| `grammar_points`     | object{id: {id,label,children[]}} | **Q1 一级语法点 → Q2 二级条目** 的嵌套映射，是依赖解析的核心                                                                                          |
| `participant_levels` | array[Option]                     | Q3 水平选项（B1/B2/C1/C2/混合）                                                                                                                       |
| `scales`             | array[Option]                     | Q5 规模选项（含 people_min/max/minutes）                                                                                                              |
| `topic_dimensions`   | array[Option]                     | 话题生成维度（方法论参考），含 `desc`                                                                                                                 |
| `topic_pool`         | array[Option]                     | Q4 话题题库（选题库 / 🎲 随机 / 自定义来源），含 `desc`                                                                                               |
| `time_allocation`    | array[TimeSlot]                   | 固定 90 分钟环节分配（phase/label/pct/minutes）                                                                                                       |
| `vocab_targets`      | object{level:[min,max]}           | 各水平生词量区间                                                                                                                                      |
| `exam`               | object                            | `levels` / `rules` / `rubric[]`：本包的评分体系对照（西语 DELE）                                                                                      |
| `question_plan`      | array[QuestionSpec]               | **交互问题编排**（驱动 intake 状态机）                                                                                                                |
| `style`              | object                            | 风格约定：`pure_spanish` / `no_hr` / `no_full_line_bold` / `output_path_template` / `pos_groups[]` / `phase_labels{}` / `section_labels{}` / `i18n{}` |

> `brief_filename`（meta 内）：简报落盘文件名（`es-corner-brief.md`）；`export_brief()` 据此决定输出名，不硬编码。

### `constraints`

```json
{
  "max_participants": 10,
  "duration_minutes": 90,
  "max_grammar_primary": 2,
  "max_topics": 2,
  "level_range": ["B1","B2","C1","C2"],
  "level_mixed_label": "混合（B1–C2）",
  "ask_options_per_question": 6,
  "questions_max_per_call": 4,
  "hint_words_range": [3, 5]
}
```

- `ask_options_per_question=6`：单个 AskUserQuestion 选项上限。
  超过则按 6 个一组拆成多个子问题（id 形如 `Q1#1`、`Q1#2`）。
- `questions_max_per_call=4`：单次调用最多提问数（与工具限制对齐）。
- `hint_words_range=[3, 5]`：每题提示词的条数区间；`SKILL.md` 须写出同一串 `3–5`，
  由 `corner_audit.py` 逐字比对（与 `ask_options_per_question` 同法）。

### `grammar_points`（依赖解析底座）

```json
"adj_adv": {
  "id": "adj_adv",
  "label": "形容词与副词",
  "children": [
    {"id": "adj_comparative", "label": "比较级"}
  ]
}
```

- 一级 `id` 即 Q1 的答案值；其 `children[].id` 即 Q2 的答案值。
- `secondary_grammar_options(primary_ids)` 用这些 id 做 O(1) 查找并去重。

### `question_plan`（intake 状态机蓝图）

每个元素字段：

| 字段               | 含义                                                                                                                        |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `id`               | 问题编号（Q1–Q5），仅用于展示与日志                                                                                         |
| `key`              | **语义键**，写入 `SkillSession.answers` 的索引（如 `grammar_primary`）                                                      |
| `order`            | 提问顺序                                                                                                                    |
| `ask`              | 是否向用户提问（`false`=自动产出，如 auto_recommend 的 Q4）                                                                 |
| `type`             | `multi` / `single`                                                                                                          |
| `title` / `header` | 展示标题 / 工具分组标签                                                                                                     |
| `source`           | 选项来源表达式：`grammar_points(keys)` / `grammar_points[chosen].children` / `participant_levels` / `scales` / `topic_pool` |
| `max_choices`      | 最多选几个（`null`=不限）                                                                                                   |
| `depends_on`       | 依赖的**语义键**（如 `grammar_primary`），用于动态选项与 skip 判断                                                          |
| `allow_custom`     | 是否允许用户自定义                                                                                                          |
| `mode`             | `select`（询问）或 `auto_recommend`（自动推荐）                                                                             |
| `allow_random`     | 为 true 时在选项首位追加「🎲 随机选一个」哨兵项（`__random__`），由 `expand_choices()` 展开为题库随机话题                   |

> 本包 `question_plan` 的五题：Q1 一级语法点 → Q2 二级条目（依赖 Q1）→ Q3 水平 →
> Q4 话题（`ask:true, mode:"select"`，选项来自 `topic_pool`，同时开启 `allow_custom` 与 `allow_random`）→ Q5 规模。
>
> 若日后要改为不询问，把该问题设为 `ask:false, mode:"auto_recommend"` —— 此时由
> `ask_next()` 调用 `auto_recommend_choices()` 按 source 填值（topic_pool 走随机推荐，其它源取前 N 个候选），
> 解析层无需改动。

### `style.i18n`（文案外置的关键）

`style.i18n` 存放**所有面向人的展示文案**，使 `corner_config.py` 不硬编码任何文案。键名固定，取值随包提供：

| 键                                                                              | 用途               | 占位符                              |
| ------------------------------------------------------------------------------- | ------------------ | ----------------------------------- |
| `brief_title`                                                                   | 简报一级标题       | —                                   |
| `brief_intro`                                                                   | 简报导语           | —                                   |
| `constraints_header` / `constraint_form` / `constraint_output_lang`             | 基础约束段         | —                                   |
| `exam_label`                                                                    | 评分体系标签行前缀 | —                                   |
| `collected_header`                                                              | 已收集参数段标题   | —                                   |
| `level_line` / `grammar1_line` / `grammar2_line` / `topics_line` / `scale_line` | 各字段行           | `{v}`                               |
| `vocab_line`                                                                    | 词汇量目标行       | `{lo}` `{hi}`                       |
| `phases_line` / `pos_line`                                                      | 阶段 / 词性分组行  | `{v}`                               |
| `output_req_header` / `output_req_intro`                                        | 产出要求段         | —                                   |
| `structure_line` / `vocab_note_line`                                            | 结构 / 词汇说明行  | —                                   |
| `exam_rules_prefix`                                                             | 评分规则行前缀     | —                                   |
| `rubric_line`                                                                   | 评分 rubric 行     | `{level}` `{abilities}` `{feature}` |
| `no_hr_rule`                                                                    | 风格约束行         | —                                   |

> 文案一律不含行首 `-` 前缀（列表符由解析层统一添加）。
> `structure_line` / `vocab_note_line` 例外，由解析层以 `-` 前缀拼装。

### 包专属键

除 `i18n` 取值外，`style` 里只有一个是本包专属的产出语言标志键：

| 键                   | 值     |
| -------------------- | ------ |
| `style.pure_spanish` | `true` |

- 该键仅供 skill 正文与人工阅读时判别产出语言，**解析层不读取**（`scripts/corner_config.py` 无需任何分支）。
- 其余 `style` 键为通用键，仅取值不同：
  - `no_hr` / `no_full_line_bold` / `output_path_template`
  - `pos_groups[]` / `phase_labels{}` / `section_labels{}` / `i18n{}`

### 顶层键的消费方

| JSON 顶层键          | 内容                                                        | 消费方 / 对应章节                         |
| -------------------- | ----------------------------------------------------------- | ----------------------------------------- |
| `meta`               | 技能名、版本、简报文件名                                    | `scripts/corner_skill.py` 落盘命名        |
| `constraints`        | 人数、时长、选项上限、分组大小、提示词条数                  | 校验 / SKILL.md「交互契约」的选项分组规则 |
| `grammar_points`     | 一级章节 → 二级条目树                                       | Q1、Q2                                    |
| `participant_levels` | 水平档位（含「混合」）                                      | Q3                                        |
| `scales`             | 规模（人数区间 + 总时长）                                   | Q5、时间分配校验                          |
| `topic_dimensions`   | 三个通用生活维度                                            | SKILL.md「话题生成方法」                  |
| `topic_pool`         | 话题题库（可选题库 / 🎲 随机一个 / 自定义输入）             | Q4                                        |
| `time_allocation`    | 环节占比与分钟数                                            | 简报、SKILL.md「环节时间分配与规模备注」  |
| `vocab_targets`      | 各水平词汇量区间                                            | 简报、SKILL.md「生成生词表」              |
| `exam`               | DELE 等级、标注规则、rubric                                 | 简报、SKILL.md「30 题生成规则」           |
| `question_plan`      | 提问编排（顺序 / 类型 / 依赖 / 上限 / 模式）                | intake 状态机、SKILL.md「交互契约」       |
| `style`              | 纯西文、无 `---`、输出路径模板、POS 分组、阶段名、i18n 文案 | 简报渲染、SKILL.md「风格约定」            |

## 解析层 `scripts/corner_config.py`

### 输入（Input）

`load_config(path: str | Path | None = None) -> SkillConfig`

- `path=None`：固定读取 `assets_dir() / DEFAULT_CONFIG_NAME`，即
  `<skill>/assets/es-corner-config.json`，不做任何探测或回退；
- 模块**仅依赖标准库**（`json` / `dataclasses` / `pathlib` / `typing`），无第三方依赖。

### 处理（Transform）

读文件 → `json.loads` → 结构 / 取值校验 → 归一化为 `dataclass`：

- `ConfigError`：统一包装所有结构 / 取值 / 布局错误，由 skill 主体捕获；
- `Option` / `GrammarPoint` / `Constraint` / `TimeSlot` / `QuestionSpec`：不可变值对象；
- `SkillConfig`（`frozen`）：构建时生成 `_grammar_index`（一级 id → `GrammarPoint`）供 O(1) 查询。
  `exam` 字段承载评分体系。

### 输出（Output）

返回 **`SkillConfig`** 实例，对外暴露稳定查询 API（返回值均为普通 Python 对象）：

| API                                                       | 返回                                                                         |
| --------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `primary_grammar_options()`                               | `list[Option]` —— Q1 选项                                                    |
| `secondary_grammar_options(primary_ids)`                  | `list[Option]` —— Q2（依赖 Q1，去重）                                        |
| `level_options()` / `scale_options()` / `topic_options()` | `list[Option]`；`topic_options()` 亦可作为 `source: "topic_dimensions"` 使用 |
| `auto_recommend_choices(q, state) -> list[str]`           | 为 `ask=false` 节点生成自动推荐值（topic 源走随机推荐，其它源取前 N 个候选） |
| `vocab_range(level) -> (min,max)`                         | 生词量区间                                                                   |
| `time_slots() -> list[TimeSlot]`                          | 环节时间分配                                                                 |
| `next_question(state) -> QuestionSpec \| None`            | intake 下一题（含 auto_recommend 节点）                                      |
| `build_ask_payload(state) -> list[dict]`                  | AskUserQuestion 兼容负载（含 ≤5 分组）                                       |
| `topic_pool_options() -> list[Option]`                    | Q4 题库选项（不含随机哨兵）                                                  |
| `random_topics(count=1, exclude=None) -> list[str]`       | 从题库随机抽取话题标签（避开 exclude）                                       |
| `expand_choices(choices) -> list[str]`                    | 哨兵→随机话题、题库 id→label、自定义文本原样保留                             |
| `build_markdown_brief(answers) -> str`                    | **Markdown 简报（交给 skill 主体读取）**，文案全部取自 `style.i18n`          |
| `label_of_level(id)` / `label_of_scale(id)`               | id → 展示标签                                                                |

> 上面是 `SkillConfig`（corner_config.py）的查询 API。
> 下方 `SkillSession`（corner_skill.py）的公开方法见「交互收集」与「导出简报」。其中 `preview_brief()` 返回与
> `export_brief()` 相同的 Markdown 简报字符串但**不落盘**，供调试预览。

`build_markdown_brief` 输出一份清晰的 Markdown 简报（含「基础约束 / 已收集参数 / 产出要求」三节）。
由 `SkillSession.export_brief()` 落盘为 `meta.brief_filename`，供 skill 主体读取后生成主持脚本。

## JSON ↔ skill 主体数据接口与调用约定

### 初始化（配置 → 对象）

```python
from corner_config import load_config

cfg = load_config()  # 读 assets/es-corner-config.json
from corner_skill import init_skill

session = init_skill()  # 等价：加载 + 构造 SkillSession
session = init_skill(config_path="…")  # 显式指定配置（测试 / 多套配置场景）
```

**契约**：skill 主体**从不直接读取 JSON**，一切访问经由 `SkillConfig` 查询 API。
JSON 格式升级（增删字段、改结构）只要 `SkillConfig` 接口不变，主体逻辑无需改动。

### 交互收集（intake，逐题一问一答）

```text
loop:
    payload = session.ask_next()       # -> list[dict]，当前问题的全部分组
    if payload == []: break            # intake 完成
    # 宿主把 payload 交给 AskUserQuestion 工具并收回 choices
    for chunk in payload:
        session.submit(chunk["id"], chosen_values)   # chosen = 选中项的 value(id)
```

- `ask_next()` 一次返回**当前问题所有分组**（`Q1` 超 5 选项 → `[Q1#1, Q1#2]`）。
  宿主须对**全部分组**作答后再调用下一次 `ask_next()`。
- `submit(question_id, choices)`：`question_id` 形如 `Q1` 或 `Q1#2`，自动归并到父 `key`。
  `choices` 是选中项的 **`value`（id）** 列表；多分组去重合并；超 `max_choices` 抛 `ConfigError`。
- `allow_random` 的 Q4：选项首位是「🎲 随机选一个」哨兵。
  `submit()` 调用 `expand_choices()` 把哨兵展开为题库随机话题、把题库 id 转为可读标签，
  用户自定义输入原样保留。
- `auto_recommend` 的节点（`ask=false`）：由 `ask_next()` 内部调用 `auto_recommend_choices()` 填值，不向用户提问。
  `topic_pool`/`allow_random` 来源走随机话题推荐，其它来源取解析出的前 N 个候选（单选 1 个、多选取 `max_choices`）。
  本包当前配置未启用此模式。

### 导出简报（交给 skill 主体读取，不依赖 LLM / openai）

```python
if session.is_intake_done():
    brief_path = session.export_brief()  # -> 写出 meta.brief_filename
```

> 设计要点：**完全不依赖任何 LLM / openai**。intake 只负责「参数收集」，产出的 Markdown 简报文件交给
> skill 主体读取并据此生成主持脚本，从而把「参数收集」与「内容生成」彻底解耦，也便于人工核对收集到的参数。

`SkillSession` 公共方法一览：

| 方法                                   | 说明                                                                                     |
| -------------------------------------- | ---------------------------------------------------------------------------------------- |
| `ask_next() -> list[dict]`             | 返回当前问题**全部分组**负载；`[]` 表示 intake 完成                                      |
| `submit(question_id, choices) -> None` | 提交某分组答案（value/id 列表）；跨分组去重合并；超 `max_choices` 抛 `ConfigError`       |
| `is_intake_done() -> bool`             | 全部问题已收集返回 `True`（用于 `export_brief` 守卫，与 `ask_next()==[]` 同源）          |
| `preview_brief() -> str`               | 返回与 `export_brief()` 相同的 Markdown 简报字符串，**不落盘**，便于调试预览             |
| `export_brief(path=None) -> str`       | 落盘 `meta.brief_filename`（默认写到**调用方 cwd**）并返回路径；未完成时抛 `ConfigError` |
| `init_skill(*, config_path=None)`      | 构造本包 `SkillSession`（默认读 assets 内的本包配置）                                    |

### 选项 value/label 约定（关键）

- `build_ask_payload` 每个选项带 `{"value": <id>, "label": <展示>, "description": <备注>}`；
- 宿主回传**选中的 `value`（id）** 给 `submit`；
- 若宿主（如原生 AskUserQuestion）只回传 `label`，需在桥接层做 `label→value` 映射后再 `submit`。

## 技能包文件清单

| 文件                                  | 角色                                                                                                          |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `scripts/corner_config.py`            | 解析模块：JSON → `SkillConfig` + 查询 API；含本包身份常量（`SKILL_NAME` / `LANG` / `DEFAULT_CONFIG_NAME`）    |
| `assets/es-corner-config.json`        | 西语选择数据（唯一数据源）                                                                                    |
| `scripts/corner_skill.py`             | skill 主体：`SkillSession` 状态机 + 简报导出                                                                  |
| `scripts/corner_audit.py`             | 一致性守卫：schema 引用 + 包身份（一个配置 / 名称一致）+ 文档 ↔ 配置取值比对 + 内容纯度（不得引用他语言配置） |
| `assets/corner-config.schema.v1.json` | 配置的 JSON Schema（编辑器补全 / 校验用；解析层不读取；各包逐字节一致）                                       |
| `references/corner-architecture.md`   | 本规范                                                                                                        |
| `SKILL.md`                            | 工作流 / 风格散文（权威流程说明，数据外置）                                                                   |

运行自检（均用标准库，免装依赖）：

```bash
python scripts/corner_config.py                        # 加载 + 校验 assets/es-corner-config.json
python scripts/corner_config.py assets/es-corner-config.json   # 显式指定配置（等价）
python scripts/corner_skill.py                         # intake 状态机 + 简报导出
python scripts/corner_skill.py selftest                # 自动推荐配对 + 公共 API 自检
python scripts/corner_audit.py                         # schema 引用 + 身份 + 文档 ↔ 配置 + 纯度审计
```

`scripts/corner_audit.py` **只审计本包**，不与其他技能包交叉比对（每包自包含）。
检查按 schema → 身份 → 文档 ↔ 配置 → 纯度依次执行，**四段全部跑完才收尾**（某段失败不截断后续段，一次报全）：

1. **schema 引用**：`$schema` 指向包内真实存在且可解析（带 `title`）的 JSON Schema，
   且配置齐备该 schema 的 `required` 顶层键。
2. **身份**：`assets/` 下恰好一个 `*-corner-config.json` 且名为 `es-corner-config.json`。
   `SKILL.md` frontmatter `name` == `SKILL_NAME` == `meta.skill_name`。
3. **文档 ↔ 配置**：逐项核对 SKILL.md 与配置是否对得上：
   - `meta.version` 自查 X.Y.Z 形态（版本号唯一真源在配置，frontmatter 不写）
   - `brief_filename`、`constraints`（人数 / 时长 / 选项上限 / 提示词条数）
   - `time_allocation` 逐行核对分钟与占比，且分钟合计须等于 `duration_minutes`、pct 合计 ≈ 1.0
   - `style.output_path_template` / `pos_groups` / `phase_labels`
   - `vocab_targets` 区间、`exam.levels` 标签
   - SKILL.md「交互契约」表中每题的 `ask` 标注
4. **内容纯度**：包内任何 `.md` / `.py` / `.json` 都不得出现别的语言配置文件名。

报出不一致时退出码 `1`；自检自身跑不下去（包布局不对、配置或正文读不了）时退出码 `2`。两者必须分得开：
`1` 要你改包，`2` 要你先修好自检的输入。输出只有两种行：`✗` 是报出的不一致（决定退出码），
`○` 是「这一段没验」的告知（不影响退出码）。
`○` 目前只有一种来源：`$schema` 写成 URL 时本包解析不了它，引用与必填键两层校验都被跳过。
**跳过不等于校验过** —— 这正是它不叫 `✓` 的原因。

## 本包实例取值

| 项                          | 本包取值                                                                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `meta.skill_name`           | `anchor-spanish`                                                                                                                     |
| `meta.source_of_truth`      | `skills/anchor-spanish/assets/es-corner-config.json`（JSON 自身为唯一数据源）                                                        |
| `meta.brief_filename`       | `es-corner-brief.md`                                                                                                                 |
| `grammar_points`（一级 id） | `adj_adv` / `indicativo` / `pronombres` / `relativas_conj` / `modos` / `no_finitas_pasiva` / `fragmentos`（各含西语二级条目）        |
| `exam.levels`               | `DELE B1` / `DELE B2` / `DELE C1` / `DELE C2`                                                                                        |
| `question_plan`             | Q1 一级语法点 → Q2 二级条目 → Q3 水平 → **Q4 话题 `ask:true, source:"topic_pool", allow_random`**（选题库 / 随机 / 自定义）→ Q5 规模 |
| `style.pure_spanish`        | `true`（产出语言：西班牙语）                                                                                                         |
| `style.phase_labels`        | `Apertura` / `Rompehielos` / `Tema` / `Discusión` / `Cierre`                                                                         |
| `style.pos_groups`          | `Sustantivos` / `Verbos` / `Adjetivos` / `Adverbios` / `Preposiciones·Conjunciones` / `Pronombres` / `Otros`                         |
| `style.i18n`                | 所有简报展示文案（标题、约束、已收集参数、产出要求、rubric 行等）均为西语，由解析层统一渲染                                          |

```python
from corner_config import load_config

cfg = load_config()  # 默认路径由 assets_dir() 解析
from corner_skill import init_skill

session = init_skill()  # 等价：加载 + 构造 SkillSession
```

## 按语法点的题型骨架（由 SKILL.md 附录 A 下沉）

由 `SKILL.md` 附录 A 下沉而来：一次生成只用到其中几行，正文里不必常驻。

每条语法点给出 3 档难度骨架；使用时把 {S} 换成本次话题，并打上对应 DELE 标签：

| 语法点           | 易（B1）骨架                         | 中（B2）骨架                                | 难（C1 / C2）骨架                                |
| ---------------- | ------------------------------------ | ------------------------------------------- | ------------------------------------------------ |
| 简单过去时       | *¿Qué hiciste respecto a {S}?*       | *Cuenta una anécdota sobre {S}.*            | *¿En qué cambió {S} tu manera de ver las cosas?* |
| 未完成过去时     | *¿Qué hacías de niño con {S}?*       | *Describe cómo era {S} antes.*              | *¿Cómo ha evolucionado {S} con el tiempo?*       |
| 条件式           | *Si tuvieras…, ¿qué harías con {S}?* | *¿Qué le aconsejarías a alguien sobre {S}?* | *¿Y si {S} no existiera, cómo viviríamos?*       |
| 虚拟式           | *Es necesario que… para {S}.*        | *Dudo que {S} sea fácil.*                   | *Aunque digan que {S},…*                         |
| 比较级           | *¿Prefieres {S} o {S2}?*             | *¿En qué es {S} mejor que {S2}?*            | *¿Qué modelo de {S} se impone?*                  |
| ser vs estar     | *¿Cómo es {S}?*                      | *¿Cómo está {S} hoy?*                       | *¿En qué estado quedó {S}?*                      |
| por vs para      | *¿Para qué sirve {S}?*               | *¿Por qué importa {S}?*                     | *¿Hasta qué punto cambia {S} por/debido a algo?* |
| 疑问句           | *¿Qué opinas de {S}?*                | *¿Cómo explicas {S}?*                       | *¿Hasta dónde llegarías por {S}?*                |
| 宾语代词         | *¿Lo has hecho por {S}?*             | *Nos lo explicaron sobre {S}.*              | *Lo que se ha sacado de {S}…*                    |
| 将来时           | *¿Qué harás con {S}?*                | *¿Cuándo empezarás {S}?*                    | *¿Cómo será {S} dentro de diez años?*            |
| 人称 a           | *¿A quién admiras respecto a {S}?*   | *¿A quién le contarías {S}?*                | *¿A qué personas afecta {S}?*                    |
| 被动             | *{S} suele malinterpretarse.*        | *Se ha decidido algo sobre {S}.*            | *¿Cómo percibe la sociedad {S}?*                 |
| 连词从句         | *Porque {S}…*                        | *Aunque {S},…*                              | *A condición de que {S}, ¿qué haríamos?*         |
| 数字 / 量词      | *¿Cuántos/as {S}?*                   | *La mayoría de los {S} son…*                | *¿Cuál es el equilibrio justo de {S}?*           |
| gerundio（进行） | *Haciendo {S}, se aprende…*          | *{S} se entiende actuando.*                 | *Sigue haciendo {S}, pero…*                      |

> 骨架仅供启发；实际生成时结合话题与所选水平微调。
