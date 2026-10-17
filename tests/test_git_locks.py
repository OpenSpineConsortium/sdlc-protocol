"""The lock table's git side, against git itself: a bare repository in a
temporary directory with a clone per worker. The table starts with the
first lock; two claims on different functions of one file both hold
theirs, and a claim on what another holds or reads is refused; a claim
and its locks are pushed all or none; a push built on a tip that moved
meanwhile is refused and decided again on the new tip; a lock lives while
its claim's branch is on the remote and not yet in main, through a rebase
of the claim; and verify holds a change to the locks as the switch says."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims, cli, locks  # noqa: E402
from sdlc_protocol.adapters import git_locks  # noqa: E402
from sdlc_protocol.locks import READ, WRITE, Entry  # noqa: E402

BASE = "def f(x):\n    return x + 1\n\n\ndef g(y):\n    return f(y) * 2\n\n\ndef h():\n    return 0\n"

# Automatic maintenance runs detached in git 2.55 and races a test's
# cleanup, so every scratch repository has it off, in the environment and
# in its own configuration (symphony-template#63).
QUIET = {"gc.auto": "0", "gc.autoDetach": "false", "maintenance.auto": "false",
         "maintenance.autoDetach": "false", "receive.autogc": "false"}
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_CONFIG_COUNT": str(len(QUIET)),
       **{f"GIT_CONFIG_KEY_{i}": key for i, key in enumerate(QUIET)},
       **{f"GIT_CONFIG_VALUE_{i}": value for i, value in enumerate(QUIET.values())}}


def git(cwd, *args, check=True):
    done = subprocess.run(["git", "-c", "user.name=worker", "-c", "user.email=worker@test.local",
                           "-c", "commit.gpgsign=false", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=60, env=ENV)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()


def quiet(repo):
    for key, value in QUIET.items():
        git(repo, "config", key, value)


class Remote(unittest.TestCase):
    """A bare remote seeded with m.py on main, and the clones a and b."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sdlc-git-locks-test-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bare = str(root / "remote.git")
        git(root, "init", "--quiet", "--bare", "--initial-branch=main", self.bare)
        quiet(self.bare)
        seed = root / "seed"
        git(root, "init", "--quiet", "--initial-branch=main", str(seed))
        quiet(seed)
        (seed / "m.py").write_text(BASE, encoding="utf-8")
        git(seed, "add", "m.py")
        git(seed, "commit", "--quiet", "-m", "base")
        git(seed, "remote", "add", "origin", self.bare)
        git(seed, "push", "--quiet", "origin", "main")
        self.seed = str(seed)
        self.a = self.clone(root / "a")
        self.b = self.clone(root / "b")

    def clone(self, path):
        git(path.parent, "clone", "--quiet", self.bare, str(path))
        quiet(path)
        return str(path)

    def claim(self, repo, number, edit=None):
        """A claim branch made from main with one commit, not pushed."""
        git(repo, "fetch", "--quiet", "origin")
        git(repo, "checkout", "--quiet", "-B", f"claude/{number}", "origin/main")
        target = Path(repo) / claims.claim_path(number)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"claim {number} in {repo}\n", encoding="utf-8")
        git(repo, "add", "docs")
        git(repo, "commit", "--quiet", "-m", f"claim #{number}")
        if edit:
            Path(repo, "m.py").write_text(edit, encoding="utf-8")
            git(repo, "commit", "--quiet", "-am", f"work on #{number}")

    def remote_ref(self, ref):
        return git(self.bare, "rev-parse", "--verify", "--quiet", ref, check=False)

    def table(self):
        return locks.read_table(git(self.bare, "show", f"{locks.LOCKS_BRANCH}:{locks.TABLE_FILE}"))


class Acquiring(Remote):
    def test_the_first_lock_starts_the_branch_and_claim_and_lock_go_together(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1, WRITE, "wa")], claim_branch="claude/1")
        self.assertTrue(self.remote_ref("refs/heads/claude/1"))
        self.assertEqual(self.table(), [Entry("m.py#f", 1, WRITE, "wa")])
        log = git(self.bare, "log", "--format=%s", locks.LOCKS_BRANCH)
        self.assertEqual(log, "Locks: #1")

    def test_two_claims_on_different_functions_both_hold_theirs(self):
        self.claim(self.a, 1)
        self.claim(self.b, 2)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        git_locks.acquire(self.b, "origin", [Entry("m.py#h", 2)], claim_branch="claude/2")
        self.assertEqual(self.table(), [Entry("m.py#f", 1), Entry("m.py#h", 2)])

    def test_a_claim_on_what_another_reads_is_refused_with_its_branch(self):
        self.claim(self.a, 1)
        self.claim(self.b, 2)
        git_locks.acquire(self.a, "origin", [Entry("m.py#g", 1), Entry("m.py#f", 1, READ)],
                          claim_branch="claude/1")
        with self.assertRaises(locks.Held):
            git_locks.acquire(self.b, "origin", [Entry("m.py#f", 2)], claim_branch="claude/2")
        self.assertFalse(self.remote_ref("refs/heads/claude/2"))
        self.assertEqual(len(self.table()), 2)

    def test_a_claim_already_held_is_refused_and_takes_no_lock(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        self.claim(self.b, 1)
        with self.assertRaises(git_locks.ClaimTaken):
            git_locks.acquire(self.b, "origin", [Entry("m.py#h", 1, WRITE, "wb")], claim_branch="claude/1")
        self.assertEqual(self.table(), [Entry("m.py#f", 1)])

    def test_a_table_that_moved_meanwhile_is_decided_again_on_its_new_tip(self):
        self.claim(self.a, 1)
        self.claim(self.b, 2)
        seen = []

        def decide(entries, live):
            if not seen:
                git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
            seen.append(list(entries))
            return locks.acquire(entries, [Entry("m.py#f", 2)], live)

        with self.assertRaises(locks.Held):
            git_locks.change(self.b, "origin", decide, claim_branch="claude/2")
        self.assertEqual(seen[0], [])
        self.assertEqual(seen[1], [Entry("m.py#f", 1)])
        self.assertFalse(self.remote_ref("refs/heads/claude/2"))

    def test_a_push_on_a_stale_tip_is_refused_by_git(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        tip, entries = git_locks.fetch_table(self.b, "origin")
        git_locks.release(self.a, "origin", 1)
        stale = git_locks.commit_table(self.b, tip, entries + [Entry("m.py#h", 2)], "stale\n")
        outcome = git_locks.push(self.b, "origin", [f"{stale}:refs/heads/{locks.LOCKS_BRANCH}"])
        self.assertTrue(outcome[f"refs/heads/{locks.LOCKS_BRANCH}"])
        self.assertEqual(self.table(), [])


class Living(Remote):
    def test_a_lock_ends_when_its_claim_lands_in_main(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        git(self.a, "push", "--quiet", "origin", "claude/1:main")
        self.assertEqual(git_locks.sweep(self.b, "origin"), [])
        self.assertEqual(self.table(), [])

    def test_a_lock_lives_through_a_rebase_of_its_claim(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        Path(self.seed, "other.txt").write_text("moved\n", encoding="utf-8")
        git(self.seed, "add", "other.txt")
        git(self.seed, "commit", "--quiet", "-m", "main moves")
        git(self.seed, "push", "--quiet", "origin", "main")
        git(self.a, "fetch", "--quiet", "origin")
        git(self.a, "rebase", "--quiet", "origin/main")
        tip = git(self.bare, "rev-parse", "claude/1")
        git(self.a, "push", "--quiet", f"--force-with-lease=claude/1:{tip}", "origin", "claude/1")
        self.assertEqual(git_locks.sweep(self.b, "origin"), [Entry("m.py#f", 1)])

    def test_the_caller_may_end_a_claim_git_cannot_see_has_ended(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        self.assertEqual(git_locks.sweep(self.b, "origin", ended=[1]), [])

    def test_a_dead_lock_is_taken_over_in_the_same_commit(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        git(self.a, "push", "--quiet", "origin", "claude/1:main")
        self.claim(self.b, 2)
        after = git_locks.acquire(self.b, "origin", [Entry("m.py#f", 2)], claim_branch="claude/2")
        self.assertEqual(after, [Entry("m.py#f", 2)])


class Verifying(Remote):
    def run_cli(self, *args, mode="enforce"):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["locks", "--repo", self.a, *args]) if mode is None else \
                cli.run_locks(["--repo", self.a, *args], environ={"SYMPHONY_LOCKS": mode})
        return code, out.getvalue() + err.getvalue()

    def test_a_change_inside_its_grant_passes_and_one_outside_fails_when_enforced(self):
        self.claim(self.a, 1, edit=BASE.replace("x + 1", "x + 2"))
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        self.assertEqual(self.run_cli("verify", "--base", "origin/main", "--claim", "1")[0], 0)
        Path(self.a, "m.py").write_text(BASE.replace("x + 1", "x + 2").replace("return 0", "return 1"),
                                        encoding="utf-8")
        git(self.a, "commit", "--quiet", "-am", "outside the grant")
        code, said = self.run_cli("verify", "--base", "origin/main", "--claim", "1")
        self.assertEqual(code, 1)
        self.assertIn("m.py#h is changed but #1 holds no write lock on it", said)
        self.assertEqual(self.run_cli("verify", "--base", "origin/main", "--claim", "1", mode="advise")[0], 0)
        self.assertIn("locks are off", self.run_cli("verify", "--base", "origin/main", mode="off")[1])

    def test_a_pull_request_of_no_claim_fails_only_on_what_a_live_claim_holds(self):
        self.claim(self.b, 2)
        git_locks.acquire(self.b, "origin", [Entry("m.py#f", 2, WRITE, "wb")], claim_branch="claude/2")
        git(self.a, "checkout", "--quiet", "-B", "fix", "origin/main")
        Path(self.a, "m.py").write_text(BASE.replace("return 0", "return 1"), encoding="utf-8")
        git(self.a, "commit", "--quiet", "-am", "a fix to h")
        self.assertEqual(self.run_cli("verify", "--base", "origin/main")[0], 0)
        Path(self.a, "m.py").write_text(BASE.replace("x + 1", "x - 1"), encoding="utf-8")
        git(self.a, "commit", "--quiet", "-am", "a fix to f")
        code, said = self.run_cli("verify", "--base", "origin/main")
        self.assertEqual(code, 1)
        self.assertIn("m.py#f is held by #2 (wb, write)", said)

    def test_status_and_a_refusal_read_as_lines(self):
        self.claim(self.a, 1)
        self.assertIn("no locks", self.run_cli("status")[1])
        self.assertEqual(self.run_cli("acquire", "--claim", "1", "--worker", "wa", "--with-claim", "m.py#g")[0], 0)
        self.assertIn("m.py#f\tread\t#1\twa", self.run_cli("status")[1])
        self.claim(self.b, 2)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run_locks(["--repo", self.b, "acquire", "--claim", "2", "--with-claim", "m.py#f"], environ={})
        self.assertEqual(code, 1)
        self.assertIn("refused: m.py#f (write) is held by #1 (wa, read)", err.getvalue())


class Reviewed(Remote):
    """The cases an adversarial review of the first version found."""

    def test_a_refusal_of_the_table_by_the_server_is_retried_not_read_as_a_held_claim(self):
        marker = Path(self.tmp.name) / "refuse-once"
        marker.write_text("x", encoding="utf-8")
        hook = Path(self.bare) / "hooks" / "update"
        hook.write_text(f"#!/bin/sh\nif [ \"$1\" = refs/heads/locks ] && [ -e {marker} ]; then\n"
                        f"  rm {marker}; echo refused once >&2; exit 1\nfi\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        self.assertFalse(marker.exists())
        self.assertTrue(self.remote_ref("refs/heads/claude/1"))
        self.assertEqual(self.table(), [Entry("m.py#f", 1)])

    def test_commits_built_on_another_s_claim_are_never_pushed_onto_it(self):
        self.claim(self.a, 1)
        git_locks.acquire(self.a, "origin", [Entry("m.py#f", 1)], claim_branch="claude/1")
        before = self.remote_ref("refs/heads/claude/1")
        git(self.b, "fetch", "--quiet", "origin")
        git(self.b, "checkout", "--quiet", "-B", "claude/1", "origin/claude/1")
        Path(self.b, "m.py").write_text(BASE.replace("return 0", "return 9"), encoding="utf-8")
        git(self.b, "commit", "--quiet", "-am", "on top of another's claim")
        with self.assertRaises(git_locks.ClaimTaken):
            git_locks.acquire(self.b, "origin", [Entry("m.py#h", 1)], claim_branch="claude/1")
        self.assertEqual(self.remote_ref("refs/heads/claude/1"), before)

    def test_locks_for_a_claim_not_on_the_remote_are_refused(self):
        with self.assertRaises(git_locks.LocksFailed):
            git_locks.acquire(self.a, "origin", [Entry("m.py#f", 3)])
        with self.assertRaises(git_locks.LocksFailed):
            git_locks.acquire(self.a, "origin", [Entry("m.py#f", 0)])
        self.assertFalse(self.remote_ref(f"refs/heads/{locks.LOCKS_BRANCH}"))

    def test_a_pattern_in_a_file_name_is_read_as_the_name(self):
        Path(self.seed, "a[1].py").write_text(BASE, encoding="utf-8")
        Path(self.seed, "a1.py").write_text(BASE, encoding="utf-8")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "--quiet", "-m", "two files")
        git(self.seed, "push", "--quiet", "origin", "main")
        git(self.a, "fetch", "--quiet", "origin")
        git(self.a, "checkout", "--quiet", "-B", "work", "origin/main")
        Path(self.a, "a[1].py").write_text(BASE.replace("return 0", "return 1"), encoding="utf-8")
        Path(self.a, "a1.py").write_text(BASE.replace("x + 1", "x + 2"), encoding="utf-8")
        git(self.a, "commit", "--quiet", "-am", "both")
        self.assertEqual(git_locks.touched(self.a, "origin/main", "HEAD"), ["a1.py#f", "a[1].py#h"])

    def test_a_mode_change_touches_the_whole_file(self):
        git(self.a, "checkout", "--quiet", "-B", "work", "origin/main")
        os.chmod(Path(self.a, "m.py"), 0o755)
        git(self.a, "commit", "--quiet", "-am", "executable")
        self.assertEqual(git_locks.touched(self.a, "origin/main", "HEAD"), ["m.py"])

    def test_verify_at_advise_never_fails_on_what_it_cannot_read(self):
        args = ["--repo", self.a, "verify", "--base", "no-such-ref", "--claim", "1"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run_locks(args, environ={"SYMPHONY_LOCKS": "advise"}), 0)
            self.assertEqual(cli.run_locks(args, environ={"SYMPHONY_LOCKS": "enforce"}), 1)


if __name__ == "__main__":
    unittest.main()
