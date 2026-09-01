from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.repository_attribution import RepositoryAttribution


class RepositoryAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git("init", "--quiet")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str, environment: dict[str, str] | None = None) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        return completed.stdout.strip()

    def commit(
        self,
        relative_path: str,
        content: str,
        *,
        name: str,
        email: str,
        timestamp: str,
        message: str,
    ) -> tuple[Path, str]:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git("add", "--", relative_path)
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_AUTHOR_DATE": timestamp,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
                "GIT_COMMITTER_DATE": timestamp,
            }
        )
        self.git("commit", "--quiet", "-m", message, environment=environment)
        return path, self.git("rev-parse", "HEAD")

    @staticmethod
    def without_github_credentials() -> patch.dict[str, str]:
        return patch.dict(
            os.environ,
            {
                "PROOF_RELAY_GITHUB_TOKEN": "",
                "PROOF_RELAY_GITHUB_REPOSITORY": "",
            },
        )

    def test_github_noreply_email_becomes_public_username(self) -> None:
        path, sha = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\n",
            name="Alice Example",
            email="12345+alice-proof@users.noreply.github.com",
            timestamp="2026-08-01T10:00:00+00:00",
            message="Add first proof run",
        )

        with self.without_github_credentials():
            result = RepositoryAttribution(
                self.root,
                github_repository="proof-org/proof-hub",
            ).for_path(path, introduced=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            {
                "label": "@alice-proof",
                "display_name": "Alice Example",
                "github_username": "alice-proof",
                "profile_url": "https://github.com/alice-proof",
                "source": "git_noreply_email",
            },
            result["added_by"],
        )
        self.assertEqual(sha, result["commit"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("12345+alice-proof@users.noreply.github.com", rendered)
        self.assertNotIn("users.noreply.github.com", rendered)

    def test_standard_email_falls_back_to_name_without_leaking_email(self) -> None:
        path, _ = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\n",
            name="Bob Mathematician",
            email="bob.private@example.edu",
            timestamp="2026-08-02T11:00:00+00:00",
            message="Import proof run",
        )

        with self.without_github_credentials():
            result = RepositoryAttribution(self.root).for_path(path, introduced=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            {
                "label": "Bob Mathematician",
                "display_name": "Bob Mathematician",
                "source": "git_author",
            },
            result["added_by"],
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("bob.private@example.edu", rendered)
        self.assertNotIn("example.edu", rendered)

    def test_mailmap_corrects_a_historical_identity_without_rewriting_history(self) -> None:
        path, _ = self.commit(
            "content/projects/example/status.yaml",
            "summary: historical status\n",
            name="local-account",
            email="incorrect identity",
            timestamp="2026-08-02T11:15:00+00:00",
            message="Add historical status",
        )
        (self.root / ".mailmap").write_text(
            "Correct Person <123+correct-user@users.noreply.github.com> "
            "local-account <incorrect identity>\n",
            encoding="utf-8",
        )

        with self.without_github_credentials():
            result = RepositoryAttribution(self.root).for_path(path, introduced=False)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("@correct-user", result["updated_by"]["label"])
        self.assertEqual("Correct Person", result["updated_by"]["display_name"])
        self.assertNotIn("incorrect identity", json.dumps(result))

    def test_email_like_author_name_is_not_published(self) -> None:
        path, _ = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\n",
            name="private.identity@example.edu",
            email="private.identity@example.edu",
            timestamp="2026-08-02T11:30:00+00:00",
            message="Import privacy-sensitive run",
        )

        with self.without_github_credentials():
            result = RepositoryAttribution(self.root).for_path(path, introduced=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("Git author", result["added_by"]["label"])
        self.assertNotIn("example.edu", json.dumps(result))

    def test_github_api_resolves_standard_email_without_becoming_required(self) -> None:
        path, sha = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\n",
            name="API Associated Author",
            email="private@example.edu",
            timestamp="2026-08-02T12:00:00+00:00",
            message="Add API-associated run",
        )

        payload = json.dumps({"author": {"login": "api-user"}}).encode("utf-8")

        class FakeResponse:
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *_arguments):
                return False

            def read(self, _limit: int) -> bytes:
                return payload

        with patch("scripts.repository_attribution.urlopen", return_value=FakeResponse()) as request:
            result = RepositoryAttribution(
                self.root,
                github_repository="proof-org/proof-hub",
                github_token="read-only-token",
            ).for_path(path, introduced=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("@api-user", result["added_by"]["label"])
        self.assertEqual("github_api", result["added_by"]["source"])
        self.assertIn(f"/commits/{sha}", request.call_args.args[0].full_url)
        self.assertNotIn("private@example.edu", json.dumps(result))

    def test_first_add_attribution_is_stable_after_later_changes(self) -> None:
        path, added_sha = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\nsummary: initial\n",
            name="Initial Contributor",
            email="100+initial@users.noreply.github.com",
            timestamp="2026-08-03T09:00:00+00:00",
            message="Add run",
        )
        _, updated_sha = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\nsummary: corrected before publication\n",
            name="Later Editor",
            email="200+later@users.noreply.github.com",
            timestamp="2026-08-03T10:00:00+00:00",
            message="Correct run metadata",
        )

        with self.without_github_credentials():
            resolver = RepositoryAttribution(self.root)
            introduced = resolver.for_path(path, introduced=True)
            updated = resolver.for_path(path, introduced=False)

        self.assertIsNotNone(introduced)
        self.assertIsNotNone(updated)
        assert introduced is not None and updated is not None
        self.assertEqual(added_sha, introduced["commit"])
        self.assertEqual("@initial", introduced["added_by"]["label"])
        self.assertEqual(updated_sha, updated["commit"])
        self.assertEqual("@later", updated["updated_by"]["label"])

    def test_status_like_file_uses_latest_update_not_first_add(self) -> None:
        path, _ = self.commit(
            "content/projects/example/status.yaml",
            "summary: initial\n",
            name="Original Curator",
            email="300+curator@users.noreply.github.com",
            timestamp="2026-08-04T08:00:00+00:00",
            message="Add project status",
        )
        _, update_sha = self.commit(
            "content/projects/example/status.yaml",
            "summary: reviewed update\n",
            name="Reviewing Supervisor",
            email="400+supervisor@users.noreply.github.com",
            timestamp="2026-08-04T09:00:00+00:00",
            message="Review project status",
        )
        self.commit(
            "README.md",
            "Unrelated documentation change.\n",
            name="Documentation Editor",
            email="500+docs@users.noreply.github.com",
            timestamp="2026-08-04T10:00:00+00:00",
            message="Update documentation",
        )

        with self.without_github_credentials():
            result = RepositoryAttribution(self.root).for_path(path, introduced=False)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertNotIn("added_by", result)
        self.assertEqual(update_sha, result["commit"])
        self.assertEqual("@supervisor", result["updated_by"]["label"])

    def test_coauthor_trailers_are_parsed_and_deduplicated(self) -> None:
        message = """Add collaborative result

Co-authored-by: Bob Builder <600+bob@users.noreply.github.com>
Co-authored-by: B. Duplicate <601+BOB@users.noreply.github.com>
Co-authored-by: Alternate Alice <700+alice@users.noreply.github.com>
Co-authored-by: Carol Researcher <carol.private@example.edu>
Co-authored-by: Carol Researcher <other.private@example.edu>
"""
        path, _ = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\n",
            name="Alice Primary",
            email="700+alice@users.noreply.github.com",
            timestamp="2026-08-05T12:00:00+00:00",
            message=message,
        )

        with self.without_github_credentials():
            result = RepositoryAttribution(self.root).for_path(path, introduced=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            [
                {
                    "label": "@bob",
                    "display_name": "Bob Builder",
                    "github_username": "bob",
                    "profile_url": "https://github.com/bob",
                    "source": "git_trailer",
                },
                {
                    "label": "Carol Researcher",
                    "display_name": "Carol Researcher",
                    "source": "git_trailer",
                },
            ],
            result["co_contributors"],
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("carol.private@example.edu", rendered)
        self.assertNotIn("other.private@example.edu", rendered)

    def test_untracked_path_and_path_without_repository_have_no_attribution(self) -> None:
        tracked, _ = self.commit(
            "tracked.txt",
            "tracked\n",
            name="Tracked Author",
            email="800+tracked@users.noreply.github.com",
            timestamp="2026-08-06T12:00:00+00:00",
            message="Add tracked file",
        )
        self.assertIsNotNone(tracked)
        untracked = self.root / "untracked.txt"
        untracked.write_text("not committed\n", encoding="utf-8")
        with self.without_github_credentials():
            resolver = RepositoryAttribution(self.root)
            self.assertIsNone(resolver.for_path(untracked, introduced=True))
            self.assertIsNone(resolver.for_path(untracked, introduced=False))

        with tempfile.TemporaryDirectory() as directory:
            plain_root = Path(directory)
            plain_file = plain_root / "file.txt"
            plain_file.write_text("not a repository\n", encoding="utf-8")
            with self.without_github_credentials():
                plain_resolver = RepositoryAttribution(plain_root)
                self.assertIsNone(plain_resolver.for_path(plain_file, introduced=True))
                self.assertIsNone(plain_resolver.for_path(plain_file, introduced=False))

    def test_generated_dictionary_contains_commit_metadata(self) -> None:
        path, sha = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\n",
            name="Metadata Author",
            email="900+metadata@users.noreply.github.com",
            timestamp="2026-08-07T13:14:15+00:00",
            message="Add metadata test run",
        )

        with self.without_github_credentials():
            result = RepositoryAttribution(
                self.root,
                github_repository="proof-org/proof-hub",
            ).for_path(path, introduced=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(sha, result["commit"])
        self.assertEqual(sha[:7], result["short_commit"])
        self.assertEqual("2026-08-07T13:14:15+00:00", result["committed_at"])
        self.assertEqual(
            f"https://github.com/proof-org/proof-hub/commit/{sha}",
            result["commit_url"],
        )
        self.assertEqual([], result["co_contributors"])
        self.assertEqual(result, json.loads(json.dumps(result)))

    def test_origin_remote_supplies_commit_link_for_local_preview(self) -> None:
        path, sha = self.commit(
            "content/projects/example/runs/run-001/run.yaml",
            "run_id: run-001\n",
            name="Local Preview Author",
            email="901+local-preview@users.noreply.github.com",
            timestamp="2026-08-08T13:14:15+00:00",
            message="Add locally previewed run",
        )
        self.git("remote", "add", "origin", "git@github.com:proof-org/proof-hub.git")

        with self.without_github_credentials():
            result = RepositoryAttribution(self.root).for_path(path, introduced=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            f"https://github.com/proof-org/proof-hub/commit/{sha}",
            result["commit_url"],
        )


if __name__ == "__main__":
    unittest.main()
