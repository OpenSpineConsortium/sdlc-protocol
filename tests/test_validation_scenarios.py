"""The five scenarios of DESIGN.md section 5.2 (validation/scenarios.py),
played by validation/run.py: every expectation holds, every guarantee is
green on every step as built, the one step that shows F7 is red for G1,
and each finding a step names turns red at that step once the oracle
stops reading the finding as built. The scenarios are the fast lane's
first part and run in a second."""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import oracle, run, scenarios  # noqa: E402

BUDGET_SECONDS = 15


def steps_showing(scenario, finding):
    return [i for i, step in enumerate(scenario.steps) if step.shows == finding]


class EveryScenario(unittest.TestCase):
    def test_the_five_scenarios_and_their_variants_are_there(self):
        self.assertEqual(sorted(scenarios.SCENARIOS), ["S01", "S01c", "S01l-account", "S01l-agent", "S02", "S02l",
                                                       "S03", "S04", "S05a", "S05b", "S05q"])
        for name, scenario in scenarios.SCENARIOS.items():
            with self.subTest(scenario=name):
                self.assertEqual(scenario.name, name)
                self.assertTrue(scenario.guarantees)
                self.assertGreater(len(scenario.steps), 3)

    def test_every_expectation_holds_and_every_guarantee_is_green_as_built(self):
        began = time.perf_counter()
        for name, scenario in scenarios.SCENARIOS.items():
            with self.subTest(scenario=name):
                record = run.run_scenario(scenario)
                self.assertEqual(record.mismatches, [], "\n".join(record.timeline))
                self.assertEqual(record.red_steps, steps_showing(scenario, "F7"), "\n".join(record.timeline))
                self.assertGreater(record.checks, 3)
                self.assertTrue(record.pages)
        self.assertLess(time.perf_counter() - began, BUDGET_SECONDS)

    def test_f7_is_red_for_g1_where_the_careless_worker_shows_it(self):
        record = run.run_scenario(scenarios.SCENARIOS["S01c"])
        self.assertEqual({v.guarantee for _, v in record.violations}, {"G1"})
        self.assertIn("the board names b for claude/1, whose holder is a", {v.detail for _, v in record.violations})

    def test_each_finding_a_step_names_turns_red_there_when_not_read_as_built(self):
        named = {(name, step.shows) for name, scenario in scenarios.SCENARIOS.items()
                 for step in scenario.steps if step.shows and step.shows != "F7"}
        self.assertEqual({f for _, f in named}, {"F2", "F12"})
        for name, finding in sorted(named):
            with self.subTest(scenario=name, finding=finding):
                scenario = scenarios.SCENARIOS[name]
                record = run.run_scenario(scenario, oracle.AS_BUILT - {finding})
                self.assertEqual(record.red_steps, steps_showing(scenario, finding), "\n".join(record.timeline))

    def test_a_scenario_plays_the_same_twice(self):
        scenario = scenarios.SCENARIOS["S03"]
        first, second = run.run_scenario(scenario), run.run_scenario(scenario)
        self.assertEqual(first.timeline, second.timeline)
        self.assertEqual(second.mismatches, [])

    def test_the_record_reads_as_a_timeline(self):
        record = run.run_scenario(scenarios.SCENARIOS["S02"])
        text = "\n".join(record.timeline)
        self.assertIn("claim a 1", text)
        self.assertIn("board board schedule", text)
        self.assertNotIn("mismatch", text)
        self.assertEqual(record.scenario, "S02")

    def test_a_prefix_gives_the_model_where_a_scenario_left_off(self):
        model = run.prefix(scenarios.SCENARIOS["S02"], 5)
        self.assertEqual(model.now, 24 * 60)
        self.assertIn("claude/1", model.refs)


if __name__ == "__main__":
    unittest.main()
