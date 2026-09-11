from __future__ import annotations

from pathlib import Path
import unittest
import yaml

from scripts.build_projects import ProjectBuilder

ROOT = Path(__file__).resolve().parents[1]


class PromptWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.builder = ProjectBuilder(ROOT)
        self.assertTrue(self.builder.validate())
        self.project = self.builder.projects["euclid-primes"]

    def test_new_project_prompt_produces_the_actual_page_and_file_contract(self):
        prompt = self.builder.new_project_prompt()
        self.assertIn("{{< proof-project >}}", prompt)
        self.assertIn("proofhub-upload/", prompt)
        self.assertIn("result.yaml", prompt)
        self.assertNotIn("schema_version", prompt)
        self.assertNotIn("redaction_reviewed", prompt)
        self.assertNotIn("finalize-run", prompt)
        self.assertIn("If you cannot create files", prompt)

    def test_continuation_includes_current_context_and_avoids_overwrite(self):
        prompt = self.builder.continuation_prompt(self.project, imported=False)
        self.assertIn(self.project["problem"].strip(), prompt)
        self.assertIn(self.project["current"]["output"].strip(), prompt)
        self.assertIn(self.project["current"]["prompt"].strip(), prompt)
        self.assertIn("runs/run-002/", prompt)
        self.assertIn("use the next unused number", prompt)
        self.assertIn("Do not overwrite earlier runs", prompt)
        self.assertIn("Preserve useful earlier conclusions", prompt)

    def test_packaging_preserves_original_records_and_metadata(self):
        prompt = self.builder.continuation_prompt(self.project, imported=True)
        self.assertIn("kind: imported", prompt)
        self.assertNotIn("kind: live", prompt)
        self.assertIn("original visible prompt(s) and output(s) verbatim", prompt)
        self.assertIn("Original prompt not recorded", prompt)
        self.assertIn("Do not record this packaging session", prompt)
        self.assertIn("Do not start new proof work", prompt)
        self.assertIn("never capacity spent", prompt)
        self.assertIn("account-wide quota", prompt)

    def test_prompt_yaml_core_satisfies_the_real_contract(self):
        text = self.builder.result_contract("imported")
        core = text[text.index("kind: imported"):text.index("\nAllowed outcomes")]
        value = yaml.safe_load(core)
        errors = list(self.builder._result_validator.iter_errors(value))
        self.assertEqual([], errors)
