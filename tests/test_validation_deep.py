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
own, lands the other parts when one raises or its worker process dies,
runs a part again on resume once it raised or a budget stopped it, plays
the counterexamples a resumed search wrote, and refuses to start beside
a run in progress. The real spaces run for hours (scripts/validate.py
deep); here the spaces are small."""

import errno
import io
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
import warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims  # noqa: E402
from validation import cycle, deep, explore, guard_grammar, invariants, known, lanes, model as M, oracle, replay, run  # noqa: E402
from validation import shrink, soak, verbs  # noqa: E402
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


def dying_soak(seeds, steps, out):
    """A part whose worker process dies, as one killed for its memory does."""
    os._exit(3)


def one_megabyte():
    """A part whose result is more than a pipe holds."""
    return "x" * 1_000_000


def ends_slowly(signum, frame):
    time.sleep(0.5)
    os._exit(3)


def slow_to_end(ready):
    """A part that takes half a second to end once told to, so a parent
    that does not wait for it finds it still running."""
    signal.signal(signal.SIGTERM, ends_slowly)
    ready.set()
    time.sleep(600)


def waits_outside_its_work(ready):
    """A part's process where run_part's try does not hold it, as one
    sending its result is."""
    ready.set()
    time.sleep(600)


def a_live_process(test):
    """A process that lives until the test ends, standing for a run in
    progress: a marker naming the test's own process reads as a dead
    run's."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    test.addCleanup(process.wait)
    test.addCleanup(process.kill)
    return process.pid


def end_session(process):
    """A process started in a session of its own, ended with every process
    in its group, its workers among them, whatever the test saw."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def results_in(folder):
    return json.loads((Path(folder) / "deep.json").read_text(encoding="ascii"))


def wait_for(condition, seconds, every=0.1):
    """The condition's first true value within the seconds, else its last."""
    deadline = time.monotonic() + seconds
    while True:
        value = condition()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(every)


def children_of(pid):
    done = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    return [int(line) for line in done.stdout.split()]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class TheCodeDigest(unittest.TestCase):
    def test_it_covers_the_source_and_the_data_and_not_the_docs_or_the_kept_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("validation/a.py", "validation/known.json", "validation/README.md", "validation/runs/x/deep.json",
                         "validation/runs/x/traces/G1-E-two-1.json", "sdlc_protocol/b.py", "sdlc_protocol/spec/PROTOCOL.md"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("one\n", encoding="ascii")
            self.assertEqual(deep.code_files(root), ["validation/a.py", "validation/known.json", "sdlc_protocol/b.py"])
            first = deep.code_digest(root)
            for name in ("validation/README.md", "validation/runs/x/deep.json", "sdlc_protocol/spec/PROTOCOL.md"):
                (root / name).write_text("two\n", encoding="ascii")
            self.assertEqual(deep.code_digest(root), first)
            (root / "validation/known.json").write_text("two\n", encoding="ascii")
            second = deep.code_digest(root)
            self.assertNotEqual(second, first)
            (root / "sdlc_protocol/b.py").write_text("two\n", encoding="ascii")
            self.assertNotEqual(deep.code_digest(root), second)
        files = deep.code_files()
        for name in ("validation/known.json", "validation/guard_git.json", "validation/unproven.json", "validation/traces/F7.json",
                     "validation/deep.py", "sdlc_protocol/claims.py"):
            self.assertIn(name, files)
        self.assertFalse([f for f in files if f.startswith("validation/runs/") or f.endswith(".md")])


class TheSearch(unittest.TestCase):
    def test_depth_first_finds_what_breadth_first_finds_and_counts_the_states_alike(self):
        wide = explore.explore(SMALL.start(), SMALL.scope, SMALL.depth)
        first = deep.search_space(SMALL)
        self.assertEqual(first.states, wide.states)
        self.assertEqual(first.transitions, wide.transitions)
        self.assertEqual(first.found, ())
        self.assertTrue(first.complete)

    def test_a_mutant_s_violations_are_found_in_both_orders(self):
        """The breadth-first search keys a violation by its detail word for
        word, the deep one by its identity, with a sha read as any sha;
        the two agree under the identity, and count the same problems."""
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            wide = explore.explore(ONE.start(), ONE.scope, ONE.depth)
            first = deep.search_space(ONE)
        self.assertEqual({(f.guarantee, invariants.identity(f.detail)) for f in first.found},
                         {(f.guarantee, invariants.identity(f.detail)) for f in wide.found})
        self.assertLess(len(first.found), len(wide.found))
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


class TheIdentityOfAViolation(unittest.TestCase):
    def test_a_sha_in_the_detail_is_read_as_any_sha(self):
        rename = "made and not owed: ('rename', 'claude/1', 'released/1-6d6ae03')"
        self.assertEqual(invariants.identity(rename), "made and not owed: ('rename', 'claude/1', 'released/1-*')")
        self.assertEqual(invariants.identity("#100 heard <!-- sdlc-board: main-moved 0a3acec -->  2 times"),
                         "#100 heard <!-- sdlc-board: main-moved * -->  2 times")
        self.assertEqual(invariants.identity("the guard refused git push origin released/12-abcdef0:claude/12: x"),
                         "the guard refused git push origin released/12-*:claude/12: x")
        self.assertEqual(invariants.identity("released #1, which was not due"), "released #1, which was not due")

    def test_one_fault_met_from_several_prefixes_is_one_violation_the_shrink_can_shorten(self):
        """Under the mutant that releases at exactly 24 hours the rename
        of claude/1 was six violations, one per sha of the head the
        prefix left (a commit, a wait, the claim file dropped), each
        written on its own, and the one met after a wait could lose no
        wait, since the sha then differed; it is one now, and shortens to
        the claim, the wait and the run."""
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            found = deep.search_space(ONE)
            renames = [f for f in found.found if "'rename'" in f.detail]
            self.assertEqual(len(renames), 1)
            self.assertGreater(renames[0].count, 10)
            target = (renames[0].guarantee, renames[0].detail)
            # The record is the first meeting whole: the detail is the one
            # its own trace shows, not the last of the other prefixes'.
            self.assertEqual(shrink.shown(ONE.start, ONE.scope, deep.symbolic_trace(ONE, renames[0].trace), target),
                             renames[0].detail)
            shorter = shrink.shrink(ONE.start, ONE.scope, deep.symbolic_trace(ONE, renames[0].trace), target)
            self.assertEqual([v.name for v in shorter.trace], ["claim", "wait", "board"])
            with mock.patch.object(invariants, "step_checks", side_effect=lambda *a: [
                    invariants.Violation("G3", "made and not owed: ('rename', 'claude/1', 'released/1-0000000')"),
                    invariants.Violation("G3", "made and not owed: ('rename', 'claude/1', 'released/1-1111111')")]):
                walk = soak.walk(0, 3)
        self.assertEqual(len(walk.violations), 1)


class Shortening(unittest.TestCase):
    def test_a_trace_nothing_came_out_of_needs_no_replay_for_its_detail(self):
        """Past its part's half hour a violation is written as met, with no
        budget left; reading its detail by one more replay cost a replay
        of the whole walk, about half a second a violation, where the
        trace it was met on shows the detail it was met with."""
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            violation = deep.search_space(ONE).found[0]
            trace = deep.symbolic_trace(ONE, violation.trace)
            with mock.patch.object(shrink, "shown", side_effect=AssertionError("replayed")):
                spent = shrink.shrink(ONE.start, ONE.scope, trace, (violation.guarantee, violation.detail), budget=0.0)
            self.assertEqual((spent.trace, spent.tried, spent.detail), (tuple(trace), 0, violation.detail))
            self.assertEqual(shrink.shown(ONE.start, ONE.scope, trace, (violation.guarantee, violation.detail)),
                             violation.detail)

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

    def test_a_file_names_the_detail_its_own_steps_show(self):
        """Taking a wait out of a trace moves the clock a released
        branch's name is made from, so a shortened trace can show the
        violation under another sha than the one it was met with; each
        file, and what the lane keeps of it, names the detail a replay of
        the file meets, sha and all."""
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            found = deep.search_space(ONE)
            written = deep.write_counterexamples(folder, ONE, found.found)
            self.assertTrue(any("released/" in w["detail"] for w in written), written)
            for w in written:
                with self.subTest(file=Path(w["file"]).name):
                    violated = json.loads(Path(w["file"]).read_text(encoding="ascii"))["violated"]
                    self.assertEqual(violated, f"{w['guarantee']} {w['detail']}")
                    record = replay.replay(replay.load(w["file"]))
                    self.assertIn((w["guarantee"], w["detail"]), [(v.guarantee, v.detail) for _, v in record.violations])

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

    def test_the_files_a_claim_changes_are_compared_in_both_worlds(self):
        """The files a branch changes fill the board's Files column and its
        overlaps, and the memory model computes them where git is not
        asked; a fault there leaves the trees equal, so nothing compared
        saw it. Here the memory model diffs against main's tip in place
        of the merge base, and main moved past the claim."""
        right = M.RepoModel.files_changed

        def against_main_s_tip(model, ref):
            if model.store is not None:
                return right(model, ref)
            head = model.refs.get(ref, "")
            return model.changed(model.refs[M.MAIN], head) if head else []

        path = (Verb("claim", "a", (1,)), Verb("commit", "a", (1,)), Verb("open-draft", "a", (1,)),
                Verb("move-main", M.OWNER), Verb("board", "board", (cycle.SCHEDULE, "")))
        self.assertEqual(deep.compare(deep.SPACES["E-deep"], path), [])
        with mock.patch.object(M.RepoModel, "files_changed", against_main_s_tip):
            differences = deep.compare(deep.SPACES["E-deep"], path)
        fields = {(d["step"], d["field"]) for d in differences}
        self.assertIn((len(path), "files"), fields)
        self.assertIn((len(path), "page"), fields)
        page_at_the_board = next(d for d in differences if d["field"] == "page")
        self.assertIn("README.md, docs/agents/claims/1.md", page_at_the_board["memory"])
        self.assertNotIn("README.md", page_at_the_board["git"])

    def test_a_counterexample_red_in_one_world_alone_is_a_disagreement(self):
        """What observe reads says nothing of the checks, which read what
        it does not; a file red in memory and green over git, or red at
        another step, is a disagreement named by the file, as a sampled
        path's is by the space."""
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
                found = deep.search_space(ONE)
                file = deep.write_counterexamples(folder, ONE, found.found[:1])[0]["file"]
            self.assertEqual(deep.conformance((), [file], 0)["disagreements"], [])
            worlds = iter([[(3, "G3")], []])
            with mock.patch.object(deep.replay, "results", side_effect=lambda record: next(worlds)):
                played = deep.conformance((), [file], 0)
        self.assertEqual(played["counterexamples"], 1)
        self.assertEqual(played["disagreements"],
                         [{"file": file, "step": -1, "field": "red", "memory": repr([(3, "G3")]), "git": repr([])}])

    def test_two_released_branches_of_one_minute_are_ordered_as_they_were_made(self):
        """A trace names a released branch by its place among the issue's,
        oldest first; two made in one minute, which the clock skews of
        E-faults allow, were ordered by name, that is by the sha in it,
        which differs between the worlds, so the file would act on one
        worker's branch in memory and the other's over git. The order the
        branches were made in is the same in both worlds, and total where
        the worker is not (one worker, main moving between its claims)."""
        model = types.SimpleNamespace(refs={"released/2-aaaaaaa": "x", "released/2-bbbbbbb": "y", "main": "z"},
                                      created={"released/2-aaaaaaa": 0, "released/2-bbbbbbb": 0},
                                      made={"released/2-aaaaaaa": 2, "released/2-bbbbbbb": 1})
        self.assertEqual(run.released_order(model, 2), ["released/2-bbbbbbb", "released/2-aaaaaaa"])
        self.assertEqual(run.released_ref(model, 2, 0), "released/2-bbbbbbb")
        self.assertEqual(run.released_ref(model, 2), "released/2-aaaaaaa")
        self.assertEqual(run.symbolic(model, ("released/2-aaaaaaa", 2)), (("released", 2, 1), 2))
        self.assertEqual(run.resolve(model, (("released", 2, 0),)), ("released/2-bbbbbbb",))
        model.created["released/2-aaaaaaa"] = -1
        self.assertEqual(run.released_order(model, 2), ["released/2-aaaaaaa", "released/2-bbbbbbb"])

    def test_a_trace_naming_one_of_two_released_branches_of_one_minute_plays_alike_in_both_worlds(self):
        """Two skewed claims of one issue in one minute, by a and b, and
        by a alone with main moving between them: each is released by the
        next run, the rename-back of the first names it by its place, and
        both worlds act on the same branch."""
        space = deep.Space("tie", Scope(workers=("a", "b"), issues=(2,), skews=(-48,)), (), 6)
        board = Verb("board", "board", (cycle.SCHEDULE, ""))
        back = Verb("rename-back", M.OWNER, (2, ("released", 2, 0)))
        two_workers = (Verb("claim", "a", (2, -2880)), board, Verb("claim", "b", (2, -2880)), board, back)
        one_worker = (Verb("claim", "a", (2, -2880)), board, Verb("move-main", M.OWNER), board,
                      Verb("claim", "a", (2, -2880)), board, back)
        for path in (two_workers, one_worker):
            model = space.start()
            for verb in path:
                verbs.apply(model, run.concrete(model, verb), space.scope)
            self.assertEqual(model.creators["claude/2"], "a")
            self.assertEqual(sorted(model.made[r] for r in model.refs if r.startswith("released/2-")), [2])
            self.assertEqual(deep.compare(space, path), [])


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
            # Each checkpoint, stopped at the first state, is given a
            # thousand seconds, which the search counts on only when it
            # reads the file: so the resumed figures tell a resume from a
            # search started over.
            for checkpoint in (Path(folder) / lanes.CHECKPOINTS).glob("*.pickle"):
                kept = deep.read_checkpoint(checkpoint)
                deep.write_checkpoint(checkpoint, kept["stamp"], kept["seen"], kept["stack"], kept["found"],
                                      kept["transitions"], 1000.0)
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
        self.assertTrue(all(s["seconds"] >= 1000.0 for s in resumed["searches"]), resumed["searches"])
        self.assertEqual(resumed["red"], [])

    def test_a_gate_exploration_the_budget_stopped_is_run_again_on_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("gate",), jobs=2, quick=True, budget=0.0)
            self.assertEqual(lanes.run(settings, out), 1, out.getvalue())
            stopped = results_in(folder)
            self.assertTrue(all(m["stopped"] for m in stopped["gate"]["memory"]))
            self.assertNotIn("gate", lanes.done_parts(stopped))
            again = io.StringIO()
            settings = lanes.Settings(folder, only=("gate",), jobs=2, quick=True, resume=True)
            self.assertEqual(lanes.run(settings, again), 0, again.getvalue())
            resumed = results_in(folder)
        self.assertTrue(all(line.startswith("gate ") and line.endswith("not done") for line in stopped["red"]), stopped["red"])
        self.assertIn("    gate done at", again.getvalue())
        self.assertFalse(any(m["stopped"] for m in resumed["gate"]["memory"]))
        self.assertIn("gate", lanes.done_parts(resumed))
        self.assertEqual(resumed["red"], [])

    def test_a_part_that_raised_is_run_again_on_resume_and_its_error_goes_when_it_lands(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(lanes, "run_soak", broken_soak):
                settings = lanes.Settings(folder, only=("soak",), jobs=2, quick=True)
                self.assertEqual(lanes.run(settings, io.StringIO()), 1)
            self.assertEqual(sorted(e["part"] for e in results_in(folder)["errors"]), ["soak 0", "soak 2"])
            again = io.StringIO()
            settings = lanes.Settings(folder, only=("soak",), jobs=2, quick=True, resume=True)
            self.assertEqual(lanes.run(settings, again), 0, again.getvalue())
            resumed = results_in(folder)
        self.assertEqual(again.getvalue().count(" done at "), 2)
        self.assertEqual(resumed["errors"], [])
        self.assertEqual(resumed["red"], [])
        self.assertEqual(sorted(c["seeds"] for c in resumed["soak"]["chunks"]), [[0, 1], [2, 3]])

    def test_a_part_whose_worker_process_dies_is_red_alone_and_the_run_finishes(self):
        """One shared pool would mark every part in flight as raised when
        one worker died and refuse the second wave; a process per part
        loses the part alone, and conformance still runs."""
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(lanes, "run_soak", dying_soak):
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search", "soak", "conformance"), jobs=2, quick=True)
            self.assertEqual(lanes.run(settings, out), 1, out.getvalue())
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())
            results = results_in(folder)
        self.assertEqual(sorted(s["space"] for s in results["searches"]), ["E-deep", "E-faults", "E-two"])
        self.assertEqual(sorted(c["space"] for c in results["conformance"]), ["E-deep", "E-faults", "E-two"])
        self.assertEqual(sorted(e["part"] for e in results["errors"]), ["soak 0", "soak 2"])
        self.assertTrue(all(e["error"].startswith("its worker process died") for e in results["errors"]), results["errors"])
        self.assertEqual(len(results["red"]), 2)
        self.assertIn("soak 0: its worker process died at", out.getvalue())
        self.assertIn("deep lane: found something", out.getvalue())

    def test_the_counterexamples_a_resumed_search_writes_go_through_conformance(self):
        """The first run's searches stop at once and write nothing, and
        its conformance plays the sampled paths; the resumed run's
        searches, under the mutant that releases a claim at exactly 24
        hours, write counterexamples, which the run then plays in both
        worlds as a part of its own, owed again whenever a file is
        added. The quick depths are lowered further, since the files the
        mutant makes at them, 36, cost twenty seconds of replay over git
        where a few show the same."""
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(lanes.QUICK["depths"], {"E-two": 1, "E-deep": 1, "E-faults": 2}):
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search", "conformance"), jobs=2, quick=True, budget=0.0)
            self.assertEqual(lanes.run(settings, out), 1, out.getvalue())
            stopped = results_in(folder)
            self.assertEqual(sorted(c["space"] for c in stopped["conformance"]), ["E-deep", "E-faults", "E-two"])
            self.assertEqual(lanes.counterexample_files(stopped), [])
            with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
                again = io.StringIO()
                settings = lanes.Settings(folder, only=("search", "conformance"), jobs=2, quick=True, resume=True)
                self.assertEqual(lanes.run(settings, again), 1, again.getvalue())
            resumed = results_in(folder)
        files = lanes.counterexample_files(resumed)
        self.assertTrue(files)
        self.assertEqual(sorted(c["space"] for c in resumed["conformance"]), ["E-deep", "E-faults", "E-two", "counterexamples"])
        played = next(c for c in resumed["conformance"] if c["space"] == lanes.COUNTEREXAMPLES)
        self.assertEqual(played["files"], files)
        self.assertEqual((played["counterexamples"], played["paths"], played["disagreements"]), (len(files), 0, []))
        self.assertIn("    conformance counterexamples done at", again.getvalue())
        self.assertFalse([line for line in resumed["red"] if line.startswith("conformance,")], resumed["red"])
        self.assertIn("conformance counterexamples", lanes.done_parts(resumed))
        resumed["searches"][0]["violations"].append({"file": "one more"})
        self.assertNotIn("conformance counterexamples", lanes.done_parts(resumed))

    def test_a_run_is_refused_while_another_is_in_progress_in_the_folder(self):
        """A fresh run removes the checkpoints and the trace files first,
        which would take them from a run still writing them; the running
        file names that run's process while it lives."""
        with tempfile.TemporaryDirectory() as folder:
            stale = Path(folder) / lanes.CHECKPOINTS / "E-two.pickle"
            stale.parent.mkdir()
            stale.write_bytes(b"a run's")
            other = a_live_process(self)
            (Path(folder) / lanes.RUNNING).write_text(f"{other}\n", encoding="ascii")
            for resume in (False, True):
                with self.assertRaises(SystemExit) as refused:
                    lanes.run(lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=resume), io.StringIO())
                self.assertIn(f"names process {other}, a run in progress", str(refused.exception))
            self.assertTrue(stale.exists())
            ended = subprocess.Popen([sys.executable, "-c", "pass"])
            ended.wait()
            gone = ended.pid
            (Path(folder) / lanes.RUNNING).write_text(f"{gone}\n", encoding="ascii")
            out = io.StringIO()
            self.assertEqual(lanes.run(lanes.Settings(folder, only=("search",), jobs=2, quick=True), out), 0, out.getvalue())
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())
            self.assertFalse(stale.exists())
            (Path(folder) / lanes.RUNNING).write_text("not a process\n", encoding="ascii")
            self.assertEqual(lanes.run(lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True), io.StringIO()), 0)

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

    def test_a_count_of_processes_below_one_is_refused(self):
        """run_jobs would submit nothing and spin for good under a count
        below one: no part fits, and waiting on no future returns at
        once. Zero and None are the processor count, as before."""
        with tempfile.TemporaryDirectory() as folder:
            for jobs in (-1, -4):
                with self.assertRaises(ValueError) as refused:
                    lanes.Settings(folder, jobs=jobs)
                self.assertIn("--jobs takes a count of processes of 1 or more", str(refused.exception))
            self.assertEqual(lanes.Settings(folder, jobs=0).jobs, os.cpu_count() or 2)
            self.assertEqual(lanes.Settings(folder, jobs=3).jobs, 3)

    def test_the_marker_is_taken_first_and_exclusively(self):
        """Two runs started at the same moment both passed a liveness check
        made before the marker was written, and both removed the
        checkpoints and wrote one results file; the marker is made first
        now, exclusively, so the second finds the first's and is refused,
        and a refusal after it is taken removes it."""
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / lanes.RUNNING
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            seen = []

            def removing(path, ignore_errors=False):
                seen.append(marker.read_text(encoding="ascii").strip())

            with mock.patch.object(lanes.shutil, "rmtree", removing):
                self.assertEqual(lanes.run(settings, io.StringIO()), 0)
            self.assertEqual(seen, [str(os.getpid())] * 2)
            self.assertFalse(marker.exists())
            other = {**lanes.fresh_results(settings), "run": {"quick": False}}
            (Path(folder) / "deep.json").write_text(json.dumps(other), encoding="ascii")
            with self.assertRaises(SystemExit) as refused:
                lanes.run(lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True), io.StringIO())
            self.assertIn("other settings or other code", str(refused.exception))
            self.assertFalse(marker.exists())
            other = a_live_process(self)
            marker.write_text(f"{other}\n", encoding="ascii")
            with self.assertRaises(SystemExit) as refused:
                lanes.take_marker(settings)
            self.assertIn(f"names process {other}, a run in progress", str(refused.exception))
            self.assertEqual(marker.read_text(encoding="ascii"), f"{other}\n")
            self.assertEqual([p.name for p in Path(folder).iterdir() if p.name.startswith(lanes.RUNNING)], [lanes.RUNNING])

    def test_the_marker_appears_with_its_process_s_id_in_it(self):
        """A marker made empty and written after read as no run to a
        second run started in between, which replaced it and ran beside
        the first; it is written under a name of the process's own and
        linked into place, so it is never seen empty."""
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            marker = Path(folder) / lanes.RUNNING
            linked = []
            real_link = os.link

            def link(source, target):
                linked.append((Path(source).read_text(encoding="ascii"), Path(target), marker.exists()))
                real_link(source, target)

            with mock.patch.object(lanes.os, "link", link):
                lanes.take_marker(settings)
            self.assertEqual(linked, [(f"{os.getpid()}\n", marker, False)])
            self.assertEqual([p.name for p in Path(folder).iterdir()], [lanes.RUNNING])
            self.assertEqual(marker.read_text(encoding="ascii"), f"{os.getpid()}\n")

    def test_a_marker_naming_this_very_process_is_a_dead_run_s(self):
        """A restart hands process numbers out again from the same low
        range, so a resume can draw the dead run's, and a container whose
        entry point is the lane is process 1 every time; a live run never
        has this process's id, so the resume goes on, where it refused
        itself."""
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True)
            (Path(folder) / lanes.RUNNING).write_text(f"{os.getpid()}\n", encoding="ascii")
            self.assertIsNone(lanes.running_process(settings))
            self.assertEqual(lanes.run(settings, io.StringIO()), 0)
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())

    def test_the_results_file_is_replaced_whole_and_one_that_cannot_be_read_is_not_resumed(self):
        """deep.json was rewritten in place, so a write that failed or was
        cut, on a full disk or by a kill, left a file a resume died on
        with a traceback and the marker left, and the one way on removed
        every checkpoint. It is written beside and moved into place, and
        a file that cannot be read is refused by name."""
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            results = lanes.fresh_results(settings)
            lanes.write_results(settings, results, time.perf_counter())
            file = Path(folder) / "deep.json"
            before = file.read_text(encoding="ascii")
            real_write = Path.write_text

            def cut(path, text, encoding=None):
                real_write(path, text[:len(text) // 2], encoding=encoding)
                raise OSError(errno.ENOSPC, "No space left on device")

            with mock.patch.object(Path, "write_text", cut), self.assertRaises(OSError):
                lanes.write_results(settings, results, time.perf_counter() - 100.0)
            self.assertEqual(file.read_text(encoding="ascii"), before)
            file.write_text(before[:len(before) // 2], encoding="ascii")
            with self.assertRaises(SystemExit) as refused:
                lanes.run(lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True), io.StringIO())
            self.assertIn("cannot be read", str(refused.exception))
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())

    def test_a_resume_under_another_git_is_refused(self):
        """The guard part, the git scenarios and traces, the gate over git
        and conformance are git's answers, so a run resumed under another
        git kept the parts measured under the old one and could end green
        on them."""
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True)
            self.assertTrue(settings.record()["git"].startswith("git version "), settings.record()["git"])
            (Path(folder) / "deep.json").write_text(json.dumps(lanes.fresh_results(settings)), encoding="ascii")
            with mock.patch.object(lanes, "git_version", lambda: "git version 9.9.9"), \
                    self.assertRaises(SystemExit) as refused:
                lanes.run(settings, io.StringIO())
            self.assertIn("(git differ)", str(refused.exception))
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())

    def test_a_run_that_did_not_finish_says_so_in_its_file(self):
        """A file left by a run killed, or by a job cancelled, before a
        red part landed read green, with nothing it still owed named
        anywhere; until every part the run asked for has landed or shows
        red, the file says the run did not finish, and that is red. A
        resume narrower than the run, `--only search` after a run cut
        short before conformance, names the parts still owed, where it
        read green and said ok."""
        def interrupted(settings, results):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(lanes, "second_wave", interrupted), self.assertRaises(KeyboardInterrupt):
                lanes.run(lanes.Settings(folder, only=("search", "conformance"), jobs=2, quick=True), io.StringIO())
            stopped = results_in(folder)
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())
            narrower = io.StringIO()
            self.assertEqual(lanes.run(lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True), narrower),
                             1, narrower.getvalue())
            owing = results_in(folder)
            out = io.StringIO()
            self.assertEqual(lanes.run(lanes.Settings(folder, jobs=2, quick=True, resume=True, only=("conformance",)), out),
                             0, out.getvalue())
            done = results_in(folder)
        self.assertEqual(len(stopped["searches"]), 3)
        self.assertIs(stopped["finished"], False)
        self.assertEqual(stopped["red"], ["the run did not finish: the parts it had not landed are not in this file"])
        self.assertIs(owing["finished"], False)
        self.assertEqual(owing["red"], ["the run did not finish: still owed: "
                                        + ", ".join(f"conformance {name}" for name in deep.SPACES)])
        self.assertIn("deep lane: found something", narrower.getvalue())
        self.assertIs(done["finished"], True)
        self.assertEqual(done["red"], [])
        self.assertEqual(done["asked"], ["conformance", "search"])

    def test_the_parts_are_ended_and_waited_for(self):
        """A run that ends by its own SIGTERM runs no exit handler, which
        waited for the parts before, so an ended part stayed unreaped
        until something else reaped it, and where nothing does, as in a
        container whose first process is no init, it stays; end_workers
        waits for each part it ends."""
        context = lanes.fork_context()
        ready = [context.Event() for _ in range(2)]
        parts = [context.Process(target=slow_to_end, args=(one,)) for one in ready]
        for part in parts:
            part.start()
            self.addCleanup(part.join)
            self.addCleanup(part.kill)
        self.assertTrue(all(one.wait(30) for one in ready))
        lanes.end_workers()
        self.assertEqual([part.exitcode for part in parts], [3, 3])

    def test_a_part_ended_outside_its_work_ends_quietly(self):
        """A part's process inherits the runner's SIGTERM handler by the
        fork; outside run_part's try, as while it sends its result, a
        Terminated reached multiprocessing's own except, which printed it
        and ended the part with 1. In a part the handler raises
        SystemExit, which ends it with 143 and prints nothing."""
        context = lanes.fork_context()
        ready = context.Event()
        previous = signal.signal(signal.SIGTERM, lanes.stop_on_term)
        try:
            part = context.Process(target=waits_outside_its_work, args=(ready,))
            part.start()
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.addCleanup(part.join)
        self.addCleanup(part.kill)
        self.assertTrue(ready.wait(30))
        part.terminate()
        part.join(30)
        self.assertEqual(part.exitcode, 128 + signal.SIGTERM)

    def test_a_folder_with_no_hard_links_still_takes_the_marker(self):
        """os.link fails with EPERM on vfat, exFAT and SMB without unix
        extensions, where the run could not start and left running.<pid>
        behind; there the marker is made exclusively and written."""
        def no_links(source, target):
            raise PermissionError(errno.EPERM, "Operation not permitted")

        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            marker = Path(folder) / lanes.RUNNING
            with mock.patch.object(lanes.os, "link", no_links):
                lanes.take_marker(settings)
                self.assertEqual([p.name for p in Path(folder).iterdir()], [lanes.RUNNING])
                self.assertEqual(marker.read_text(encoding="ascii"), f"{os.getpid()}\n")
                other = a_live_process(self)
                marker.write_text(f"{other}\n", encoding="ascii")
                with self.assertRaises(SystemExit) as refused:
                    lanes.take_marker(settings)
            self.assertIn(f"names process {other}, a run in progress", str(refused.exception))
            self.assertEqual([p.name for p in Path(folder).iterdir()], [lanes.RUNNING])

    def test_a_result_larger_than_the_pipe_holds_lands(self):
        """A part's result is sent only as the parent reads it once it is
        more than the pipe holds, so a parent that waited on the
        process's end alone would wait for good; it waits on the pipe as
        well. An alarm ends the test, not the suite, if it does wait."""
        def waited_for_good(signum, frame):
            raise AssertionError("the parent waited a minute for a part that had ended its work")

        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            results = lanes.fresh_results(settings)
            taken = []
            previous = signal.signal(signal.SIGALRM, waited_for_good)
            self.addCleanup(signal.signal, signal.SIGALRM, previous)
            self.addCleanup(lanes.end_workers)
            signal.alarm(60)
            try:
                with mock.patch.object(lanes, "take", lambda results, part, result, out: taken.append((part, len(result)))):
                    lanes.run_jobs(settings, [("a large part", one_megabyte, ())], results, time.perf_counter(), io.StringIO())
            finally:
                signal.alarm(0)
        self.assertEqual(taken, [("a large part", 1_000_000)])
        self.assertEqual(results["errors"], [])

    def test_a_resumed_search_s_files_replace_the_ones_its_stopped_run_wrote(self):
        """A search the budget stopped wrote the files of what it had met,
        and the resumed search numbers its own anew; a file of the
        stopped one that the new result does not name is no result's,
        and goes. Files are told apart by name, since a resume may spell
        the folder another way: compared whole, the first file, named
        again under that spelling, went too."""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / deep.TRACES).mkdir()
            files = [Path(folder) / deep.TRACES / f"G1-E-two-{n}.json" for n in (1, 2, 3)]
            for file in files:
                file.write_text("{}", encoding="ascii")
            results = lanes.fresh_results()
            results["searches"].append({"space": "E-two", "violations": [{"file": str(f)} for f in files[:2]]})
            results["searches"].append({"space": "E-deep", "violations": []})
            spelt_otherwise = f"{folder}/./{deep.TRACES}/{files[0].name}"
            lanes.take(results, "search E-two", {"space": "E-two", "violations": [{"file": spelt_otherwise},
                                                                                  {"file": str(files[2])}]}, folder)
            self.assertEqual([f.exists() for f in files], [True, False, True])
        self.assertEqual(sorted(s["space"] for s in results["searches"]), ["E-deep", "E-two"])

    def test_an_out_folder_among_the_code_the_run_is_stamped_by_is_refused(self):
        """The code digest reads every .py and .json file under validation
        and sdlc_protocol but validation/runs/, so a run into a folder
        there counted its own deep.json and trace files as code: its
        resume was refused as made under other code."""
        for inside in ("validation/out", "sdlc_protocol/out", "validation"):
            with self.assertRaises(ValueError) as refused:
                lanes.Settings(deep.ROOT / inside)
            self.assertIn("--out takes a folder outside validation and sdlc_protocol", str(refused.exception))
        for outside in ("validation-out", "validation/runs/2026-10-14"):
            self.assertEqual(lanes.Settings(deep.ROOT / outside).out, deep.ROOT / outside)

    def test_the_parts_are_forked_and_the_parent_forks_with_no_helper_thread(self):
        """A mutant a test patches into the harness reaches a part by fork
        alone, whatever Python's default start method; and forking a
        multi-threaded process, which a pool per part made of every fork
        after the first, is what Python 3.12 warns may deadlock the
        child."""
        self.assertEqual(lanes.fork_context().get_start_method(), "fork")
        with tempfile.TemporaryDirectory() as folder, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            settings = lanes.Settings(folder, only=("search", "soak"), jobs=2, quick=True)
            self.assertEqual(lanes.run(settings, io.StringIO()), 0)
        self.assertEqual([str(w.message) for w in caught if "fork" in str(w.message)], [])

    def test_on_windows_the_marker_is_taken_at_its_word(self):
        """os.kill(pid, 0) there is TerminateProcess: the probe would end
        the run it asks after, or whatever process holds a stale marker's
        id, so no probe is made."""
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            (Path(folder) / lanes.RUNNING).write_text("4242\n", encoding="ascii")

            def never(pid, signum):
                raise AssertionError("os.kill was called")

            with mock.patch.object(lanes.os, "name", "nt"), mock.patch.object(lanes.os, "kill", never):
                self.assertEqual(lanes.running_process(settings), 4242)
            with mock.patch.object(lanes.os, "kill", never):
                self.assertRaises(AssertionError, lanes.running_process, settings)

    def test_two_violations_of_one_walk_at_one_step_get_a_file_each(self):
        """A board run that releases a claim not due shows G3 twice at one
        step, the comment and the rename; one name per step would keep
        the second's file alone and name it twice."""
        walk = soak.walk(0, 6)
        two = walk._replace(violations=(("G3", "made and not owed: ('comment', 1, 'claim-released')", 5),
                                        ("G3", "made and not owed: ('rename', 'claude/1', 'released/1-abcdef0')", 5)))

        def unshortened(start, scope, trace, target, known_reading, budget):
            return shrink.Shrunk(tuple(trace), 0, 0.0)

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(lanes.soak, "walk", lambda seed, steps: two), \
                mock.patch.object(lanes.shrink, "shrink", unshortened):
            result = lanes.run_soak([0], 6, folder)
            files = sorted(p.name for p in (Path(folder) / deep.TRACES).glob("*.json"))
        self.assertEqual(files, ["G3-soak-0-5-1.json", "G3-soak-0-5-2.json"])
        self.assertEqual([Path(v["file"]).name for v in result["violations"]], files)
        self.assertEqual([v["detail"] for v in result["violations"]], [d for _, d, _ in two.violations])
        self.assertEqual(result["violations"][0]["shortened_to"], 6)

    def test_a_sigterm_to_the_runner_ends_its_workers_and_removes_the_marker(self):
        """`kill <pid>` sends SIGTERM to the runner alone; with no handler
        the parent died at once, its finally never run, the marker left
        naming a dead process and each part's process, which watches no
        parent, running its search on for hours, orphaned. The handler
        ends the workers and removes the marker, then ends the process by
        the signal, where a SystemExit let a caller that catches one, as
        a test runner does, go on after it. The runner is a session of
        its own, so the test ends it and its workers whatever it sees."""
        if shutil.which("pgrep") is None:
            self.skipTest("pgrep is not installed")
        with tempfile.TemporaryDirectory() as folder:
            out, went_on = Path(folder) / "out", Path(folder) / "went-on"
            script = ("import os, sys\n"
                      f"sys.path.insert(0, {str(ROOT)!r})\n"
                      "from validation import lanes\n"
                      "try:\n"
                      f"    lanes.run(lanes.Settings({str(out)!r}, only=('search',), jobs=1), open(os.devnull, 'w'))\n"
                      "except SystemExit:\n"
                      f"    open({str(went_on)!r}, 'w').close()\n")
            runner = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=True)
            self.addCleanup(end_session, runner)
            marker = out / lanes.RUNNING
            # deep.json is written after the record, whose git --version
            # is a child too, so the children then are the parts.
            workers = wait_for(lambda: children_of(runner.pid) if (out / "deep.json").exists() else [], 60)
            self.assertTrue(workers, "no worker started within a minute")
            runner.send_signal(signal.SIGTERM)
            self.assertEqual(runner.wait(timeout=60), -signal.SIGTERM)
            self.assertFalse(went_on.exists())
            self.assertFalse(marker.exists())
            self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in workers), 30), "a worker outlived the runner")


if __name__ == "__main__":
    unittest.main()
