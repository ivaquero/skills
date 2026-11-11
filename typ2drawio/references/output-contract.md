# 产物契约与校验

## XML 结构

`.drawio` 是未压缩的原生 `mxGraphModel` XML，首行带 XML 声明：

```text
<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" ... type="device">
  <diagram id="..." name="...">
    <mxGraphModel ...>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="..." value="..." style="..." vertex="1" parent="1">
          <mxGeometry x="..." y="..." width="..." height="..." as="geometry"/>
        </mxCell>
        <mxCell id="e..." value="..." style="..." edge="1" parent="1" source="..." target="...">
          <mxGeometry x="0.58" relative="1" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

- `id="0"` 是根层，`id="1"` 是默认父层，二者缺一不可。
- 每个节点带 `vertex="1" parent="1"` 与 `mxGeometry` 的绝对坐标；每条连线带 `edge="1" parent="1"` 与 `source` / `target`。
- `value` 里的换行写成 `<br>` 并在 XML 属性中转义为 `&lt;br&gt;`；`&`、`<`、`>`、引号一律转义。节点样式带 `html=1`，draw.io 才会渲染换行。
- 关系标签的位置用 `<mxGeometry x="0.58" relative="1" as="geometry"/>`，比例见 `layout.md`。
- `pageWidth` / `pageHeight` 由内容包围盒加 `PAD` 算出，不是固定值。
- `modified` 时间戳默认取当前 UTC 时间；传 `--modified` 可固定它，让同一输入两次生成字节完全一致。

机器可读的基线规则在 `references/mxgraph-rules.json`，自检脚本会拿自检样例的产物逐项对照它，改 XML 结构时必须同步更新该文件。注意 `baseline_style_fragments` 是**自检样例必须展示出来的样式集合**，不是每份产物的必需样式：胶囊 `arcSize=50`、蓝底 `fillColor=#DAE8FC`、蓝字主干 `fontColor=#0000FF` 都依赖样例里恰好有对应的内容，所以样例固定为 5 个主干、含做法类知识点与 API 类知识点。

## 命名

`<编号>-<讲名>.drawio`。编号来自 docx 文件名（给了 `--docx-dir` 时）或讲义的 `NN`；讲名取 `vNN-` 之后、`.typ` 之前的部分。

`<diagram name>` 与中心节点文字都只写章名（讲名），**不带编号、课次或讲义号**——编号只出现在文件名里，这一点与「教案-图」目录里同名的 `.vsdx` 保持一致。

## 前置知识节点

前置知识的唯一来源是教案 docx 的「学习先决知识」栏，不看讲义。该栏的稳定写法是：

```text
《上一讲章名》相关概念（理解、运用）；Python 与 NumPy 基础（熟悉、运用）；OpenCV-Python 开发环境（运用）
```

提取规则：按文档顺序找第一个匹配 `《([^》]+)》\s*相关概念` 的段落，取书名号内的章名；同一段用 `；` 切分，逐条去掉尾部学习水平括注（`（理解、运用）` 之类），剔除含章名的那条，剩下最多两条并入第二行。

节点文案固定两行：

```text
前置知识：《上一讲章名》
Python 与 NumPy 基础 · OpenCV-Python 开发环境
```

注意部分 docx 把这一栏放在了表格首行、另一处「学习先决知识」小节里填的却是本讲自己的知识点（模板错填）。按上面的正则抓取可以绕过这类错填，因为错填的小节写的是知识点名而不是「《…》相关概念」。

docx 缺该栏时不生成节点，并在 stderr 提示，不编造内容。

## 校验项

`scripts/verify_drawio.py` 逐文件检查：

| 项目 | 通过条件 |
| --- | --- |
| XML | `ElementTree` 可解析，根为 `mxfile` |
| id | 全部 `mxCell` 的 id 唯一 |
| 连线 | 每条边的 `source` / `target` 都指向存在的节点 |
| 密度 | 每讲至少 15 个节点、15 条连线 |
| 编号 | 产物编号集合与 docx 编号集合完全一致 |
| 噪声 | 公式符号、`ex\d`、双下划线、省略号、`#code` / `#figure` 零命中 |
| 布局 | 任意两个有边框节点矩形重叠对数为 0 |
| 前置知识 | 给了 `--docx-dir` 时检查：恰有 1 个绿色文字节点、文案以「前置知识：」开头、有一条从它指向 `center` 的「先修」连线 |

`scripts/selfcheck.py` 检查技能包自身，与产物校验分开：文档常量对照代码、样例往返生成、XML 对照外部规则表。

## 交付边界

- 生成过程不依赖 draw.io Desktop；本机没有 CLI 时不要擅自安装，只交付 `.drawio` 源文件。
- `--preview` 产出的 HTML/SVG 是脚本用同一几何自绘的自检预览，**不是 draw.io 的真实导出**。交付时必须说明这一点。
- 需要 PNG/SVG/PDF 时，按 draw.io CLI 的 `--embed-diagram` 另做一轮导出，保留 `.drawio` 源文件。
- 校验通过不等于视觉通过：最密与最疏两张仍要目视抽查。
