#!/usr/bin/env python3
"""Validate proof-project bundles and generate deterministic continuation files.

The generator treats every uploaded file as untrusted data. It reads and packages
files but never executes verification commands, proof code, notebooks, or tools.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable
import zipfile

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EVENT_TYPES = {"message", "tool_call", "tool_result", "checkpoint", "error", "note"}
MESSAGE_ROLES = {"system", "developer", "user", "assistant"}
TOOL_RESULT_STATUSES = {"success", "error", "timeout", "cancelled"}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MAX_PROJECT_FILE_BYTES = 25 * 1024 * 1024
MAX_PROJECT_TOTAL_BYTES = 100 * 1024 * 1024


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys and keeps dates as strings."""


UniqueKeyLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for first_char, resolvers in UniqueKeyLoader.yaml_implicit_resolvers.items():
    UniqueKeyLoader.yaml_implicit_resolvers[first_char] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:timestamp"
    ]


def _construct_unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, order=True)
class Diagnostic:
    path: str
    location: str
    message: str

    def render(self) -> str:
        location = f": {self.location}" if self.location else ""
        return f"{self.path}{location}: {self.message}"


class ProjectBuilder:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.projects_root = self.root / "content" / "projects"
        self.schema_root = self.root / "schemas"
        self.generated_data_root = self.root / "data" / "generated" / "proof_projects"
        self.generated_static_root = self.root / "static" / "generated"
        self.diagnostics: list[Diagnostic] = []
        self.projects: dict[str, dict[str, Any]] = {}
        self._status_schema: dict[str, Any] = {}
        self._run_schema: dict[str, Any] = {}

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (ValueError, FileNotFoundError):
            return path.as_posix()

    def error(self, path: Path, message: str, location: str = "") -> None:
        self.diagnostics.append(Diagnostic(self._relative(path), location, message))

    def _load_schema(self, name: str) -> dict[str, Any]:
        path = self.schema_root / name
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise SchemaError("top-level schema must be a JSON object")
            Draft202012Validator.check_schema(schema)
            return schema
        except (OSError, json.JSONDecodeError, SchemaError) as exc:
            self.error(path, f"cannot load schema: {exc}")
            return {}

    def load_yaml(self, path: Path) -> dict[str, Any] | None:
        try:
            value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            self.error(path, f"cannot parse YAML: {exc}")
            return None
        if not isinstance(value, dict):
            self.error(path, "top-level YAML value must be a mapping")
            return None
        return value

    def validate_schema(self, path: Path, value: dict[str, Any], schema: dict[str, Any]) -> None:
        if not schema:
            return
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path)
            self.error(path, error.message, location)

    def read_front_matter(self, path: Path) -> tuple[dict[str, Any] | None, str]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self.error(path, f"cannot read page: {exc}")
            return None, ""
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            self.error(path, "page must begin with YAML front matter delimited by ---")
            return None, text
        try:
            end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
        except StopIteration:
            self.error(path, "front matter is missing its closing --- delimiter")
            return None, ""
        front_text = "\n".join(lines[1:end])
        try:
            front = yaml.load(front_text, Loader=UniqueKeyLoader)
        except yaml.YAMLError as exc:
            self.error(path, f"cannot parse front matter: {exc}")
            return None, "\n".join(lines[end + 1 :])
        if not isinstance(front, dict):
            self.error(path, "front matter must be a mapping")
            return None, "\n".join(lines[end + 1 :])
        return front, "\n".join(lines[end + 1 :])

    def safe_reference(
        self,
        project_root: Path,
        base: Path,
        reference: Any,
        source_path: Path,
        location: str,
        *,
        expect_directory: bool = False,
    ) -> Path | None:
        if not isinstance(reference, str) or not reference.strip():
            self.error(source_path, "file reference must be a non-empty string", location)
            return None
        if "\\" in reference:
            self.error(source_path, "file references must use forward slashes", location)
            return None
        pure = PurePosixPath(reference)
        if (
            pure.is_absolute()
            or pure.as_posix() != reference
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            self.error(source_path, "unsafe or non-normalized file reference", location)
            return None
        if "raw" in pure.parts:
            self.error(source_path, "raw files may be stored but may not be referenced by public metadata", location)
            return None
        candidate = base.joinpath(*pure.parts)
        try:
            project_resolved = project_root.resolve(strict=True)
            candidate_resolved = candidate.resolve(strict=False)
            relative_parts = candidate_resolved.relative_to(project_resolved).parts
        except (OSError, ValueError):
            self.error(source_path, "file reference escapes the project directory", location)
            return None
        cursor = project_resolved
        for part in relative_parts:
            cursor = cursor / part
            if cursor.is_symlink():
                self.error(source_path, "symlink references are not allowed", location)
                return None
        if not candidate_resolved.exists():
            self.error(source_path, f"referenced path does not exist: {reference}", location)
            return None
        if expect_directory and not candidate_resolved.is_dir():
            self.error(source_path, f"expected a directory: {reference}", location)
            return None
        if not expect_directory and not candidate_resolved.is_file():
            self.error(source_path, f"expected a file: {reference}", location)
            return None
        return candidate_resolved

    @staticmethod
    def parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    def validate_events(self, project_root: Path, run_root: Path, events_path: Path) -> None:
        seen_calls: set[str] = set()
        expected_seq = 1
        event_count = 0
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            self.error(events_path, f"cannot read event log: {exc}")
            return
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            event_count += 1
            location = f"line {line_number}"
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                self.error(events_path, f"invalid JSON: {exc.msg}", location)
                continue
            if not isinstance(event, dict):
                self.error(events_path, "event must be a JSON object", location)
                continue
            if event.get("seq") != expected_seq:
                self.error(events_path, f"expected seq {expected_seq}", location)
            expected_seq += 1
            if self.parse_datetime(event.get("timestamp")) is None:
                self.error(events_path, "timestamp must be an RFC 3339 value with a timezone", location)
            event_type = event.get("type")
            if event_type not in EVENT_TYPES:
                self.error(events_path, f"unsupported event type {event_type!r}", location)
                continue
            if event_type == "message":
                if event.get("role") not in MESSAGE_ROLES:
                    self.error(events_path, "message role is invalid", location)
                has_content = isinstance(event.get("content"), str) and bool(event.get("content"))
                has_file = isinstance(event.get("content_file"), str) and bool(event.get("content_file"))
                if has_content == has_file:
                    self.error(events_path, "message must contain exactly one of content or content_file", location)
                if has_file:
                    self.safe_reference(project_root, run_root, event["content_file"], events_path, location)
            elif event_type == "tool_call":
                call_id = event.get("call_id")
                if not isinstance(call_id, str) or not call_id:
                    self.error(events_path, "tool_call requires call_id", location)
                elif call_id in seen_calls:
                    self.error(events_path, f"duplicate call_id {call_id!r}", location)
                else:
                    seen_calls.add(call_id)
                if not isinstance(event.get("tool"), str) or not event.get("tool"):
                    self.error(events_path, "tool_call requires tool", location)
                if "input" not in event:
                    self.error(events_path, "tool_call requires input", location)
            elif event_type == "tool_result":
                if event.get("call_id") not in seen_calls:
                    self.error(events_path, "tool_result references an unknown earlier call_id", location)
                if event.get("status") not in TOOL_RESULT_STATUSES:
                    self.error(events_path, "tool_result status is invalid", location)
                if "content_file" in event:
                    self.safe_reference(project_root, run_root, event["content_file"], events_path, location)
            elif event_type == "checkpoint":
                if not isinstance(event.get("summary"), str) or not event.get("summary"):
                    self.error(events_path, "checkpoint requires a non-empty summary", location)
            else:
                if not isinstance(event.get("content"), str) or not event.get("content"):
                    self.error(events_path, f"{event_type} requires non-empty content", location)
        if event_count == 0:
            self.error(events_path, "event log must contain at least one event")

    def validate_run(
        self,
        project_root: Path,
        run_root: Path,
        manifest_path: Path,
    ) -> dict[str, Any] | None:
        manifest = self.load_yaml(manifest_path)
        if manifest is None:
            return None
        self.validate_schema(manifest_path, manifest, self._run_schema)
        run_id = manifest.get("run_id")
        if run_id != run_root.name:
            self.error(manifest_path, f"run_id must match directory name {run_root.name!r}", "run_id")
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        resolved_files: dict[str, Path] = {}
        for key in ("prompt", "output", "events"):
            if key in files:
                resolved = self.safe_reference(project_root, run_root, files[key], manifest_path, f"files.{key}")
                if resolved:
                    resolved_files[key] = resolved
                    if key in {"prompt", "output"}:
                        try:
                            if not resolved.read_text(encoding="utf-8").strip():
                                self.error(resolved, f"{key} file must not be empty")
                        except (OSError, UnicodeError) as exc:
                            self.error(resolved, f"cannot read {key} file: {exc}")
        artifacts_root = None
        if "artifacts" in files:
            artifacts_root = self.safe_reference(
                project_root,
                run_root,
                files["artifacts"],
                manifest_path,
                "files.artifacts",
                expect_directory=True,
            )
        if "events" in resolved_files:
            self.validate_events(project_root, run_root, resolved_files["events"])
        result = manifest.get("result") if isinstance(manifest.get("result"), dict) else {}
        verification_log = result.get("verification_log")
        if verification_log is not None:
            self.safe_reference(project_root, run_root, verification_log, manifest_path, "result.verification_log")
        environment = manifest.get("environment") if isinstance(manifest.get("environment"), dict) else {}
        for index, lockfile in enumerate(environment.get("dependency_lockfiles", [])):
            self.safe_reference(
                project_root,
                run_root,
                lockfile,
                manifest_path,
                f"environment.dependency_lockfiles.{index}",
            )
        execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
        started = self.parse_datetime(execution.get("started_at"))
        ended = self.parse_datetime(execution.get("ended_at"))
        wall_seconds = execution.get("wall_seconds")
        if started and ended:
            elapsed = (ended - started).total_seconds()
            if elapsed < 0:
                self.error(manifest_path, "ended_at must not be before started_at", "execution.ended_at")
            if isinstance(wall_seconds, int) and abs(elapsed - wall_seconds) > 1:
                self.error(manifest_path, "wall_seconds is inconsistent with the timestamps", "execution.wall_seconds")
        if execution.get("status") == "completed" and (started is None or ended is None):
            self.error(manifest_path, "completed runs require started_at and ended_at", "execution")
        manifest["_run_root"] = run_root
        manifest["_manifest_path"] = manifest_path
        manifest["_artifacts_root"] = artifacts_root
        return manifest

    def validate_project(self, project_root: Path) -> dict[str, Any] | None:
        index_path = project_root / "index.md"
        problem_path = project_root / "problem.txt"
        status_path = project_root / "status.yaml"
        for required in (index_path, problem_path, status_path):
            if not required.is_file():
                self.error(required, "required project file is missing")
        if self.diagnostics and any(item.path.startswith(self._relative(project_root)) for item in self.diagnostics):
            if not all(path.is_file() for path in (index_path, problem_path, status_path)):
                return None
        front, body = self.read_front_matter(index_path)
        if front is None:
            return None
        project_id = front.get("project_id")
        if project_id != project_root.name:
            self.error(index_path, f"project_id must match directory name {project_root.name!r}", "project_id")
        if not isinstance(project_id, str) or ID_PATTERN.fullmatch(project_id) is None:
            self.error(index_path, "project_id has an invalid format", "project_id")
        for field in ("title", "summary"):
            if not isinstance(front.get(field), str) or not front[field].strip():
                self.error(index_path, f"front matter requires non-empty {field}", field)
        build_options = front.get("build")
        if not isinstance(build_options, dict) or build_options.get("publishResources") is not False:
            self.error(
                index_path,
                "front matter must set build.publishResources to false so unlinked uploads are not published",
                "build.publishResources",
            )
        if "{{< proof-project >}}" not in body:
            self.error(index_path, "page body must contain {{< proof-project >}}")
        draft = front.get("draft", False)
        if not isinstance(draft, bool):
            self.error(index_path, "draft must be true or false", "draft")
        if draft is True:
            return None
        try:
            problem = problem_path.read_text(encoding="utf-8")
            if not problem.strip():
                self.error(problem_path, "problem statement must not be empty")
        except (OSError, UnicodeError) as exc:
            self.error(problem_path, f"cannot read problem statement: {exc}")
            problem = ""
        status = self.load_yaml(status_path)
        if status is None:
            return None
        self.validate_schema(status_path, status, self._status_schema)
        runs_root = project_root / "runs"
        if not runs_root.is_dir():
            self.error(runs_root, "project must contain a runs directory")
            return None
        runs: dict[str, dict[str, Any]] = {}
        run_dirs = sorted(path for path in runs_root.iterdir() if path.is_dir() and not path.name.startswith("_"))
        if not run_dirs:
            self.error(runs_root, "project must contain at least one run")
        seen_casefold: set[str] = set()
        for run_root in run_dirs:
            if run_root.is_symlink():
                self.error(run_root, "run directories may not be symlinks")
                continue
            folded = run_root.name.casefold()
            if folded in seen_casefold:
                self.error(run_root, "run IDs must also be unique when case-folded")
            seen_casefold.add(folded)
            manifest_path = run_root / "run.yaml"
            if not manifest_path.is_file():
                self.error(manifest_path, "required run manifest is missing")
                continue
            run = self.validate_run(project_root, run_root, manifest_path)
            if run is not None and isinstance(run.get("run_id"), str):
                runs[run["run_id"]] = run
        for field in ("current_run",):
            run_id = status.get(field)
            if isinstance(run_id, str) and run_id not in runs:
                self.error(status_path, f"unknown run ID {run_id!r}", field)
        for index, run_id in enumerate(status.get("as_of_runs", [])):
            if run_id not in runs:
                self.error(status_path, f"unknown run ID {run_id!r}", f"as_of_runs.{index}")
        graph: dict[str, list[str]] = {}
        for run_id, run in runs.items():
            parent_ids: list[str] = []
            for index, parent in enumerate(run.get("parents", [])):
                if not isinstance(parent, dict):
                    continue
                parent_id = parent.get("run_id")
                if parent_id == run_id:
                    self.error(run["_manifest_path"], "a run may not be its own parent", f"parents.{index}.run_id")
                elif parent_id not in runs:
                    self.error(run["_manifest_path"], f"unknown parent run {parent_id!r}", f"parents.{index}.run_id")
                elif isinstance(parent_id, str):
                    parent_ids.append(parent_id)
            graph[run_id] = parent_ids
        self.validate_acyclic_graph(graph, runs)
        for category in ("established", "candidate_results", "special_cases"):
            for item_index, item in enumerate(status.get(category, [])):
                if not isinstance(item, dict):
                    continue
                for evidence_index, evidence in enumerate(item.get("evidence", [])):
                    self.safe_reference(
                        project_root,
                        project_root,
                        evidence,
                        status_path,
                        f"{category}.{item_index}.evidence.{evidence_index}",
                    )
        project_bytes = 0
        for path in project_root.rglob("*"):
            if path.is_symlink():
                self.error(path, "symlinks are not allowed inside project bundles")
            elif path.is_file():
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    self.error(path, f"cannot inspect uploaded file: {exc}")
                    continue
                project_bytes += size
                if size > MAX_PROJECT_FILE_BYTES:
                    self.error(path, f"uploaded file exceeds the {MAX_PROJECT_FILE_BYTES // (1024 * 1024)} MiB limit")
        if project_bytes > MAX_PROJECT_TOTAL_BYTES:
            self.error(
                project_root,
                f"project bundle exceeds the {MAX_PROJECT_TOTAL_BYTES // (1024 * 1024)} MiB total limit",
            )
        return {
            "project_id": project_id,
            "front": front,
            "problem": problem,
            "status": status,
            "runs": runs,
            "root": project_root,
            "status_path": status_path,
            "problem_path": problem_path,
        }

    def validate_acyclic_graph(self, graph: dict[str, list[str]], runs: dict[str, dict[str, Any]]) -> None:
        state: dict[str, int] = {run_id: 0 for run_id in graph}

        def visit(run_id: str, trail: list[str]) -> None:
            if state[run_id] == 2:
                return
            if state[run_id] == 1:
                cycle = " -> ".join(trail + [run_id])
                self.error(runs[run_id]["_manifest_path"], f"run lineage contains a cycle: {cycle}", "parents")
                return
            state[run_id] = 1
            for parent_id in graph.get(run_id, []):
                if parent_id in graph:
                    visit(parent_id, trail + [run_id])
            state[run_id] = 2

        for run_id in sorted(graph):
            visit(run_id, [])

    def validate(self) -> bool:
        self.diagnostics.clear()
        self.projects.clear()
        self._status_schema = self._load_schema("status-v1.schema.json")
        self._run_schema = self._load_schema("run-v1.schema.json")
        if not self.projects_root.is_dir():
            self.error(self.projects_root, "projects directory is missing")
            return False
        project_dirs = sorted(
            path for path in self.projects_root.iterdir() if path.is_dir() and not path.name.startswith("_")
        )
        if not project_dirs:
            self.error(self.projects_root, "at least one project is required")
        seen_casefold: set[str] = set()
        for project_root in project_dirs:
            if project_root.is_symlink():
                self.error(project_root, "project directories may not be symlinks")
                continue
            folded = project_root.name.casefold()
            if folded in seen_casefold:
                self.error(project_root, "project IDs must also be unique when case-folded")
            seen_casefold.add(folded)
            project = self.validate_project(project_root)
            if project is not None and isinstance(project.get("project_id"), str):
                self.projects[project["project_id"]] = project
        self.diagnostics.sort()
        return not self.diagnostics

    @staticmethod
    def sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def clean_manifest(run: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in run.items() if not key.startswith("_")}

    def artifact_records(self, project: dict[str, Any], run: dict[str, Any]) -> list[dict[str, Any]]:
        artifacts_root = run.get("_artifacts_root")
        if not isinstance(artifacts_root, Path):
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(item for item in artifacts_root.rglob("*") if item.is_file() and not item.is_symlink()):
            if "raw" in path.relative_to(artifacts_root).parts:
                continue
            data = path.read_bytes()
            records.append(
                {
                    "name": path.name,
                    "path": path.relative_to(project["root"]).as_posix(),
                    "bytes": len(data),
                    "sha256": self.sha256(data),
                }
            )
        return records

    def project_record(self, project: dict[str, Any]) -> dict[str, Any]:
        runs: list[dict[str, Any]] = []
        for run_id, raw_run in project["runs"].items():
            run = self.clean_manifest(raw_run)
            run_root = raw_run["_run_root"]
            files = run["files"]
            resources = {
                "manifest": (run_root / "run.yaml").relative_to(project["root"]).as_posix(),
                "prompt": (run_root / files["prompt"]).relative_to(project["root"]).as_posix(),
                "output": (run_root / files["output"]).relative_to(project["root"]).as_posix(),
                "events": (run_root / files["events"]).relative_to(project["root"]).as_posix(),
                "artifacts": self.artifact_records(project, raw_run),
            }
            verification_log = run.get("result", {}).get("verification_log")
            if isinstance(verification_log, str):
                resources["verification_log"] = (run_root / verification_log).relative_to(project["root"]).as_posix()
            run["resources"] = resources
            runs.append(run)
        earliest = datetime.min.replace(tzinfo=timezone.utc)
        runs.sort(
            key=lambda item: (
                self.parse_datetime(item.get("execution", {}).get("started_at")) or earliest,
                item["run_id"],
            ),
            reverse=True,
        )
        project_id = project["project_id"]
        return {
            "schema_version": 1,
            "project_id": project_id,
            "title": project["front"]["title"],
            "summary": project["front"]["summary"],
            "problem": project["problem"],
            "status": project["status"],
            "runs": runs,
            "downloads": {
                "continuation": f"generated/{project_id}/continuation.txt",
                "handoff": f"generated/{project_id}/handoff.zip",
            },
        }

    @staticmethod
    def render_claims(title: str, claims: Iterable[dict[str, Any]]) -> list[str]:
        lines = [title]
        claims = list(claims)
        if not claims:
            return lines + ["- None recorded.", ""]
        for item in claims:
            lines.append(f"- {item['claim']}")
            for evidence in item["evidence"]:
                lines.append(f"  Evidence: {evidence}")
        lines.append("")
        return lines

    def continuation_text(self, project: dict[str, Any]) -> str:
        status = project["status"]
        base_run = project["runs"][status["current_run"]]
        run = self.clean_manifest(base_run)
        lines = [
            "PROOF RELAY CONTINUATION PACKAGE",
            "================================",
            "",
            f"Project: {project['front']['title']}",
            f"Project ID: {project['project_id']}",
            "Problem schema version: 1",
            f"Continue from run: {status['current_run']}",
            f"Current outcome: {status['outcome']}",
            f"Current verification: {status['verification']}",
            "",
            "EXACT PROBLEM",
            "-------------",
            project["problem"].rstrip(),
            "",
            "CURRENT SUMMARY",
            "---------------",
            status["summary"].strip(),
            "",
        ]
        lines.extend(self.render_claims("ESTABLISHED RESULTS", status["established"]))
        lines.extend(self.render_claims("CANDIDATE RESULTS", status["candidate_results"]))
        lines.extend(self.render_claims("SPECIAL CASES", status["special_cases"]))
        lines.append("SCOPED FAILED ATTEMPTS")
        if status["failed_attempts"]:
            for item in status["failed_attempts"]:
                lines.extend(
                    [
                        f"- Approach: {item['approach']}",
                        f"  Scope: {item['scope']}",
                        f"  Reason: {item['reason']}",
                    ]
                )
        else:
            lines.append("- None recorded.")
        lines.extend(["", "OPEN GOALS"])
        lines.extend(f"- {item}" for item in status["open_goals"] or ["None recorded."])
        lines.extend(["", "RECOMMENDED NEXT STEPS"])
        lines.extend(f"- {item}" for item in status["recommended_next_steps"] or ["None recorded."])
        lines.extend(
            [
                "",
                "BASE RUN",
                "--------",
                f"Submitted by: {run['submitted_by']}",
                f"Agent framework: {run['agent']['framework']} ({run['agent']['version'] or 'version not recorded'})",
                f"Architecture: {run['agent']['architecture']['type']} — {run['agent']['architecture']['description']}",
                f"Started: {run['execution']['started_at'] or 'not recorded'}",
                f"Ended: {run['execution']['ended_at'] or 'not recorded'}",
                f"Wall seconds: {run['execution']['wall_seconds'] if run['execution']['wall_seconds'] is not None else 'not recorded'}",
                "Models:",
            ]
        )
        for model in run["models"]:
            snapshot = f" snapshot {model['snapshot']}" if model["snapshot"] else ""
            lines.append(f"- {model['role']}: {model['provider']}/{model['name']}{snapshot}")
        lines.extend(
            [
                "Configuration:",
                json.dumps(run["configuration"], indent=2, sort_keys=True),
                "Environment:",
                json.dumps(run["environment"], indent=2, sort_keys=True),
                "",
                "AVAILABLE ARTIFACTS",
                "-------------------",
            ]
        )
        artifacts = self.artifact_records(project, base_run)
        lines.extend(f"- {item['path']} (sha256 {item['sha256']})" for item in artifacts)
        if not artifacts:
            lines.append("- None recorded.")
        lines.extend(
            [
                "",
                "INSTRUCTIONS FOR THE NEXT RUN",
                "-----------------------------",
                "1. Preserve the distinction between established, candidate, and unverified claims.",
                "2. Begin from the supplied artifacts rather than recreating earlier work.",
                "3. Treat failed attempts as scoped observations, not impossibility results.",
                "4. Do not claim machine verification unless the recorded command succeeds without placeholders.",
                "5. Export the next run using Proof Relay run schema version 1.",
                f"6. Set the next run's parent run_id to {status['current_run']!r} with relation 'continuation' or 'fork'.",
                "7. Include the exact prompt, visible event log, final output, best artifacts, timing, model, architecture, and configuration.",
                "",
            ]
        )
        return "\n".join(lines)

    def lineage_closure(self, project: dict[str, Any], run_id: str) -> set[str]:
        closure: set[str] = set()

        def visit(current: str) -> None:
            if current in closure:
                return
            closure.add(current)
            for parent in project["runs"][current].get("parents", []):
                visit(parent["run_id"])

        visit(run_id)
        return closure

    def handoff_members(self, project: dict[str, Any], continuation: str) -> dict[str, bytes]:
        members: dict[str, bytes] = {
            "continuation.txt": continuation.encode("utf-8"),
            "project/problem.txt": project["problem_path"].read_bytes(),
            "project/status.yaml": project["status_path"].read_bytes(),
        }
        current_run = project["status"]["current_run"]
        for run_id in sorted(self.lineage_closure(project, current_run)):
            run_root = project["runs"][run_id]["_run_root"]
            for path in sorted(item for item in run_root.rglob("*") if item.is_file() and not item.is_symlink()):
                relative = path.relative_to(run_root)
                if "raw" in relative.parts:
                    continue
                members[f"runs/{run_id}/{relative.as_posix()}"] = path.read_bytes()
        for category in ("established", "candidate_results", "special_cases"):
            for item in project["status"][category]:
                for evidence in item["evidence"]:
                    source = project["root"] / evidence
                    members.setdefault(f"evidence/{evidence}", source.read_bytes())
        manifest_files = [
            {
                "path": name,
                "bytes": len(data),
                "sha256": self.sha256(data),
            }
            for name, data in sorted(members.items())
        ]
        manifest = {
            "schema_version": 1,
            "project_id": project["project_id"],
            "current_run": current_run,
            "files": manifest_files,
        }
        members["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        return members

    @staticmethod
    def write_reproducible_zip(path: Path, members: dict[str, bytes]) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(members.items()):
                info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)

    def generate(self) -> None:
        if self.diagnostics:
            raise RuntimeError("cannot generate with validation errors")
        with tempfile.TemporaryDirectory(prefix=".proof-relay-build-", dir=self.root) as temporary:
            temporary_root = Path(temporary)
            data_root = temporary_root / "data"
            static_root = temporary_root / "static"
            data_root.mkdir(parents=True)
            static_root.mkdir(parents=True)
            for project_id, project in sorted(self.projects.items()):
                record = self.project_record(project)
                (data_root / f"{project_id}.json").write_text(
                    json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                project_static = static_root / project_id
                project_static.mkdir()
                continuation = self.continuation_text(project)
                (project_static / "continuation.txt").write_text(continuation, encoding="utf-8")
                self.write_reproducible_zip(
                    project_static / "handoff.zip",
                    self.handoff_members(project, continuation),
                )
            self.replace_generated_directory(data_root, self.generated_data_root)
            self.replace_generated_directory(static_root, self.generated_static_root)

    @staticmethod
    def replace_generated_directory(source: Path, destination: Path) -> None:
        if destination.is_symlink():
            raise RuntimeError(f"refusing to replace symlinked generated directory: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(source, destination)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate without writing generated files")
    mode.add_argument("--generate", action="store_true", help="validate and generate site data and downloads")
    parser.add_argument("--root", type=Path, help="repository root (defaults to the script's parent repository)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    builder = ProjectBuilder(root)
    if not builder.validate():
        for diagnostic in builder.diagnostics:
            print(diagnostic.render(), file=sys.stderr)
        print(f"Validation failed with {len(builder.diagnostics)} error(s).", file=sys.stderr)
        return 1
    if args.generate or not args.check:
        builder.generate()
        print(f"Validated and generated {len(builder.projects)} project(s).")
    else:
        print(f"Validated {len(builder.projects)} project(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
