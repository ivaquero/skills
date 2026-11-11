# skills

[English](README.en.md)

一组我的日常 Agent Skill 的集合。

每个 skill 都是一个自包含的包：一份由模型按需加载的 `SKILL.md`，外加 `scripts/`（确定性的代码）、`references/`（长文档，按需读取）和 `assets/`（模板与数据，永不进入上下文）。skill 的 frontmatter 里 `name` 必须等于其目录名，否则不会被加载。

本仓库是主要工作副本。一个 skill 只要落在 WorkBuddy 会扫描的 skills 目录里，就对应用可用，而两个候选位置的作用范围不同：

| 层级   | 路径                                  | 可用范围                         |
| :----- | :------------------------------------ | :------------------------------- |
| 用户级 | `~/.workbuddy/skills/<name>/`         | 本机所有项目                     |
| 项目级 | `<project>/.workbuddy/skills/<name>/` | 仅该项目，且随项目一起分享给他人 |

skill 的目录名必须与 frontmatter 里的 `name` 一致，否则不会被加载。

## 安装 skill

### 把包放到应用会扫描的位置

最简做法是复制——WorkBuddy 在每次启动时扫描目录树，因此单纯复制即可生效，无需其他步骤：

```bash
cp -r <skill> ~/.workbuddy/skills/<skill>            # 用户级，所有项目可用
cp -r <skill> <project>/.workbuddy/skills/<skill>    # 项目级，仅该仓库可用
```

若想只保留一份可编辑的副本，用链接指向它而不是复制：

```powershell
# Windows：目录 junction 不需要管理员权限
New-Item -ItemType Junction -Path "$env:USERPROFILE\.workbuddy\skills\<skill>" -Target "<repo>\<skill>"
```

```bash
# macOS / Linux
ln -s "<repo>/<skill>" ~/.workbuddy/skills/<skill>
```

### 用 npx 安装

若目标 skill 已存在于公开的 Git 仓库或市场，`skills` CLI 可以一步完成抓取与放置，无需手工复制：

```bash
npx skills find "关键词"                          # 交互式搜索
npx skills add <owner>/<repo> -g -y              # 装仓库内全部 skill，用户级
npx skills add <owner>/<repo>@<skill-name> -g -y # 只装指定的一个
npx skills add <owner>/<repo> -y                 # 项目级（默认行为）
```

## 调用 skill

这些 skill 都不需要显式命令来触发，是拿你的措辞去匹配 `description` 及其触发词，从而决定加载哪一个。由此推出两件事：**用 skill 已经列出的词汇来表述需求**，是让它被加载的关键；而当一句话可能落在多个 skill 上时，**直接点名**才是强制指定它的手段。

下面的例子都用各 skill 自己的触发词。凡是封装了 CLI 的 skill，也会给出它将要构造的调用形式——很多时候，知道一个精确调用的形状比知道触发词更有用。

### skill-draft

触发词：*create a skill、new skill、write SKILL.md、save this workflow as a skill、edit a skill、validate a skill package、add a file type to the gate。*

```text
把我刚才做的事整理成一个 skill。
```

```text
建一个"旋转 PDF 页面"的 skill，然后对它跑一遍门禁。
```

```text
把 .toml 加进门禁的规则表，用 taplo。
```

第三条是扩展路径：新增一种文件类型通常只需编辑 `scripts/file-types.json` 一处，不必改代码，而这个 skill 知道这一点。

### media-merge

触发词：*合并视频、拼接视频、合并音频、拼接音频、音乐合并、转mp3合并、分组合并、按区间合并、ffmpeg 合并、concat、merge video、merge audio、join、帧率不一致、分辨率不一致、采样率不一致、音轨合并、提取音轨合并。*

```text
把 conquer-1 和 conquer-2 合成一个视频。
```

```text
先分析帧率和分辨率，再按区间把 video-01 到 video-11 拼起来。
```

```text
这 34 段先给我看参数，我决定要不要分三组。
```

```text
把这几段 m4a 合成一个 mp3，码率别降。
```

```text
把 family.mp4 追加到 family-merged.mp4 后面。
```

第三条是交互那条路：先出表、再问分组，用户点头才执行。第五条落在最省的那一档：两段几何参数相同、只有编码不同，于是只重编 4 分半的新片段，24 分半的主段原样保留。第四条同理——四段 m4a 采样率一致，只有一段是 48 kHz，重编那一段即可，其余三段原样拷贝。

### project-py

触发词：*Python、py、ruff、ty、lint、类型检查、包管理、micromamba、mamba、conda、uv、matplotlib、subplots、运行脚本、虚拟环境、死代码、删除死代码、清理未使用代码。*

```text
给这些图加上误差棒，顺便把绘图代码收拾干净。
```

```text
下一节要用 scipy，装一下。
```

```text
对 python/ 目录跑一遍 lint 和类型检查。
```

```text
跑一下 code/fit_model.py。
```

第二条值得单独说明：这个 skill 在问清楚你要 `micromamba` 还是 `uv`、以及具体环境名之前，不会安装任何东西——`pip install` 会被直接拒绝，而且「这个包哪个环境都没有」是停下汇报的理由，不是动用 pip 的许可。第四条是同一道门禁的运行侧：只要脚本的 import 越出标准库就不会直接跑，而是先探测本机环境、再由你在交互提示里选定；那个提示永远留一个「暂停，我自己装」的出口，选它是把装包交回给你，而不是打开 pip 这道门。

### project-tex

触发词：*LaTeX、latex、tex、公式、数学公式、矩阵、行列式、方程组、分段函数、begin、aligned、bmatrix、vmatrix、mathrm、atop。*

```text
把这段推导整理成多行公式，按语义挑环境。
```

```text
写一下这个分段函数，再配一个矩阵。
```

```text
这段公式检查一下写法。
```

第三条是它的机械那一半：会执行 `python <this skill dir>/scripts/check_style.py <文件>`，该脚本只报告不改写，干净时退出码为 0。它既认 Markdown 也认 `.tex`——能从 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 以及 `latex` 围栏代码块里取出公式，所以讲义笔记和源文件受到的审查是同一套。

### project-typ

触发词：*Typst、typ、讲义、课件、幻灯片、touying、qooklet、.typ、figure、tableq、read()。*

```text
给图像处理那份课件加一节直方图均衡化。
```

```text
这一页溢出了，检查一下版式并修好。
```

```text
把这段代码做成两栏幻灯片，右边配输出图。
```

这里最要紧的两条提示正好对应两条规则：任何"新定义一个 helper"的请求都应该先走一遍查包；第三个例子必须落成一个 `python/` 下的真实文件、通过 `read()` 引入，绝不能内联进 `.typ`。

### scoop-main-plus

触发词：*scoop manifest、generate / update / lint manifest、checkver、autoupdate、hash、version bump、Excavator、main-plus、scoop-main-plus。*

```text
给 main-plus 加上新版 ripgrep 的 manifest。
```

```text
把 main-plus 里所有包都升一遍版本并重算 hash。
```

```text
检查一下 main-plus bucket。
```

底层对应的是 `gen --name <app> --recipe <recipe>`、`upd --all --checkver --apply --rehash` 和 `lint`；不传 `--repo` 时 bucket 从 `$Scoop` 解析。

### scoop-extras-plus

触发词：与 main-plus 同一组，把 *main-plus* 换成 *scoop-extras-plus*。

```text
给新版 IsoBuster 加一个 manifest。
```

```text
extras-plus 里哪些包的版本和 URL 已经对不上了？
```

```text
把我刚加的那几个包同步进 README 总结表。
```

第二条是 lint 规则能直接回答的只读问题（`W104`），比跑一次版本升格审计更省事。

### scoop-extras-cn

触发词：与上面同一组，再加 *extras-cn* 与 *scoop-extras-cn*，以及中文形式 *生成 / 更新 / 检查 manifest。*

```text
给 extras-cn 加一个新包，中文名是「飞书」。
```

```text
检查一下 extras-cn 的 README 总结表，哪些包没列进去？
```

```text
把 extras-cn 里所有包检查一遍，并把格式问题修掉。
```

第一条会用到这个 bucket 独有的四列 README 与 `中文名称` 单元格；第二条会命中 `W105`，它的提示会报出实际找到的拼写，这正是让"展示名与 manifest 名不一致"这类问题无需手工翻 README 就能定位的原因。

### anchor-french

触发词：*法语角、French corner、主持法语角、法语口语、法语讨论、法语话题、法语会话。*

```text
下周的法语角帮我准备一下，练条件式。
```

```text
主持法语角，话题随机一个。
```

```text
这周法语角是「环保」，参与者水平混合。
```

这里的重点是 intake 而非措辞：它在动笔之前先问语法点、水平和规模，而且不会替你脑补语法点。第一条自己给了语法点，于是跳过该题；第二条走 `🎲 随机` 话题来源；第三条直接点明水平，跳过水平那一问。

### anchor-spanish

触发词：*西班牙语角、西语角、Spanish corner、主持西语角、西班牙语口语、西班牙语讨论、西班牙语话题、西班牙语会话。*

```text
帮我准备这周的西语角，练虚拟式。
```

```text
主持西语角，话题随机一个。
```

```text
西语角话题是「家庭与朋友」，B2，8 个人。
```

它与 `anchor-french` 架构相同，主要差别在考试等级体系——一个用 DELE，一个用 DELF/DALF——以及各自独立的话题库。第三条把问题一次答全，正是文档里写明会被跳过的情形。

### svg2drawio

触发词：*SVG 转 draw.io、转成 drawio、.drawio 格式、drawio 文件、亿图转 drawio、eddx 转 drawio、批量转换 svg、PDF 转 svg 再转 drawio、字形反推、svg2drawio。*

```text
把这张亿图导出的 svg 转成 drawio，文字还要能编辑。
```

```text
这批 svg 是从 PDF 转出来的，先探一遍再决定走哪条工序。
```

两条工序的分水岭只有一个：图里有没有真 `<text>` 元素。有，就按单元内嵌图形、把文字剥成可编辑 cell；没有，就先反推字形，再重建为全可编辑的框、连线与标签。

### typ2drawio

触发词：*typ 转 drawio、typ2drawio、讲义生成思维导图、按讲义生成 .drawio、知识建模图、思维导图批量生成、vNN 讲义导图、drawio 批量生成。*

```text
把这批 vNN 讲义做成四向放射式 .drawio 思维导图。
```

```text
按教案 docx 编号对齐产物，再跑一遍校验。
```

四向放射式版式、几何与配色一律照 `references/layout.md`；校验只读，挡住公式噪声、节点重叠和编号不对齐。

### drawio2vsdx

触发词：*drawio 转 vsdx、drawio2vsdx、批量转 vsdx、思维导图转 Visio、vsdx 形状不对、document 节点形状不对、vsdx 校验、形状保真回归、vsdx 产物过期。*

```text
把这批 .drawio 转成 Visio .vsdx，不装 Visio。
```

```text
vsdx 里某个形状不对，定位到规则表再修。
```

纯 Python 生成 OPC 包，静态校验与反向渲染抽查成对出现，形状几何回归把「改一处、坏一片」挡在交付前。

## 目录

| 分组         | Skill                                     | 一句话                                 |
| :----------- | :---------------------------------------- | :------------------------------------- |
| 工具         | [`skill-draft`](#skill-draft)             | 构建 skill 包，并为其中每个文件设门禁  |
| 工具         | [`media-merge`](#media-merge)             | 音视频拼接：先出表再问，按组最省地合并 |
| 工具         | [`svg2drawio`](#svg2drawio)               | SVG 转 draw.io：内嵌与重建两条工序     |
| 工具         | [`typ2drawio`](#typ2drawio)               | Typst 讲义批量生成 draw.io 思维导图    |
| 工具         | [`drawio2vsdx`](#drawio2vsdx)             | draw.io 导图保真转 Visio .vsdx         |
| 项目规范     | [`project-py`](#project-py)               | Python：环境、包管理、风格、ruff + ty  |
| 项目规范     | [`project-tex`](#project-tex)             | LaTeX 数学公式：环境、括号、记号       |
| 项目规范     | [`project-typ`](#project-typ)             | Typst 课件：资源、版式、编译           |
| 语言角       | [`anchor-french`](#anchor-french)         | 法语角主持资料包                       |
| 语言角       | [`anchor-spanish`](#anchor-spanish)       | 西班牙语角主持资料包                   |
| Scoop bucket | [`scoop-main-plus`](#scoop-main-plus)     | Main-Plus bucket 的 manifest           |
| Scoop bucket | [`scoop-extras-plus`](#scoop-extras-plus) | Extras-Plus bucket 的 manifest         |
| Scoop bucket | [`scoop-extras-cn`](#scoop-extras-cn)     | Extras-CN bucket 的 manifest           |

上面每个 skill 的触发词与调用示例都在「调用 skill」；设计原则、实现取舍、算法细节与流程图收在 [docs/design.md](docs/design.md)。
