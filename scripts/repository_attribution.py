#!/usr/bin/env python3
"""Derive public contributor attribution from immutable Git history.

Git is the source of truth for who added repository content. GitHub's API is an
optional enhancement for commits whose author email does not expose a GitHub
handle; attribution still works offline and never publishes an email address.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
GITHUB_REPOSITORY_PATTERN = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+)$"
)
GITHUB_NOREPLY_PATTERN = re.compile(
    r"^(?:\d+\+)?(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)"
    r"@users\.noreply\.github\.com$",
    re.IGNORECASE,
)
COAUTHOR_PATTERN = re.compile(
    r"^Co-authored-by:\s*(?P<name>[^<>\r\n]+?)\s*<(?P<email>[^<>\s]+)>\s*$",
    re.IGNORECASE | re.MULTILINE,
)
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
EMAIL_LIKE_PATTERN = re.compile(r"\b[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+\b")


@dataclass(frozen=True)
class GitCommit:
    sha: str
    author_name: str
    author_email: str
    committed_at: str
    message: str


class RepositoryAttribution:
    """Resolve the commit author for files without making Git a validator dependency."""

    def __init__(
        self,
        root: Path,
        *,
        github_repository: str | None = None,
        github_token: str | None = None,
        github_api_url: str | None = None,
    ) -> None:
        self.root = root.resolve()
        repository = github_repository or os.environ.get("PROOF_RELAY_GITHUB_REPOSITORY")
        if not repository:
            repository = self._repository_from_origin()
        self.github_repository = repository if self._valid_repository(repository) else None
        self.github_token = github_token or os.environ.get("PROOF_RELAY_GITHUB_TOKEN")
        self.github_api_url = (
            github_api_url or os.environ.get("PROOF_RELAY_GITHUB_API_URL") or "https://api.github.com"
        ).rstrip("/")
        self._commit_cache: dict[tuple[str, bool], GitCommit | None] = {}
        self._login_cache: dict[str, str | None] = {}

    @staticmethod
    def _valid_repository(value: str | None) -> bool:
        return isinstance(value, str) and GITHUB_REPOSITORY_PATTERN.fullmatch(value) is not None

    def _repository_from_origin(self) -> str | None:
        output = self._git(["remote", "get-url", "origin"])
        if output is None:
            return None
        remote = output.decode("utf-8", errors="replace").strip()
        patterns = (
            r"^git@github\.com:(?P<repository>[^/\s]+/[^/\s]+?)(?:\.git)?$",
            r"^https://github\.com/(?P<repository>[^/\s]+/[^/\s]+?)(?:\.git)?/?$",
            r"^ssh://git@github\.com/(?P<repository>[^/\s]+/[^/\s]+?)(?:\.git)?/?$",
        )
        for pattern in patterns:
            match = re.fullmatch(pattern, remote)
            if match and self._valid_repository(match.group("repository")):
                return match.group("repository")
        return None

    def _git(self, arguments: list[str]) -> bytes | None:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=self.root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout if completed.returncode == 0 else None

    def _relative_path(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return None

    def _commit_sha_for_path(self, relative_path: str, introduced: bool) -> str | None:
        if introduced:
            output = self._git(
                ["log", "--full-history", "--diff-filter=A", "-1", "--format=%H", "--", relative_path]
            )
            if output is None:
                return None
            shas = [line.decode("ascii", errors="ignore").strip() for line in output.splitlines()]
            shas = [sha for sha in shas if SHA_PATTERN.fullmatch(sha)]
            return shas[0] if shas else None
        output = self._git(["log", "-1", "--format=%H", "--", relative_path])
        if output is None:
            return None
        sha = output.decode("ascii", errors="ignore").strip()
        return sha if SHA_PATTERN.fullmatch(sha) else None

    def commit_for_path(self, path: Path, *, introduced: bool) -> GitCommit | None:
        relative_path = self._relative_path(path)
        if relative_path is None:
            return None
        cache_key = (relative_path, introduced)
        if cache_key in self._commit_cache:
            return self._commit_cache[cache_key]
        sha = self._commit_sha_for_path(relative_path, introduced)
        if sha is None:
            self._commit_cache[cache_key] = None
            return None
        # Uppercase ``%aN``/``%aE`` apply the repository's standard .mailmap,
        # allowing a corrected GitHub identity to cover older commits without
        # rewriting immutable history.
        output = self._git(
            ["show", "-s", "--format=%H%x00%aN%x00%aE%x00%cI%x00%B%x00", sha]
        )
        if output is None:
            self._commit_cache[cache_key] = None
            return None
        fields = output.split(b"\x00", 5)
        if len(fields) < 5:
            self._commit_cache[cache_key] = None
            return None
        decoded = [field.decode("utf-8", errors="replace").strip() for field in fields[:5]]
        # Git releases differ on whether a UTC strict-ISO timestamp uses ``Z``
        # or the equivalent ``+00:00`` suffix. Keep the
        # generated site data byte-for-byte stable across Git versions.
        committed_at = decoded[3]
        if committed_at.endswith("Z"):
            committed_at = f"{committed_at[:-1]}+00:00"
        commit = GitCommit(
            sha=decoded[0],
            author_name=decoded[1],
            author_email=decoded[2],
            committed_at=committed_at,
            message=decoded[4],
        )
        if SHA_PATTERN.fullmatch(commit.sha) is None or not commit.author_name:
            commit = None
        self._commit_cache[cache_key] = commit
        return commit

    @staticmethod
    def github_login_from_email(email: str) -> str | None:
        match = GITHUB_NOREPLY_PATTERN.fullmatch(email.strip())
        return match.group("login") if match else None

    def _github_login_from_api(self, sha: str) -> str | None:
        if sha in self._login_cache:
            return self._login_cache[sha]
        if not self.github_repository or not self.github_token:
            self._login_cache[sha] = None
            return None
        owner, name = self.github_repository.split("/", 1)
        url = (
            f"{self.github_api_url}/repos/{quote(owner, safe='')}/"
            f"{quote(name, safe='')}/commits/{sha}"
        )
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.github_token}",
                "User-Agent": "proof-relay-attribution/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        login: str | None = None
        try:
            with urlopen(request, timeout=5) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_API_RESPONSE_BYTES:
                    raise ValueError("GitHub response is too large")
                raw_payload = response.read(MAX_API_RESPONSE_BYTES + 1)
                if len(raw_payload) > MAX_API_RESPONSE_BYTES:
                    raise ValueError("GitHub response is too large")
                payload = json.loads(raw_payload)
                if not isinstance(payload, dict):
                    raise ValueError("GitHub response is not an object")
                author = payload.get("author")
                candidate = author.get("login") if isinstance(author, dict) else None
                if isinstance(candidate, str) and GITHUB_LOGIN_PATTERN.fullmatch(candidate):
                    login = candidate
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            login = None
        self._login_cache[sha] = login
        return login

    @staticmethod
    def _safe_display_name(name: str, github_login: str | None) -> str:
        cleaned = name.strip()
        if (
            not cleaned
            or len(cleaned) > 200
            or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
            or EMAIL_LIKE_PATTERN.search(cleaned)
        ):
            return github_login or "Git author"
        return cleaned

    @classmethod
    def _person(cls, name: str, github_login: str | None, *, source: str) -> dict[str, str]:
        name = cls._safe_display_name(name, github_login)
        if github_login and GITHUB_LOGIN_PATTERN.fullmatch(github_login):
            return {
                "label": f"@{github_login}",
                "display_name": name or github_login,
                "github_username": github_login,
                "profile_url": f"https://github.com/{github_login}",
                "source": source,
            }
        return {"label": name, "display_name": name, "source": source}

    def _coauthors(self, commit: GitCommit, primary: dict[str, str]) -> list[dict[str, str]]:
        people: list[dict[str, str]] = []
        seen = {
            primary.get("github_username", "").casefold(),
            primary.get("display_name", "").casefold(),
        }
        seen.discard("")
        for match in COAUTHOR_PATTERN.finditer(commit.message):
            name = match.group("name").strip()
            login = self.github_login_from_email(match.group("email"))
            person = self._person(name, login, source="git_trailer")
            keys = {
                person.get("github_username", "").casefold(),
                person.get("display_name", "").casefold(),
            }
            keys.discard("")
            if keys & seen:
                continue
            seen.update(keys)
            people.append(person)
        return people

    def for_path(self, path: Path, *, introduced: bool) -> dict[str, Any] | None:
        commit = self.commit_for_path(path, introduced=introduced)
        if commit is None:
            return None
        login = self.github_login_from_email(commit.author_email)
        source = "git_noreply_email"
        if login is None:
            login = self._github_login_from_api(commit.sha)
            source = "github_api" if login else "git_author"
        primary = self._person(commit.author_name, login, source=source)
        result: dict[str, Any] = {
            "added_by" if introduced else "updated_by": primary,
            "co_contributors": self._coauthors(commit, primary),
            "commit": commit.sha,
            "short_commit": commit.sha[:7],
            "committed_at": commit.committed_at,
        }
        if self.github_repository:
            result["commit_url"] = (
                f"https://github.com/{self.github_repository}/commit/{commit.sha}"
            )
        return result
