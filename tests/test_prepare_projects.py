from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml

from scripts.prepare_projects import ProjectBuilder


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ProjectBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(REPOSITORY_ROOT / "schemas", self.root / "schemas")
        projects = self.root / "content" / "projects"
        projects.mkdir(parents=True)
        shutil.copy2(REPOSITORY_ROOT / "content" / "projects" / "_index.md", projects / "_index.md")
        shutil.copytree(
            REPOSITORY_ROOT / "content" / "projects" / "euclid-primes",
            projects / "euclid-primes",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def project(self) -> Path:
        return self.root / "content" / "projects" / "euclid-primes"

    @property
    def status_path(self) -> Path:
        return self.project / "status.yaml"

    @property
    def run_path(self) -> Path:
        return self.project / "runs" / "run-001" / "run.yaml"

    def load_yaml(self, path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def save_yaml(self, path: Path, value: dict) -> None:
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    def validate(self) -> ProjectBuilder:
        builder = ProjectBuilder(self.root)
        builder.validate()
        return builder

    def assert_invalid_with(self, text: str) -> None:
        builder = self.validate()
        self.assertTrue(builder.diagnostics)
        rendered = "\n".join(item.render() for item in builder.diagnostics)
        self.assertIn(text, rendered)

    def content_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted((self.root / "content").rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(self.root).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_valid_example_passes(self) -> None:
        builder = self.validate()
        self.assertEqual([], builder.diagnostics)
        self.assertEqual(["euclid-primes"], list(builder.projects))

    def test_missing_or_malformed_schema_fails_closed(self) -> None:
        status_schema = self.root / "schemas" / "status-v1.schema.json"
        status_schema.unlink()
        self.assert_invalid_with("cannot load schema")
        shutil.copy2(REPOSITORY_ROOT / "schemas" / "status-v1.schema.json", status_schema)
        status_schema.write_text("{not-json", encoding="utf-8")
        self.assert_invalid_with("cannot load schema")

    def test_invalid_schema_definition_is_rejected(self) -> None:
        status_schema = self.root / "schemas" / "status-v1.schema.json"
        status_schema.write_text('{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"not-a-type"}', encoding="utf-8")
        self.assert_invalid_with("cannot load schema")

    def test_generation_is_reproducible_and_does_not_edit_content(self) -> None:
        builder = self.validate()
        self.assertFalse(builder.diagnostics)
        before = self.content_digest()
        builder.generate()
        first = (self.root / "static" / "generated" / "euclid-primes" / "handoff.zip").read_bytes()
        builder.generate()
        second = (self.root / "static" / "generated" / "euclid-primes" / "handoff.zip").read_bytes()
        self.assertEqual(before, self.content_digest())
        self.assertEqual(first, second)
        self.assertTrue((self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json").is_file())
        self.assertTrue((self.root / "static" / "generated" / "euclid-primes" / "continuation.txt").is_file())

    def test_handoff_has_manifest_and_excludes_raw_directory(self) -> None:
        raw = self.project / "runs" / "run-001" / "raw"
        raw.mkdir()
        (raw / "vendor-export.txt").write_text("not needed in handoff", encoding="utf-8")
        builder = self.validate()
        self.assertFalse(builder.diagnostics)
        builder.generate()
        archive_path = self.root / "static" / "generated" / "euclid-primes" / "handoff.zip"
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("continuation.txt", names)
            self.assertIn("runs/run-001/artifacts/Main.lean", names)
            self.assertFalse(any("raw/" in name for name in names))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual("euclid-primes", manifest["project_id"])

    def test_raw_files_cannot_be_referenced_by_public_metadata(self) -> None:
        raw = self.project / "runs" / "run-001" / "raw"
        raw.mkdir()
        (raw / "private.txt").write_text("not public", encoding="utf-8")
        status = self.load_yaml(self.status_path)
        status["candidate_results"][0]["evidence"] = ["runs/run-001/raw/private.txt"]
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("raw files may be stored but may not be referenced")

    def test_draft_project_does_not_generate_public_data_or_downloads(self) -> None:
        index_path = self.project / "index.md"
        index_path.write_text(
            index_path.read_text(encoding="utf-8").replace("draft: false", "draft: true"),
            encoding="utf-8",
        )
        builder = self.validate()
        self.assertEqual([], builder.diagnostics)
        self.assertEqual({}, builder.projects)
        builder.generate()
        self.assertFalse((self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json").exists())
        self.assertFalse((self.root / "static" / "generated" / "euclid-primes").exists())

    def test_project_must_disable_automatic_resource_publication(self) -> None:
        index_path = self.project / "index.md"
        index_path.write_text(
            index_path.read_text(encoding="utf-8").replace("publishResources: false", "publishResources: true"),
            encoding="utf-8",
        )
        self.assert_invalid_with("build.publishResources to false")

    def test_uploaded_file_size_limit_is_enforced(self) -> None:
        with patch("scripts.prepare_projects.MAX_PROJECT_FILE_BYTES", 1):
            self.assert_invalid_with("uploaded file exceeds")

    def test_unknown_core_field_fails_but_extensions_are_allowed(self) -> None:
        run = self.load_yaml(self.run_path)
        run["unexpected"] = True
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("Additional properties are not allowed")
        run.pop("unexpected")
        run["extensions"]["experiment_note"] = "allowed"
        self.save_yaml(self.run_path, run)
        self.assertEqual([], self.validate().diagnostics)

    def test_project_and_run_directory_ids_must_match(self) -> None:
        index_path = self.project / "index.md"
        index_path.write_text(index_path.read_text(encoding="utf-8").replace("project_id: euclid-primes", "project_id: other"), encoding="utf-8")
        self.assert_invalid_with("project_id must match directory name")
        index_path.write_text(index_path.read_text(encoding="utf-8").replace("project_id: other", "project_id: euclid-primes"), encoding="utf-8")
        run = self.load_yaml(self.run_path)
        run["run_id"] = "other"
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("run_id must match directory name")

    def test_missing_current_run_and_parent_are_rejected(self) -> None:
        status = self.load_yaml(self.status_path)
        status["current_run"] = "missing-run"
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("unknown run ID 'missing-run'")
        status["current_run"] = "run-001"
        self.save_yaml(self.status_path, status)
        run = self.load_yaml(self.run_path)
        run["parents"] = [{"run_id": "missing-run", "relation": "continuation"}]
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("unknown parent run 'missing-run'")

    def test_cycles_are_rejected(self) -> None:
        run_002 = self.project / "runs" / "run-002"
        shutil.copytree(self.project / "runs" / "run-001", run_002)
        first = self.load_yaml(self.run_path)
        first["parents"] = [{"run_id": "run-002", "relation": "continuation"}]
        self.save_yaml(self.run_path, first)
        second_path = run_002 / "run.yaml"
        second = self.load_yaml(second_path)
        second["run_id"] = "run-002"
        second["parents"] = [{"run_id": "run-001", "relation": "continuation"}]
        self.save_yaml(second_path, second)
        self.assert_invalid_with("run lineage contains a cycle")

    def test_unsafe_and_missing_references_are_rejected(self) -> None:
        run = self.load_yaml(self.run_path)
        run["files"]["prompt"] = "../prompt.txt"
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("unsafe or non-normalized file reference")
        run["files"]["prompt"] = "./prompt.txt"
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("unsafe or non-normalized file reference")
        run["files"]["prompt"] = "missing.txt"
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("referenced path does not exist")

    def test_bad_event_sequence_is_rejected(self) -> None:
        events = self.project / "runs" / "run-001" / "events.jsonl"
        lines = events.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[1])
        event["seq"] = 4
        lines[1] = json.dumps(event)
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assert_invalid_with("expected seq 2")

    def test_redaction_acknowledgement_is_required(self) -> None:
        run = self.load_yaml(self.run_path)
        run["publication"]["redaction_reviewed"] = False
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("True was expected")

    def test_terminal_claim_requires_strong_verification(self) -> None:
        status = self.load_yaml(self.status_path)
        status["outcome"] = "proved"
        status["verification"] = "agent_reported"
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("is not one of ['machine_checked', 'supervisor_reviewed']")

    def test_terminal_status_requires_established_evidence(self) -> None:
        status = self.load_yaml(self.status_path)
        status["outcome"] = "proved"
        status["verification"] = "supervisor_reviewed"
        status["established"] = []
        self.save_yaml(self.status_path, status)
        builder = self.validate()
        self.assertTrue(
            any(diagnostic.location == "established" for diagnostic in builder.diagnostics),
            "expected terminal status validation to reject an empty established list",
        )

    def test_terminal_run_requires_verification_evidence(self) -> None:
        run = self.load_yaml(self.run_path)
        run["result"]["outcome"] = "proved"
        run["result"]["verification"] = "machine_checked"
        run["result"]["verification_command"] = None
        run["result"]["verification_log"] = None
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("None is not of type 'string'")

    def test_validation_failure_leaves_generated_output_unchanged(self) -> None:
        builder = self.validate()
        self.assertFalse(builder.diagnostics)
        builder.generate()
        output = self.root / "static" / "generated" / "euclid-primes" / "continuation.txt"
        before = output.read_bytes()
        self.status_path.unlink()
        failed = self.validate()
        self.assertTrue(failed.diagnostics)
        with self.assertRaises(RuntimeError):
            failed.generate()
        self.assertEqual(before, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
