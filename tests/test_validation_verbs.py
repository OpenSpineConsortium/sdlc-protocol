"""The harness's verbs (validation/verbs.py) as the board workflow sees
them: which events each act fires, as board.yml's triggers name them,
which of them a scope listens for, and which board verbs the checker may
take: a run for the schedule or for each run queued, one at a time, and,
with the scope's split_board on, a run in two halves with any verb but
the clock's between them."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cycle, model as M, verbs  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

LISTENING = Scope(events=cycle.EVENTS)
CLAIM = Verb("claim", "a", (1,))


def after(steps, scope=LISTENING):
    model = M.seeded()
    cycle.board_run(model)
    outcome = None
    for step in steps:
        outcome = verbs.apply(model, step, scope)
    return model, outcome


def names(found):
    return [str(v) for v in found]


class TheEventsAnActFires(unittest.TestCase):
    def test_a_claim_is_a_push_and_a_branch_made(self):
        _, outcome = after([CLAIM])
        self.assertEqual(outcome.events, (("push", "claude/1"), ("create", "claude/1")))

    def test_a_push_to_a_branch_with_a_draft_moves_the_pull_request_too(self):
        _, outcome = after([CLAIM, Verb("open-draft", "a", (1,)), Verb("commit", "a", (1,))])
        self.assertEqual(outcome.events, (("push", "claude/1"), ("pull_request", "")))

    def test_a_refused_push_fires_nothing(self):
        _, outcome = after([CLAIM, Verb("claim", "b", (1,))])
        self.assertEqual((outcome.taken, outcome.events), (False, ()))

    def test_labels_and_a_closed_issue_are_issues_events_a_comment_none(self):
        _, labelled = after([CLAIM, Verb("label-on", "a", (1, "claimed"))])
        _, taken_off = after([CLAIM, Verb("label-on", "a", (1, "claimed")), Verb("label-off", "a", (1, "claimed"))])
        _, planned = after([CLAIM, Verb("plan", "a", (1,))])
        _, closed = after([Verb("close-issue", M.OWNER, (1,))])
        self.assertEqual([o.events for o in (labelled, taken_off, planned, closed)],
                         [(("issues", ""),), (("issues", ""),), (), (("issues", ""),)])

    def test_a_label_already_on_fires_nothing(self):
        _, outcome = after([CLAIM, Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "claimed"))])
        self.assertEqual(outcome.events, ())

    def test_a_pull_request_opened_fires_and_one_marked_ready_does_not(self):
        """board.yml takes pull_request opened, synchronize, reopened and
        closed, and not ready_for_review."""
        _, opened = after([CLAIM, Verb("open-draft", "a", (1,))])
        _, ready = after([CLAIM, Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,))])
        self.assertEqual((opened.events, ready.events), ((("pull_request", ""),), ()))

    def test_a_merge_fires_the_four_at_once(self):
        _, outcome = after([CLAIM, Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)),
                            Verb("merge", M.OWNER, (100,))])
        self.assertEqual(outcome.events, (("push", "main"), ("pull_request", ""), ("issues", ""), ("delete", "main")))

    def test_main_moved_is_a_push_to_main(self):
        _, outcome = after([Verb("move-main", M.OWNER)])
        self.assertEqual(outcome.events, (("push", "main"),))


class WhatAScopeListensFor(unittest.TestCase):
    def test_a_claim_queues_the_run_for_the_branch_made(self):
        """The push's own run, which the job skips, is a run only where the
        scope can start it and leave it in progress."""
        model, _ = after([CLAIM])
        self.assertEqual(model.queued, (("create", "claude/1"),))
        model, _ = after([CLAIM], LISTENING._replace(split_board=True))
        self.assertEqual(model.queued, (("create", "claude/1"), ("push", "claude/1")))

    def test_the_default_scope_queues_nothing(self):
        model, _ = after([CLAIM, Verb("open-draft", "a", (1,))], Scope())
        self.assertEqual(model.pending, ())

    def test_a_scope_hears_only_the_events_it_names(self):
        model, _ = after([Verb("move-main", M.OWNER), CLAIM], Scope(events=("schedule", "push")))
        self.assertEqual((model.queued, model.pending), ((("push", "main"),), ()))
        model, _ = after([Verb("move-main", M.OWNER), CLAIM], Scope(events=("schedule", "create")))
        self.assertEqual((model.queued, model.pending), ((("create", "claude/1"),), ()))

    def test_a_newer_event_s_run_replaces_the_one_waiting_behind_the_run_in_progress(self):
        """Main moves while the claim's run is in progress; the worker's
        push to the claim branch, with its pull request marked ready,
        fires the push's own run, which the job skips, and the pull
        request's synchronize run, one of which takes the waiting run's
        place; so the run that the push to main queued never runs."""
        scope = LISTENING._replace(split_board=True)
        model, _ = after([CLAIM, Verb("board-start", "board", ("create", "claude/1"))], scope)
        self.assertEqual(model.pending, (("push", "claude/1"),))
        verbs.apply(model, Verb("board-finish", "board"), scope)
        verbs.apply(model, Verb("board-start", "board", ("push", "claude/1")), scope)
        self.assertEqual((model.queued, model.pending), ((), ()))
        for step in (Verb("board-finish", "board"), Verb("open-draft", "a", (1,)),
                     Verb("board-start", "board", ("pull_request", "")), Verb("board-finish", "board"),
                     Verb("mark-ready", "a", (100,)), Verb("board-start", "board", ("schedule", ""))):
            verbs.apply(model, step, scope)
        verbs.apply(model, Verb("move-main", M.OWNER), scope)
        self.assertEqual(model.pending, (("push", "main"),))
        verbs.apply(model, Verb("commit", "a", (1,)), scope)
        self.assertEqual(model.pending, (("pull_request", ""), ("push", "claude/1")))
        verbs.apply(model, Verb("board-finish", "board"), scope)
        self.assertEqual((model.queued, model.pending), ((("pull_request", ""), ("push", "claude/1")), ()))

    def test_a_skipped_push_holds_the_group_while_the_run_behind_it_is_replaced(self):
        """F12's commonest mechanism, with no board run in progress: the
        worker's own push holds the group for the seconds its skip takes,
        main moves behind it, and the worker's next push replaces the run
        that would have told the pull request."""
        scope = LISTENING._replace(split_board=True)
        model, _ = after([CLAIM, Verb("board-start", "board", ("create", "claude/1")), Verb("board-finish", "board"),
                          Verb("board-start", "board", ("push", "claude/1")), Verb("board-finish", "board"),
                          Verb("open-draft", "a", (1,)), Verb("board-start", "board", ("pull_request", "")),
                          Verb("board-finish", "board"), Verb("mark-ready", "a", (100,)), Verb("commit", "a", (1,))],
                         scope)
        self.assertEqual(model.queued, (("pull_request", ""), ("push", "claude/1")))
        outcome = verbs.apply(model, Verb("board-start", "board", ("push", "claude/1")), scope)
        self.assertEqual((outcome.half, outcome.run, model.running), (None, None, cycle.SkippedRun("push", "claude/1")))
        self.assertEqual(model.pending, (("pull_request", ""),))
        verbs.apply(model, Verb("move-main", M.OWNER), scope)
        self.assertEqual(model.pending, (("push", "main"),))
        verbs.apply(model, Verb("commit", "a", (1,)), scope)
        self.assertEqual(model.pending, (("pull_request", ""), ("push", "claude/1")))
        outcome = verbs.apply(model, Verb("board-finish", "board"), scope)
        self.assertEqual((outcome.run, model.running, model.queued, model.pending),
                         (None, None, (("pull_request", ""), ("push", "claude/1")), ()))


class TheBoardVerbs(unittest.TestCase):
    def test_the_run_queued_comes_before_the_schedule_and_meets_every_fault(self):
        model, _ = after([CLAIM, Verb("open-draft", "a", (1,))])
        scope = LISTENING._replace(faults=("rename-403", "comment-500"))
        self.assertEqual(names(verbs.board_verbs(model, scope)),
                         ["board board create claude/1", "board board create claude/1 ('rename-403',)",
                          "board board create claude/1 ('comment-500',)"])
        verbs.apply(model, Verb("board", "board", ("create", "claude/1")), scope)
        self.assertEqual(names(verbs.board_verbs(model, scope)), ["board board pull_request ",
                                                                  "board board pull_request  ('rename-403',)",
                                                                  "board board pull_request  ('comment-500',)"])
        verbs.apply(model, Verb("board", "board", ("pull_request", "")), scope)
        self.assertEqual(names(verbs.board_verbs(model, scope)), ["board board schedule ",
                                                                  "board board schedule  ('rename-403',)",
                                                                  "board board schedule  ('comment-500',)"])

    def test_a_queued_run_that_runs_leaves_the_queue(self):
        model, outcome = after([CLAIM, Verb("board", "board", ("create", "claude/1"))])
        self.assertEqual((model.queued, model.pending, outcome.run.event), ((), (), "create"))

    def test_the_run_queued_starts_before_anything_else(self):
        """A run into an idle group starts within seconds, so no other verb
        is offered while one is queued; once it is in progress, the split
        scope offers the rest again."""
        model, _ = after([CLAIM])
        self.assertEqual(names(verbs.enabled(model, LISTENING)), ["board board create claude/1"])
        scope = LISTENING._replace(split_board=True)
        model, _ = after([CLAIM], scope)
        self.assertEqual(names(verbs.enabled(model, scope)),
                         ["board-start board create claude/1", "board-start board push claude/1"])
        verbs.apply(model, Verb("board-start", "board", ("create", "claude/1")), scope)
        found = names(verbs.enabled(model, scope))
        self.assertEqual(found[0], "board-finish board")
        self.assertIn("commit a 1", found)

    def test_a_split_scope_offers_the_first_half_then_only_the_second(self):
        scope = Scope(split_board=True)
        model, _ = after([CLAIM], scope)
        self.assertEqual([v for v in names(verbs.enabled(model, scope)) if v.startswith(("board", "wait"))],
                         ["wait clock 24", "wait clock 25", "board-start board schedule "])
        outcome = verbs.apply(model, Verb("board-start", "board", ("schedule", "")), scope)
        self.assertIs(outcome.half, model.running)
        found = names(verbs.enabled(model, scope))
        self.assertEqual([v for v in found if v.startswith(("board", "wait"))], ["board-finish board"])
        self.assertIn("commit a 1", found)
        outcome = verbs.apply(model, Verb("board-finish", "board"), scope)
        self.assertEqual((model.running, outcome.run.exit_code), (None, 0))


if __name__ == "__main__":
    unittest.main()
