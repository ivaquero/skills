# draw.io 的十三条硬约束

每一条都是实测撞出来的，不是文档抄的。改转换器之前先读；任意一条没满足，
生成的文件在 draw.io 里就是错的——而且多数时候是**静默**错的：XML 合法、能打开、
不报错，只是图不对。

## 1 分号是 style 的键值分隔符，所以 `;base64` 会截断图像

draw.io 用 `;` 切分 style，再按第一个 `=` 拆键值。于是

```text
shape=image;...;image=data:image/svg+xml;base64,PHN2Zy...
```

被切成 `image=data:image/svg+xml`、`base64,PHN2Zy...` 两段：前者不是合法 URI，
后者成了无主键值。图像单元一律渲染成破图。

**证据**：同一张 60×40 的小 SVG，A 单元用 base64、B 单元用百分号编码，同文件导出
PNG 对比——A 是破图图标，B 正常显示（圆角框、红点、文字都在）。

**对策**：`data:image/svg+xml,` + `quote(svg, safe="/:")`。百分号编码后不存在裸分号，
整个值安全通过。代价是体积膨胀：中文每字 3 字节变 9 字符，带大量 CJK 标签的图
`.drawio` 会是源 SVG 的三到四倍。**这是必须付的价钱**，不要为了体积回退到 base64。

## 2 mxGraphModel 没有贝塞尔图元

只有 `rect` / `ellipse` / `text` / 贴纸（stencil）。想「把每条 path 变成可编辑 cell」
就是要把曲线重新拟合到这些形状上，结果必然失真。因此本包走「图形内嵌、文字剥离」：
图形 100% 保真，文字真正可编辑。这条不是实现取巧，是目标格式的能力边界。

## 3 分组里的子单元坐标是相对的

`parent=<group>` 的 mxCell，其 `mxGeometry` 是**相对组原点**的偏移，不是画布绝对坐标。
按绝对坐标写，渲染结果整体平移一个组原点的量（实测 100,100 处出图到 200,200）。

**对策**：先算出所有成员的并集 bbox，把每个成员的 `x`/`y` 减去 `(gx0, gy0)`，
组本身用并集 bbox 定位。

**自检**：`scripts/selfcheck.py` 的 `group coords` 检查成员不得为负、不得超出组框。

## 4 mini-SVG 必须把页面基变换补回去

包围盒是在**含页面变换**的绝对坐标里算的，而裁出来的是单元自身（只带自己的
`transform`）。少补一层，内容就偏移「页面变换」的量，被 `viewBox` 一裁，
**最先消失的是靠近边界的线端和箭头**——框和文字都还在，所以肉眼容易放过。

**证据**：亿图导出的 `g#page1` 常带 `translate(5,5)`。箭头的 mini-SVG 少补这层，
箭头被裁掉；补上 `matrix(1,0,0,1,5,5)` 包一层后恢复。

**对策**：`build_unit_svg()` 收 `base_matrix`，非单位矩阵时外包一层
`<g transform="matrix(...)">`。

## 5 页面级的裸图元本身就是一个单元

Edraw 导出的连接线常常是 `g#page1` 的直接子节点 `path`，不属于任何 `g`。
只遍历单元的**子节点**就会把这类图元整条漏掉：图里所有连线消失，其余完好。

**对策**：`_emit_unit()` 里先判断单元自身 tag 是否属于 `GRAPHICS_TAGS`，是则先把它
自己的采样点算进 bbox。

## 6 ElementTree 会自己声明命名空间，别再手写 xmlns

`ET.Element(NS + "svg")` 序列化时自动带 `xmlns`。再 `svg.set("xmlns", ...)` 一次，
输出就是 `xmlns="..." xmlns="..."`——不是良构 XML。内嵌 SVG 解析失败，
draw.io 里表现为整块空白。

**对策**：绝不手写 `xmlns` / `xmlns:xlink`；需要 `xlink` 前缀时用
`ET.register_namespace("xlink", XLINK_NS)`，由序列化器决定声明位置。

## 7 旋转单元里的文字只能烘焙

draw.io 的文本 cell 支持 `rotation=`，但和 SVG 的旋转锚点、字距、换行规则叠加后
对不齐，越改越歪。判据用单元矩阵的旋转角：`abs(mat_rotation_deg(m)) < 0.5`
才剥离文字，否则整单元内嵌、文字留在图里。

**代价**：这类单元的文字不可编辑。报告时必须说明有几处、在哪。

## 8 走自由端点的边必须写 `edge="1"`

带 `source`/`target`、带 `<mxPoint as="sourcePoint">`、带 `<Array>`，
唯独缺 `edge="1"` → draw.io 不报警，**一条线都不画**。页面只剩框和文字，
而 XML 完全合法。附带条件：坐标点写 `x`/`y`，不是 `sourceX`/`sourceY`。

**自检**：`scripts/selfcheck.py` 的 `edge attrs` 检查要求每条边都带 `edge="1"`
和显式的 `endArrow=` / `startArrow=`。

## 9 只有一个中间点的折线也要 `<Array>`

```python
if len(inner) > 1:   # 错：一个拐点的连线被吞掉
```

被吞的 `<Array>` 让 draw.io 直接按 source→target 拉直线，页面上就是
一堆横穿全图的对角线。判据改成 `if inner:`。

## 10 `dashPattern` 的长度单位是线宽

draw.io 把 `dashPattern` 乘以 `strokeWidth`。同一个 `dashPattern=8 4` 在
0.7 / 1.0 / 2.0 三种线宽下导出对比，虚线段长度随线宽等比变化。
所以 SVG 的 `stroke-dasharray` 要**除以线宽**再写；不除的话 0.7px 的点划线
会密到看起来是实线。

两个限制：`stroke-dashoffset`（相位）无法表达；`dashPattern` 只接受空格分隔的
正数，`stroke-dasharray="none"` 要落到 `dashed=0`。

## 11 每条边默认带经典箭头

不写 `endArrow=none;startArrow=none`，每条普通连线末端都会多出一个三角。
反向同理，`startArrow` 默认也是 none，但**显式写出来**才不会被上游样式带偏。

## 12 文档顺序就是图层顺序

`<root>` 里先出现的 cell 画在下面。所以连线 → 色块 → 文字，
否则 0.7px 的细线会从文字中间穿过，看起来像删除线。
不支持用 `z-order` 之类的属性调整，只能调整顺序。

## 13 `arcSize` 需要 `absoluteArcSize=1`

没有 `absoluteArcSize=1` 时，`arcSize` 被解释为「短边的百分比」，
`arcSize=8` 的卡片会胖成胶囊；写上之后才是像素值。

同时注意单行标签要给 `whiteSpace=nowrap`：换行规则一开，
`3 generations skipped` 会断成两行，而框里只有一个标签时这一行就是全部内容。

## 附：其它已知偏差

- 文字宽度是按字符估算的（CJK 1.0em、窄字符 0.3em、其余 0.56em），
  所以文本 cell 的框只保证**大致**贴合；`verticalAlign=middle` 配上
  `y = baseline - 0.34 * fs - h / 2` 是让中英文都能居中的折中值。
- 描边会让包围盒外扩半个 `stroke-width`，另加 0.5px 余量；没有这层余量，
  粗描边的外框在边缘会被切掉一条。
- 极小单元（2×2 之类）多是箭头尖端或图钉，不要因为体积小就丢弃；
  它们在视觉上是「线有没有终点」的唯一证据。
- 背景底色单元也要走 CSS class 解析。只读 `rect` 的 `fill`/`stroke` 属性时，
  亿图导出的「白底 + 浅灰外框」会退化成「白底无框」——图还在，页边框悄悄没了，
  结构自检查不出来，只有真机比对才暴露。
