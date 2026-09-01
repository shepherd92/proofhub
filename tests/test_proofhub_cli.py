from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from scripts.prepare_projects import ProjectBuilder
from scripts.proofhub import main


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ProofHubScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(REPOSITORY_ROOT / "schemas", self.root / "schemas")
        projects = self.root / "content" / "projects"
        projects.mkdir(parents=True)
        shutil.copy2(REPOSITORY_ROOT / "content" / "projects" / "_index.md", projects / "_index.md")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call_cli(self, arguments: list[str]) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(arguments)

    def imported_run_source(
        self,
        name: str = "incoming-run",
        *,
        prompt: str = "Continue from the supplied proof state.",
        output: str = "The finite case reduces to the stated lemma.",
        summary: str = "Reduced the remaining argument to one explicit lemma.",
    ) -> Path:
        source = self.root / name
        source.mkdir()
        (source / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        (source / "output.txt").write_text(output + "\n", encoding="utf-8")
        (source / "summary.txt").write_text(summary + "\n", encoding="utf-8")
        return source

    def test_new_project_creates_complete_unpublished_scaffold_and_refuses_overwrite(self) -> None:
        arguments = [
            "--root",
            str(self.root),
            "new-project",
            "riemann-hypothesis",
            "--title",
            "Riemann hypothesis",
        ]
        self.assertEqual(0, self.call_cli(arguments))
        project = self.root / "content" / "projects" / "riemann-hypothesis"
        self.assertTrue((project / "runs" / "run-001" / "artifacts" / "README.txt").is_file())
        self.assertIn("draft: true", (project / "index.md").read_text(encoding="utf-8"))
        run = yaml.safe_load((project / "runs" / "run-001" / "run.yaml").read_text(encoding="utf-8"))
        self.assertTrue(run["draft"])
        self.assertNotIn("submitted_by", run)
        self.assertFalse(run["publication"]["redaction_reviewed"])
        builder = ProjectBuilder(self.root)
        self.assertTrue(builder.validate())
        self.assertEqual({}, builder.projects)
        index = project / "index.md"
        index.write_text(index.read_text(encoding="utf-8").replace("draft: true", "draft: false", 1), encoding="utf-8")
        run["draft"] = False
        run["publication"]["redaction_reviewed"] = True
        (project / "runs" / "run-001" / "run.yaml").write_text(
            yaml.safe_dump(run, sort_keys=False), encoding="utf-8"
        )
        published = ProjectBuilder(self.root)
        self.assertTrue(published.validate(), "generated scaffold should be structurally publishable after review flags change")
        self.assertEqual(1, self.call_cli(arguments))

    def test_new_run_uses_current_run_as_parent_and_is_excluded_until_published(self) -> None:
        shutil.copytree(
            REPOSITORY_ROOT / "content" / "projects" / "euclid-primes",
            self.root / "content" / "projects" / "euclid-primes",
        )
        self.assertEqual(
            0,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "new-run",
                    "euclid-primes",
                ]
            ),
        )
        run_path = self.root / "content" / "projects" / "euclid-primes" / "runs" / "run-002" / "run.yaml"
        run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
        self.assertEqual([{"run_id": "run-001", "relation": "continuation"}], run["parents"])
        self.assertTrue(run["draft"])
        self.assertNotIn("submitted_by", run)
        builder = ProjectBuilder(self.root)
        self.assertTrue(builder.validate())
        builder.generate()
        record = json.loads(
            (self.root / "data" / "generated" / "proof_projects" / "euclid-primes.json").read_text(encoding="utf-8")
        )
        self.assertEqual(["run-001"], [item["run_id"] for item in record["runs"]])

    def test_submitted_by_remains_available_for_imported_material(self) -> None:
        arguments = [
            "--root",
            str(self.root),
            "new-project",
            "imported-problem",
            "--title",
            "Imported problem",
            "--submitted-by",
            "External research group",
        ]
        self.assertEqual(0, self.call_cli(arguments))
        run_path = self.root / "content" / "projects" / "imported-problem" / "runs" / "run-001" / "run.yaml"
        run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
        self.assertEqual("External research group", run["submitted_by"])

    def test_import_project_needs_only_problem_prompt_output_and_summary(self) -> None:
        source = self.imported_run_source(summary="Found a useful reduction.\nThe last lemma remains open.")
        problem = self.root / "problem-statement.txt"
        problem.write_text("Prove that every widget has finite rank.\n", encoding="utf-8")
        arguments = [
            "--root",
            str(self.root),
            "import-project",
            "widget-rank",
            "--title",
            "Finite widget rank",
            "--problem-file",
            str(problem),
            "--run-dir",
            str(source),
            "--outcome",
            "partial",
        ]
        self.assertEqual(0, self.call_cli(arguments))
        project = self.root / "content" / "projects" / "widget-rank"
        run_root = project / "runs" / "run-001"
        manifest = yaml.safe_load((run_root / "run.yaml").read_text(encoding="utf-8"))
        status = yaml.safe_load((project / "status.yaml").read_text(encoding="utf-8"))
        self.assertEqual(
            "Prove that every widget has finite rank.\n",
            (project / "problem.txt").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            "Continue from the supplied proof state.\n",
            (run_root / "prompt.txt").read_text(encoding="utf-8"),
        )
        self.assertEqual("Found a useful reduction. The last lemma remains open.", manifest["result"]["summary"])
        self.assertEqual("partial", manifest["result"]["outcome"])
        self.assertEqual("unknown", manifest["agent"]["architecture"]["type"])
        self.assertEqual("not-recorded", manifest["models"][0]["name"])
        self.assertTrue(manifest["draft"])
        self.assertEqual("partial", status["outcome"])
        self.assertTrue((run_root / "artifacts" / "NO_ARTIFACTS.txt").is_file())
        event = json.loads((run_root / "events.jsonl").read_text(encoding="utf-8"))
        self.assertIn("without a separate event transcript", event["content"])
        builder = ProjectBuilder(self.root)
        self.assertTrue(builder.validate(), "the imported draft must be canonical v1 data")
        self.assertEqual({}, builder.projects)
        self.assertEqual(1, self.call_cli(arguments), "an import must never overwrite its destination")

    def test_empty_optional_artifacts_directory_gets_a_tracked_marker(self) -> None:
        source = self.imported_run_source()
        (source / "artifacts").mkdir()
        problem = self.root / "empty-artifacts-problem.txt"
        problem.write_text("An exact mathematical problem.\n", encoding="utf-8")
        self.assertEqual(
            0,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "import-project",
                    "empty-artifacts",
                    "--title",
                    "Empty artifacts",
                    "--problem-file",
                    str(problem),
                    "--run-dir",
                    str(source),
                ]
            ),
        )
        marker = (
            self.root
            / "content"
            / "projects"
            / "empty-artifacts"
            / "runs"
            / "run-001"
            / "artifacts"
            / "NO_ARTIFACTS.txt"
        )
        self.assertTrue(marker.is_file())

    def test_import_and_finalize_project_is_one_reviewed_operation(self) -> None:
        source = self.imported_run_source()
        problem = self.root / "problem.txt"
        problem.write_text("Decide whether all finite widgets are ranked.\n", encoding="utf-8")
        import_arguments = [
            "--root",
            str(self.root),
            "import-project",
            "finite-widgets",
            "--title",
            "Finite widgets",
            "--problem-file",
            str(problem),
            "--run-dir",
            str(source),
            "--outcome",
            "partial",
        ]
        self.assertEqual(0, self.call_cli(import_arguments))
        project = self.root / "content" / "projects" / "finite-widgets"
        run_path = project / "runs" / "run-001" / "run.yaml"
        self.assertEqual(
            1,
            self.call_cli(["--root", str(self.root), "finalize-run", "finite-widgets"]),
            "redaction review must be explicit",
        )
        self.assertTrue(yaml.safe_load(run_path.read_text(encoding="utf-8"))["draft"])
        self.assertEqual(
            0,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "finalize-run",
                    "finite-widgets",
                    "--redaction-reviewed",
                ]
            ),
        )
        manifest = yaml.safe_load(run_path.read_text(encoding="utf-8"))
        front = (project / "index.md").read_text(encoding="utf-8")
        status = yaml.safe_load((project / "status.yaml").read_text(encoding="utf-8"))
        self.assertFalse(manifest["draft"])
        self.assertTrue(manifest["publication"]["redaction_reviewed"])
        self.assertIn("draft: false", front)
        self.assertEqual("run-001", status["current_run"])
        self.assertEqual(["run-001"], status["as_of_runs"])
        builder = ProjectBuilder(self.root)
        self.assertTrue(builder.validate())
        self.assertIn("finite-widgets", builder.projects)

    def test_import_run_uses_current_parent_and_finalize_preserves_stronger_status(self) -> None:
        shutil.copytree(
            REPOSITORY_ROOT / "content" / "projects" / "euclid-primes",
            self.root / "content" / "projects" / "euclid-primes",
        )
        source = self.imported_run_source(
            output="This attempt found no proof of the remaining Lean lemma.",
            summary="No new verified lemma was obtained.",
        )
        artifacts = source / "artifacts"
        artifacts.mkdir()
        (artifacts / "notes.txt").write_text("Reusable search notes.\n", encoding="utf-8")
        self.assertEqual(
            0,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "import-run",
                    "euclid-primes",
                    "--run-dir",
                    str(source),
                ]
            ),
        )
        project = self.root / "content" / "projects" / "euclid-primes"
        run_path = project / "runs" / "run-002" / "run.yaml"
        manifest = yaml.safe_load(run_path.read_text(encoding="utf-8"))
        self.assertEqual([{"run_id": "run-001", "relation": "continuation"}], manifest["parents"])
        self.assertTrue(manifest["draft"])
        self.assertEqual(
            "Reusable search notes.\n",
            (run_path.parent / "artifacts" / "notes.txt").read_text(encoding="utf-8"),
        )
        before = yaml.safe_load((project / "status.yaml").read_text(encoding="utf-8"))
        self.assertEqual("run-001", before["current_run"], "draft import must not alter public status")
        self.assertEqual(
            0,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "finalize-run",
                    "euclid-primes",
                    "--redaction-reviewed",
                ]
            ),
        )
        after = yaml.safe_load((project / "status.yaml").read_text(encoding="utf-8"))
        self.assertEqual("run-002", after["current_run"])
        self.assertEqual(["run-001", "run-002"], after["as_of_runs"])
        self.assertEqual("candidate_proof", after["outcome"], "a no-progress run must not erase stronger prior status")
        self.assertEqual("supervisor_reviewed", after["verification"])
        self.assertEqual("No new verified lemma was obtained.", after["summary"])
        builder = ProjectBuilder(self.root)
        self.assertTrue(builder.validate())

    def test_invalid_import_is_removed_before_it_can_appear_in_repository(self) -> None:
        source = self.imported_run_source()
        (source / "events.jsonl").write_text("not json\n", encoding="utf-8")
        problem = self.root / "problem.txt"
        problem.write_text("An exact problem.\n", encoding="utf-8")
        self.assertEqual(
            1,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "import-project",
                    "invalid-import",
                    "--title",
                    "Invalid import",
                    "--problem-file",
                    str(problem),
                    "--run-dir",
                    str(source),
                ]
            ),
        )
        self.assertFalse((self.root / "content" / "projects" / "invalid-import").exists())
        leftovers = list((self.root / "content" / "projects").glob("_proofhub-*"))
        self.assertEqual([], leftovers)

    def test_finalize_rejects_untouched_scaffold_without_partial_updates(self) -> None:
        self.assertEqual(
            0,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "new-project",
                    "unfinished",
                    "--title",
                    "Unfinished",
                ]
            ),
        )
        project = self.root / "content" / "projects" / "unfinished"
        before = (project / "runs" / "run-001" / "run.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            1,
            self.call_cli(
                [
                    "--root",
                    str(self.root),
                    "finalize-run",
                    "unfinished",
                    "--redaction-reviewed",
                ]
            ),
        )
        self.assertEqual(before, (project / "runs" / "run-001" / "run.yaml").read_text(encoding="utf-8"))
        self.assertIn("draft: true", (project / "index.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
