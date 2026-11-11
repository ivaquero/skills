# skills

[中文](README.md)

A collection of my daily Agent Skills.

Each skill is a self-contained package: a `SKILL.md` that the model loads on demand, plus `scripts/` (deterministic code), `references/` (long specs, read only when needed) and `assets/` (templates and data that never enter context). A skill's `name` in its frontmatter must equal its directory name, or it will not load.

This repo is the main working copy. A skill becomes available to WorkBuddy once it sits in a skills directory the app scans, and the two candidate locations differ in scope:

| Level   | Path                                  | Availability                                                   |
| :------ | :------------------------------------ | :------------------------------------------------------------- |
| User    | `~/.workbuddy/skills/<name>/`         | Every project on this machine                                  |
| Project | `<project>/.workbuddy/skills/<name>/` | That project only, and shared with anyone who gets the project |

A skill's directory name and its frontmatter `name` must match, or it will not load.

## Installing a skill

### Put the package where the app looks

The simplest install is a copy — WorkBuddy scans the directory tree on each launch, so a plain copy works with no further steps:

```bash
cp -r <skill> ~/.workbuddy/skills/<skill>            # user level, all projects
cp -r <skill> <project>/.workbuddy/skills/<skill>    # project level, that repo only
```

To keep one editable copy instead, link to it rather than copying:

```powershell
# Windows: a directory junction needs no administrator rights
New-Item -ItemType Junction -Path "$env:USERPROFILE\.workbuddy\skills\<skill>" -Target "<repo>\<skill>"
```

```bash
# macOS / Linux
ln -s "<repo>/<skill>" ~/.workbuddy/skills/<skill>
```

### Install with npx

For a skill that already lives in a public Git repository or on a marketplace, the `skills` CLI does the fetch and the placement in one step, so nothing has to be copied by hand:

```bash
npx skills find "keyword"                        # search interactively
npx skills add <owner>/<repo> -g -y              # every skill in a repo, user level
npx skills add <owner>/<repo>@<skill-name> -g -y # one named skill
npx skills add <owner>/<repo> -y                 # project level (the default)
```

## Using a skill

None of these skills needs an explicit command to fire. None declares `disable-model-invocation`, so WorkBuddy loads one by matching your wording against the `description` and its trigger words. Two things follow from that: phrasing the request in the vocabulary the skill already lists is what makes it load, and naming the skill outright is the way to force it when a request could plausibly match more than one.

The examples below use each skill's own trigger words. Where a skill wraps a CLI, the invocation it will build is shown too, since knowing the shape of an exact call is often more useful than knowing the trigger phrase.

### skill-draft

Triggers: *create a skill, new skill, write SKILL.md, save this workflow as a skill, edit a skill, validate a skill package, add a file type to the gate.*

```text
Walk through what I just did and save it as a skill.
```

```text
Create a skill for rotating PDF pages, then run the gate on it.
```

```text
Add .toml to the gate's rules table, for taplo.
```

The third one is the extension path: adding a file type normally means editing `scripts/file-types.json` alone, with no code change, and the skill knows that.

### media-merge

Triggers: *merge video, merge audio, join, concatenate, append a clip, merge music, merge in groups, merge by range, ffmpeg merge, concat, extract and merge audio tracks, mismatched frame rate, mismatched resolution, mismatched sample rate, plus the Chinese terms for merging and stitching media.*

```text
Merge conquer-1 and conquer-2 into one video.
```

```text
Check the frame rate and resolution first, then stitch video-01 through video-11.
```

```text
Show me the parameters for these 34 clips; I'll decide whether to split them into three groups.
```

```text
Merge these m4a files into a single mp3 without dropping the bitrate.
```

```text
Append family.mp4 to the end of family-merged.mp4.
```

The third one is the interactive path: table first, then the grouping question, and nothing runs until the user agrees. The fifth lands in the cheapest tier: same geometry, different codecs, so only the 4.5-minute newcomer is re-encoded and the 24.5-minute main part keeps its original bitstream. The fourth works the same way — four m4a files share a sample rate and only one is 48 kHz, so just that one gets re-encoded.

### project-py

Triggers: *Python, py, ruff, ty, lint, micromamba, mamba, conda, uv, matplotlib, subplots, dead code, plus the Chinese terms for type checking, package management, running a script, virtual environments and dead code.*

```text
Add error bars to these plots and clean up the figure code.
```

```text
I need scipy for the next section — set it up.
```

```text
Lint and type-check the python/ directory.
```

```text
Run code/fit_model.py.
```

The second is worth noting: this skill will not install anything until it has asked which of `micromamba` / `uv` you want, and which environment by name — `pip install` is refused outright, and a package that exists in no environment at all is a reason to stop and report, not a licence to reach for pip. The fourth shows the same gate on the running side: a script whose imports reach past the standard library does not run until the environments on the machine have been probed and you have picked one in a prompt — and that prompt always offers a pause-and-install-it-yourself way out, which returns the install to you rather than opening the pip door.

### project-tex

Triggers: *LaTeX, latex, tex, begin, aligned, bmatrix, vmatrix, mathrm, atop, plus the Chinese terms for formula, matrix, determinant, equation system and piecewise function.*

```text
Reformat this LaTeX derivation as a multi-line formula, picking the environment by meaning.
```

```text
Write up this piecewise function in LaTeX, with a matrix to go with it.
```

```text
Check the LaTeX in this formula.
```

The third is the mechanical half: it runs `python <this skill dir>/scripts/check_style.py <file>`, which is report-only and exits 0 when clean. The script knows Markdown as well as `.tex` — it finds math inside `$…$`, `$$…$$`, `\(…\)`, `\[…\]` and `latex`-fenced blocks, so a lecture note gets the same audit as a source file.

### project-typ

Triggers: *Typst, typ, touying, qooklet, .typ, figure, tableq, read(), plus the Chinese terms for lecture notes and slides.*

```text
Add a section on histogram equalisation to the image processing deck.
```

```text
This page overflows — check the layout and fix it.
```

```text
Turn this code sample into a two-column slide with the output image beside it.
```

The two prompts that matter most here are the ones that trip the rules: any request to define a new helper should first get a package search, and the third example must come back as a real file under `python/` reached through `read()`, never inlined into the `.typ`.

### scoop-main-plus

Triggers: *scoop manifest, generate / update / lint manifest, checkver, autoupdate, hash, version bump, Excavator, main-plus, scoop-main-plus.*

```text
Add a manifest for the new ripgrep release to main-plus.
```

```text
Bump every package in main-plus and recompute the hashes.
```

```text
Lint the main-plus bucket.
```

Under the hood those become `gen --name <app> --recipe <recipe>`, `upd --all --checkver --apply --rehash`, and `lint`, with the bucket resolved from `$Scoop` when you do not pass `--repo`.

### scoop-extras-plus

Triggers: the same set as main-plus, with *scoop-extras-plus* in place of *main-plus*.

```text
Add a manifest for the new IsoBuster release.
```

```text
Which packages in extras-plus have a version that no longer matches their URL?
```

```text
Sync the README summary table for the packages I just added.
```

The second is a read-only question the lint rules answer directly (`W104`), which is a cheaper way to audit than running a bump.

### scoop-extras-cn

Triggers: the same set plus *extras-cn* and *scoop-extras-cn*; the generate, update and lint manifest commands are also matched in Chinese.

```text
Add a new package to extras-cn and put its Chinese display name in the README.
```

```text
Check extras-cn's README summary table — which packages are missing from it?
```

```text
Lint every package in extras-cn and fix the formatting problems.
```

The first exercises the four-column README and the Chinese display-name column that this bucket alone has; the second reaches `W105`, whose hint names the exact spelling it found, which is what makes the display-name-versus-manifest-name mismatch diagnosable without reading the README by hand.

### anchor-french

Triggers: *French corner, plus the Chinese phrases for the French corner, hosting it, spoken French, French discussion, French topics and French conversation.*

```text
Prepare next week's French corner — we are practising the conditional.
```

```text
Host a French corner with a random topic.
```

```text
This week's French corner is on the environment; the group is mixed level.
```

The intake is the point here, not the wording: it asks about the grammar point, the level and the group size before writing anything, and it will not invent a grammar point for you. The first prompt supplies one and skips that question; the second exercises the random-topic source; the third jumps past the level question by naming a level outright.

### anchor-spanish

Triggers: *Spanish corner, plus the Chinese phrases for the Spanish corner, hosting it, spoken Spanish, Spanish discussion, Spanish topics and Spanish conversation.*

```text
Help me prepare this week's Spanish corner — we are practising the subjunctive.
```

```text
Host a Spanish corner with a random topic.
```

```text
The Spanish corner topic is family and friends, B2, eight people.
```

It shares the architecture with `anchor-french` and differs mainly in the exam ladder — DELE here, DELF/DALF there — and in its own topic pool. The third prompt answers every question in one line, which is the case the intake is documented to skip.

### svg2drawio

Triggers: *SVG to draw.io, convert to drawio, .drawio file, Edraw to drawio, eddx to drawio, batch SVG conversion, PDF to SVG to drawio, glyph reconstruction, svg2drawio, plus the Chinese terms for converting diagrams and inferring glyphs.*

```text
Convert this Edraw SVG to draw.io, keeping the text editable.
```

```text
These SVGs came from a PDF; probe them first, then pick the pipeline.
```

One question separates the two pipelines: does the SVG contain real `<text>` elements? If yes, graphics are embedded per unit and the text is pulled out into editable cells; if not, glyphs are decoded first and the boxes, connectors and labels are rebuilt as native objects.

### typ2drawio

Triggers: *Typst lecture notes to draw.io, typ2drawio, mind map from lecture notes, batch .drawio generation, four-way radial mind map, vNN lecture mind map, concept map, plus the Chinese terms for aligning outputs by lesson number.*

```text
Turn this batch of vNN lecture notes into four-way radial .drawio mind maps.
```

```text
Align the outputs with the lesson docx numbers, then verify them.
```

The four-way radial layout, geometry and palette all come from `references/layout.md`; verification is read-only and rejects formula noise, overlapping nodes and mismatched numbering.

### drawio2vsdx

Triggers: *drawio to vsdx, drawio2vsdx, batch vsdx conversion, mind map to Visio, vsdx shape geometry, document node shape, vsdx verification, shape-fidelity regression, stale vsdx output, plus the Chinese terms for converting and verifying Visio files.*

```text
Convert this batch of .drawio files to Visio .vsdx without installing Visio.
```

```text
One shape in the vsdx is wrong; find the rule, then fix it.
```

The OPC package is generated in pure Python. Static verification and reverse-render spot checks always come as a pair, and the shape-geometry regression stops a one-line fix from breaking the rest of the batch.

## Contents

| Group               | Skill                                     | One line                                                  |
| :------------------ | :---------------------------------------- | :-------------------------------------------------------- |
| Tooling             | [`skill-draft`](#skill-draft)             | Build a skill package and gate every file in it           |
| Tooling             | [`media-merge`](#media-merge)             | Audio/video stitching: probe, then pick the cheapest plan |
| Tooling             | [`svg2drawio`](#svg2drawio)               | SVG to draw.io: embed, or rebuild                         |
| Tooling             | [`typ2drawio`](#typ2drawio)               | Typst lectures to draw.io mind maps                       |
| Tooling             | [`drawio2vsdx`](#drawio2vsdx)             | draw.io to Visio .vsdx, with fidelity checks              |
| Project conventions | [`project-py`](#project-py)               | Python: environment, packages, style, ruff + ty           |
| Project conventions | [`project-tex`](#project-tex)             | LaTeX math: environments, brackets, notation              |
| Project conventions | [`project-typ`](#project-typ)             | Typst lecture slides: assets, layout, compile             |
| Language corners    | [`anchor-french`](#anchor-french)         | French conversation-circle host kit                       |
| Language corners    | [`anchor-spanish`](#anchor-spanish)       | Spanish conversation-circle host kit                      |
| Scoop buckets       | [`scoop-main-plus`](#scoop-main-plus)     | Manifests for the Main-Plus bucket                        |
| Scoop buckets       | [`scoop-extras-plus`](#scoop-extras-plus) | Manifests for the Extras-Plus bucket                      |
| Scoop buckets       | [`scoop-extras-cn`](#scoop-extras-cn)     | Manifests for the Extras-CN bucket                        |

Trigger words and invocation examples for every skill are in "Using a skill" above; design principles, implementation trade-offs, algorithms and flowcharts live in [docs/design.en.md](docs/design.en.md).
