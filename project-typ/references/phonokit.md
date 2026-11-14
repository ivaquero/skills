# phonokit：Typst 里的语音图表

**版本**：`@preview/phonokit:0.8.0`。源码在 `%APPDATA%/typst/packages/preview/phonokit/0.8.0`
（Linux/macOS 是 `~/.cache/typst` 或 `~/Library/Caches/typst`），查导出符号直接看 `lib.typ`。

**一句话立场**：这个包只能信一半 —— 辅音表、声变折线、VOT 时间线是现成的、值得用；
元音图和坐标轴必须自己画，硬套 `vowels()` 会被两个设计缺陷卡死。

| 图形 | 用什么 | 理由 |
| --- | --- | --- |
| 辅音发音部位·方式表 | `consonants()` | 清浊双格、无对应音素的灰格、自动裁掉该语言不用的行列，全是现成的 |
| 元音舌位梯形 | **自绘 CeTZ** | `vowels()` 有两条硬缺陷，见下 |
| 历时声变 / 元音推移 | `sound-shift()` | 节点定坐标 + 折线 + **按标签墨迹自动断线**，正是「符号压在线上」想要的效果 |
| 清浊塞音 VOT 时间线 | `vot()` | 唯一现成实现，可调项够用，但四条硬约束必须绕 |

## 先做字体初始化

`phonokit-init(font: ...)` 改的是**全局 state**，默认值 `"Charis"`。大多数机器上没有 Charis，
未初始化就直接调用 `consonants()` / `vot()` / `sound-shift()`，编译虽通过但刷一片
`warning: unknown font family: charis`，且 IPA 符号落到回退字体上。

```typ
#import "@preview/phonokit:0.8.0": *
#phonokit-init(font: "Libertinus Serif")
```

**放在 `#import` 之后、`#show: ...` 之前**，全篇一次即可。反过来，若某个章节文件独立编译，
它自己也得有这一行 —— 仓库根 `lib/lib.typ` 转出了 phonokit 的符号，但**不会替章节做 init**。

## `consonants()`：辅音表

```typ
consonants("english", affricates: true, simplify: true, label-width: 2.9)
```

- `affricates: true` 才有破擦音两行；`simplify: true` 裁掉英语不用的部位/方式行列。
  英语共 24 个辅音音位（含 2 个破擦音），`tr` `dr` `ts` `dz` 不在其列 —— 它们是辅音连缀。
- **`label-width` 默认 3.5，偏宽**：左侧方法名最长的是 `Lateral approximant`（实测 2.06 cm），
  3.5 会在左边留 0.6 cm 空白。给 **2.9** 正好。
- 该表按音位排，`/r/` 严格记法是 `/ɹ/`，与表里的 `r` 同指一个音 —— 正文要补一句，否则读者对不上。
- 表格本身也要 `pad(x:, y:)` 包一层再入版：它的包围盒按格线算，`label-width` 的文字与
  末列的边框会顶到版心边线。

## 元音舌位图：不要用 `vowels()`

两条硬理由，实测都会让图无法入书：

1. `vowel-data` 里 `/ʌ/` 与 `/ɔ/`、`/ɑ/` 与 `/ɒ/` **正好同点、只差 `rounded`**，
   `vowels()` 把它们当最小对立对，**在两点中间画一个黑点**（渲染出来是「ʌ • ɔ」）。
2. 不认长音符号 `ː`：`iː uː ɑː ɔː ɜː` 全部丢成 `i u ɑ ɔ ɜ`，英语元音图就废了。

所以元音图自绘（CeTZ 画梯形 + 手工坐标表）。自绘时的四个坑：

- **长度不能乘长度**。画布坐标全程先 `/ 1cm` 化成数值算完，最后 `* 1cm` 转回；
  直接 `1.2cm * 0.3` 会报 `cannot multiply length with length`。
- **建一个 `at(x, y)` 插值函数**，把归一化坐标按当行左右边界线性映射到梯形内，
  坐标表就能写成 `(label, x, y, dx, dy)` 直接对拍 IPA 元音图，改一个数只动一行。
- **画布 bbox 不含外溢标签**。角落符号（`iː` `uː` `ɑː`）往外推之后，`cetz.canvas` 的包围盒
  仍按梯形算，直接入版会压住后文 —— 必须 `pad(x: ..., y: ...)` 留余量。
- **角落标签的两种模式不能用「偏移取反」实现**。单图把 `iː/uː/ɑː` 推到框外（`outside`），
  两图并排时必须收进框内（`inside`），否则左图的 `uː` 与右图的 `i` 在栏间相撞，`pad` 再大也压不住。
  但 `inside` 若靠把 `dy` 取反来做，`ɑː`（dy −0.10，向下推）会变成上移，
  与 `ɒ`（dy +0.09）**交换上下位置**，图上开元音 `ɑː` 跑到 `ɒ` 上面。
  正确做法：按归一化坐标判角（到四顶点距离 < 0.15 才算角落符号），命中者坐标内收固定比例（5.5%）
  且不带偏移，其余符号的微调量原样保留。

参考实现（langs 仓库）：`lib/en-phonemes.typ` 的 `vowel-space()` / `vowel-space-pair()`。
`ɛ`/`e` 这类贴左边斜边的符号，x 要给到 0.16 左右（0.10 会压线），但别越过 `ɪ`（0.12），否则破坏舌位次序。

## `sound-shift()`：历时声变与元音推移

```typ
sound-shift(
  nodes: (
    (id: "a", label: [iː], at: (0, 0)),
    (id: "b", label: [aɪ], at: (0, 1)),
  ),
  arrows: (("a", "b"),),          // 端点可给 id，也可以直接给坐标 ((0,0), (0,1))
  arrow-size: 0.32,              // 0.0 = 无箭头折线
)
```

- 节点除 `id` / `label` / `at` 外还有 `size`（该标签字号），刻度、关键词行靠它压小。
- 它的核心价值是 **`_ss-gap`：按标签墨迹自动给折线留缝**，符号压在线上时线自动断开。
  标签白底再补 `box(fill: white, outset: (x: 0.05u, y: 0.10u))` 才断得干净。
- **坐标轴、刻度、网格、关键词行它一概不给**，得自己搭。搭法是「把横线当成 `size: 0pt` 的超宽节点」：
  `box(width: rule-w, height: 0pt, line(...))`；左右竖框用 `line(angle: 90deg)` 节点；
  年份刻度用 `at: (plot-left - 0.46, y(yr))` 摆到框外。
- **坑 1：字符串标签一律走 TIPA 转换**，所以年份刻度 `"1400"` 会渲染成 `"ɨʉʉʉ"`
  （`1`→ɨ、`0`→ʉ）。刻度必须传 content：`label: text(str(yr))`。
- **坑 2：`\ae` 后面紧跟 `:` 时整串不匹配**，`"\ae:"` 原样吐反斜杠（`"\ae j"` 因为有空格分隔反而正常）。
  æ 系列一律改用 Unicode 字面量 `"æ:"` / `"æ:ɪ"` / `"æ"`。
  其余 TIPA 映射实测无误：`i:`→iː · `E:`→ɛː · `2I`→ʌɪ · `@I`→əɪ · `OU`→oʊ · `aU`→aʊ ·
  `O:U`→ɔːʊ · `E:U`→ɛːʊ · `iU`→iʊ · `O/6`→ɔ/ɒ · `U`→ʊ · `2`→ʌ。
- **坑 3：`typst compile` 不接受绝对路径 import**（报 `path contains invalid component "D:"`）。
  调试时把临时 `.typ` 放仓库根，用相对路径引。
- 列数由数据决定，**塞得进版心才算数**：15 列全景在 B5 版心里过挤，7 列正好。
  同一条语言史要画两版时，用两个文件、各自头部注释写清分工（全景 vs 特写），别在一张图上删列。

## `vot()`：清浊塞音的 VOT 时间线

```typ
vot(
  60,                     // VOT，毫秒。正 = 送气，负 = 预浊化
  closure: 40,            // 闭塞段示意宽度（ms）
  vowel: 60,              // 元音段示意宽度（ms）
  scale: 1.4,
  keys: true,
  closure-label: [闭塞], release-label: [除阻], voicing-label: [浊音起始],
  vowel-label: [元音], interval-label: [送气], vot-label: [VOT ≈ +60 ms],
  closure-segment: "p", vowel-segment: "i",
)
```

四条硬约束：

1. **`release-label` / `voicing-label` 只在 `keys: true` 时出现**，而且**不是**标在虚线上，
   是拼成一句图例 `R = 除阻; V = 浊音起始` 放在图右上（`legend-y = 2.2`，把整图撑高约 1.5 cm）。
   不传 `keys` 则那两条虚线**没有任何标记**，读者分不清哪条是除阻、哪条是浊音起始。
2. **图例里的分号会被吃掉**（英文默认文本 `R = release; V = voicing onset` 同样丢），
   图上只剩「R = 除阻 V = 浊音起始」。这是 phonokit/cetz 侧的问题，绕法是给 `release-label`
   的值补一个中文分号：`release-label: [除阻；]`，渲染成「R = 除阻； V = 浊音起始」（分号后多一个空格）。
3. **`scale` 是整体等比缩放**（`cetz.canvas(length: scale-factor * 1cm)`），不是只放大字号。
   `1.4` 时图宽约 8.7 cm，配 B5 版心合适；`2.2` 会把图撑出 14.08 cm 版心，
   **整块被裁掉，页面只剩图表编号和一片空白**——看着像「图丢了」。
4. **宽度算法**：`width = max(4.5, (closure + vowel + |vot|) * 0.045)` cm。
   想加宽只有三条路：调 `closure`、调 `vowel`、调 `scale`。`closure`/`vowel` 是示意宽度，
   图上不标数值，改了不会被误读；但 `scale` 会连字号一起放大（`label-size = 7pt * scale`）。

两条使用经验：

- **负 VOT 不是所有语言都真**。英语词首 `/b/` 实际接近 0（不送气清音），
  真正有预浊化的是法语、西班牙语一类。画 `vot(-60)` 作示意时必须配一句限定，
  否则把英语 `/b/` 说成有预浊化是错的。
- 两张图对比时纵向 `stack(spacing: 0.4em)` 比并排好：每条 7~9 cm，并排必超版心，
  而缩小 `closure`/`vowel` 换来的是标签挤在一起。

## 落位与校验

- **图不能紧接表格后面**。qooklet 里表后常见的剩余高度只够 6~7 cm，9 cm 的图整块挪到下一页，
  前一页底部留一片空白。改成「图紧跟表 A + 另一张图下移一节」，中间用正文填满，空白即消失。
- **qooklet 的 figure 默认左对齐**，`align(center, figure(...))` 无效（figure 自身占满版心）。
  要给图居中，得在 figure **内部**套：`figure(block(width: 100%, align(center, 图)), caption: ...)`。
- **判断图的大小别用 A4**。空壳 `chapter-style` 单章编译出的是 A4，真书是 B5
  （`iso-b5`，17.6×25 cm，margin 10%），版心 **14.08 cm**。尺子在书里量，不在 A4 量。
- **`ː` `ɜ` `ɒ` 在缩略图上分别长得像 `:` `3` `D`**，都是 Libertinus 的正常字形，不是缺字。
  核对是否改对要看**PDF 文本层的码位**（`pdftotext` 后数 U+02D0），不要靠肉眼。
  同理，`vot()` 图里字号小，核对中文标签是否被自动隐藏（放不下就不显示）也只能靠文本层。

参考实现（langs 仓库）：`lib/vowel-shift.typ`（15 列全景）、`lib/great-vowel-shift.typ`（7 列 GVS 特写）、
`lib/en-phonemes.typ`（`vowel-space()` / `vowel-space-pair()` / `consonant-space()` / `diphthong-space()` 四张图）。
VOT 图没有封装进 `lib/`，是直接在 `en-12-pronunciation-2.typ` 章内调 `vot()` —— 因此那一章的
`#import` 之后必须自己加 `#phonokit-init(font: "Libertinus Serif")`，否则 `vot()` 用默认的 Charis 撞警告。
