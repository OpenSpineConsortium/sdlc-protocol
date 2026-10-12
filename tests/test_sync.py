"""scripts/sync.py, the logic the sync and downstream workflows call.

The workflows only run git, gh and this script, so what they decide is
tested here: the matrix they run a leg per consumer from, what the run's
summary tells the owner when the App is not set up or the run was started
off main, what a consumer's reviewer reads in the sync's pull request (the
title, the commit message, the body with the library's log of the package
since the commit the copy named before, the files of the copy that moved
and, for the plugin marketplace, the release the raised version in its
plugin.json makes), the comment that closes a stale one, and each
consumer's row in the summary, which gives GitHub's own reason when it
refuses to arm auto-merge. The body's library log sits in a fenced
block, where GitHub neither links "#12" to the consumer's own #12 nor reads
"Closes #12" as closing anything."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ecosystem  # noqa: E402
import sync  # noqa: E402
from ecosystem import vendoring  # noqa: E402

ECO = ecosystem.load()
TEMPLATE = ecosystem.consumer_named(ECO, "OpenSpineConsortium/symphony-template")
APP = ecosystem.consumer_named(ECO, "OpenSpineConsortium/symphony-app")
MARKETPLACE = ecosystem.consumer_named(ECO, "OpenSpineConsortium/symphony-marketplace")
MANIFEST = MARKETPLACE["version_file"]
COORDBENCH = ecosystem.consumer_named(ECO, "OpenSpineConsortium/coordbench")
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

    def test_public_only_leaves_out_what_is_not_public(self):
        names = [leg["name"] for leg in sync.matrix(ECO, public_only=True)["include"]]
        self.assertNotIn("symphony-app", names)
        self.assertNotIn("symphony-template", names)
        self.assertIn("coordbench", names)

    def test_the_command_prints_one_line_of_json_for_github_output(self):
        code, out = run("matrix")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out), sync.matrix(ECO))


class TheOwnersSetup(unittest.TestCase):
    def test_names_the_variable_the_secret_and_auto_merge_for_each_consumer_that_takes_it(self):
        text = sync.setup_text(ECO)
        self.assertIn("SDLC_APP_ID", text)
        self.assertIn("SDLC_APP_PRIVATE_KEY", text)
        for consumer in ECO["consumers"]:
            line = f"repos/{consumer['repository']} -F allow_auto_merge=true"
            (self.assertIn if consumer["auto_merge"] else self.assertNotIn)(line, text)

    def test_the_key_goes_through_register_pys_pipe_as_the_template_checklist_says(self):
        """register.py keeps the key only inside .sdlc-app.env, so there is
        no key file to redirect from; --print-key turns it back into PEM."""
        text = sync.setup_text(ECO)
        self.assertIn("from a clone of OpenSpineConsortium/symphony-app", text)
        self.assertIn("python3 scripts/register.py --print-key | gh secret set SDLC_APP_PRIVATE_KEY --repo "
                      f"{ECO['library']}", text)
        self.assertNotIn("key file>", text)

    def test_the_readme_gives_the_same_pipe(self):
        readme = (ROOT / "README.md").read_text(encoding="ascii")
        self.assertIn("python3 scripts/register.py --print-key | gh secret set SDLC_APP_PRIVATE_KEY --repo "
                      f"{ECO['library']}", readme)

    def test_is_ascii(self):
        sync.setup_text(ECO).encode("ascii")


class OffMain(unittest.TestCase):
    def test_says_the_sync_vendors_only_main_and_names_the_ref(self):
        text = sync.off_main_text("refs/heads/claude/7")
        self.assertIn("vendors only the library's main", text)
        self.assertIn("`refs/heads/claude/7`", text)
        self.assertIn("changed nothing in any consumer", text)

    def test_the_command_prints_it(self):
        code, out = run("off-main", "--ref", "refs/heads/claude/7")
        self.assertEqual(code, 0)
        self.assertEqual(out, sync.off_main_text("refs/heads/claude/7"))


class TitleAndMessage(unittest.TestCase):
    def test_the_title_names_the_version_and_the_short_commit(self):
        self.assertEqual(sync.title("0.2.0", NEW), "Vendor symphony-protocol 0.2.0 (2222222)")
        self.assertEqual(sync.title("0.2.0", f"{NEW}-dirty"), "Vendor symphony-protocol 0.2.0 (2222222-dirty)")
        self.assertEqual(sync.title("0.2.0", "unknown"), "Vendor symphony-protocol 0.2.0 (unknown)")

    def test_the_message_is_the_title_then_why(self):
        lines = sync.message(ECO, TEMPLATE, "0.2.0", NEW).splitlines()
        self.assertEqual(lines[:2], ["Vendor symphony-protocol 0.2.0 (2222222)", ""])
        self.assertIn("tools/VENDORED", " ".join(lines))
        self.assertTrue(all(len(line) <= 90 for line in lines))

    def test_the_message_names_the_release_it_makes(self):
        text = sync.message(ECO, MARKETPLACE, "0.2.0", NEW, ("0.1.0", "0.1.1"))
        self.assertIn(f"It raises the version in {MANIFEST} from 0.1.0\nto 0.1.1:", text)
        self.assertTrue(all(len(line) <= 90 for line in text.splitlines()))

    def test_no_release_no_word_of_one(self):
        for consumer, release in ((MARKETPLACE, ("0.1.1", "0.1.1")), (TEMPLATE, None)):
            with self.subTest(consumer=consumer["repository"]):
                self.assertNotIn("It raises", sync.message(ECO, consumer, "0.2.0", NEW, release))


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
        self.assertIn("symphony-protocol 0.2.0 at 2222222", text)
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

    def test_a_raised_version_is_the_release_this_pull_request_makes(self):
        text = sync.body(ECO, MARKETPLACE, "0.2.0", NEW, OLD, [], NO_CHANGES, ("0.1.0", "0.1.1"))
        self.assertIn(f"## The release\n\n`{MANIFEST}` goes from 0.1.0 to 0.1.1 in this same pull request", text)
        self.assertLess(text.index("## The release"), text.index("## How it lands"))

    def test_an_unchanged_version_says_there_is_nothing_new_to_take(self):
        text = sync.body(ECO, MARKETPLACE, "0.2.0", NEW, OLD, [], NO_CHANGES, ("0.1.1", "0.1.1"))
        self.assertIn(f"`{MANIFEST}` stays at 0.1.1", text)

    def test_a_consumer_with_no_version_file_has_no_release_section(self):
        self.assertNotIn("## The release", self.body())

    def test_auto_merge_where_ecosystem_json_gives_it_and_the_owner_on_a_private_repository_without(self):
        self.assertIn("Auto-merge", self.body(TEMPLATE))
        self.assertIn("Auto-merge, when this repository allows it", self.body(APP))
        private = self.body(dict(APP, auto_merge=False))
        self.assertIn("Its owner merges it: GitHub Free offers auto-merge only on public", private)
        self.assertNotIn("Auto-merge,", private)

    def test_a_public_consumer_without_auto_merge_is_not_called_private(self):
        text = self.body(dict(COORDBENCH, auto_merge=False))
        self.assertIn("ecosystem.json gives this repository no auto-merge", text)
        self.assertNotIn("GitHub Free", text)

    def test_is_ascii(self):
        self.body().encode("ascii")


class TheClosingComment(unittest.TestCase):
    def test_says_main_already_holds_the_copy(self):
        text = sync.closing_comment(ECO)
        self.assertIn(f"Closed by {ECO['library']}'s sync workflow", text)
        self.assertIn("main already holds the copy", text)

    def test_the_command_prints_it(self):
        code, out = run("closing", "--consumer", "OpenSpineConsortium/coordbench")
        self.assertEqual(code, 0)
        self.assertEqual(out, sync.closing_comment(ECO))


class TheSummary(unittest.TestCase):
    PULL = "https://github.com/o/a/pull/1"

    def row(self, *args, **kwargs):
        return sync.summary(*args, **kwargs).splitlines()[2]

    def test_each_leg_writes_a_table_of_its_own_row(self):
        lines = sync.summary("OpenSpineConsortium/coordbench", "up to date").splitlines()
        self.assertEqual(lines[:2], ["| Consumer | Pull request | Auto-merge |", "| --- | --- | --- |"])
        self.assertEqual(lines[2], "| OpenSpineConsortium/coordbench | up to date | - |")

    def test_every_state_the_workflow_reports(self):
        self.assertEqual(set(sync.PULL_STATES), {"up to date", "closed", "opened", "updated", "unchanged"})
        self.assertEqual(self.row("o/a", "closed", self.PULL), f"| o/a | up to date; closed the stale {self.PULL} | - |")
        self.assertIn(f"opened: {self.PULL}", self.row("o/a", "opened", self.PULL, "on"))
        self.assertIn(f"updated: {self.PULL}", self.row("o/a", "updated", self.PULL, "on"))
        self.assertIn(f"already this copy: {self.PULL}", self.row("o/a", "unchanged", self.PULL, "on"))

    def test_auto_merge_armed(self):
        self.assertEqual(self.row("o/a", "opened", self.PULL, "on"),
                         f"| o/a | opened: {self.PULL} | on: it merges once the required check is green |")

    def test_auto_merge_refused_gives_githubs_own_reason(self):
        said = "GraphQL: Pull request is in clean status (enablePullRequestAutoMerge)"
        row = self.row("o/a", "updated", self.PULL, "refused", reason=f"{said}\n")
        self.assertIn(f'not armed: GitHub said "{said}"', row)
        self.assertIn("a check its main requires", row)
        self.assertIn("GitHub gave no reason", self.row("o/a", "updated", self.PULL, "refused"))

    def test_the_reason_stays_inside_its_cell(self):
        row = self.row("o/a", "opened", self.PULL, "refused", reason="a | b\n\n  c" + " d" * 200)
        self.assertEqual(len(re.findall(r"(?<!\\)\|", row)), 4)
        self.assertIn("a \\| b c d", row)
        self.assertLessEqual(len(sync.one_line("x" * 500)), sync.REASON_LENGTH)
        self.assertTrue(sync.one_line("x" * 500).endswith("..."))

    def test_a_private_consumers_pull_request_waits_for_its_owner(self):
        self.assertIn("off: the owner merges", self.row("o/app", "opened", "https://github.com/o/app/pull/2"))

    def test_an_unknown_state_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as caught, redirect_stderr(io.StringIO()):
            run("summary", "--consumer", "OpenSpineConsortium/coordbench", "--state", "merged")
        self.assertEqual(caught.exception.code, 2)

    def test_the_command_passes_the_reason_on(self):
        code, out = run("summary", "--consumer", "OpenSpineConsortium/coordbench", "--state", "opened", "--url",
                        self.PULL, "--auto-merge", "refused", "--reason", "Auto merge is not allowed")
        self.assertEqual(code, 0)
        self.assertIn('GitHub said "Auto merge is not allowed"', out)

    def test_the_command_finds_the_consumer_whatever_the_case(self):
        code, out = run("summary", "--consumer", "openspineconsortium/SYMPHONY-APP", "--state", "up to date")
        self.assertEqual(code, 0)
        self.assertIn("| OpenSpineConsortium/symphony-app | up to date | - |", out)


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
    """A library with two commits to the package, and one between them
    that changes only its README, and a consumer whose last commit holds a
    copy of the first, with the second vendored on top, as the sync's
    checkout of it stands when the body is written. The README's commit
    brings the consumer nothing, so the body's log leaves it out."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.library, self.target = self.scratch / "library", self.scratch / "consumer"
        package = self.library / "sdlc_protocol"
        package.mkdir(parents=True)
        (package / "guard.py").write_text("first\n", encoding="ascii")
        git(self.library, "init", "-q")
        self.first = commit(self.library, "The first guard")
        (self.library / "README.md").write_text("the library\n", encoding="ascii")
        commit(self.library, "Say what the library is")
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
        self.assertNotIn("Say what the library is", text)
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


class TheReleaseFromGit(unittest.TestCase):
    """The marketplace's checkout as the sync leaves it: its last commit
    holds the plugin at 0.1.0, and scripts/vendor.py raised it to 0.1.1
    beside a new copy of the guard."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.target = self.scratch / "marketplace"
        self.write("guard.py", "first\n", version="0.1.0")
        git(self.target, "init", "-q")
        commit(self.target, "The plugin at 0.1.0")
        self.write("guard.py", "second\n", version="0.1.1")

    def write(self, name, text, version):
        copy = self.target / MARKETPLACE["vendor_dir"] / "sdlc_protocol"
        copy.mkdir(parents=True, exist_ok=True)
        (copy / name).write_text(text, encoding="ascii")
        record = vendoring.record_text("0.2.0", NEW, vendoring.digests_of(copy))
        (copy.parent / "VENDORED").write_text(record, encoding="ascii")
        (self.target / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
        (self.target / MANIFEST).write_text(json.dumps({"name": "sdlc", "version": version}) + "\n",
                                            encoding="ascii")

    def test_reads_the_version_before_and_after(self):
        self.assertEqual(sync.release_of(self.target, MARKETPLACE), ("0.1.0", "0.1.1"))
        self.assertIsNone(sync.release_of(self.target, TEMPLATE))

    def test_the_body_and_the_message_name_the_release(self):
        self.assertIn(f"`{MANIFEST}` goes from 0.1.0 to 0.1.1", sync.body_for(ECO, MARKETPLACE, self.target))
        code, out = run("message", str(self.target), "--consumer", MARKETPLACE["repository"])
        self.assertEqual(code, 0)
        self.assertIn(f"It raises the version in {MANIFEST} from 0.1.0\nto 0.1.1:", out)


if __name__ == "__main__":
    unittest.main()
