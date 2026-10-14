"""The mutation lane's mutants (validation/mutants.py), in the required
check: the lane's size, every mutant's target still in the library, and
each mutant caught on its shortest trace, which the library itself passes
(or, for a lane, on something beyond what the lane knows red as built).
The lane proper, every mutant against every search, runs in validate.yml
(python3 -m validation.mutants), since it takes about two minutes; these
traces are what the lane found, kept so a change that stops the harness
catching a mutant fails here too, naming the rule. A mutant of a rule no
search runs names the cheap lane that catches it instead (step 4), and
the test runs that lane."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cycle, invariants, model as M, mutants, oracle, verbs  # noqa: E402
from validation.verbs import DANA, SAM, Scope, Verb  # noqa: E402

BOARD = Verb("board", "board", (cycle.SCHEDULE, ""))
CLAIM, CLAIMED, WORKER = mutants.CLAIM, mutants.CLAIMED, mutants.WORKER
HELD = [CLAIM, CLAIMED, WORKER]
WAIT = Verb("wait", "clock", (25,))
SHARED = Scope(issues=(1, 2), files=((1, mutants.SHARED), (2, mutants.SHARED)))


def board_with(fault):
    return Verb("board", "board", (cycle.SCHEDULE, "", (fault,)))


# Each mutant's shortest trace, as the lane found it, with the scope it needs.
TRACES = {
    "idle at exactly 24 hours": (HELD + [Verb("wait", "clock", (24,)), BOARD], Scope()),
    "a 36-hour idle time": (HELD + [WAIT, BOARD], Scope()),
    "the worker label left on at release": (HELD + [WAIT, BOARD], Scope()),
    "a release comment accepted from any author": (HELD + [WAIT, Verb("marker", SAM, (1,)), BOARD], Scope()),
    "an idle comment to a released claim's pull request": (HELD + [Verb("open-draft", "a", (1,)), WAIT, BOARD],
                                                           Scope()),
    "the comment before the labels": (HELD + [WAIT, BOARD], Scope()),
    "a refused rename never said": (HELD + [WAIT, board_with("rename-403")], Scope()),
    "a release ignores the label claimed": (HELD + [WAIT, Verb("marker", M.OWNER, (1,)), BOARD], Scope()),
    "a finished draft released by the coordinator": (HELD + [WAIT, Verb("open-draft", "a", (1,)),
                                                             Verb("finished", "a", (100,)), BOARD], Scope()),
    "any comment counts as a release": (HELD + [WAIT, Verb("plan", "a", (1,)), BOARD], Scope()),
    "a board comment posted again": (HELD + [WAIT, BOARD, Verb("push-old-head", "a", (1, "released/1-6d6ae03")),
                                             BOARD], Scope()),
    "main moved told only in the push's run": "the regression traces",
    "the rename skipped": (HELD + [WAIT, BOARD], Scope()),
    "overlaps told on every run": ([CLAIM, Verb("claim", "b", (2,)), BOARD], SHARED),
    "the labels not put back": (HELD + [WAIT, board_with("comment-500")], Scope()),
    "a lost comment's release not seen": (HELD + [WAIT, board_with("comment-lost")], Scope()),
    "a writer's release not vouched for": (HELD + [WAIT, Verb("marker", DANA, (1,)), BOARD], Scope()),
    "a claim number with leading zeros": ([CLAIM, CLAIMED, Verb("push-by-hand", M.OWNER, (1, "claude/01"))],
                                          Scope(careless=True)),
    "the guard passes a misnamed claim": ([CLAIM, CLAIMED, Verb("claim-misnamed", "a", (1, "claude/01"))],
                                          Scope(careless=True)),
    "the rename made without reading the head again": (HELD + [WAIT, Verb("board-start", "board", (cycle.SCHEDULE, "")),
                                                               Verb("commit", "a", (1,)), Verb("board-finish", "board")],
                                                       Scope(split_board=True)),
    "the guard passes --force": "the guard grammar against git's answers",
    "the guard passes a lease outside claude/": "the guard grammar against git's answers",
    "the guard passes a lease onto another's claim": "the regression traces",
    "dealing without its taken set": "dealing from one look",
    "an overlap marker read on any line": ([CLAIM, Verb("claim", "b", (2,)), Verb("overlap-quote", "a", (1, (2,))),
                                            BOARD], SHARED),
    "an overlap marker trusted from any author": ([CLAIM, Verb("claim", "b", (2,)),
                                                   Verb("overlap-marker", SAM, (1, (2,))), BOARD],
                                                  SHARED._replace(people=(SAM,))),
    "a sort key that calls int after isdigit": "the regression traces",
    "a branch name sent unquoted": "the regression traces",
    "a finished draft's release by hand not seen": "the regression traces",
    "a refused comment stops the run": "the regression traces",
}
LANES = {lane.name: lane for lane in mutants.LANES}


def violations(steps, scope):
    model = M.seeded(scope.issues, writers=("a", "b", "c", verbs.CAROL, DANA))
    cycle.board_run(model)
    found = []
    for step in steps:
        before = model.clone()
        outcome = verbs.apply(model, step, scope)
        found += invariants.step_checks(before, model, step, outcome, oracle.AS_BUILT)
    return found


class TheLane(unittest.TestCase):
    def test_it_holds_10_to_30_mutants_each_with_a_trace(self):
        """Section 7 asks for 10 to 20; step 4 added five for the rules its
        lanes judge, step 5 four that undo fixes of 0.4.1, and each fix
        since one that undoes it (F7's first), so the bound is widened to
        40 and the count noted."""
        self.assertTrue(10 <= len(mutants.MUTANTS) <= 40)
        self.assertEqual(len(mutants.MUTANTS), 30)
        self.assertEqual(sorted(TRACES), sorted(mutants.MUTANTS))

    def test_every_mutant_names_a_rule_the_library_still_has(self):
        """A mutant whose target was renamed would patch nothing the library
        calls, and so be caught by nothing."""
        for name, mutant in mutants.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertTrue(callable(getattr(mutant.module, mutant.name)))
                self.assertIn(mutant.guarantee, invariants.GUARANTEES)

    def test_the_lanes_come_first_then_the_searches_cheapest_first(self):
        self.assertEqual([c.name for c in mutants.CATCHERS[:len(LANES)]], list(LANES))
        self.assertEqual(mutants.SEARCHES[-2].name, "the holder back between the halves of a run")
        self.assertEqual(mutants.SEARCHES[-1].name, "a stranger's marker on overlapping claims")
        self.assertEqual([s.known for s in mutants.CATCHERS if s.known], [(mutants.F4,)])


class EachMutantIsCaught(unittest.TestCase):
    def test_on_its_shortest_trace_which_the_library_passes(self):
        for name, trace in TRACES.items():
            with self.subTest(mutant=name):
                if isinstance(trace, str):
                    lane = LANES[trace]
                    plain = lane.run()
                    self.assertEqual(set(plain) - set(lane.known), set(), "the library itself passes the lane")
                    with mutants.switched_on(mutants.MUTANTS[name]):
                        found = lane.run()
                    self.assertNotEqual(set(found) - set(plain), set())
                    continue
                steps, scope = trace
                self.assertEqual(violations(steps, scope), [], "the library itself passes the trace")
                with mutants.switched_on(mutants.MUTANTS[name]):
                    found = violations(steps, scope)
                self.assertNotEqual(found, [])


class TheHarnessFixesTheLaneAskedFor(unittest.TestCase):
    def test_a_head_pushed_again_and_deleted_is_no_refused_rename(self):
        """The rename of a branch pushed again at the head a release kept is
        refused with 422 and the board deletes the branch, as the spec says;
        that is no refusal for good to say on the issue."""
        steps, scope = TRACES["a board comment posted again"]
        self.assertEqual(violations(steps, scope), [])

    def test_a_head_pushed_again_whose_rename_or_delete_is_refused_for_good_is_said(self):
        """Only the 422 the library answers by deleting is excused: a 403 on
        the rename, or a refused delete, leaves the name taken, and the
        issue hears so once."""
        steps, scope = TRACES["a board comment posted again"]
        for fault in ("rename-403", "ref-delete-422"):
            with self.subTest(fault=fault):
                self.assertEqual(violations(steps[:-1] + [board_with(fault)], scope), [])

    def test_the_guard_must_refuse_what_the_rules_forbid(self):
        outcome = verbs.Outcome(True, "taken", "git push origin claude/01", None, allowed_shape=False)
        self.assertEqual([v.guarantee for v in invariants.command_checks(outcome)], ["G11a"])
        by_hand = outcome._replace(guarded=False)
        self.assertEqual(invariants.command_checks(by_hand), [])


if __name__ == "__main__":
    unittest.main()
