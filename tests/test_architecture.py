"""ECOSYSTEM.md and scripts/architecture.py, which writes it.

The page is the map a newcomer reads first, so it must say what the code
and ecosystem.json say today: it is held to what the script would write
now, and the script is held to drawing every repository, every module
with its one-line summary and every import the package makes. Its
--check form is what scripts/check.sh runs, so its exit code and its
message are tested too."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import architecture  # noqa: E402
import ecosystem  # noqa: E402

ECO = ecosystem.load()
PAGE = ROOT / "ECOSYSTEM.md"


def run(*argv, page=PAGE):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(architecture, "PAGE", page), redirect_stdout(out), redirect_stderr(err):
        code = architecture.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ThePage(unittest.TestCase):
    def setUp(self):
        self.text = architecture.page(ECO)

    def test_ecosystem_md_is_up_to_date(self):
        self.assertEqual(PAGE.read_text(encoding="ascii"), self.text,
                         "ECOSYSTEM.md is out of date; run python3 scripts/architecture.py")

    def test_is_ascii(self):
        self.text.encode("ascii")

    def test_names_every_repository_with_what_it_is(self):
        self.assertIn(ECO["about"], self.text)
        for consumer in ECO["consumers"]:
            with self.subTest(consumer=consumer["repository"]):
                self.assertIn(f"**{consumer['repository']}** ({consumer['visibility']}): {consumer['about']}",
                              self.text)
                self.assertIn(f"`{consumer['vendor_dir']}/sdlc_protocol`", self.text)
                self.assertIn(consumer["uses"], self.text)

    def test_names_every_module_with_its_summary(self):
        for name in ecosystem.module_files():
            with self.subTest(module=name):
                module = ecosystem.module_name(name)
                self.assertIn(f"    {architecture.node_id(module)}[\"{module}\"]", self.text)
                self.assertIn(f"- `{module}`: {architecture.code_spans(architecture.summary_of(name))}", self.text)

    def test_draws_every_import(self):
        for name in ecosystem.module_files():
            for needed in ecosystem.imports_of(name):
                edge = (f"  {architecture.node_id(ecosystem.module_name(name))} --> "
                        f"{architecture.node_id(ecosystem.module_name(needed))}")
                self.assertIn(edge, self.text)

    def test_draws_what_each_consumer_takes(self):
        self.assertIn("  r_symphony_template -. all .-> library", self.text)
        self.assertIn("  r_symphony_marketplace -.-> m_guard", self.text)
        self.assertIn("  r_symphony_app -.-> m_claims", self.text)
        self.assertIn("  r_symphony_app -.-> m_protocol_check", self.text)

    def test_names_each_version_file_and_the_release_it_makes(self):
        """The plugin's release is part of the sync, and the map says so."""
        for consumer in ECO["consumers"]:
            if "version_file" in consumer:
                with self.subTest(consumer=consumer["repository"]):
                    self.assertIn(f"- {ecosystem.short_name(consumer['repository'])}: `{consumer['version_file']}`",
                                  self.text)
        self.assertIn("the same commit raises its patch version whenever the copy's files change", self.text)
        self.assertIn("6. Where the consumer names a version file, that merge is also its release", self.text)

    def test_says_how_a_change_travels(self):
        self.assertIn("## How a change travels", self.text)
        for words in ("downstream (<consumer>)", "symphony-protocol/sync", "auto-merge", "vendoring.py verify"):
            self.assertIn(words, self.text)

    def test_says_a_change_reaches_the_consumers_only_by_a_release_the_owner_approves(self):
        """The sync no longer carries main's head: the map draws the release
        between the merge and the consumers' pull requests, and says so."""
        self.assertIn("  merge --> release[\"a release, once the owner approves it\"]", self.text)
        self.assertIn("once the owner approves the run in the environment `release`, and not before", self.text)
        self.assertIn("carries the newest published release, never main's head", self.text)
        self.assertNotIn("vendors the new main", self.text)

    def test_a_consumer_that_goes_first_takes_the_raised_version_and_is_the_one_exception(self):
        """A raise made after the consumer vendored the branch moves the
        release past its copy, so the pull request raises the version from
        the start; and the consumer's copy has the change before the
        release, which the page says rather than promising otherwise."""
        self.assertIn("this pull request raises the version from the start", self.text)
        self.assertIn("the one way a change reaches a consumer before the owner's approval", self.text)
        self.assertNotIn("this pull request merges, raising the version", self.text)


class Words(unittest.TestCase):
    def test_a_summary_is_the_docstrings_first_sentence_on_one_line(self):
        self.assertEqual(architecture.summary_of("guard.py"),
                         "Refuse a git push the board rules forbid, before it runs.")
        self.assertEqual(architecture.summary_of("protocol_check.py"),
                         "Hold a claude/<issue> pull request to the claim protocol.")

    def test_a_placeholder_word_goes_in_a_code_span_so_github_shows_it(self):
        self.assertEqual(architecture.code_spans("Hold a claude/<issue> pull request."),
                         "Hold a `claude/<issue>` pull request.")
        self.assertEqual(architecture.code_spans("python3 -m x <a|b>."), "python3 -m x `<a|b>`.")

    def test_a_pipe_in_a_table_cell_is_escaped(self):
        self.assertEqual(architecture.cell("a | b"), "a \\| b")


class TheCommand(unittest.TestCase):
    def test_check_passes_on_the_page_as_it_is(self):
        self.assertEqual(run("--check")[0], 0)

    def test_check_fails_on_a_stale_page_and_says_how_to_regenerate_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            stale = Path(scratch) / "ECOSYSTEM.md"
            stale.write_text("an old map\n", encoding="ascii")
            code, _, err = run("--check", page=stale)
            self.assertEqual(code, 1)
            self.assertIn("run python3 scripts/architecture.py", err)
            self.assertEqual(stale.read_text(encoding="ascii"), "an old map\n")

    def test_without_check_it_writes_the_page_and_then_leaves_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            page = Path(scratch) / "ECOSYSTEM.md"
            self.assertEqual(run(page=page)[:2], (0, "ECOSYSTEM.md: rewritten\n"))
            self.assertEqual(page.read_text(encoding="ascii"), architecture.page(ECO))
            self.assertEqual(run(page=page)[:2], (0, "ECOSYSTEM.md: unchanged\n"))

    def test_check_sh_runs_the_check(self):
        self.assertIn("python3 scripts/architecture.py --check", (ROOT / "scripts" / "check.sh").read_text())


if __name__ == "__main__":
    unittest.main()
