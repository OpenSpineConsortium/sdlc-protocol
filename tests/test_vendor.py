"""scripts/vendor.py and its wrapper scripts/vendor.sh, run as people and
the sync workflow run them, on temporary git repositories: one a copy of
this library, the other a consumer.

What a consumer trusts is the copy and its record, so these tests hold
both: the whole package or exactly the modules ecosystem.json names, the
consumer found by its remote, the record's commit by the rule that keeps
the sync from churning (the last commit that changed sdlc_protocol/, which
a merge commit on main does not move), "-dirty" and "unknown" where the
copy is no commit, and a second run that changes nothing. A copy whose
modules could not import each other, and a copy into this repository
itself, are refused before anything is written."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import sdlc_protocol  # noqa: E402
import vendor  # noqa: E402
from sdlc_protocol import vendoring  # noqa: E402

PACKAGE = ROOT / "sdlc_protocol"
TEMPLATE = "OpenSpineConsortium/sdlc-template"
MARKETPLACE = "OpenSpineConsortium/sdlc-marketplace"
APP = "OpenSpineConsortium/sdlc-app"


def quiet_environment():
    """This environment without git's own variables, which a hook or an
    outer git might have set and which would point git elsewhere."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(folder, *args):
    done = subprocess.run(["git", "-c", "user.name=vendor test", "-c", "user.email=vendor@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                          cwd=folder, capture_output=True, text=True, env=quiet_environment(), timeout=60)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout.strip()


def commit_all(folder, message):
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", message)
    return git(folder, "rev-parse", "HEAD")


def copy_library(destination):
    """This library's package, scripts and ecosystem.json, without caches."""
    shutil.copytree(PACKAGE, destination / "sdlc_protocol", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (destination / "scripts").mkdir()
    for name in ("vendor.py", "vendor.sh", "ecosystem.py"):
        shutil.copy2(ROOT / "scripts" / name, destination / "scripts" / name)
    shutil.copy2(ROOT / "ecosystem.json", destination / "ecosystem.json")
    (destination / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="ascii")
    (destination / "README.md").write_text("the library\n", encoding="ascii")


class Repositories(unittest.TestCase):
    """A library that is a git repository of its own and a consumer that
    is another, both removed after each test."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.library = self.scratch / "sdlc-protocol"
        self.library.mkdir()
        copy_library(self.library)
        git(self.library, "init", "-q")
        self.package_commit = commit_all(self.library, "the library")

    def consumer(self, remote=None, name="consumer"):
        folder = self.scratch / name
        folder.mkdir()
        git(folder, "init", "-q")
        if remote:
            git(folder, "remote", "add", "origin", remote)
        return folder

    def vendor(self, target, *args, script="vendor.py"):
        runner = [sys.executable, "-B"] if script.endswith(".py") else ["bash"]
        return subprocess.run([*runner, str(self.library / "scripts" / script), str(target), *args],
                              capture_output=True, text=True, timeout=60, env=quiet_environment())

    def vendored(self, target, *args):
        done = self.vendor(target, *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def record_of(self, vendor_dir):
        return vendoring.parse_record((vendor_dir / "VENDORED").read_text(encoding="ascii"))


class WholeAndPartialCopies(Repositories):
    def test_the_template_takes_the_whole_package_byte_for_byte(self):
        target = self.consumer(f"https://github.com/{TEMPLATE}.git")
        self.vendored(target)
        copy = target / "tools" / "sdlc_protocol"
        self.assertEqual(vendoring.copied_files(copy), vendoring.copied_files(PACKAGE))
        for name in vendoring.copied_files(PACKAGE):
            self.assertEqual((copy / name).read_bytes(), (PACKAGE / name).read_bytes(), name)
        self.assertEqual(vendoring.problems(target / "tools"), [])

    def test_the_plugin_takes_the_guard_and_what_every_copy_takes(self):
        target = self.consumer()
        readme = target / "plugins" / "sdlc" / "vendor" / "README.md"
        readme.parent.mkdir(parents=True)
        readme.write_text("the plugin's own note\n", encoding="ascii")
        self.vendored(target, "--consumer", MARKETPLACE)
        vendor_dir = target / "plugins" / "sdlc" / "vendor"
        self.assertEqual(vendoring.copied_files(vendor_dir / "sdlc_protocol"),
                         ["__init__.py", "guard.py", "vendoring.py"])
        self.assertEqual(sorted(self.record_of(vendor_dir)[2]), ["__init__.py", "guard.py", "vendoring.py"])
        self.assertEqual(readme.read_text(encoding="ascii"), "the plugin's own note\n")
        self.assertEqual(vendoring.problems(vendor_dir), [])

    def test_the_record_names_the_version_the_package_states(self):
        version, _, _ = self.record_of_a_fresh_copy()
        self.assertEqual(version, sdlc_protocol.__version__)

    def record_of_a_fresh_copy(self):
        target = self.consumer()
        self.vendored(target, "--consumer", APP)
        return self.record_of(target / "app" / "vendor")

    def test_the_version_is_read_without_importing_the_package(self):
        init = self.library / "sdlc_protocol" / "__init__.py"
        init.write_text(init.read_text(encoding="ascii").replace(
            f'__version__ = "{sdlc_protocol.__version__}"', '__version__ = "9.8.7"') + "raise SystemExit(3)\n",
            encoding="ascii")
        self.assertEqual(self.record_of_a_fresh_copy()[0], "9.8.7")

    def test_a_package_that_states_no_version_is_refused(self):
        (self.library / "sdlc_protocol" / "__init__.py").write_text('"""no version"""\n', encoding="ascii")
        done = self.vendor(self.consumer(), "--consumer", APP)
        self.assertEqual(done.returncode, 1)
        self.assertIn("no __version__", done.stderr)

    def test_replaces_the_old_copy_and_leaves_the_rest_of_the_vendor_dir_alone(self):
        target = self.consumer()
        stale = target / "app" / "vendor" / "sdlc_protocol" / "board.py"
        stale.parent.mkdir(parents=True)
        stale.write_text("a module this consumer no longer takes\n", encoding="ascii")
        own = target / "app" / "vendor" / "__init__.py"
        own.write_text('"""the App\'s own package marker"""\n', encoding="ascii")
        self.vendored(target, "--consumer", APP)
        self.assertFalse(stale.exists())
        self.assertEqual(own.read_text(encoding="ascii"), '"""the App\'s own package marker"""\n')

    def test_caches_in_the_library_stay_behind(self):
        planted = self.library / "sdlc_protocol" / "__pycache__"
        planted.mkdir()
        (planted / "guard.cpython-312.pyc").write_bytes(b"\0")
        target = self.consumer()
        self.vendored(target, "--consumer", TEMPLATE)
        copy = target / "tools" / "sdlc_protocol"
        self.assertEqual([p for p in copy.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc"], [])

    def test_says_what_it_did(self):
        target = self.consumer()
        done = self.vendored(target, "--consumer", MARKETPLACE)
        self.assertIn(f"{MARKETPLACE} takes __init__.py, guard.py, vendoring.py (3 files)", done.stdout)
        self.assertIn(f"sdlc-protocol {sdlc_protocol.__version__} {self.package_commit}, 3 file hashes",
                      done.stdout)
        self.assertIn("replaced the copy and its record", done.stdout)


class FindingTheConsumer(Repositories):
    def test_by_an_https_remote(self):
        target = self.consumer(f"https://github.com/{APP}")
        self.vendored(target)
        self.assertTrue((target / "app" / "vendor" / "VENDORED").is_file())

    def test_by_an_ssh_remote_whatever_the_case(self):
        target = self.consumer("git@github.com:openspineconsortium/SDLC-Marketplace.git")
        self.vendored(target)
        self.assertTrue((target / "plugins" / "sdlc" / "vendor" / "VENDORED").is_file())

    def test_consumer_wins_over_the_remote(self):
        target = self.consumer(f"https://github.com/{APP}.git")
        self.vendored(target, "--consumer", MARKETPLACE)
        self.assertFalse((target / "app").exists())
        self.assertTrue((target / "plugins" / "sdlc" / "vendor" / "VENDORED").is_file())

    def test_no_remote_and_no_consumer_lists_the_consumers(self):
        done = self.vendor(self.consumer())
        self.assertEqual(done.returncode, 1)
        self.assertIn("--consumer", done.stderr)
        for consumer in (TEMPLATE, MARKETPLACE, APP):
            self.assertIn(consumer, done.stderr)

    def test_a_repository_that_is_no_consumer_lists_the_consumers(self):
        for args, remote in (((), "https://github.com/octo/widgets.git"), (("--consumer", "octo/widgets"), None)):
            with self.subTest(args=args):
                target = self.consumer(remote, name=f"widgets{len(args)}")
                done = self.vendor(target, *args)
                self.assertEqual(done.returncode, 1)
                self.assertIn("octo/widgets is not a consumer", done.stderr)
                self.assertIn(TEMPLATE, done.stderr)
                self.assertEqual(list(target.iterdir()), [target / ".git"])

    def test_remote_urls(self):
        cases = {
            "https://github.com/OpenSpineConsortium/sdlc-app.git": "OpenSpineConsortium/sdlc-app",
            "https://github.com/OpenSpineConsortium/sdlc-app": "OpenSpineConsortium/sdlc-app",
            "https://x-access-token:abc@github.com/OpenSpineConsortium/sdlc-app.git": "OpenSpineConsortium/sdlc-app",
            "git@github.com:OpenSpineConsortium/sdlc.marketplace.git": "OpenSpineConsortium/sdlc.marketplace",
            "ssh://git@github.com/OpenSpineConsortium/coordbench.git": "OpenSpineConsortium/coordbench",
            "https://gitlab.com/OpenSpineConsortium/sdlc-app.git": None,
            "/srv/git/sdlc-app.git": None,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(vendor.repository_of_remote(url), expected)


class TheCommitRule(Repositories):
    def commit_recorded(self, name="consumer"):
        target = self.consumer(name=name)
        self.vendored(target, "--consumer", MARKETPLACE)
        return self.record_of(target / "plugins" / "sdlc" / "vendor")[1]

    def test_a_commit_elsewhere_in_the_library_does_not_move_the_record(self):
        (self.library / "README.md").write_text("the library, documented\n", encoding="ascii")
        commit_all(self.library, "document the library")
        self.assertEqual(self.commit_recorded(), self.package_commit)

    def test_a_commit_to_the_package_does(self):
        guard = self.library / "sdlc_protocol" / "guard.py"
        guard.write_text(guard.read_text(encoding="ascii") + "# changed\n", encoding="ascii")
        changed = commit_all(self.library, "change the guard")
        self.assertEqual(self.commit_recorded(), changed)

    def test_a_merge_commit_records_what_the_pull_requests_branch_did(self):
        git(self.library, "checkout", "-q", "-b", "claude/7")
        guard = self.library / "sdlc_protocol" / "guard.py"
        guard.write_text(guard.read_text(encoding="ascii") + "# from the pull request\n", encoding="ascii")
        branch_commit = commit_all(self.library, "change the guard on a branch")
        on_the_branch = self.commit_recorded("on-the-branch")
        git(self.library, "checkout", "-q", "main")
        (self.library / "README.md").write_text("main moved meanwhile\n", encoding="ascii")
        commit_all(self.library, "main moves")
        git(self.library, "merge", "-q", "--no-ff", "-m", "Merge pull request #7", "claude/7")
        self.assertNotEqual(git(self.library, "rev-parse", "HEAD"), branch_commit)
        self.assertEqual((on_the_branch, self.commit_recorded("on-main")), (branch_commit, branch_commit))

    def test_a_change_not_yet_committed_is_dirty(self):
        guard = self.library / "sdlc_protocol" / "guard.py"
        guard.write_text(guard.read_text(encoding="ascii") + "# not committed\n", encoding="ascii")
        self.assertEqual(self.commit_recorded(), f"{self.package_commit}-dirty")

    def test_a_new_file_not_yet_committed_is_dirty(self):
        (self.library / "sdlc_protocol" / "new_rule.py").write_text('"""A new rule."""\n', encoding="ascii")
        self.assertEqual(self.commit_recorded(), f"{self.package_commit}-dirty")

    def test_outside_a_git_checkout_the_commit_is_unknown(self):
        loose = self.scratch / "loose"
        loose.mkdir()
        copy_library(loose)
        target = self.consumer()
        done = subprocess.run([sys.executable, "-B", str(loose / "scripts" / "vendor.py"), str(target),
                               "--consumer", MARKETPLACE], capture_output=True, text=True, timeout=60,
                              env=quiet_environment())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.record_of(target / "plugins" / "sdlc" / "vendor")[1], "unknown")


class RunningTwice(Repositories):
    def test_the_second_run_changes_nothing_and_says_so(self):
        target = self.consumer(f"https://github.com/{TEMPLATE}.git")
        self.vendored(target)
        commit_all(target, "vendor")
        again = self.vendored(target)
        self.assertIn("nothing changed", again.stdout)
        self.assertEqual(git(target, "status", "--porcelain"), "")


class Refusals(Repositories):
    def test_this_repository_is_not_a_target(self):
        done = self.vendor(self.library, "--consumer", TEMPLATE)
        self.assertEqual(done.returncode, 1)
        self.assertIn("is this repository", done.stderr)
        self.assertFalse((self.library / "tools").exists())

    def test_a_target_that_is_not_a_directory(self):
        missing = self.scratch / "nowhere"
        done = self.vendor(missing, "--consumer", TEMPLATE)
        self.assertEqual(done.returncode, 1)
        self.assertIn("is not a directory", done.stderr)
        self.assertFalse(missing.exists())

    def test_a_partial_copy_whose_imports_would_not_resolve(self):
        """Refused for every consumer, as anything wrong in ecosystem.json is,
        before a file is written."""
        path = self.library / "ecosystem.json"
        eco = json.loads(path.read_text(encoding="utf-8"))
        for consumer in eco["consumers"]:
            if consumer["repository"] == MARKETPLACE:
                consumer["modules"] = ["protocol_check.py"]
        path.write_text(json.dumps(eco, indent=2) + "\n", encoding="utf-8")
        target = self.consumer()
        done = self.vendor(target, "--consumer", MARKETPLACE)
        self.assertEqual(done.returncode, 1)
        self.assertIn("protocol_check.py imports claims.py, which its copy leaves out", done.stderr)
        self.assertFalse((target / "plugins").exists())


class TheWrapper(Repositories):
    """scripts/vendor.sh, the name the consumers' documents give."""

    def test_no_target_is_a_usage_error(self):
        done = subprocess.run(["bash", str(self.library / "scripts" / "vendor.sh")], capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage: scripts/vendor.sh <target repository>", done.stderr)

    def test_hands_its_arguments_to_vendor_py(self):
        target = self.consumer()
        done = self.vendor(target, "--consumer", APP, script="vendor.sh")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(vendoring.problems(target / "app" / "vendor"), [])


if __name__ == "__main__":
    unittest.main()
