#!/usr/bin/env python3
"""Fork-parity gate for this skills monorepo.

WHY THIS EXISTS
    The scoop-* and anchor-* skills are distributed as copy-paste forks so each
    skill stays self-contained on another machine. That is intentional. The cost
    is drift: a fix that lands in one fork silently misses the others, and a
    helper that shares a name across forks can grow an incompatible signature
    while every fork still passes its own self-test. There is no CI here, so
    today the only thing keeping the forks coherent is a human remembering to
    propagate. This script turns "engineer remembered" into "gate refused".

DESIGN
    It never imports the skill code and never touches the network or $Scoop —
    it is pure filesystem + hashing + regex, so it is deterministic and offline.
    Three tiers:

      1. byte-identical  : files declared identical must hash identical across
                           every fork in the group (the anchor engine trio).
      2. symbol-present  : markers that MUST exist in every fork of a family
                           (the caching / --profile / rehash-order additions).
                           Dropping one in any fork is a hard failure — this is
                           what pins the propagation so it cannot regress.
      3. value-parity    : a canonical value extracted per fork (lint rule ids,
                           env-var lookup, key function signatures). All forks
                           MUST agree, UNLESS the divergence is explicitly
                           recorded in RECORDED_DIVERGENCES. New, unrecorded
                           drift fails; a recorded divergence is a loud warning;
                           a recorded divergence that has since been unified is
                           reported so you can retire the allowance.

    Run from the repo root:  python tools/parity_check.py
    Exit 0 clean, 1 on any hard failure. Exit 0 with warnings still means the
    tree matches the recorded baseline.

Exit-code contract (mirrors the skills themselves): 0 = parity holds, 1 = drift.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    """Read a repo-relative file; return '' if missing (a missing file is itself
    a parity failure the caller will surface via the empty hash / no-match)."""
    path = ROOT / rel
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except IsADirectoryError:
        return ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scoop_forks() -> list[str]:
    return sorted(d.name for d in ROOT.glob("scoop-*") if (d / "scripts").is_dir())


def _anchor_forks() -> list[str]:
    return sorted(d.name for d in ROOT.glob("anchor-*") if (d / "scripts").is_dir())


# --------------------------------------------------------------------------- #
# Tier 1 — byte-identical groups
# --------------------------------------------------------------------------- #
BYTE_IDENTICAL_GROUPS = [
    {
        "name": "anchor engine (corner_skill/audit/bank)",
        "files": [
            "scripts/corner_skill.py",
            "scripts/corner_audit.py",
            "scripts/corner_bank.py",
        ],
        "note": (
            "the anchor engine is deliberately language-agnostic; only "
            "corner_config.py may differ (3 identity constants)."
        ),
    },
]


def check_byte_identical(forks: list[str]) -> list[str]:
    """Return a list of human-readable failure strings (empty == pass)."""
    failures: list[str] = []
    for group in BYTE_IDENTICAL_GROUPS:
        for rel in group["files"]:
            hashes = {}
            present: list[str] = []
            for fork in forks:
                full = ROOT / fork / rel
                if not full.is_file():
                    continue
                present.append(fork)
                hashes[fork] = _sha(_read(f"{fork}/{rel}"))
            distinct = set(hashes.values())
            if len(distinct) > 1:
                odd = ", ".join(sorted(hashes))
                failures.append(
                    f"[byte-identical] {group['name']}: {rel} differs across "
                    f"{odd} — this file is declared identical; reconcile it or "
                    f"move the language-specific bits into corner_config.py."
                )
    return failures


# --------------------------------------------------------------------------- #
# Tier 2 — required symbols in every fork of a family
# --------------------------------------------------------------------------- #
REQUIRED_SYMBOLS = {
    "scoop": {
        "scripts/sm_lib.py": [
            r"def load_recipes\(",
            r"_recipes_cache",
            r"def parse_summary\(",
            r"_parse_summary_memo",
            r"def scan_line_endings\(",
        ],
        "scripts/scoop_manifest.py": [
            r"def cmd_lint\(",
            r"--profile",
            r"# Recompute hash",  # pins the rehash-after-bump ordering fix
        ],
    },
}


def check_required_symbols(forks: list[str]) -> list[str]:
    failures: list[str] = []
    for family, spec in REQUIRED_SYMBOLS.items():
        prefix = f"{family}-" if family != "scoop" else "scoop-"
        members = [f for f in forks if f.startswith(prefix)]
        for rel, patterns in spec.items():
            for fork in members:
                text = _read(f"{fork}/{rel}")
                failures.extend(
                    f"[symbol-present] {fork}/{rel}: missing required marker "
                    f"{pat!r} — this optimization/fix is expected in every "
                    f"{family} fork; propagate it."
                    for pat in patterns
                    if not re.search(pat, text)
                )
    return failures


# --------------------------------------------------------------------------- #
# Tier 3 — value parity, with an explicit recorded-divergence ledger
# --------------------------------------------------------------------------- #
def _extract_rule_ids(text: str) -> tuple[str, ...]:
    return tuple(sorted(set(re.findall(r'["\'](E\d{3}|W\d{3})["\']', text))))


def _extract_scoop_env(text: str) -> str:
    m = re.search(r"SCOOP_ENV_VARS?\s*=\s*(.+)", text)
    return m.group(1).strip() if m else "<none>"


def _extract_signature(text: str, name: str) -> str:
    m = re.search(rf"def {name}\((.*?)\)", text, re.DOTALL)
    if not m:
        return "<missing>"
    params = [
        p.strip()
        for p in m.group(1).split(",")
        if p.strip() and not p.strip().startswith("#")
    ]
    return f"{name}({', '.join(params)})"


VALUE_CHECKS = {
    "scoop lint rule-id set (sm_lib.py)": (
        "scripts/sm_lib.py",
        _extract_rule_ids,
    ),
    "scoop env-var lookup (sm_lib.py)": (
        "scripts/sm_lib.py",
        _extract_scoop_env,
    ),
    "scoop find_repo_root signature (sm_lib.py)": (
        "scripts/sm_lib.py",
        lambda t: _extract_signature(t, "find_repo_root"),
    ),
    "scoop insert_summary_row signature (sm_lib.py)": (
        "scripts/sm_lib.py",
        lambda t: _extract_signature(t, "insert_summary_row"),
    ),
}

# Divergences we know about today and are deliberately tolerating. Each entry is
# the value-check key -> a short reason. A check NOT listed here that diverges is
# an unexpected new drift and fails the gate. Keep this ledger honest: when you
# reconcile a fork, delete the entry (the tool tells you when one is now unified).
RECORDED_DIVERGENCES = {
    "scoop lint rule-id set (sm_lib.py)": (
        "accidental drift (main-plus grew E012/E013); reconcile when unifying"
    ),
    "scoop env-var lookup (sm_lib.py)": (
        "borderline: main-plus reads SCOOP+Scoop, extras read Scoop only"
    ),
    "scoop find_repo_root signature (sm_lib.py)": (
        "documented: main-plus (start) vs extras (explicit, start); different "
        "resolution precedence"
    ),
    "scoop insert_summary_row signature (sm_lib.py)": (
        "accidental drift: main-plus dict form vs extras positional; reconcile"
    ),
}


def check_value_parity(forks: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (failures, warnings, info)."""
    scoop = [f for f in forks if f.startswith("scoop-")]
    failures: list[str] = []
    warnings: list[str] = []
    info: list[str] = []
    for key, (rel, extract) in VALUE_CHECKS.items():
        values = {fork: extract(_read(f"{fork}/{rel}")) for fork in scoop}
        distinct = set(values.values())
        if len(distinct) <= 1:
            if key in RECORDED_DIVERGENCES:
                info.append(
                    f"[value-parity] {key}: now UNIFIED across forks — you can "
                    f"remove it from RECORDED_DIVERGENCES."
                )
            continue
        if key in RECORDED_DIVERGENCES:
            rendered = "; ".join(f"{f}={values[f]}" for f in sorted(values))
            warnings.append(
                f"[value-parity] {key}: DIVERGED (recorded — "
                f"{RECORDED_DIVERGENCES[key]}) :: {rendered}"
            )
        else:
            rendered = "; ".join(f"{f}={values[f]}" for f in sorted(values))
            failures.append(
                f"[value-parity] {key}: UNRECORDED divergence — {rendered}. "
                f"Either reconcile the forks or add an explained entry to "
                f"RECORDED_DIVERGENCES."
            )
    return failures, warnings, info


def main() -> int:
    forks = _scoop_forks() + _anchor_forks()
    if not forks:
        print("no fork families found under the repo root", file=sys.stderr)
        return 1

    failures: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    failures += check_byte_identical(_anchor_forks())
    failures += check_required_symbols(forks)
    f3, w3, i3 = check_value_parity(forks)
    failures += f3
    warnings += w3
    info += i3

    # files present on disk but covered by no tier -> nudge (not a failure)
    uncovered = []
    for fork in _scoop_forks():
        for py in sorted((ROOT / fork / "scripts").glob("*.py")):
            rel = f"scripts/{py.name}"
            family = REQUIRED_SYMBOLS.get("scoop", {})
            if rel not in family and not any(
                rel in g["files"] for g in BYTE_IDENTICAL_GROUPS
            ):
                uncovered.append(f"{fork}/{rel}")
    if uncovered:
        info.append(
            "[coverage] not covered by any parity tier (fine, but consider a "
            "rule): " + ", ".join(uncovered)
        )

    for line in warnings:
        print(f"WARN {line}")
    for line in info:
        print(f"INFO {line}")
    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)

    print(
        f"\nparity: {len(failures)} failure(s), {len(warnings)} recorded "
        f"divergence(s), {len(info)} note(s) across {len(forks)} forks"
    )
    if failures:
        print(
            "RESULT: drift detected — reconcile or record the divergence.",
            file=sys.stderr,
        )
        return 1
    print("RESULT: parity holds (baseline locked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
