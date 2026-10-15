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

import _thread
import errno
import io
import itertools
import json
import multiprocessing
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
from validation import fake_github, shrink, soak, verbs  # noqa: E402
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


def sleeps():
    """A part that runs until it is ended."""
    time.sleep(600)


def begins_and_sleeps(began):
    """A part that says its work began, then runs until it is ended."""
    Path(began).write_text("began\n", encoding="ascii")
    time.sleep(600)


def ignores_sigterm(ready):
    """A part a SIGTERM does not end, as one is whose signal a finalizer
    swallowed."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready.set()
    time.sleep(600)


def says_it_is_ready(folder):
    Path(folder, "ready").write_text("ready\n", encoding="ascii")
    time.sleep(600)


def runs_the_lane_and_goes_on(folder):
    """A runner that is itself a process multiprocessing started, as under
    a test runner that forks, with one part that runs until it is ended;
    it says so when the run lets it go on after a SIGTERM. It runs in a
    session of its own, so the test ends it with its part whatever the
    test sees."""
    os.setsid()
    lanes.first_wave = lambda settings, results: [("a part", says_it_is_ready, (folder,))]
    lanes.second_wave = lambda settings, results: []
    try:
        lanes.run(lanes.Settings(Path(folder) / "out", only=("search",), jobs=1), io.StringIO())
    except SystemExit as stopped:
        Path(folder, "went-on").write_text(f"{stopped!r}\n", encoding="ascii")


# A runner with one part that takes three seconds to end once told to, and
# says when it is told, in a process of its own. Given "its own SIGINT
# handler", it runs under a caller whose SIGINT handler is its own and ends
# the program, as many command-line programs set one; either way the caller
# goes on after a SystemExit, as a test runner does, and says so.
SLOW_TO_END = """\
import os, signal, sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from validation import lanes
out = Path({out!r})


def ends_in_three_seconds(signum, frame):
    (out / "ending").write_text("ending\\n", encoding="ascii")
    time.sleep(3)
    os._exit(3)


def slow_to_end():
    signal.signal(signal.SIGTERM, ends_in_three_seconds)
    (out / "ready").write_text("ready\\n", encoding="ascii")
    time.sleep(600)


lanes.first_wave = lambda settings, results: [("a slow part", slow_to_end, ())]
lanes.second_wave = lambda settings, results: []
if sys.argv[1:] == ["its own SIGINT handler"]:
    signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(130))
try:
    lanes.run(lanes.Settings(out, only=("search",), jobs=1), open(os.devnull, "w"))
except SystemExit as ended:
    (out.parent / "went-on").write_text(f"{{ended!r}}\\n", encoding="ascii")
"""


# A runner that leaves while its part runs, past run's cleanup, as an
# exception it does not catch leaves; the part says it is ready with its id.
LEAVES_ITS_PART = """\
import io, os, signal, sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from validation import lanes
out = Path({out!r})


def says_it_is_ready():
    (out / "ready.tmp").write_text(f"{{os.getpid()}}\\n", encoding="ascii")
    (out / "ready.tmp").replace(out / "ready")
    time.sleep(600)


def leaves(signum, frame):
    raise RuntimeError("the runner left past its cleanup")


signal.signal(signal.SIGUSR1, leaves)
out.mkdir()
lanes.run_jobs(lanes.Settings(out, only=("search",), jobs=1), [("a part", says_it_is_ready, ())], lanes.fresh_results(),
               time.perf_counter(), io.StringIO())
"""


# A runner with nothing to run, to which a SIGTERM comes as its cleanup
# puts the default handler back, before the marker is removed.
SIGNALLED_AS_ITS_HANDLER_IS_PUT_BACK = """\
import io, os, signal, sys
from pathlib import Path
sys.path.insert(0, {root!r})
from validation import lanes
setting = signal.signal


def signalled_as_it_is_put_back(signum, handler):
    previous = setting(signum, handler)
    if signum == signal.SIGTERM and handler == signal.SIG_DFL:
        os.kill(os.getpid(), signal.SIGTERM)
    return previous


signal.signal = signalled_as_it_is_put_back
lanes.first_wave = lambda settings, results: []
lanes.second_wave = lambda settings, results: []
lanes.run(lanes.Settings(Path({out!r}), only=("search",), jobs=1), io.StringIO())
"""


def a_live_process(test):
    """A process that lives until the test ends, standing for a run in
    progress: a marker naming the test's own process reads as a dead
    run's."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    test.addCleanup(process.wait)
    test.addCleanup(process.kill)
    return process.pid


def kill_group(pid):
    """Every process left in the group the process pid leads."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def end_session(process):
    """A process started in a session of its own, ended with every process
    in its group, its workers among them, whatever the test saw."""
    kill_group(process.pid)
    process.wait()


def end_forked(pids):
    """The processes a test forked, killed and reaped whatever the test
    saw, through multiprocessing where it lists them."""
    listed = {child.pid: child for child in multiprocessing.active_children()}
    for pid in pids:
        if pid in listed:
            listed[pid].kill()
            listed[pid].join()
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


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
        next run, which GitHub refuses to say when the claim's push came,
        so the run reads the committer date dated two days back (F8), the
        rename-back of the first names it by its place, and both worlds act
        on the same branch."""
        space = deep.Space("tie", Scope(workers=("a", "b"), issues=(2,), skews=(-48,), faults=("pushes-500",)), (), 6)
        board = Verb("board", "board", (cycle.SCHEDULE, "", ("pushes-500",)))
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

    def test_the_activity_faults_are_met_where_they_can_fire(self):
        """The board reads when the branches were made only for the work in
        the Overlaps (F13), which E-faults' one issue never has, so
        E-faults took the two faults on that read and never met them;
        E-activity, from two claims on one file, meets them, and the fault
        on the read of a claim's head's push (F8), which every board run
        with a claim makes."""
        met = {name: 0 for name in deep.ACTIVITY_FAULTS}
        fault_for = fake_github.FakeGitHub.fault_for

        def counting(fake, call, query=None):
            fault = fault_for(fake, call, query)
            for name in met:
                met[name] += fault == verbs.FAULTS[name]
            return fault

        with mock.patch.object(fake_github.FakeGitHub, "fault_for", counting):
            found = deep.search_space(deep.SPACES["E-activity"], depth=2)
        self.assertEqual(sorted(met), ["activity-500", "activity-500-all", "pushes-500"])
        self.assertTrue(all(met.values()), met)
        self.assertEqual([(f.guarantee, f.detail) for f in found.found], [])

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
        self.assertEqual(sorted(s["space"] for s in data["searches"]), ["E-activity", "E-deep", "E-faults", "E-two"])
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
            self.assertEqual(out.getvalue().count(" done at "), len(deep.SPACES))
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
        self.assertEqual(lanes.done_parts(second), {"search E-two", "search E-deep", "search E-faults", "search E-activity", "soak 0",
                                                        "soak 2"})

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
        self.assertEqual(len(stopped["red"]), len(deep.SPACES))
        self.assertTrue(all(line.endswith("not done") for line in stopped["red"]), stopped["red"])
        self.assertEqual(len(resumed["searches"]), len(deep.SPACES))
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
        self.assertEqual(sorted(s["space"] for s in results["searches"]), ["E-activity", "E-deep", "E-faults", "E-two"])
        self.assertEqual(sorted(c["space"] for c in results["conformance"]), ["E-activity", "E-deep", "E-faults", "E-two"])
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
                mock.patch.dict(lanes.QUICK["depths"], {"E-two": 1, "E-deep": 1, "E-faults": 2, "E-activity": 1}):
            out = io.StringIO()
            settings = lanes.Settings(folder, only=("search", "conformance"), jobs=2, quick=True, budget=0.0)
            self.assertEqual(lanes.run(settings, out), 1, out.getvalue())
            stopped = results_in(folder)
            self.assertEqual(sorted(c["space"] for c in stopped["conformance"]), ["E-activity", "E-deep", "E-faults", "E-two"])
            self.assertEqual(lanes.counterexample_files(stopped), [])
            with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
                again = io.StringIO()
                settings = lanes.Settings(folder, only=("search", "conformance"), jobs=2, quick=True, resume=True)
                self.assertEqual(lanes.run(settings, again), 1, again.getvalue())
            resumed = results_in(folder)
        files = lanes.counterexample_files(resumed)
        self.assertTrue(files)
        self.assertEqual(sorted(c["space"] for c in resumed["conformance"]),
                         ["E-activity", "E-deep", "E-faults", "E-two", "counterexamples"])
        played = next(c for c in resumed["conformance"] if c["space"] == lanes.COUNTEREXAMPLES)
        self.assertEqual(played["files"], files)
        self.assertEqual((played["counterexamples"], played["paths"], played["disagreements"]), (len(files), 0, []))
        self.assertIn("    conformance counterexamples done at", again.getvalue())
        self.assertFalse([line for line in resumed["red"] if line.startswith("conformance,")], resumed["red"])
        self.assertIn("conformance counterexamples", lanes.done_parts(resumed))
        resumed["searches"][0]["violations"].append({"file": "one more"})
        self.assertNotIn("conformance counterexamples", lanes.done_parts(resumed))

    def test_the_counterexamples_are_read_from_this_run_s_folder_however_a_run_spelt_it(self):
        """A result records each trace file as the invocation that wrote it
        spelt the folder, relative to its own working directory, so a
        resume from another directory, or of a folder since moved, found
        none of the files, and the counterexamples' conformance raised on
        every resume. The files are read by name from this run's traces
        folder, and a conformance that played them under another spelling
        is done."""
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
                found = deep.search_space(ONE)
                name = Path(deep.write_counterexamples(folder, ONE, found.found[:1], budget=0.0)[0]["file"]).name
            settings = lanes.Settings(folder, only=("conformance",), jobs=2, quick=True)
            results = lanes.fresh_results(settings)
            elsewhere = f"another/working/directory/{deep.TRACES}/{name}"
            results["searches"].append({"space": "E-two", "stopped": False, "violations": [{"file": elsewhere}]})
            part, function, args = lanes.second_wave(settings, results)[-1]
            self.assertEqual((part, args), (f"conformance {lanes.COUNTEREXAMPLES}",
                                            ([str(Path(folder) / deep.TRACES / name)],)))
            played = function(*args)
            self.assertEqual((played["counterexamples"], played["disagreements"]), (1, []))
            results["conformance"].append({**played, "files": [f"{folder}/./{deep.TRACES}/{name}"]})
            self.assertIn(f"conformance {lanes.COUNTEREXAMPLES}", lanes.done_parts(results))

    def test_a_resumed_file_names_each_trace_file_as_this_run_spells_its_folder(self):
        """After a resume from another directory the red lines named each
        trace file as the invocation that wrote it spelt the folder, a
        path replay could not open from here, and one results file held
        two spellings of one folder; a resumed file's trace files are
        named as this run spells it."""
        elsewhere = f"another/working/directory/{deep.TRACES}"
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True, resume=True)
            results = lanes.fresh_results(settings)
            results["searches"].append({"space": "E-two", "depth": 3, "states": 1, "transitions": 0, "seconds": 0.0,
                                        "stopped": False, "differences": [],
                                        "violations": [{"guarantee": "G1", "detail": "d", "count": 1, "finding": None,
                                                        "file": f"{elsewhere}/G1-E-two-1.json"}]})
            results["soak"]["chunks"].append({"seeds": [0, 1], "walks": 2, "steps": 4, "seconds": 0.0,
                                              "violations": [{"seed": 0, "step": 1, "guarantee": "G3", "detail": "d",
                                                              "file": f"{elsewhere}/G3-soak-0-1-1.json"}]})
            results["conformance"].append({"space": lanes.COUNTEREXAMPLES, "paths": 0, "counterexamples": 2,
                                           "seconds": 0.0, "files": lanes.counterexample_files(results),
                                           "disagreements": [{"file": f"{elsewhere}/G1-E-two-1.json", "step": -1,
                                                              "field": "red", "memory": "[]", "git": "[]"}]})
            (Path(folder) / "deep.json").write_text(json.dumps(results), encoding="ascii")
            kept, _ = lanes.kept_results(settings)
        here = Path(folder) / deep.TRACES
        files = [str(here / "G1-E-two-1.json"), str(here / "G3-soak-0-1-1.json")]
        self.assertEqual(lanes.counterexample_files(kept), files)
        self.assertEqual(kept["conformance"][0]["files"], files)
        self.assertEqual(kept["conformance"][0]["disagreements"][0]["file"], files[0])
        self.assertIn(f"search E-two: G1 d (1 times), {files[0]}", lanes.red_lines(kept))
        self.assertIn(f"soak seed 0 step 1: G3 d, {files[1]}", lanes.red_lines(kept))

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
        self.assertEqual(sorted(s["space"] for s in results["searches"]), ["E-activity", "E-deep", "E-faults", "E-two"])
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
        self.assertEqual(len(stopped["searches"]), len(deep.SPACES))
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
        previous = signal.signal(signal.SIGTERM, lanes.stop_on_term(os.getpid()))
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

    def test_a_sigterm_that_lands_as_a_part_is_forked_finds_the_part_listed(self):
        """A SIGTERM that reached the runner as os.fork returned raised
        before multiprocessing listed the part, so end_workers never found
        it, and it ran its search on, orphaned. Held while the part is
        forked and listed, it raises once the part is listed, and the
        part is ended."""
        forked, fork = [], os.fork

        def signalled_as_it_forks():
            pid = fork()
            if pid:
                forked.append(pid)
                os.kill(os.getpid(), signal.SIGTERM)
            return pid

        self.addCleanup(end_forked, forked)
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=1, quick=True)
            previous = signal.signal(signal.SIGTERM, lanes.stop_on_term(os.getpid()))
            try:
                with mock.patch.object(os, "fork", signalled_as_it_forks), self.assertRaises(lanes.Terminated):
                    lanes.run_jobs(settings, [("a part", sleeps, ())], lanes.fresh_results(settings), time.perf_counter(),
                                   io.StringIO())
            finally:
                signal.signal(signal.SIGTERM, previous)
        listed = [child for child in multiprocessing.active_children() if child.pid in forked]
        lanes.end_workers()
        self.assertEqual(len(forked), 1)
        self.assertEqual([child.exitcode for child in listed], [128 + signal.SIGTERM])

    def test_a_sigterm_that_reaches_a_part_as_it_is_forked_ends_it_before_its_work(self):
        """A SIGTERM that reached a part during the fork's own steps was
        lost there, or what its handler raised was swallowed, and the
        part ran its whole work. Held until run_part lets it in, it ends
        the part before its work begins. An alarm ends the test, not the
        suite, if the part runs on."""
        def ran_on(signum, frame):
            raise AssertionError("the part ran on after its SIGTERM")

        forked, fork = [], os.fork

        def its_part_signalled_as_it_forks():
            pid = fork()
            if pid:
                forked.append(pid)
                os.kill(pid, signal.SIGTERM)
            return pid

        self.addCleanup(end_forked, forked)
        with tempfile.TemporaryDirectory() as folder:
            began = Path(folder) / "began"
            settings = lanes.Settings(folder, only=("search",), jobs=1, quick=True)
            results = lanes.fresh_results(settings)
            previous = signal.signal(signal.SIGTERM, lanes.stop_on_term(os.getpid()))
            self.addCleanup(signal.signal, signal.SIGTERM, previous)
            self.addCleanup(signal.signal, signal.SIGALRM, signal.signal(signal.SIGALRM, ran_on))
            signal.alarm(60)
            try:
                with mock.patch.object(os, "fork", its_part_signalled_as_it_forks):
                    lanes.run_jobs(settings, [("a part", begins_and_sleeps, (str(began),))], results, time.perf_counter(),
                                   io.StringIO())
            finally:
                signal.alarm(0)
            self.assertFalse(began.exists())
        self.assertEqual([e["error"] for e in results["errors"]],
                         [f"its worker process died: exit code {128 + signal.SIGTERM}"])

    def test_a_part_alive_after_the_grace_is_killed(self):
        """A part whose SIGTERM a finalizer swallowed outlived end_workers'
        wait and, once the runner had ended, ran its search on, orphaned;
        one still alive after the grace is killed."""
        context = lanes.fork_context()
        ready = context.Event()
        part = context.Process(target=ignores_sigterm, args=(ready,))
        part.start()
        self.addCleanup(part.join)
        self.addCleanup(part.kill)
        self.assertTrue(ready.wait(30))
        lanes.end_workers(grace=0.5)
        self.assertEqual(part.exitcode, -signal.SIGKILL)

    def test_a_second_signal_while_the_run_ends_waits_until_it_has_ended(self):
        """A cancelled job sends SIGINT and, seconds later, SIGTERM; the
        second came while end_workers waited for a part slow to end and
        raised there, past the rest of the cleanup, so the marker stayed,
        naming a dead process, and the process ended on a traceback. Held
        until the parts are ended, the handler put back and the marker
        removed, it then ends the process."""
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            runner = subprocess.Popen([sys.executable, "-c", SLOW_TO_END.format(root=str(ROOT), out=str(out))],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.addCleanup(end_session, runner)
            self.assertTrue(wait_for(lambda: (out / "ready").exists(), 60), "the part did not start within a minute")
            runner.send_signal(signal.SIGINT)
            self.assertTrue(wait_for(lambda: (out / "ending").exists(), 60), "the part was not ended within a minute")
            runner.send_signal(signal.SIGTERM)
            self.assertEqual(runner.wait(timeout=60), -signal.SIGTERM)
            self.assertFalse((out / lanes.RUNNING).exists())

    def sigterm_then_sigint(self, *args):
        """SLOW_TO_END's runner sent SIGTERM and then, while its cleanup
        waits for the part slow to end, SIGINT, `kill <pid>` and then
        Ctrl-C: its exit code, and whether its caller went on and the
        marker stayed."""
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            runner = subprocess.Popen([sys.executable, "-c", SLOW_TO_END.format(root=str(ROOT), out=str(out)), *args],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.addCleanup(end_session, runner)
            self.assertTrue(wait_for(lambda: (out / "ready").exists(), 60), "the part did not start within a minute")
            runner.send_signal(signal.SIGTERM)
            self.assertTrue(wait_for(lambda: (out / "ending").exists(), 60), "the part was not ended within a minute")
            runner.send_signal(signal.SIGINT)
            return runner.wait(timeout=60), (Path(folder) / "went-on").exists(), (out / lanes.RUNNING).exists()

    def test_a_sigint_while_a_sigterm_ends_the_run_leaves_it_ended_by_the_sigterm(self):
        """The cleanup holds the SIGINT until it is done, and its
        KeyboardInterrupt, raised as the hold ends, is taken there, so the
        run still ends by the SIGTERM; let out of the cleanup, it ended the
        run by SIGINT, and a caller that catches a KeyboardInterrupt went on
        after the SIGTERM."""
        self.assertEqual(self.sigterm_then_sigint(), (-signal.SIGTERM, False, False))

    def test_a_caller_s_own_sigint_handler_while_a_sigterm_ends_the_run_leaves_it_ended_by_the_sigterm(self):
        """A caller's own SIGINT handler that ends the program raised
        SystemExit as the hold ended, which the cleanup did not take, so
        the run never ended by the SIGTERM and the caller, which catches a
        SystemExit, went on after it. Whatever a handler raises there is
        taken, and raised again only once the run has not ended by a
        SIGTERM."""
        self.assertEqual(self.sigterm_then_sigint("its own SIGINT handler"), (-signal.SIGTERM, False, False))

    def test_a_sigint_held_until_the_cleanup_ends_is_raised_once_it_is_done(self):
        """A Ctrl-C while the cleanup holds the signals reaches the runner
        as the hold ends, once the marker is gone; its KeyboardInterrupt
        is taken there and raised once the cleanup is done, where a run
        that dropped it would return as if no Ctrl-C had come."""
        def interrupted_as_the_parts_end(grace=10.0):
            signal.raise_signal(signal.SIGINT)

        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        handler = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=1, quick=True)
            with mock.patch.object(lanes, "first_wave", lambda settings, results: []), \
                    mock.patch.object(lanes, "second_wave", lambda settings, results: []), \
                    mock.patch.object(lanes, "end_workers", interrupted_as_the_parts_end), \
                    self.assertRaises(KeyboardInterrupt):
                lanes.run(settings, io.StringIO())
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())
        self.assertIs(signal.getsignal(signal.SIGTERM), handler)

    def run_tripped_as_the_cleanup_begins(self, parts, cleanup_call):
        """A run whose first wave is the parts given and whose wait raises
        KeyboardInterrupt, as Ctrl-C does there, with SIGINT and SIGTERM
        both tripped in one C call as the cleanup begins, at the given
        call of signals_held: what the run raised, and the SIGTERMs the
        handler it put back saw."""
        signalled, calls, held = [], [], lanes.signals_held

        def recorded(signum, frame):
            signalled.append(signum)

        def interrupted(*args):
            raise KeyboardInterrupt

        def tripped_as_the_cleanup_begins():
            calls.append(1)
            if len(calls) == cleanup_call:
                list(map(_thread.interrupt_main, [signal.SIGINT, signal.SIGTERM]))
            return held()

        self.addCleanup(lanes.end_workers, 0.5)
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        self.addCleanup(signal.signal, signal.SIGTERM, signal.signal(signal.SIGTERM, recorded))
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=1, quick=True)
            with mock.patch.object(lanes, "first_wave", lambda settings, results: parts), \
                    mock.patch.object(lanes, "second_wave", lambda settings, results: []), \
                    mock.patch.object(lanes, "wait", interrupted), \
                    mock.patch.object(lanes, "signals_held", tripped_as_the_cleanup_begins), \
                    self.assertRaises(SystemExit) as ended:
                try:
                    lanes.run(settings, io.StringIO())
                except KeyboardInterrupt:
                    self.fail("the run ended by the SIGINT alone, though a SIGTERM reached it")
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())
        self.assertIs(signal.getsignal(signal.SIGTERM), recorded)
        self.assertFalse([child for child in multiprocessing.active_children() if child.name == "a part"])
        return ended.exception.code, signalled

    def test_two_signals_as_the_cleanup_begins_still_end_the_run_whole(self):
        """A SIGINT and a SIGTERM that come together as the cleanup begins
        have their handlers run one after the other: SIGINT's
        KeyboardInterrupt was taken and the cleanup begun again, but
        SIGTERM's handler ran at the cleanup loop's back edge, which no try
        holds, and its Terminated left run past all of it, the part running
        on, the marker left and the handler never put back. From the
        cleanup's start the handler records the signal, and the run ends by
        it once the cleanup is done. Here the two come after a body that
        Ctrl-C ended, with a part running, where Python 3.12 met it, and
        after one that ended as it should, where 3.13 met it too."""
        with self.subTest(body="ended by Ctrl-C"):
            self.assertEqual(self.run_tripped_as_the_cleanup_begins([("a part", sleeps, ())], 2),
                             (128 + signal.SIGTERM, [signal.SIGTERM]))
        with self.subTest(body="ended as it should"):
            self.assertEqual(self.run_tripped_as_the_cleanup_begins([], 1), (128 + signal.SIGTERM, [signal.SIGTERM]))

    def test_a_signal_as_the_handler_is_put_back_waits_until_the_marker_is_removed(self):
        """Once the cleanup has put the default handler back, a SIGTERM
        ends the process at once, so one that came before the marker was
        removed left it naming a dead process; held, it ends the process
        once the marker is gone."""
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            runner = subprocess.Popen([sys.executable, "-c",
                                       SIGNALLED_AS_ITS_HANDLER_IS_PUT_BACK.format(root=str(ROOT), out=str(out))],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.addCleanup(end_session, runner)
            self.assertEqual(runner.wait(timeout=60), -signal.SIGTERM)
            self.assertTrue((out / "deep.json").exists())
            self.assertFalse((out / lanes.RUNNING).exists())

    def test_two_signals_that_land_as_a_part_is_forked_still_end_the_run_whole(self):
        """A SIGINT and a SIGTERM held while a part was forked were let in
        together; SIGINT's KeyboardInterrupt left first, and SIGTERM's
        handler raised as run's finally began, past the whole cleanup: the
        part ran on, the runner waited for it at its exit, the handler was
        never put back and the marker stayed. The cleanup is begun again
        until it is done, from its start the SIGTERM is recorded and not
        raised, and the run ends by it."""
        forked, fork, signalled = [], os.fork, []

        def both_signals_as_it_forks():
            pid = fork()
            if pid:
                forked.append(pid)
                os.kill(os.getpid(), signal.SIGINT)
                os.kill(os.getpid(), signal.SIGTERM)
            return pid

        def recorded(signum, frame):
            signalled.append(signum)

        self.addCleanup(end_forked, forked)
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        self.addCleanup(signal.signal, signal.SIGTERM, signal.signal(signal.SIGTERM, recorded))
        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=1, quick=True)
            with mock.patch.object(lanes, "first_wave", lambda settings, results: [("a part", sleeps, ())]), \
                    mock.patch.object(lanes, "second_wave", lambda settings, results: []), \
                    mock.patch.object(os, "fork", both_signals_as_it_forks), self.assertRaises(SystemExit) as ended:
                try:
                    lanes.run(settings, io.StringIO())
                except KeyboardInterrupt:
                    self.fail("the run ended by the SIGINT alone, though a SIGTERM reached it")
            self.assertFalse((Path(folder) / lanes.RUNNING).exists())
        self.assertEqual(ended.exception.code, 128 + signal.SIGTERM)
        self.assertEqual(signalled, [signal.SIGTERM])
        self.assertIs(signal.getsignal(signal.SIGTERM), recorded)
        self.assertEqual(len(forked), 1)
        self.assertFalse(alive(forked[0]))

    def test_a_handler_that_runs_as_the_hold_begins_leaves_nothing_held(self):
        """CPython runs the handler of a signal that came just before the
        mask was set once it is set; what the handler raised left the
        hold's start with SIGINT and SIGTERM held for good, so a caller
        that went on after it could be stopped by neither. Here the
        signal comes as pthread_sigmask reads its set, in C, where no
        handler runs before the mask is set."""
        class Raised(Exception):
            pass

        def raises(signum, frame):
            raise Raised()

        before = signal.pthread_sigmask(signal.SIG_BLOCK, ())
        self.addCleanup(signal.pthread_sigmask, signal.SIG_SETMASK, before)
        self.addCleanup(signal.signal, signal.SIGTERM, signal.signal(signal.SIGTERM, raises))
        tripped = itertools.chain(lanes.HELD, filter(None, map(_thread.interrupt_main, [signal.SIGTERM])))
        with mock.patch.object(lanes, "HELD", tripped), self.assertRaises(Raised):
            with lanes.signals_held():
                self.fail("the held block ran")
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), before)

    def test_an_exit_past_the_cleanup_ends_the_parts(self):
        """An exit that passed run's cleanup by, an exception the runner
        did not catch, had multiprocessing's exit handler wait for the
        parts, each running its search on for hours; the parts are
        daemons, so the exit handler ends them."""
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            runner = subprocess.Popen([sys.executable, "-c", LEAVES_ITS_PART.format(root=str(ROOT), out=str(out))],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.addCleanup(end_session, runner)
            self.assertTrue(wait_for(lambda: (out / "ready").exists(), 60), "the part did not start within a minute")
            part = int((out / "ready").read_text(encoding="ascii"))
            runner.send_signal(signal.SIGUSR1)
            try:
                code = runner.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.fail("the runner waited for its part at its exit")
            self.assertEqual(code, 1)
            self.assertFalse(alive(part))

    def test_a_runner_multiprocessing_started_ends_by_the_signal(self):
        """Under a test runner that forks, the runner is itself a process
        multiprocessing started, and there the handler took a part's way:
        it raised SystemExit, and a caller that catches one went on after
        the signal. The process's id tells the runner from its parts."""
        context = lanes.fork_context()
        with tempfile.TemporaryDirectory() as folder:
            runner = context.Process(target=runs_the_lane_and_goes_on, args=(folder,))
            runner.start()
            self.addCleanup(runner.join)
            self.addCleanup(runner.kill)
            self.addCleanup(kill_group, runner.pid)
            self.assertTrue(wait_for(lambda: (Path(folder) / "ready").exists(), 60), "the part did not start within a minute")
            runner.terminate()
            runner.join(60)
            self.assertEqual(runner.exitcode, -signal.SIGTERM)
            self.assertFalse((Path(folder) / "went-on").exists())
            self.assertFalse((Path(folder) / "out" / lanes.RUNNING).exists())

    def test_the_file_under_the_process_s_own_name_goes_however_the_marker_fails(self):
        """The marker is written under a name of the process's own first;
        where the folder holds no hard links and the exclusive create
        failed too, or where the disk filled as that file was written,
        it stayed behind."""
        def no_links(source, target):
            raise PermissionError(errno.EPERM, "Operation not permitted")

        opened = Path.open

        def no_exclusive_create(path, mode="r", *args, **kwargs):
            if "x" in mode:
                raise PermissionError(errno.EACCES, "Permission denied")
            return opened(path, mode, *args, **kwargs)

        def made_then_full(path, *args, **kwargs):
            path.touch()
            raise OSError(errno.ENOSPC, "No space left on device")

        with tempfile.TemporaryDirectory() as folder:
            settings = lanes.Settings(folder, only=("search",), jobs=2, quick=True)
            with mock.patch.object(lanes.os, "link", no_links), mock.patch.object(Path, "open", no_exclusive_create), \
                    self.assertRaises(PermissionError):
                lanes.take_marker(settings)
            self.assertEqual(list(Path(folder).iterdir()), [])
            with mock.patch.object(Path, "write_text", made_then_full), self.assertRaises(OSError) as full:
                lanes.take_marker(settings)
            self.assertEqual(full.exception.errno, errno.ENOSPC)
            self.assertEqual(list(Path(folder).iterdir()), [])

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
