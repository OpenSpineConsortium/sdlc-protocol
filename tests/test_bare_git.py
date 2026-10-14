"""The world over a bare repository, which is the coordbench sdlc arm's
merge gate moved into the library. Its merges are checked against what git
itself did, in a bare repository in a temporary directory with a working
clone per agent, as the arm's gate tests check them: a clean branch lands
as a --no-ff merge commit made by the gate's identity; a conflicting one is
MainMoved and leaves main and the scratch clone as they were; a push
refused because main moved meanwhile is MainMoved too; one hold of the
world fetches once; and closing the world frees its clone for good. The
second half runs the arm's gate tests through sdlc_protocol.gate over this
world, without the hub."""

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import gate  # noqa: E402
from sdlc_protocol.adapters import bare_git  # noqa: E402
from sdlc_protocol.adapters.bare_git import BareGitWorld  # noqa: E402

MAIN_MOVED = ("main moved: merge team/main into your branch, resolve the conflicts, push, "
              "and call mark_ready again")
ARM = gate.Policy(bring_main_in=gate.MERGE, who_merges=gate.GATE, required_check="", remote="team")
BASE_TEXT = "def greet():\n    return 'hello'\n\n\ndef part():\n    return 'base'\n"
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def git(cwd, *args, check=True):
    """git as an agent runs it, with an identity of its own so the host's
    configuration does not matter."""
    result = subprocess.run(["git", "-c", "user.name=agent", "-c", "user.email=agent@coordbench.local",
                             "-c", "commit.gpgsign=false", *args], cwd=cwd, capture_output=True,
                            text=True, timeout=60, env=GIT_ENV)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


class SharedRemote:
    """A bare repository seeded with one commit on main, the base ref, and a
    working clone per agent with the bare repository as its remote `team`."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sdlc-bare-git-test-")
        self.root = Path(self.tmp.name)
        self.bare = self.root / "team.git"
        git(self.root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.bare))
        seed = self.root / "seed"
        seed.mkdir()
        git(seed, "init", "--quiet", "--initial-branch=main")
        (seed / "app.py").write_text(BASE_TEXT, encoding="utf-8")
        git(seed, "add", "app.py")
        git(seed, "commit", "--quiet", "-m", "base")
        git(seed, "push", "--quiet", str(self.bare), "HEAD:refs/heads/main")
        self.base = git(seed, "rev-parse", "HEAD")

    def clone(self, agent):
        path = self.root / agent
        git(self.root, "clone", "--quiet", "--origin", "team", str(self.bare), str(path))
        return path

    def main(self):
        return git(self.bare, "rev-parse", "refs/heads/main")

    def show(self, ref, path):
        return git(self.bare, "show", f"{ref}:{path}")

    def close(self):
        self.tmp.cleanup()


def start_branch(clone, issue, base):
    git(clone, "checkout", "--quiet", "-b", f"claude/{issue}", base)


def commit_and_push(clone, issue, files, message):
    for name, text in files.items():
        (clone / name).write_text(text, encoding="utf-8")
    git(clone, "add", *files)
    git(clone, "commit", "--quiet", "-m", message)
    git(clone, "push", "--quiet", "team", f"claude/{issue}")


def refusal(action, *args):
    try:
        answer = action(*args)
    except gate.GateError as error:
        return error
    raise AssertionError(f"{action.__name__} was expected to refuse, and answered: {answer}")


class RemoteTest(unittest.TestCase):
    """A shared remote, two agents' clones and the world over the remote."""

    def setUp(self):
        self.remote = SharedRemote()
        self.addCleanup(self.remote.close)
        self.world = BareGitWorld(str(self.remote.bare))
        self.addCleanup(self.world.close)
        self.one = self.remote.clone("agent1")
        self.two = self.remote.clone("agent2")

    def push_branch(self, clone, issue, files):
        start_branch(clone, issue, self.remote.base)
        commit_and_push(clone, issue, files, f"feature {issue}")
        return git(clone, "rev-parse", "HEAD")

    def push_main(self, files):
        """Someone else moves main on the remote."""
        clone = self.remote.clone("other")
        for name, text in files.items():
            (clone / name).write_text(text, encoding="utf-8")
        git(clone, "add", *files)
        git(clone, "commit", "--quiet", "-m", "elsewhere")
        git(clone, "push", "--quiet", "team", "HEAD:main")
        return git(clone, "rev-parse", "HEAD")


class WorldTest(RemoteTest):
    def test_it_reads_main_and_the_branches_and_runs_no_checks(self):
        self.assertEqual(self.world.main_sha(), self.remote.base)
        self.assertEqual(self.world.branch_sha("claude/1"), "")
        tip = self.push_branch(self.one, 1, {"one.py": "ONE = 1\n"})
        self.assertEqual(self.world.branch_sha("claude/1"), tip)
        self.assertEqual(self.world.check("claude/1"), gate.NO_CHECK)
        self.assertEqual(self.world.now().utcoffset().total_seconds(), 0)

    def test_each_standing_is_what_git_says(self):
        self.assertEqual(self.world.standing("claude/1"), gate.NO_BRANCH)
        start_branch(self.one, 1, self.remote.base)
        git(self.one, "push", "--quiet", "team", "claude/1")
        self.assertEqual(self.world.standing("claude/1"), gate.NOTHING_NEW)
        commit_and_push(self.one, 1, {"app.py": BASE_TEXT.replace("'hello'", "'hello, world'")}, "one")
        self.push_branch(self.two, 2, {"two.py": "TWO = 2\n"})
        self.assertEqual(self.world.standing("claude/1"), gate.CLEAN)
        self.push_main({"app.py": BASE_TEXT.replace("'hello'", "'goodbye'")})
        self.assertEqual(self.world.standing("claude/2"), gate.BEHIND)
        detail = self.world.standing_detail("claude/1")
        self.assertEqual((detail.word, detail.main, detail.conflicts),
                         (gate.CONFLICTING, self.remote.main(), ("app.py",)))

    def test_a_clean_branch_lands_as_a_merge_commit_by_the_gates_identity(self):
        tip = self.push_branch(self.one, 1, {"one.py": "ONE = 1\n"})
        result = self.world.merge("claude/1")
        main = self.remote.main()
        self.assertEqual(result, gate.MergeResult(main))
        parents = git(self.remote.bare, "rev-list", "--parents", "-n", "1", main).split()[1:]
        self.assertEqual(parents, [self.remote.base, tip], "--no-ff makes a merge commit of main and the branch")
        made = git(self.remote.bare, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%s", main)
        self.assertEqual(made, "coordbench merge gate <gate@coordbench.local>|"
                               "coordbench merge gate <gate@coordbench.local>|Merge claude/1: closes #1")
        self.assertEqual(self.remote.show("main", "one.py"), "ONE = 1")
        self.assertEqual(self.world.main_sha(), main, "the next read sees the merge")

    def test_a_conflict_is_main_moved_and_leaves_main_and_the_clone_as_they_were(self):
        self.push_branch(self.one, 1, {"app.py": BASE_TEXT.replace("'hello'", "'hello, world'")})
        self.push_branch(self.two, 2, {"app.py": BASE_TEXT.replace("'hello'", "'goodbye'")})
        first = self.world.merge("claude/1").sha
        with self.assertRaises(gate.MainMoved) as moved:
            self.world.merge("claude/2")
        self.assertEqual((moved.exception.main, moved.exception.conflicts), (first, ("app.py",)))
        self.assertEqual(moved.exception.exit_code, 3)
        self.assertEqual(self.remote.main(), first)
        self.assertFalse(os.path.exists(os.path.join(self.world.repo, ".git", "MERGE_HEAD")),
                         "the world aborts the merge it could not finish")

    def test_a_push_refused_because_main_moved_meanwhile_is_main_moved(self):
        self.push_branch(self.one, 1, {"one.py": "ONE = 1\n"})
        with self.world:
            before = self.world.main_sha()
            elsewhere = self.push_main({"other.py": "OTHER = 1\n"})
            with self.assertRaises(gate.MainMoved) as moved:
                self.world.merge("claude/1")
        self.assertIn("the push of main was refused", moved.exception.reason)
        self.assertEqual((moved.exception.main, moved.exception.conflicts), (before, ()))
        self.assertEqual(self.remote.main(), elsewhere, "main only moves forward")

    def test_a_branch_with_nothing_new_or_none_at_all_is_refused(self):
        start_branch(self.one, 1, self.remote.base)
        git(self.one, "push", "--quiet", "team", "claude/1")
        with self.assertRaisesRegex(gate.MergeRefused, "nothing to merge: every commit on claude/1 is already"):
            self.world.merge("claude/1")
        with self.assertRaisesRegex(gate.MergeRefused, "claude/9 is not on the shared remote"):
            self.world.merge("claude/9")
        self.assertEqual(self.remote.main(), self.remote.base)

    def test_one_hold_of_the_world_fetches_once(self):
        self.push_branch(self.one, 1, {"one.py": "ONE = 1\n"})
        fetches = []
        fetch = self.world.fetch
        self.world.fetch = lambda: (fetches.append(1), fetch())[1]
        with self.world:
            self.world.main_sha()
            self.world.branch_sha("claude/1")
            self.world.standing("claude/1")
            moved = self.push_main({"other.py": "OTHER = 1\n"})
            self.assertEqual(self.world.main_sha(), self.remote.base, "one hold sees one state of the remote")
        self.assertEqual(len(fetches), 1)
        self.assertEqual(self.world.main_sha(), moved)
        self.world.branch_sha("claude/1")
        self.assertEqual(len(fetches), 3, "outside a hold each read fetches")

    def test_standings_reads_every_branch_from_one_fetch(self):
        self.push_branch(self.one, 1, {"one.py": "ONE = 1\n"})
        main, live = self.world.standings(["claude/1", "claude/2"])
        self.assertEqual(main, self.remote.base)
        self.assertEqual({branch: standing.word for branch, standing in live.items()},
                         {"claude/1": gate.CLEAN, "claude/2": gate.NO_BRANCH})

    def test_closing_frees_the_clone_and_refuses_later_calls(self):
        self.world.main_sha()
        scratch = self.world.scratch.name
        self.assertTrue(os.path.isdir(scratch))
        self.world.close()
        self.assertFalse(os.path.isdir(scratch))
        with self.assertRaisesRegex(gate.GateFailed, "the run has closed") as late:
            self.world.main_sha()
        self.assertEqual(late.exception.exit_code, 4)
        self.assertIsNone(self.world.scratch, "a call after close does not clone again")
        self.assertEqual(gate.standings(self.world, ["claude/1"]), ("", {}))
        self.world.close()

    def test_closing_waits_for_a_merge_in_progress(self):
        self.world.main_sha()
        scratch = self.world.scratch.name
        done = threading.Event()
        with self.world:  # as a merge holds it
            closer = threading.Thread(target=lambda: (self.world.close(), done.set()))
            closer.start()
            self.assertFalse(done.wait(0.3), "close must not free the clone under a merge")
            self.assertTrue(os.path.isdir(scratch))
        closer.join(10)
        self.assertTrue(done.is_set())
        self.assertFalse(os.path.isdir(scratch))

    def test_a_remote_that_cannot_be_cloned_fails(self):
        world = BareGitWorld(str(self.remote.root / "missing.git"))
        with self.assertRaisesRegex(gate.GateFailed, "the merge gate cannot clone the shared remote"):
            world.main_sha()
        self.assertIsNone(world.scratch)


class HelpersTest(RemoteTest):
    def test_remote_main_reads_main_or_nothing(self):
        self.assertEqual(bare_git.remote_main(str(self.remote.bare)), self.remote.base)
        self.assertEqual(bare_git.remote_main(str(self.remote.root / "missing.git")), "")

    def test_a_claim_branch_merge_closes_its_issue(self):
        self.assertEqual(bare_git.merge_message("claude/12"), "Merge claude/12: closes #12")
        self.assertEqual(bare_git.merge_message("feature/x"), "Merge feature/x")

    def test_a_missing_git_is_gate_failed(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.remote.root / "no-bin")}):
            with self.assertRaisesRegex(gate.GateFailed, "git is not installed where the merge gate runs"):
                bare_git.run_git(["--version"])


class GateOverBareGitTest(RemoteTest):
    """The arm's gate tests, through sdlc_protocol.gate: two agents, two
    features, one shared bare repository."""

    def setUp(self):
        super().setUp()
        self.queue = gate.Queue({1: "agent1", 2: "agent2"}, {1: "Greeting", 2: "Farewell"}, False,
                                self.remote.base)

    def call(self, decision, number, agent, *rest):
        with self.world:
            return decision(ARM, self.world, self.queue, number, agent, *rest)

    def ready(self, number, agent=None):
        return self.call(gate.mark_ready, number, agent or f"agent{number}")

    def status(self, number):
        with self.world:
            return gate.pull_status(ARM, self.world, self.queue, number)

    def claim_branch_and_open(self, clone, issue, files):
        gate.claim(ARM, self.queue, issue, f"agent{issue}")
        self.push_branch(clone, issue, files)
        return self.call(gate.open_pull, issue, f"agent{issue}")

    def both_change_the_same_line(self):
        """agent1 lands first; agent2's change to the same line now conflicts."""
        self.claim_branch_and_open(self.one, 1, {"app.py": BASE_TEXT.replace("'hello'", "'hello, world'")})
        self.claim_branch_and_open(self.two, 2, {"app.py": BASE_TEXT.replace("'hello'", "'goodbye'")})
        self.assertTrue(self.ready(1).merged)

    def test_a_pull_request_needs_the_claim_and_the_pushed_branch(self):
        unclaimed = refusal(self.call, gate.open_pull, 1, "agent1")
        self.assertIn("nobody has claimed it", str(unclaimed))
        self.assertEqual(unclaimed.exit_code, 1)
        gate.claim(ARM, self.queue, 1, "agent1")
        unpushed = refusal(self.call, gate.open_pull, 1, "agent1")
        self.assertIn("claude/1 is not on team yet; push it first", str(unpushed))
        self.assertEqual(unpushed.exit_code, 1)
        self.push_branch(self.one, 1, {"one.py": "ONE = 1\n"})
        self.assertEqual(self.call(gate.open_pull, 1, "agent1").title, "Greeting", "titled as its issue")
        self.assertIn("one issue, one pull request", str(refusal(self.call, gate.open_pull, 1, "agent1")))
        theirs = refusal(self.call, gate.open_pull, 1, "agent2")
        self.assertIn("claimed by agent1", str(theirs))
        self.assertEqual(theirs.exit_code, 2)

    def test_a_clean_pull_request_lands_on_main_as_a_merge_commit(self):
        self.claim_branch_and_open(self.one, 1, {"one.py": "ONE = 1\n"})
        tip = git(self.one, "rev-parse", "HEAD")
        outcome = self.ready(1)
        main = self.remote.main()
        self.assertTrue(outcome.text.startswith(f"merged: claude/1 is in main as {main[:7]}"))
        self.assertEqual((outcome.branch_commit, outcome.main_before, outcome.main), (tip, self.remote.base, main))
        parents = git(self.remote.bare, "rev-list", "--parents", "-n", "1", main).split()[1:]
        self.assertEqual(parents, [self.remote.base, tip])
        self.assertEqual(self.remote.show("main", "one.py"), "ONE = 1")
        self.assertEqual(self.queue.pulls[1].state, "merged")
        self.assertEqual(self.queue.issues[1].label(), "closed")
        self.assertIn("already merged", str(refusal(self.ready, 1)))
        self.assertIn("#1 is closed", str(refusal(gate.claim, ARM, self.queue, 1, "agent1")))

    def test_a_conflicting_pull_request_answers_main_moved_and_leaves_main(self):
        self.both_change_the_same_line()
        main_before = self.remote.main()
        outcome = self.ready(2)
        self.assertEqual(outcome.exit_code, 3)
        self.assertTrue(outcome.text.startswith(MAIN_MOVED + "."))
        self.assertIn("Conflicting files: app.py.", outcome.text)
        self.assertIn(f"main is still at {main_before[:7]}", outcome.text)
        self.assertEqual(self.remote.main(), main_before)
        self.assertIn("'hello, world'", self.remote.show("main", "app.py"))
        self.assertEqual(self.queue.pulls[2].state, "main moved")
        self.assertEqual(self.queue.issues[2].label(), "in-review")
        self.assertFalse(os.path.exists(os.path.join(self.world.repo, ".git", "MERGE_HEAD")),
                         "the gate aborts the merge it could not finish")

    def test_after_merging_main_in_and_pushing_the_pull_request_merges(self):
        self.both_change_the_same_line()
        self.assertEqual(self.ready(2).exit_code, 3)
        first_merge = self.remote.main()
        status = self.status(2)
        self.assertIn("by agent2, main moved", status)
        self.assertIn("conflicting", status)
        # What the answer asks for: merge main in, resolve, commit, push.
        git(self.two, "fetch", "--quiet", "team")
        self.assertIn("CONFLICT", git(self.two, "merge", "team/main", check=False))
        resolved = BASE_TEXT.replace("'hello'", "'hello, world'") + "\n\ndef farewell():\n    return 'goodbye'\n"
        (self.two / "app.py").write_text(resolved, encoding="utf-8")
        git(self.two, "add", "app.py")
        git(self.two, "commit", "--quiet", "--no-edit")
        git(self.two, "push", "--quiet", "team", "claude/2")
        self.assertIn("clean, up to date", self.status(2))
        self.assertTrue(self.ready(2).text.startswith("merged: claude/2 is in main"))
        main = self.remote.main()
        # Main only moved forward: the first merge is still in its history.
        git(self.remote.bare, "merge-base", "--is-ancestor", first_merge, main)
        self.assertEqual(self.remote.show("main", "app.py"), resolved.rstrip("\n"))
        diff = git(self.remote.bare, "diff", self.remote.base, "main")
        self.assertIn("+    return 'hello, world'", diff)
        self.assertIn("+    return 'goodbye'", diff)

    def test_only_the_author_marks_ready_and_only_a_branch_with_new_work(self):
        gate.claim(ARM, self.queue, 1, "agent1")
        start_branch(self.one, 1, self.remote.base)
        git(self.one, "push", "--quiet", "team", "claude/1")
        self.call(gate.open_pull, 1, "agent1")
        not_theirs = refusal(self.ready, 1, "agent2")
        self.assertIn("only its author marks it ready", str(not_theirs))
        self.assertEqual(not_theirs.exit_code, 2)
        nothing_new = refusal(self.ready, 1)
        self.assertIn("nothing to merge", str(nothing_new))
        self.assertEqual(nothing_new.exit_code, 1)
        self.assertEqual(self.queue.pulls[1].state, "draft")
        self.assertEqual(self.queue.issues[1].label(), "claimed", "a refused ready keeps the claim")
        self.assertIn("#2 has no pull request", str(refusal(self.ready, 2)))

    def test_two_clean_pull_requests_marked_ready_at_once_both_land(self):
        self.claim_branch_and_open(self.one, 1, {"one.py": "ONE = 1\n"})
        self.claim_branch_and_open(self.two, 2, {"two.py": "TWO = 2\n"})
        start = threading.Barrier(2)
        outcomes = {}

        def ready(number):
            start.wait()
            outcomes[number] = self.ready(number)

        threads = [threading.Thread(target=ready, args=(number,)) for number in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(outcomes), [1, 2])
        self.assertTrue(all(outcome.merged for outcome in outcomes.values()))
        self.assertEqual(self.remote.show("main", "one.py"), "ONE = 1")
        self.assertEqual(self.remote.show("main", "two.py"), "TWO = 2")
        self.assertEqual(len(git(self.remote.bare, "rev-list", "--merges", "main").split()), 2)

    def test_status_reads_how_the_branch_stands_to_main(self):
        self.assertIn("#1 has no pull request yet; the issue is not claimed yet (todo)", self.status(1))
        self.both_change_the_same_line()
        merged = self.status(1)
        self.assertIn("Pull request for #1: claude/1 -> main, by agent1, merged. Merge attempts: 1.", merged)
        self.assertIn("Merged into main as", merged)
        conflicting = self.status(2)
        self.assertIn("by agent2, draft", conflicting)
        self.assertIn("conflicting", conflicting)
        self.assertIn("conflicts in app.py", conflicting)

    def test_status_says_behind_when_main_moved_without_a_conflict(self):
        self.claim_branch_and_open(self.one, 1, {"one.py": "ONE = 1\n"})
        self.claim_branch_and_open(self.two, 2, {"two.py": "TWO = 2\n"})
        self.assertIn("clean, up to date", self.status(2))
        self.ready(1)
        self.assertIn("behind", self.status(2))

    def test_the_board_shows_issues_claims_and_pull_requests(self):
        self.both_change_the_same_line()
        self.ready(2)
        with self.world:
            text = gate.current_board(ARM, self.world, self.queue)
        main = self.remote.main()
        self.assertIn(f"As of commit {main[:7]} on main; the run began at the base ref {self.remote.base[:7]}",
                      text)
        self.assertIn("| #1 | Greeting | agent1 | closed |", text)
        self.assertIn("| #2 | Farewell | agent2 | in-review |", text)
        self.assertIn(f"| #1 | claude/1 | agent1 | merged | merged as {main[:7]} | 1 |", text)
        self.assertIn("| #2 | claude/2 | agent2 | main moved | conflicting (app.py) | 1 |", text)

    def test_after_the_world_closes_status_is_unknown_and_nothing_merges(self):
        self.claim_branch_and_open(self.one, 1, {"one.py": "ONE = 1\n"})
        self.world.close()
        status = self.status(1)
        self.assertIn("Pull request for #1", status)
        self.assertIn("Against main: unknown", status)
        late = refusal(self.ready, 1)
        self.assertIn("the run has closed", str(late))
        self.assertEqual(late.exit_code, 4)
        self.assertEqual(self.queue.pulls[1].state, "draft", "nothing changed")
        self.assertIsNone(self.world.scratch, "a call after close does not clone again")
        self.assertEqual(self.remote.main(), self.remote.base)


if __name__ == "__main__":
    unittest.main()
