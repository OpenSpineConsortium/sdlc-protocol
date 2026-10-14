"""The deep lane (validation/deep.py, shrink.py, lanes.py): the depth-first
search with its depth-aware digest set finds what the breadth-first
search finds and expands a state again when reached shallower; a
violation is shortened to a trace that still shows it and written as a
trace file replay.py plays, red in both worlds alike; conformance plays
a path in both worlds and compares what each left; git's answers for
the guard grammar recorded again match the file; known.json's search
entries are empty, since E-faults meets no known finding after F7's fix;
a search continues from its own checkpoint and starts over from
another's; and the lane as one run writes deep.json, resumes only its
own, and lands the other parts when one raises. The real spaces run for
hours (scripts/validate.py deep); here the spaces are small."""

import io
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims  # noqa: E402
from validation import deep, explore, guard_grammar, known, lanes, oracle, replay, run, shrink  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

SMALL = deep.Space("small", Scope(), (), 3)
ONE = deep.Space("one", Scope(workers=("a",)), (), 4)


def at_exactly_24_hours(last, now, idle_after=claims.IDLE_AFTER):
    return now - claims.parse_time(last) >= idle_after


def red(record):
    return sorted({(at, v.guarantee) for at, v in record.violations})


def broken_soak(seeds, steps, out):
    """A part that raises, at module level so the pool's workers get it by name."""
    raise RuntimeError("a walk that raised")


class TheSearch(unittest.TestCase):
    def test_depth_first_finds_what_breadth_first_finds_and_counts_the_states_alike(self):
        wide = explore.explore(SMALL.start(), SMALL.scope, SMALL.depth)
        first = deep.search_space(SMALL)
        self.assertEqual(first.states, wide.states)
        self.assertEqual(first.transitions, wide.transitions)
        self.assertEqual(first.found, ())
        self.assertTrue(first.complete)

    def test_a_mutant_s_violations_are_found_in_both_orders(self):
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            wide = explore.explore(ONE.start(), ONE.scope, ONE.depth)
            first = deep.search_space(ONE)
        self.assertEqual({(f.guarantee, f.detail) for f in first.found}, {(f.guarantee, f.detail) for f in wide.found})
        self.assertEqual(sum(f.count for f in first.found), sum(f.count for f in wide.found))

    def test_a_state_reached_shallower_is_expanded_again(self):
        """With a plain seen-set a state first met deep is never expanded
        when met again shallower, and what lies under it at the bound is
        missed; the digest set keeps the shallowest depth and expands
        again, so the count of states matches the breadth-first search's,
        which meets every state at its shallowest first."""
        space = deep.Space("again", Scope(workers=("a",)), (), 5)
        first = deep.search_space(space)
        wide = explore.explore(space.start(), space.scope, space.depth)
        self.assertEqual(first.states, wide.states)

    def test_a_budget_stops_the_search_and_says_so(self):
        stopped = deep.search_space(deep.SPACES["E-deep"], budget=0.0)
        self.assertTrue(stopped.stopped)
        self.assertFalse(stopped.complete)

    def test_a_search_continues_from_its_checkpoint_to_the_same_end(self):
        """Under a clock that ticks a millisecond a reading, the budget of
        a tenth of a second stops the search at the same place on every
        machine; the space is ONE under the mutant that releases a claim
        at exactly 24 hours, so the checkpoint carries violations too."""
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            whole = deep.search_space(ONE)
            self.assertTrue(whole.found)
            with tempfile.TemporaryDirectory() as folder:
                checkpoint = Path(folder) / "one.pickle"
                said = []
                with mock.patch.object(deep.time, "perf_counter", side_effect=(n / 1000 for n in itertools.count())):
                    stopped = deep.search_space(ONE, budget=0.1, say=said.append, checkpoint=str(checkpoint))
                self.assertTrue(stopped.stopped)
                self.assertTrue(checkpoint.exists())
                self.assertLess(stopped.states, whole.states)
                self.assertLess(len(stopped.found), len(whole.found))
                resumed = deep.search_space(ONE, say=said.append, checkpoint=str(checkpoint), checkpoint_every=0.0)
        self.assertFalse(resumed.stopped)
        self.assertEqual((resumed.states, resumed.transitions), (whole.states, whole.transitions))
        self.assertEqual(sorted(resumed.found), sorted(whole.found))
        self.assertGreaterEqual(resumed.seconds, stopped.seconds)
        self.assertTrue(any(line.startswith("continued from the checkpoint") for line in said))
        self.assertTrue(any(line.startswith("checkpoint written") for line in said))

    def test_a_search_starts_over_from_a_checkpoint_another_search_wrote(self):
        """A checkpoint carries the stamp of the search that wrote it: its
        start, scope, depth, library and code. One written at depth 3 and
        found by the search at depth 4 is not continued, since the states
        it had seen would never be expanded to the deeper depth."""
        whole = deep.search_space(SMALL, depth=4)
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "small.pickle"
            deep.search_space(SMALL, depth=3, checkpoint=str(checkpoint), checkpoint_every=0.0)
            self.assertEqual(deep.read_checkpoint(checkpoint)["stamp"]["depth"], 3)
            said = []
            resumed = deep.search_space(SMALL, depth=4, say=said.append, checkpoint=str(checkpoint))
            self.assertEqual(deep.stamp_differences(deep.read_checkpoint(checkpoint), deep.stamp_of(SMALL.start(), SMALL.scope, 4)),
                             ["depth"])
        self.assertEqual((resumed.states, resumed.transitions), (whole.states, whole.transitions))
        self.assertTrue(any("starts over" in line and "depth differ" in line for line in said))
        self.assertFalse(any(line.startswith("continued from the checkpoint") for line in said))
        other = deep.stamp_of(ONE.start(), ONE.scope, 4)
        self.assertEqual(sorted(deep.stamp_differences({"stamp": other}, deep.stamp_of(SMALL.start(), SMALL.scope, 4))),
                         ["scope", "start"])
        self.assertEqual(deep.stamp_differences({}, other), sorted(other, key=list(other).index))


class Shortening(unittest.TestCase):
    def test_a_violation_is_shortened_to_a_trace_that_still_shows_it(self):
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            found = deep.search_space(ONE)
            longest = max(found.found, key=lambda f: len(f.trace))
            target = (longest.guarantee, longest.detail)
            shorter = shrink.shrink(ONE.start, ONE.scope, longest.trace, target)
            self.assertTrue(shrink.reproduces(ONE.start, ONE.scope, shorter.trace, target))
            for at in range(len(shorter.trace)):
                without = shorter.trace[:at] + shorter.trace[at + 1:]
                self.assertFalse(shrink.reproduces(ONE.start, ONE.scope, without, target))
        self.assertLessEqual(len(shorter.trace), len(longest.trace))

    def test_a_part_s_total_bounds_the_shortening_of_all_its_violations(self):
        """With no time left in the total, each violation is written as
        the search met it, unshortened, so a red search ends in time."""
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            found = deep.search_space(ONE)
            with tempfile.TemporaryDirectory() as folder:
                written = deep.write_counterexamples(folder, ONE, found.found, budget=60.0, total=0.0)
                files = sorted(p.name for p in (Path(folder) / deep.TRACES).glob("*.json"))
        self.assertEqual(len(written), len(found.found))
        self.assertEqual(len(files), len(found.found))
        self.assertTrue(all(w["shortened_to"] == w["found_at"] for w in written), written)

    def test_a_counterexample_file_replays_red_in_memory_and_over_git(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
                found = deep.search_space(ONE)
                written = deep.write_counterexamples(folder, ONE, found.found)
            self.assertTrue(written)
            trace = replay.load(written[0]["file"])
            self.assertEqual(trace.status, "counterexample")
            self.assertEqual(trace.scenario.steps[0].verb, "board")
            with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
                in_memory = replay.replay(trace)
                over_git = replay.replay(trace, world=run.GIT)
            self.assertTrue(red(in_memory))
            self.assertEqual(red(in_memory), red(over_git))
            self.assertEqual(in_memory.mismatches, [])


class Conformance(unittest.TestCase):
    def test_sampled_paths_agree_in_both_worlds(self):
        results = deep.conformance([SMALL], count=3, length=4)
        self.assertEqual(results["paths"], 3)
        self.assertEqual(results["disagreements"], [])

    def test_a_difference_between_the_worlds_is_reported_by_step_and_field(self):
        path = deep.sample_paths(SMALL, 1, 3)[0]
        self.assertTrue(path)
        with mock.patch.object(deep, "observe", side_effect=lambda model, outcomes: {"refs": sorted(model.refs)}):
            self.assertEqual(deep.compare(SMALL, path), [])
        counting = iter(range(10 ** 6))
        with mock.patch.object(deep, "observe", side_effect=lambda model, outcomes: {"n": next(counting)}):
            differences = deep.compare(SMALL, path)
        self.assertTrue(differences)
        self.assertEqual({d["field"] for d in differences}, {"n"})


class TheGuardRecordedAgain(unittest.TestCase):
    def test_git_answers_the_grammar_as_the_file_records_for_a_few_lists(self):
        lists = ["push origin claude/7", "push --force origin claude/7", "push origin HEAD:main"]
        with tempfile.TemporaryDirectory() as scratch:
            with mock.patch.object(deep.guard_grammar, "argument_lists", lambda: lists):
                result = deep.guard_recorded_again(Path(scratch) / "guard_git.json")
        self.assertEqual(result["recorded"], 3)
        self.assertEqual(result["differing"], [])
        recorded = len(guard_grammar.read_answers())
        self.assertEqual(len(result["missing"]), recorded - 3)


class KnownSearches(unittest.TestCase):
    def test_e_faults_meets_nothing_at_depth_two_since_f7s_fix(self):
        # Until F7's fix the careless lease showed G1 here at depth one,
        # and known.json's searches named it; the guard's fence refuses
        # the lease now, in both worlds, and the file names nothing.
        found = deep.search_space(deep.SPACES["E-faults"], depth=2)
        self.assertEqual([(f.guarantee, f.detail) for f in found.found], [])
        self.assertEqual(known.SEARCH_ENTRIES, ())
        self.assertEqual(known.search_differences("E-faults", []), [])

    def test_an_entry_names_the_violation_it_matches_and_is_owed_when_none_does(self):
        entry = known.SearchEntry("E-faults", "G1", "the board names * for claude/*, whose holder is *", "F7")
        shown = "the board names b for claude/1, whose holder is a"
        with mock.patch.object(known, "SEARCH_ENTRIES", (entry,)):
            self.assertEqual(known.search_finding("E-faults", "G1", shown), "F7")
            self.assertIsNone(known.search_finding("E-deep", "G1", shown))
            self.assertIsNone(known.search_finding("E-faults", "G9", shown))
            self.assertEqual(known.search_differences("E-faults", [("G1", shown)]), [])
            self.assertEqual(len(known.search_differences("E-faults", [])), 1)
            self.assertEqual(known.search_differences("E-deep", []), [])


class TheLaneAsOneRun(unittest.TestCase):
    def test_the_quick_run_writes_deep_json_and_is_green(self):
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search", "soak", "conformance"), jobs=2, quick=True)
            self.assertEqual(lanes.run(settings, out), 0, out.getvalue())
            data = json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))
        self.assertEqual(sorted(s["space"] for s in data["searches"]), ["E-deep", "E-faults", "E-two"])
        self.assertEqual(data["red"], [])
        self.assertIn("search E-faults: depth 2", out.getvalue())
        self.assertIn("0 violations", out.getvalue())
        self.assertIn("deep lane: ok", out.getvalue())

    def test_a_resumed_run_runs_only_the_parts_the_file_lacks_and_drops_the_checkpoints(self):
        """The first run checkpoints every 50 ms, so each search leaves a
        file for the lane to drop; the resumed run runs the soak alone."""
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True, checkpoint_every=0.05)
            self.assertEqual(lanes.run(settings, out), 0, out.getvalue())
            self.assertEqual(out.getvalue().count(" done at "), 3)
            first = json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))
            self.assertFalse(list((Path(folder) / lanes.CHECKPOINTS).glob("*")))
            again = io.StringIO()
            settings = lanes.Settings(folder, only=("search", "soak"), jobs=2, quick=True, resume=True)
            self.assertEqual(lanes.run(settings, again), 0, again.getvalue())
            second = json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))
        done = [line for line in again.getvalue().splitlines() if " done at " in line]
        self.assertEqual(len(done), 2)
        self.assertTrue(all(line.strip().startswith("soak ") for line in done), done)
        self.assertEqual(second["searches"], first["searches"])
        self.assertEqual(second["run"], first["run"])
        self.assertEqual(len(second["soak"]["chunks"]), 2)
        self.assertGreaterEqual(second["seconds"], first["seconds"])
        self.assertEqual(lanes.done_parts(second), {"search E-two", "search E-deep", "search E-faults", "soak 0", "soak 2"})

    def test_a_search_the_budget_stopped_is_run_again_from_its_checkpoint_on_resume(self):
        """A stopped search keeps its checkpoint and is not done; the
        resumed run, with no budget, continues it to the whole space."""
        whole = {name: deep.search_space(deep.SPACES[name], depth=depth).states
                 for name, depth in lanes.QUICK["depths"].items()}
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True, budget=0.0)
            self.assertEqual(lanes.run(settings, out), 1, out.getvalue())
            stopped = json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))
            self.assertTrue(all(s["stopped"] for s in stopped["searches"]))
            self.assertEqual(sorted(p.stem for p in (Path(folder) / lanes.CHECKPOINTS).glob("*.pickle")),
                             sorted(deep.SPACES))
            self.assertEqual(lanes.done_parts(stopped), set())
            again = io.StringIO()
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True)
            self.assertEqual(lanes.run(settings, again), 0, again.getvalue())
            resumed = json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))
            self.assertFalse(list((Path(folder) / lanes.CHECKPOINTS).glob("*.pickle")))
        self.assertEqual(len(stopped["red"]), 3)
        self.assertTrue(all(line.endswith("not done") for line in stopped["red"]), stopped["red"])
        self.assertEqual(len(resumed["searches"]), 3)
        self.assertEqual({s["space"]: s["states"] for s in resumed["searches"]}, whole)
        self.assertFalse(any(s["stopped"] for s in resumed["searches"]))
        self.assertEqual(resumed["red"], [])

    def test_a_run_that_is_not_a_resume_starts_clean_and_a_resume_under_other_settings_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            stale = Path(folder) / lanes.CHECKPOINTS / "E-two.pickle"
            stale.parent.mkdir()
            stale.write_bytes(b"not this run's")
            (Path(folder) / deep.TRACES).mkdir()
            (Path(folder) / deep.TRACES / "G1-1.json").write_text("{}", encoding="ascii")
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            self.assertEqual(lanes.run(settings, out), 0, out.getvalue())
            self.assertFalse(stale.exists())
            self.assertFalse((Path(folder) / deep.TRACES / "G1-1.json").exists())
            results = json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))
            self.assertEqual(results["run"], settings.record())
            with self.assertRaises(SystemExit) as refused:
                lanes.run(lanes.Settings(folder, only=("search",), jobs=2, quick=False, resume=True), io.StringIO())
        self.assertIn("other settings or other code", str(refused.exception))
        self.assertIn("quick", str(refused.exception))
        self.assertIn("depths", str(refused.exception))

    def test_a_part_that_raises_is_red_and_the_other_parts_still_land(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(lanes, "run_soak", broken_soak):
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search", "soak"), jobs=2, quick=True)
            self.assertEqual(lanes.run(settings, out), 1, out.getvalue())
            results = json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))
        self.assertEqual(sorted(s["space"] for s in results["searches"]), ["E-deep", "E-faults", "E-two"])
        self.assertEqual(sorted(e["part"] for e in results["errors"]), ["soak 0", "soak 2"])
        self.assertIn("a walk that raised", results["errors"][0]["traceback"])
        self.assertEqual(len(results["red"]), 2)
        self.assertIn("soak 0 raised: RuntimeError('a walk that raised')", results["red"])
        self.assertIn("raised at", out.getvalue())

    def test_a_stopped_search_is_red(self):
        results = lanes.fresh_results()
        results["searches"].append({"space": "E-deep", "depth": 8, "states": 1, "transitions": 1, "seconds": 0.0,
                                    "stopped": True, "violations": [], "differences": []})
        self.assertEqual(len(lanes.red_lines(results)), 1)
        self.assertIn("stopped by the budget", lanes.red_lines(results)[0])


if __name__ == "__main__":
    unittest.main()
