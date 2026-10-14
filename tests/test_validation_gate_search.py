"""The gate's lane (validation/gate_search.py): G13 over every
interleaving of two agents' acts to a small depth under the four
policies over MemoryWorld, S05g over BareGitWorld, and a few seeded
sequences over git that reach a merge. The deep lane goes to depth 8
and forty sequences."""

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import gate  # noqa: E402
from validation import gate_search  # noqa: E402


class OverMemory(unittest.TestCase):
    def test_every_policy_to_depth_4_breaks_g13_nowhere(self):
        for name in gate_search.POLICIES:
            with self.subTest(policy=name):
                found = gate_search.explore(name, 4)
                self.assertGreater(found.states, 500)
                self.assertEqual(found.violations, ())

    def test_a_landing_that_moves_main_without_saying_so_is_a_violation(self):
        policy = gate_search.POLICIES["arm"]
        queue = gate.Queue({1: "a"}, open_queue=True)
        step = gate_search.StepWithCheck(gate_search.Act("ready", "a", 1), gate.Outcome(1, gate.READY, "", 0, "t", "m", "m"),
                                         None, "m", "m2", None)
        self.assertIn("outcome ready but main moved", gate_search.problems(policy, queue, step, {}))


class OverGit(unittest.TestCase):
    def test_s05g_runs_as_the_design_says(self):
        result = gate_search.s05g()
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["violations"], [])
        self.assertIn("ready r 2: main moved", result["timeline"])
        self.assertEqual(len([line for line in result["timeline"] if line.endswith(": merged")]), 2)

    def test_seeded_sequences_reach_a_merge_and_break_nothing(self):
        results = gate_search.sequences_over_git(count=3, length=12, seed=0)
        self.assertEqual(results["sequences"], 3)
        self.assertEqual(results["violations"], [])
        self.assertGreater(results["merges"], 0)

    def test_a_sequence_mostly_follows_the_gate_s_steps(self):
        acts = gate_search.actions(("a", "b"), (1, 2), checks=False)
        taken = gate_search.random_sequence(random.Random(1), acts, 12, gate_search.GIT_POLICIES["arm"])
        self.assertEqual(len(taken), 12)
        self.assertIn("claim", {a.kind for a in taken})


if __name__ == "__main__":
    unittest.main()
