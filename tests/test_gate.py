"""The merge gate's decisions, over a world held in memory. These are the
coordbench sdlc arm's claim and gate tests without the hub: a claim is
exclusive and first come, first served; an agent claims only its own issue
unless the queue is open; a pull request needs the claim and the pushed
branch; one that merges cleanly lands and closes its issue; one that
conflicts answers "main moved" and leaves main as it was, and lands once
its author brings main in. A refusal's exit code tells losing a claim (2)
from main having moved (3) and from the gate failing (4). Beyond the arm,
the policy decides the main-moved wording (rebase for accounts, merge for
team members), whether the gate merges or the owner or auto-merge does,
and whether a required check holds the merge."""

import sys
import threading
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import gate  # noqa: E402
from sdlc_protocol.adapters.memory import MemoryWorld  # noqa: E402

# The arm's own wording for main moved, which the merge policy reproduces.
MAIN_MOVED = ("main moved: merge team/main into your branch, resolve the conflicts, push, "
              "and call mark_ready again")
# The policy the arm runs: members merge main in, the gate merges, and
# merging cleanly is the whole check.
ARM = gate.Policy(bring_main_in=gate.MERGE, who_merges=gate.GATE, required_check="", remote="team")
TITLES = {1: "Greeting", 2: "Farewell", 3: "Colours"}


def new_queue(features=(1, 2), open_queue=False, base_ref="0a1b2c3d4e"):
    given = {number: f"agent{number}" for number in features}
    return gate.Queue(given, {n: TITLES.get(n, f"Feature {n}") for n in features}, open_queue, base_ref)


def refusal(action, *args):
    """The GateError a decision raises; a decision that answers fails the test."""
    try:
        answer = action(*args)
    except gate.GateError as error:
        return error
    raise AssertionError(f"{action.__name__} was expected to refuse, and answered: {answer}")


class FailingWorld(MemoryWorld):
    """A world that cannot be read, as a remote that is gone or a closed run."""

    def main_sha(self):
        raise gate.GateFailed("the remote cannot be read")

    def branch_sha(self, branch):
        raise gate.GateFailed("the remote cannot be read")


class PolicyTest(unittest.TestCase):
    def test_the_defaults_are_the_accounts_way_of_working(self):
        policy = gate.Policy()
        self.assertEqual(policy.claim_prefix, "claude/")
        self.assertEqual(policy.required_check, "check")
        self.assertEqual(policy.bring_main_in, "rebase")
        self.assertEqual(policy.who_merges, "owner")
        self.assertEqual(policy.idle_after, timedelta(hours=24))
        self.assertEqual(policy.branch_of(7), "claude/7")
        self.assertEqual(policy.upstream, "origin/main")
        self.assertEqual(gate.ACCOUNT_POLICY, policy)
        self.assertEqual((gate.TEAM_POLICY.bring_main_in, gate.TEAM_POLICY.who_merges), ("merge", "auto-merge"))

    def test_an_unknown_choice_is_refused_when_the_policy_is_made(self):
        with self.assertRaisesRegex(ValueError, "bring_main_in is one of rebase, merge"):
            gate.Policy(bring_main_in="squash")
        with self.assertRaisesRegex(ValueError, "who_merges is one of owner, gate, auto-merge"):
            gate.Policy(who_merges="anyone")

    def test_merging_main_in_at_the_gate_is_the_arms_wording(self):
        self.assertEqual(gate.main_moved_instruction(ARM), MAIN_MOVED)

    def test_rebasing_pushes_with_force_with_lease_as_the_protocol_allows(self):
        self.assertEqual(gate.main_moved_instruction(replace(ARM, bring_main_in=gate.REBASE)),
                         "main moved: rebase your branch on team/main, resolve the conflicts, push it with "
                         "--force-with-lease, and call mark_ready again")
        self.assertEqual(gate.main_moved_instruction(gate.ACCOUNT_POLICY),
                         "main moved: rebase your branch on origin/main, resolve the conflicts, and push it "
                         "with --force-with-lease")

    def test_where_the_gate_does_not_merge_nobody_calls_mark_ready_again(self):
        self.assertEqual(gate.main_moved_instruction(gate.TEAM_POLICY),
                         "main moved: merge origin/main into your branch, resolve the conflicts, and push")

    def test_the_exit_codes_are_the_arms(self):
        self.assertEqual((gate.EXIT_DONE, gate.EXIT_REFUSED, gate.EXIT_SOMEONE_ELSES, gate.EXIT_MAIN_MOVED,
                          gate.EXIT_GATE_FAILED), (0, 1, 2, 3, 4))
        self.assertEqual(gate.Refused("x").exit_code, 1)
        self.assertEqual(gate.NotAllowed("x").exit_code, 1)
        self.assertEqual(gate.MergeRefused("x").exit_code, 1)
        self.assertEqual(gate.SomeoneElses("x").exit_code, 2)
        self.assertEqual(gate.MainMoved("x").exit_code, 3)
        self.assertEqual(gate.GateFailed("x").exit_code, 4)

    def test_both_worlds_are_worlds(self):
        from sdlc_protocol.adapters.bare_git import BareGitWorld
        self.assertIsInstance(MemoryWorld(), gate.World)
        self.assertIsInstance(BareGitWorld("/nowhere"), gate.World)


class IssueNumberTest(unittest.TestCase):
    def test_an_issue_may_be_written_as_the_board_writes_it(self):
        self.assertEqual(gate.issue_number("#1"), 1)
        self.assertEqual(gate.issue_number("2"), 2)
        self.assertEqual(gate.issue_number(3), 3)

    def test_an_issue_is_ascii_digits_only_and_padding_is_forgiven(self):
        """str.isdigit took a superscript two, which int() then crashed on,
        and an Arabic-Indic three, which int() read as 3; a 5000-digit
        string crashed on int()'s digit limit. Each is refused with exit 1,
        while "#3" and " 3 " still read as 3."""
        for odd in ("\u00b2", "\u0663", "1" * 5000, "#", "3.0", "-3", "3 #", "one", True):
            with self.subTest(issue=str(odd)[:12]):
                error = refusal(gate.issue_number, odd)
                self.assertIn("issue must be an issue number", str(error))
                self.assertEqual(error.exit_code, 1)
        self.assertEqual(gate.issue_number(" #3 "), 3)
        self.assertEqual(gate.issue_number(" 1 "), 1)


class ClaimTest(unittest.TestCase):
    """Claims touch no world."""

    def test_a_claim_of_your_own_issue_succeeds_once(self):
        queue = new_queue()
        self.assertEqual(gate.claim(ARM, queue, 1, "agent1").claimant, "agent1")
        error = refusal(gate.claim, ARM, queue, 1, "agent1")
        self.assertIn("you already hold #1; carry on with claude/1", str(error))
        self.assertEqual(error.exit_code, 1)

    def test_an_agent_claims_only_its_own_issue(self):
        queue = new_queue()
        error = refusal(gate.claim, ARM, queue, 2, "agent1")
        self.assertRegex(str(error), "#2 was given to agent2.*yours is #1")
        self.assertEqual(error.exit_code, 2)
        self.assertEqual(gate.claim(ARM, queue, 2, "agent2").claimant, "agent2")

    def test_an_agent_given_nothing_is_told_so(self):
        error = refusal(gate.claim, ARM, new_queue(), 1, "agent9")
        self.assertIn("and yours is none", str(error))

    def test_unknown_issues_are_refused(self):
        error = refusal(gate.claim, ARM, new_queue(), 9, "agent1")
        self.assertIn("there is no issue #9; the issues are #1, #2", str(error))
        self.assertEqual(error.exit_code, 1)

    def test_an_open_queue_is_first_come_first_served(self):
        queue = new_queue(open_queue=True)
        gate.claim(ARM, queue, 2, "agent1")
        error = refusal(gate.claim, ARM, queue, 2, "agent2")
        self.assertIn("#2 is claimed by agent1: a claim is first come, first served", str(error))
        self.assertEqual(error.exit_code, 2)
        self.assertEqual(gate.claim(ARM, queue, 1, "agent2").claimant, "agent2")

    def test_of_agents_racing_for_one_issue_under_the_callers_lock_exactly_one_wins(self):
        queue, lock = new_queue((1, 2, 3), open_queue=True), threading.Lock()
        start = threading.Barrier(3)
        answers = {}

        def race(agent):
            start.wait()
            try:
                with lock:
                    answers[agent] = gate.claim(ARM, queue, 3, agent)
            except gate.GateError as error:
                answers[agent] = error

        threads = [threading.Thread(target=race, args=(f"agent{n}",)) for n in (1, 2, 3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        winners = [agent for agent, answer in answers.items() if isinstance(answer, gate.Issue)]
        self.assertEqual(len(answers), 3)
        self.assertEqual(len(winners), 1)
        self.assertEqual(queue.issues[3].claimant, winners[0])
        losers = [answer for agent, answer in answers.items() if agent != winners[0]]
        self.assertEqual([loser.exit_code for loser in losers], [2, 2])

    def test_a_closed_issue_is_claimed_by_nobody(self):
        queue = new_queue()
        queue.issues[1].closed = True
        self.assertIn("#1 is closed: its pull request has merged", str(refusal(gate.claim, ARM, queue, 1, "agent1")))

    def test_labels_follow_the_issue(self):
        issue = gate.Issue(1, "agent1", "Greeting")
        self.assertEqual(issue.label(), "todo")
        issue.claimant = "agent1"
        self.assertEqual(issue.label(), "claimed")
        issue.claim_released = True
        self.assertEqual(issue.label(), "in-review")
        issue.closed = True
        self.assertEqual(issue.label(), "closed")


class Flow(unittest.TestCase):
    """Two agents, two issues, one world in memory."""

    policy = ARM

    def setUp(self):
        self.world = MemoryWorld()
        self.queue = new_queue()

    def claim_push_and_open(self, number, standing=gate.CLEAN, conflicts=()):
        agent = f"agent{number}"
        gate.claim(self.policy, self.queue, number, agent)
        self.world.push(f"claude/{number}", standing, conflicts)
        return gate.open_pull(self.policy, self.world, self.queue, number, agent)

    def ready(self, number):
        return gate.mark_ready(self.policy, self.world, self.queue, number, f"agent{number}")

    def both_change_the_same_line(self):
        """agent1 lands first; agent2's change to the same line conflicts."""
        self.claim_push_and_open(1)
        self.claim_push_and_open(2, gate.CONFLICTING, ("app.py",))
        self.assertTrue(self.ready(1).merged)


class OpenPullTest(Flow):
    def test_a_pull_request_needs_the_claim_and_the_pushed_branch(self):
        unclaimed = refusal(gate.open_pull, ARM, self.world, self.queue, 1, "agent1")
        self.assertIn("nobody has claimed it", str(unclaimed))
        self.assertEqual(unclaimed.exit_code, 1)
        gate.claim(ARM, self.queue, 1, "agent1")
        unpushed = refusal(gate.open_pull, ARM, self.world, self.queue, 1, "agent1")
        self.assertIn("claude/1 is not on team yet; push it first (git push -u team claude/1)", str(unpushed))
        self.assertEqual(unpushed.exit_code, 1)
        self.world.push("claude/1")
        pull = gate.open_pull(ARM, self.world, self.queue, 1, "agent1")
        self.assertEqual((pull.issue, pull.author, pull.title, pull.state), (1, "agent1", "Greeting", "draft"))
        again = refusal(gate.open_pull, ARM, self.world, self.queue, 1, "agent1")
        self.assertIn("#1 already has a pull request (draft); one issue, one pull request", str(again))
        theirs = refusal(gate.open_pull, ARM, self.world, self.queue, 1, "agent2")
        self.assertIn("claimed by agent1", str(theirs))
        self.assertEqual(theirs.exit_code, 2)

    def test_a_pull_request_keeps_the_title_its_author_gives(self):
        gate.claim(ARM, self.queue, 1, "agent1")
        self.world.push("claude/1")
        gate.open_pull(ARM, self.world, self.queue, 1, "agent1", title="Greet the world")
        self.assertEqual(self.queue.pulls[1].title, "Greet the world")

    def test_adding_the_pull_checks_again_for_a_caller_that_let_go_of_its_lock(self):
        queue = new_queue(open_queue=True)
        gate.claim(ARM, queue, 1, "agent1")
        gate.can_open_pull(ARM, queue, 1, "agent1")
        gate.add_pull(ARM, queue, 1, "agent1")
        self.assertIn("one issue, one pull request", str(refusal(gate.add_pull, ARM, queue, 1, "agent1")))


class MarkReadyTest(Flow):
    def test_a_clean_pull_request_merges_and_closes_its_issue(self):
        self.claim_push_and_open(1)
        tip, before = self.world.branch_sha("claude/1"), self.world.main_sha()
        outcome = self.ready(1)
        main = self.world.main_sha()
        self.assertEqual((outcome.result, outcome.exit_code), ("merged", 0))
        self.assertEqual(outcome.text, f"merged: claude/1 is in main as {main[:7]}, and #1 is closed.")
        self.assertEqual((outcome.branch_commit, outcome.main_before, outcome.main), (tip, before, main))
        self.assertEqual(self.world.merges, [("claude/1", main)])
        self.assertEqual((self.queue.pulls[1].state, self.queue.pulls[1].attempts), ("merged", 1))
        self.assertEqual(self.queue.issues[1].label(), "closed")
        self.assertIn("already merged", str(refusal(self.ready, 1)))
        self.assertIn("#1 is closed", str(refusal(gate.claim, ARM, self.queue, 1, "agent1")))

    def test_a_conflicting_pull_request_answers_main_moved_and_leaves_main(self):
        self.both_change_the_same_line()
        before = self.world.main_sha()
        outcome = self.ready(2)
        self.assertEqual((outcome.result, outcome.exit_code), ("main moved", 3))
        self.assertTrue(outcome.text.startswith(MAIN_MOVED + "."))
        self.assertIn("Conflicting files: app.py.", outcome.text)
        self.assertIn(f"main is still at {before[:7]}; the gate left it as it was.", outcome.text)
        self.assertEqual(outcome.conflicts, ("app.py",))
        self.assertEqual(self.world.main_sha(), before)
        self.assertEqual(self.queue.pulls[2].state, "main moved")
        self.assertEqual(self.queue.pulls[2].conflicts, ("app.py",))
        self.assertEqual(self.queue.issues[2].label(), "in-review", "a ready pull request releases its claim")

    def test_after_bringing_main_in_and_pushing_the_pull_request_merges(self):
        self.both_change_the_same_line()
        self.assertEqual(self.ready(2).exit_code, 3)
        self.world.push("claude/2", gate.CLEAN)
        outcome = self.ready(2)
        self.assertTrue(outcome.merged)
        self.assertEqual(self.queue.pulls[2].attempts, 2)
        self.assertEqual([branch for branch, _ in self.world.merges], ["claude/1", "claude/2"])

    def test_a_branch_behind_main_still_merges(self):
        self.claim_push_and_open(1)
        self.claim_push_and_open(2)
        self.ready(1)
        self.assertEqual(self.world.standing("claude/2"), gate.BEHIND, "main moved past it")
        self.assertTrue(self.ready(2).merged)

    def test_only_the_author_marks_ready_and_only_a_branch_with_new_work(self):
        self.claim_push_and_open(1, gate.NOTHING_NEW)
        not_theirs = refusal(gate.mark_ready, ARM, self.world, self.queue, 1, "agent2")
        self.assertIn("only its author marks it ready", str(not_theirs))
        self.assertEqual(not_theirs.exit_code, 2)
        nothing_new = refusal(self.ready, 1)
        self.assertIn("nothing to merge: every commit on claude/1 is already in main", str(nothing_new))
        self.assertEqual(nothing_new.exit_code, 1)
        self.assertEqual(self.queue.pulls[1].state, "draft")
        self.assertEqual(self.queue.issues[1].label(), "claimed", "a refused ready keeps the claim")
        no_pull = refusal(self.ready, 2)
        self.assertIn("#2 has no pull request; open it once claude/2 is pushed", str(no_pull))

    def test_a_branch_gone_from_the_remote_is_refused_before_anything_changes(self):
        self.claim_push_and_open(1)
        del self.world.branches["claude/1"]
        error = refusal(self.ready, 1)
        self.assertIn("claude/1 is not on team; push it first: git push -u team claude/1", str(error))
        self.assertEqual(self.queue.pulls[1].state, "draft")

    def test_main_moving_during_the_merge_is_main_moved_without_conflicts(self):
        self.claim_push_and_open(1)
        main = self.world.main_sha()
        self.world.fail_next_merge(gate.MainMoved("the push of main was refused: rejected", main))
        outcome = self.ready(1)
        self.assertEqual(outcome.exit_code, 3)
        self.assertIn("Why: the push of main was refused: rejected.", outcome.text)
        self.assertEqual(self.queue.pulls[1].state, "main moved")

    def test_a_merge_refused_for_another_reason_exits_1_and_stays_ready(self):
        self.claim_push_and_open(1)
        self.world.fail_next_merge(gate.MergeRefused("fatal: refusing to merge unrelated histories"))
        outcome = self.ready(1)
        self.assertEqual((outcome.result, outcome.exit_code), ("not merged", 1))
        self.assertEqual(outcome.text, "the merge gate could not merge claude/1 into main: fatal: refusing to "
                                       "merge unrelated histories. main was left as it was; fix what git names, "
                                       "push, and call mark_ready again.")
        self.assertEqual(self.queue.pulls[1].state, "ready")

    def test_a_gate_that_fails_during_the_merge_exits_4_with_the_claim_released(self):
        self.claim_push_and_open(1)
        self.world.fail_next_merge(gate.GateFailed("the merge gate's git checkout failed: disk full"))
        outcome = self.ready(1)
        self.assertEqual((outcome.result, outcome.exit_code), ("gate failed", 4))
        self.assertEqual(outcome.text, "the merge gate's git checkout failed: disk full")
        self.assertEqual((self.queue.pulls[1].state, self.queue.pulls[1].attempts), ("ready", 1))
        self.assertEqual(self.queue.issues[1].label(), "in-review")

    def test_a_world_that_cannot_be_read_fails_before_anything_changes(self):
        self.claim_push_and_open(1)
        error = refusal(gate.mark_ready, ARM, FailingWorld(), self.queue, 1, "agent1")
        self.assertIsInstance(error, gate.GateFailed)
        self.assertEqual(error.exit_code, 4)
        self.assertEqual((self.queue.pulls[1].state, self.queue.pulls[1].attempts), ("draft", 0))

    def test_a_remote_without_main_fails(self):
        self.claim_push_and_open(1)
        self.world.main_commit = ""
        error = refusal(self.ready, 1)
        self.assertIn("the remote has no branch main", str(error))
        self.assertEqual(error.exit_code, 4)

    def test_the_parts_do_what_mark_ready_does_for_a_caller_holding_its_own_locks(self):
        self.claim_push_and_open(1)
        pull = gate.pull_to_ready(ARM, self.queue, 1, "agent1")
        attempt = gate.prepare(ARM, self.world, 1)
        self.assertEqual((attempt.issue, attempt.branch, attempt.tip, attempt.main),
                         (1, "claude/1", self.world.branch_sha("claude/1"), self.world.main_sha()))
        gate.begin_attempt(self.queue, pull)
        self.assertEqual((pull.state, pull.attempts, self.queue.issues[1].label()), ("ready", 1, "in-review"))
        merge = gate.attempt_merge(self.world, attempt)
        self.assertTrue(merge.merged)
        self.assertEqual(self.queue.pulls[1].state, "ready", "attempt_merge leaves the queue alone")
        outcome = gate.record_outcome(ARM, self.queue, attempt, merge)
        self.assertTrue(outcome.merged)
        self.assertEqual(self.queue.pulls[1].merge_commit, merge.main)


class RebasePolicyTest(Flow):
    policy = replace(ARM, bring_main_in=gate.REBASE, remote="origin")

    def test_main_moved_tells_an_account_to_rebase(self):
        self.both_change_the_same_line()
        outcome = self.ready(2)
        self.assertTrue(outcome.text.startswith("main moved: rebase your branch on origin/main, resolve the "
                                                "conflicts, push it with --force-with-lease, and call "
                                                "mark_ready again."))
        status = gate.pull_status(self.policy, self.world, self.queue, 2)
        self.assertIn("Rebase your branch on origin/main, resolve the conflicts and push it with "
                      "--force-with-lease before mark_ready.", status)


class RequiredCheckTest(Flow):
    policy = replace(ARM, required_check="check")

    def test_the_gate_merges_only_on_a_green_check_and_changes_nothing_before(self):
        self.claim_push_and_open(1)
        for state, words in ((gate.NO_CHECK, "the check check has not reported on claude/1"),
                             (gate.PENDING, "the check check is still running on claude/1"),
                             (gate.RED, "the check check failed on claude/1; fix what it reports")):
            with self.subTest(check=state):
                self.world.set_check("claude/1", state)
                error = refusal(self.ready, 1)
                self.assertIn(words, str(error))
                self.assertEqual(error.exit_code, 1)
                self.assertEqual((self.queue.pulls[1].state, self.queue.pulls[1].attempts), ("draft", 0))
                self.assertEqual(self.queue.issues[1].label(), "claimed")
        self.world.set_check("claude/1", gate.GREEN)
        self.assertTrue(self.ready(1).merged)

    def test_without_a_required_check_a_world_without_checks_merges(self):
        self.assertEqual(self.world.check("claude/1"), gate.NO_CHECK)
        self.policy = ARM
        self.claim_push_and_open(1)
        self.assertTrue(self.ready(1).merged)


class OwnerMergesTest(Flow):
    policy = replace(ARM, who_merges=gate.OWNER)

    def test_ready_releases_the_claim_and_waits_for_the_owner(self):
        self.claim_push_and_open(1)
        self.world.set_check("claude/1", gate.RED)
        outcome = self.ready(1)
        self.assertEqual((outcome.result, outcome.exit_code), ("ready", 0))
        self.assertEqual(outcome.text, "ready: claude/1 waits for the owner to merge it into main, and your "
                                       "claim on #1 is released.")
        self.assertEqual(self.world.merges, [], "only the owner moves main")
        self.assertEqual((self.queue.pulls[1].state, self.queue.pulls[1].attempts), ("ready", 0))
        self.assertEqual(self.queue.issues[1].label(), "in-review")

    def test_the_owners_merge_is_recorded_as_the_world_reports_it(self):
        self.claim_push_and_open(1)
        self.ready(1)
        gate.record_merged(self.queue, 1, "f" * 40)
        self.assertEqual((self.queue.pulls[1].state, self.queue.pulls[1].merge_commit), ("merged", "f" * 40))
        self.assertEqual(self.queue.issues[1].label(), "closed")

    def test_the_board_says_who_moves_main(self):
        self.assertIn("only the owner moves main.", gate.current_board(self.policy, self.world, self.queue))


class AutoMergeTest(Flow):
    policy = replace(ARM, who_merges=gate.AUTO_MERGE, required_check="check")

    def test_a_ready_pull_request_lands_once_its_check_is_green(self):
        self.claim_push_and_open(1)
        self.world.set_check("claude/1", gate.PENDING)
        outcome = self.ready(1)
        self.assertEqual(outcome.text, "ready: claude/1 merges by itself once check is green and it merges "
                                       "cleanly into main, and your claim on #1 is released.")
        self.assertIn("still running", str(refusal(gate.land, self.policy, self.world, self.queue, 1)))
        self.world.set_check("claude/1", gate.GREEN)
        landed = gate.land(self.policy, self.world, self.queue, 1)
        self.assertTrue(landed.merged)
        self.assertEqual(self.queue.pulls[1].attempts, 1)
        self.assertIn("already merged", str(refusal(gate.land, self.policy, self.world, self.queue, 1)))

    def test_a_draft_does_not_land(self):
        self.claim_push_and_open(1)
        error = refusal(gate.land, self.policy, self.world, self.queue, 1)
        self.assertIn("the pull request for #1 is a draft", str(error))

    def test_main_moved_tells_a_member_to_merge_main_in_and_push(self):
        self.both_change_the_same_line_landing_one()
        self.world.set_check("claude/2", gate.GREEN)
        outcome = gate.land(self.policy, self.world, self.queue, 2)
        self.assertTrue(outcome.text.startswith("main moved: merge team/main into your branch, resolve the "
                                                "conflicts, and push."))

    def both_change_the_same_line_landing_one(self):
        self.claim_push_and_open(1)
        self.claim_push_and_open(2, gate.CONFLICTING, ("app.py",))
        self.world.set_check("claude/1", gate.GREEN)
        self.ready(1)
        self.ready(2)
        self.assertTrue(gate.land(self.policy, self.world, self.queue, 1).merged)


class StatusTest(Flow):
    def status(self, number, world=None):
        return gate.pull_status(ARM, world or self.world, self.queue, number)

    def test_an_issue_without_a_pull_request_says_who_holds_it(self):
        self.assertEqual(self.status(1), "#1 has no pull request yet; the issue is not claimed yet (todo).")
        gate.claim(ARM, self.queue, 1, "agent1")
        self.assertEqual(self.status(1), "#1 has no pull request yet; the issue is claimed by agent1 (claimed).")

    def test_status_reads_how_the_branch_stands_to_main(self):
        self.both_change_the_same_line()
        merged = self.status(1)
        self.assertIn("Pull request for #1: claude/1 -> main, by agent1, merged. Merge attempts: 1.", merged)
        self.assertIn(f"Merged into main as {self.world.main_sha()[:7]}.", merged)
        conflicting = self.status(2)
        self.assertIn("by agent2, draft", conflicting)
        self.assertIn("conflicting; main moved and claude/2 no longer merges cleanly with it "
                      "(conflicts in app.py). Merge team/main into your branch, resolve the conflicts and "
                      "push before mark_ready.", conflicting)

    def test_the_gates_last_answer_is_kept_until_the_next(self):
        self.both_change_the_same_line()
        self.ready(2)
        status = self.status(2)
        self.assertIn("by agent2, main moved", status)
        self.assertIn("The gate's last answer was main moved, with conflicts in app.py.", status)

    def test_each_standing_has_its_sentence(self):
        self.claim_push_and_open(1)
        main = self.world.main_sha()[:7]
        sentences = {
            gate.CLEAN: f"Against main at {main}: clean; it merges cleanly with main as it stands.",
            gate.BEHIND: f"Against main at {main}: behind; main moved since claude/1 branched, but it still "
                         f"merges cleanly, so the gate can merge it as it is.",
            gate.NOTHING_NEW: f"Against main at {main}: nothing new; every commit on claude/1 is already in main.",
            gate.UNKNOWN: f"Against main at {main}: unknown.",
        }
        for word, sentence in sentences.items():
            with self.subTest(standing=word):
                self.world.set_standing("claude/1", word)
                self.assertTrue(self.status(1).endswith(sentence), self.status(1))
        del self.world.branches["claude/1"]
        self.assertTrue(self.status(1).endswith(f"Against main at {main}: claude/1 is not on team; push it."))

    def test_a_world_that_cannot_be_read_is_unknown(self):
        self.claim_push_and_open(1)
        self.assertIn("Against main: unknown; the gate could not read the shared remote.",
                      self.status(1, FailingWorld()))


class BoardTest(Flow):
    def board(self, world=None):
        return gate.current_board(ARM, world or self.world, self.queue)

    @staticmethod
    def row(text, number):
        """The Issues table's row for one issue."""
        issues = text.split("## Issues")[1].split("## Claims")[0]
        return next(line for line in issues.splitlines() if line.startswith(f"| #{number} |"))

    def test_the_board_shows_issues_claims_and_pull_requests(self):
        self.both_change_the_same_line()
        self.ready(2)
        text = self.board()
        main = self.world.main_sha()
        self.assertIn(f"As of commit {main[:7]} on main; the run began at the base ref 0a1b2c3. One issue, one "
                      f"branch claude/<issue>, one pull request; only the merge gate moves main.", text)
        self.assertEqual(self.row(text, 1), "| #1 | Greeting | agent1 | closed |")
        self.assertEqual(self.row(text, 2), "| #2 | Farewell | agent2 | in-review |")
        claims = text.split("## Claims")[1].split("## Pull requests")[0]
        self.assertIn("| (none) |", claims, "a ready pull request has released its claim")
        self.assertIn(f"| #1 | claude/1 | agent1 | merged | merged as {main[:7]} | 1 |", text)
        self.assertIn("| #2 | claude/2 | agent2 | main moved | conflicting (app.py) | 1 |", text)
        self.assertIn("| (none) |", text.split("## Queue")[1])

    def test_the_board_lists_held_claims_and_the_queue(self):
        gate.claim(ARM, self.queue, 1, "agent1")
        text = self.board()
        self.assertIn("| #1 | claude/1 | agent1 | none yet |", text)
        self.assertIn("| #2 | Farewell | agent2 |", text.split("## Queue")[1])
        self.assertIn("| (none) |", text.split("## Pull requests")[1].split("## Queue")[0])

    def test_the_tables_keep_their_shape(self):
        text = self.board()
        self.assertIn("## Issues (one per feature)\n\n| Issue | Title | Given to | Label |\n| --- | --- | --- | --- |",
                      text)
        self.assertIn("| Issue | Branch | Author | State | Against main | Merge attempts |\n"
                      "| --- | --- | --- | --- | --- | --- |\n| (none) |", text)

    def test_a_pipe_in_a_title_keeps_the_table_whole(self):
        self.queue.issues[1].title = "Read | write"
        self.assertEqual(self.row(self.board(), 1), "| #1 | Read \\| write | agent1 | todo |")

    def test_a_world_that_cannot_be_read_is_unknown_on_the_board(self):
        self.claim_push_and_open(1)
        text = self.board(FailingWorld())
        self.assertIn("As of commit unknown on main", text)
        self.assertIn("| #1 | claude/1 | agent1 | draft | unknown | 0 |", text)

    def test_only_open_branches_are_read(self):
        self.both_change_the_same_line()
        self.assertEqual(gate.open_branches(ARM, self.queue), ["claude/2"])


class ReleaseTest(unittest.TestCase):
    def test_a_claim_is_due_for_release_after_the_policys_idle_time_by_the_worlds_clock(self):
        world = MemoryWorld()
        last_commits = {"claude/2": world.now(), "claude/10": world.now() - timedelta(hours=2)}
        self.assertEqual(gate.due_for_release(ARM, world, last_commits), [])
        world.advance(hours=23)
        self.assertEqual(gate.due_for_release(ARM, world, last_commits), ["claude/10"])
        world.advance(hours=1, seconds=1)
        self.assertEqual(gate.due_for_release(ARM, world, last_commits), ["claude/2", "claude/10"])
        quick = replace(ARM, idle_after=timedelta(hours=1))
        self.assertEqual(gate.due_for_release(quick, world, {"claude/3": world.now() - timedelta(hours=2)}),
                         ["claude/3"])

    def test_a_claim_released_already_is_not_due_again(self):
        world = MemoryWorld()
        last_commits = {"claude/2": world.now()}
        world.advance(hours=25)
        released = {"claude/2": world.now()}
        world.advance(hours=1)
        self.assertEqual(gate.due_for_release(ARM, world, last_commits, released), [])
        self.assertEqual(gate.due_for_release(ARM, world, last_commits), ["claude/2"])


if __name__ == "__main__":
    unittest.main()
