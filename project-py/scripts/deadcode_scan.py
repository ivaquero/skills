#!/usr/bin/env python3
"""Report top-level definitions that nothing in the project refers to.

Standard library only, offline, report-only:

    python deadcode_scan.py <package dir> [more dirs ...] [--root PATH]

Why a bare reference count is not enough
----------------------------------------
The count scans plain *text*, so anything that spells the name out stays safe
without extra work: ``__all__ = ["name"]``, ``getattr(obj, "name")`` and the
``pkg.mod:name`` form under ``[project.scripts]`` are all just text, and text
is what gets counted.

One shape leaves no text behind -- a function registered through a *decorator
argument*::

    @app.get("/evaluate/jobs/{job_id}")
    def evaluate_job_status(job_id: str): ...

The name appears exactly once in the whole project, yet the route is live. So
every decorated top-level function is treated as reachable and is never
reported.

Finding one dead function often orphans the helpers that only it called, so
the candidate set is recomputed until it stops growing; late finds are marked
``chain``.

Exit code 0 when nothing was found, 1 when candidates were reported, 2 when
the arguments or the source tree cannot be read. Nothing is ever rewritten.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ipynb_checkpoints",
        "node_modules",
        "build",
        "dist",
        "site-packages",
    }
)

#: Extensions that may mention a name. A hit in *any* of them counts as a
#: reference, so front-end callers and packaging metadata keep a symbol alive.
REF_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".pyw",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".vue",
        ".html",
        ".htm",
        ".toml",
        ".ini",
        ".cfg",
        ".json",
        ".yaml",
        ".yml",
        ".sh",
        ".bash",
        ".md",
        ".txt",
    }
)

#: Source extensions whose top-level definitions become candidates.
SRC_SUFFIXES = frozenset({".py", ".pyi"})

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Guards against a pathological chain; real projects converge in one round.
MAX_ROUNDS = 8


@dataclass(frozen=True)
class Definition:
    """One top-level definition that may or may not still be referenced."""

    name: str
    path: Path
    line: int
    end_line: int
    decorated: bool


def _iter_files(root: Path) -> list[Path]:
    """Every reference-bearing file under ``root``, skipping vendored trees."""
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in REF_SUFFIXES
        and not any(part in SKIP_DIRS for part in path.parts)
    ]


def _read(path: Path) -> str | None:
    """Read UTF-8 text, or None when the file cannot be read at all.

    The two failure modes stay in separate ``except`` clauses on purpose: with
    ``target-version = "py314"`` a formatter rewrites ``except (A, B):`` into
    ``except A, B:`` (PEP 758), which only parses on 3.14+.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError:
        return None


def _defined_names(node: ast.stmt) -> list[str]:
    """Names a single top-level statement binds, minus dunder bookkeeping.

    ``__all__``, ``__version__`` and friends are module protocol, not code
    anyone calls, so they are never candidates -- while still counting as
    *references* to whatever they name, which is what keeps exported symbols
    alive.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        names = [node.name]
    elif isinstance(node, ast.Assign):
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names = [node.target.id]
    else:
        return []
    return [name for name in names if not name.startswith("__")]


def _top_level_defs(path: Path, text: str) -> list[Definition]:
    """Every top-level def, class or assignment in one module."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    defs: list[Definition] = []
    for node in tree.body:
        decorated = bool(getattr(node, "decorator_list", []))
        end_line = getattr(node, "end_lineno", None) or node.lineno
        defs.extend(
            Definition(
                name=name,
                path=path,
                line=node.lineno,
                end_line=end_line,
                decorated=decorated,
            )
            for name in _defined_names(node)
        )
    return defs


def _load_corpus(root: Path) -> dict[Path, str]:
    """Read every reference-bearing file under ``root``, keyed by path."""
    texts: dict[Path, str] = {}
    for path in _iter_files(root):
        text = _read(path)
        if text is not None:
            texts[path] = text
    return texts


def scan(
    src_dirs: list[Path],
    root: Path,
    on_phase: Callable[[str, float], None] | None = None,
) -> list[tuple[Definition, bool]]:
    """Return ``(definition, is_chain)`` for everything nothing refers to.

    ``on_phase`` is an optional ``(label, seconds)`` callback used solely by
    ``--profile``. When it is ``None`` (the default) no timing code runs and the
    result is identical to the plain scan.
    """

    def _phase(label: str, started: float) -> None:
        if on_phase is not None:
            on_phase(label, time.perf_counter() - started)

    started = time.perf_counter() if on_phase is not None else 0.0
    texts = _load_corpus(root)
    _phase("load corpus", started)

    started = time.perf_counter() if on_phase is not None else 0.0
    defs: list[Definition] = []
    for path, text in texts.items():
        if path.suffix.lower() not in SRC_SUFFIXES:
            continue
        if not any(path.is_relative_to(directory) for directory in src_dirs):
            continue
        defs.extend(_top_level_defs(path, text))
    _phase("collect definitions", started)

    # Tokenize the corpus exactly once, remembering which identifiers sit on
    # which line. Every round then decrements only the tokens on the lines a
    # definition actually drops -- no per-round rebuild of the whole corpus and
    # no re-splitting a file once per dropped definition. Counts only ever
    # decrease, so the fixed point (a name whose sole textual occurrence is its
    # own definition) is identical to recounting from scratch, while total work
    # drops from O(rounds x corpus) to O(corpus + dropped tokens).
    started = time.perf_counter() if on_phase is not None else 0.0
    line_tokens: dict[Path, dict[int, list[str]]] = {}
    counts: Counter[str] = Counter()
    for path, text in texts.items():
        per_line: dict[int, list[str]] = {}
        for lineno, row in enumerate(text.splitlines(), start=1):
            tokens = IDENT_RE.findall(row)
            if tokens:
                per_line[lineno] = tokens
                counts.update(tokens)
        line_tokens[path] = per_line
    _phase("tokenize corpus", started)

    started = time.perf_counter() if on_phase is not None else 0.0
    dropped: dict[Path, set[int]] = {}
    reported: list[tuple[Definition, bool]] = []
    seen: set[str] = set()

    for round_index in range(MAX_ROUNDS):
        fresh = [
            definition
            for definition in defs
            if not definition.decorated
            and definition.name not in seen
            and counts[definition.name] <= 1
        ]
        if not fresh:
            break
        for definition in fresh:
            seen.add(definition.name)
            lines = dropped.setdefault(definition.path, set())
            per_line = line_tokens[definition.path]
            for lineno in range(definition.line, definition.end_line + 1):
                if lineno in lines:
                    continue  # already dropped on an earlier round
                lines.add(lineno)
                for token in per_line.get(lineno, ()):
                    counts[token] -= 1
            reported.append((definition, round_index > 0))
    _phase("resolve fixed point", started)
    return reported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deadcode_scan.py",
        description="Report top-level definitions nothing in the project refers to.",
    )
    parser.add_argument(
        "src",
        nargs="+",
        metavar="DIR",
        help="directory whose top-level definitions are candidates",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="project root used as the reference corpus (default: cwd)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="print per-phase wall-clock timings to stderr",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    src_dirs = [Path(directory).resolve() for directory in args.src]
    missing = [directory for directory in src_dirs if not directory.is_dir()]
    if missing:
        print(
            "not a directory: " + ", ".join(str(path) for path in missing),
            file=sys.stderr,
        )
        return 2

    if args.profile:
        timings: list[tuple[str, float]] = []

        def _record(label: str, seconds: float) -> None:
            timings.append((label, seconds))

        reported = scan(src_dirs, root, on_phase=_record)
        for label, seconds in timings:
            print(f"[profile] {label}: {seconds * 1000:.1f} ms", file=sys.stderr)
        total = sum(secs for _, secs in timings)
        print(f"[profile] total: {total * 1000:.1f} ms", file=sys.stderr)
    else:
        reported = scan(src_dirs, root)
    if not reported:
        print("no unreferenced top-level definition found")
        return 0
    for definition, is_chain in reported:
        try:
            shown = definition.path.relative_to(root)
        except ValueError:
            shown = definition.path
        marker = "  (chain)" if is_chain else ""
        print(f"{shown}:{definition.line}  {definition.name}{marker}")
    print(f"\n{len(reported)} candidate(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
