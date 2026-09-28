# 本机 Python 工具链速查（lectures 仓库）

以下命令均在本机实测通过。

## 先解析工具在哪（不要写死路径）

**本文件不记录任何工具或环境的绝对路径。** 路径因机器与安装方式
（scoop / conda / mise）而异，写死会在换机或升级后失效。
需要时**扫描 `PATH` 与环境变量**现取：

```bash
# 工具是否可用 / 装的哪个
command -v ruff ty micromamba uv

# micromamba 的根前缀（环境目录的父目录）
micromamba info | grep -iE "base|root prefix"

# 所有 conda 环境名 + 各自路径（路径由此推导，不要硬编码）
micromamba env list
micromamba run -n <env> python -c "import sys; print(sys.prefix)"
```

结论请**运行时现取**，不要从本文件抄。

> 名字与路径每次都用 `micromamba env list` 现取，再按下节的方式交给用户选定。

## 运行脚本：环境交给用户选，不自己挑

跑任何 import 越出标准库的 `.py` 之前，先探测、再询问：

```bash
# 1) 本机有哪些管理器（有的机器只有 micromamba，没有 mamba/conda）
command -v micromamba mamba conda

# 2) 环境清单 —— 名字与路径都从这里现取，不要凭印象写
micromamba env list

# 3) 逐个候选环境试探依赖（比只看 `env list` 有用得多：包在不在，一试便知）
micromamba run -n <env> python -c "import numpy, pandas"
```

再用 **`AskUserQuestion`** 把选择权交给用户：一个候选环境一项，`description`
写清路径与「已可导入 / 缺什么」，依赖齐全的排第一，`header` ≤ 12 字符
（例如 `运行环境`），并且**必留一项「暂停，我自己装」**。

选中暂停项就**报告探测结论并终止**——不要 pip、也不要换个环境硬跑。
**所有候选环境都缺依赖时同理**：这仍是「报告 + 停下」，不是解禁 pip 的开关。
选定之后，安装（若需要）与运行用同一个名字：

```bash
micromamba run -n <选定的环境> python <脚本>
```

## 检查流程

顺序固定：**format → check → ty**。改完代码不跑 format 就算没做完。

```bash
# 1) 格式化 —— 修改后第一步，必须执行（只动空白/换行，语义零风险）
ruff format code python --exclude "*.ipynb"
ruff format code python --check --exclude "*.ipynb"   # 复核用

# 2) lint —— 只看问题。根配置同时设了 fix 与 fix-only，只加 --no-fix 仍会改写文件，
#    必须两个都带才是唯一只读组合（实测：--no-fix-only 单独用也会落盘）
# 必须排除 notebook —— ruff 原生会解析 .ipynb，本技能范围仅 .py
ruff check code python --no-fix --no-fix-only --exclude "*.ipynb"

# 3) 类型检查 —— 必须借 micromamba 指定环境
micromamba run -n <选定的环境> ty check code python
```

### 为什么 format 必须排在最前

`ruff check --fix` 的自动修复（尤其 `UP` 类升级）常产出需要重新排版的代码，
先 format 可减少二次改动。曾经出现过的顺序错误是「只跑 check，看到
`All checks passed!` 就以为完事」——但 **check 通过不代表格式正确**，两者互不覆盖。

## Markdown 检查：rumdl

本技能的 `.md` 文件用 `rumdl`（Rust 写的 Markdown linter/formatter）把关。

```bash
# 0) 确认可用（在 PATH 中，勿写死路径）
command -v rumdl

# 1) 检查 + 自动修复（务必限定路径，否则会扫全仓）
rumdl check --fix skills/project-py/

# 2) 格式化
rumdl fmt skills/project-py/

# 3) 复核
rumdl check skills/project-py/
```

### 只读 vs 落盘

| 命令                        | 行为                                    |
| --------------------------- | --------------------------------------- |
| `rumdl check <path>`        | **只读**，只报问题                      |
| `rumdl check --fix <path>`  | **直接改写文件**                        |
| `rumdl check --diff <path>` | 只显示会改成什么，不落盘                |
| `rumdl fmt <path>`          | 格式化并落盘                            |
| `rumdl fmt --check <path>`  | 只判断「是否需要格式化」，需要则 exit 1 |

> `-f` 是 `--fix` 的短选项。**`rumdl check -f skills/project-py/` 会当场改文件**，
> 不是预演 —— 想预览请用 `--diff`。

### 改完必须核对范围

`--fix` / `fmt` 的**路径范围实测严格受限**：给了文件或目录，就只动那些路径下的文件。
真正危险的是**不给路径**（或给仓根）—— 那等于扫全仓，会改掉成片的无关 `.md`。

所以每次 `--fix` 或 `fmt` 之后照旧跑 `git status`，确认只有预期文件被改；多出来的立即还原：

```bash
git status --short            # 核对范围
git diff -- <path>            # 逐行审查
git checkout -- <path>        # 还原被顺手改掉的文件
```

**不要**在没核对范围的情况下接受 rumdl 的自动修复。

### 规则处理

`rumdl check` 输出形如 `<file>:<line>:<col>: [MD0xx] <描述>`，末尾汇总
`Run rumdl fmt to automatically fix N of the M issues` —— **N < M 时，剩下的要手改**。

本技能常见的不可自动修复项：

| 规则    | 含义             | 处理建议                                                                   |
| ------- | ---------------- | -------------------------------------------------------------------------- |
| `MD013` | 行超 80 字符     | 中文长句不要为凑行宽而硬断；必要时行内 `<!-- rumdl-disable-line MD013 -->` |
| `MD036` | 用粗体冒充标题   | 改成真正的 `####` 标题，或去掉加粗                                         |
| `MD028` | 引用块内出现空行 | 删掉块内空行，或拆成两个引用块                                             |

可自动修复的常见项（`rumdl fmt` 会处理，或 `check --fix`）：

- `MD031` —— 围栏代码块前后缺空行
- `MD040` —— 围栏代码块缺语言标注（会补成 ` ```text `）

### 仓库根 `.rumdl.toml`

改这份配置前先评估影响面：`rumdl check .` 看全仓，`rumdl check --no-config <path>`
对比「不开配置」时的结果。

> ⚠️ **`rumdl check` 会自动向上查找最近的 `.rumdl.toml`**，所以在仓库任意子目录
> 运行时，这份根配置都生效 —— 排查「为什么没报某条」时先想到它。

## 根配置里的 `disable` 必须是成文的放宽

为了单次任务往根配置里加 `disable` 不行 —— 那既没写理由，也没人复核。需要针对性豁免时用行内注释：

```markdown
<!-- rumdl-disable-next-line MD013 -->
这一行可以很长……
```

## 为什么 ty 必须借 `micromamba run`

`ty` 需要一个**装有依赖**的解释器来解析导入。裸跑会让它拿系统 Python 当检查环境，
于普通项目刷出大量 `unresolved-import` **假警报**。

- 推荐：`micromamba run -n <env> ty check .`（ty 能识别被激活的 conda 环境）
- 等价：`ty check --python <env 的 sys.prefix> .`
  （prefix 用上面 `micromamba run` 现取）
- **不要**把环境绝对路径写进 `ty.toml`：路径失效时 ty 会以
  `Invalid environment.python setting` 直接失败（exit 2），比不配置更糟。
  同理**不要在文档、脚本、配置里写死解释器路径**。

## `ty` 默认尊重 `.gitignore`，可让 `src.include` 静默失效

ignore 过滤只作用于**配置里的 `include`**，且默认开启。于是只要某个已在
`[tool.ty.src] include` 声明的目录同时被 `.gitignore` 忽略（常见：`tests/`），
ty 就跳过它且**不报任何提示** —— `ty check` 照旧打印 `All checks passed!`，
那道门其实从没检查过那些文件。

实测（ty 0.0.78，一个 17 + 11 个 `.py` 的项目）：`include = ["pkg", "tests"]`
且 `tests` 被 gitignore 时，无参 `ty check` 只索引 `pkg` 的 17 个文件；
显式传目录 `ty check pkg tests` 才覆盖；在 `[tool.ty.src]` 加
`respect-ignore-files = false` 后，无参调用同样覆盖 28 个文件。

对策二选一：

- 在 `[tool.ty.src]` 写 `respect-ignore-files = false`，让声明的 `include`
  真正生效 —— CLI 与编辑器行为一致，推荐；
- 或在调用处**显式传目录**：显式路径不受 ignore 过滤。

自查一行：`ty check -v` 的 `Indexed N file(s)` 是否等于该范围 `.py` 的实际总数。

## 独立脚本的 Python（非 conda 场景）

需要 numpy + Pillow 的脚本（如 `code/slide_qa.py`）跑在一个隔离环境里。
**不要抄路径**，用 `<托管 Python> -m venv <venv 目录>` 的方式现建现用，
或按运行时环境变量（如 `WORKBUDDY_*` / `VIRTUAL_ENV`）解析；解析不到就现建：

```bash
python -m venv .venv && .venv/Scripts/python <脚本>
```

**建环境可以，往里装包不行 —— 除非装包走的是 SKILL.md「包管理器」的合法入口。**
`pip` 在任何场景下都不是选项，**包括「这个包本机哪个环境都没有」的时候**：

| 情形 | 做法 |
| --- | --- |
| conda 环境里已有该包 | 直接用：`micromamba run -n <env> python <脚本>` |
| conda 环境缺该包，用户选 conda | `micromamba install -n <用户给的环境名> -c conda-forge <pkg> -y` |
| 非 conda 场景，用户选 uv | `uv run --with <pkg> <脚本>`（临时环境，不需要 pyproject），或 `uv add` / `uv sync` 进项目 |
| **两条路都不通，或用户选了「暂停」** | **停下报告缺什么，交给用户；`pip` 不是兜底** |

禁止的形式（不限于）：`pip install` / `pip uninstall`、`python -m pip`、
`.venv/Scripts/python -m pip install ...`、`uv pip ...`，
以及把 `pip` 塞进 `&&` 链或脚本里顺手执行。想从结构上断掉后路，
建一次性环境时用 `python -m venv --without-pip .venv`。

## 常见 ty 假警报：OpenCV 存根

`cv2.imread()` 返回 `MatLike | None`，而 ty 把 `MatLike` 解析成
`ndarray[Any, dtype[integer[Any] | floating[Any]]]`。该 union 不匹配「仅数组」重载，
于是 `cvtColor` / `Canny` / `erode` / `threshold` / `Sobel` 等**所有下游调用**一起报
`no-matching-overload` —— 一处没 annotate 能连坐十几个错误。

修复点在**赋值处**：

```python
img = cast("cv2.typing.MatLike", cv2.imread("img/lena.png"))
```

四种写法的实测结果：

| 写法                                              | 结果                                           |
| ------------------------------------------------- | ---------------------------------------------- |
| `x: cv2.typing.MatLike = cv2.imread(...)`         | ✗ `invalid-assignment`（union 不可赋给非可选） |
| `x: np.ndarray = cv2.imread(...)`                 | ✗ `invalid-assignment`（同上）                 |
| `x = cast("cv2.typing.MatLike", cv2.imread(...))` | ✓                                              |
| `x = cv2.imread(...)` + `assert x is not None`    | ✓                                              |

**`# ty: ignore[code]` 的落点**：必须落在**报错的那一行**。
多行调用写在调用首行（含 `(` 的那行）；列表里的元素写在**元素行**，
不能写在列表首行（否则报 `unused-ignore-comment`）。

> `cv2.normalize(src, None, ...)` 的 `dst=None`、`cv2.multiply(mat, 2)` 的标量操作数
> 确属存根签名缺陷，用行内 ignore 合理；能 cast 解决的优先 cast，不要滥用 ignore。

## conda 包损坏的识别与修复

本机 conda 包缓存出现过**解包不完整**：`conda-meta/*.json` 记录了文件清单，
但 `site-packages/` 下对应目录是空的或缺失。症状是 `ModuleNotFoundError`，
而 `micromamba list` 显示包「已安装」。

已踩过：`cv2` / `ipykernel` / `IPython` / `comm` / `attrs` / `idna`。

排查与修复（环境前缀与包缓存目录**现取**，不硬编码）：

```bash
# 0) 现取该环境的 prefix 与 pkgs 缓存目录
PREFIX=$(micromamba run -n <env> python -c "import sys; print(sys.prefix)")
PKGS=$(micromamba info | grep -i "pkgs dir" | awk '{print $NF}')

# 1) 看包是否真的落盘
ls "$PREFIX/Lib/site-packages/<pkg>"

# 2) 清掉损坏的缓存条目 + 强制重装（必要时直连官方源，绕开镜像 502）
rm -rf "$PKGS/<pkg>-<ver>-<build>"
micromamba install -n <env> \
  --override-channels -c https://conda.anaconda.org/conda-forge \
  --force-reinstall <pkg> -y
```

若报 `Cannot find a valid extracted directory cache for '<pkg>.conda'`，
就是缓存条目损坏 —— 删掉那个 pkgs 子目录再装即可。

## 仓库当前忽略规则（`.gitignore` 相关）

- `images/`、`output/`、`post/`、`.workbuddy`、`.build`、`*.pdf` 等不入库。

## 批量 lint 修复的安全流程（可复用）

1. `cp -r` 到临时目录，先跑 `--unsafe-fixes` **预演**
2. 逐类审查改动性质
3. **只对无损规则**用 `--select` 放行到正式目录
4. `ruff format` 收尾（UP 类转换后常需二次格式化）
