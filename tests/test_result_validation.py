from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts.build_projects import ProjectBuilder

ROOT = Path(__file__).resolve().parents[1]


class ResultValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "schemas", self.root / "schemas")
        shutil.copytree(ROOT / "content/projects", self.root / "content/projects")
        self.project = self.root / "content/projects/euclid-primes"
        self.result_path = self.project / "runs/run-001/result.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def result(self):
        return yaml.safe_load(self.result_path.read_text())

    def save(self, value):
        self.result_path.write_text(yaml.safe_dump(value, sort_keys=False))

    def check(self):
        builder = ProjectBuilder(self.root)
        builder.validate()
        return builder

    def assert_invalid(self, message):
        builder = self.check()
        self.assertIn(message, "\n".join(item.render() for item in builder.diagnostics))

    def test_symlinked_project_and_artifact_are_rejected(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside project")
        link = self.project / "runs/run-001/artifacts/link.txt"
        link.symlink_to(outside)
        self.assert_invalid("symbolic links")
        link.unlink()
        (self.root / "content/projects/linked-project").symlink_to(self.project, target_is_directory=True)
        self.assert_invalid("symbolic links")

    def test_duplicate_yaml_keys_and_nonfinite_configuration_are_rejected(self):
        original = self.result_path.read_text()
        self.result_path.write_text(original + "\nsummary: conflicting summary\n")
        self.assert_invalid("duplicate key")
        self.result_path.write_text(original)
        result = self.result()
        result["configuration"] = {"bad": float("nan")}
        self.save(result)
        self.assert_invalid("finite JSON-compatible")
        result["configuration"] = {"bad": b"binary"}
        self.save(result)
        self.assert_invalid("finite JSON-compatible")

    def test_duration_is_derived_without_editing_source(self):
        result = self.result()
        result["timing"].pop("duration_seconds")
        self.save(result)
        before = self.result_path.read_bytes()
        builder = self.check()
        self.assertEqual([], builder.diagnostics)
        builder.generate()
        data = json.loads((self.root / "data/generated/proof_projects/euclid-primes.json").read_text())
        self.assertEqual(720, data["runs"][0]["timing"]["duration_seconds"])
        self.assertEqual(before, self.result_path.read_bytes())

    def test_inconsistent_or_reversed_timing_is_rejected(self):
        result = self.result()
        result["timing"]["duration_seconds"] = 1
        self.save(result)
        self.assert_invalid("duration_seconds does not match")
        result["timing"]["ended_at"] = "2026-08-24T09:00:00Z"
        self.save(result)
        self.assert_invalid("ended_at must not be before")

    def test_ambiguous_run_numbers_and_bad_identifiers_are_rejected(self):
        shutil.copytree(self.project / "runs/run-001", self.project / "runs/run-0001")
        self.assert_invalid("already uses number 1")
        shutil.rmtree(self.project / "runs/run-0001")
        shutil.copytree(self.project / "runs/run-001", self.project / "runs/final")
        self.assert_invalid("run directory must be named")

    def test_missing_schema_and_missing_core_file_fail_clearly(self):
        (self.root / "schemas/result.schema.json").unlink()
        self.assert_invalid("cannot load result schema")
        shutil.copy2(ROOT / "schemas/result.schema.json", self.root / "schemas/result.schema.json")
        (self.project / "runs/run-001/output.txt").unlink()
        self.assert_invalid("missing output")

    def test_active_artifacts_are_kept_out_of_direct_page_links(self):
        (self.project / "runs/run-001/artifacts/proof.html").write_text("<script>alert(1)</script>")
        builder = self.check()
        self.assertEqual([], builder.diagnostics)
        artifacts = builder.projects["euclid-primes"]["current"]["resources"]["artifacts"]
        self.assertFalse(next(item for item in artifacts if item["name"] == "proof.html")["public"])

    def test_unknown_usage_is_not_the_configured_budget(self):
        result = self.result()
        self.assertEqual(20000, result["configuration"]["token_budget"])
        self.assertNotIn("capacity", result)
        result["capacity"] = {"amount": 500}
        self.save(result)
        self.assert_invalid("amount needs a unit")

    def test_failed_generated_replacement_restores_previous_output(self):
        builder = self.check()
        builder.generate()
        target = self.root / "data/generated/proof_projects/euclid-primes.json"
        before = target.read_bytes()
        original_replace = os.replace
        def fail_new_data(source, destination):
            if Path(source).name == "data":
                raise OSError("simulated replacement failure")
            return original_replace(source, destination)
        with patch("scripts.build_projects.os.replace", side_effect=fail_new_data):
            with self.assertRaises(OSError):
                builder.generate()
        self.assertEqual(before, target.read_bytes())
