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
from validation import gate_search, git_store, lanes  # noqa: E402


class OverMemory(unittest.TestCase):
    def test_every_policy_to_depth_4_breaks_g13_nowhere(self):
        for name in gate_search.POLICIES:
            with self.subTest(policy=name):
                found = gate_search.explore(name, 4)
                self.assertGreater(found.states, 500)
                self.assertEqual(found.violations, ())

    def test_an_exploration_the_budget_stops_says_so_and_the_lane_reds_it(self):
        stopped = gate_search.explore("account", 8, budget=0.0)
        self.assertTrue(stopped.stopped)
        self.assertFalse(gate_search.explore("account", 2).stopped)
        results = lanes.fresh_results()
        results["gate"] = {"memory": [{"policy": "account", "depth": 8, "states": stopped.states, "transitions": 1,
                                       "seconds": 0.0, "stopped": True, "violations": [], "violation_count": 0}],
                           "s05g": {"timeline": [], "mismatches": [], "violations": []},
                           "git": {"sequences": 0, "acts": 0, "merges": 0, "main_moved": 0, "refusals": 0, "violations": [],
                                   "seconds": 0.0}}
        self.assertTrue(gate_search.red(results["gate"]))
        self.assertEqual(lanes.red_lines(results), [f"gate account: stopped by the budget after {stopped.states} states, not done"])
        self.assertIn("stopped by the budget", lanes.summary_lines(results)[0])

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

    def test_the_shared_remote_s_repositories_carry_the_git_world_s_settings(self):
        """git hands the receive-pack of a local remote none of the pushing
        side's -c settings, so the bare repository's own config must turn
        the automatic maintenance off, or it races the folder's removal."""
        remote = gate_search.SharedRemote()
        try:
            clone = remote.clone("a")
            for repo in (remote.bare, remote.root / "seed", clone):
                for name, value in git_store.SETTINGS:
                    with self.subTest(repo=repo.name, setting=name):
                        self.assertEqual(gate_search.git(repo, "config", "--local", "--get", name), value)
        finally:
            remote.close()
        self.assertFalse(remote.root.exists())

    def test_a_sequence_mostly_follows_the_gate_s_steps(self):
        acts = gate_search.actions(("a", "b"), (1, 2), checks=False)
        taken = gate_search.random_sequence(random.Random(1), acts, 12, gate_search.GIT_POLICIES["arm"])
        self.assertEqual(len(taken), 12)
        self.assertIn("claim", {a.kind for a in taken})


if __name__ == "__main__":
    unittest.main()
