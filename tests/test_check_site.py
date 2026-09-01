from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.check_site import SiteChecker


class SiteCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.site = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str) -> None:
        path = self.site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def problems(self, base_url: str = "https://example.invalid/proofhub/") -> str:
        return "\n".join(problem.render() for problem in SiteChecker(self.site, base_url=base_url).check())

    def test_relative_links_work_under_repository_subpath(self) -> None:
        self.write(
            "index.html",
            '<html data-wc-relurl="/proofhub/"><body><a href="projects/">Projects</a>'
            '<img src="media/logo.png"></body></html>',
        )
        self.write("projects/index.html", '<html><body><h1 id="catalog">Projects</h1><a href="#catalog">Top</a></body></html>')
        self.write("media/logo.png", "not really an image")
        self.assertEqual("", self.problems())

    def test_root_relative_link_outside_base_is_rejected(self) -> None:
        self.write("index.html", '<html><body><a href="/projects/">Projects</a></body></html>')
        self.write("projects/index.html", "<html><body>Projects</body></html>")
        self.assertIn("escapes configured base path", self.problems())

    def test_component_button_urls_are_checked(self) -> None:
        self.write(
            "index.html",
            '<html><body><div data-preact-render="true" '
            'data-props=\'{"content":{"primary_action":{"url":"/projects/"}}}\'></div></body></html>',
        )
        self.write("projects/index.html", "<html><body>Projects</body></html>")
        self.assertIn("escapes configured base path", self.problems())

    def test_missing_target_and_fragment_are_rejected(self) -> None:
        self.write(
            "index.html",
            '<html><body><a href="missing/">Missing</a><a href="projects/#absent">Fragment</a></body></html>',
        )
        self.write("projects/index.html", '<html><body><h1 id="present">Projects</h1></body></html>')
        rendered = self.problems()
        self.assertIn("target does not exist", rendered)
        self.assertIn("fragment #absent does not exist", rendered)

    def test_development_host_leak_is_rejected_but_external_links_are_ignored(self) -> None:
        self.write(
            "index.html",
            '<html><body><a href="http://localhost:1313/proofhub/">Bad</a>'
            '<a href="https://www.example.org/">External</a></body></html>',
        )
        rendered = self.problems()
        self.assertIn("leaks development/placeholder host", rendered)
        self.assertNotIn("www.example.org", rendered)

    def test_base_path_can_be_discovered_from_built_html(self) -> None:
        self.write("index.html", '<html data-wc-relurl="/proofhub/"><body><a href="guide/">Guide</a></body></html>')
        self.write("guide/index.html", "<html><body>Guide</body></html>")
        self.assertEqual([], SiteChecker(self.site).check())


if __name__ == "__main__":
    unittest.main()
