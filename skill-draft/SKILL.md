---
name: skill-draft
description: Turn a workflow or domain knowledge into a Skill package, and make every file in it pass a quality gate before it is saved. Trigger words: create a skill, new skill, write SKILL.md, save this workflow as a skill, edit a skill, validate a skill package, add a file type to the gate.
agent_created: true
---

# skill-draft

Create or modify a Skill package, and make every file in it pass the gate
before it lands on disk.

**Skill type**: process (流程型) — this package owns a workflow and a gate, not
a tone.

## Iron rules

- A file is not done until the gate passes. Run the gate immediately after
  every Write or Edit.
- Use system tools only. Do not install dependencies, and do not add config
  just to downgrade rules.
- Suppress false positives inline, and state why the tool is wrong rather
  than the code.
- Wording and structure are not this skill's concern: how to write steps,
  set pointers, or layer content is all covered by `writing-for-agents`.
  This skill owns only the skeleton and the gate.

## Steps

### Fix the identity

Settle these with the user; none of them is optional.

- `name`: lowercase kebab, and it must equal the package directory name,
  or the skill will not load.
- `description`: one line of capability plus trigger branches, written as
  the words the user would actually say.
- Invocation: leave `description` for skills that the model or another
  skill should trigger automatically; add `disable-model-invocation: true`
  for the ones the user types by hand.
- Type: `constraint` (约束型, owns a tone), `process` (流程型, owns a
  workflow) or `mixed` (混合型). Which one it is decides what to review,
  so state it in the first paragraph of the body — a constraint skill is
  judged on its prohibitions and boundaries, a process skill on its steps
  and completion criteria.

Write nothing else into the frontmatter. `name`, `description` and the
platform fields (`agent_created`, `disable-model-invocation`) are the whole
allowed set, and `skill-frontmatter` in the gate fails on any other key. A
`title` gets read as the document title and demotes the body's H1; `version`
and `tags` and display-name keys only drift out of date.

### Fix the skeleton

```text
<name>/
├── SKILL.md        required
├── scripts/        code that runs deterministically and is rewritten often
├── references/     long specs, loaded on demand
└── assets/         templates and fonts for output, never entering context
```

Create only the directories you need. Something belongs in `references/`
only if the body points to it.

Every package also carries a one-command self-check in `scripts/`, written
with the standard library and running offline. It has to cover at least three
things: package-internal consistency (the docs against the code, `name`
against the directory name), a round-trip over real data, and a comparison
against an external baseline or rule table. The gate in step 4 checks the
files; this checks the package.

### Write the files

Write steps imperatively (verb first), one completion criterion per step,
and push long specs down into `references/`. Scripts inside the package may
use the standard library only, so that the package runs on any machine.

Never write a machine-local absolute path into the body or the scripts
(`C:\Users\...`, `/home/...`). A package gets copied and ported, and a
hard-coded path turns into wrong information the moment it is copied.
Refer to this skill itself with the `<this skill dir>` placeholder, and to
the home directory with `~` or `Path.home()`. The gate's `no-local-paths`
check backstops this.

### Pass the gate

One command runs everything:

```bash
# <this skill dir> is where this skill package lives;
# for a user-level install that is ~/.workbuddy/skills/skill-draft
python <this skill dir>/scripts/verify.py <file-or-dir>...
```

Every type's pipeline has three stages: builtin check, repair, verify.
The repair stage's exit code is ignored; the verify stage must be all zero.
Currently covered: `.py`, `.md`, `.json`/`.jsonc`, the `.ts`/`.js` family,
`.css`/`.scss`/`.less`, `.png`.

To see which rules exist and whether the tools are present, run `--list`:

```bash
python <this skill dir>/scripts/verify.py --list
```

Residual problems come in two kinds.

- The machine cannot fix them: real type errors, `ruff` violations with no
  autofix such as `PERF401`, and the class of `oxlint` warnings that has no
  autofix. Fix those by hand.
- The tool already fixed them: nothing to do, the script has written the
  result to disk.

Rerun the gate after fixing, until the exit code is 0. Exit code 2 means
the gate or the rules table itself is broken.

### Actually run it

The package's entry script must be executed once for real, against a
minimal sample. Static checks cannot catch runtime problems.

```bash
python <skill>/scripts/<entry>.py <minimal-sample>
```

### Report

Give one table with "file / residual problems / gate exit code". Write 0
when there are no residual problems. For compression results, include the
bytes saved.

## Extending

Adding a file type usually means editing `scripts/file-types.json` only,
with no code change.

1. Add a block under `types`: `id`, `suffixes`, plus the two command
   groups `fix` and `verify`. Write commands as arrays; the engine appends
   the file paths at the end.
2. When a tool accepts only some suffixes, also declare its `suffixes`
   under `tools`. The engine validates before running: handing a file to a
   tool that does not accept it fails loudly and exits, instead of
   producing a false failure that can never be fixed.
3. When a tool silently skips files it does not recognize, give it
   `no_target_markers`. Such output is then classified as a rules-table
   error instead of masquerading as a file problem.
4. When you need in-process validation (syntax, parsing, binary
   integrity), add a function in `scripts/checkers.py`, register it in
   `CHECKERS`, and reference it with `{"check": "name"}`; `only` can
   restrict it to specific file names. Checks that are not tied to a
   suffix and should apply to the whole package go into the top-level
   `package_checks`, and the engine runs them over every collected file —
   that is how `no-local-paths` is wired in.

`report_savings: true` makes the engine report byte counts before and
after optimization, which is useful for compression tools.

## Porting an existing Skill to a new corpus

Moving a built skill package from one dataset to another (a different
repo, bucket, or corpus) is not just a path change. The skeleton has
usually internalized the old corpus's shape, and it fails silently once
moved.

1. **Quantify first, then act.** Run the old corpus and the new corpus and
   produce a shape distribution table for each (field occurrence rate,
   dominant values, mechanism and extension share). Editing the recipe
   from impressions only bakes in the old corpus's bias.
2. **Watch for the dominant trait to flip.** The two corpora may have
   opposite shapes. What decides the default recipe is not the average but
   the single most common case. The old default recipe may cover only a
   few percent of the new corpus, and then you need a new recipe rather
   than a parameter tweak.
3. **Over-strict required fields kill the main recipe.** A field that
   virtually always appears in the old corpus may be rare in the new one.
   Leave it in `required` and the main recipe fails to render for most
   cases in the new corpus. Downgrade it to optional and validate on
   demand inside the constructor. A way to expose such over-strict config:
   make the self-check render **using only the parameters the recipe
   itself declares** (`required` ∪ `optional`), so that feeding one extra
   field that should not exist fails immediately.
4. **Samples must really exist.** Every Samples path in the docs must
   resolve to a real file inside the package or in the upstream repo;
   prefer paths inside the package. An invented sample path is worse than
   no sample at all: it makes the next person build against a file that
   does not exist.
5. **One change has to sync three places.** When the recipe directory
   changes, update in order: the recipe directory itself, the recipe
   documentation (decision tree plus sections), and the coverage survey
   document (a new recipe must come with its population evidence in the
   new corpus, meaning the number of manifests that support it). Miss any
   one of them and the self-check assertion "docs cover all recipes"
   stops you.
6. **Hard-coded parsing is a time bomb.** If a parser hard-codes column
   counts, heading levels, or header text, it will break on a new corpus.
   Make it structure-driven instead: recognize headers rather than
   positions, and headings at any level rather than `###`. Add an
   assertion that "rewriting the same line must be byte-identical", to
   protect the columns this skill does not own.

## Gotchas

One entry per thing the gate or its tools have surprised us with. Add a line
as soon as a new one shows up — this is where the density is.

- **The config that counts is the repo's own `.rumdl.toml`, not this
  machine's user config.** `rumdl` looks for `.rumdl.toml` upward from
  **each file** it is handed, not from the caller's directory, and once it
  finds one it drops the user config (`%APPDATA%\rumdl\rumdl.toml`)
  **whole** instead of merging key by key: any key the project file omits
  falls back to the built-in default, never to the user's value. Here that
  file is the workspace-root `.rumdl.toml`, holding
  `disable = ["MD013", "MD025", "MD029", "MD033"]` and
  `line-length = 120`, so **long lines, repeated H1s, ordered-list
  numbering, and inline HTML do not warn, and the gate will not stop
  them.** Those four rules are off because the repo switched them off, and
  the file ships with the repo, so the same holds for every package here on
  any machine. To judge a package at `rumdl`'s built-in strictness, ignore
  every config file: `rumdl check --no-config <file>` (alias `--isolated`;
  the verify stage has no such switch). Inline overrides survive it, which
  keeps one deliberate threshold while dropping the rest:
  `rumdl check --no-config --config 'MD013.line-length = 60' <file>`.
- `MD013` (only when not disabled) is what forces manual rewrapping. It
  measures display width: CJK characters count as 2 columns, with a
  default limit of 80. And being too wide is not enough on its own; there
  must also be a space at or past column 80. Pure-CJK long sentences
  often do not warn, while long sentences containing English and inline
  code always do. To calibrate the measured width, use an inline threshold
  — with `--no-config`, since this repo disables `MD013`:
  `rumdl check --no-config --config 'MD013.line-length = 60' <file>`. The
  number in the error message is the measured width, more reliable than
  counting by hand. Without `--no-config` the threshold alone reports
  nothing here: a disabled rule stays disabled no matter how low you set
  its limit.
- `MD025` (only when not disabled) is what demotes a body H1: with
  `title:` in the frontmatter, `rumdl` treats it as the document title,
  the body `#` becomes a "second level-1 heading", and `fmt`'s fix is to
  **demote that H1 to H2** — a silent structural change, which only
  demotes and never promotes. This repo's config disables the rule, so the
  effect does not occur here; and SKILL.md's frontmatter writes only
  `name` + `description` with no `title:`, so it stays safe on any
  machine.
- Three configuration traps. First, `rumdl config file` names the file it
  actually loaded, and prints `No configuration file found (using
  defaults)` when there is none — but it answers for the **cwd**: `config
  file` and `config get` resolve upward from the current directory, while
  `check` and `fmt` resolve upward from **each file**. When the two
  disagree, trust the `check` run. Second, `config get` needs a qualified
  key — `global.disable`, not `disable`, which exits 2 with `Unknown key:
  disable. Must be in the form global.key, MDxxx.key, or MDxxx`. The source
  tag on the line (`default` / `project config` / `user config`) is what
  says which file won. To list everything the project file sets in one
  shot, skip the per-key loop: `rumdl config --no-defaults` prints only the
  non-default keys, tagged `[from .rumdl.toml]`. Third, **an unrecognized
  key is ignored**: `rumdl` writes `[config warning] Unknown global option
  in <file>: <key>` to stderr and still exits 0, so a typo is
  indistinguishable from not writing the key at all. Every `rumdl` step in
  the rules table therefore carries `--deny-config-warnings`, which turns
  that warning into exit 2. Two limits on it. It bites only in the verify
  stage: the gate ignores repair-step exit codes, and `--fix` / plain `fmt`
  still write the file before exiting 2. And it covers unknown option
  names, unknown rule names (`disable = ["MD999"]`, `[MD13]`) and unknown
  top-level keys — but **not** a value of the wrong type:
  `line-length = "wide"` is accepted without a single warning. After
  editing config, `get` every key.
- `rumdl fmt`'s exit code does not indicate whether anything changed; only
  `rumdl fmt --check` returns 1 when a change is needed. Calling a file
  clean requires both `check` and `fmt --check`.
- `oxlint` reports rule problems as warnings with exit code 0 by default,
  so running it alone misses warnings. The verify stage must pass
  `--deny-warnings`, or the gate lets unfixed warnings through.
- `oxlint` only accepts the JS/TS family. Handing it `.css` / `.scss` /
  `.less` reports "No files found" and exits 1, which is a false failure;
  CSS goes through `oxfmt` only.
- Suffixes that `oxfmt` does not support fail loudly and exit, so do not
  put things like `.vue` / `.svelte` into the rules table.
- `oxipng` returns 0 whether or not it could still compress, so that
  column cannot be called clean by exit code. Rely on the builtin
  `png-integrity`, which rechecks chunk structure and CRCs.
- `oxipng` only does lossless compression and keeps all metadata by
  default. For smaller files you can add `-s` or raise `-o` by hand, but
  that goes beyond "compression"; do not enable it by default.
- `ruff check` enables 413 rules by default, including `PLW`-style ones,
  far beyond the classic `E4/E7/E9/F`. Verified with `--isolated`: this is
  the built-in default set of 0.16.x.
- **A bare `ruff check` stops being a check once the host config sets
  `fix`.** A repo whose `pyproject.toml` carries `fix = true` or
  `fix-only = true` — this one carries both — turns it into a silent
  rewriter: exit 0, empty output, and the violations drop out of the
  report. `--no-fix` closes only the first half, because `fix-only` is a
  separate key that keeps swallowing unfixable ones. Hence the verify
  stage's `--no-fix --no-fix-only`; drop either flag and the gate can no
  longer fail on a violation that has no autofix.
- `ty check --fix` autofixes very little; a non-zero return is normal and
  the real fixes are manual. ty resolves third-party packages from the
  project root's `pyproject.toml` and `.venv` by default; when a package's
  scripts use only the standard library, no extra flags are needed.
- The gate leaves no trace. Syntax compilation runs through in-process
  `compile()`, and `ruff` and `rumdl` always get `--no-cache`. So no
  `__pycache__`, `.ruff_cache`, or `.rumdl_cache` appears in the package
  directory.
- `no-local-paths` only matches **this machine's real home directory**
  (`Path.home()` and the values of `USERPROFILE` / `HOME`; both slash
  styles, case-insensitive), so the `C:/Users/someone` used as an example
  in docs does not false-positive, and binary files are skipped silently.
  It can stay on by default precisely because it does not false-positive —
  a check that false-positives gets turned off sooner or later.
- `skill-frontmatter` rejects any top-level key outside `name`,
  `description`, `agent_created` and `disable-model-invocation`. The set is a
  constant in `checkers.py` rather than a rules-table entry, so widening it
  takes a code change: the keys it catches are always display names and
  version numbers that no reader consumes, and each one is a claim that
  quietly goes stale.
- Do not remove the syntax-compilation stage. An older `ruff format` used to
  strip the parentheses from `except (A, B):` regardless of target version
  (a bug since fixed), and this stage is what turns that rewrite into a loud
  failure the next time the gate runs.
- At `target-version = "py314"` ruff drops those parentheses *on purpose*
  (PEP 758), and the result parses on 3.14+ only. Code that has to keep
  parsing on an older interpreter therefore avoids the tuple form entirely:
  the `no-local-paths` checker keeps its two failure modes in separate
  `except` clauses, which no formatter rewrites. A bare `# fmt: skip` is the
  other way out, but it stops protecting the line the moment the comment is
  lost, and prose after the directive silently disables it.
- **`ruff format` reaches into `.md` code fences, and in a CRLF package that
  is a line-ending bug wearing a formatting costume.** Fences marked
  `python` / `py` get formatted; those marked `text` or left bare do not.
  The diff then reads as a no-op: the
  `-` and `+` lines are identical to the eye, while the fence's line endings
  really do flip and the package's own line-ending invariant breaks.
  `pyproject.toml` therefore carries `[tool.ruff.format] exclude = ["*.md"]`,
  which keeps markdown out of `ruff`'s reach, so a hand-run `ruff format .`
  no longer edits the docs. Moving `ruff format` into the markdown group
  instead is not the way out: the rules table refuses to load, because a
  step's tool must accept the type's suffixes (`markdown hands .md to ruff,
  which only accepts .py`). Markdown content belongs to `rumdl`; `ruff` only
  ever sees `.py`.

## Anti-patterns

- Delivering after editing a file without rerunning the gate, or turning a
  rule off to go green when the gate reports errors.
- Creating `ruff.toml` / `ty.toml` to downgrade the code checks, or relaxing a
  layout rule in `.rumdl.toml` without recording which rule and why. Layout
  rules may be relaxed; content-quality rules may not.
- Writing `description` as an introduction with no branches, so the skill
  never triggers.
- Cramming all long specs into SKILL.md, or conversely stuffing all the
  steps into `references/`.
- Using third-party libraries in package scripts: others cannot install
  them, and the gate cannot run either.
- Putting `# ty: ignore[...]` in the middle of an expression, which
  comments out the trailing `)` along with it.
- Adding behaviour-changing switches such as `--fix-dangerously` to the
  gate for convenience.
- Putting a skill package inside a repo that has its own CI while using
  extensions the host CI scans for data files. Read the host's CI script
  before naming files: the Scoop bucket CI validates "any changed `.json`
  in the repo" against the manifest schema, so non-manifest data files
  have to be named `.jsonc`.
