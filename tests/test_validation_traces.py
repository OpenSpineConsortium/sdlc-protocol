"""The regression traces of DESIGN.md 5.3, one per finding F1 to F15
(validation/traces/, played by validation/replay.py), and known.json, the
record of what is red on purpose: every trace loads and plays with no
mismatch; its red results are exactly what known.json records, as built
and with each open finding's switch off in the oracle; a trace of a
finding fixed, in 0.4.1 or on main after it, is green and turns red at
the step the design's table names under the mutant that undoes the fix;
and every entry of
known.json names a trace or scenario, a step it has, a guarantee the
invariants know and a finding of DESIGN.md's section 13."""

import re
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sdlc_protocol  # noqa: E402
from validation import invariants, known, mutants, oracle, replay, scenarios  # noqa: E402

DESIGN = (ROOT / "validation" / "DESIGN.md").read_text(encoding="ascii")
FINDINGS_IN_DESIGN = set(re.findall(r"^\*\*(F\d+)\. ", DESIGN, re.M))
BUDGET_SECONDS = 10
# A fixed finding, each mutant of validation/mutants.py that undoes a part
# of its fix, the step the trace then goes red at (numbered as played, an
# announce counting as three) and the guarantee DESIGN.md's table names;
# F6's fix has two parts, the render's sort key and the quoted path. F9's
# trace goes red at its first board run, step 2, where the draft level
# with main is told it moved once the adapter reads the comparison's
# ahead_by for behind_by, and at its second, step 4, where the draft
# behind main hears nothing once the adapter takes GitHub's word alone;
# F13's at its first board run, step 4, where b's draft lands before a's,
# whose branch was made first, once the order is by number again or once
# a draft is dated by its opening; at its second, step 5, where GitHub
# refuses the first read of the activity, the run fails once the refusal
# escapes, and the drafts land by their openings once no branch is asked
# about on its own; and at its third, step 7, where GitHub refuses every
# read, c's claim, which nothing dates then, lands first once such work
# counts first. F15's at its run that fails at the comment, step 6, where
# the todo the release put on stays on once the putting back forgets it,
# and at the next run, step 7, where the release puts no todo on once the
# rule is gone.
UNDONE = {
    "F1": [("the comment before the labels", 5, "G3L")],
    "F3": [("the guard passes --force", 1, "G11a")],
    "F4": [("each leg dealt from its own look", 9, "G14")],
    "F14": [("given to workers needing the label todo", 0, "G9")],
    "F5": [("a claim number with leading zeros", 2, "G1")],
    "F6": [("a sort key that calls int after isdigit", 1, "G15"), ("a branch name sent unquoted", 3, "G15")],
    "F7": [("the guard passes a lease onto another's claim", 1, "G11a")],
    "F9": [("an opaque state read by GitHub's word alone", 4, "G10"), ("the comparison read by ahead_by", 2, "G10")],
    "F10": [("a finished draft's release by hand not seen", 9, "G5")],
    "F11": [("a refused comment stops the run", 6, "G15")],
    "F12": [("main moved told only in the push's run", 8, "G10")],
    "F13": [("the landing order by number", 4, "G7"), ("a pull request dated by its opening", 4, "G7"),
            ("a refused activity read stops the run", 5, "G15"), ("a branch never asked about on its own", 5, "G7"),
            ("work nothing dates landing first", 7, "G7")],
    "F15": [("a release that leaves todo off", 7, "G3"), ("a failed release leaves its todo on", 6, "G3L")],
}
# The thirteen fixed findings: F1, F3, F5, F6, F10 and F11 in 0.4.1, and
# F4, F7, F9, F12, F13, F14 and F15 on main after it (known.json's
# fixed_after).
FIXED = ("F1", "F3", "F4", "F5", "F6", "F7", "F9", "F10", "F11", "F12", "F13", "F14", "F15")
# Findings red as built, which no rule of the library answers: none since
# F4's fix; the test keeps the case for the next one.
RED_AS_BUILT = ()


class TheTraces(unittest.TestCase):
    def setUp(self):
        self.traces = replay.traces()

    def test_one_trace_per_finding_of_the_design(self):
        self.assertEqual(sorted(self.traces, key=lambda f: int(f[1:])), list(replay.FINDINGS))
        self.assertEqual(set(replay.FINDINGS), FINDINGS_IN_DESIGN)
        for finding, trace in self.traces.items():
            with self.subTest(finding=finding):
                self.assertEqual(trace.path.stem, finding)
                self.assertEqual(trace.scenario.name, finding)
                self.assertTrue(trace.title and trace.status)
                self.assertTrue(trace.scenario.steps)
                self.assertTrue(set(trace.breaks) <= set(invariants.GUARANTEES))

    def test_every_trace_plays_with_no_mismatch_and_is_red_where_known_json_says(self):
        """As built, and with each switch of the oracle off in turn: a
        switch off shows its own finding where the file records it and
        nothing else, and the traces of the fixed findings stay green."""
        began = time.perf_counter()
        for finding, trace in self.traces.items():
            for switched_off in (None, *sorted(known.AS_BUILT)):
                with self.subTest(finding=finding, switched_off=switched_off):
                    reading = oracle.AS_BUILT if switched_off is None else oracle.AS_BUILT - {switched_off}
                    record = replay.replay(trace, reading)
                    self.assertEqual(record.mismatches, [], "\n".join(record.timeline))
                    found = replay.results(record)
                    self.assertEqual(known.differences("trace", finding, found, switched_off), [],
                                     "\n".join(record.timeline))
        self.assertLess(time.perf_counter() - began, BUDGET_SECONDS)

    def test_the_fixed_findings_are_green_and_the_open_ones_red_as_the_design_says(self):
        for finding, trace in self.traces.items():
            with self.subTest(finding=finding):
                entries = known.entries_of("trace", finding)
                if finding in FIXED:
                    self.assertIn("fixed", trace.status)
                    self.assertEqual(entries, [])
                elif finding in RED_AS_BUILT:
                    self.assertTrue(entries and all(e.finding == finding for e in entries))
                    self.assertNotIn(finding, known.AS_BUILT)
                elif finding == "F8":
                    self.assertEqual((entries, trace.breaks), ([], ()))
                else:
                    self.assertIn(finding, known.AS_BUILT)
                    self.assertTrue(entries and all(e.finding == finding for e in entries))
                self.assertTrue({e.guarantee for e in entries} <= set(trace.breaks))

    def test_each_fixed_finding_s_trace_turns_red_under_the_mutant_that_undoes_its_fix(self):
        """So a trace recorded green from the start is known to bite: the
        step and the guarantee are the design's table's (DESIGN.md, 5.3)."""
        self.assertEqual(sorted(UNDONE), sorted(FIXED))
        for finding, undoings in UNDONE.items():
            for mutant, step, guarantee in undoings:
                with self.subTest(finding=finding, mutant=mutant):
                    trace = self.traces[finding]
                    self.assertEqual(replay.results(replay.replay(trace)), set())
                    with mutants.switched_on(mutants.MUTANTS[mutant]):
                        record = replay.replay(trace)
                    self.assertIn((step, guarantee), replay.results(record), "\n".join(record.timeline))
                    self.assertIn(guarantee, trace.breaks)

    def test_f4_s_trace_deals_from_the_run_s_one_look_and_twice_under_its_mutant(self):
        """The run looks before main moves, so c is dealt nothing; with
        each leg looking for itself, c's later look deals #6 twice."""
        trace = self.traces["F4"]
        record = replay.replay(trace)
        self.assertEqual(replay.results(record), set())
        self.assertIn("{'a': 'take 5', 'b': 'take 6', 'c': 'none'}", record.timeline[-1])
        with mutants.switched_on(mutants.MUTANTS["each leg dealt from its own look"]):
            record = replay.replay(trace)
        self.assertEqual({(v.guarantee, v.detail) for _, v in record.violations},
                         {("G14", "#6 dealt twice from differing looks: {'a': 'take 5', 'b': 'take 6', 'c': 'take 6'}")})

    def test_f7_s_trace_is_green_and_red_for_g1_too_under_its_mutant(self):
        """The fence refuses the careless lease (G11a at the lease); with
        it gone git takes the lease and the board names b (G1 too)."""
        trace = self.traces["F7"]
        self.assertEqual(replay.results(replay.replay(trace)), set())
        with mutants.switched_on(mutants.MUTANTS["the guard passes a lease onto another's claim"]):
            record = replay.replay(trace)
        self.assertEqual(replay.results(record), {(1, "G11a"), (1, "G1"), (2, "G1")})
        self.assertIn(("G1", "the board names b for claude/1, whose holder is a"),
                      {(v.guarantee, v.detail) for _, v in record.violations})

    def test_f3_s_commands_are_judged_by_git_s_recorded_answers(self):
        record = replay.replay(self.traces["F3"])
        self.assertEqual(record.checks, 6)
        self.assertTrue(all("judged by git" in line for line in record.timeline))

    def test_a_trace_reads_as_a_report(self):
        trace = self.traces["F1"]
        text = replay.report(trace, replay.replay(trace))
        self.assertIn("F1 (fixed in 0.4.1 (#11))", text)
        self.assertIn("red: none", text)
        self.assertIn("board board schedule", text)


class KnownJson(unittest.TestCase):
    def test_the_oracle_s_switches_are_the_file_s(self):
        self.assertEqual(oracle.AS_BUILT, known.AS_BUILT)
        self.assertEqual(known.AS_BUILT, frozenset({"F2"}))

    def test_every_entry_names_a_step_of_a_trace_or_scenario_a_guarantee_and_a_finding(self):
        traces = replay.traces()
        for entry in known.ENTRIES:
            with self.subTest(entry=entry):
                steps = (traces[entry.name].scenario if entry.kind == "trace" else scenarios.SCENARIOS[entry.name]).steps
                self.assertTrue(0 <= entry.step < len(steps))
                self.assertIn(entry.guarantee, invariants.GUARANTEES)
                self.assertIn(entry.finding, FINDINGS_IN_DESIGN)
                self.assertEqual(steps[entry.step].shows, entry.finding)

    def test_every_step_a_trace_or_scenario_says_shows_a_finding_is_an_entry(self):
        shown = {("trace", f, i, s.shows) for f, t in replay.traces().items() for i, s in enumerate(t.scenario.steps)
                 if s.shows}
        shown |= {("scenario", n, i, s.shows) for n, sc in scenarios.SCENARIOS.items() for i, s in enumerate(sc.steps)
                  if s.shows}
        self.assertEqual(shown, {(e.kind, e.name, e.step, e.finding) for e in known.ENTRIES})

    def test_a_finding_s_switch_in_the_oracle_has_entries_to_show_it(self):
        for finding in sorted(known.AS_BUILT):
            with self.subTest(finding=finding):
                self.assertTrue([e for e in known.ENTRIES if e.finding == finding])

    def test_expected_reads_the_file_as_its_docstring_says(self):
        self.assertEqual(known.expected("trace", "F4"), set())
        self.assertEqual(known.expected("trace", "F7"), set())
        self.assertEqual(known.expected("trace", "F2"), set())
        self.assertEqual(known.expected("trace", "F2", "F2"), {(4, "G7")})
        self.assertEqual(known.expected("scenario", "S05q", "F2"), {(12, "G7")})
        self.assertEqual(known.expected("scenario", "S05q", "F12"), set())
        self.assertEqual(known.differences("trace", "F2", set(), "F2"),
                         ["trace F2 with F2 read from the spec: recorded red and green: step 4, G7"])
        self.assertEqual(known.differences("trace", "F1", {(0, "G3")}),
                         ["trace F1 as built: red and not recorded: step 0, G3"])

    def test_a_finding_fixed_on_main_is_recorded_with_the_last_version_that_has_it(self):
        """fixed_after is the canary's: it runs the release the canary
        repository vendors, so a finding fixed on main is still known there
        at that version and before, and new from the next release on."""
        self.assertEqual(known.FIXED_AFTER,
                         {"F4": "0.4.1", "F7": "0.4.1", "F9": "0.4.1", "F12": "0.4.1", "F13": "0.4.1", "F14": "0.4.1",
                          "F15": "0.4.1"})
        current = known.version_key(sdlc_protocol.__version__)
        for finding, version in known.FIXED_AFTER.items():
            with self.subTest(finding=finding):
                self.assertIn(finding, FINDINGS_IN_DESIGN)
                self.assertNotIn(finding, known.AS_BUILT, "a finding is as built or fixed, never both")
                self.assertIsNotNone(known.version_key(version))
                self.assertLessEqual(known.version_key(version), current,
                                     "the last version with a finding is one main has stated, never a later one")
        self.assertEqual(known.as_built_for("0.4.1"), known.AS_BUILT | {"F4", "F7", "F9", "F12", "F13", "F14", "F15"})
        self.assertEqual(known.as_built_for("0.4.0"), known.AS_BUILT | {"F4", "F7", "F9", "F12", "F13", "F14", "F15"})
        self.assertEqual(known.as_built_for("0.4.2"), known.AS_BUILT, "the next release carries the fixes")
        self.assertEqual(known.as_built_for("0.5.0"), known.AS_BUILT)
        self.assertEqual(known.as_built_for("1.0.0"), known.AS_BUILT)
        for none in (None, "", "unknown", "0.4", "0.4.1-dirty", "1" * 10 + ".0.0"):
            self.assertEqual(known.as_built_for(none), known.AS_BUILT, none)
        self.assertEqual(known.version_key("0.4.1"), (0, 4, 1))
        self.assertGreater(known.version_key("0.10.0"), known.version_key("0.9.9"))


if __name__ == "__main__":
    unittest.main()
