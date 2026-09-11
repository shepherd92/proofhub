from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

import yaml

from scripts.build_projects import ProjectBuilder


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
    def result_path(self) -> Path:
        return self.project / "runs" / "run-001" / "result.yaml"

    def builder(self) -> ProjectBuilder:
        builder = ProjectBuilder(self.root)
        builder.validate()
        return builder

    def assert_invalid_with(self, text: str) -> None:
        builder = self.builder()
        rendered = "\n".join(item.render() for item in builder.diagnostics)
        self.assertIn(text, rendered)

    def content_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted((self.root / "content").rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(self.root).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_example_uses_the_minimal_unversioned_format(self) -> None:
        builder = self.builder()
        self.assertEqual([], builder.diagnostics)
        self.assertEqual(["euclid-primes"], list(builder.projects))
        result = yaml.safe_load(self.result_path.read_text(encoding="utf-8"))
        self.assertNotIn("schema_version", result)
        run_files = {
            path.name for path in self.result_path.parent.iterdir() if path.is_file()
        }
        self.assertTrue({"prompt.txt", "output.txt", "result.yaml"}.issubset(run_files))

    def test_generation_creates_chatgpt_prompts_context_and_site_data(self) -> None:
        builder = self.builder()
        before = self.content_digest()
        builder.generate()
        self.assertEqual(before, self.content_digest())
        generated = self.root / "static" / "generated"
        self.assertTrue((generated / "new-project" / "prompt.txt").is_file())
        self.assertTrue((generated / "euclid-primes" / "continue.txt").is_file())
        self.assertTrue((generated / "euclid-primes" / "add-existing-result.txt").is_file())
        self.assertEqual(
            [generated / "euclid-primes" / "project-context.zip"],
            list(generated.rglob("*.zip")),
        )
        prompt = (generated / "euclid-primes" / "continue.txt").read_text(encoding="utf-8")
        self.assertIn("content/projects/euclid-primes/runs/run-002/", prompt)
        self.assertIn("model/version and usage", prompt)
        imported = (generated / "euclid-primes" / "add-existing-result.txt").read_text(encoding="utf-8")
        self.assertIn("Do not start new proof work", imported)
        self.assertIn("original research timestamps", imported)

    def test_context_archive_preserves_research_without_unrelated_files(self) -> None:
        artifacts = self.project / "runs" / "run-001" / "artifacts"
        (artifacts / "proof.md").write_text("# A useful proof\n", encoding="utf-8")
        (artifacts / "plot.png").write_bytes(b"binary\x00artifact")
        (self.project / "unrelated.txt").write_text("Do not package me.\n", encoding="utf-8")
        builder = self.builder()
        self.assertEqual([], builder.diagnostics)
        before = self.content_digest()
        builder.generate()
        archive_path = self.root / "static/generated/euclid-primes/project-context.zip"
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.namelist()
            prefix = "content/projects/euclid-primes/"
            expected = {
                prefix + "index.md", prefix + "problem.txt",
                prefix + "runs/run-001/prompt.txt", prefix + "runs/run-001/output.txt",
                prefix + "runs/run-001/result.yaml",
            }
            expected.update(
                path.relative_to(self.root).as_posix() for path in artifacts.rglob("*") if path.is_file()
            )
            self.assertEqual(expected, set(members))
            self.assertEqual(len(members), len(set(members)))
            for member in members:
                self.assertFalse(member.startswith("/"))
                self.assertNotIn("..", Path(member).parts)
                self.assertEqual((self.root / member).read_bytes(), archive.read(member))
        self.assertEqual(before, self.content_digest())
        first_archive = archive_path.read_bytes()
        builder.generate()
        self.assertEqual(first_archive, archive_path.read_bytes())
        record = json.loads((self.root / "data/generated/proof_projects/euclid-primes.json").read_text())
        self.assertEqual("generated/euclid-primes/project-context.zip", record["downloads"]["context"])

    def test_three_run_files_work_without_any_legacy_records(self) -> None:
        # The historical example retains its originals; new contributors need none of them.
        for relative in ("status.yaml", "runs/run-001/run.yaml", "runs/run-001/events.jsonl", "runs/run-001/verification.log"):
            (self.project / relative).unlink(missing_ok=True)
        builder = self.builder()
        self.assertEqual([], builder.diagnostics)
        before = self.content_digest()
        builder.generate()
        self.assertEqual(before, self.content_digest())

    def test_latest_numbered_run_becomes_current_without_a_status_file(self) -> None:
        second = self.project / "runs" / "run-002"
        second.mkdir()
        (second / "prompt.txt").write_text("Continue the proof.\n", encoding="utf-8")
        (second / "output.txt").write_text("A new reduction.\n", encoding="utf-8")
        result = yaml.safe_load(self.result_path.read_text(encoding="utf-8"))
        result["summary"] = "Reduced the remaining problem to one lemma."
        result["current_status"]["summary"] = "Only one explicit lemma remains."
        (second / "result.yaml").write_text(
            yaml.safe_dump(result, sort_keys=False), encoding="utf-8"
        )
        builder = self.builder()
        self.assertEqual([], builder.diagnostics)
        builder.generate()
        record = json.loads(
            (self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("run-002", record["status"]["current_run"])
        self.assertEqual("Only one explicit lemma remains.", record["status"]["summary"])
        self.assertEqual(["run-002", "run-001"], [run["run_id"] for run in record["runs"]])
        with zipfile.ZipFile(self.root / "static/generated/euclid-primes/project-context.zip") as archive:
            self.assertIn("content/projects/euclid-primes/runs/run-001/prompt.txt", archive.namelist())
            self.assertIn("content/projects/euclid-primes/runs/run-002/output.txt", archive.namelist())

    def test_missing_core_file_and_bad_yaml_are_clear_errors(self) -> None:
        (self.result_path.parent / "prompt.txt").unlink()
        self.assert_invalid_with("missing prompt")
        (self.result_path.parent / "prompt.txt").write_text("A prompt.\n", encoding="utf-8")
        result = yaml.safe_load(self.result_path.read_text(encoding="utf-8"))
        result["current_status"].pop("next_steps")
        self.result_path.write_text(yaml.safe_dump(result, sort_keys=False), encoding="utf-8")
        self.assert_invalid_with("'next_steps' is a required property")

    def test_unknown_optional_metadata_is_omitted_not_fabricated(self) -> None:
        result = yaml.safe_load(self.result_path.read_text(encoding="utf-8"))
        for key in ("model", "agent", "timing", "capacity", "configuration"):
            result.pop(key, None)
        self.result_path.write_text(yaml.safe_dump(result, sort_keys=False), encoding="utf-8")
        builder = self.builder()
        self.assertEqual([], builder.diagnostics)
        builder.generate()
        record = json.loads(
            (self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json").read_text(
                encoding="utf-8"
            )
        )
        run = record["runs"][0]
        self.assertNotIn("model", run)
        self.assertNotIn("capacity", run)
        self.assertNotIn("not-recorded", json.dumps(record))

    def test_git_noreply_username_is_the_submitter(self) -> None:
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "content", "schemas"], cwd=self.root, check=True)
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": "Supervisor",
                "GIT_AUTHOR_EMAIL": "123+proof-supervisor@users.noreply.github.com",
                "GIT_COMMITTER_NAME": "Supervisor",
                "GIT_COMMITTER_EMAIL": "123+proof-supervisor@users.noreply.github.com",
            }
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "Add proof result"],
            cwd=self.root,
            check=True,
            env=environment,
        )
        builder = self.builder()
        builder.generate()
        record = json.loads(
            (self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json").read_text(
                encoding="utf-8"
            )
        )
        submitter = record["runs"][0]["repository_attribution"]["added_by"]
        self.assertEqual("@proof-supervisor", submitter["label"])
        self.assertNotIn("users.noreply.github.com", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
