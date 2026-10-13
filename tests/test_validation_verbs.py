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
        model, _ = after([CLAIM])
        self.assertEqual(model.pending, (("create", "claude/1"),))

    def test_the_default_scope_queues_nothing(self):
        model, _ = after([CLAIM, Verb("open-draft", "a", (1,))], Scope())
        self.assertEqual(model.pending, ())

    def test_a_scope_hears_only_the_events_it_names(self):
        model, _ = after([Verb("move-main", M.OWNER), CLAIM], Scope(events=("schedule", "push")))
        self.assertEqual(model.pending, ())
        model, _ = after([Verb("move-main", M.OWNER), CLAIM], Scope(events=("schedule", "create")))
        self.assertEqual(model.pending, (("create", "claude/1"),))

    def test_a_workers_push_replaces_the_run_of_the_push_to_main(self):
        """F12's mechanism: the run the push to main queued is cancelled by
        the next event's, here a push to a claim branch whose own run is
        skipped."""
        model, _ = after([CLAIM, Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)),
                          Verb("move-main", M.OWNER)])
        self.assertEqual(model.pending, (("push", "main"),))
        verbs.apply(model, Verb("commit", "a", (1,)), LISTENING)
        self.assertEqual(model.pending, (("pull_request", ""),))


class TheBoardVerbs(unittest.TestCase):
    def test_the_schedule_and_each_run_queued_with_the_faults_on_the_first(self):
        model, _ = after([CLAIM, Verb("open-draft", "a", (1,))])
        scope = LISTENING._replace(faults=("rename-403",))
        self.assertEqual(names(verbs.board_verbs(model, scope)),
                         ["board board schedule ", "board board pull_request ",
                          "board board schedule  ('rename-403',)"])

    def test_a_queued_run_that_runs_leaves_the_queue(self):
        model, outcome = after([CLAIM, Verb("board", "board", ("create", "claude/1"))])
        self.assertEqual((model.pending, outcome.run.event), ((), "create"))

    def test_the_schedule_s_run_replaces_what_waited(self):
        model, _ = after([CLAIM, Verb("board", "board", ("schedule", ""))])
        self.assertEqual(model.pending, ())

    def test_a_split_scope_offers_the_first_half_then_only_the_second(self):
        scope = Scope(split_board=True)
        model, _ = after([CLAIM], scope)
        self.assertEqual([v for v in names(verbs.enabled(model, scope)) if v.startswith(("board", "wait"))],
                         ["wait clock 24", "wait clock 25", "board-start board schedule "])
        verbs.apply(model, Verb("board-start", "board", ("schedule", "")), scope)
        self.assertIsNotNone(model.running)
        found = names(verbs.enabled(model, scope))
        self.assertEqual([v for v in found if v.startswith(("board", "wait"))], ["board-finish board"])
        self.assertIn("commit a 1", found)
        outcome = verbs.apply(model, Verb("board-finish", "board"), scope)
        self.assertEqual((model.running, outcome.run.exit_code), (None, 0))


if __name__ == "__main__":
    unittest.main()
