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
