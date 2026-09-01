from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
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

    def test_generated_site_data_contains_git_contributor_without_email(self) -> None:
        before_commit = ProjectBuilder(self.root)
        self.assertTrue(before_commit.validate())
        before_commit.generate()
        handoff_before = (
            self.root / "static" / "generated" / "euclid-primes" / "handoff.zip"
        ).read_bytes()
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "content", "schemas"], cwd=self.root, check=True)
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": "Integration Contributor",
                "GIT_AUTHOR_EMAIL": "123+integration-user@users.noreply.github.com",
                "GIT_AUTHOR_DATE": "2026-08-24T12:00:00+00:00",
                "GIT_COMMITTER_NAME": "Integration Contributor",
                "GIT_COMMITTER_EMAIL": "123+integration-user@users.noreply.github.com",
                "GIT_COMMITTER_DATE": "2026-08-24T12:00:00+00:00",
            }
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "Add calibration project"],
            cwd=self.root,
            check=True,
            env=environment,
        )
        with patch.dict(
            os.environ,
            {
                "PROOF_RELAY_GITHUB_REPOSITORY": "proof-org/proof-hub",
                "PROOF_RELAY_GITHUB_TOKEN": "",
            },
        ):
            builder = ProjectBuilder(self.root)
            self.assertTrue(builder.validate())
            builder.generate()
        handoff_after = (
            self.root / "static" / "generated" / "euclid-primes" / "handoff.zip"
        ).read_bytes()
        record_path = self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json"
        rendered = record_path.read_text(encoding="utf-8")
        record = json.loads(rendered)
        self.assertEqual("@integration-user", record["runs"][0]["repository_attribution"]["added_by"]["label"])
        self.assertEqual(
            "@integration-user",
            record["status"]["repository_attribution"]["updated_by"]["label"],
        )
        self.assertNotIn("users.noreply.github.com", rendered)
        self.assertEqual(handoff_before, handoff_after, "Git attribution must not change the handoff ZIP")

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
            self.assertIn("project/problem.txt", names)
            self.assertIn("project/status.yaml", names)
            self.assertIn("runs/run-001/prompt.txt", names)
            self.assertIn("runs/run-001/output.txt", names)
            self.assertIn("runs/run-001/artifacts/Main.lean", names)
            self.assertNotIn("runs/run-001/events.jsonl", names)
            self.assertNotIn("runs/run-001/run.yaml", names)
            self.assertNotIn("runs/run-001/verification.log", names)
            self.assertFalse(any(name.startswith("evidence/") for name in names))
            self.assertFalse(any("raw/" in name for name in names))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual("euclid-primes", manifest["project_id"])
            continuation = archive.read("continuation.txt").decode("utf-8")
            self.assertIn("Return the exact prompt, final output, a short factual summary", continuation)
            self.assertIn("metadata when known; do not guess", continuation)
            self.assertIn("Do not upload this handoff ZIP again", continuation)
            self.assertNotIn("visible event log", continuation)

    def test_handoff_keeps_lineage_artifacts_and_evidence_without_old_transcripts(self) -> None:
        ancestor_root = self.project / "runs" / "run-001"
        (ancestor_root / "old-notes.txt").write_text("superseded notes", encoding="utf-8")
        (ancestor_root / "lean-toolchain").write_text("leanprover/lean4:v4.21.0\n", encoding="utf-8")
        ancestor = self.load_yaml(self.run_path)
        ancestor["environment"]["dependency_lockfiles"] = ["lean-toolchain"]
        ancestor["result"]["verification"] = "reproduced"
        self.save_yaml(self.run_path, ancestor)

        current_root = self.project / "runs" / "run-002"
        shutil.copytree(ancestor_root, current_root)
        (current_root / "prompt.txt").write_text("Continue from the Lean scaffold.\n", encoding="utf-8")
        (current_root / "output.txt").write_text("A second-run result.\n", encoding="utf-8")
        (current_root / "artifacts" / "Current.lean").write_text("-- useful new proof\n", encoding="utf-8")
        current_path = current_root / "run.yaml"
        current = self.load_yaml(current_path)
        current["run_id"] = "run-002"
        current["parents"] = [{"run_id": "run-001", "relation": "continuation"}]
        current["execution"] = {
            "status": "imported",
            "started_at": None,
            "ended_at": None,
            "wall_seconds": None,
        }
        current["result"]["verification"] = "agent_reported"
        current["environment"]["dependency_lockfiles"] = []
        self.save_yaml(current_path, current)

        status = self.load_yaml(self.status_path)
        status["current_run"] = "run-002"
        status["as_of_runs"] = ["run-001", "run-002"]
        self.save_yaml(self.status_path, status)

        builder = self.validate()
        self.assertEqual([], builder.diagnostics)
        builder.generate()
        archive_path = self.root / "static" / "generated" / "euclid-primes" / "handoff.zip"
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            self.assertIn("runs/run-002/prompt.txt", names)
            self.assertIn("runs/run-002/output.txt", names)
            self.assertIn("runs/run-001/artifacts/Main.lean", names)
            self.assertIn("runs/run-002/artifacts/Current.lean", names)
            self.assertIn("runs/run-001/lean-toolchain", names)
            self.assertIn("runs/run-001/verification.log", names)
            self.assertIn("runs/run-001/output.txt", names, "status-cited ancestor evidence must remain")
            self.assertNotIn("runs/run-001/prompt.txt", names)
            self.assertNotIn("runs/run-001/events.jsonl", names)
            self.assertNotIn("runs/run-002/events.jsonl", names)
            self.assertNotIn("runs/run-001/old-notes.txt", names)
            self.assertNotIn("evidence/runs/run-001/output.txt", names)

    def test_raw_files_cannot_be_referenced_by_public_metadata(self) -> None:
        raw = self.project / "runs" / "run-001" / "raw"
        raw.mkdir()
        (raw / "private.txt").write_text("not public", encoding="utf-8")
        status = self.load_yaml(self.status_path)
        status["candidate_results"][0]["evidence"] = ["runs/run-001/raw/private.txt"]
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("raw files may be stored but may not be referenced")

    def test_raw_exclusion_is_case_insensitive(self) -> None:
        raw = self.project / "runs" / "run-001" / "Raw"
        raw.mkdir()
        (raw / "private.txt").write_text("not public", encoding="utf-8")
        status = self.load_yaml(self.status_path)
        status["candidate_results"][0]["evidence"] = ["runs/run-001/Raw/private.txt"]
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

    def test_schema_type_errors_are_reported_without_secondary_crashes(self) -> None:
        status = self.load_yaml(self.status_path)
        status["as_of_runs"] = None
        status["candidate_results"] = None
        self.save_yaml(self.status_path, status)
        run = self.load_yaml(self.run_path)
        run["parents"] = None
        run["execution"] = "not-a-mapping"
        run["environment"]["dependency_lockfiles"] = None
        self.save_yaml(self.run_path, run)
        builder = self.validate()
        rendered = "\n".join(item.render() for item in builder.diagnostics)
        self.assertIn("None is not of type 'array'", rendered)
        self.assertIn("'not-a-mapping' is not of type 'object'", rendered)

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

    def test_ambiguous_or_nonfinite_event_json_is_rejected(self) -> None:
        events = self.project / "runs" / "run-001" / "events.jsonl"
        lines = events.read_text(encoding="utf-8").splitlines()
        lines[0] = '{"seq":1,"seq":2,"timestamp":"2026-08-24T09:30:00Z","type":"note","content":"x"}'
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assert_invalid_with("duplicate JSON key 'seq'")
        lines[0] = '{"seq":1,"timestamp":"2026-08-24T09:30:00Z","type":"tool_call","call_id":"x","tool":"t","input":NaN}'
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assert_invalid_with("non-finite JSON number")

    def test_completed_event_logs_require_monotonic_time_and_resolved_calls(self) -> None:
        events = self.project / "runs" / "run-001" / "events.jsonl"
        events.write_text(
            '{"seq":1,"timestamp":"2026-08-24T09:31:00Z","type":"tool_call","call_id":"x","tool":"Lean","input":{}}\n'
            '{"seq":2,"timestamp":"2026-08-24T09:30:30Z","type":"checkpoint","summary":"waiting"}\n',
            encoding="utf-8",
        )
        builder = self.validate()
        rendered = "\n".join(item.render() for item in builder.diagnostics)
        self.assertIn("event timestamps must be nondecreasing", rendered)
        self.assertIn("has no tool_result for call_id 'x'", rendered)

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

    def test_verification_log_must_contain_nonblank_evidence(self) -> None:
        log = self.project / "runs" / "run-001" / "verification.log"
        log.write_text("   \n", encoding="utf-8")
        run = self.load_yaml(self.run_path)
        run["result"]["verification"] = "reproduced"
        self.save_yaml(self.run_path, run)
        self.assert_invalid_with("verification log must not be empty")

    def test_terminal_status_requires_a_matching_strong_run(self) -> None:
        status = self.load_yaml(self.status_path)
        status["outcome"] = "proved"
        status["verification"] = "supervisor_reviewed"
        status["established"] = status.pop("candidate_results")
        status["candidate_results"] = []
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("terminal status requires an as_of_run with the same terminal outcome")

    def test_current_run_must_be_in_status_snapshot(self) -> None:
        status = self.load_yaml(self.status_path)
        status["as_of_runs"] = ["other-run"]
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("current_run must also appear in as_of_runs")

    def test_draft_runs_validate_but_are_not_published(self) -> None:
        draft_root = self.project / "runs" / "run-002"
        shutil.copytree(self.project / "runs" / "run-001", draft_root)
        run_path = draft_root / "run.yaml"
        run = self.load_yaml(run_path)
        run["run_id"] = "run-002"
        run["draft"] = True
        run["parents"] = [{"run_id": "run-001", "relation": "continuation"}]
        run["execution"] = {"status": "imported", "started_at": None, "ended_at": None, "wall_seconds": None}
        run["publication"]["redaction_reviewed"] = False
        self.save_yaml(run_path, run)
        builder = self.validate()
        self.assertEqual([], builder.diagnostics)
        self.assertEqual(["run-001"], list(builder.projects["euclid-primes"]["runs"]))

    def test_yaml_aliases_and_non_json_extension_values_are_rejected(self) -> None:
        text = self.status_path.read_text(encoding="utf-8")
        self.status_path.write_text(
            text.replace("extensions: {}", "extensions:\n  first: &shared {value: 1}\n  second: *shared"),
            encoding="utf-8",
        )
        self.assert_invalid_with("aliases are not allowed")
        status = self.load_yaml(REPOSITORY_ROOT / "content" / "projects" / "euclid-primes" / "status.yaml")
        status["extensions"]["binary"] = b"not-json"
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("unsupported YAML value type 'bytes'")

    def test_unhashable_yaml_key_is_a_diagnostic_not_a_crash(self) -> None:
        text = self.status_path.read_text(encoding="utf-8")
        self.status_path.write_text(
            text.replace("extensions: {}", "extensions:\n  ? [one, two]\n  : value"),
            encoding="utf-8",
        )
        self.assert_invalid_with("mapping keys must be scalar values")

    def test_portable_path_collisions_and_active_evidence_are_rejected(self) -> None:
        artifacts = self.project / "runs" / "run-001" / "artifacts"
        (artifacts / "Proof.txt").write_text("one", encoding="utf-8")
        (artifacts / "proof.txt").write_text("two", encoding="utf-8")
        self.assert_invalid_with("collides case-insensitively")
        (artifacts / "Proof.txt").unlink()
        (artifacts / "proof.txt").unlink()
        (artifacts / "report.html").write_text("<script>bad()</script>", encoding="utf-8")
        status = self.load_yaml(self.status_path)
        status["candidate_results"][0]["evidence"] = ["runs/run-001/artifacts/report.html"]
        self.save_yaml(self.status_path, status)
        self.assert_invalid_with("web-executable files may not be referenced")

    def test_active_artifacts_are_handoff_only(self) -> None:
        artifact = self.project / "runs" / "run-001" / "artifacts" / "report.html"
        artifact.write_text("<p>untrusted</p>", encoding="utf-8")
        builder = self.validate()
        self.assertEqual([], builder.diagnostics)
        record = builder.project_record(builder.projects["euclid-primes"])
        html_record = next(item for item in record["runs"][0]["resources"]["artifacts"] if item["name"] == "report.html")
        self.assertFalse(html_record["public"])
        builder.generate()
        archive = self.root / "static" / "generated" / "euclid-primes" / "handoff.zip"
        with zipfile.ZipFile(archive) as handoff:
            self.assertIn("runs/run-001/artifacts/report.html", handoff.namelist())

    def test_metadata_is_size_limited_before_parsing(self) -> None:
        self.status_path.write_text("x" * 128, encoding="utf-8")
        with patch("scripts.prepare_projects.MAX_METADATA_FILE_BYTES", 64):
            self.assert_invalid_with("metadata/log file exceeds")

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

    def test_generation_rolls_back_both_output_trees_on_install_failure(self) -> None:
        builder = self.validate()
        self.assertFalse(builder.diagnostics)
        builder.generate()
        data_path = self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json"
        static_path = self.root / "static" / "generated" / "euclid-primes" / "continuation.txt"
        before_data = data_path.read_bytes()
        before_static = static_path.read_bytes()
        status = self.load_yaml(self.status_path)
        status["summary"] = "A changed summary that should not survive a failed installation."
        self.save_yaml(self.status_path, status)
        changed = self.validate()
        self.assertFalse(changed.diagnostics)
        real_replace = shutil.os.replace

        def fail_second_install(source: Path, destination: Path) -> None:
            if Path(source).name == "static" and Path(destination) == changed.generated_static_root:
                raise OSError("simulated second-tree installation failure")
            real_replace(source, destination)

        with patch("scripts.prepare_projects.os.replace", side_effect=fail_second_install):
            with self.assertRaises(OSError):
                changed.generate()
        self.assertEqual(before_data, data_path.read_bytes())
        self.assertEqual(before_static, static_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
