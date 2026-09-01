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
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable
import unicodedata
import zipfile

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

try:
    from scripts.repository_attribution import RepositoryAttribution
except ModuleNotFoundError:  # Direct execution adds scripts/, rather than the repository, to sys.path.
    from repository_attribution import RepositoryAttribution


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EVENT_TYPES = {"message", "tool_call", "tool_result", "checkpoint", "error", "note"}
MESSAGE_ROLES = {"system", "developer", "user", "assistant"}
TOOL_RESULT_STATUSES = {"success", "error", "timeout", "cancelled"}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MAX_PROJECT_FILE_BYTES = 25 * 1024 * 1024
MAX_PROJECT_TOTAL_BYTES = 100 * 1024 * 1024
MAX_METADATA_FILE_BYTES = 1024 * 1024
MAX_EVENT_FILE_BYTES = 10 * 1024 * 1024
MAX_PROJECT_ENTRIES = 2_000
MAX_PROJECT_RUNS = 500
MAX_DIRECTORY_DEPTH = 16
MAX_JSON_NODES = 100_000
MAX_JSON_DEPTH = 64
MIN_JSON_INTEGER = -(2**63)
MAX_JSON_INTEGER = 2**63 - 1
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
UNSAFE_PATH_CHARACTERS = set('<>:"\\|?*[]')
ACTIVE_WEB_EXTENSIONS = {
    ".adoc",
    ".asciidoc",
    ".css",
    ".htm",
    ".html",
    ".js",
    ".markdown",
    ".md",
    ".mdown",
    ".mjs",
    ".mkdn",
    ".org",
    ".rst",
    ".svg",
    ".xhtml",
    ".xml",
}


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys and keeps dates as strings."""

    def compose_node(self, parent: yaml.Node | None, index: int | None) -> yaml.Node:
        if self.check_event(yaml.AliasEvent):
            event = self.get_event()
            raise yaml.constructor.ConstructorError(
                "while composing YAML",
                event.start_mark,
                "aliases are not allowed",
                event.start_mark,
            )
        return super().compose_node(parent, index)


UniqueKeyLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for first_char, resolvers in UniqueKeyLoader.yaml_implicit_resolvers.items():
    UniqueKeyLoader.yaml_implicit_resolvers[first_char] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:timestamp"
    ]


def _construct_unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "mapping keys must be scalar values",
                key_node.start_mark,
            ) from exc
        if duplicate:
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
        self.repository_attribution = RepositoryAttribution(self.root)

    @staticmethod
    def is_raw_path(parts: Iterable[str]) -> bool:
        return any(part.casefold() == "raw" for part in parts)

    @staticmethod
    def is_web_executable(path: PurePosixPath | Path) -> bool:
        return path.suffix.casefold() in ACTIVE_WEB_EXTENSIONS

    @staticmethod
    def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    @staticmethod
    def _reject_nonfinite_json_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value!r} is not allowed")

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (ValueError, FileNotFoundError):
            return path.as_posix()

    def error(self, path: Path, message: str, location: str = "") -> None:
        self.diagnostics.append(Diagnostic(self._relative(path), location, message))

    def validate_json_compatible(self, value: Any, source_path: Path) -> bool:
        """Reject YAML values that cannot be represented safely and portably as JSON."""

        nodes = 0
        active: set[int] = set()
        stack: list[tuple[Any, str, int, bool]] = [(value, "", 0, False)]
        valid = True
        while stack:
            current, location, depth, leaving = stack.pop()
            if leaving:
                active.discard(id(current))
                continue
            nodes += 1
            if nodes > MAX_JSON_NODES:
                self.error(source_path, f"structured data exceeds the {MAX_JSON_NODES:,}-value limit")
                return False
            if depth > MAX_JSON_DEPTH:
                self.error(source_path, f"structured data exceeds the maximum nesting depth of {MAX_JSON_DEPTH}", location)
                valid = False
                continue
            if current is None or isinstance(current, (str, bool)):
                continue
            if isinstance(current, int):
                if current < MIN_JSON_INTEGER or current > MAX_JSON_INTEGER:
                    self.error(source_path, "integer is outside the portable signed 64-bit range", location)
                    valid = False
                continue
            if isinstance(current, float):
                if not math.isfinite(current):
                    self.error(source_path, "non-finite numbers are not allowed", location)
                    valid = False
                continue
            if isinstance(current, (dict, list)):
                identity = id(current)
                if identity in active:
                    self.error(source_path, "recursive structured data is not allowed", location)
                    valid = False
                    continue
                active.add(identity)
                stack.append((current, location, depth, True))
                if isinstance(current, dict):
                    for key, item in reversed(list(current.items())):
                        if not isinstance(key, str):
                            self.error(source_path, "mapping keys must be strings", location)
                            valid = False
                            continue
                        child_location = f"{location}.{key}" if location else key
                        stack.append((item, child_location, depth + 1, False))
                else:
                    for index in range(len(current) - 1, -1, -1):
                        child_location = f"{location}.{index}" if location else str(index)
                        stack.append((current[index], child_location, depth + 1, False))
                continue
            self.error(source_path, f"unsupported YAML value type {type(current).__name__!r}", location)
            valid = False
        return valid

    def validate_path_component(self, path: Path, name: str) -> bool:
        valid = True
        if unicodedata.normalize("NFC", name) != name:
            self.error(path, "path names must use Unicode NFC normalization")
            valid = False
        if name != name.strip() or name.endswith((".", " ")):
            self.error(path, "path names may not have leading/trailing whitespace or a trailing dot")
            valid = False
        if any(ord(character) < 32 or ord(character) == 127 for character in name):
            self.error(path, "path names may not contain control characters")
            valid = False
        if any(character in UNSAFE_PATH_CHARACTERS for character in name):
            self.error(path, "path name contains a character that is unsafe in ZIPs, URLs, or Hugo resource patterns")
            valid = False
        basename = name.split(".", 1)[0].casefold()
        if basename in WINDOWS_RESERVED_NAMES:
            self.error(path, "path name is reserved on Windows")
            valid = False
        return valid

    def preflight_project(self, project_root: Path) -> bool:
        """Inventory a bundle before parsing any attacker-controlled metadata."""

        valid = True
        total_bytes = 0
        entry_count = 0
        seen_portable_paths: dict[str, Path] = {}
        stack: list[tuple[Path, int]] = [(project_root, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth >= MAX_DIRECTORY_DEPTH:
                self.error(directory, f"project directory depth exceeds the limit of {MAX_DIRECTORY_DEPTH}")
                valid = False
                continue
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda item: item.name)
            except OSError as exc:
                self.error(directory, f"cannot inspect project directory: {exc}")
                valid = False
                continue
            for entry in entries:
                path = Path(entry.path)
                entry_count += 1
                if entry_count > MAX_PROJECT_ENTRIES:
                    self.error(project_root, f"project contains more than {MAX_PROJECT_ENTRIES:,} files and directories")
                    return False
                if not self.validate_path_component(path, entry.name):
                    valid = False
                relative = path.relative_to(project_root).as_posix()
                folded = relative.casefold()
                previous = seen_portable_paths.get(folded)
                if previous is not None:
                    self.error(path, f"path collides case-insensitively with {previous.relative_to(project_root).as_posix()!r}")
                    valid = False
                else:
                    seen_portable_paths[folded] = path
                try:
                    if entry.is_symlink():
                        self.error(path, "symlinks are not allowed inside project bundles")
                        valid = False
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append((path, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        total_bytes += size
                        if size > MAX_PROJECT_FILE_BYTES:
                            self.error(path, f"uploaded file exceeds the {MAX_PROJECT_FILE_BYTES // (1024 * 1024)} MiB limit")
                            valid = False
                        metadata_limit = None
                        if path.name in {"index.md", "problem.txt", "run.yaml", "status.yaml"}:
                            metadata_limit = MAX_METADATA_FILE_BYTES
                        elif path.suffix.casefold() == ".jsonl":
                            metadata_limit = MAX_EVENT_FILE_BYTES
                        if metadata_limit is not None and size > metadata_limit:
                            self.error(path, f"metadata/log file exceeds its {metadata_limit // (1024 * 1024)} MiB parsing limit")
                            valid = False
                    else:
                        self.error(path, "unsupported filesystem entry type")
                        valid = False
                except OSError as exc:
                    self.error(path, f"cannot inspect uploaded path: {exc}")
                    valid = False
        if total_bytes > MAX_PROJECT_TOTAL_BYTES:
            self.error(project_root, f"project bundle exceeds the {MAX_PROJECT_TOTAL_BYTES // (1024 * 1024)} MiB total limit")
            valid = False
        return valid

    def _load_schema(self, name: str) -> dict[str, Any]:
        path = self.schema_root / name
        try:
            schema = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=self._reject_duplicate_json_keys,
                parse_constant=self._reject_nonfinite_json_constant,
            )
            if not isinstance(schema, dict):
                raise SchemaError("top-level schema must be a JSON object")
            Draft202012Validator.check_schema(schema)
            return schema
        except (OSError, json.JSONDecodeError, ValueError, SchemaError) as exc:
            self.error(path, f"cannot load schema: {exc}")
            return {}

    def load_yaml(self, path: Path) -> dict[str, Any] | None:
        try:
            value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
            self.error(path, f"cannot parse YAML: {exc}")
            return None
        if not isinstance(value, dict):
            self.error(path, "top-level YAML value must be a mapping")
            return None
        if not self.validate_json_compatible(value, path):
            return None
        return value

    def validate_schema(self, path: Path, value: dict[str, Any], schema: dict[str, Any]) -> None:
        if not schema:
            return
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(value),
            key=lambda item: tuple(f"{type(part).__name__}:{part}" for part in item.absolute_path),
        )
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
        if not lines or lines[0] != "---":
            self.error(path, "page must begin with YAML front matter delimited by ---")
            return None, text
        try:
            end = next(index for index, line in enumerate(lines[1:], start=1) if line == "---")
        except StopIteration:
            self.error(path, "front matter is missing its closing --- delimiter")
            return None, ""
        front_text = "\n".join(lines[1:end])
        try:
            front = yaml.load(front_text, Loader=UniqueKeyLoader)
        except (yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
            self.error(path, f"cannot parse front matter: {exc}")
            return None, "\n".join(lines[end + 1 :])
        if not isinstance(front, dict):
            self.error(path, "front matter must be a mapping")
            return None, "\n".join(lines[end + 1 :])
        if not self.validate_json_compatible(front, path):
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
        if reference != reference.strip():
            self.error(source_path, "file references may not have leading or trailing whitespace", location)
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
        if self.is_raw_path(pure.parts):
            self.error(source_path, "raw files may be stored but may not be referenced by public metadata", location)
            return None
        if not expect_directory and self.is_web_executable(pure):
            self.error(
                source_path,
                "web-executable files may not be referenced by public metadata; keep them as handoff-only artifacts",
                location,
            )
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

    def validate_events(
        self,
        project_root: Path,
        run_root: Path,
        events_path: Path,
        execution: dict[str, Any],
    ) -> None:
        seen_calls: set[str] = set()
        resolved_calls: set[str] = set()
        expected_seq = 1
        event_count = 0
        previous_timestamp: datetime | None = None
        started = self.parse_datetime(execution.get("started_at"))
        ended = self.parse_datetime(execution.get("ended_at"))
        try:
            stream = events_path.open("r", encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self.error(events_path, f"cannot read event log: {exc}")
            return
        try:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                event_count += 1
                location = f"line {line_number}"
                try:
                    event = json.loads(
                        line,
                        object_pairs_hook=self._reject_duplicate_json_keys,
                        parse_constant=self._reject_nonfinite_json_constant,
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    message = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
                    self.error(events_path, f"invalid JSON: {message}", location)
                    continue
                if not isinstance(event, dict):
                    self.error(events_path, "event must be a JSON object", location)
                    continue
                if not self.validate_json_compatible(event, events_path):
                    continue
                sequence = event.get("seq")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence != expected_seq:
                    self.error(events_path, f"expected seq {expected_seq} as an integer", location)
                expected_seq += 1
                timestamp = self.parse_datetime(event.get("timestamp"))
                if timestamp is None:
                    self.error(events_path, "timestamp must be an RFC 3339 value with a timezone", location)
                else:
                    if previous_timestamp is not None and timestamp < previous_timestamp:
                        self.error(events_path, "event timestamps must be nondecreasing", location)
                    if execution.get("status") == "completed":
                        if started is not None and timestamp < started:
                            self.error(events_path, "event occurs before execution.started_at", location)
                        if ended is not None and timestamp > ended:
                            self.error(events_path, "event occurs after execution.ended_at", location)
                    previous_timestamp = timestamp
                event_type = event.get("type")
                if event_type not in EVENT_TYPES:
                    self.error(events_path, f"unsupported event type {event_type!r}", location)
                    continue
                if event_type == "message":
                    if event.get("role") not in MESSAGE_ROLES:
                        self.error(events_path, "message role is invalid", location)
                    has_content = isinstance(event.get("content"), str) and bool(event["content"].strip())
                    has_file = isinstance(event.get("content_file"), str) and bool(event["content_file"].strip())
                    if has_content == has_file:
                        self.error(events_path, "message must contain exactly one of content or content_file", location)
                    if has_file:
                        self.safe_reference(project_root, run_root, event["content_file"], events_path, location)
                elif event_type == "tool_call":
                    call_id = event.get("call_id")
                    if not isinstance(call_id, str) or not call_id.strip():
                        self.error(events_path, "tool_call requires call_id", location)
                    elif call_id in seen_calls:
                        self.error(events_path, f"duplicate call_id {call_id!r}", location)
                    else:
                        seen_calls.add(call_id)
                    if not isinstance(event.get("tool"), str) or not event["tool"].strip():
                        self.error(events_path, "tool_call requires tool", location)
                    if "input" not in event:
                        self.error(events_path, "tool_call requires input", location)
                elif event_type == "tool_result":
                    call_id = event.get("call_id")
                    if call_id not in seen_calls:
                        self.error(events_path, "tool_result references an unknown earlier call_id", location)
                    elif call_id in resolved_calls:
                        self.error(events_path, f"duplicate tool_result for call_id {call_id!r}", location)
                    else:
                        resolved_calls.add(call_id)
                    if event.get("status") not in TOOL_RESULT_STATUSES:
                        self.error(events_path, "tool_result status is invalid", location)
                    if "content_file" in event:
                        self.safe_reference(project_root, run_root, event["content_file"], events_path, location)
                elif event_type == "checkpoint":
                    if not isinstance(event.get("summary"), str) or not event["summary"].strip():
                        self.error(events_path, "checkpoint requires a non-empty summary", location)
                else:
                    if not isinstance(event.get("content"), str) or not event["content"].strip():
                        self.error(events_path, f"{event_type} requires non-empty content", location)
        except (OSError, UnicodeError) as exc:
            self.error(events_path, f"cannot read event log: {exc}")
        finally:
            stream.close()
        if event_count == 0:
            self.error(events_path, "event log must contain at least one event")
        if execution.get("status") == "completed":
            for call_id in sorted(seen_calls - resolved_calls):
                self.error(events_path, f"completed run has no tool_result for call_id {call_id!r}")

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
        result = manifest.get("result") if isinstance(manifest.get("result"), dict) else {}
        verification_log = result.get("verification_log")
        verification_log_path = None
        if verification_log is not None:
            verification_log_path = self.safe_reference(
                project_root,
                run_root,
                verification_log,
                manifest_path,
                "result.verification_log",
            )
        environment = manifest.get("environment") if isinstance(manifest.get("environment"), dict) else {}
        dependency_lockfiles = environment.get("dependency_lockfiles", [])
        if not isinstance(dependency_lockfiles, list):
            dependency_lockfiles = []
        for index, lockfile in enumerate(dependency_lockfiles):
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
        if "events" in resolved_files:
            self.validate_events(project_root, run_root, resolved_files["events"], execution)
        verification = result.get("verification")
        outcome = result.get("outcome")
        if verification == "machine_checked":
            command = result.get("verification_command")
            if not isinstance(command, str) or not command.strip():
                self.error(manifest_path, "machine-checked results require a nonblank verification command", "result.verification_command")
        if outcome in {"proved", "refuted"} and execution.get("status") not in {"completed", "imported"}:
            self.error(manifest_path, "terminal results require a completed or imported execution", "execution.status")
        if verification in {"machine_checked", "reproduced"} or outcome in {"proved", "refuted"}:
            if verification_log_path is None:
                self.error(manifest_path, "this result requires a verification log", "result.verification_log")
            else:
                try:
                    if not verification_log_path.read_text(encoding="utf-8").strip():
                        self.error(verification_log_path, "verification log must not be empty")
                except (OSError, UnicodeError) as exc:
                    self.error(verification_log_path, f"cannot read verification log: {exc}")
        manifest["_run_root"] = run_root
        manifest["_manifest_path"] = manifest_path
        manifest["_artifacts_root"] = artifacts_root
        return manifest

    def validate_project(self, project_root: Path) -> dict[str, Any] | None:
        index_path = project_root / "index.md"
        problem_path = project_root / "problem.txt"
        status_path = project_root / "status.yaml"
        if not self.preflight_project(project_root):
            return None
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
        if "date" in front:
            try:
                if not isinstance(front["date"], str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", front["date"]) is None:
                    raise ValueError
                date.fromisoformat(front["date"])
            except ValueError:
                self.error(index_path, "date must use YYYY-MM-DD format", "date")
        for forbidden in ("aliases", "layout", "outputs", "slug", "type", "url"):
            if forbidden in front:
                self.error(index_path, f"front matter field {forbidden!r} is not allowed on proof projects", forbidden)
        build_options = front.get("build")
        if not isinstance(build_options, dict) or build_options.get("publishResources") is not False:
            self.error(
                index_path,
                "front matter must set build.publishResources to false so unlinked uploads are not published",
                "build.publishResources",
            )
        elif set(build_options) != {"publishResources"}:
            self.error(index_path, "build may contain only publishResources: false", "build")
        shortcode_count = 0
        fence: str | None = None
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                marker = stripped[:3]
                fence = None if fence == marker else marker if fence is None else fence
                continue
            if fence is None and stripped == "{{< proof-project >}}":
                shortcode_count += 1
        if shortcode_count != 1:
            self.error(index_path, "page body must contain exactly one active {{< proof-project >}} shortcode")
        draft = front.get("draft", False)
        if not isinstance(draft, bool):
            self.error(index_path, "draft must be true or false", "draft")
        project_is_draft = draft is True
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
        all_runs: dict[str, dict[str, Any]] = {}
        runs: dict[str, dict[str, Any]] = {}
        run_dirs = sorted(path for path in runs_root.iterdir() if path.is_dir() and not path.name.startswith("_"))
        if not run_dirs:
            self.error(runs_root, "project must contain at least one run")
        if len(run_dirs) > MAX_PROJECT_RUNS:
            self.error(runs_root, f"project contains more than {MAX_PROJECT_RUNS} runs")
            return None
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
                all_runs[run["run_id"]] = run
                if run.get("draft", False) is not True:
                    runs[run["run_id"]] = run
        if not runs and not project_is_draft:
            self.error(runs_root, "a published project must contain at least one non-draft run")
        status_runs = all_runs if project_is_draft else runs
        for field in ("current_run",):
            run_id = status.get(field)
            if isinstance(run_id, str) and run_id not in status_runs:
                if run_id in all_runs and not project_is_draft:
                    self.error(status_path, f"run ID {run_id!r} is still a draft", field)
                else:
                    self.error(status_path, f"unknown run ID {run_id!r}", field)
        as_of_runs = status.get("as_of_runs", [])
        if not isinstance(as_of_runs, list):
            as_of_runs = []
        for index, run_id in enumerate(as_of_runs):
            if run_id not in status_runs:
                if run_id in all_runs and not project_is_draft:
                    self.error(status_path, f"run ID {run_id!r} is still a draft", f"as_of_runs.{index}")
                else:
                    self.error(status_path, f"unknown run ID {run_id!r}", f"as_of_runs.{index}")
        if isinstance(status.get("current_run"), str) and status["current_run"] not in as_of_runs:
            self.error(status_path, "current_run must also appear in as_of_runs", "current_run")
        graph: dict[str, list[str]] = {}
        for run_id, run in all_runs.items():
            parent_ids: list[str] = []
            seen_parent_ids: set[str] = set()
            parents = run.get("parents", [])
            if not isinstance(parents, list):
                parents = []
            for index, parent in enumerate(parents):
                if not isinstance(parent, dict):
                    continue
                parent_id = parent.get("run_id")
                if parent_id == run_id:
                    self.error(run["_manifest_path"], "a run may not be its own parent", f"parents.{index}.run_id")
                elif parent_id not in all_runs:
                    self.error(run["_manifest_path"], f"unknown parent run {parent_id!r}", f"parents.{index}.run_id")
                elif isinstance(parent_id, str):
                    parent_ids.append(parent_id)
                    if parent_id in seen_parent_ids:
                        self.error(run["_manifest_path"], f"duplicate parent run {parent_id!r}", f"parents.{index}.run_id")
                    seen_parent_ids.add(parent_id)
                    if run.get("draft", False) is not True and all_runs[parent_id].get("draft", False) is True:
                        self.error(run["_manifest_path"], "a published run may not depend on a draft parent", f"parents.{index}.run_id")
                    relation = parent.get("relation")
                    if relation in {"continuation", "retry", "merge"}:
                        parent_execution = all_runs[parent_id].get("execution")
                        child_execution = run.get("execution")
                        parent_execution = parent_execution if isinstance(parent_execution, dict) else {}
                        child_execution = child_execution if isinstance(child_execution, dict) else {}
                        parent_end = self.parse_datetime(parent_execution.get("ended_at"))
                        child_start = self.parse_datetime(child_execution.get("started_at"))
                        if parent_end is not None and child_start is not None and parent_end > child_start:
                            self.error(
                                run["_manifest_path"],
                                "continuation/retry/merge parent must end before the child starts",
                                f"parents.{index}.run_id",
                            )
            graph[run_id] = parent_ids
        self.validate_acyclic_graph(graph, all_runs)
        for category in ("established", "candidate_results", "special_cases"):
            category_items = status.get(category, [])
            if not isinstance(category_items, list):
                category_items = []
            for item_index, item in enumerate(category_items):
                if not isinstance(item, dict):
                    continue
                evidence_items = item.get("evidence", [])
                if not isinstance(evidence_items, list):
                    evidence_items = []
                for evidence_index, evidence in enumerate(evidence_items):
                    if isinstance(evidence, str):
                        parts = PurePosixPath(evidence).parts
                        if len(parts) >= 2 and parts[0] == "runs":
                            evidence_run = all_runs.get(parts[1])
                            if (
                                not project_is_draft
                                and evidence_run is not None
                                and evidence_run.get("draft", False) is True
                            ):
                                self.error(
                                    status_path,
                                    "published status may not cite evidence from a draft run",
                                    f"{category}.{item_index}.evidence.{evidence_index}",
                                )
                    self.safe_reference(
                        project_root,
                        project_root,
                        evidence,
                        status_path,
                        f"{category}.{item_index}.evidence.{evidence_index}",
                    )
        review = status.get("review")
        if status.get("verification") == "supervisor_reviewed" and not isinstance(review, dict):
            self.error(status_path, "supervisor-reviewed status requires reviewer attribution", "review")
        if isinstance(review, dict):
            reviewed_at = self.parse_datetime(review.get("reviewed_at"))
            if reviewed_at is not None:
                referenced_times: list[datetime | None] = []
                for run_id in as_of_runs:
                    if run_id not in status_runs:
                        continue
                    run_execution = status_runs[run_id].get("execution")
                    run_execution = run_execution if isinstance(run_execution, dict) else {}
                    referenced_times.append(
                        self.parse_datetime(run_execution.get("ended_at"))
                        or self.parse_datetime(run_execution.get("started_at"))
                    )
                known_times = [timestamp for timestamp in referenced_times if timestamp is not None]
                if known_times and reviewed_at < max(known_times):
                    self.error(status_path, "reviewed_at must not precede the runs summarized by this status", "review.reviewed_at")
        terminal_outcome = status.get("outcome")
        if terminal_outcome in {"proved", "refuted"}:
            allowed_verifications = (
                {"machine_checked"}
                if status.get("verification") == "machine_checked"
                else {"machine_checked", "supervisor_reviewed"}
            )
            supporting_runs: list[dict[str, Any]] = []
            for run_id in as_of_runs:
                if run_id not in status_runs:
                    continue
                candidate = status_runs[run_id]
                candidate_result = candidate.get("result")
                candidate_execution = candidate.get("execution")
                candidate_result = candidate_result if isinstance(candidate_result, dict) else {}
                candidate_execution = candidate_execution if isinstance(candidate_execution, dict) else {}
                if (
                    candidate_result.get("outcome") == terminal_outcome
                    and candidate_result.get("verification") in allowed_verifications
                    and candidate_execution.get("status") in {"completed", "imported"}
                ):
                    supporting_runs.append(candidate)
            if not supporting_runs:
                self.error(
                    status_path,
                    "terminal status requires an as_of_run with the same terminal outcome and strong verification",
                    "as_of_runs",
                )
        return {
            "project_id": project_id,
            "draft": project_is_draft,
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
        for start in sorted(graph):
            if state[start] != 0:
                continue
            state[start] = 1
            trail = [start]
            positions = {start: 0}
            stack: list[tuple[str, Any]] = [(start, iter(graph.get(start, [])))]
            while stack:
                run_id, parents = stack[-1]
                try:
                    parent_id = next(parents)
                except StopIteration:
                    state[run_id] = 2
                    stack.pop()
                    positions.pop(run_id, None)
                    trail.pop()
                    continue
                if parent_id not in graph:
                    continue
                if state[parent_id] == 0:
                    state[parent_id] = 1
                    positions[parent_id] = len(trail)
                    trail.append(parent_id)
                    stack.append((parent_id, iter(graph.get(parent_id, []))))
                elif state[parent_id] == 1:
                    cycle_start = positions[parent_id]
                    cycle = " -> ".join(trail[cycle_start:] + [parent_id])
                    self.error(runs[parent_id]["_manifest_path"], f"run lineage contains a cycle: {cycle}", "parents")
                    return

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
            self.validate_path_component(project_root, project_root.name)
            if project_root.is_symlink():
                self.error(project_root, "project directories may not be symlinks")
                continue
            folded = project_root.name.casefold()
            if folded in seen_casefold:
                self.error(project_root, "project IDs must also be unique when case-folded")
            seen_casefold.add(folded)
            project = self.validate_project(project_root)
            if (
                project is not None
                and project.get("draft", False) is not True
                and isinstance(project.get("project_id"), str)
            ):
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
            if self.is_raw_path(path.relative_to(artifacts_root).parts):
                continue
            data = path.read_bytes()
            records.append(
                {
                    "name": path.name,
                    "path": path.relative_to(project["root"]).as_posix(),
                    "bytes": len(data),
                    "sha256": self.sha256(data),
                    "public": not self.is_web_executable(path),
                }
            )
        return records

    def file_record(self, project: dict[str, Any], path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        return {
            "name": path.name,
            "path": path.relative_to(project["root"]).as_posix(),
            "bytes": len(data),
            "sha256": self.sha256(data),
            "public": not self.is_web_executable(path),
        }

    def project_record(self, project: dict[str, Any]) -> dict[str, Any]:
        runs: list[dict[str, Any]] = []
        for run_id, raw_run in project["runs"].items():
            run = self.clean_manifest(raw_run)
            run_root = raw_run["_run_root"]
            repository_attribution = self.repository_attribution.for_path(
                run_root / "run.yaml", introduced=True
            )
            if repository_attribution is not None:
                run["repository_attribution"] = repository_attribution
            files = run["files"]
            resources = {
                "manifest": (run_root / "run.yaml").relative_to(project["root"]).as_posix(),
                "prompt": (run_root / files["prompt"]).relative_to(project["root"]).as_posix(),
                "output": (run_root / files["output"]).relative_to(project["root"]).as_posix(),
                "events": (run_root / files["events"]).relative_to(project["root"]).as_posix(),
                "artifacts": self.artifact_records(project, raw_run),
                "dependency_lockfiles": [
                    self.file_record(project, run_root / lockfile)
                    for lockfile in run.get("environment", {}).get("dependency_lockfiles", [])
                ],
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
        status = copy.deepcopy(project["status"])
        status_attribution = self.repository_attribution.for_path(
            project["status_path"], introduced=False
        )
        if status_attribution is not None:
            status["repository_attribution"] = status_attribution
        review = status.get("review", {})
        updated_at = review.get("reviewed_at") if isinstance(review, dict) else None
        if not updated_at:
            timestamps = [
                item.get("execution", {}).get("ended_at") or item.get("execution", {}).get("started_at")
                for item in runs
            ]
            updated_at = max((item for item in timestamps if item), default=None)
        return {
            "schema_version": 1,
            "project_id": project_id,
            "title": project["front"]["title"],
            "summary": project["front"]["summary"],
            "problem": project["problem"],
            "status": status,
            "runs": runs,
            "updated_at": updated_at,
            "counts": {
                "runs": len(runs),
                "open_goals": len(project["status"].get("open_goals", [])),
                "established": len(project["status"].get("established", [])),
                "candidate_results": len(project["status"].get("candidate_results", [])),
            },
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
        review = status.get("review")
        if isinstance(review, dict):
            reviewer_line = (
                f"Reviewed by: {review['reviewed_by']}"
                if review.get("reviewed_by")
                else "Reviewed by: the Git author of the status commit (see the public repository audit trail)"
            )
            lines.extend(
                [
                    "STATUS REVIEW",
                    "-------------",
                    reviewer_line,
                    f"Reviewed at: {review['reviewed_at']}",
                    f"Review note: {review.get('note') or 'None recorded.'}",
                    "",
                ]
            )
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
        lines.extend(["", "BASE RUN", "--------"])
        if run.get("submitted_by"):
            lines.append(f"Run supplied by: {run['submitted_by']}")
        lines.extend(
            [
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
                "5. Return the exact prompt, final output, a short factual summary, and only useful new or changed proof files.",
                "6. Include model, timing, architecture, and other run metadata when known; do not guess.",
                "7. Do not upload this handoff ZIP again; the repository already contains its earlier material.",
                f"8. Record {status['current_run']!r} as the parent with relation 'continuation' or 'fork'.",
                "",
            ]
        )
        return "\n".join(lines)

    def lineage_closure(self, project: dict[str, Any], run_id: str) -> set[str]:
        closure: set[str] = set()
        stack = [run_id]
        while stack:
            current = stack.pop()
            if current in closure:
                continue
            closure.add(current)
            for parent in project["runs"][current].get("parents", []):
                stack.append(parent["run_id"])
        return closure

    def handoff_members(self, project: dict[str, Any], continuation: str) -> dict[str, bytes]:
        members: dict[str, bytes] = {
            "continuation.txt": continuation.encode("utf-8"),
            "project/problem.txt": project["problem_path"].read_bytes(),
            "project/status.yaml": project["status_path"].read_bytes(),
        }

        def add_run_file(run_id: str, run_root: Path, source: Path) -> None:
            relative = source.relative_to(run_root)
            if self.is_raw_path(relative.parts):
                return
            members.setdefault(f"runs/{run_id}/{relative.as_posix()}", source.read_bytes())

        current_run = project["status"]["current_run"]
        for run_id in sorted(self.lineage_closure(project, current_run)):
            run = project["runs"][run_id]
            run_root = run["_run_root"]

            # The next agent needs the current prompt and final output, not a full
            # replay of every ancestor conversation.
            if run_id == current_run:
                files = run.get("files", {})
                for key in ("prompt", "output"):
                    reference = files.get(key) if isinstance(files, dict) else None
                    if isinstance(reference, str):
                        add_run_file(run_id, run_root, run_root.joinpath(*PurePosixPath(reference).parts))

            # Proof artifacts and dependency pins remain useful across a lineage,
            # even when intermediate prompts and event streams have been superseded.
            artifacts_root = run.get("_artifacts_root")
            if isinstance(artifacts_root, Path):
                for path in sorted(
                    item for item in artifacts_root.rglob("*") if item.is_file() and not item.is_symlink()
                ):
                    add_run_file(run_id, run_root, path)
            environment = run.get("environment", {})
            lockfiles = environment.get("dependency_lockfiles", []) if isinstance(environment, dict) else []
            if isinstance(lockfiles, list):
                for reference in lockfiles:
                    if isinstance(reference, str):
                        add_run_file(run_id, run_root, run_root.joinpath(*PurePosixPath(reference).parts))

            # Strong verification is part of the mathematical handoff, whereas a
            # weak or merely agent-reported log is retained only when status cites it.
            result = run.get("result", {})
            if isinstance(result, dict) and result.get("verification") in {
                "machine_checked",
                "reproduced",
                "supervisor_reviewed",
            }:
                verification_log = result.get("verification_log")
                if isinstance(verification_log, str):
                    add_run_file(
                        run_id,
                        run_root,
                        run_root.joinpath(*PurePosixPath(verification_log).parts),
                    )

        # Status evidence is authoritative and may come from a non-ancestor branch.
        # Store it once at its project-relative run path (or under project/ for
        # project-level evidence), rather than duplicating it in an evidence/ tree.
        for category in ("established", "candidate_results", "special_cases"):
            for item in project["status"][category]:
                for evidence in item["evidence"]:
                    evidence_path = PurePosixPath(evidence)
                    source = project["root"].joinpath(*evidence_path.parts)
                    archive_path = evidence if evidence_path.parts[0] == "runs" else f"project/{evidence}"
                    members.setdefault(archive_path, source.read_bytes())
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
        folded_names: dict[str, str] = {}
        for name in members:
            folded = name.casefold()
            if folded in folded_names:
                raise RuntimeError(f"ZIP member {name!r} collides with {folded_names[folded]!r}")
            folded_names[folded] = name
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
            self.replace_generated_directories(
                [
                    (data_root, self.generated_data_root),
                    (static_root, self.generated_static_root),
                ],
                temporary_root,
            )

    @staticmethod
    def replace_generated_directories(replacements: list[tuple[Path, Path]], backup_root: Path) -> None:
        backups: list[tuple[Path, Path]] = []
        installed: list[Path] = []
        for source, destination in replacements:
            if not source.is_dir():
                raise RuntimeError(f"generated source directory is missing: {source}")
            if destination.is_symlink():
                raise RuntimeError(f"refusing to replace symlinked generated directory: {destination}")
            if destination.exists() and not destination.is_dir():
                raise RuntimeError(f"refusing to replace non-directory generated path: {destination}")
        try:
            for index, (_, destination) in enumerate(replacements):
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    backup = backup_root / f"backup-{index}"
                    os.replace(destination, backup)
                    backups.append((destination, backup))
            for source, destination in replacements:
                os.replace(source, destination)
                installed.append(destination)
        except Exception as original:
            recovery_errors: list[str] = []
            for destination in reversed(installed):
                try:
                    if destination.exists():
                        shutil.rmtree(destination)
                except OSError as exc:
                    recovery_errors.append(f"could not remove {destination}: {exc}")
            for destination, backup in reversed(backups):
                try:
                    if backup.exists():
                        os.replace(backup, destination)
                except OSError as exc:
                    recovery_errors.append(f"could not restore {destination}: {exc}")
            if recovery_errors:
                raise RuntimeError(
                    "generated output update failed and rollback was incomplete: " + "; ".join(recovery_errors)
                ) from original
            raise


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
