# 高频 Typst 写法与排雷

## 文件形态

### 幻灯片 deck

```typst
#import "lib/lib.typ": *

#show: touying-quick.with(
  title: "环境搭建",
  info: info-cv,
  bgimg: bghexagon,
)

# 可选：把无关章节片段拉进来
#include "z-Python环境搭建.typ"

= OpenCV            // 一级标题 → 章节扉页（自动成页）
== 简介             // 二级标题 → 内容页（自动成页）
=== 中缀到后缀      // 三级标题 → 拆页
```

`= / ==` 各自自动成页，**不需要写 `#pagebreak()`**。
唯一需要显式拆页的情况是一个 `==` 装不下、要拆成多个 `===` 时。

### 章节片段

无任何文件头，首行直接是 `= 标题`。被 deck 用 `#include` 拉入，继承 deck 的样式。
`z-` 前缀的文件都是这种片段（`z-Python环境搭建.typ`、`z-个人介绍.typ`、`z-Python学习.typ`）。

### 书稿（qooklet `chapter-style`）

同模板体系的 `book-*` 系列是**书稿**：每章一个 `.typ`，入口用 `#cover` / `#contents` /
`#chapter` 串起来，章首换样式：

```typst
#import "lib/lib.typ": *
#show: chapter-style.with(title: "概率与计数", info: info)   // 附录改 appendix-style
```

资源五条硬规则、两种分栏块的判据、`#colbreak()` 的用法**全部照用**。三处与 deck 不同：

- **字号不写。** 正文由 qooklet 的 `styles.sizes.context` 统一下发，`#columns()[]` 块首**一律不写**
  `#set text(size: …)` —— **任何数值都不写**，不只是 deck 的 `18pt`。补 `18pt` 会把正文放大一圈、
  串到同页其他内容；补一个「和正文一样」的字面值（书稿实测 `10.5pt`）同样是错的：`styles.toml`
  一改字号，写死的块就不跟。要每栏不同字号时才在**那一栏的 `#[ … ]` 里**覆盖。
- **分栏点手工定。** 书稿里没有「先跑脚本实测分栏点」这条路，「批量摘壳」的两条跳过判据只服务于
  课件仓库的批处理。手工摘壳时按语义把同级列表项对半分，n 栏补 n−1 个 `#colbreak()`；
  两种分法一样匀时**照原版的分栏点**。

量化位移影响面有个省事的办法：前后两版都渲染成 PNG 序列，**逐页比文件字节**。第 k 页起字节完全相同，
说明摘壳造成的位移只影响块所在的那一两页。

## 控制单页容量

decks 里最常出问题的是**内容撑破 16:9 单页**。惯用手法：

```typst
// 分栏（columns() 默认两栏）—— 块首默认 18pt，栏与栏之间必须显式 #colbreak()
#columns()[
  #set text(size: 18pt)
  左栏内容
  #colbreak()
  右栏内容
]

// 单栏限高
#block(height: 15em)[
  #set text(size: 18pt)
  ...
]
```

- 分栏块**不套 `block(height: …)`**：高度由内容决定，换栏靠 `#colbreak()`；
  `columns(n)` 就要 n−1 个 `#colbreak()`。
- **例外**：块里有有序列表（`+` / `1.`）时，保留 `#block(height: 18em, columns()[…])`，
  且块内**不写 `#colbreak()`** —— 分栏交给块高，两者混用断点会有两个来源。
  `#colbreak()` 还会截断有序列表、右栏编号从 `1.` 重来；靠块高隐式分栏则编号连续（见 SKILL.md「分栏块」）。
- 移除固定高度后每页不再预留那段空白，版面会变紧凑、分页点前移，页数可能变化 ——
  用 `code/deck_pages.py render` / `diff` 前后逐页比像素确认。
- 字号在块内**显式**`#set text(size: ...)`，**分栏块首行默认 `18pt`**（正文档）。常用档位：
  18pt（正文）、16–17pt（较密）、12–15pt（含代码）、10–11pt（长代码）；某栏要别的字号，
  就写在该栏的 `#[ … ]` 里，别改块首那一行。
- `columns()` 不带参数即两栏；栏宽按 `columns(n, gutter: g)` 现算，别拿两栏的宽度去量三栏块。

容量实测（16:9 deck，正文区约 19–20em，半栏宽约 300pt）：

- `#set text(size: 16pt)` 的半栏，装得下 **8–10 行正文 + 一条提示框**；
  想再塞一张 `height: 40%` 的图，正文要收到 5–6 行，且图只能独占一栏。
- `#set text(size: 11–12pt)` 包住的代码块，半栏能放 **20–26 行**；10pt 可到 30 行上下。
  配置类 JSON 一律按这个档位包一层 `#[ #set text(size: 11pt) … ]`，否则二十几行必然拆页。
- `tableq(data, 3)` 放进**半栏**时，若有一列是 `publisher.extension-id` 这种长 ID：
  等宽字宽约 0.6em，半栏 300pt 换算下来**字号必须压到 12–13pt** 才不折行；
  长 ID 列更建议整页全宽放。

### 用墨水密度定位残页

页数比预期多，说明有页被自动拆开；而拆出来的那页常常只有两三行，翻缩略图容易看漏。
按「墨水密度」（正文区暗像素占比）扫一遍，一眼就能找出来：

```python
from PIL import Image
import glob, numpy as np
for f in sorted(glob.glob('C:/Users/me/AppData/Local/Temp/render/*.png')):
    a = np.asarray(Image.open(f).convert('L')); h, w = a.shape
    ink = (a[70:h-40, :] < 170).mean()          # 掐掉页眉与页脚
    if ink < 0.013:
        print(f[-6:-4], round(ink, 4))
```

判读：正常内容页 0.03–0.07，章节扉页与结束页约 0.008，**拆出来的残页落在 0.002–0.012**
——所以「低墨水页的个数」应当正好等于「章节扉页数 + 结束页数」，多出来的就是残页。
（含大图的页会到 0.2 以上，不干扰判断。）

### 批量摘壳（旧写法 → 裸 `columns()`）

存量课件从 `#block(height: …, columns()[…])` 摘掉外层，工具在 `code/`：

```bash
python code/measure_columns_split.py                                         # 实测各块分栏点 -> .tmp/columns-split.json
python code/unwrap_columns_block.py --splits .tmp/columns-split.json         # 干跑
python code/unwrap_columns_block.py --splits .tmp/columns-split.json --apply # 落盘
```

- **含有序列表的块一律跳过**（判据：块内出现 `+` / `1.` 开头的列表项）—— 见上面的「例外」。
- 不给 `--splits` 时，**没有 `#colbreak()` 的块一律跳过**：固定高度是它唯一的分栏依据，
  摘壳会整块塌进第一栏，比不改更糟。
- 分栏点不是猜的：按「可分页单元」切分正文（空行分段、段落若为列表再按同级列表项拆细），
  量每个前缀在**该块自己的单栏宽**下的自然高度，取第一个装不下的单元。
- 这套脚本只存在于课件仓库。书稿（`book-*`）没有它们，摘壳是手工的 —— 见上「书稿（qooklet `chapter-style`）」。

## 表格

```typst
// 1) 外部 CSV + 三线表（首选）
#let data = csv("data/algo-expr.csv")
#figure(
  tableq(data, 3),
  caption: "运算表达式",
)

// 2) 手写三线表
#figure(
  table(
    columns: (auto,) * 3,
    inset: 0.3em,
    align: center + horizon,
    stroke: table-three-line(rgb("000")),
    [触发键], [功能键], [作用],
    [中键], [], [旋转视图 Orbit],
  ),
  caption: none,
)

// 3) xlsx（仅多 sheet / 公式时）
#figure(
  xlsx-parser(
    read("data/ml-melon-hypo.xlsx", encoding: none),
    parse-table-style: false,
    parse-stroke: false,
    stroke: table-three-line(rgb("000")),
  ),
  caption: "",
)
```

`encoding: none` 是必需的 —— 否则二进制会被当文本解码。

`csv()` 返回「行数组」，每个元素是「列数组」，与 `tableq` 的 `data` 参数形状一致，
用 `..data.flatten()` 展开成 table 的位置参数。

**列宽一律 `columns: (auto,) * n`，n 取实际列数。** 三种写法里只有这一种同时满足两条要求：
`(1fr,) * n` 把表拉满正文宽（短表变空荡，长列被硬撑、词距被拉开），`pt` 定宽是从 Excel 列宽换算来的
魔数、换字号即失准；`auto` 让每列按内容收缩、整表居中，与 `xlsx-parser` 出来的窄表视觉一致。
`columns: n`（int 形式）在 typst 里与 `(auto,) * n` 完全等价，但按约定仍写数组形式 —— 一眼看得见列数。

**n 必须是实际列数。** 轨数少于单元格数时 typst 按轨数循环折行且**不报错**：五列表写成 `* 4`，
10 个单元格会被折成「4 + 4 + 2」三行，只能靠肉眼发现。五列就写 `* 5`。

单元格内容走 markup 解析，`*` 与 `'` 要转义（`/g/\*`、`I\'ll`）；`table.cell(colspan: 2)[…]` 可跨列。

## 代码块

````typst
// 外部文件（首选）
#code(read("blender/v04_blender_morphology.py"))

// 短片段直接写围栏
```python
img = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel)
```
````

围栏代码块由 codly 接管，**自带行号**。局部关掉行号：

````typst
#no-codly[
```python
x = 1
```
]
````

deck 里出现 `raw` 块时，`code-block-style` 已经把它调成灰底圆角。

## 数学

```typst
$ y = w^T x + b $

$
  V(s) = max_a sum_(s') P(s' | s, a) (R(s, a, s') + gamma V(s'))
$ <bellman>

见 @bellman
```

- **CJK 进公式**用 `ctext("...")`：`(ctext("色泽")=? ) ∩ (ctext("根蒂")=? )`。
- 带 label 的公式自动获得 `(章节号.序号)` 编号。
- 多行/分段用 `cases`、`&` 对齐。

## 键位与提示框

```typst
#kbd("Ctrl")            // 来自 keyle

#note[一句话提示]
#warning[危险]
#caution[注意]
#quote[...]
```

`#tip[...]` **默认这样写**——外包 `#[ … ]`、块首 `#set text(size: 14pt)`，不要裸写 `#tip[...]`：

```typst
#[
  #set text(size: 14pt)
  #tip[
    ...
  ]
]
```

`note[...]` 常用于页面底部给一句总结，外面可再包一层字号设置：

```typst
#[
  #set text(size: 15pt)
  #note[
    一句话：`ops` 是「用户动作」，`data` 是「文件内容」，`context` 是「你此刻站在哪儿」。
  ]
]
```

## 图片

```typst
#figure(
  image("images/blender-ui.png", height: 50%),
  caption: none,
)
```

- 一律 `figure` 包 `image`，默认 `caption: none`。
- **`height` / `width` 只写百分数**，不写 `pt` / `em` / `cm` / `mm` / `in` / `auto`。
- 百分比相对**所在容器**解析：分栏块高度是 auto，基准变成页面（栏）内可用高度，
  数值要按新基准重新标定，别照抄旧百分比；想让图占满一栏就给 `width: N%`（见「控制单页容量」）。
- 需要并排两张图时用 `columns()` 或 `subpar` 的 `sgrid`。

---

## 排雷清单（踩过的坑）

### 幽灵逗号

内容块 `[...]` 内的**可见逗号**会被解析成参数分隔符：

```typst
// ✗ 错
#note[第一句，第二句。],
// ✓ 对
#note[第一句，第二句。]
```

### 双反引号 raw

`` `` ` `` `` 这种双反引号 raw 极易造成定界符错配，**吞掉后续整段文本和标题**。
需要展示反引号时用单反引号或纯文字描述。

### raw 围栏必须配对

``` 与 ```` 嵌套要成对，否则级联破坏整个文件的解析。
在 Typst 里演示 Markdown 代码块时，内层用 `` ```text `` 而不是 `` ```markdown ``（避免 `**` 被染色）。

### 超高 slide 被自动拆页

Touying 检测到内容超过一页会**自动拆成两页**，留下半页空白。
解法：精简内容、拆成多个 `===`，或者改成分栏版式（`columns()` + `#colbreak()`）。
**不要再靠 `block(height: …)` 把内容硬压进一页** —— 固定高度只是把溢出的部分藏起来。
（唯一的正当用法是分栏块里带有序列表，见「控制单页容量」。）

### 路径基准是仓库根

`read()` / `image()` / `csv()` 的路径都相对**仓库根**，不相对当前 `.typ`。
deck 在根目录，所以看起来像相对路径；但如果把 `.typ` 移进子目录就会全部断链。

### 编译必须带字体路径

```bash
typst compile --font-path "C:/Windows/Fonts" v01-环境搭建.typ
```

否则中文缺字（显示为空白或豆腐块）。`code/slide_qa.py` 已经把 `--font-path` 内置了。

### 本机 shell 工具注意

- `find` / `grep` 是 scoop shim 版（BusyBox），行为与 GNU 不同：
  `grep` **不支持 `--include`**，`find -name` 报参数格式错误。
  → 搜文件/内容一律用编辑器的 Glob / Grep 工具。
- 不要用 shell `diff` 比对含中文的混合编码文件：遇到 GBK 字节会抛
  `UnicodeDecodeError` 并**静默返回 "(no diff)"**（假阴性）。
  → 用 Python `difflib` + 编码回退（`utf-8-sig` → `utf-8` → `gbk` → `latin-1`）。

### Git Bash 的 `/tmp` 与 Windows Python 对不上

现象：`typst compile … "/tmp/r/{0p}.png"` 编译成功，shell 里 `ls /tmp/r` 也能列出一堆 PNG，
可 Python 的 `glob.glob('/tmp/r/*.png')` 返回**空列表**。

原因：Git Bash 把 `/tmp` 映射到 `C:/Users/<user>/AppData/Local/Temp`（`cd /tmp/r && pwd -W` 可验证），
而 Windows 原生 Python 把 `/tmp/r` 当成「当前盘符根目录下的 `tmp\r`」，自然找不到。
typst 是原生程序，但它收的是 Git Bash 转换后的路径，所以能写进去。

→ 渲染目录直接写 Windows 路径（`C:/Users/<user>/AppData/Local/Temp/r`），
或先 `pwd -W` 拿到真实路径再交给 Python。

### 导出的 PNG 序列

`typst compile` 要导出多页图时必须带页码模板：

```bash
typst compile --font-path "C:/Windows/Fonts" --ppi 100 v05-几何变换.typ "output/{0p}.png"
```

缺 `{0p}` 会报 *cannot export multiple images without a page number template*。

### 包目录与命名空间

`lib/lib.typ` 里的 `#import "@preview/qooklet:0.7.2": *` 这类引用，按
`{数据目录}/typst/packages/{命名空间}/{包名}/{版本}` 逐级解析，**本地有就不走网络**。
Windows 上两个根不是同一个：

| 命名空间   | 位置（Windows）                                 | 来历                      |
| ---------- | ----------------------------------------------- | ------------------------- |
| `@preview` | `%LOCALAPPDATA%\typst\packages\preview\`        | 首次用到时自动下载的缓存  |
| `@local`   | `%APPDATA%\typst\packages\local\<名字>\<版本>\` | 手放或 git clone 的开发版 |

要改包源码来调试，就把仓库 clone 进 `local` 下的 `<名字>\`，版本号那一层留作包体，
用 `@local/<名字>:<版本>` 引入——改完即生效，不必等发布。
「找不到包」「改了半天没生效」先看这两处，而不是翻 `lib.typ`。
