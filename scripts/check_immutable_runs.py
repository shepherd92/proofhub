#!/usr/bin/env python3
"""Reject changes to runs that were already published at a Git base revision."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any

import yaml

try:
    from scripts.prepare_projects import UniqueKeyLoader
except ModuleNotFoundError:
    from prepare_projects import UniqueKeyLoader


def git(root: Path, arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def commit_id(root: Path, revision: str) -> str:
    try:
        result = git(root, ["rev-parse", "--verify", f"{revision}^{{commit}}"])
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot resolve base revision {revision!r}: {detail}") from exc
    return result.stdout.decode("ascii").strip()


def changed_run_roots(root: Path, base: str, head: str) -> set[PurePosixPath]:
    result = git(
        root,
        ["diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", base, head, "--", "content/projects"],
    )
    try:
        paths = result.stdout.decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise ValueError("Git contains a changed path that is not valid UTF-8") from exc
    runs: set[PurePosixPath] = set()
    for text in paths:
        if not text:
            continue
        parts = PurePosixPath(text).parts
        if len(parts) >= 6 and parts[0:2] == ("content", "projects") and parts[3] == "runs":
            runs.add(PurePosixPath(*parts[:5]))
    return runs


def manifest_at(root: Path, revision: str, run_root: PurePosixPath) -> dict[str, Any] | None:
    spec = f"{revision}:{run_root.as_posix()}/run.yaml"
    result = git(root, ["show", spec], check=False)
    if result.returncode != 0:
        return None
    try:
        value = yaml.load(result.stdout.decode("utf-8"), Loader=UniqueKeyLoader)
    except (UnicodeError, yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"cannot parse base manifest {spec}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"base manifest {spec} is not a YAML mapping")
    return value


def immutable_changes(root: Path, base: str, head: str = "HEAD") -> list[PurePosixPath]:
    resolved_base = commit_id(root, base)
    resolved_head = commit_id(root, head)
    violations: list[PurePosixPath] = []
    for run_root in sorted(changed_run_roots(root, resolved_base, resolved_head)):
        manifest = manifest_at(root, resolved_base, run_root)
        if manifest is not None and manifest.get("draft", False) is not True:
            violations.append(run_root)
    return violations


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Git revision to compare against")
    parser.add_argument("--head", default="HEAD", help="new Git revision (default: HEAD)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        violations = immutable_changes(root, args.base, args.head)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Immutable-run check failed: {exc}", file=sys.stderr)
        return 1
    if violations:
        for run_root in violations:
            print(f"Published run is immutable: {run_root.as_posix()}", file=sys.stderr)
        print("Create a new run directory for corrections, retries, forks, or continuations.", file=sys.stderr)
        return 1
    print("No previously published run was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
