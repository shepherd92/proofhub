from __future__ import annotations

from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import unittest

from scripts.check_immutable_runs import immutable_changes


class ImmutableRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=self.root, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True, text=True, stdout=subprocess.PIPE
        ).stdout.strip()

    def test_published_run_cannot_be_changed(self) -> None:
        manifest = "content/projects/example/runs/run-001/run.yaml"
        self.write(manifest, "schema_version: 1\nrun_id: run-001\n")
        base = self.commit("published")
        self.write(manifest, "schema_version: 1\nrun_id: run-001\nextensions: {}\n")
        self.commit("rewrite")
        self.assertEqual([PurePosixPath("content/projects/example/runs/run-001")], immutable_changes(self.root, base))

    def test_draft_run_can_be_completed(self) -> None:
        manifest = "content/projects/example/runs/run-001/run.yaml"
        self.write(manifest, "schema_version: 1\nrun_id: run-001\ndraft: true\n")
        base = self.commit("draft")
        self.write(manifest, "schema_version: 1\nrun_id: run-001\ndraft: false\n")
        self.commit("publish")
        self.assertEqual([], immutable_changes(self.root, base))

    def test_new_run_is_allowed(self) -> None:
        self.write("content/projects/example/status.yaml", "schema_version: 1\n")
        base = self.commit("project")
        self.write("content/projects/example/runs/run-001/run.yaml", "schema_version: 1\nrun_id: run-001\n")
        self.commit("new run")
        self.assertEqual([], immutable_changes(self.root, base))

    def test_exact_original_publication_can_be_restored_after_a_failed_push(self) -> None:
        manifest = "content/projects/example/runs/run-001/run.yaml"
        output = "content/projects/example/runs/run-001/output.txt"
        self.write(manifest, "schema_version: 1\nrun_id: run-001\n")
        self.write(output, "original output\n")
        self.commit("published")
        self.write(output, "accidentally rewritten output\n")
        bad_base = self.commit("bad rewrite")
        self.write(output, "original output\n")
        self.commit("restore exact publication")
        self.assertEqual([], immutable_changes(self.root, bad_base))

    def test_corruption_remains_a_failure_after_an_unrelated_push(self) -> None:
        manifest = "content/projects/example/runs/run-001/run.yaml"
        output = "content/projects/example/runs/run-001/output.txt"
        self.write(manifest, "schema_version: 1\nrun_id: run-001\n")
        self.write(output, "original output\n")
        self.commit("published")
        self.write(output, "accidentally rewritten output\n")
        bad_base = self.commit("bad rewrite")
        self.write("README.md", "An unrelated later change.\n")
        self.commit("unrelated change")
        self.assertEqual(
            [PurePosixPath("content/projects/example/runs/run-001")],
            immutable_changes(self.root, bad_base),
        )

    def test_partial_restoration_or_an_extra_file_still_fails(self) -> None:
        manifest = "content/projects/example/runs/run-001/run.yaml"
        output = "content/projects/example/runs/run-001/output.txt"
        self.write(manifest, "schema_version: 1\nrun_id: run-001\n")
        self.write(output, "original output\n")
        self.commit("published")
        self.write(output, "bad output\n")
        bad_base = self.commit("bad rewrite")
        self.write(output, "original output\n")
        self.write("content/projects/example/runs/run-001/extra.txt", "not originally published\n")
        self.commit("partial restoration")
        self.assertEqual(
            [PurePosixPath("content/projects/example/runs/run-001")],
            immutable_changes(self.root, bad_base),
        )

    def test_non_ancestor_base_fails_closed(self) -> None:
        self.write("README.md", "base\n")
        common = self.commit("common")
        subprocess.run(["git", "switch", "-q", "-c", "other"], cwd=self.root, check=True)
        self.write("other.txt", "other branch\n")
        other = self.commit("other")
        subprocess.run(["git", "switch", "-q", "main"], cwd=self.root, check=True)
        self.write("main.txt", "main branch\n")
        self.commit("main")
        self.assertNotEqual(common, other)
        with self.assertRaisesRegex(ValueError, "not an ancestor"):
            immutable_changes(self.root, other)

    def test_first_parent_merge_result_defines_the_publication_snapshot(self) -> None:
        self.write("README.md", "base\n")
        base = self.commit("base")
        subprocess.run(["git", "switch", "-q", "-c", "feature"], cwd=self.root, check=True)
        manifest = "content/projects/example/runs/run-001/run.yaml"
        output = "content/projects/example/runs/run-001/output.txt"
        self.write(manifest, "schema_version: 1\nrun_id: run-001\n")
        self.write(output, "intermediate branch output\n")
        self.commit("intermediate feature state")
        self.write(output, "reviewed merge output\n")
        self.commit("reviewed feature state")
        subprocess.run(["git", "switch", "-q", "main"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "merge", "-q", "--no-ff", "feature", "-m", "publish feature"],
            cwd=self.root,
            check=True,
        )
        self.assertEqual([], immutable_changes(self.root, base))
        self.write(output, "bad main-line rewrite\n")
        bad_base = self.commit("bad main-line rewrite")
        self.write(output, "reviewed merge output\n")
        self.commit("restore reviewed merge output")
        self.assertEqual([], immutable_changes(self.root, bad_base))


if __name__ == "__main__":
    unittest.main()
