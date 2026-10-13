"""The harness's guarantees (validation/invariants.py) on hand-written
traces: an honest release and reclaim breaks none; the 0.4.1 fixes hold;
a run in two halves is judged by the states its halves read; the open
findings F2, F7, F9 and F12 show as soon as the oracle reads the spec,
F12 by the run the concurrency group cancels. The mutants, the prototype's
among them, are tests/test_validation_mutants.py's."""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol.adapters import github  # noqa: E402
from validation import cycle, invariants, model as M, mutants, oracle, verbs  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

BOARD = Verb("board", "board", (cycle.SCHEDULE, ""))
SHARED = Scope(issues=(1, 2), files=((1, ("src/shared.py",)), (2, ("src/shared.py",))))


def board_with(*faults, event=cycle.SCHEDULE, ref_name=""):
    return Verb("board", "board", (event, ref_name, faults))


def violations(steps, scope=Scope(), known=oracle.AS_BUILT, issues=(1,), labels=("todo",)):
    model = M.seeded(issues or scope.issues, writers=("a", "b", "c", verbs.CAROL, verbs.DANA), labels=labels)
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


class TheOpenFindingsShow(unittest.TestCase):
    """With its switch off the oracle reads the spec, and the finding is red."""

    def test_f2_overlapping_work_told_again_once_a_claim_opens_its_pull_request(self):
        steps = [Verb("claim", "a", (1,)), Verb("claim", "b", (2,)), BOARD, Verb("open-draft", "a", (1,)), BOARD]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        self.assertEqual(guarantees(violations(steps, SHARED, oracle.AS_BUILT - {"F2"}, issues=None)), ["G7"])

    def test_f9_a_draft_behind_main_is_never_told(self):
        steps = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("move-main", M.OWNER),
                 board_with(event=cycle.PUSH, ref_name=M.MAIN)]
        self.assertEqual(violations(steps), [])
        self.assertEqual(guarantees(violations(steps, known=oracle.AS_BUILT - {"F9"})), ["G10"])

    def test_f12_main_moved_told_only_in_the_run_the_push_started(self):
        steps = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)),
                 Verb("move-main", M.OWNER), board_with(event=cycle.PULL_REQUEST)]
        self.assertEqual(violations(steps), [])
        self.assertEqual(guarantees(violations(steps, known=oracle.AS_BUILT - {"F12"})), ["G10"])

    def test_f12_the_run_of_the_push_to_main_is_cancelled_by_the_next_event_s(self):
        """The mechanism (DESIGN.md, F12): main moves, a worker pushes to a
        claim branch, whose skipped run takes the waiting run's place, and
        the run that follows owes nothing as built; reading the spec, it
        owes the pull request the word."""
        scope = Scope(events=cycle.EVENTS)
        steps = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)),
                 Verb("move-main", M.OWNER), Verb("commit", "a", (1,)), Verb("board", "board", (cycle.PULL_REQUEST, ""))]
        self.assertEqual(violations(steps, scope), [])
        self.assertEqual(guarantees(violations(steps, scope, known=oracle.AS_BUILT - {"F12"})), ["G10"])

    def test_f7_a_lease_after_a_fetch_takes_another_s_claim(self):
        found = violations([Verb("claim", "a", (1,)), Verb("lease-onto", "b", (1,))], Scope(careless=True))
        self.assertEqual(guarantees(found), ["G1"])

    def test_f13_the_landing_order_s_oldest_is_the_lowest_number(self):
        """Found by this step: a claim made five hours after another's draft
        lands first, since its issue's number is below the pull request's."""
        steps = [Verb("claim", "a", (1,)), Verb("open-draft", "a", (1,)), Verb("wait", "clock", (5,)),
                 Verb("claim", "b", (2,)), BOARD]
        self.assertEqual(violations(steps, SHARED, issues=None), [])
        found = violations(steps, SHARED, oracle.AS_BUILT - {"F13"}, issues=None)
        self.assertEqual([(v.guarantee, v.detail) for v in found],
                         [("G7", "the board's Overlaps are {'src/shared.py': [2, 100]}, the oracle's "
                                 "{'src/shared.py': [100, 2]}")])

    def test_f14_given_to_workers_needs_the_label_todo(self):
        """Found by this step: an issue a worker's label names, with no todo,
        is shown in neither the Queue nor Given to workers."""
        self.assertEqual(violations([BOARD], labels=("worker:a",)), [])
        found = violations([BOARD], known=oracle.AS_BUILT - {"F14"}, labels=("worker:a",))
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
