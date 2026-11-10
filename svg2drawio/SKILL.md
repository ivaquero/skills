---
name: svg2drawio
description: 把 SVG 转成 draw.io 的 .drawio 文件，分两条工序：有真 <text> 的图（亿图 Edraw 一类）按单元内嵌图形、文字剥离成可编辑 cell；没有 <text>、字形已轮廓化的图（cairo/Pango 从 PDF 导出）反推字形并重建为全可编辑的框、连线与标签；两条工序都附结构自检与真机渲染校验。触发词：SVG 转 draw.io、转成 drawio、.drawio 格式、drawio 文件、亿图转 drawio、eddx 转 drawio、批量转换 svg、PDF 转 svg 再转 drawio、字形反推、svg2drawio。
agent_created: true
---

# SVG → draw.io

**技能类型**：流程型 —— 管「按什么顺序转、转完怎么验」，不管绘图风格。

## 先接受一个约束，再谈方案

draw.io 的 `mxGraphModel` 没有「任意贝塞尔路径」这种基础图元，只有矩形、椭圆、
标签和贴纸。所以把每条 `path` 还原成可编辑 mxCell 这条路是不通的，只剩三条：

| 方案 | 图形保真 | 可编辑性 | 什么时候用 |
| --- | --- | --- | --- |
| 按单元内嵌 SVG + 文字独立成 cell | 像素级 | 文字可改、图形可拖动分组 | 有真 `<text>` 的图，默认 |
| 整幅页内嵌为单张图像 | 像素级 | 无 | 只做归档、只需在 draw.io 里看得清 |
| 按语义重建（框 / 连线 / 标签） | 近似到像素级 | 全部可编辑 | 源图本身是「框 + 折线 + 标签」的结构图 |

别把第二、三种混着用：第三种要能认出「什么是框、什么是线、什么是字」，
认不出来就是一页错位图形。判据在下一步的探针里。

## 步骤

### 1 先判工序

一次问清三件事：页面尺寸、图形单元是 `g` 还是裸 `path`、文字是真 `<text>`
还是已经 outlining 成 `<use xlink:href="#glyph-…">`。

```bash
python <this skill dir>/scripts/rebuild.py <svg> --probe
```

它输出页尺寸、`<text>` 数、`<use>` 数、字形定义数、字体子集数、path 分类、
多子路径描边数、虚线数，并直接给出该走哪条工序。

| 探针结果 | 工序 | 产物 |
| --- | --- | --- |
| `<text>` 不为 0 | 图形内嵌（第 2 步） | 图形嵌图 + 文字可编辑 |
| `<text>` 为 0 且字形上千 | 全可编辑重建（第 3 步） | 框、连线、文字全原生 |
| 两者都不是 | 整图内嵌 | 一张图，仅归档 |

判据：能说出「N 个单元、页面是否有 translate、文字属于哪一种」。
页面自身带 `transform` 的图，每个单元都要补这层变换（见 quirk 4）。

### 2 图形内嵌（有真 `<text>`）

```bash
python <this skill dir>/scripts/svg2drawio.py <svg 或目录> -o <输出目录>
```

默认 `--mode shapes`；只要整图就 `--mode image`。子目录加 `--recursive`；
曲线采样精度用 `--steps`（默认 16，放大后仍毛糙再调高）；不要页面底色加 `--no-bg`。

判据：命令输出表里每个文件的 `shapes` 与源图单元数量级一致，`texts` 不为 0。
若某个文件报 `no <text>, N glyph references`，说明它其实是轮廓化输入，
停手换第 3 步——这条提示存在的原因就是「转完看着成功、实际上一个字都没留下」。

### 3 全可编辑重建（无 `<text>`，字形轮廓化）

先备一张字形表（`glyph-id → 字符`，格式见 `assets/outline-glyphs.tsv`）。
这一步要栅格化与距离变换，只能用 numpy / Pillow / scipy，**不在本包脚本里**；
算法、参数与踩过的坑全部写在 `references/outline-pipeline.md`，照它做一份表出来。

```bash
python <this skill dir>/scripts/rebuild.py <svg> --glyphs <表.tsv> -o <out.drawio>
```

判据：输出的 `unmapped` 为 0（缺字形的会被静默渲染成空格，脚本会警告），
`boxes` / `edges` / `lines` 与探针给出的量级相符，`arrows` 接近源图箭头数。
字体族猜错的表现是标签整体偏宽或偏窄，用 `--font-family` 与 `--cap-ratio` 校正。

### 4 结构自检

```bash
python <this skill dir>/scripts/selfcheck.py
```

它跑十几项：包内一致性、两条工序的往返转换、style 里有没有分号、分组子坐标、
页面变换是否补进 mini-SVG、文字是否齐全、`edge="1"` 与显式箭头、
单点 `<Array>`、虚线换算、图层顺序、空白字形是否被当成空格、计数是否与基线一致、
两次转换是否只差时间戳。

判据：`all checks passed`。有 FAIL 先按 `references/drawio-quirks.md` 的成因表定位，
不要靠肉眼猜。

### 5 真机渲染校验（不可跳过）

结构自检只能证明 XML 合法，证明不了 draw.io 会把它画对。必须用本机 draw.io
导出一次 PNG，再与原 SVG 的浏览器渲染对比。完整命令与阈值见
`references/verification.md`。

判据：`RMSE` 落在 0.02–0.07 是**含文字图表的正常值**（实测正确结果就是 0.035–0.046），
超过 0.12 或差值图里出现实心亮块才算缺陷。上千 cell 的大图改用 1:1 导出的
IoU + 残差连通域（参照值 0.74，最大残差块 < 150px）。但真正的判定在放大核对：
**线端、箭头尖、旋转单元的文字**三处必须逐点看过——它们丢了，指标几乎不动。

### 6 报告

给出表：文件 / cells / 图形单元 / 文字单元 / 大小。指出文字是否全部可编辑，
哪些单元因为旋转被烘焙进了图像；重建工序还要交代字形表的覆盖率与存疑条目。

## 症状对照表

| 看到的现象 | 真正的原因 | 处理 |
| --- | --- | --- |
| 图像单元全变破图 | style 里 `image=` 的值含分号（`;base64`） | 改百分号编码，见 quirk 1 |
| 图形整体偏移一倍 | 分组子单元按绝对坐标写了 | 子坐标减去组原点，见 quirk 3 |
| 箭头、线端缺失，框和文字都在 | mini-SVG 没补页面变换，内容被 viewBox 裁掉 | 见 quirk 4 |
| 连接线整条不见（内嵌工序） | 页面级的裸 `path` 被当成容器跳过 | 见 quirk 5 |
| 连接线整条不见（重建工序） | 边少了 `edge="1"` | 见 quirk 8 |
| 转换后 XML 解析失败、draw.io 打不开 | `xmlns` 被声明两次 | 见 quirk 6 |
| 文字位置差半行、上下浮 | 基线换算是按字号估的 | 内嵌调 `0.34 * fs`；重建调 `1.31 * size` |
| 文件体积是源图三四倍 | 百分号编码对中文/符号的膨胀 | 正常，别改回 base64 |
| 一页满是横穿全图的长斜线 | 单点 `<Array>` 被丢，或一个 `d` 里的多条线没拆开 | 见 quirk 9 与 outline-pipeline 3.1 |
| 虚线渲染成实线 | 折返的「总线」被走两遍，相位交错 | 线段级建图，见 outline-pipeline 3.2 |
| 每个箭头都指向反方向 | 箭头尖端取成了底边中点 | 用路径第一个点，见 outline-pipeline 3.6 |
| 空格变成句点 | 空白字形跟任何字符都形似 | bbox < 0.05px 一律当空格 |
| 细线从文字中间穿过 | 图层顺序反了 | 文档顺序 = 图层顺序，见 quirk 12 |
| 卡片胖成胶囊 | `arcSize` 当成了百分比 | 加 `absoluteArcSize=1` |
| 单行标签被断成两行 | 没写 `whiteSpace=nowrap` | 单行 nowrap，多行 `<br>` |

## 参考

- `references/drawio-quirks.md`：十三条实测出来的硬约束，附证据与最小复现。
  动手改脚本前先读它，这十三条里任意一条没满足，图就是错的。
- `references/outline-pipeline.md`：轮廓化 SVG 的完整工序——字形反推的算法与参数、
  结构重建的判定阈值、十三条静默出错的坑，以及实测数量级。
- `references/verification.md`：draw.io CLI 与无头浏览器对比的具体命令、裁剪对齐、
  RMSE 阈值、大图的 1:1 对齐与残差判定，以及哪些差异属于可接受。
- `assets/sample.svg` + `assets/baseline.json`：内嵌工序的自检样本与期望计数。
  样本刻意覆盖了「页面 translate、裸 path 连接线、旋转单元、双 tspan」四种情况。
- `assets/outline-sample.svg` + `assets/outline-glyphs.tsv`：重建工序的自检样本。
  刻意覆盖「填充框、卡片 + 光晕去重、4 段贝塞尔闭合轮廓、单/双拐点连线、
  虚线、独立箭头、自由文字、空白字形」八种情况。改转换逻辑后基线要跟着更新，
  并说明为什么变。
