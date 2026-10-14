"""The five scenarios of DESIGN.md section 5.2 (validation/scenarios.py),
played by validation/run.py: every expectation holds, and every result is
what validation/known.json records (since build step 5): green on every
step as built, the careless lease of S01c refused by the guard since F7's
fix, and each finding a step names red at that step, and nowhere else,
once the oracle stops reading the finding as built. The scenarios are the fast lane's first
part and run in a second."""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import known, oracle, run, scenarios  # noqa: E402

BUDGET_SECONDS = 15


def results(record):
    return {(at, v.guarantee) for at, v in record.violations}


class EveryScenario(unittest.TestCase):
    def test_the_five_scenarios_and_their_variants_are_there(self):
        self.assertEqual(sorted(scenarios.SCENARIOS), ["S01", "S01c", "S01l-account", "S01l-agent", "S02", "S02l",
                                                       "S03", "S04", "S05a", "S05b", "S05q"])
        for name, scenario in scenarios.SCENARIOS.items():
            with self.subTest(scenario=name):
                self.assertEqual(scenario.name, name)
                self.assertTrue(scenario.guarantees)
                self.assertGreater(len(scenario.steps), 3)

    def test_every_expectation_holds_and_every_result_is_what_known_json_records_as_built(self):
        began = time.perf_counter()
        for name, scenario in scenarios.SCENARIOS.items():
            with self.subTest(scenario=name):
                record = run.run_scenario(scenario)
                self.assertEqual(record.mismatches, [], "\n".join(record.timeline))
                self.assertEqual(known.differences("scenario", name, results(record)), [], "\n".join(record.timeline))
                self.assertGreater(record.checks, 3)
                self.assertTrue(record.pages)
        self.assertLess(time.perf_counter() - began, BUDGET_SECONDS)

    def test_the_careless_lease_of_s01c_is_refused_and_the_claim_stays_a_s(self):
        """F7, fixed: the guard reads the claim file on origin."""
        record = run.run_scenario(scenarios.SCENARIOS["S01c"])
        self.assertEqual(record.violations, [])
        self.assertEqual(record.mismatches, [])
        self.assertIn("   0 h  lease-onto b 1: refused by the guard", record.timeline)

    def test_each_finding_a_step_names_turns_red_there_when_not_read_as_built(self):
        """Each switch known.json names on a scenario, off in turn: the
        finding is red where the file records it and nowhere else. The
        other switches are tried on the traces, which play in milliseconds
        (tests/test_validation_traces.py), not on every scenario."""
        named = {(name, f) for name in scenarios.SCENARIOS for f in known.findings_named("scenario", name)
                 if f in known.AS_BUILT}
        self.assertEqual({f for _, f in named}, {"F2"})
        for name, finding in sorted(named):
            with self.subTest(scenario=name, finding=finding):
                scenario = scenarios.SCENARIOS[name]
                record = run.run_scenario(scenario, oracle.AS_BUILT - {finding})
                self.assertEqual(known.differences("scenario", name, results(record), finding), [],
                                 "\n".join(record.timeline))

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
