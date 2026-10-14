"""The fast lane's searches (DESIGN.md 6.8): E-fast, two workers on one
issue to depth 5; E-three, three workers on one issue; the searches from
each scenario's prefix (E-prefix); and the faults, one kind at a time,
from a claim that is due. Each breaks nothing on the library as it stands
and stays inside its budget, so the whole lane, with the scenarios, the
guard grammar, dealing and the properties, runs in under 60 seconds on a
laptop. The budgets are generous against CI's slower machines; the
measurements validation/README.md records are the laptop's."""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cycle, explore, model as M, run, scenarios, verbs  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

DUE = (Verb("claim", "a", (1,)), Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")),
       Verb("wait", "clock", (25,)))
# The design's points (6.8), as the built scenarios count their steps: S02
# after the run at 25 hours, S03 after the owner's release by hand at 58,
# S04 after the run at 26 that released the abandoned draft, S05 after the
# first pull request is merged.
PREFIXES = {"S02": 6, "S03": 12, "S04": 6, "S05a": 23}


def start(scope=Scope(), prefix=(), labels=("todo",)):
    model = M.seeded(labels=labels)
    cycle.board_run(model)
    for verb in prefix:
        verbs.apply(model, verb, scope)
    return model


def broken(found):
    return [(f.guarantee, f.detail, f.trace) for f in found.found]


class TheFastLane(unittest.TestCase):
    def test_e_fast_two_workers_on_one_issue_to_depth_5(self):
        began = time.perf_counter()
        found = explore.explore(start(), Scope(), 5)
        self.assertLess(time.perf_counter() - began, 45)
        self.assertGreater(found.states, 20000)
        self.assertEqual(broken(found), [])

    def test_e_three_three_workers_on_one_issue_to_depth_4(self):
        began = time.perf_counter()
        found = explore.explore(start(), Scope(workers=("a", "b", "c")), 4)
        self.assertLess(time.perf_counter() - began, 12)
        self.assertGreater(found.states, 4000)
        self.assertEqual(broken(found), [])

    def test_e_prefix_from_each_scenarios_interesting_prefix_to_depth_3(self):
        began = time.perf_counter()
        for name, upto in PREFIXES.items():
            with self.subTest(scenario=name):
                scenario = scenarios.SCENARIOS[name]
                found = explore.explore(run.prefix(scenario, upto), run.scope_of(scenario), 3)
                self.assertGreater(found.states, 1000)
                self.assertEqual(broken(found), [])
        self.assertLess(time.perf_counter() - began, 30)

    def test_the_faults_one_kind_at_a_time_from_a_due_claim(self):
        began = time.perf_counter()
        for fault in verbs.FAULTS:
            with self.subTest(fault=fault):
                scope = Scope(workers=("a",), people=(), waits=(25,), faults=(fault,))
                found = explore.explore(start(scope, DUE), scope, 3)
                self.assertGreater(found.states, 20)
                self.assertEqual(broken(found), [])
        self.assertLess(time.perf_counter() - began, 30)

    def test_the_faults_one_kind_at_a_time_from_a_due_claim_on_a_task_given_by_a_label_alone(self):
        """F15: the release puts todo on such a task's issue, so a fault
        between the labels and the comment has the todo to take off as
        well as the labels to put back (G3L)."""
        began = time.perf_counter()
        for fault in verbs.FAULTS:
            with self.subTest(fault=fault):
                scope = Scope(workers=("a",), people=(), waits=(25,), faults=(fault,))
                found = explore.explore(start(scope, DUE, labels=("worker:a",)), scope, 3)
                self.assertGreater(found.states, 20)
                self.assertEqual(broken(found), [])
        self.assertLess(time.perf_counter() - began, 30)


if __name__ == "__main__":
    unittest.main()
