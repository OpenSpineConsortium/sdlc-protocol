"""The harness's guarantees (validation/invariants.py) on hand-written
traces: an honest release and reclaim breaks none; the 0.4.1 fixes hold;
a run in two halves is judged by the states its halves read; the open
finding F2 shows as soon as the oracle reads the spec; F7's careless
lease is refused, F9's draft misses "main moved" under the mutant that
takes GitHub's opaque word alone, and is told it while level with main
under the one that reads the comparison's other count, and F12's lost run
tells "main moved" under its mutant alone, by the run the concurrency
group cancels.
The mutants, the prototype's among them, are
tests/test_validation_mutants.py's."""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol.adapters import github  # noqa: E402
from sdlc_protocol import board, guard  # noqa: E402
from validation import cycle, invariants, model as M, mutants, oracle, verbs  # noqa: E402
from validation.fake_github import Call  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

BOARD = Verb("board", "board", (cycle.SCHEDULE, ""))
SHARED = Scope(issues=(1, 2), files=((1, ("src/shared.py",)), (2, ("src/shared.py",))))


def board_with(*faults, event=cycle.SCHEDULE, ref_name=""):
    return Verb("board", "board", (event, ref_name, faults))


def run(event, ref_name=""):
    """A run in two halves with nothing between them."""
    return [Verb("board-start", "board", (event, ref_name)), Verb("board-finish", "board")]


def violations(steps, scope=Scope(), known=oracle.AS_BUILT, issues=(1,), labels=("todo",), opaque_pulls=False):
    model = M.seeded(issues or scope.issues, writers=("a", "b", "c", verbs.CAROL, verbs.DANA), labels=labels)
    # As validation/run.py sets it from a scenario: with it on the fake
    # answers blocked for every mergeable pull request, as GitHub does
    # under a required check (F9).
    model.opaque_pulls = opaque_pulls
    cycle.board_run(model)
    found = []
    for step in steps:
        before = model.clone()
        outcome = verbs.apply(model, step, scope)
        found += invariants.step_checks(before, model, step, outcome, known)
    return found


def guarantees(found):
    return sorted({v.guarantee for v in found})


IDLE_RELEASE = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
                Verb("plan", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("commit", "a", (1,)),
                Verb("wait", "clock", (24,)), BOARD, Verb("wait", "clock", (1,)), BOARD,
                Verb("wait", "clock", (1,)), BOARD, BOARD]


class AnHonestRun(unittest.TestCase):
    def test_an_idle_claim_released_once_then_claimed_again_breaks_nothing(self):
        steps = IDLE_RELEASE + [Verb("claim", "c", (1,)), Verb("label-on", "c", (1, "claimed")),
                                Verb("label-on", "c", (1, "worker:c")), BOARD]
        self.assertEqual(violations(steps), [])

    def test_a_person_releases_by_hand_a_stranger_cannot_and_a_holder_who_comes_back_keeps_it(self):
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("marker", M.OWNER, (1,)),
                 Verb("label-off", M.OWNER, (1, "claimed")), BOARD, Verb("wait", "clock", (25,)),
                 Verb("marker", verbs.SAM, (1,)), Verb("label-on", "a", (1, "claimed")), Verb("marker", M.OWNER, (1,)),
                 Verb("label-off", M.OWNER, (1, "claimed")), Verb("commit", "a", (1,)), BOARD,
                 Verb("wait", "clock", (25,)), BOARD]
        self.assertEqual(violations(steps, Scope(people=(M.OWNER, verbs.SAM))), [])

    def test_a_finished_draft_waits_and_a_person_s_release_of_it_counts(self):
        """F10, fixed in 0.4.1."""
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("open-draft", "a", (1,)),
                 Verb("finished", "a", (100,)), Verb("wait", "clock", (50,)), BOARD, Verb("marker", M.OWNER, (1,)),
                 Verb("label-off", M.OWNER, (1, "claimed")), BOARD]
        self.assertEqual(violations(steps), [])


class TheWorkerCell(unittest.TestCase):
    """G1 holds the board's Worker cell to the branch's holder by the claim
    file, and, once the finishing push has deleted the file, to the issue's
    worker label, which the spec leaves the board; the soak's G1 leads
    (seeds 585 and 959) were a closed issue's leftover branch whose stale
    label named the worker that had merged."""

    def claim(self, pusher, named, labels=("todo",)):
        model = M.seeded((1,), writers=("a", "b", "e"), labels=labels)
        tree = model.tree(model.refs[M.MAIN])
        tree["docs/agents/claims/1.md"] = M.claim_text(1, named)
        model.push(pusher, "claude/1", model.commit((model.refs[M.MAIN],), pusher, tree, "Claim #1"))
        return model

    def drop_file(self, model, pusher):
        tree = model.tree(model.refs["claude/1"])
        del tree["docs/agents/claims/1.md"]
        model.push(pusher, "claude/1", model.commit((model.refs["claude/1"],), pusher, tree, "Finish #1"))

    def test_the_cell_names_the_holder_by_the_claim_file(self):
        self.assertEqual(invariants.one_name_one_holder(self.claim("b", "b")), [])
        found = invariants.one_name_one_holder(self.claim("b", "e"))
        self.assertEqual([v.guarantee for v in found], ["G1"])
        self.assertIn("names e for claude/1, whose holder is b", found[0].detail)

    def test_once_the_file_is_gone_the_cell_names_the_issue_s_label_or_says_there_is_none(self):
        model = self.claim("b", "b", labels=("todo", "worker:e"))
        self.drop_file(model, "b")
        self.assertEqual(invariants.one_name_one_holder(model), [])
        model.issues[1].state = "closed"
        self.assertEqual(invariants.one_name_one_holder(model), [])
        model = self.claim("b", "b")
        self.drop_file(model, "b")
        self.assertEqual(invariants.one_name_one_holder(model), [])

    def test_a_board_that_shows_another_cell_than_the_rule_owes_is_seen(self):
        """The cell is held to one value: a board that drops the label once
        the file is gone, or says there is no claim file while one is
        there, breaks G1."""
        no_file = mock.patch.object(invariants.board, "claim_row",
                                    lambda key, branch, text, pull, issue: ["", branch, board.NO_CLAIM_FILE, "", ""])
        model = self.claim("b", "b", labels=("todo", "worker:e"))
        self.drop_file(model, "b")
        with no_file:
            found = invariants.one_name_one_holder(model)
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G1", "the board names no claim file yet for claude/1, whose claim file is gone and whose "
                                 "issue's label names e")])
        with no_file:
            found = invariants.one_name_one_holder(self.claim("b", "b"))
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G1", "the board names no claim file yet for claude/1, whose holder is b")])

    def test_the_oracle_reads_the_worker_label_by_itself(self):
        self.assertEqual(oracle.label_worker(["todo", "account:main", "worker:e"]), "e")
        self.assertEqual(oracle.label_worker(["agent:w1", "todo"]), "w1")
        self.assertIsNone(oracle.label_worker(["todo", "worker:"]))


class TheProtocolCheckIsReadAsGitHubReadsABody(unittest.TestCase):
    def test_a_body_closing_another_issue_or_in_another_keyword(self):
        """G12's "the body closes its issue" is GitHub's reading: any closing
        keyword in any case, and #12 does not close #1."""
        for body, closes in (("Closes #1", True), ("closes #1", True), ("Fixes #1", True), ("Closes #12", False),
                             ("Resolves #1.", True), ("See #1", False)):
            with self.subTest(body=body):
                model = M.seeded()
                verbs.apply(model, Verb("claim", "a", (1,)))
                verbs.apply(model, Verb("open-draft", "a", (1,)))
                model.pulls[100].body = body
                found = invariants.protocol_check_agrees(model)
                self.assertEqual(found, [], body)
                self.assertEqual(re.search(invariants.CLOSES % 1, body) is not None, closes)


class TheFixesOf041Hold(unittest.TestCase):
    def test_a_refused_label_removal_leaves_the_claim_whole_and_the_next_run_releases_it(self):
        """F1."""
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (25,)),
                 board_with("label-delete-500"), BOARD]
        self.assertEqual(violations(steps), [])

    def test_a_refused_release_comment_puts_the_labels_back(self):
        for fault in ("comment-500", "comment-rate-limited", "comment-lost"):
            with self.subTest(fault=fault):
                steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")),
                         Verb("label-on", "a", (1, "worker:a")), Verb("wait", "clock", (25,)), board_with(fault), BOARD]
                self.assertEqual(violations(steps), [])

    def test_a_refused_rename_is_said_once_and_tried_again(self):
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (25,)), board_with("rename-403"), BOARD,
                 board_with("rename-422"), BOARD]
        self.assertEqual(violations(steps), [])

    def test_a_misnamed_claim_is_refused_by_the_guard_and_claims_nothing(self):
        """F5."""
        steps = [Verb("claim", "a", (1,)), Verb("claim-misnamed", "b", (1, "claude/01")),
                 Verb("push-by-hand", M.OWNER, (1, "claude/01")), Verb("wait", "clock", (25,)), BOARD]
        self.assertEqual(violations(steps), [])


class ARunInTwoHalves(unittest.TestCase):
    HELD = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
            Verb("wait", "clock", (25,)), Verb("board-start", "board", (cycle.SCHEDULE, ""))]
    SPLIT = Scope(split_board=True)

    def test_the_holder_back_between_the_halves_keeps_the_claim_and_breaks_nothing(self):
        """S09: the rename the oracle owes from the state the run started
        from is rightly left alone, with the word on the issue."""
        steps = self.HELD + [Verb("commit", "a", (1,)), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])
        with mock.patch.object(github, "free_name", mutants.renamed_unread):
            self.assertEqual(sorted({(v.guarantee, v.detail) for v in violations(steps, self.SPLIT)}),
                             [("G10", "made and not owed: ('comment', 1, 'released')"),
                              ("G3", "made and not owed: ('rename', 'claude/1', 'released/1-6d6ae03')")])

    def test_a_release_undone_between_the_halves_is_the_holder_s_not_the_run_s(self):
        """The holder puts claimed back after the first half took it off:
        the run released the claim, and what the issue carries at the end
        is not the run's doing (G8)."""
        steps = self.HELD + [Verb("label-on", "a", (1, "claimed")), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])

    def test_main_moved_between_the_halves_owes_the_next_run_nothing_new_as_built(self):
        steps = self.HELD + [Verb("move-main", M.OWNER), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])

    def test_a_second_run_s_quiet_is_asked_only_when_nothing_changed_between_the_halves(self):
        """A draft opened between the halves makes the next run owe the
        overlap comments the first half could not know of; with nothing
        between the halves, a second run that writes again is red as a
        whole one is."""
        steps = [Verb("claim", "a", (1,)), Verb("claim", "b", (2,)), Verb("board-start", "board", (cycle.SCHEDULE, "")),
                 Verb("open-draft", "a", (1,)), Verb("board-finish", "board")]
        scope = SHARED._replace(split_board=True)
        self.assertEqual(violations(steps, scope, issues=None), [])
        checks = invariants.cycle_checks

        def asked_anyway(before, after, record, known=oracle.AS_BUILT):
            return checks(before, after, record, known) + invariants.quiet_second_run(after, record)

        with mock.patch.object(invariants, "cycle_checks", asked_anyway):
            self.assertEqual(guarantees(violations(steps, scope, issues=None)), ["G9"])
        told_again = mutants.MUTANTS["overlaps told on every run"]
        quiet = steps[:3] + [Verb("board-finish", "board"), Verb("board-start", "board", (cycle.SCHEDULE, "")),
                             Verb("board-finish", "board")]
        with mock.patch.object(told_again.module, told_again.name, told_again.replacement):
            self.assertIn("G9", guarantees(violations(quiet, scope, issues=None)))

    def test_the_second_half_s_writes_are_judged_whatever_their_kind(self):
        """The second half's writes are the calls after the first half's,
        so a label it takes off, the first half's kind of write, is made
        and not owed (G3), where a split by kind would judge it by neither
        half."""
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")),
                 Verb("board-start", "board", (cycle.SCHEDULE, "")), Verb("board-finish", "board")]
        self.assertEqual(violations(steps, self.SPLIT), [])
        releases = github.release_claims

        def claimed_off_too(api, snapshot):
            lines = releases(api, snapshot)
            api.remove_label(1, "claimed")
            return lines

        with mock.patch.object(github, "release_claims", claimed_off_too):
            found = violations(steps, self.SPLIT)
        self.assertIn(("G3", "made and not owed: ('label-off', 1, 'claimed')"), [(v.guarantee, v.detail) for v in found])
        self.assertEqual(guarantees(found), ["G3", "G9"])

    def test_the_first_half_is_judged_when_it_is_made(self):
        """A search judges a first half at its last level as it judges a
        whole run: a release made at exactly 24 hours is red on the
        board-start step, not two steps later."""
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (24,)),
                 Verb("board-start", "board", (cycle.SCHEDULE, ""))]
        self.assertEqual(violations(steps, self.SPLIT), [])
        with mock.patch.object(github.claims, "is_idle", mutants.MUTANTS["idle at exactly 24 hours"].replacement):
            self.assertEqual(guarantees(violations(steps, self.SPLIT)), ["G2", "G3", "G5"])


class TheOpenFindingsShow(unittest.TestCase):
    """With its switch off the oracle reads the spec, and the finding is red."""

    def test_f2_overlapping_work_told_again_once_a_claim_opens_its_pull_request(self):
        steps = [Verb("claim", "a", (1,)), Verb("claim", "b", (2,)), BOARD, Verb("open-draft", "a", (1,)), BOARD]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        self.assertEqual(guarantees(violations(steps, SHARED, oracle.AS_BUILT - {"F2"}, issues=None)), ["G7"])

    def test_f9_a_draft_behind_main_hears_main_moved_once_its_standing_is_read(self):
        """Fixed after 0.4.1: with the model's opaque_pulls on GitHub's word
        for the draft is blocked, as for a draft whose required check has
        not passed, and the adapter reads how its branch stands through
        mergeable and one compare, so the run owes the draft the word only
        once main has moved; under the mutant that takes GitHub's word
        alone nothing is told, and under the one that reads the
        comparison's ahead_by the draft, its claim commit ahead and level
        with main, is told before main moves. With opaque_pulls off the
        fake answers behind itself and the mutants have nothing to bite,
        so the finding is checked with it on."""
        level = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), board_with(event=cycle.PULL_REQUEST)]
        steps = level + [Verb("move-main", M.OWNER), board_with(event=cycle.PUSH, ref_name=M.MAIN)]
        self.assertEqual(violations(steps), [])
        self.assertEqual(violations(steps, opaque_pulls=True), [])
        with mutants.switched_on(mutants.MUTANTS["an opaque state read by GitHub's word alone"]):
            self.assertEqual(guarantees(violations(level, opaque_pulls=True)), [])
            self.assertEqual(guarantees(violations(steps, opaque_pulls=True)), ["G10"])
        with mutants.switched_on(mutants.MUTANTS["the comparison read by ahead_by"]):
            self.assertEqual(guarantees(violations(level, opaque_pulls=True)), ["G10"])

    def test_f12_main_moved_is_told_by_whichever_run_finds_the_branch_behind(self):
        """Fixed after 0.4.1; the mutant keys the word on the push's run again."""
        steps = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)),
                 Verb("move-main", M.OWNER), board_with(event=cycle.PULL_REQUEST)]
        self.assertEqual(violations(steps), [])
        with mutants.switched_on(mutants.MUTANTS["main moved told only in the push's run"]):
            self.assertEqual(guarantees(violations(steps)), ["G10"])

    def test_f12_the_run_of_the_push_to_main_is_cancelled_behind_the_worker_s_own_push(self):
        """The mechanism (DESIGN.md, F12): the worker's push to the claim
        branch, with its pull request marked ready, holds the group while
        its job is skipped, main moves behind it, the worker's next push
        replaces the run that waited, and the runs that follow tell the
        pull request, since the word depends on the state (F12's fix);
        under the mutant that keys it on the push's run they owe it and
        stay silent."""
        scope = Scope(events=cycle.EVENTS, split_board=True)
        steps = ([Verb("claim", "a", (1,))] + run(cycle.CREATE, "claude/1") + run(cycle.PUSH, "claude/1")
                 + [Verb("open-draft", "a", (1,))] + run(cycle.PULL_REQUEST) + [Verb("mark-ready", "a", (100,)),
                 Verb("commit", "a", (1,)), Verb("board-start", "board", (cycle.PUSH, "claude/1")),
                 Verb("move-main", M.OWNER), Verb("commit", "a", (1,)), Verb("board-finish", "board")]
                 + run(cycle.PULL_REQUEST) + run(cycle.PUSH, "claude/1"))
        self.assertEqual(violations(steps, scope), [])
        with mutants.switched_on(mutants.MUTANTS["main moved told only in the push's run"]):
            self.assertEqual(guarantees(violations(steps, scope)), ["G10"])

    def test_f7_a_lease_after_a_fetch_onto_another_s_claim_is_refused(self):
        """Fixed after 0.4.1: git would take the lease (test_validation_model),
        and the guard refuses it by the claim file on origin."""
        steps = [Verb("claim", "a", (1,)), Verb("lease-onto", "b", (1,)), BOARD]
        self.assertEqual(violations(steps, Scope(careless=True)), [])
        with mock.patch.object(guard, "other_holder", lambda *args: None):
            found = violations(steps, Scope(careless=True))
        self.assertEqual(guarantees(found), ["G1", "G11a"])

    def test_f13_the_oldest_work_lands_first(self):
        """Found by this step, fixed after 0.4.1: a draft whose branch was
        made two hours before another claim, and opened two hours after it,
        lands first, as the spec reads the oldest; until the fix the claim
        landed first, its issue's number being below the pull request's,
        which the mutant that orders by number again shows, and so it does
        under the mutant that dates the draft by its opening (F13's
        review)."""
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (2,)), Verb("claim", "b", (2,)),
                 Verb("wait", "clock", (2,)), Verb("open-draft", "a", (1,)), BOARD]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        for mutant in ("the landing order by number", "a pull request dated by its opening"):
            with self.subTest(mutant=mutant), mutants.switched_on(mutants.MUTANTS[mutant]):
                found = violations(steps, SHARED, issues=None)
                self.assertEqual([(v.guarantee, v.detail) for v in found],
                                 [("G7", "the board's Overlaps are {'src/shared.py': [2, 100]}, the oracle's "
                                         "{'src/shared.py': [100, 2]}")])

    def test_f13_work_github_will_not_date_lands_as_the_spec_reads_it(self):
        """F13's second review: with every read of the activity refused, the
        draft is dated by its opening and the claim, which nothing dates,
        lands last; the oracle reads the branches GitHub refused from the
        run's calls, so the page the spec asks for is green, and under the
        mutant that puts such work first it is not."""
        steps = [Verb("claim", "a", (1,)), Verb("wait", "clock", (2,)), Verb("claim", "b", (2,)),
                 Verb("wait", "clock", (2,)), Verb("open-draft", "b", (2,)), board_with("activity-500-all")]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        with mutants.switched_on(mutants.MUTANTS["work nothing dates landing first"]):
            found = violations(steps, SHARED, issues=None)
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G7", "the board's Overlaps are {'src/shared.py': [1, 100]}, the oracle's "
                                 "{'src/shared.py': [100, 1]}")])

    def test_unsaid_branches_are_those_whose_own_read_github_refused(self):
        """A refused read of the newest creations of the whole repository
        leaves each branch to its own read, so it makes no branch unsaid."""
        def read(status, ref=None, injected=False):
            query = (("activity_type", "branch_creation"),) + ((("ref", ref),) if ref else ())
            return Call("GET", "/activity", query, None, status, False, injected)

        calls = [read(500, injected=True), read(500, "refs/heads/claude/1", True), read(200, "refs/heads/claude/2"),
                 read(404, "refs/heads/claude/3")]
        self.assertEqual(invariants.unsaid_branches(calls), {"claude/1", "claude/3"})
        self.assertEqual(invariants.unsaid_branches(calls[:1]), set())

    def test_f14_an_issue_a_worker_s_label_names_is_shown_under_given_to_workers(self):
        """Found by this step, fixed after 0.4.1: an issue a worker's label
        names, with no todo, is shown under Given to workers as the spec
        reads; until the fix it was shown in neither section, which the
        mutant that asks for todo again shows."""
        self.assertEqual(violations([BOARD], labels=("worker:a",)), [])
        with mutants.switched_on(mutants.MUTANTS["given to workers needing the label todo"]):
            found = violations([BOARD], labels=("worker:a",))
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G9", "Given to workers is {}, the oracle's {1: ['a']}")])


class TheChecksHoldUnderAFaultThatNeverFires(unittest.TestCase):
    """The review of this step found the whole effects comparison skipped
    for any board verb that named a fault, fired or not."""

    def test_the_release_order_is_checked_in_a_run_whose_fault_never_fired(self):
        steps = [Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (25,)),
                 board_with("label-post-500")]
        self.assertEqual(violations(steps), [])
        with mock.patch.object(github, "release_one", mutants.comment_then_labels):
            self.assertEqual(guarantees(violations(steps)), ["G3"])


if __name__ == "__main__":
    unittest.main()
