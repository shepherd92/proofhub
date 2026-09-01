#!/usr/bin/env python3
"""Fail a static build when an internal HTML reference cannot be resolved."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import posixpath
import sys
from urllib.parse import unquote, urljoin, urlsplit


REFERENCE_ATTRIBUTES = {"action", "href", "poster", "src"}
IGNORED_SCHEMES = {"data", "javascript", "mailto", "tel"}
LEAKED_HOSTS = {"0.0.0.0", "127.0.0.1", "::1", "example.github.io", "localhost"}


@dataclass
class ParsedHTML:
    references: list[tuple[str, str]] = field(default_factory=list)
    anchors: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    relative_base: str | None = None


class ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = ParsedHTML()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs if value is not None}
        for name in ("id", "name"):
            if values.get(name):
                self.result.anchors.add(values[name])
        if tag.casefold() == "html" and values.get("data-wc-relurl"):
            self.result.relative_base = values["data-wc-relurl"]
        for name in REFERENCE_ATTRIBUTES:
            if values.get(name):
                self.result.references.append((name, values[name]))
        if values.get("srcset"):
            for candidate in values["srcset"].split(","):
                url = candidate.strip().split(maxsplit=1)[0]
                if url:
                    self.result.references.append(("srcset", url))
        if values.get("data-props"):
            try:
                props = json.loads(values["data-props"])
            except (json.JSONDecodeError, TypeError) as exc:
                self.result.errors.append(f"invalid component data-props JSON: {exc}")
            else:
                self._component_references(props)

    def _component_references(self, value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"href", "link", "src", "url"} and isinstance(item, str) and item.strip():
                    self.result.references.append((f"data-props.{key}", item))
                else:
                    self._component_references(item)
        elif isinstance(value, list):
            for item in value:
                self._component_references(item)


@dataclass(frozen=True, order=True)
class LinkProblem:
    source: str
    reference: str
    message: str

    def render(self) -> str:
        return f"{self.source}: {self.reference!r}: {self.message}"


def normalize_base_path(value: str) -> str:
    path = urlsplit(value).path if "://" in value else value
    if not path.startswith("/"):
        path = "/" + path
    path = posixpath.normpath(path)
    if path == ".":
        path = "/"
    if not path.endswith("/"):
        path += "/"
    return path


def parse_html(path: Path) -> ParsedHTML:
    parser = ReferenceParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser.result


def html_url(relative: PurePosixPath, base_path: str) -> str:
    text = relative.as_posix()
    if text == "index.html":
        suffix = ""
    elif text.endswith("/index.html"):
        suffix = text[: -len("index.html")]
    else:
        suffix = text
    return base_path + suffix


def resolve_target(site: Path, base_path: str, path: str) -> Path | None:
    if not path.startswith(base_path):
        return None
    relative_text = unquote(path[len(base_path) :])
    if "\\" in relative_text or "\x00" in relative_text:
        return None
    pure = PurePosixPath(relative_text)
    if any(part in {"..", "."} for part in pure.parts):
        return None
    candidate = site.joinpath(*pure.parts)
    if path.endswith("/"):
        candidate = candidate / "index.html"
    elif candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


class SiteChecker:
    def __init__(self, site: Path, base_url: str | None = None, base_path: str | None = None):
        self.site = site.resolve()
        self.base_host = "proof-relay.invalid"
        if base_url:
            split = urlsplit(base_url)
            if split.scheme not in {"http", "https"} or not split.netloc:
                raise ValueError("--base-url must be an absolute HTTP(S) URL")
            self.base_host = split.netloc.casefold()
            self.base_path = normalize_base_path(split.path)
        elif base_path:
            self.base_path = normalize_base_path(base_path)
        else:
            index = self.site / "index.html"
            if not index.is_file():
                raise ValueError("site root has no index.html; pass --base-path explicitly")
            discovered = parse_html(index).relative_base
            if not discovered:
                raise ValueError("could not discover the base path; pass --base-path explicitly")
            self.base_path = normalize_base_path(discovered)
        self.pages: dict[Path, ParsedHTML] = {}
        self.page_urls: dict[Path, str] = {}
        self.problems: set[LinkProblem] = set()

    def load(self) -> None:
        for path in sorted(self.site.rglob("*.html")):
            if path.is_symlink():
                self.problems.add(LinkProblem(path.relative_to(self.site).as_posix(), "", "built HTML may not be a symlink"))
                continue
            try:
                parsed = parse_html(path)
            except (OSError, UnicodeError) as exc:
                self.problems.add(LinkProblem(path.relative_to(self.site).as_posix(), "", f"cannot parse HTML: {exc}"))
                continue
            resolved = path.resolve()
            self.pages[resolved] = parsed
            self.page_urls[resolved] = html_url(PurePosixPath(path.relative_to(self.site).as_posix()), self.base_path)
            for message in parsed.errors:
                self.problems.add(LinkProblem(path.relative_to(self.site).as_posix(), "data-props", message))

    def check_reference(self, source: Path, attribute: str, raw: str) -> None:
        source_label = source.relative_to(self.site).as_posix()
        value = raw.strip()
        if not value:
            return
        try:
            parsed_raw = urlsplit(value)
        except ValueError as exc:
            self.problems.add(LinkProblem(source_label, raw, f"malformed URL: {exc}"))
            return
        if parsed_raw.scheme.casefold() in IGNORED_SCHEMES:
            return
        if parsed_raw.scheme and parsed_raw.scheme.casefold() not in {"http", "https"}:
            return
        source_url = f"https://{self.base_host}{self.page_urls[source]}"
        try:
            resolved = urlsplit(urljoin(source_url, value))
        except ValueError as exc:
            self.problems.add(LinkProblem(source_label, raw, f"malformed URL: {exc}"))
            return
        target_host = resolved.netloc.casefold()
        if target_host != self.base_host:
            hostname = (resolved.hostname or "").casefold()
            if hostname in LEAKED_HOSTS:
                self.problems.add(LinkProblem(source_label, raw, f"leaks development/placeholder host {hostname!r}"))
            return
        decoded_path = unquote(resolved.path)
        if not decoded_path.startswith(self.base_path):
            self.problems.add(
                LinkProblem(source_label, raw, f"internal URL escapes configured base path {self.base_path!r}")
            )
            return
        target = resolve_target(self.site, self.base_path, resolved.path)
        if target is None:
            self.problems.add(LinkProblem(source_label, raw, "URL path is unsafe or outside the site"))
            return
        try:
            target.relative_to(self.site)
        except ValueError:
            self.problems.add(LinkProblem(source_label, raw, "URL resolves outside the generated site"))
            return
        if not target.is_file():
            self.problems.add(LinkProblem(source_label, raw, "target does not exist in the generated site"))
            return
        fragment = unquote(resolved.fragment)
        if fragment and not fragment.startswith(":~:text=") and target.suffix.casefold() == ".html":
            target_page = self.pages.get(target.resolve())
            if target_page is not None and fragment not in target_page.anchors:
                self.problems.add(LinkProblem(source_label, raw, f"fragment #{fragment} does not exist"))

    def check(self) -> list[LinkProblem]:
        self.load()
        for source, page in self.pages.items():
            for attribute, reference in page.references:
                self.check_reference(source, attribute, reference)
        return sorted(self.problems)


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("public"), help="generated site directory")
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--base-url", help="absolute URL used for the Hugo build")
    location.add_argument("--base-path", help="URL path prefix used for the Hugo build")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    if not args.site.is_dir():
        print(f"Site check failed: directory does not exist: {args.site}", file=sys.stderr)
        return 1
    try:
        problems = SiteChecker(args.site, args.base_url, args.base_path).check()
    except ValueError as exc:
        print(f"Site check failed: {exc}", file=sys.stderr)
        return 1
    if problems:
        for problem in problems[:100]:
            print(problem.render(), file=sys.stderr)
        if len(problems) > 100:
            print(f"... and {len(problems) - 100} more problem(s)", file=sys.stderr)
        print(f"Site check failed with {len(problems)} broken or unsafe reference(s).", file=sys.stderr)
        return 1
    print(f"Checked {len(list(args.site.rglob('*.html')))} HTML page(s); all internal references resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
