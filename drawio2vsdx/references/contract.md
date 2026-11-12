# VSDX 生成契约与形状几何规则

本文件是 `drawio2vsdx.py` 的长规范，只在需要改几何、改包结构或排查 Visio 兼容性时读取。
机器可判的基线另存为 `shape-rules.json`，`verify_vsdx.py` 与 `selfcheck.py` 都读它。

## 1 OPC 包结构

`.vsdx` 是一个 ZIP（OPC）包，最小可用部件如下，缺一即打不开：

| 部件 | 作用 |
|---|---|
| `[Content_Types].xml` | 声明各部件 ContentType，每页一条 Override |
| `_rels/.rels` | 根关系，指向 `visio/document.xml` 与 docProps |
| `docProps/core.xml`、`docProps/app.xml` | 文档属性 |
| `visio/document.xml` | 文档级设置 |
| `visio/_rels/document.xml.rels` | 指向 pages.xml、windows.xml |
| `visio/windows.xml` | 窗口设置 |
| `visio/pages/pages.xml` | 页表，含 `PageWidth` / `PageHeight`（英寸） |
| `visio/pages/_rels/pages.xml.rels` | 指向 `pageN.xml` |
| `visio/pages/pageN.xml` | 每页的 `Shapes` 集合 |

页面尺寸取每个 diagram 的 `pageWidth/pageHeight` 按 `--ppi` 换算，不强制 A4。
多个 diagram 生成多页，`pageN.xml` 与 `rIdN` 必须一一对应。

## 2 坐标与单位

- draw.io 左上原点、y 向下、单位 px；Visio 左下原点、y 向上、单位 inch。
- 换算：`in = px / ppi`，`y_visio = (pageHeight_px - y_px - h_px) / ppi`。
- `Char.Size` 也是英寸，字号 px 同样除以 ppi。
- 形状用 `PinX/PinY`（中心）、`Width/Height`、`LocPinX/LocPinY`（局部中心）、`Angle=0` 定位。
- 连线是静态 1-D `Connector`：`Pin` 取线段中点，`Width/Height` 取 `|dx|,|dy|`，
  Geometry 用**带符号的局部坐标**表达四个方向；不写 `BeginX/EndX` 等端点单元格，
  也不生成 glue / `Connects`，移动节点时连线不会自动跟随。
- 箭头不是连线的属性，而是紧随连线生成的独立实心三角 `ArrowHead` 形状（见第 9 节）。

## 3 形状映射

所有节点统一表达为归一化折线多边形：`RelMoveTo` + `RelLineTo`，
X/Y 取 `[0,1]` 相对比例，`Y = 1 - v`（v 为左上原点的归一化纵坐标）。

| NameU | 来源 | 点数 | 几何 |
|---|---|---|---|
| `Rect` | 普通直角矩形 | 5 | 四角闭合 |
| `Label` | 边上的关系标签（白底文本框） | 5 | 同 Rect，置于顶层盖住连线 |
| `Capsule` | `rounded=1` | 31 | 四角各 7 点圆弧折线近似 |
| `Cylinder` | `shape=cylinder3` | 28 | 顶椭圆上半弧 + 侧线 + 底椭圆下半弧 |
| `Document` | `shape=*document*` | 28 | 见第 5 节 |
| `ArrowHead` | 边的 `endArrow!=none` | 4 | 归一化闭合实心三角，见第 9 节 |
| `Connector` | 边 | 2 | 绝对局部坐标，不参与归一化几何断言 |

刻意避开 `RelEllipticalArcTo`：该行的 A/B 若传英寸绝对值，Visio 会生成巨型弧面覆盖整页。
折线近似是确定性的，代价是圆柱体缺顶部椭圆的下沿线、曲线略带棱角。

## 4 兼容性硬约束

| 规则 | 原因 |
|---|---|
| 颜色写 `#RRGGBB`，不写 `RGB(r,g,b)` | 后者在 Visio 导出时全部变黑 |
| 中文显式 `AsianFont=Microsoft YaHei` | 否则中文回退成系统字体，可能错位 |
| Shape 上不写 `Master="-1"` | 会让 Visio 静默丢弃整页 |
| 源文本 `<br>` 必须转成真换行 `\n` | 原样写入会印出字面量 `<br>` |
| 不写 glue / `BeginX` 端点 | 静态线段由 Geometry 决定，写端点会被 Visio 重算错位 |
| 不写 `EndArrow` 单元格 | 自建 1-D 形状无 Master / 端点 / `EndArrowSize` 时 Visio 不保证渲染，见第 9 节 |

`visio_text()` 是唯一的文本归一化入口：比对源 `.drawio` 与产物文本多重集之前必须过它，
否则含 `<br>` 的图会全部误报。

## 5 document 形状硬约定（本次修复的核心）

对照参考图与 mxGraph `flowchart.Document` stencil：顶 / 左 / 右三边平直，
**左右两个下角必须是直角**，竖边直落到基线；底边是一条完整的 S 形波
（从右端起先上凸、再下凹），两端落回基线。

- `base = 1 - size/2`，波形 `v = base - (size/2)·sin(2π(1-u))`，`cycles=1`。
- 波谷恰在框底 `v=1`，波峰在 `v=1-size`，全程不越出外接框。
- `size` 取自 style，既可能是 `0.16` 也可能是百分数 `16`；大于 1 时除以 100。
- **禁止**用 `1-cos` 的多周期波形：那会变成三团向下鼓包，且把左右下角削掉。

写几何断言时取底边 `pts[2:-1]`——最后的 `(0,0)` 是闭合点（v=0），
把它算进底边会让每个 document 都误报「波浪越界」。

## 6 反向渲染的边界

`vsdx_preview.py` 从 `.vsdx` 读回 `Shape / Geometry / Text` 自绘 SVG，
可验证坐标映射、五类节点形状、几何箭头与标签位置，**但它不是 Visio 自身排版**。
渲染器**不得**根据单元格标记自行补画箭头：早期的 `if "EndArrow" in c: 画三角形`
会把「VSDX 里其实没有箭头」伪装成「预览里有箭头」，从而掩盖 Visio 端的真实缺失。
预览只能画 `pageN.xml` 里真实存在的 Shape。
`rsvg-convert` 只用于把 SVG 转 PNG 做目视抽查。
Visio COM 在部分自动化通道会被安全策略拦截（禁止实例化 COM 对象），
此时以静态校验 + 反向渲染为准，并如实告知用户「Visio 自身打开未实测」。

## 7 产物同步判定

`zipfile.writestr` 会写入当前时间戳，同一内容每次生成的文件哈希都不同。
判断产物是否过期，必须**逐部件比 ZIP 内解压后的字节**，不能比文件哈希。
覆盖重生成前先备份现有 `.vsdx` 到带时间戳的目录。

## 8 门控内联抑制说明

包内四个脚本顶部都带一条精确的 `# ruff: noqa: ...`，少数行带 `# ty: ignore[...]`。
这是 `skill-draft` 允许的「内联抑制并说明工具为何不适用」，不是降低门控规则：

- `S314`：解析对象是本转换器自己生成的本地 `.drawio` / `.vsdx`，不是不可信网络输入；
  包内脚本只允许标准库，不能为了 lint 去装 `defusedxml`。
- `UP031` / `PERF401` / `B023` / `PLW1510` / `S607`：风格与可选工具链建议。
  转换器与预览器是项目内已验证实现的工作副本，逐项改写会引入逐字漂移；
  校验器与自检脚本同样以「与已验证产物等价」为第一约束。
- `ty` 的 Optional 推断（`Element.get()` 返回 `str | None`、正则 `Match | None`）：
  输入 XML 的这些属性由第 1、3 节的契约保证必然存在，运行时不会为 `None`。

规则码按各文件实际命中情况精确列出；`ruff format --check`、`ruff check` 与
`ty check` 仍必须全绿，自检里的反向测试负责证明抑制没有掩盖真实缺陷。

## 9 箭头硬约定（2026-11-12 修复）

**结论：draw.io 的 `endArrow=block` 必须转换成 VSDX 里的几何形状，不能只写
`<Cell N="EndArrow" V="4"/>`。**

原因：转换器生成的 `Connector` 是自建 1-D 形状，没有 Master、没有
`BeginX/EndX` 端点单元格、也没有 `EndArrowSize`。MS-VSDX 里 `EndArrow` 属于
Line Format section（1–45 为箭头样式），`EndArrowSize` 省略时取 0 = Very small；
在这种缺少默认样式继承的形状上，Visio 端实测表现为「连线在、箭头没了」。
尺寸极小或完全不渲染都取决于版本，不能依赖。

生成规则：

- 每条 `endArrow != none` 的边额外产出**一个** `NameU="ArrowHead"` 的 Shape。
- 三角形尖端 = 连线终点 `p2`，底边沿线段反向退 `endSize` 像素（默认 7），
  半宽 `endSize/2`；三角形完全落在目标节点框外，不会被上层节点遮住。
- 归一化 `RelMoveTo` + 3 条 `RelLineTo`（4 点、首尾重合闭合），
  `FillPattern=1`、`FillForegnd=连线 LineColor`、`LineWeight=0`。
- 形状数因此是 `节点 + 2×带箭头连线 + 标签`；校验器按此计数。

机器判据（`shape-rules.json` 的 `arrows` 段 + `verify_vsdx.py`）：
箭头数必须等于源里带箭头边数；每个箭头 4 点闭合且已填充；
尖端与同页某条 `Connector` 的终点距离 ≤ 0.002 in（防方向画反）；
填充色必须等于某条连线的线色；`compat.forbidden_bytes` 含 `EndArrow`，
防止后来者改回单元格写法。`selfcheck.py` 会删除全部 `ArrowHead`
并断言校验器报「箭头数」错误。
