"""The harness's oracle (validation/oracle.py): the spec's terms over the
model, at the thresholds the prototype never sat on (waits of 24 and 25
hours), and what a board run owes from a state."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import model as M, oracle, verbs  # noqa: E402
from validation.oracle import Effect  # noqa: E402
from validation.verbs import Verb  # noqa: E402


def run(model, *steps, scope=verbs.Scope()):
    for step in steps:
        verbs.apply(model, step, scope)
    return model


def claimed(*more):
    return run(M.seeded((1,), writers=("a", "b", verbs.CAROL, verbs.DANA)), Verb("claim", "a", (1,)),
               Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")), *more)


class Terms(unittest.TestCase):
    def test_a_claim_branch_is_spelled_exactly(self):
        self.assertEqual(oracle.claim_number("claude/12"), 12)
        for name in ("claude/012", "claude/0", "claude/\u0663", "claude/\u00b2", "claude/1234567890", "x/1"):
            self.assertIsNone(oracle.claim_number(name), name)

    def test_a_name_that_reads_as_a_claim_and_claims_nothing(self):
        self.assertTrue(oracle.reads_as_claim("claude/03"))
        self.assertTrue(oracle.reads_as_claim("claude/\u00b2"))
        self.assertFalse(oracle.reads_as_claim("claude/3"))
        self.assertFalse(oracle.reads_as_claim("claude/caf\u00e9"))

    def test_more_than_24_hours_is_idle_and_exactly_24_is_not(self):
        self.assertFalse(oracle.due(claimed(Verb("wait", "clock", (24,))), 1))
        self.assertTrue(oracle.due(claimed(Verb("wait", "clock", (25,))), 1))

    def test_a_claim_is_idle_by_when_its_head_was_pushed_whatever_its_committer_date(self):
        """F8: SPEC["last commit"]; a view that could not read the push
        dates the head by its committer date (SPEC["undated push"])."""
        ahead = run(M.seeded((1,), writers=("a",)), Verb("claim", "a", (1, 72 * M.HOUR)),
                    Verb("label-on", "a", (1, "claimed")), Verb("wait", "clock", (25,)))
        self.assertEqual(oracle.head_time(ahead, "claude/1"), 0)
        self.assertTrue(oracle.due(ahead, 1))
        undated = oracle.dated(ahead, {"claude/1"})
        self.assertEqual(oracle.head_time(undated, "claude/1"), 72 * M.HOUR)
        self.assertFalse(oracle.due(undated, 1))
        self.assertIs(oracle.dated(ahead, set()), ahead)
        self.assertIn("claude/1", ahead.pushed, "the view leaves the model as it was")
        back = run(claimed(Verb("wait", "clock", (2,))), Verb("commit", "a", (1, -48 * M.HOUR)))
        self.assertFalse(oracle.due(back, 1))
        self.assertTrue(oracle.due(oracle.dated(back, {"claude/1"}), 1))

    def test_an_entry_dates_the_head_it_set_and_any_after_the_run_began(self):
        """SPEC["last commit"] and SPEC["moved off"], as F8's fix check reads
        them: an entry that set the head dates it however old; any other,
        whatever heads it names, only when it came after the run began, the
        slack before allowed; and SPEC["floor"]: where no read dates a
        push, the newest entry listed dates the claim only when it is later
        than the head's committer date, the same minute being no later."""
        began = 25 * M.HOUR
        self.assertEqual(oracle.entry_dates(True, M.HOUR, began), M.HOUR)
        self.assertEqual(oracle.entry_dates(False, began, began), began)
        self.assertEqual(oracle.entry_dates(False, began - oracle.MOVED_SLACK_MINUTES, began),
                         began - oracle.MOVED_SLACK_MINUTES)
        self.assertIsNone(oracle.entry_dates(False, began - oracle.MOVED_SLACK_MINUTES - 1, began))
        self.assertIsNone(oracle.entry_dates(False, M.HOUR, began))
        self.assertEqual(oracle.floor_dates(M.HOUR, 0), M.HOUR)
        self.assertIsNone(oracle.floor_dates(M.HOUR, M.HOUR))
        self.assertIsNone(oracle.floor_dates(M.HOUR, 2 * M.HOUR))
        self.assertIsNone(oracle.floor_dates(None, 0))

    def test_a_head_a_release_kept_is_released_by_any_release_comment_that_counts(self):
        """F8: SPEC["pushed again"] and SPEC["kept deleted"]: pushed again
        at once, the branch is released already and not due."""
        model = claimed(Verb("wait", "clock", (25,)), Verb("board", "board", ("schedule",)))
        old = next(r for r in model.refs if r.startswith("released/"))
        run(model, Verb("push-old-head", "a", (1, old)), Verb("label-on", "a", (1, "claimed")))
        self.assertTrue(oracle.kept(model, "claude/1"))
        self.assertTrue(oracle.already_released(model, 1))
        self.assertFalse(oracle.due(model, 1))
        self.assertFalse(oracle.kept(model, "claude/9"))

    def test_a_finished_claim_is_never_due(self):
        for finish in ([Verb("label-on", "a", (1, "in-review"))],
                       [Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,))],
                       [Verb("open-draft", "a", (1,)), Verb("finished", "a", (100,))]):
            with self.subTest(finish=[str(v) for v in finish]):
                model = claimed(*finish, Verb("wait", "clock", (25,)))
                self.assertTrue(oracle.finished(model, 1))
                self.assertFalse(oracle.due(model, 1))

    def test_whose_release_comment_counts(self):
        for person, counts in ((M.OWNER, True), (verbs.CAROL, True), (verbs.DANA, True), (verbs.SAM, False)):
            with self.subTest(person=person):
                model = claimed(Verb("wait", "clock", (25,)), Verb("marker", person, (1,)),
                                Verb("label-off", M.OWNER, (1, "claimed")))
                self.assertEqual(oracle.released(model, 1), counts)

    def test_whose_pull_request_is_the_claim_s_own(self):
        """SPEC["stranger"]: the owner's, a collaborator's, a writer's GitHub
        shows as a contributor (dana) and an App's are the claim's own;
        sam's from the claim's own branch is no claim's, so it finishes
        nothing, and a release's rename closes it, after which the board
        speaks to it no more (F19)."""
        model = claimed()
        for person, own in ((M.OWNER, True), (verbs.CAROL, True), (verbs.DANA, True), (verbs.SAM, False)):
            pull = M.PullRequest(100, "claude/1", person, "Closes #1", draft=False,
                                 association=verbs.ASSOCIATIONS[person])
            with self.subTest(person=person):
                self.assertEqual(oracle.opened_by_writer(model, pull), own)
                self.assertEqual(oracle.claim_of(model, pull), 1 if own else None)
        app = M.PullRequest(100, "claude/1", "symphony[bot]", "Closes #1", association="NONE")
        self.assertTrue(oracle.opened_by_writer(model, app))
        theirs = run(model, Verb("stranger-pull", verbs.SAM, (1, True)))
        self.assertIsNotNone(theirs.open_pull_from("claude/1"))
        self.assertIsNone(oracle.own_pull(theirs, "claude/1"))
        self.assertFalse(oracle.finished(theirs, 1))
        released = run(theirs, Verb("wait", "clock", (25,)), Verb("marker", M.OWNER, (1,)),
                       Verb("label-off", M.OWNER, (1, "claimed")))
        self.assertTrue(oracle.released(released, 1))
        self.assertTrue(oracle.closed_by_release(released, released.open_pull_from("claude/1")))
        self.assertEqual(oracle.pulls_spoken_to(released), [])
        own = run(claimed(), Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)))
        self.assertEqual(oracle.own_pull(own, "claude/1").number, 100)
        self.assertTrue(oracle.finished(own, 1))

    def test_a_login_github_will_not_answer_for_keeps_its_pull_request_and_loses_its_comments(self):
        """SPEC["unanswered"] (F19's review): where GitHub will not say
        whether sam or dana may write, sam's pull request from the claim's
        branch stays the claim's, finished once marked ready, and dana's
        release marker, which counted while GitHub vouched for dana, counts
        for nothing; the owner's, vouched for by association, still does."""
        model = claimed()
        model.unanswered = frozenset({verbs.SAM, verbs.DANA})
        theirs = run(model, Verb("stranger-pull", verbs.SAM, (1, True)))
        self.assertTrue(oracle.opened_by_writer(theirs, theirs.open_pull_from("claude/1")))
        self.assertEqual(oracle.claim_of(theirs, theirs.open_pull_from("claude/1")), 1)
        self.assertIs(oracle.own_pull(theirs, "claude/1"), theirs.open_pull_from("claude/1"))
        self.assertTrue(oracle.finished(theirs, 1))
        marked = run(claimed(), Verb("wait", "clock", (25,)), Verb("marker", verbs.DANA, (1,)))
        self.assertEqual(len(oracle.release_times(marked, 1)), 1)
        marked.unanswered = frozenset({verbs.DANA})
        self.assertEqual(oracle.release_times(marked, 1), [])
        both = run(marked, Verb("marker", M.OWNER, (1,)))
        self.assertEqual(len(oracle.release_times(both, 1)), 1)

    def test_a_release_comment_made_too_soon_frees_nothing(self):
        model = claimed(Verb("marker", M.OWNER, (1,)), Verb("label-off", M.OWNER, (1, "claimed")),
                        Verb("wait", "clock", (25,)))
        self.assertFalse(oracle.released(model, 1))
        self.assertTrue(oracle.due(model, 1))

    def test_a_worker_label_in_any_spelling_gives_the_issue_and_keeps_it_off_the_queue(self):
        for label in ("worker:c", "account:c", "agent:c"):
            with self.subTest(label=label):
                model = M.seeded((1, 2))
                model.add_label(2, M.OWNER, label)
                self.assertEqual(oracle.queue(model), [1])
                self.assertEqual(oracle.given(model), {2: ["c"]})


class WhatARunOwes(unittest.TestCase):
    def test_a_due_release_in_the_spec_s_order(self):
        model = claimed(Verb("wait", "clock", (25,)))
        sha = model.refs["claude/1"]
        self.assertEqual(oracle.expected_effects(model), [
            Effect("label-off", 1, "claimed"), Effect("label-off", 1, "worker:a"),
            Effect("comment", 1, "claim-released"),
            Effect("rename", "claude/1", f"released/1-{sha[:7]}"), Effect("comment", 1, "released"),
        ])

    def test_a_released_head_pushed_again_is_deleted(self):
        model = claimed(Verb("wait", "clock", (25,)), Verb("board", "board", ("schedule",)))
        old = next(r for r in model.refs if r.startswith("released/"))
        run(model, Verb("push-old-head", "a", (1, old)))
        self.assertEqual(oracle.expected_effects(model), [Effect("delete-ref", "claude/1")])

    def test_nothing_is_owed_twice(self):
        model = claimed(Verb("wait", "clock", (25,)), Verb("board", "board", ("schedule",)))
        self.assertEqual(oracle.expected_effects(model), [])

    def test_comment_kinds_by_their_first_line(self):
        self.assertEqual(oracle.comment_kind(oracle.RELEASE_MARKER + "\nwhy"), "claim-released")
        self.assertEqual(oracle.comment_kind("<!-- sdlc-coordinator: overlap 2 3 -->\nx"), "overlap")
        self.assertEqual(oracle.comment_kind("<!-- sdlc-board: main-moved abc1234 -->\nx"), "main-moved")
        self.assertEqual(oracle.comment_kind("<!-- sdlc-board: idle -->\nx"), "idle")
        self.assertEqual(oracle.comment_kind("<!-- sdlc-board: released released/1-abc1234 -->"), "released")
        self.assertEqual(oracle.comment_kind("words " + oracle.RELEASE_MARKER), "other")


if __name__ == "__main__":
    unittest.main()
