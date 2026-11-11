"""skill-draft builtin checkers: the validations that do not go through an external tool.

Each checker has the signature `(path: Path) -> list[str]`, returns problem descriptions,
and an empty list means it passed. Reference one in `file-types.json` with
`{"check": "name"}`; `{"check": "name", "only": ["SKILL.md"]}` limits it to those file names.

To extend: add a function here and register it in `CHECKERS`; the engine needs no change.
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import zlib
from collections.abc import Callable
from functools import cache
from pathlib import Path

FRONTMATTER_END = "\n---"
NAME_RE = re.compile(r"^name:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
DESC_RE = re.compile(r"^description:", re.MULTILINE)
TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):")
ALLOWED_FRONTMATTER_KEYS = (
    "name",
    "description",
    "agent_created",
    "disable-model-invocation",
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CRC_LIMIT = 8 * 1024 * 1024
IHDR_LENGTH = 13

Checker = Callable[[Path], list[str]]


def _read_text(path: Path) -> tuple[str, list[str]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except (UnicodeDecodeError, OSError) as exc:
        return "", [f"cannot read as UTF-8: {exc}"]


def python_syntax(path: Path) -> list[str]:
    """Compile the source once to catch syntax-level errors; the cheapest regression net."""
    source, problems = _read_text(path)
    if problems:
        return problems
    try:
        compile(source, str(path), "exec")
    except (SyntaxError, ValueError) as exc:
        return [f"syntax compile failed: {exc}"]
    return []


def json_parse(path: Path) -> list[str]:
    """Confirm the file parses as JSON, catching hand-written syntax errors in data files."""
    source, problems = _read_text(path)
    if problems:
        return problems
    try:
        json.loads(source)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"JSON does not parse: {exc}"]
    return []


def skill_frontmatter(path: Path) -> list[str]:
    """Check the frontmatter has name == directory name, a description, and no extra keys.

    `ALLOWED_FRONTMATTER_KEYS` is the single source of truth for the exception list, and
    the exception only ever runs one way: a key the host writes in for itself (a platform
    state flag such as `agent_created`) is not an extra key the author added. Anything the
    author wants to say belongs in the body, not in a frontmatter field.

    Length is deliberately not gated: how long a description should be follows from how
    many trigger branches it needs, so a hard limit would only force useful branches out.
    """
    if path.name != "SKILL.md":
        return []
    source, problems = _read_text(path)
    if problems:
        return problems
    if not source.startswith("---"):
        return ["frontmatter missing"]
    end = source.find(FRONTMATTER_END, 3)
    if end == -1:
        return ["frontmatter not closed"]
    block = source[3:end]
    found: list[str] = []
    match = NAME_RE.search(block)
    if match is None:
        found.append("frontmatter is missing name")
    else:
        name = match.group(1).strip().strip("\"'")
        if name != path.parent.name:
            found.append(
                f"frontmatter name={name!r} does not match "
                f"directory name {path.parent.name!r}"
            )
    if DESC_RE.search(block) is None:
        found.append("frontmatter is missing description")
    found.extend(_extra_frontmatter_keys(block))
    return found


def _extra_frontmatter_keys(block: str) -> list[str]:
    """Flag top-level keys outside the allowed set; extra keys drift out of date silently.

    Only the keys themselves are judged, not the indented list items under them.
    """
    allowed = ", ".join(ALLOWED_FRONTMATTER_KEYS)
    found: list[str] = []
    for line in block.splitlines():
        match = TOP_LEVEL_KEY_RE.match(line)
        if match is not None and match.group(1) not in ALLOWED_FRONTMATTER_KEYS:
            found.append(
                f"frontmatter key {match.group(1)!r} is not allowed; allowed: {allowed}"
            )
    return found


def _walk_png_chunks(data: bytes, found: list[str], seen: set[bytes]) -> int:
    """Walk the PNG chunk by chunk, returning the end offset and recording structural problems."""
    check_crc = len(data) <= PNG_CRC_LIMIT
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[offset : offset + 8])
        body_start = offset + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            found.append(f"chunk {tag!r} declares length {length} past end of file")
            return offset
        seen.add(tag)
        if tag == b"IHDR":
            if length != IHDR_LENGTH:
                found.append(f"IHDR length should be {IHDR_LENGTH}, got {length}")
            else:
                width, height = struct.unpack(">II", data[body_start : body_start + 8])
                if width == 0 or height == 0:
                    found.append(f"IHDR has invalid dimensions: {width}x{height}")
        if check_crc:
            expected = struct.unpack(">I", data[body_end : body_end + 4])[0]
            actual = zlib.crc32(data[offset + 4 : body_end]) & 0xFFFFFFFF
            if expected != actual:
                found.append(f"chunk {tag!r} CRC mismatch")
        offset = body_end + 4
        if tag == b"IEND":
            break
    return offset


def png_integrity(path: Path) -> list[str]:
    """oxipng rewrites the binary in place; re-check chunk structure and CRCs to confirm it held."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"cannot read: {exc}"]
    if not data.startswith(PNG_SIGNATURE):
        return ["not a PNG: signature mismatch"]
    found: list[str] = []
    seen: set[bytes] = set()
    offset = _walk_png_chunks(data, found, seen)
    if b"IHDR" not in seen:
        found.append("missing IHDR")
    if b"IEND" not in seen:
        found.append("missing IEND")
    elif offset != len(data):
        found.append(f"{len(data) - offset} extra bytes after IEND")
    return found


@cache
def _home_needles() -> tuple[str, ...]:
    """The spellings of this machine's home directory, used to spot hard-coded local paths.

    Only the home directory that really exists on this machine is used, so an
    illustrative `C:/Users/someone` in documentation never triggers a false positive.
    Anything shorter than 4 characters (such as `C:/`) is dropped, so a whole drive
    never becomes a match pattern.

    The result is cached: it depends only on the environment and the home directory,
    which are fixed for the life of the process, yet before this it was rebuilt for
    every `.py` file the gate scanned.
    """
    candidates = {str(Path.home())}
    for var in ("USERPROFILE", "HOME"):
        value = os.environ.get(var)
        if value:
            candidates.add(value)
    needles: list[str] = []
    for item in sorted(candidates):
        for form in (item, item.replace("\\", "/")):
            if len(form) > 3 and form not in needles:
                needles.append(form)
    return tuple(needles)


@cache
def _home_needles_lowered() -> tuple[tuple[str, str], ...]:
    """Pair each home-path needle with its lowercase form, computed once.

    Matching lowercases both sides, but the error message quotes the original
    spelling, so the pairs carry both and the scan loop does no per-file lowering.
    """
    return tuple((needle, needle.lower()) for needle in _home_needles())


def no_local_paths(path: Path) -> list[str]:
    """A skill package must not carry this machine's absolute paths: elsewhere that text is wrong.

    Only this machine's home directory is a needle. A platform-standard location such
    as `C:/Windows/Fonts` is deliberately out of scope: it is the same on every machine
    of that platform, so it is a portability question, not a stale local path.

    Binary files are skipped silently; portability is not their concern, so a decode
    failure is not counted as a problem.

    The two failure modes deliberately stay in separate clauses. Written as one
    tuple, `except (UnicodeDecodeError, OSError):`, the formatter would rewrite it
    at `target-version = "py314"` into the bare 3.14-only form (PEP 758) and this
    file would stop parsing on the 3.13 interpreter the gate may be running on.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    except OSError:
        return []
    lowered = source.lower()
    for needle, needle_lower in _home_needles_lowered():
        if needle_lower in lowered:
            return [
                (
                    f"found this machine's absolute path {needle}; a skill package gets "
                    "copied to other machines and paths, where a hard-coded path is simply "
                    "wrong -- use ~ or the <this skill dir> placeholder instead"
                )
            ]
    return []


# Self-check entry points, keyed by script name. They are discovered from each
# package's scripts/ directory, so adding a package needs no change here. A
# package is checked once, through its SKILL.md (see the `only` filter in the
# rules table). corner_skill.py is deliberately absent: run without arguments
# it writes a brief file into the repo root.
SELFCHECK_ENTRIES: dict[str, tuple[str, ...]] = {
    "selfcheck.py": (),
    "sm_selftest.py": (),
    "corner_audit.py": (),
    "corner_bank.py": (),
    "check_style.py": ("--selfcheck",),
    "checkers.py": (),
}
SELFCHECK_TIMEOUT = 120


def package_selfcheck(path: Path) -> list[str]:
    """Run the owning package's own self-tests, which the gate otherwise skips.

    The gate runs ruff/ty/rumdl but not the per-package round-trip tests, and
    those tests do not run ruff/ty. Wiring them into package_checks closes that
    gap whenever the gate is aimed at a package tree. sys.executable keeps the
    child on the same interpreter as the gate.
    """
    scripts = path.parent / "scripts"
    if not scripts.is_dir():
        return []
    problems: list[str] = []
    for name, args in sorted(SELFCHECK_ENTRIES.items()):
        entry = scripts / name
        if not entry.is_file():
            continue
        try:
            result = subprocess.run(
                [sys.executable, str(entry), *args],
                capture_output=True,
                text=True,
                timeout=SELFCHECK_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            problems.append(f"{name}: self-check timed out ({exc})")
            continue
        except OSError as exc:
            problems.append(f"{name}: cannot start self-check ({exc})")
            continue
        if result.returncode != 0:
            tail = (result.stdout + result.stderr).strip().splitlines()[-8:]
            problems.append(
                f"{name}: self-check exit {result.returncode}: " + " | ".join(tail)
            )
    return problems


CHECKERS: dict[str, Checker] = {
    "python-syntax": python_syntax,
    "json-parse": json_parse,
    "skill-frontmatter": skill_frontmatter,
    "png-integrity": png_integrity,
    "no-local-paths": no_local_paths,
    "package-selfcheck": package_selfcheck,
}
