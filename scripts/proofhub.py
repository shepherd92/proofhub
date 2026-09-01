#!/usr/bin/env python3
"""Create or import safe draft Proof Relay projects and runs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Callable

import yaml

try:
    from scripts.prepare_projects import ID_PATTERN, ProjectBuilder, UniqueKeyLoader
except ModuleNotFoundError:  # Direct execution adds scripts/, rather than the repository, to sys.path.
    from prepare_projects import ID_PATTERN, ProjectBuilder, UniqueKeyLoader


def clean_label(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise ValueError(f"{field} may not contain control characters or newlines")
    return cleaned


def validate_id(value: str, field: str) -> str:
    if ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must match {ID_PATTERN.pattern}")
    return value


def dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def atomic_directory(
    destination: Path,
    populate: Callable[[Path], None],
    validate: Callable[[Path], None] | None = None,
) -> None:
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise ValueError(f"refusing to write through symlinked directory: {parent}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    for sibling in parent.iterdir():
        if sibling.name.casefold() == destination.name.casefold():
            raise FileExistsError(f"a case-equivalent destination already exists: {sibling}")
    temporary_parent = Path(tempfile.mkdtemp(prefix="_proofhub-", dir=parent))
    temporary = temporary_parent / destination.name
    temporary.mkdir()
    try:
        populate(temporary)
        if validate is not None:
            validate(temporary)
        os.replace(temporary, destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    finally:
        if temporary_parent.exists():
            shutil.rmtree(temporary_parent)


def draft_run_manifest(
    run_id: str,
    parents: list[dict[str, str]],
    submitted_by: str | None = None,
    *,
    summary: str = "Draft run; replace this with a factual result summary.",
    outcome: str = "no_progress",
    architecture_type: str = "single_agent",
    architecture_description: str = "Replace with the architecture used for this run.",
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "draft": True,
        "parents": parents,
        "execution": {
            "status": "imported",
            "started_at": None,
            "ended_at": None,
            "wall_seconds": None,
        },
        "agent": {
            "framework": "not-recorded",
            "version": None,
            "architecture": {
                "type": architecture_type,
                "description": architecture_description,
            },
        },
        "models": [
            {
                "provider": "not-recorded",
                "name": "not-recorded",
                "snapshot": None,
                "role": "prover",
                "parameters": {},
            }
        ],
        "configuration": {
            "maximum_iterations": None,
            "token_budget": None,
            "concurrency": None,
            "tools": [],
            "extensions": {},
        },
        "environment": {
            "source_repository": None,
            "source_commit": None,
            "dependency_lockfiles": [],
        },
        "result": {
            "outcome": outcome,
            "verification": "unverified",
            "summary": summary,
            "verification_command": None,
            "verification_log": None,
        },
        "files": {
            "prompt": "prompt.txt",
            "output": "output.txt",
            "events": "events.jsonl",
            "artifacts": "artifacts",
        },
        "publication": {"redaction_reviewed": False},
        "extensions": {},
    }
    if submitted_by is not None:
        manifest["submitted_by"] = submitted_by
    return manifest


def populate_run(directory: Path, manifest: dict[str, Any], timestamp: str) -> None:
    write_text(directory / "run.yaml", dump_yaml(manifest))
    write_text(directory / "prompt.txt", "Replace this text with the exact prompt supplied to the agent.\n")
    write_text(directory / "output.txt", "Replace this text with the agent's final visible output.\n")
    event = {
        "seq": 1,
        "timestamp": timestamp,
        "type": "note",
        "content": "Draft run scaffold created; replace this with the visible run history.",
    }
    write_text(directory / "events.jsonl", json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    write_text(
        directory / "artifacts" / "README.txt",
        "Replace this file with proof code, counterexamples, logs, and other continuation artifacts.\n",
    )


def read_required_text(path: Path, field: str) -> str:
    if path.is_symlink():
        raise ValueError(f"{field} may not be a symlink: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"{field} is missing: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {field} as UTF-8 text from {path}: {exc}") from exc
    if not content.strip():
        raise ValueError(f"{field} must not be empty: {path}")
    return content


def import_summary(run_directory: Path) -> str:
    content = read_required_text(run_directory / "summary.txt", "summary.txt")
    return clean_label(" ".join(content.split()), "summary.txt")


def copy_regular_tree(source: Path, destination: Path) -> None:
    """Copy a directory without ever following symlinks or special files."""

    if source.is_symlink():
        raise ValueError(f"artifact directory may not be a symlink: {source}")
    if not source.is_dir():
        raise ValueError(f"artifacts must be a directory: {source}")
    destination.mkdir(parents=True)
    stack = [(source, destination)]
    while stack:
        source_directory, destination_directory = stack.pop()
        for entry in sorted(source_directory.iterdir(), key=lambda item: item.name):
            target = destination_directory / entry.name
            if entry.is_symlink():
                raise ValueError(f"artifact paths may not be symlinks: {entry}")
            if entry.is_dir():
                target.mkdir()
                stack.append((entry, target))
            elif entry.is_file():
                shutil.copy2(entry, target)
            else:
                raise ValueError(f"artifact path is not a regular file or directory: {entry}")
    if not any(path.is_file() for path in destination.rglob("*")):
        write_text(
            destination / "NO_ARTIFACTS.txt",
            "No separate reusable artifacts were supplied with this imported run.\n",
        )


def imported_run_source(run_directory: Path) -> tuple[str, str, str]:
    if run_directory.is_symlink():
        raise ValueError(f"run directory may not be a symlink: {run_directory}")
    if not run_directory.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {run_directory}")
    prompt = read_required_text(run_directory / "prompt.txt", "prompt.txt")
    output = read_required_text(run_directory / "output.txt", "output.txt")
    summary = import_summary(run_directory)
    return prompt, output, summary


def populate_imported_run(
    directory: Path,
    manifest: dict[str, Any],
    source_directory: Path,
    prompt: str,
    output: str,
    timestamp: str,
) -> None:
    write_text(directory / "run.yaml", dump_yaml(manifest))
    write_text(directory / "prompt.txt", prompt)
    write_text(directory / "output.txt", output)
    source_events = source_directory / "events.jsonl"
    if source_events.exists() or source_events.is_symlink():
        events = read_required_text(source_events, "events.jsonl")
        write_text(directory / "events.jsonl", events)
    else:
        event = {
            "seq": 1,
            "timestamp": timestamp,
            "type": "note",
            "content": "Run files imported without a separate event transcript.",
        }
        write_text(directory / "events.jsonl", json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    source_artifacts = source_directory / "artifacts"
    if source_artifacts.exists() or source_artifacts.is_symlink():
        copy_regular_tree(source_artifacts, directory / "artifacts")
    else:
        write_text(
            directory / "artifacts" / "NO_ARTIFACTS.txt",
            "No separate reusable artifacts were supplied with this imported run.\n",
        )


def validation_error(builder: ProjectBuilder, context: str) -> ValueError:
    rendered = "\n".join(diagnostic.render() for diagnostic in builder.diagnostics)
    return ValueError(f"{context}:\n{rendered}")


def load_builder_schemas(builder: ProjectBuilder) -> None:
    builder._status_schema = builder._load_schema("status-v1.schema.json")
    builder._run_schema = builder._load_schema("run-v1.schema.json")


def validate_imported_project(root: Path, directory: Path) -> None:
    builder = ProjectBuilder(root)
    load_builder_schemas(builder)
    project = builder.validate_project(directory)
    if project is None or builder.diagnostics:
        raise validation_error(builder, "the imported project would not validate")


def validate_imported_run(root: Path, project_root: Path, directory: Path) -> None:
    builder = ProjectBuilder(root)
    load_builder_schemas(builder)
    preflight_ok = builder.preflight_project(directory)
    run = builder.validate_run(project_root, directory, directory / "run.yaml") if preflight_ok else None
    if run is None or builder.diagnostics:
        raise validation_error(builder, "the imported run would not validate")


def new_project(args: argparse.Namespace) -> None:
    project_id = validate_id(args.project_id, "project_id")
    title = clean_label(args.title, "title")
    submitted_by = clean_label(args.submitted_by, "submitted_by") if args.submitted_by else None
    summary = clean_label(args.summary or "Draft proof project awaiting its first reviewed import.", "summary")
    projects_root = args.root / "content" / "projects"
    if not projects_root.is_dir():
        raise FileNotFoundError(f"projects directory does not exist: {projects_root}")
    destination = projects_root / project_id
    now = datetime.now(timezone.utc).replace(microsecond=0)
    timestamp = now.isoformat().replace("+00:00", "Z")

    def populate(directory: Path) -> None:
        front = {
            "title": title,
            "date": now.date().isoformat(),
            "summary": summary,
            "tags": [],
            "project_id": project_id,
            "featured": False,
            "draft": True,
            "build": {"publishResources": False},
        }
        write_text(
            directory / "index.md",
            "---\n" + dump_yaml(front) + "---\n\nReplace this paragraph with a short human introduction.\n\n{{< proof-project >}}\n",
        )
        write_text(directory / "problem.txt", "Replace this text with the exact mathematical problem statement.\n")
        status = {
            "schema_version": 1,
            "current_run": "run-001",
            "as_of_runs": ["run-001"],
            "outcome": "no_progress",
            "verification": "unverified",
            "summary": "No reviewed result has been imported yet.",
            "established": [],
            "candidate_results": [],
            "special_cases": [],
            "failed_attempts": [],
            "open_goals": ["Import and review the first agent run."],
            "recommended_next_steps": ["Replace every scaffold placeholder with the original run material."],
            "extensions": {},
        }
        write_text(directory / "status.yaml", dump_yaml(status))
        manifest = draft_run_manifest("run-001", [], submitted_by)
        populate_run(directory / "runs" / "run-001", manifest, timestamp)

    atomic_directory(destination, populate)
    print(f"Created draft project: {destination}")
    print("Contributor attribution will be derived automatically from the Git commit.")
    print("Next: replace the placeholders, review redactions, set the run and project draft flags to false, then validate.")


def import_project(args: argparse.Namespace) -> None:
    project_id = validate_id(args.project_id, "project_id")
    title = clean_label(args.title, "title")
    submitted_by = clean_label(args.submitted_by, "submitted_by") if args.submitted_by else None
    run_directory = args.run_dir
    prompt, output, summary = imported_run_source(run_directory)
    problem = read_required_text(args.problem_file, "problem file")
    projects_root = args.root / "content" / "projects"
    if not projects_root.is_dir():
        raise FileNotFoundError(f"projects directory does not exist: {projects_root}")
    destination = projects_root / project_id
    now = datetime.now(timezone.utc).replace(microsecond=0)
    timestamp = now.isoformat().replace("+00:00", "Z")

    def populate(directory: Path) -> None:
        front = {
            "title": title,
            "date": now.date().isoformat(),
            "summary": summary,
            "tags": [],
            "project_id": project_id,
            "featured": False,
            "draft": True,
            "build": {"publishResources": False},
        }
        write_text(
            directory / "index.md",
            "---\n"
            + dump_yaml(front)
            + "---\n\nThis project tracks work on the stated problem.\n\n{{< proof-project >}}\n",
        )
        write_text(directory / "problem.txt", problem)
        status = {
            "schema_version": 1,
            "current_run": "run-001",
            "as_of_runs": ["run-001"],
            "outcome": args.outcome,
            "verification": "unverified",
            "summary": summary,
            "established": [],
            "candidate_results": [],
            "special_cases": [],
            "failed_attempts": [],
            "open_goals": ["Review the imported result and choose the next proof goal."],
            "recommended_next_steps": ["Review this run before publishing or continuing it."],
            "extensions": {},
        }
        write_text(directory / "status.yaml", dump_yaml(status))
        manifest = draft_run_manifest(
            "run-001",
            [],
            submitted_by,
            summary=summary,
            outcome=args.outcome,
            architecture_type="unknown",
            architecture_description="The agent architecture was not recorded in the imported files.",
        )
        populate_imported_run(
            directory / "runs" / "run-001",
            manifest,
            run_directory,
            prompt,
            output,
            timestamp,
        )

    atomic_directory(
        destination,
        populate,
        lambda directory: validate_imported_project(args.root, directory),
    )
    print(f"Imported draft project: {destination}")
    print("Contributor attribution will be derived automatically from the Git commit.")
    print("Next: inspect every imported file, then run:")
    print(f"  python3 scripts/proofhub.py finalize-run {project_id} --redaction-reviewed")


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return value


def load_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError(f"{path} must begin with YAML front matter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---")
    except StopIteration as exc:
        raise ValueError(f"{path} has no closing front matter delimiter") from exc
    try:
        front = yaml.load("".join(lines[1:end]), Loader=UniqueKeyLoader)
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"cannot parse front matter in {path}: {exc}") from exc
    if not isinstance(front, dict):
        raise ValueError(f"front matter in {path} must be a YAML mapping")
    return front, "".join(lines[end + 1 :])


def render_front_matter(front: dict[str, Any], body: str) -> str:
    return "---\n" + dump_yaml(front) + "---\n" + body


def stage_text(path: Path, content: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix="_proofhub-update-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def atomic_text_updates(updates: dict[Path, str]) -> None:
    """Replace several text files, restoring originals if a replacement fails."""

    originals = {path: path.read_text(encoding="utf-8") for path in updates}
    staged: dict[Path, Path] = {}
    try:
        for path, content in updates.items():
            staged[path] = stage_text(path, content)
    except Exception:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        raise
    replaced: list[Path] = []
    try:
        for path in sorted(updates, key=lambda item: item.as_posix()):
            os.replace(staged[path], path)
            replaced.append(path)
    except Exception:
        for path in replaced:
            restoration = stage_text(path, originals[path])
            os.replace(restoration, path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def validate_finalized_project(root: Path, project_root: Path, updates: dict[Path, str]) -> None:
    temporary_parent = Path(tempfile.mkdtemp(prefix="_proofhub-finalize-", dir=project_root.parent))
    staged_project = temporary_parent / project_root.name
    try:
        shutil.copytree(project_root, staged_project)
        for path, content in updates.items():
            relative = path.relative_to(project_root)
            write_text(staged_project / relative, content)
        builder = ProjectBuilder(root)
        load_builder_schemas(builder)
        project = builder.validate_project(staged_project)
        if project is None or builder.diagnostics:
            raise validation_error(builder, "finalizing this run would make the project invalid")
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)


SCAFFOLD_MARKERS = (
    "replace this text with the exact mathematical problem statement.",
    "replace this text with the exact prompt supplied to the agent.",
    "replace this text with the agent's final visible output.",
    "draft run; replace this with a factual result summary.",
    "replace with the architecture used for this run.",
    "draft run scaffold created; replace this with the visible run history.",
    "replace this file with proof code, counterexamples, logs, and other continuation artifacts.",
)


def reject_scaffold_placeholders(project_root: Path, run_root: Path, manifest: dict[str, Any]) -> None:
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    checks: list[tuple[str, str]] = [
        ("problem statement", read_required_text(project_root / "problem.txt", "problem statement")),
    ]
    for field in ("prompt", "output", "events"):
        reference = files.get(field)
        if isinstance(reference, str):
            checks.append((field, read_required_text(run_root / reference, field)))
    result = manifest.get("result") if isinstance(manifest.get("result"), dict) else {}
    if isinstance(result.get("summary"), str):
        checks.append(("result summary", result["summary"]))
    agent = manifest.get("agent") if isinstance(manifest.get("agent"), dict) else {}
    architecture = agent.get("architecture") if isinstance(agent.get("architecture"), dict) else {}
    if isinstance(architecture.get("description"), str):
        checks.append(("architecture description", architecture["description"]))
    artifacts_reference = files.get("artifacts")
    if isinstance(artifacts_reference, str):
        scaffold_readme = run_root / artifacts_reference / "README.txt"
        if scaffold_readme.is_file() and not scaffold_readme.is_symlink():
            try:
                checks.append(("artifact", scaffold_readme.read_text(encoding="utf-8")))
            except (OSError, UnicodeError):
                pass
    for field, content in checks:
        normalized = " ".join(content.split()).casefold()
        if any(marker in normalized for marker in SCAFFOLD_MARKERS):
            raise ValueError(f"cannot finalize: {field} still contains scaffold placeholder text")


OUTCOME_STRENGTH = {
    "no_progress": 0,
    "partial": 1,
    "candidate_proof": 2,
    "candidate_refutation": 2,
    "proved": 3,
    "refuted": 3,
}


VERIFICATION_STRENGTH = {
    "unverified": 0,
    "failed": 0,
    "agent_reported": 1,
    "reproduced": 2,
    "machine_checked": 3,
    "supervisor_reviewed": 3,
}


def status_after_finalization(
    status: dict[str, Any],
    manifest: dict[str, Any],
    run_id: str,
    project_was_draft: bool,
) -> dict[str, Any]:
    updated = dict(status)
    result = manifest["result"]
    previous_outcome = status.get("outcome", "no_progress")
    previous_verification = status.get("verification", "unverified")
    new_outcome = result["outcome"]
    new_verification = result["verification"]
    keep_previous_classification = (
        not project_was_draft
        and (
            OUTCOME_STRENGTH.get(previous_outcome, 0) > OUTCOME_STRENGTH.get(new_outcome, 0)
            or (
                previous_outcome == new_outcome
                and VERIFICATION_STRENGTH.get(previous_verification, 0)
                > VERIFICATION_STRENGTH.get(new_verification, 0)
            )
        )
    )
    updated["current_run"] = run_id
    if project_was_draft:
        updated["as_of_runs"] = [run_id]
    else:
        previous_runs = status.get("as_of_runs") if isinstance(status.get("as_of_runs"), list) else []
        updated["as_of_runs"] = [*previous_runs, *([] if run_id in previous_runs else [run_id])]
    if not keep_previous_classification:
        updated["outcome"] = new_outcome
        updated["verification"] = new_verification
        if new_verification != "supervisor_reviewed":
            updated.pop("review", None)
    updated["summary"] = result["summary"]
    if project_was_draft:
        updated["open_goals"] = ["Choose and attempt the next proof step."]
        updated["recommended_next_steps"] = ["Continue from the current run and its reusable artifacts."]
    return updated


def automatic_run_id(runs_root: Path) -> str:
    used: set[str] = set()
    largest = 0
    for path in runs_root.iterdir():
        if not path.is_dir():
            continue
        used.add(path.name.casefold())
        match = re.fullmatch(r"run-(\d+)", path.name)
        if match:
            largest = max(largest, int(match.group(1)))
    candidate = largest + 1
    while f"run-{candidate:03d}".casefold() in used:
        candidate += 1
    return f"run-{candidate:03d}"


def new_run(args: argparse.Namespace) -> None:
    project_id = validate_id(args.project_id, "project_id")
    submitted_by = clean_label(args.submitted_by, "submitted_by") if args.submitted_by else None
    builder = ProjectBuilder(args.root)
    if not builder.validate():
        rendered = "\n".join(diagnostic.render() for diagnostic in builder.diagnostics)
        raise ValueError("the repository must validate before adding a run:\n" + rendered)
    project = builder.projects.get(project_id)
    if project is None:
        raise ValueError(f"published project {project_id!r} was not found")
    runs_root = project["root"] / "runs"
    run_id = validate_id(args.run_id or automatic_run_id(runs_root), "run_id")
    if args.no_parent:
        parents: list[dict[str, str]] = []
    else:
        parent_id = args.parent or project["status"]["current_run"]
        validate_id(parent_id, "parent run_id")
        if parent_id not in project["runs"]:
            raise ValueError(f"parent run {parent_id!r} is not a published run in {project_id!r}")
        parents = [{"run_id": parent_id, "relation": args.relation}]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    destination = runs_root / run_id
    manifest = draft_run_manifest(run_id, parents, submitted_by)
    atomic_directory(destination, lambda directory: populate_run(directory, manifest, now))
    print(f"Created draft run: {destination}")
    print("Contributor attribution will be derived automatically from the Git commit.")
    print("It is excluded from the website while draft: true. Fill the files, review redactions, then set draft: false and update status.yaml.")


def import_run(args: argparse.Namespace) -> None:
    project_id = validate_id(args.project_id, "project_id")
    submitted_by = clean_label(args.submitted_by, "submitted_by") if args.submitted_by else None
    run_directory = args.run_dir
    prompt, output, summary = imported_run_source(run_directory)
    builder = ProjectBuilder(args.root)
    if not builder.validate():
        rendered = "\n".join(diagnostic.render() for diagnostic in builder.diagnostics)
        raise ValueError("the repository must validate before importing a run:\n" + rendered)
    project = builder.projects.get(project_id)
    if project is None:
        raise ValueError(f"published project {project_id!r} was not found")
    runs_root = project["root"] / "runs"
    run_id = validate_id(args.run_id or automatic_run_id(runs_root), "run_id")
    if args.no_parent:
        parents: list[dict[str, str]] = []
    else:
        parent_id = args.parent or project["status"]["current_run"]
        validate_id(parent_id, "parent run_id")
        if parent_id not in project["runs"]:
            raise ValueError(f"parent run {parent_id!r} is not a published run in {project_id!r}")
        parents = [{"run_id": parent_id, "relation": args.relation}]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    destination = runs_root / run_id
    manifest = draft_run_manifest(
        run_id,
        parents,
        submitted_by,
        summary=summary,
        outcome=args.outcome,
        architecture_type="unknown",
        architecture_description="The agent architecture was not recorded in the imported files.",
    )

    def populate(directory: Path) -> None:
        populate_imported_run(directory, manifest, run_directory, prompt, output, now)

    atomic_directory(
        destination,
        populate,
        lambda directory: validate_imported_run(args.root, project["root"], directory),
    )
    print(f"Imported draft run: {destination}")
    print("Contributor attribution will be derived automatically from the Git commit.")
    print("The public status is unchanged. Inspect every imported file, then run:")
    print(f"  python3 scripts/proofhub.py finalize-run {project_id} --run-id {run_id} --redaction-reviewed")


def finalize_run(args: argparse.Namespace) -> None:
    project_id = validate_id(args.project_id, "project_id")
    if not args.redaction_reviewed:
        raise ValueError("finalizing requires --redaction-reviewed after checking all imported files for secrets")
    builder = ProjectBuilder(args.root)
    if not builder.validate():
        rendered = "\n".join(diagnostic.render() for diagnostic in builder.diagnostics)
        raise ValueError("the repository must validate before finalizing a run:\n" + rendered)
    project_root = args.root / "content" / "projects" / project_id
    if not project_root.is_dir():
        raise ValueError(f"project {project_id!r} was not found")
    front, body = load_front_matter(project_root / "index.md")
    project_was_draft = front.get("draft", False) is True
    introduction_is_placeholder = "replace this paragraph with a short human introduction." in " ".join(
        body.split()
    ).casefold()
    if project_was_draft and introduction_is_placeholder:
        raise ValueError("cannot finalize: the project introduction still contains scaffold placeholder text")
    status = load_yaml_mapping(project_root / "status.yaml")
    runs_root = project_root / "runs"
    draft_runs: dict[str, tuple[Path, dict[str, Any]]] = {}
    for run_root in sorted(path for path in runs_root.iterdir() if path.is_dir() and not path.name.startswith("_")):
        manifest_path = run_root / "run.yaml"
        if not manifest_path.is_file():
            continue
        manifest = load_yaml_mapping(manifest_path)
        if manifest.get("draft", False) is True:
            draft_runs[run_root.name] = (run_root, manifest)
    if args.run_id:
        run_id = validate_id(args.run_id, "run_id")
        selected = draft_runs.get(run_id)
        if selected is None:
            raise ValueError(f"draft run {run_id!r} was not found in {project_id!r}")
    elif len(draft_runs) == 1:
        run_id, selected = next(iter(draft_runs.items()))
    elif not draft_runs:
        raise ValueError(f"project {project_id!r} has no draft run to finalize")
    else:
        choices = ", ".join(sorted(draft_runs))
        raise ValueError(f"project has several draft runs ({choices}); choose one with --run-id")
    run_root, manifest = selected
    reject_scaffold_placeholders(project_root, run_root, manifest)

    finalized_manifest = dict(manifest)
    finalized_manifest["draft"] = False
    publication = dict(manifest.get("publication", {}))
    publication["redaction_reviewed"] = True
    finalized_manifest["publication"] = publication
    finalized_status = status_after_finalization(status, finalized_manifest, run_id, project_was_draft)
    finalized_front = dict(front)
    finalized_front["draft"] = False
    finalized_front["summary"] = finalized_status["summary"]
    updates = {
        run_root / "run.yaml": dump_yaml(finalized_manifest),
        project_root / "status.yaml": dump_yaml(finalized_status),
        project_root / "index.md": render_front_matter(finalized_front, body),
    }
    validate_finalized_project(args.root, project_root, updates)
    atomic_text_updates(updates)
    print(f"Finalized run: {project_id}/{run_id}")
    print("The run is ready to publish, the project status is current, and Git will record who commits it.")
    print("Next: commit and push the generated project files.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to this script's repository)",
    )
    commands = result.add_subparsers(dest="command", required=True)
    project = commands.add_parser("new-project", help="create a complete unpublished project scaffold")
    project.add_argument("project_id")
    project.add_argument("--title", required=True)
    project.add_argument(
        "--submitted-by",
        help="optional credit when the run material was supplied by someone other than the Git committer",
    )
    project.add_argument("--summary")
    project.set_defaults(handler=new_project)

    imported_project = commands.add_parser(
        "import-project",
        help="create a draft project from a problem file and three ordinary run files",
    )
    imported_project.add_argument("project_id")
    imported_project.add_argument("--title", required=True)
    imported_project.add_argument("--problem-file", required=True, type=Path)
    imported_project.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="directory containing prompt.txt, output.txt, summary.txt, and optional artifacts/ or events.jsonl",
    )
    imported_project.add_argument(
        "--outcome",
        choices=("no_progress", "partial", "candidate_proof", "candidate_refutation"),
        default="no_progress",
        help="conservative non-terminal result classification (default: no_progress)",
    )
    imported_project.add_argument(
        "--submitted-by",
        help="optional credit when the run material was supplied by someone other than the Git committer",
    )
    imported_project.set_defaults(handler=import_project)

    run = commands.add_parser("new-run", help="add a complete draft run scaffold to a published project")
    run.add_argument("project_id")
    run.add_argument("run_id", nargs="?")
    run.add_argument(
        "--submitted-by",
        help="optional credit when the run material was supplied by someone other than the Git committer",
    )
    parent_group = run.add_mutually_exclusive_group()
    parent_group.add_argument("--parent")
    parent_group.add_argument("--no-parent", action="store_true")
    run.add_argument(
        "--relation",
        choices=("continuation", "retry", "fork", "merge", "import"),
        default="continuation",
    )
    run.set_defaults(handler=new_run)

    imported_run = commands.add_parser(
        "import-run",
        help="add a draft run from prompt.txt, output.txt, and summary.txt",
    )
    imported_run.add_argument("project_id")
    imported_run.add_argument("--run-dir", required=True, type=Path)
    imported_run.add_argument("--run-id")
    imported_run.add_argument(
        "--outcome",
        choices=("no_progress", "partial", "candidate_proof", "candidate_refutation"),
        default="no_progress",
        help="conservative non-terminal result classification (default: no_progress)",
    )
    imported_run.add_argument(
        "--submitted-by",
        help="optional credit when the run material was supplied by someone other than the Git committer",
    )
    imported_parent_group = imported_run.add_mutually_exclusive_group()
    imported_parent_group.add_argument("--parent")
    imported_parent_group.add_argument("--no-parent", action="store_true")
    imported_run.add_argument(
        "--relation",
        choices=("continuation", "retry", "fork", "merge", "import"),
        default="continuation",
    )
    imported_run.set_defaults(handler=import_run)

    finalized_run = commands.add_parser(
        "finalize-run",
        help="make one reviewed draft run publishable and update its project status",
    )
    finalized_run.add_argument("project_id")
    finalized_run.add_argument("--run-id")
    finalized_run.add_argument(
        "--redaction-reviewed",
        action="store_true",
        help="confirm that prompt, output, events, and artifacts were checked for secrets",
    )
    finalized_run.set_defaults(handler=finalize_run)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.root = args.root.resolve()
    try:
        args.handler(args)
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
