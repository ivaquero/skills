---
name: project-py
description: 编写、修改、检查、运行 Python 代码时使用。规定运行脚本前的虚拟环境探测与选择（交互对话框）、包管理器选择（只允许 micromamba/uv，任何情况下都不用 pip，缺包也要停下而不是 pip 补）、代码风格（enumerate、matplotlib 面向对象接口）、以及用 ruff + ty 做静态检查的完整流程。触发词：Python、py、ruff、ty、lint、类型检查、包管理、micromamba、mamba、conda、uv、matplotlib、subplots、运行脚本、虚拟环境、死代码、删除死代码、清理未使用代码。
agent_created: true
---

# Python 代码编写规则（lectures 仓库）

**技能类型**：混合型 —— 硬约束管「不许做什么」（禁 `pip`、禁写死路径），工序管「按什么顺序做」（判定 → 探测 → 询问 → 执行，format → check → ty）。

> 适用范围：`.py` 源码。**不处理 `.ipynb`**。

## 包管理器：先问，再动手（硬规则）

**严禁使用 `pip` 装包或卸包 —— 包在本机不存在时也不行。** 「这个包哪个环境都没有」
从来不是放宽这条规则的理由：缺依赖时的正确反应是**报告并停下**，不是去 `pip`。

禁止的形式包括（不限于）：`pip install` / `pip uninstall`、`python -m pip`、
`<环境>/Scripts/python -m pip`（或 `bin/python -m pip`），以及把 `pip` 塞进一条
`&&` 链、写进脚本或 `Makefile` 里顺手执行。**判定看的是「有没有调用 pip」，
不是「这条命令是不是用户点的头」** —— 用户同意装包，也仍然只走下面两条合法入口。

任何依赖变动只能通过下面两者之一完成，且**必须先询问用户选哪一个**：

| 选项 | 追问 | 安装命令（示意） |
| --- | --- | --- |
| `micromamba` / `mamba` | **必须再问具体虚拟环境名**（见「运行脚本」） | `micromamba install -n <env> -c conda-forge <pkg> -y` |
| `uv` | 默认仓库内 `.venv` | `uv add <pkg>` / `uv sync` |

**两条路都不通时（本机没有 micromamba / mamba / conda，也不打算用 uv），
就停下来。** 把「缺哪个包、试过哪些环境、各自什么结论」交代清楚，交给用户处理；
**不许退回 `pip`**，也不许用 `pip` 往系统解释器或临时 venv 里塞包补缺。

本机最常用的选择是 `kaggle` 环境，但它**只是候选之一**——具体用哪个，一律由
**「运行脚本」的对话框**确认，文档与脚本里都不写死。
环境目录同样**不要写死**，运行时扫描 `PATH` 与环境变量现取：

```bash
command -v micromamba                     # 工具在哪
micromamba env list                       # 有哪些环境、各自路径
micromamba run -n <env> python -c "import sys; print(sys.prefix)"   # 该环境前缀
```

> 仓库根没有 `pyproject.toml` / `requirements.txt`，依赖一律由 conda 环境承载。

**通用原则：任何文档、脚本、配置里都不写工具或环境的绝对路径，也不写工具版本号。**
需要时现场用 `command -v` / `env list` / `sys.prefix` / `--version` 解析；
`ty.toml` 里也**不要**写死解释器路径。

## 运行脚本：先探测环境，再让用户选（硬规则）

**要执行的 `.py` 只要 import 越出标准库，就不许自己挑个解释器直接跑。**
顺序固定为判定 → 探测 → 询问 → 执行，四步缺一不可。

**① 判定脚本有没有第三方依赖。** 把它的 import 与 `sys.stdlib_module_names` 比对；
全是标准库就直接跑，不必打扰用户。

**② 探测本机可用的环境**（只读，结论现取）：

```bash
command -v micromamba mamba conda                   # 本机有哪些管理器
micromamba env list                                 # 环境名 + 各自路径
micromamba run -n <env> python -c "import <mod>"    # 逐个试探依赖是否齐全
```

**③ 用 `AskUserQuestion` 让用户自己选环境**，不要替他决定：

| 要点 | 要求 |
| --- | --- |
| 选项 | 一个候选环境一项，环境名照抄探测结果；`description` 里给证据——环境路径 + 哪些依赖已可导入、缺哪个 |
| 逃生项 | **必须有一项「暂停，我自己装」**，选中即刻停止任务；**缺包的候选环境照实列出，但不许顺手 pip 补缺** |
| 排序 | 依赖已齐全的环境放第一项，并标注「推荐」 |
| 数量上限 | 单个问题最多 4 项（宿主上限）；候选多于 3 个时分批追问，**每批都要带逃生项** |
| 兜底口子 | 宿主 UI 总会额外给一个自由输入框，用户可当场敲一个不在列表里的环境名；想改用 `uv` / 项目内 `.venv` 的走这个口子或选暂停项，再按「包管理器」处理 |
| `header` | ≤ 12 字符，例如 `运行环境` |

**④ 在选定环境里执行**：

```bash
micromamba run -n <选定的环境> python <脚本>
```

用的是 `mamba` / `conda` 就换对应命令。**任何时候都不用 `pip`**（见「包管理器」）——
包括所有候选环境都缺依赖、眼看就要跑不起来的时候。

> 选中「暂停，我自己装」不是失败出口，而是合法的收尾：**报告已探明的环境清单，
> 以及每个环境各缺哪些包，然后停下**。不要顺手 `pip install`，
> 也不要挑一个依赖不全的环境硬跑。
>
> 这条出口的含义是**把「装包」这一步交回给用户**，不是「用户点过头就允许 pip」：
> 后续真要装，仍然只走「包管理器」的 micromamba / uv（用 micromamba 就先拿到环境名），
> **整条链路上都不出现 `pip`**。

同一会话里已选定的环境可以沿用，不必每次重问；一旦换了脚本、换了依赖集，
或者用户说了「换个环境」，就重新走一遍上面四步。

## 代码风格（写 Python 时必须遵守）

### 基础

**迭代用 `enumerate()`，不要 `range(len())`。**

```python
xs = range(3)
# good
for ind, x in enumerate(xs):
    print(f"{ind}: {x}")
# bad
for i in range(len(xs)):
    print(f"{i}: {xs[i]}")
```

### Matplotlib

1. **用面向对象接口（OO），不要 Artist API。**
2. **画子图用 `plt.subplots(..., constrained_layout=True)`，不要 `plt.tight_layout()`。**

```python
# good
_, axes = plt.subplots(1, 2, constrained_layout=True)
axes[0].plot(x1, y1)
axes[1].hist(x2, y2)
# bad
plt.subplot(121)
plt.plot(x1, y1)
plt.subplot(122)
plt.hist(x2, y2)
```

3. **子图数据可迭代时，用 `axes.flatten()`，不要 `plt.subplot()`。**
4. **迭代对象用 `zip()` / `enumerate()`，不要 `range()`。**

```python
# good
_, axes = plt.subplots(2, 2, figsize=[12, 8], constrained_layout=True)
for ax, x, y in zip(axes.flatten(), xs, ys):
    ax.plot(x, y)
# bad
for i in range(4):
    ax = plt.subplot(2, 2, i + 1)
    ax.plot(x[i], y[i])
```

5. **装饰统一用 `set()` 批量写，不要逐条 `set_*()`。**

```python
# good
ax.set(xlabel="x", ylabel="y")
# bad
ax.set_xlabel("x")
ax.set_ylabel("y")
```

6. **多条 spine 一次传列表，不要逐条调用。**

```python
# good
ax.spines["top", "bottom"].set_visible(False)
# bad
ax.spines["top"].set_visible(False)
ax.spines["bottom"].set_visible(False)
```

> `zip()` 记得显式写 `strict=`（ruff 会要求），避免引入新告警。

## 静态检查：ruff + ty（**改完必须先格式化**）

**顺序固定：① `ruff format` → ② `ruff check` → ③ `ty check`。**
格式化和 lint 是两个不同的动作，`check` 不会替你排版；**改完代码不跑 format 就算没做完**。

```bash
# 0) 先确认工具可用（本机 ruff / ty / micromamba / uv 均在 PATH）
command -v ruff ty micromamba uv

# 1) 格式化 —— 修改后第一步，必须执行
ruff format code python --exclude "*.ipynb"

# 2) 静态检查 —— 只查 .py，排除 notebook
#    根 pyproject 同时设了 fix 与 fix-only：只加 --no-fix 仍会改写文件，
#    两个都加才是只读 —— 这是唯一「只看不改」的组合
ruff check  code python --no-fix --no-fix-only --exclude "*.ipynb"

# 3) 类型检查 —— 必须指向装有依赖的解释器
#    环境名用「运行脚本」对话框选定的那个（现取，勿写死路径）
micromamba run -n <选定的环境> ty check code python
```

> **为什么 format 排在最前**：`ruff check --fix` 的自动修复（尤其 `UP` 类升级）
> 常会产生需要重新排版的代码，先 format 可减少二次改动；且 format 只动空白与换行、
> 语义零风险，放第一步不会掩盖后续问题。
>
> ruff **原生会解析 `.ipynb`**，不加 `--exclude "*.ipynb"` 就会把 notebook 也纳入检查。
> 本技能范围仅 `.py`，检查时显式排除。

**注意**：

- **改完必须格式化。** 不要以为 `check` 通过就等于格式正确。
- **不要在文档、脚本、配置（含 `ty.toml`）里写工具或环境的绝对路径。**
  路径一律运行时扫描 `PATH` / 环境变量现取，否则换机或升级后必然失效。
- **不要记录工具版本号。** 需要时 `ruff --version` / `ty --version` 现取；
  文档里写死版本会在升级后变成误导信息。

工具自己会咬人的那几处 —— `ruff check` 默认落盘、`ty` 裸跑刷假警报、子目录
`pyproject.toml` 覆盖根规则集 —— 见「踩坑点」。

## 删除死代码（精简工程 / 收尾时）

用 `scripts/deadcode_scan.py` 找「没有任何地方引用」的顶层定义。只用标准库，**只读**，
从不改文件：

```bash
python <本技能目录>/scripts/deadcode_scan.py <包目录> --root <项目根>
```

退出码 `0` 干净 / `1` 有候选（打到 stdout）/ `2` 参数或源码树读不了。
**候选是线索，不是结论** —— 落刀前先读 `references/deadcode.md`，
那里写了唯一的真盲区与删除时的四个注意点。

三条硬规则：

1. **带装饰器的顶层函数一律视为活代码。** `@app.get("/path")` 这类注册全靠
   **路径字符串**，函数名不会第二次出现，纯引用计数必判它死刑。脚本已内置这条例外。
2. **`--root` 要覆盖前端与打包元数据**（`templates/`、`static/`、`pyproject.toml`、
   `scripts/`），否则被它们引用的符号会被误判成死代码。只被 `tests/` 引用的**不是**
   死代码 —— 测试是资产。
3. **删完立刻跑 lint。** 死函数常是某条 import 的唯一消费者，交给工具找而不是人眼
   （实测：`defaultdict` 正是被删函数独占的）。顺序不变，先 format 再 check：

```bash
ruff format <路径> --exclude "*.ipynb"
ruff check <路径> --no-fix --no-fix-only --exclude "*.ipynb"
```

删完再扫一次，应输出 `no unreferenced top-level definition found`。
**删除属于重构**：静态门、回归测试、端到端输出比对一个都不能省。

## Markdown 文档检查：rumdl（**改完本技能自身的 .md 后必跑**）

本技能是 Markdown 交付物，改动 `SKILL.md` 或 `references/*.md` 后必须用系统环境里的
`rumdl` 检查并修复。

**顺序同样固定：① `rumdl check --fix`（修复）→ ② `rumdl fmt`（格式化）。**

```bash
# 0) 确认可用（在 PATH 中，勿写死路径）
command -v rumdl

# 1) 先看会改什么（只读，务必先做这步）
rumdl check --diff skills/project-py/

# 2) 检查 + 自动修复（范围限定在本技能目录，勿扫全仓）
rumdl check --fix skills/project-py/

# 3) 格式化 —— 修复之后再排版
rumdl fmt skills/project-py/

# 4) 复核
rumdl check skills/project-py/
```

> `--fix` / `fmt` 的作用范围实测严格受限（见「踩坑点」），但仍要核对范围 ——
> 「不给路径」等于扫全仓；`-f` 也不是预演。

**注意**：

- **`rumdl fmt` 只会修「可自动修复」的问题**，落盘前先用 `--check` 或 `--diff` 预览。
- 部分规则**不可自动修复**，需要手改。本技能常见的有三类：

  | 规则 | 含义 | 处理 |
  | --- | --- | --- |
  | `MD013` | 行超长 | 中文长句**不要为凑行宽而硬断**；先看根配置的 `line-length`，必要时行内 disable |
  | `MD036` | 用粗体冒充标题 | 改成真正的 Markdown 标题；若是有序列表项，改用 `1. **…**` 形式 |
  | `MD028` | 引用块内出现空行 | 删掉块内空行，或用 `>` 空行把两段并进同一个引用块 |

- **放宽规则只在自己的仓库里做，而且要逐条写明理由。** 排版体裁类（`MD0xx`）可以放宽，代码质量类
  一律不行；不要动别人仓库的根配置 —— 那会影响其他人的文档，局部豁免请用行内
  `<!-- rumdl-disable... -->`。

## 踩坑点

都是工具的实际行为，不是偏好。发现一个加一个，写成「现象 → 原因 → 对策」。

- **`ruff check` 会当场改文件，只加 `--no-fix` 拦不住。**
  现象：本想只看一眼问题，跑完发现代码已经被改了；补上 `--no-fix` 还是被改。
  原因：根 `pyproject.toml` 同时设了 `fix = true` 与 `fix-only = true`。CLI 的
  `--no-fix` 只关掉前者，`fix-only` 仍在，而它的语义正是「照改不误、改完不报」。
  对策：只想检视时两个都要加 —— `--no-fix --no-fix-only`；只想看会改什么用 `--diff`。
- **`ty check` 裸跑刷出一片假警报。**
  现象：普通项目里满屏 `unresolved-import`。
  原因：`ty` 默认拿系统 Python 当检查环境，那里没有项目依赖。
  对策：一律 `micromamba run -n <选定环境> ty check …`，环境名由「运行脚本」对话框给出。
- **`ruff` 会连 notebook 一起查。**
  现象：只改了 `.py`，却报出 `.ipynb` 的问题。
  原因：`ruff` 原生解析 `.ipynb`，不排除就会一并纳入检查。
  对策：命令显式带 `--exclude "*.ipynb"`。
- **`rumdl check -f` 不是预演。**
  现象：本想「先看看会改什么」，结果文件已经被改写。
  原因：`-f` 就是 `--fix`，`check` 带上它即直接落盘；预览要用 `--diff`。
  对策：顺序固定为 `--diff` 预览 → `--fix` → `git status` 核对范围 → `git diff` 逐行审查。
- **`rumdl --fix` 的作用范围严格受限，但「核对范围」这一步仍要保留。**
  现象：担心「只指定了 `skills/project-py/`，`skills/project-typ/` 也被一起改了」。
  原因：**未复现** —— 文件参数与目录参数都只动给定路径下的文件；唯一会波及全仓的写法是
  **不给路径**（或给仓根），那不是连坐，就是「扫了全仓」。
  对策：每次 `--fix` / `fmt` 后照旧跑 `git status --short` 核对范围（这是保险，不是补救）；
  多出来的用 `git checkout -- <path>` 还原。
- **在子目录新建 `pyproject.toml` 会丢掉根规则集。**
  现象：子目录里的代码突然不再被根配置的规则检查。
  原因：`ruff` 就近取配置，该目录会脱离根 `[tool.ruff.lint]`。
  对策：不在子目录新建 `pyproject.toml`。
- **装饰器注册的端点会伪装成死代码。**
  现象：扫描报某函数「全项目只出现一次」，删掉后接口 404。
  原因：`@app.get("/evaluate/jobs/{job_id}")` 靠**路径字符串**登记，函数名不再出现，
  纯引用计数看不见这层注册 —— 这是引用计数唯一的真盲区。
  对策：带装饰器的顶层函数一律保留；`scripts/deadcode_scan.py` 已内置此例外，
  连带它调用的私有 helper 也因「定义 + 调用」而安全。
- **`ty.toml` 里写死解释器路径比不配置更糟。**
  现象：`ty` 直接以 `Invalid environment.python setting` 退出（exit 2）。
  原因：路径在换机或升级后失效，`ty` 不降级，而是报错退出。
  对策：照「包管理器」，路径运行时解析，不写死。

## 检查清单

改完代码后按此顺序执行，**不得跳过格式化那一步**。

- [ ] 依赖变动只经 micromamba（已确认环境名）或 uv，**没有用 pip**
- [ ] **包在本机缺失时也没有用 pip 兜底** —— 缺包就停下报告，
      没有 `pip install` / `python -m pip` / `pip` 塞进命令链
- [ ] 迭代用 `enumerate()` / `zip()`，没有 `range(len())`
- [ ] matplotlib 用 OO 接口 + `constrained_layout=True`
- [ ] 装饰用 `ax.set(...)`、spines 用列表一次性设置
- [ ] **新增内容里没有工具/环境的绝对路径，也没有写死的工具版本号**
- [ ] ① **已执行 `ruff format code python --exclude "*.ipynb"`**
      （输出应为 `left unchanged` 或已完成改写）
- [ ] ② `ruff check code python --no-fix --no-fix-only --exclude "*.ipynb"` 无输出
- [ ] ③ `micromamba run -n <选定的环境> ty check code python` 通过（或剩余项均为已记录的存根假警报）

### 运行带第三方依赖的 `.py` 时

- [ ] 已判定脚本确实含第三方 import（全标准库就直接跑）
- [ ] 已探测本机 micromamba / mamba 环境，并逐个确认依赖是否可导入
- [ ] **已用 `AskUserQuestion` 让用户选定环境，选项里带了「暂停，我自己装」**
- [ ] 在选定的那个环境里执行，**全程没有用 pip**（缺包就停下，不 pip 补）

### 删除死代码后

- [ ] 候选已逐条读过，`references/deadcode.md` 的盲区清单已核对（装饰器端点尤其）
- [ ] 删除区间已过 `ast.parse` 语法门，写回后文本不含 `\r`（行尾未被翻转）
- [ ] 删除处补回两行空行，没留下连续 3 行以上的空行
- [ ] ① `ruff format <路径> --exclude "*.ipynb"`
      ② `ruff check <路径> --no-fix --no-fix-only --exclude "*.ipynb"` 无输出
      （F401 会替你把失去消费者的 import 找出来）
- [ ] 复扫输出 `no unreferenced top-level definition found`

### 改动本技能自身时

- [ ] ① 已执行 `rumdl check --fix skills/project-py/`
- [ ] ② 已执行 `rumdl fmt skills/project-py/`
- [ ] ③ `rumdl check skills/project-py/` 无输出（或剩余项已逐条说明为何不改）
- [ ] ④ `python <this skill dir>/scripts/selfcheck.py` 退出码 0

## 参考文件

- `scripts/selfcheck.py` —— 本包自检（frontmatter 与目录名、`references/` 指针、
  命令 flag、禁止 `pip` 的语境）；只用标准库，离线一键跑通
- `scripts/deadcode_scan.py` —— 死代码扫描（只读）：顶层符号引用计数 + 装饰器例外 +
  连锁收敛；只用标准库，退出码 0/1/2
- `references/toolchain.md` —— ruff / ty / micromamba / rumdl 的用法、
  **路径现取方式**、**运行脚本时的环境探测与选择**、隔离 venv、缓存放雷
- `references/deadcode.md` —— 死代码清理流程：引用计数的安全边界、装饰器盲区、
  连锁收敛、删除时的行尾与空行处理、验证清单
