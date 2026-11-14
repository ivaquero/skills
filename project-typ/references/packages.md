# 本地 Typst 包能力清单

## 包的位置

```text
Windows : %APPDATA%\typst\packages\local     （本地开发版，可直接改写）
          %APPDATA%\typst\packages\preview   （已发布版，只读参考）
macOS   : ~/Library/Application Support/typst/packages/{local,preview}
```

**两个目录都要查。** `local` 里通常只有一两个版本；`preview` 里是该包全部历史版本。
查某个函数是否存在时，优先看 `local` 里最新版本的 `src/utils.typ`。

`local` 下的包会**遮蔽**同名 `preview` 包，所以本地开发改动会立刻在课件里生效。

```bash
ls "$env:APPDATA/typst/packages/local"      # 本地开发版（可改写、优先看这个）
ls "$env:APPDATA/typst/packages/preview"    # 已发布版
```

课件里最常用的只有五个：`tableq()`、`code()`、`ctext()`、提示框（`tip` / `note` / `quote` /
`warning` / `caution`）、`touying-quick.with(...)` —— 其余符号要用到时再往下查。

## qooklet —— 书籍/讲义模板

位置：`packages/local/qooklet/<version>/`，entrypoint `src/lib.typ`。

导出（`src/lib.typ` 全量 re-export）：

| 来源                | 符号                                                                                                                                         |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `cover.typ`         | `cover`、`cover-style`、`epigraph`                                                                                                           |
| `front-matters.typ` | `front-matter-style`、`part-page`、`preface`                                                                                                 |
| `contents.typ`      | `contents`、`contents-style`                                                                                                                 |
| `chapters.typ`      | `appendix`、`appendix-style`、`chapter`、`chapter-img`、`chapter-style`                                                                      |
| `referable.typ`     | `equation-numbering`、`equation-numbering-style`、`ref-style`、`figure-supplement-style`、`code-block-style`、`bibx`、`heading-numbering-at` |
| `utils.typ`         | `ctext`、`table-three-line`、`table-no-left-right`、`tableq`、`code`                                                                         |

### 常用函数签名

```typst
// 三线表：data 是由「行」组成的数组（csv() 的返回值正好是这个形状）
#tableq(data, k, inset: 0.3em, stroke-color: rgb("000"))

// 三线表描边规则，可单独取出用于手写 table()
#table(columns: 3, stroke: table-three-line(rgb("000")), ...)

// 左右不封边的网格描边
table-no-left-right(stroke-color)

// CJK 文本嵌进数学式（lib 里也导出了一版更简单的实现）
#ctext("色泽")

// 灰底圆角代码块
#code(text, lang: "python", breakable: true, width: 100%)
```

### 的配置模型（与旧版差异大，注意）

`src/config/styles.toml` 结构：

```toml
font-platform = "windows"      # 可用 --input qooklet-font-platform=macos 覆盖

[paper]   note = "a4"  booklet = "iso-b5"
[spaces]  par-indent / par-leading / par-spacing / list-indent / block-above / block-below / contents-indent
[sizes]   chapter / chapter-index / cover / author / date / epigraph / preface / contents / part
          heading-1..4 / context / header / footer
[font-roles.en]  chapter = "display"  context = "text"  math = "latin" ...
[font-roles.zh]  chapter = "cjk-kai"  context = "cjk-song"  math = "cjk-kai" ...
[font-roles.cjk-latin]  default = "latin"
[fonts.macos]  display = "Palatino"  text = "Georgia"  latin = "Times New Roman"
               cjk-kai = "Kaiti SC"  cjk-song = "Songti SC"
[fonts.windows]  （同结构，字体族换成 Windows 字体名）
```

`deps.typ` 提供 `cjk-latin-style(body, font:, styles:, lang:, role:, as-style:, ..options)`，
按 `font-roles` + `fonts` 两级查表选字体；`latin-coverage()` 是一个正则，
用来把中文字体里的拉丁字符**单独**换成拉丁字体。

### 章节级排版的入口

```typst
// 完整排版 + 引用格式化（最常用）
#show: chapter.with(title: "第一章", info: info, styles: default-styles)

// 只做章节扉页，不接管 heading/figure 样式
#show: chapter-style.with(title: "...", full-style: false)

// 附录
#show: appendix.with(title: "附录 A")

// 封面 / 题记 / 前置页
#cover(info)
#epigraph[引文]
#part-page(title)
#preface[...]
```

`chapter-style` 关键参数：`heading-depth`（1/2/3）、`prefix`（`"chapter"`/`"appendix"`）、
`outline-on`（是否插目录）、`format-refs`（是否规范引用格式）、`full-style`。

---

## touying-quick —— 幻灯片模板

位置：`packages/local/touying-quick/<version>/`，entrypoint `src/lib.typ`。

### 主入口

```typst
#show: touying-quick.with(
  title: "标题",
  subtitle: "",              // 留空则用 info.series
  heading-idx: true,         // 是否给 heading 加 "1.1." 编号
  bgimg: bgsky,              // bgsky | bghexagon | bgbook | bgyellowish
  theme: "blue",             // blue | red | green
  info: default-info,        // 见下
  styles: default-styles,
  names: default-names,
  logo: emoji.bookmark,
  supplement: [],            // 结束页之后的补充页
  lang: none,                // 默认取 info.lang
)
```

它自动产出：标题页 → 目录（depth 1）→ 正文 → 结束页（`info.ending`）。

### `src/config/info.toml` 的字段

```toml
[default]  # 或任意自定义节名
footer = "..."          # 每页页脚文字
header = "..."          # 页眉（touying-quick 未直接使用，qooklet 会用）
lang = "zh"             # "zh" | "en"
author = "主讲：..."
series = "系列课程名"    # subtitle 留空时顶上
institution = "..."
ending = "谢谢大家"      # 结束页大字
```

### `bgimg` 常量

`bgsky`、`bghexagon`、`bgbook`、`bgyellowish` —— 对应 `src/config/*.png`。

### `src/config/styles.toml` 的颜色主题

`[colors.blue]` / `[colors.red]` / `[colors.green]`，每节含 20 个键：
`primary`、`primary-light`、`primary-lightest`、`primary-dark`、`primary-darkest`，
`secondary`*、`tertiary`*、`neutral`* 同构。

映射到 touying 的 `config-colors()`：`primary` → heading level 1，`secondary` → level 2，
`tertiary` → level 3，`neutral` → 正文。

### `utils.typ` 导出（与 qooklet 的同名，deck 场景生效）

```typst
#ctext(label, size: .8em, font: <zh 数学字体>, ..options)
#tableq(data, k, inset: 0.3em, stroke-color: rgb("000"))
#table-three-line(color) / #table-no-left-right(color)
#code(text, lang: "python", breakable: true, width: 100%)

// 提示框（本体来自 theorion）
#tip[...]  #note[...]  #quote[...]  #warning[...]  #caution[...]
```

### `referable.typ` —— 公式编号与引用

公式编号格式为 `(章节号.序号)`，序号**在同一 level-1 标题内重新计数**。

```typst
$
  V(s) = max_a sum_(s') P(s' | s, a) (R(s, a, s') + gamma V(s'))
$ <bellman>

见 @bellman
```

`code-block-style(body)` 会把 `codly` 初始化好（`display-name: false`、灰底、无斑马纹），
所以 deck 里直接用 ``` 围栏代码块也会带行号。

`bibx(bib, main: false)` 用于参考文献：同一文档里只有第一处 `bibx` 或标记 `main: true` 的那处会渲染。

---

## theorion —— 提示框本体

由 touying-quick 的 `deps.typ` 转发：

```typst
#import "@preview/theorion:0.6.0": *
```

在 `touying-quick` 里通过 `show: show-theorion` 挂上默认样式，
所以 deck 中直接写 `#note[...]` / `#warning[...]` 即可。

`set-theorion-numbering("A.1")` 可改编号格式（qooklet 的 appendix 会调用）。

---

## 其它被使用的 preview 包

`lib/lib.typ` 中已引入，无需重复 import：

| 包                        | 用途      | 使用到的符号                |
| ------------------------- | --------- | --------------------------- |
| `@preview/rexllent:0.4.1` | xlsx 解析 | `xlsx-parser`               |
| `@preview/physica:0.9.8`  | 物理符号  | `*`                         |
| `@preview/subpar:0.2.2`   | 子图      | `grid as sgrid`             |
| `@preview/sicons:16.0.0`  | 图标      | `sicon-label`               |
| `@preview/shadowed:0.3.0` | 阴影盒    | `shadow`                    |
| `@preview/algo:0.3.6`     | 伪代码    | `algo`、`comment`、`d`、`i` |
| `@preview/pinit:0.2.2`    | 元素标注  | `pin`、`pinit-point-from`   |
| `@preview/keyle:0.4.0`    | 键位      | `kbd`                       |
| `@preview/phonokit:0.8.0` | 语音图表  | `phonokit-init`、`consonants`、`vowels`、`sound-shift`、`vot` |

`phonokit` 的 `vowels()` 有两处硬缺陷——`/ʌ/`–`/ɔ/`、`/ɑ/`–`/ɒ/` 同点异圆唇会被画成一个黑点，
且不认长音 `ː`——所以元音舌位图要自绘。四张图（辅音表 / 元音舌位 / 声变 / VOT）各用哪个函数、
必须绕开的坑，见 `phonokit.md`。

`lib/ml.typ` 另有（按需单独 import）：`lilaq`、`suiji`、`tiptoe`、`cetz`、`cetz-venn`、`fletcher`。
