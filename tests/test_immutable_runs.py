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


if __name__ == "__main__":
    unittest.main()
