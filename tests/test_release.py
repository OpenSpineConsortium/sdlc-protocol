"""scripts/release.py, which tells .github/workflows/release.yml what to tag.

A release is the version sdlc_protocol/__init__.py states, tagged at the
first commit of main's first-parent line that carries it: the merge of the
pull request that raised it, never a commit on that pull request's branch,
which main's own line does not pass through. Every version back to the
newest one already tagged is listed, oldest first, so a release a run
missed is made by the next; a version whose tag exists is never listed
again, wherever its tag points, so no tag is moved; and the walk stops at
a commit from before the version file. The workflow's own shape is
tests/test_scaffold.py's."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402


def quiet_environment():
    """This environment without git's own variables, which a hook or an
    outer git might have set and which would point git elsewhere."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


class History(unittest.TestCase):
    """A library's history in a git repository of its own, removed after
    each test."""

    def setUp(self):
        environment = mock.patch.dict(os.environ, quiet_environment(), clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.git("init", "-q")

    def git(self, *args):
        done = subprocess.run(["git", "-c", "user.name=release test", "-c", "user.email=release@example.invalid",
                               "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                               "-c", "init.defaultBranch=main", *args],
                              cwd=self.folder, capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
        return done.stdout.strip()

    def commit(self, message, version=None):
        """A commit that raises the version file to version, or that adds
        a file of its own when given none, so two branches never meet in
        one file; its full hash."""
        if version is None:
            (self.folder / f"{message.replace(' ', '-')}.md").write_text(message + "\n", encoding="ascii")
        else:
            self.write_version(f'__version__ = "{version}"')
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def write_version(self, line):
        path = self.folder / release.VERSION_FILE
        path.parent.mkdir(exist_ok=True)
        path.write_text(f'"""The package."""\n\n{line}\n', encoding="ascii")

    def missing(self):
        return release.missing(self.folder)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(release, "ROOT", self.folder), redirect_stdout(out), redirect_stderr(err):
            code = release.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class WhatIsTagged(History):
    def test_a_new_version_is_tagged_at_the_first_commit_that_carries_it(self):
        self.commit("first", version="0.1.0")
        self.git("tag", "v0.1.0")
        raised = self.commit("raise", version="0.2.0")
        self.commit("later")
        self.assertEqual(self.missing(), [("v0.2.0", raised)])

    def test_nothing_when_the_version_main_carries_has_its_tag(self):
        self.commit("first", version="0.1.0")
        self.git("tag", "v0.1.0")
        self.commit("later")
        self.assertEqual(self.missing(), [])

    def test_versions_a_run_missed_are_all_listed_oldest_first(self):
        self.commit("first", version="0.1.0")
        self.git("tag", "v0.1.0")
        second = self.commit("second", version="0.2.0")
        third = self.commit("third", version="0.3.0")
        self.commit("later")
        self.assertEqual(self.missing(), [("v0.2.0", second), ("v0.3.0", third)])

    def test_a_tag_that_exists_is_never_moved_wherever_it_points(self):
        self.commit("first", version="0.1.0")
        self.commit("raise", version="0.2.0")
        later = self.commit("later")
        self.git("tag", "v0.2.0", later)
        self.assertEqual(self.missing(), [])

    def test_a_version_raised_on_a_branch_is_tagged_at_its_merge(self):
        """The pull request's own commits carry the version first, but
        main's first-parent line passes only through the merge."""
        self.commit("first", version="0.1.0")
        self.git("tag", "v0.1.0")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("raise", version="0.2.0")
        self.commit("more")
        self.git("checkout", "-q", "main")
        self.commit("main moved")
        self.git("merge", "-q", "--no-ff", "-m", "Merge pull request #2", "feature")
        self.assertEqual(self.missing(), [("v0.2.0", self.git("rev-parse", "HEAD"))])

    def test_a_version_main_returns_to_keeps_its_oldest_commit_and_its_place(self):
        self.commit("first", version="0.1.0")
        self.git("tag", "v0.1.0")
        raised = self.commit("raise", version="0.2.0")
        again = self.commit("raise again", version="0.3.0")
        self.commit("revert", version="0.2.0")
        self.assertEqual(self.missing(), [("v0.2.0", raised), ("v0.3.0", again)])

    def test_the_walk_stops_at_a_commit_from_before_the_version_file(self):
        self.commit("before the package")
        first = self.commit("first", version="0.1.0")
        self.assertEqual(self.missing(), [("v0.1.0", first)])

    def test_the_walk_stops_at_a_version_file_that_states_no_version(self):
        self.write_version('__version__ = "0.1"')
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a version of two numbers")
        first = self.commit("first", version="0.1.0")
        self.assertEqual(self.missing(), [("v0.1.0", first)])

    def test_a_tag_without_the_prefix_does_not_count(self):
        first = self.commit("first", version="0.1.0")
        self.git("tag", "0.1.0")
        self.assertEqual(self.missing(), [("v0.1.0", first)])


class ReadingTheVersion(unittest.TestCase):
    def test_three_numbers_in_double_quotes(self):
        self.assertEqual(release.version_in('"""Doc."""\n\n__version__ = "0.3.1"\n'), "0.3.1")

    def test_anything_else_is_no_version(self):
        for text in ("", None, '__version__ = "0.3"\n', "__version__ = '0.3.1'\n", '__version__ = "0.3.1rc1"\n',
                     '# __version__ = "0.3.1"\n'):
            with self.subTest(text=text):
                self.assertIsNone(release.version_in(text))

    def test_the_library_states_one(self):
        self.assertIsNotNone(release.current(ROOT))


class TheCommandLine(History):
    def test_missing_prints_a_tag_and_a_commit_per_line(self):
        self.commit("first", version="0.1.0")
        self.git("tag", "v0.1.0")
        second = self.commit("second", version="0.2.0")
        third = self.commit("third", version="0.3.0")
        self.assertEqual(self.run_main("missing"), (0, f"v0.2.0 {second}\nv0.3.0 {third}\n", ""))

    def test_missing_prints_nothing_when_every_version_is_tagged(self):
        self.commit("first", version="0.1.0")
        self.git("tag", "v0.1.0")
        self.assertEqual(self.run_main("missing"), (0, "", ""))

    def test_version_prints_the_version_the_working_tree_carries(self):
        self.commit("first", version="0.1.0")
        self.write_version('__version__ = "0.2.0"')
        self.assertEqual(self.run_main("version"), (0, "0.2.0\n", ""))

    def test_version_fails_and_says_why_when_the_file_states_none(self):
        self.write_version('__version__ = "0.2"')
        code, out, err = self.run_main("version")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("states no version of three numbers", err)


if __name__ == "__main__":
    unittest.main()
