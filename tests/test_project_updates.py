from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from scripts.build_projects import ProjectBuilder

ROOT = Path(__file__).resolve().parents[1]


class ProjectUpdateTests(unittest.TestCase):
    def test_editing_a_result_updates_status_without_a_review_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "schemas", root / "schemas")
            shutil.copytree(ROOT / "content/projects", root / "content/projects")
            result_path = root / "content/projects/euclid-primes/runs/run-001/result.yaml"
            result = yaml.safe_load(result_path.read_text())
            result["current_status"]["summary"] = "The corrected project status."
            result_path.write_text(yaml.safe_dump(result))
            builder = ProjectBuilder(root)
            self.assertTrue(builder.validate())
            self.assertEqual("The corrected project status.",
                             builder.project_record(builder.projects["euclid-primes"])["status"]["summary"])
            self.assertNotIn("draft", result)
            self.assertNotIn("publication", result)
