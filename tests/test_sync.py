"""scripts/sync.py, the logic the sync and downstream workflows call.

The workflows only run git, gh and this script, so what they decide is
tested here: the matrix they run a leg per consumer from, what the run's
summary tells the owner when the App is not set up, and what a consumer's
reviewer reads in the sync's pull request (the title, the commit message,
the body with the library's log since the commit the copy named before and
the files of the copy that moved). The body's library log sits in a fenced
block, where GitHub neither links "#12" to the consumer's own #12 nor reads
"Closes #12" as closing anything."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ecosystem  # noqa: E402
import sync  # noqa: E402
from ecosystem import vendoring  # noqa: E402

ECO = ecosystem.load()
TEMPLATE = ecosystem.consumer_named(ECO, "OpenSpineConsortium/sdlc-template")
APP = ecosystem.consumer_named(ECO, "OpenSpineConsortium/sdlc-app")
OLD = "1111111111111111111111111111111111111111"
NEW = "2222222222222222222222222222222222222222"
NO_CHANGES = ([], [], [])


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = sync.main(list(argv))
    return code, out.getvalue()


class TheMatrix(unittest.TestCase):
    def test_one_leg_per_consumer_with_what_the_workflows_read(self):
        legs = sync.matrix(ECO)["include"]
        self.assertEqual([leg["repository"] for leg in legs], [c["repository"] for c in ECO["consumers"]])
        for leg in legs:
            self.assertEqual(set(leg), {"repository", "name", "visibility", "vendor_dir", "check", "auto_merge"})
            self.assertEqual(leg["name"], leg["repository"].split("/")[1])

    def test_public_only_leaves_the_private_app_out(self):
        names = [leg["name"] for leg in sync.matrix(ECO, public_only=True)["include"]]
        self.assertNotIn("sdlc-app", names)
        self.assertIn("sdlc-template", names)

    def test_the_command_prints_one_line_of_json_for_github_output(self):
        code, out = run("matrix")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out), sync.matrix(ECO))


class TheOwnersSetup(unittest.TestCase):
    def test_names_the_variable_the_secret_and_auto_merge_for_each_public_consumer(self):
        text = sync.setup_text(ECO)
        self.assertIn("SDLC_APP_ID", text)
        self.assertIn("SDLC_APP_PRIVATE_KEY", text)
        for consumer in ECO["consumers"]:
            line = f"repos/{consumer['repository']} -F allow_auto_merge=true"
            (self.assertIn if consumer["auto_merge"] else self.assertNotIn)(line, text)

    def test_is_ascii(self):
        sync.setup_text(ECO).encode("ascii")


class TitleAndMessage(unittest.TestCase):
    def test_the_title_names_the_version_and_the_short_commit(self):
        self.assertEqual(sync.title("0.2.0", NEW), "Vendor sdlc-protocol 0.2.0 (2222222)")
        self.assertEqual(sync.title("0.2.0", f"{NEW}-dirty"), "Vendor sdlc-protocol 0.2.0 (2222222-dirty)")
        self.assertEqual(sync.title("0.2.0", "unknown"), "Vendor sdlc-protocol 0.2.0 (unknown)")

    def test_the_message_is_the_title_then_why(self):
        lines = sync.message(ECO, TEMPLATE, "0.2.0", NEW).splitlines()
        self.assertEqual(lines[:2], ["Vendor sdlc-protocol 0.2.0 (2222222)", ""])
        self.assertIn("tools/VENDORED", " ".join(lines))
        self.assertTrue(all(len(line) <= 90 for line in lines))


class ReadingTheRecords(unittest.TestCase):
    def test_the_commit_of_the_old_record_and_the_new(self):
        self.assertEqual(sync.recorded_commit(f"sdlc-protocol 0.1.0 {OLD}\n"), OLD)
        self.assertEqual(sync.recorded_commit(f"sdlc-protocol 0.2.0 {OLD}\n{'a' * 64}  guard.py\n"), OLD)
        self.assertEqual(sync.recorded_commit("sdlc-protocol 0.2.0 unknown\n"), "unknown")

    def test_no_record_or_one_that_cannot_be_read(self):
        for text in ("", "garbage\n", "sdlc-protocol 0.1.0\n"):
            with self.subTest(text=text):
                self.assertIsNone(sync.recorded_commit(text))

    def test_the_files_git_status_shows(self):
        porcelain = "\n".join([
            "?? tools/sdlc_protocol/vendoring.py",
            " M tools/sdlc_protocol/cli.py",
            "M  tools/sdlc_protocol/board.py",
            " D tools/sdlc_protocol/old.py",
            "D  tools/sdlc_protocol/older.py",
            "A  tools/sdlc_protocol/adapters/new.py",
            " M tools/VENDORED",
            " M README.md",
        ])
        self.assertEqual(sync.file_changes(porcelain, "tools/sdlc_protocol/"),
                         (["adapters/new.py", "vendoring.py"], ["board.py", "cli.py"], ["old.py", "older.py"]))


class TheBody(unittest.TestCase):
    def body(self, consumer=TEMPLATE, old=OLD, log=("2222222 Make the guard read here-documents",),
             changes=(["vendoring.py"], ["guard.py"], [])):
        return sync.body(ECO, consumer, "0.2.0", NEW, old, None if log is None else list(log), changes)

    def test_says_what_comes_in_where_and_what_it_is_used_for(self):
        text = self.body()
        self.assertIn("sdlc-protocol 0.2.0 at 2222222", text)
        self.assertIn(f"https://github.com/{ECO['library']}/commit/{NEW}", text)
        self.assertIn("`tools/sdlc_protocol`", text)
        self.assertIn(TEMPLATE["uses"], text)

    def test_lists_the_librarys_log_in_a_fenced_block(self):
        text = self.body(log=["2222222 Close the gap (#12)", "1234567 Closes #3"])
        self.assertIn("Since 1111111, the commit the copy named before", text)
        self.assertIn("```text\n2222222 Close the gap (#12)\n1234567 Closes #3\n```", text)

    def test_a_subject_with_a_fence_gets_a_longer_one(self):
        self.assertEqual(sync.fenced(["a ``` b"]), "````text\na ``` b\n````")

    def test_an_old_commit_the_library_does_not_have(self):
        text = self.body(log=None)
        self.assertIn(f"The copy's record named {OLD}, which is not a commit of", text)
        self.assertNotIn("```", text)

    def test_no_old_record(self):
        self.assertIn("had no record of the commit its copy was made from", self.body(old=None, log=None))

    def test_no_commit_since(self):
        self.assertIn("no commit changed sdlc_protocol/", self.body(log=[]))

    def test_lists_the_files_added_changed_and_removed(self):
        text = self.body(changes=(["vendoring.py"], ["cli.py", "guard.py"], []))
        self.assertIn("- Added: `vendoring.py`\n- Changed: `cli.py`, `guard.py`\n- Removed: none", text)

    def test_says_the_consumers_own_check_decides(self):
        text = self.body()
        self.assertIn("`bash scripts/check.sh`", text)
        self.assertIn("`vendoring.py verify`", text)
        self.assertIn("`tools/VENDORED`", text)

    def test_auto_merge_on_a_public_consumer_and_the_owner_on_the_private_app(self):
        self.assertIn("Auto-merge", self.body(TEMPLATE))
        self.assertIn("Its owner merges it", self.body(APP))
        self.assertNotIn("Auto-merge,", self.body(APP))

    def test_is_ascii(self):
        self.body().encode("ascii")


class TheSummary(unittest.TestCase):
    def rows(self, *args):
        return sync.summary(*args).splitlines()

    def test_each_leg_writes_a_table_of_its_own_row(self):
        lines = self.rows("OpenSpineConsortium/coordbench", "up to date")
        self.assertEqual(lines[:2], ["| Consumer | Pull request | Auto-merge |", "| --- | --- | --- |"])
        self.assertEqual(lines[2], "| OpenSpineConsortium/coordbench | up to date | - |")

    def test_a_pull_request_with_auto_merge_on_or_not_allowed(self):
        on = self.rows("o/a", "opened", "https://github.com/o/a/pull/1", "on")[2]
        self.assertEqual(on, "| o/a | opened: https://github.com/o/a/pull/1 | on: it merges once the required "
                             "check is green |")
        refused = self.rows("o/a", "updated", "https://github.com/o/a/pull/1", "unavailable")[2]
        self.assertIn("allow auto-merge in the repository's settings", refused)

    def test_a_private_consumers_pull_request_waits_for_its_owner(self):
        self.assertIn("off: the owner merges", self.rows("o/app", "opened", "https://github.com/o/app/pull/2")[2])

    def test_the_command_finds_the_consumer_whatever_the_case(self):
        code, out = run("summary", "--consumer", "openspineconsortium/SDLC-APP", "--state", "up to date")
        self.assertEqual(code, 0)
        self.assertIn("| OpenSpineConsortium/sdlc-app | up to date | - |", out)


# --- the body from real repositories ----------------------------------------------------

def quiet_environment():
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(folder, *args):
    done = subprocess.run(["git", "-c", "user.name=sync test", "-c", "user.email=sync@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                          cwd=folder, capture_output=True, text=True, env=quiet_environment(), timeout=60)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout.strip()


def commit(folder, message):
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", message)
    return git(folder, "rev-parse", "HEAD")


class TheBodyFromGit(unittest.TestCase):
    """A library with two commits to the package and a consumer whose last
    commit holds a copy of the first, with the second vendored on top, as
    the sync's checkout of it stands when the body is written."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.library, self.target = self.scratch / "library", self.scratch / "consumer"
        package = self.library / "sdlc_protocol"
        package.mkdir(parents=True)
        (package / "guard.py").write_text("first\n", encoding="ascii")
        git(self.library, "init", "-q")
        self.first = commit(self.library, "The first guard")
        (package / "guard.py").write_text("second\n", encoding="ascii")
        (package / "vendoring.py").write_text("record\n", encoding="ascii")
        self.second = commit(self.library, "The second guard (#12)")
        self.copy = self.target / "tools" / "sdlc_protocol"
        self.write_copy({"guard.py": "first\n", "old.py": "gone soon\n"}, self.first)
        git(self.target, "init", "-q")
        commit(self.target, "The copy at the first commit")
        shutil.rmtree(self.copy)
        self.write_copy({"guard.py": "second\n", "vendoring.py": "record\n"}, self.second)

    def write_copy(self, files, at):
        self.copy.mkdir(parents=True)
        for name, text in files.items():
            (self.copy / name).write_text(text, encoding="ascii")
        record = vendoring.record_text("0.2.0", at, vendoring.digests_of(self.copy))
        (self.target / "tools" / "VENDORED").write_text(record, encoding="ascii")

    def test_the_log_since_the_old_commit_and_the_files_that_moved(self):
        text = sync.body_for(ECO, TEMPLATE, self.target, self.library)
        self.assertIn(f"Since {self.first[:7]}", text)
        self.assertIn(f"```text\n{self.second[:7]} The second guard (#12)\n```", text)
        self.assertIn("- Added: `vendoring.py`\n- Changed: `guard.py`\n- Removed: `old.py`", text)

    def test_title_and_message_read_the_new_record(self):
        self.assertEqual(sync.version_and_commit(self.target, TEMPLATE), ("0.2.0", self.second))

    def test_a_consumer_with_no_record_yet(self):
        fresh = self.scratch / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q")
        (fresh / "README.md").write_text("a consumer\n", encoding="ascii")
        commit(fresh, "start")
        shutil.copytree(self.target / "tools", fresh / "tools")
        text = sync.body_for(ECO, TEMPLATE, fresh, self.library)
        self.assertIn("had no record of the commit", text)
        self.assertIn("- Added: `guard.py`, `vendoring.py`", text)


if __name__ == "__main__":
    unittest.main()
