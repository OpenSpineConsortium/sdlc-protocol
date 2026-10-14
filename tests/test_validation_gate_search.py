"""The gate's lane (validation/gate_search.py): G13 over every
interleaving of two agents' acts to a small depth under the four
policies over MemoryWorld, S05g over BareGitWorld, and a few seeded
sequences over git that reach a merge. The deep lane goes to depth 8
and forty sequences."""

import random
import sys
import unittest
from pathlib import Path
from unittest import mock

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

    def test_the_lane_sees_a_gate_that_breaks_part_2_or_part_3(self):
        """Three faults of the gate G13 names, each made here by hand: a
        pull request marked ready by someone other than its author, a mark
        that keeps the claim, and mark_ready merging where the owner or
        auto-merge lands. The lane meets each: at depth 4, or at depth 5
        where a merge needs the check made green first."""

        def anybody_marks_ready(policy, queue, number, who):
            pull = gate.pull_of(policy, queue, number)
            if pull.state == gate.MERGED:
                raise gate.Refused("merged already")
            return pull

        def keeps_the_claim(queue, pull):
            pull.state = gate.READY

        def merges_everywhere(policy, world, queue, number, who):
            pull = gate.pull_to_ready(policy, queue, number, who)
            attempt = gate.prepare(policy, world, number)
            gate.require_green(policy, world, attempt.branch)
            gate.begin_attempt(queue, pull)
            return gate.finish_attempt(policy, world, queue, attempt)

        faults = [("pull_to_ready", anybody_marks_ready, "arm", 4, "not its author"),
                  ("release_claim", keeps_the_claim, "account", 4, "with its claim held"),
                  ("mark_ready", merges_everywhere, "account", 5, "merged by ready where owner merges"),
                  ("mark_ready", merges_everywhere, "team", 5, "merged by ready where auto-merge merges")]
        for name, fault, policy, depth, words in faults:
            with self.subTest(fault=name, policy=policy), mock.patch.object(gate, name, fault):
                found = gate_search.explore(policy, depth)
                self.assertTrue(any(words in problem for _, problems in found.violations for problem in problems),
                                found.violations[:2])

    def test_a_refusal_of_the_wrong_kind_is_a_violation(self):
        """Part 8's expected code was read off the error's own class, whose
        code it always carries, so a gate that refused another's claim
        with the plain code passed; the code is read from the state the
        act found now."""
        claimable = gate.claimable

        def plain_refusal(policy, queue, number, who):
            issue = queue.issue(number)
            if issue.claimant and issue.claimant != who and not issue.closed:
                raise gate.Refused(f"#{number} is claimed by {issue.claimant}")
            return claimable(policy, queue, number, who)

        with mock.patch.object(gate, "claimable", plain_refusal):
            found = gate_search.explore("account", 3)
        self.assertTrue(any("exit code 1 for Refused where another holds the work, which owes 2" in problem
                            for _, problems in found.violations for problem in problems), found.violations[:2])
        queue = gate.Queue({1: "a", 2: "a"}, open_queue=True)
        gate.claim(gate_search.POLICIES["account"], queue, 1, "a")
        self.assertTrue(gate_search.anothers(queue, gate_search.Act("claim", "b", 1)))
        self.assertFalse(gate_search.anothers(queue, gate_search.Act("claim", "a", 1)))
        self.assertFalse(gate_search.anothers(queue, gate_search.Act("claim", "b", 2)))
        self.assertTrue(gate_search.anothers(queue, gate_search.Act("open", "b", 1)))
        self.assertFalse(gate_search.anothers(queue, gate_search.Act("ready", "b", 1)))

    def test_the_lane_s_own_exclusions_are_no_refusals_of_the_gate(self):
        """A push of a branch its agent did not claim and a landing where
        the gate merges on mark_ready were refused by the lane itself with
        a gate error, then judged by part 8 and counted as the gate's
        refusals; they are not offered now."""
        policy = gate_search.POLICIES["arm"]
        queue = gate.Queue({1: "a"}, open_queue=True)
        gate.claim(policy, queue, 1, "a")
        self.assertTrue(gate_search.offered(policy, queue, gate_search.Act("push", "a", 1, gate.CLEAN)))
        self.assertFalse(gate_search.offered(policy, queue, gate_search.Act("push", "b", 1, gate.CLEAN)))
        self.assertFalse(gate_search.offered(policy, queue, gate_search.Act("land", issue=1)))
        self.assertTrue(gate_search.offered(gate_search.GIT_POLICIES["owner"], queue, gate_search.Act("land", issue=1)))
        with self.assertRaises(gate_search.NotOffered):
            gate_search.apply(policy, gate_search.World(), queue, gate_search.Act("push", "b", 1, gate.CLEAN),
                              gate_search.MemoryPusher())
        acts = gate_search.actions(("a", "b"), (1,), checks=False)
        taken = gate_search.random_sequence(random.Random(3), acts, 30, policy, (1,))
        self.assertNotIn("land", {a.kind for a in taken})
        steps, found = gate_search.play_over_git(policy, taken, (1,))
        self.assertEqual(found, [])
        self.assertTrue(all(isinstance(s.error, gate.GateError) for s in steps if s.error is not None))

    def test_a_merge_of_a_branch_in_conflict_is_a_violation(self):
        policy = gate_search.POLICIES["arm"]
        queue = gate.Queue({1: "a"}, open_queue=True)
        merged = gate.Outcome(1, gate.MERGED, "", 0, "t", "m", "m2")
        step = gate_search.StepWithCheck(gate_search.Act("land", "", 1), merged, None, "m", "m2", gate.GREEN,
                                         gate.CONFLICTING)
        self.assertIn("merged a branch standing conflicting", gate_search.problems(policy, queue, step, {}))
        moved = gate.Outcome(1, gate.MAIN_MOVED, "", 3, "t", "m", "m")
        step = gate_search.StepWithCheck(gate_search.Act("land", "", 1), moved, None, "m", "m", gate.GREEN, gate.CLEAN)
        self.assertIn("main moved answer for a branch standing clean", gate_search.problems(policy, queue, step, {}))

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

    def test_the_owner_s_move_of_main_is_recorded_as_a_move_over_git(self):
        """The world fetches once a hold and the owner's push goes round
        it, so the step recorded main after the move as it was before it,
        and forward() never examined a move; the pusher tells the world
        its next read fetches."""
        acts = [gate_search.Act("move_main"), gate_search.Act("claim", "a", 1), gate_search.Act("move_main")]
        steps, found = gate_search.play_over_git(gate_search.GIT_POLICIES["arm"], acts)
        self.assertEqual(found, [])
        self.assertEqual([s.main_after != s.main_before for s in steps], [True, False, True])
        self.assertEqual([s.main_before for s in steps[1:]], [s.main_after for s in steps[:2]])

    def test_main_moving_anywhere_but_forward_is_a_violation_over_git(self):
        remote = gate_search.SharedRemote()
        try:
            first = remote.main()
            gate_search.GitPusher(remote).move_main(None)
            second = remote.main()
            ahead = gate_search.StepWithCheck(gate_search.Act("move_main"), None, None, first, second, None)
            self.assertEqual(gate_search.forward(remote.bare, ahead), [])
            back = gate_search.StepWithCheck(gate_search.Act("move_main"), None, None, second, first, None)
            self.assertEqual(gate_search.forward(remote.bare, back),
                             [f"main moved from {second[:7]} to {first[:7]}, not forward"])
        finally:
            remote.close()

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
