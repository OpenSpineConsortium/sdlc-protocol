"""The deep lane as one run (DESIGN.md, 14, step 6): every part in its
own process, the results in one file.

`python3 scripts/validate.py deep` runs it. The first wave is the
searches (deep.SPACES, each violation shortened and written as a trace
file), the soak in chunks of seeds (soak.py, its violations written the
same way), the gate's lane (gate_search.py), the scenarios and the
regression traces over the git world, and git's answers for the guard
grammar recorded again; the second wave, once the searches have written
their counterexamples, is conformance (deep.conformance), the
counterexamples and sampled paths of every space played in both worlds
and compared, split by space. Each part runs in a process of its own, as
many at once as there are processors or `--jobs` says, since the
searches take hours and share nothing.

The results file (<out>/deep.json) is rewritten as each part ends, so a
run that is killed leaves what it had measured, and each search writes
a checkpoint of its state under <out>/checkpoints/ every quarter of an
hour (deep.search), removed once its result is in the file and the
search was not stopped; `--resume` starts a run again from the file and
the checkpoints, running only the parts the file does not hold, a
search a budget stopped, a gate exploration a budget stopped and a part
that raised among the parts it lacks, and continuing each search from
where its checkpoint left it, so a restart of the machine costs a
quarter of an hour and not the hours; a search's budget counts from its
first start, the checkpoint's seconds included, so a stopped search goes
on under a larger `--budget` or none, and resumed under the budget that
stopped it expands nothing and is red again. The file records the settings and
the code the run was made under (the "run" entry; the code is
deep.code_digest, the harness's and the library's source and the
harness's data files), and a resume under others is refused, since a
part measured under other settings or other code is not this run's; a
run that is not a resume starts clean, with the checkpoints and the
trace files an earlier run left under <out> removed first. A run writes
the file <out>/running, its process id, while it runs, and a second run
into the same folder is refused while that process lives, resume or
not, since the two would write one results file and the fresh one would
remove the checkpoints the other is writing; a SIGTERM to that process,
what `kill <pid>` sends, ends the workers and removes the file, as
Ctrl-C does. Each part runs in a
process of its own, forked, with a pipe for its result, so a process
that dies (killed for its memory, say) takes its own part alone,
recorded as raised, where one shared pool would lose every part in
flight and refuse the second wave, and the parent forks with no helper
thread alive. The counterexamples the searches and the soak
wrote go through conformance as a part of their own, done only while
the files it played are the files the results name, so a resumed search
that writes more is followed by their conformance. The run is red, exit
code 1, on any violation, mismatch, disagreement, differing guard
answer, a part that raised, or a search a budget stopped before it was
done: a stopped search has not checked its space, and the owner's rule
is that a missed violation costs more than the hours.
"""

import copy
import json
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from multiprocessing.connection import wait
from pathlib import Path

from sdlc_protocol import __version__

from . import deep, gate_search, known, oracle, shrink, soak

SOAK_SEEDS = 1000
SOAK_STEPS = 200
SOAK_CHUNK = 50
CONFORMANCE_PATHS = 200
CONFORMANCE_LENGTH = 8
GATE_DEPTH = 8
GATE_SEQUENCES = 40
GATE_LENGTH = 12
SHRINK_BUDGET = 600.0
# The most one part spends shortening all its violations, so a red search
# or soak chunk ends within half an hour of its search, inside the job's
# six hours when the searches run under validate.yml's five.
SHRINK_TOTAL = 1800.0
# Where the searches keep their checkpoints under the output folder.
CHECKPOINTS = "checkpoints"
# The file under the output folder that names the process of a run in
# progress there.
RUNNING = "running"
# The conformance part that plays the counterexample files.
COUNTEREXAMPLES = "counterexamples"
# The parts --only may name.
PARTS = ("search", "soak", "gate", "git", "guard", "conformance")

# What --quick runs: a few steps of everything, for a look and for the tests.
QUICK = {"depths": {"E-two": 3, "E-deep": 4, "E-faults": 2}, "soak_seeds": 4, "soak_steps": 40, "soak_chunk": 2,
         "conformance_paths": 6, "conformance_length": 4, "gate_depth": 4, "gate_sequences": 3, "gate_length": 8}


class Settings:
    """How much of each part a run does."""

    def __init__(self, out, only=(), jobs=None, budget=None, quick=False, resume=False,
                 checkpoint_every=deep.CHECKPOINT_EVERY):
        unknown = [part for part in only if part not in PARTS]
        if unknown:
            raise ValueError(f"--only takes {', '.join(PARTS)}, not {', '.join(unknown)}")
        self.out = Path(out)
        if inside_the_code(self.out):
            # The digest would count the run's own results and trace files
            # as code, so a resume would be refused and every checkpoint
            # read as another search's.
            raise ValueError(f"--out takes a folder outside {' and '.join(deep.CODE_DIRS)}, whose files stamp the "
                             f"run's code, or one under {deep.NOT_CODE[0]}, not {out}")
        self.only = tuple(only)
        self.jobs = jobs or os.cpu_count() or 2
        if self.jobs < 1:
            # run_jobs would submit nothing and spin for good: no part fits
            # under a count below one, and waiting on no future returns at once.
            raise ValueError("--jobs takes a count of processes of 1 or more")
        self.budget = budget
        self.quick = quick
        self.resume = resume
        self.checkpoint_every = checkpoint_every
        self.depths = dict(QUICK["depths"]) if quick else {}
        self.soak_seeds = QUICK["soak_seeds"] if quick else SOAK_SEEDS
        self.soak_steps = QUICK["soak_steps"] if quick else SOAK_STEPS
        self.soak_chunk = QUICK["soak_chunk"] if quick else SOAK_CHUNK
        self.conformance_paths = QUICK["conformance_paths"] if quick else CONFORMANCE_PATHS
        self.conformance_length = QUICK["conformance_length"] if quick else CONFORMANCE_LENGTH
        self.gate_depth = QUICK["gate_depth"] if quick else GATE_DEPTH
        self.gate_sequences = QUICK["gate_sequences"] if quick else GATE_SEQUENCES
        self.gate_length = QUICK["gate_length"] if quick else GATE_LENGTH

    def wanted(self, part):
        return not self.only or part in self.only

    def checkpoint(self, name):
        return self.out / CHECKPOINTS / f"{name}.pickle"

    def record(self):
        """What shapes the run's results: a file made under other settings
        or other code is not continued."""
        return {"library": __version__, "code": deep.code_digest(), "git": git_version(), "quick": self.quick,
                "depths": {name: self.depths.get(name, space.depth) for name, space in deep.SPACES.items()},
                "soak": {"seeds": self.soak_seeds, "steps": self.soak_steps, "chunk": self.soak_chunk},
                "conformance": {"paths": self.conformance_paths, "length": self.conformance_length},
                "gate": {"depth": self.gate_depth, "sequences": self.gate_sequences, "length": self.gate_length}}


def inside_the_code(out):
    """Whether a folder lies among the files deep.code_digest reads."""
    where = out.resolve()
    for folder in deep.CODE_DIRS:
        code = (deep.ROOT / folder).resolve()
        if where == code or code in where.parents:
            kept = [(deep.ROOT / name).resolve() for name in deep.NOT_CODE]
            return not any(where == k or k in where.parents for k in kept)
    return False


def git_version():
    """The git the run's git parts answer under, as `git --version` says
    it: the guard part, the git scenarios and traces, the gate over git
    and conformance are git's answers, so a run is not resumed under
    another git."""
    return subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=60).stdout.strip()


# --- the parts, each a function a process runs ---------------------------------

def say(line):
    print(line, file=sys.stderr, flush=True)


def run_search(name, out, depth, budget, checkpoint=None, checkpoint_every=deep.CHECKPOINT_EVERY):
    space = deep.SPACES[name]
    found = deep.search_space(space, oracle.AS_BUILT, budget, lambda line: say(f"{name}: {line}"), depth, checkpoint,
                              checkpoint_every)
    written = deep.write_counterexamples(out, space, found.found, oracle.AS_BUILT, SHRINK_BUDGET, found.depth,
                                         SHRINK_TOTAL)
    for violation in written:
        violation["finding"] = known.search_finding(name, violation["guarantee"], violation["detail"])
    return {"space": name, "depth": found.depth, "states": found.states, "transitions": found.transitions,
            "seconds": round(found.seconds, 1), "stopped": found.stopped, "violations": written,
            "differences": known.search_differences(name, [(v["guarantee"], v["detail"]) for v in written])}


def run_soak(seeds, steps, out):
    """A chunk of the soak's seeds; each violation shortened from the walk
    that met it and written as a trace file, numbered within its walk,
    since one step can show several details under one guarantee (a run
    that releases a claim not due makes both the comment and the rename
    G3's) and the files of two must not share a name; the detail written
    is the one the shortened trace shows (shrink.Shrunk.detail)."""
    walks, written = [], []
    ends = time.perf_counter() + SHRINK_TOTAL
    for seed in seeds:
        walk = soak.walk(seed, steps)
        walks.append(walk)
        for n, (guarantee, detail, at) in enumerate(walk.violations, 1):
            target = (guarantee, detail)
            allowed = max(0.0, min(SHRINK_BUDGET, ends - time.perf_counter()))
            shorter = shrink.shrink(soak.SPACE.start, soak.SCOPE, deep.symbolic_trace(soak.SPACE, walk.verbs[:at + 1]),
                                    target, oracle.AS_BUILT, allowed)
            shown = shorter.detail or detail
            data = deep.trace_json(soak.SPACE, shorter.trace, f"{guarantee} {shown}")
            path = deep.write_trace(out, f"{guarantee}-soak-{seed}-{at}-{n}", data)
            written.append({"seed": seed, "step": at, "guarantee": guarantee, "detail": shown,
                            "shortened_to": len(shorter.trace), "file": str(path)})
    return {"seeds": [seeds[0], seeds[-1]], **soak.summary(walks), "violations": written}


def run_gate(depth, count, length, budget):
    return gate_search.lane(depth, count, length, 0, budget)


def run_git_scenarios():
    return deep.scenarios_over_git()


def run_git_traces():
    return deep.traces_over_git()


def run_guard():
    with tempfile.TemporaryDirectory(prefix="guard-again-") as scratch:
        return deep.guard_recorded_again(Path(scratch) / "guard_git.json")


def run_conformance(name, count, length, seed):
    """The sampled paths of one space, played in both worlds."""
    results = deep.conformance([deep.SPACES[name]], (), count, length, seed)
    return {"space": name, **results}


def run_conformance_files(files):
    """Every counterexample file written so far, played in both worlds;
    the files it played, so a resumed run knows whether it is owed
    again."""
    results = deep.conformance((), files, 0)
    return {"space": COUNTEREXAMPLES, "files": list(files), **results}


# --- the run ---------------------------------------------------------------------

def counterexample_files(results):
    """The trace files the searches and the soak wrote, in the order the
    results name them."""
    files = [v["file"] for s in results["searches"] for v in s["violations"]]
    files += [v["file"] for s in results["soak"]["chunks"] for v in s["violations"]]
    return files


def done_parts(results):
    """The parts a results file already holds, by name, which a resumed
    run does not run again; a search the budget stopped is not done, and
    a resume runs it again from its checkpoint, nor a gate whose budget
    stopped an exploration, nor the counterexamples' conformance once a
    search or the soak has written files it did not play, nor a part
    that raised, which the errors name and the results do not hold."""
    done = {f"search {s['space']}" for s in results["searches"] if not s["stopped"]}
    done |= {f"soak {c['seeds'][0]}" for c in results["soak"]["chunks"]}
    files = counterexample_files(results)
    done |= {f"conformance {c['space']}" for c in results["conformance"]
             if c["space"] != COUNTEREXAMPLES or c["files"] == files}
    gate = results["gate"]
    if gate is not None and not any(m.get("stopped") for m in gate["memory"]):
        done.add("gate")
    if results["git"]["scenarios"]:
        done.add("git scenarios")
    if results["git"]["traces"]:
        done.add("git traces")
    if results["guard"] is not None:
        done.add("guard")
    return done


def first_wave(settings, results):
    """The parts that depend on nothing, but those the results already
    hold: each as its name, its function and the arguments."""
    done = done_parts(results)
    jobs = []

    def submit(part, function, *args):
        if part not in done:
            jobs.append((part, function, args))

    if settings.wanted("search"):
        for name in deep.SPACES:
            depth = settings.depths.get(name)
            submit(f"search {name}", run_search, name, str(settings.out), depth, settings.budget,
                   str(settings.checkpoint(name)), settings.checkpoint_every)
    if settings.wanted("soak"):
        seeds = list(range(settings.soak_seeds))
        for at in range(0, len(seeds), settings.soak_chunk):
            chunk = seeds[at:at + settings.soak_chunk]
            submit(f"soak {chunk[0]}", run_soak, chunk, settings.soak_steps, str(settings.out))
    if settings.wanted("gate"):
        submit("gate", run_gate, settings.gate_depth, settings.gate_sequences, settings.gate_length, settings.budget)
    if settings.wanted("git"):
        submit("git scenarios", run_git_scenarios)
        submit("git traces", run_git_traces)
    if settings.wanted("guard"):
        submit("guard", run_guard)
    return jobs


def second_wave(settings, results):
    """Conformance, once the searches and the soak have written their
    counterexamples: the sampled paths of each space, and the files."""
    jobs = []
    if not settings.wanted("conformance"):
        return jobs
    done = done_parts(results)
    spaces = list(deep.SPACES)
    share, left = divmod(settings.conformance_paths, len(spaces))
    for n, name in enumerate(spaces):
        if f"conformance {name}" not in done:
            jobs.append((f"conformance {name}", run_conformance, (name, share + (n < left), settings.conformance_length, 1000 * n)))
    files = counterexample_files(results)
    if files and f"conformance {COUNTEREXAMPLES}" not in done:
        jobs.append((f"conformance {COUNTEREXAMPLES}", run_conformance_files, (files,)))
    return jobs


def owed(settings, results):
    """The parts the run still owes once this invocation's waves are
    done: those a resume asking for every part any invocation of the run
    asked for would run, but a stopped search or gate and a part that
    raised, which the file already shows red. A resume narrower than the
    run it resumes, say `--only search` after a run cut short before
    conformance, leaves the rest owed."""
    asked = copy.copy(settings)
    asked.only = tuple(results["asked"])
    shown_red = {e["part"] for e in results["errors"]}
    shown_red |= {f"search {s['space']}" for s in results["searches"] if s["stopped"]}
    if results["gate"] is not None and any(m.get("stopped") for m in results["gate"]["memory"]):
        shown_red.add("gate")
    return [part for part, _, _ in first_wave(asked, results) + second_wave(asked, results) if part not in shown_red]


def fresh_results(settings=None):
    return {"run": settings.record() if settings is not None else None, "searches": [], "soak": {"chunks": []}, "gate": None,
            "git": {"scenarios": [], "traces": []}, "guard": None, "conformance": [], "errors": [], "seconds": 0.0,
            "red": []}


def take(results, part, result, out):
    """The part's result into the results, in place of any the part left
    before, as a search run again from its checkpoint or a chunk run
    again after it raised; a search's earlier files that the new result
    does not name are removed from this run's traces folder, since its
    files are numbered anew. They are compared by name, since a resume
    may spell the folder another way, relative, absolute or through a
    link, and the same file under two spellings is not to go."""
    if part.startswith("search "):
        before = [s for s in results["searches"] if s["space"] == result["space"]]
        named = {Path(v["file"]).name for v in result["violations"]}
        for name in {Path(v["file"]).name for s in before for v in s["violations"]} - named:
            (Path(out) / deep.TRACES / name).unlink(missing_ok=True)
        results["searches"] = [s for s in results["searches"] if s["space"] != result["space"]] + [result]
    elif part.startswith("soak "):
        results["soak"]["chunks"] = [c for c in results["soak"]["chunks"] if c["seeds"] != result["seeds"]] + [result]
    elif part == "gate":
        results["gate"] = result
    elif part == "git scenarios":
        results["git"]["scenarios"] = result
    elif part == "git traces":
        results["git"]["traces"] = result
    elif part == "guard":
        results["guard"] = result
    elif part.startswith("conformance "):
        results["conformance"] = [c for c in results["conformance"] if c["space"] != result["space"]] + [result]


def red_lines(results):
    """Why the run is red, one line each; none when it is green. A file a
    run writes before its end says the run is not finished, so a run
    killed, or a job cancelled, before a red part landed does not read
    green, and one a resume narrower than the run left names the parts
    still owed."""
    lines = [f"{e['part']} raised: {e['error']}" for e in results.get("errors", [])]
    if results.get("finished") is False:
        owed = results.get("owed")
        lines.append(f"the run did not finish: still owed: {', '.join(owed)}" if owed else
                     "the run did not finish: the parts it had not landed are not in this file")
    for s in results["searches"]:
        if s["stopped"]:
            lines.append(f"search {s['space']}: stopped by the budget after {s['states']} states, not done")
        for v in s["violations"]:
            if v["finding"] is None:
                lines.append(f"search {s['space']}: {v['guarantee']} {v['detail']} ({v['count']} times), {v['file']}")
        lines += s["differences"]
    for chunk in results["soak"]["chunks"]:
        for v in chunk["violations"]:
            lines.append(f"soak seed {v['seed']} step {v['step']}: {v['guarantee']} {v['detail']}, {v['file']}")
    gate = results["gate"]
    if gate is not None:
        for memory in gate["memory"]:
            if memory.get("stopped"):
                lines.append(f"gate {memory['policy']}: stopped by the budget after {memory['states']} states, not done")
            if memory["violation_count"]:
                lines.append(f"gate {memory['policy']}: {memory['violation_count']} violations over MemoryWorld")
        for m in gate["s05g"]["mismatches"]:
            lines.append(f"gate S05g: {m}")
        for act, problem in gate["s05g"]["violations"]:
            lines.append(f"gate S05g, {act}: {problem}")
        for v in gate["git"]["violations"]:
            lines.append(f"gate over git, {v['policy']} seed {v['seed']}, {v['act']}: {v['problem']}")
    for s in results["git"]["scenarios"]:
        lines += [f"git scenario {s['scenario']}: {m}" for m in s["mismatches"] + s["differences"]]
    for t in results["git"]["traces"]:
        lines += [f"git trace {t['trace']}: {m}" for m in t["mismatches"] + t["differences"]]
    guard = results["guard"]
    if guard is not None and guard["differing"]:
        lines.append(f"guard: git answers {len(guard['differing'])} argument lists differently from guard_git.json, "
                     f"e.g. {guard['differing'][0]!r}")
    if guard is not None and guard.get("missing"):
        lines.append(f"guard: guard_git.json holds {len(guard['missing'])} argument lists the grammar no longer makes, "
                     f"e.g. {guard['missing'][0]!r}")
    for c in results["conformance"]:
        for d in c["disagreements"]:
            where = d.get("file") or f"{d.get('space')} path {d.get('path')}"
            lines.append(f"conformance, {where}, step {d['step']} {d['field']}: memory {d['memory']}, git {d['git']}")
    return lines


def write_results(settings, results, began):
    results["seconds"] = round(time.perf_counter() - began, 1)
    results["red"] = red_lines(results)
    settings.out.mkdir(parents=True, exist_ok=True)
    # Written beside the file and moved into place, as a checkpoint is, so
    # a write that fails or is cut leaves the one before, which a resume
    # reads.
    file = settings.out / "deep.json"
    partial = file.with_name(file.name + ".partial")
    partial.write_text(json.dumps(results, indent=1, ensure_ascii=True) + "\n", encoding="ascii")
    partial.replace(file)


def summary_lines(results):
    lines = []
    for s in sorted(results["searches"], key=lambda s: s["space"]):
        new = [v for v in s["violations"] if v["finding"] is None]
        old = sorted({v["finding"] for v in s["violations"] if v["finding"]})
        lines.append(f"search {s['space']}: depth {s['depth']}, {s['states']} states, {s['transitions']} transitions, "
                     f"{s['seconds']} s, {len(new)} violations{', ' + ' and '.join(old) + ' met as known.json records' if old else ''}"
                     f"{', stopped by the budget' if s['stopped'] else ''}")
    chunks = results["soak"]["chunks"]
    if chunks:
        lines.append(f"soak: {sum(c['walks'] for c in chunks)} walks, {sum(c['steps'] for c in chunks)} steps, "
                     f"{round(sum(c['seconds'] for c in chunks), 1)} s of walking, "
                     f"{sum(c['violations'] if isinstance(c['violations'], int) else len(c['violations']) for c in chunks)} violations")
    gate = results["gate"]
    if gate is not None:
        for m in gate["memory"]:
            lines.append(f"gate {m['policy']}: depth {m['depth']}, {m['states']} states, {m['transitions']} transitions, "
                         f"{m['seconds']} s, {m['violation_count']} violations"
                         f"{', stopped by the budget' if m.get('stopped') else ''}")
        lines.append(f"gate S05g: {len(gate['s05g']['timeline'])} acts, {len(gate['s05g']['mismatches'])} mismatches")
        g = gate["git"]
        lines.append(f"gate over git: {g['sequences']} sequences, {g['acts']} acts, {g['merges']} merges, "
                     f"{g['main_moved']} main moved, {g['refusals']} refusals, {g['seconds']} s")
    if results["git"]["scenarios"]:
        lines.append(f"git scenarios: {len(results['git']['scenarios'])} played, "
                     f"{round(sum(s['seconds'] for s in results['git']['scenarios']), 1)} s, matching known.json")
    if results["git"]["traces"]:
        lines.append(f"git traces: {len(results['git']['traces'])} played, "
                     f"{round(sum(t['seconds'] for t in results['git']['traces']), 1)} s, matching known.json")
    if results["guard"] is not None:
        lines.append(f"guard: {results['guard']['recorded']} argument lists recorded again under {results['guard']['git']}, "
                     f"{len(results['guard']['differing'])} differing from guard_git.json ({results['guard']['recorded_git']}), "
                     f"{len(results['guard'].get('missing', []))} of the file's missing")
    if results["conformance"]:
        lines.append(f"conformance: {sum(c['paths'] for c in results['conformance'])} paths and "
                     f"{sum(c['counterexamples'] for c in results['conformance'])} counterexamples in both worlds, "
                     f"{sum(len(c['disagreements']) for c in results['conformance'])} disagreements, "
                     f"{round(sum(c['seconds'] for c in results['conformance']), 1)} s")
    return lines


def running_process(settings):
    """The process id the folder's running file names, when that process
    lives; None otherwise, a file left by a run the machine's restart
    killed among the cases. The probe is POSIX's signal 0, which asks
    after a process and sends it nothing; on Windows os.kill with any
    signal but Ctrl-C's ends the process named, so there the file is
    taken at its word and a stale one is deleted by hand, as the
    refusal says. A file naming this very process is a dead run's whose
    number a restart handed out again, since a live run never has this
    process's id."""
    file = settings.out / RUNNING
    try:
        pid = int(file.read_text(encoding="ascii").strip()[:9])
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    if os.name == "nt":
        return pid
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    return pid


def take_marker(settings):
    """The running file made for this run, before anything is read or
    removed: made exclusively, so that of two runs started at the same
    moment the second finds the first's file and is refused, where a file
    written after the liveness check let both pass it, both remove the
    checkpoints and both write one results file. The file is written
    under a name of this process's own and linked into place, so it
    appears with the process's id in it: a file made empty and written
    after read as no run to a second run in between. Where the folder
    holds no hard links (vfat, exFAT, SMB without unix extensions) it is
    made exclusively and written, empty for that moment. A file a dead
    process left, as the machine's restart leaves one, is replaced; two
    runs started at the same moment over such a file can both replace
    it, a window of a few instructions."""
    file = settings.out / RUNNING
    settings.out.mkdir(parents=True, exist_ok=True)
    mine = file.with_name(f"{RUNNING}.{os.getpid()}")
    mine.write_text(f"{os.getpid()}\n", encoding="ascii")
    try:
        try:
            os.link(mine, file)
        except FileExistsError:
            raise
        except OSError:
            with file.open("x", encoding="ascii") as marker:
                marker.write(f"{os.getpid()}\n")
    except FileExistsError:
        pid = running_process(settings)
        if pid is not None:
            mine.unlink(missing_ok=True)
            raise SystemExit(f"{file} names process {pid}, a run in progress there, so this one does not start; wait "
                             f"for it, run into another --out, or delete the file if no run is in progress") from None
        mine.replace(file)
    else:
        mine.unlink()


def kept_results(settings):
    """A resumed run's results so far, from the file the last run left,
    counting its seconds on, when the file was made under this run's
    settings and code; a fresh run's otherwise, with what an earlier run
    left under <out> removed. The marker is this run's by then, and run
    removes it however this ends, a refusal here included. A file that
    cannot be read is refused by name, not with a traceback."""
    file = settings.out / "deep.json"
    record = settings.record()
    if settings.resume and file.exists():
        try:
            results = json.loads(file.read_text(encoding="ascii"))
        except ValueError as error:
            raise SystemExit(f"{file} cannot be read ({error}), so it is not resumed; run without --resume, "
                             f"or into another --out") from None
        kept = results.get("run", {})
        differing = [name for name in record if kept.get(name) != record[name]]
        if differing:
            raise SystemExit(f"{file} was made under other settings or other code ({', '.join(differing)} differ), "
                             f"so it is not resumed; run without --resume, or into another --out")
        results.setdefault("errors", [])
        return results, time.perf_counter() - results["seconds"]
    for folder in (settings.out / CHECKPOINTS, settings.out / deep.TRACES):
        shutil.rmtree(folder, ignore_errors=True)
    return fresh_results(settings), time.perf_counter()


def fork_context():
    """The parts are forked where the platform forks: a mutant a test
    patches into the harness reaches a part by fork alone, whatever a
    later Python makes the default (3.14 starts a process by forkserver on
    Linux), and the parent forks with no helper thread alive, since the
    parts are plain processes with a pipe each and not a pool, whose
    manager and feeder threads made every fork after the first one of a
    multi-threaded process, which Python 3.12 warns may deadlock the
    child."""
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else methods[0])


def run_part(connection, function, args):
    """A part in its own process: the result, or why it raised, sent back
    through the pipe; a process that dies sends nothing."""
    try:
        outcome = ("done", function(*args))
    except Exception as error:
        outcome = ("raised", repr(error), "".join(traceback.format_exception(error)))
    connection.send(outcome)
    connection.close()


def receive(process, connection):
    """What a part's process sent, once its pipe or its end is ready:
    its outcome, or ("died", exit code) when it ended without sending."""
    try:
        outcome = connection.recv() if connection.poll() else None
    except (EOFError, OSError):
        outcome = None
    process.join()
    connection.close()
    return outcome if outcome is not None else ("died", process.exitcode)


def run_jobs(settings, jobs, results, began, out):
    """The jobs, each in a process of its own with a pipe for its result,
    as many at once as the settings allow, each result taken as it ends
    and the results written; a job that raises, or whose process dies, is
    recorded as raised, in place of the error it left before, and the
    others go on. A job that lands clears the error it left. The parent
    waits on each process's end and its pipe alike, since a result larger
    than the pipe holds is sent only as it is read."""
    pending = list(jobs)
    running = {}
    context = fork_context()
    while pending or running:
        while pending and len(running) < settings.jobs:
            part, function, args = pending.pop(0)
            ours, theirs = context.Pipe(duplex=False)
            process = context.Process(target=run_part, args=(theirs, function, args), name=part)
            process.start()
            theirs.close()
            running[part] = (process, ours)
        ready = {}
        for part, (process, connection) in running.items():
            ready[process.sentinel] = part
            ready[connection] = part
        for part in dict.fromkeys(ready[one] for one in wait(list(ready))):
            process, connection = running.pop(part)
            outcome = receive(process, connection)
            results["errors"] = [e for e in results["errors"] if e["part"] != part]
            if outcome[0] == "died":
                results["errors"].append({"part": part, "error": f"its worker process died: exit code {outcome[1]}",
                                          "traceback": ""})
                write_results(settings, results, began)
                print(f"    {part}: its worker process died at {results['seconds']} s", file=out, flush=True)
                continue
            if outcome[0] == "raised":
                results["errors"].append({"part": part, "error": outcome[1], "traceback": outcome[2]})
                write_results(settings, results, began)
                print(f"    {part} raised at {results['seconds']} s: {outcome[1]}", file=out, flush=True)
                continue
            result = outcome[1]
            take(results, part, result, settings.out)
            write_results(settings, results, began)
            if part.startswith("search ") and not result["stopped"]:
                settings.checkpoint(part[len("search "):]).unlink(missing_ok=True)
            print(f"    {part} done at {results['seconds']} s", file=out, flush=True)


class Terminated(BaseException):
    """A SIGTERM to the runner, raised where the parent waits, so run's
    finally ends the workers and removes the marker; no except of a
    caller's catches it on its way, as it would a SystemExit."""


def stop_on_term(signum, frame):
    """What a SIGTERM to the runner does, the signal `kill <pid>` sends:
    the run stops by an exception that reaches run's finally, which then
    sends the signal again under the handler that was there before, so
    the process ends as the signal ends it, and a caller such as a test
    runner, which catches a SystemExit and goes on, does not outlive it.
    Without a handler the parent would die at once with the marker left
    naming a dead process and each part's process, which watches no
    parent, running its search on for hours, orphaned. A part's process
    inherits the handler by the fork, and there, ended by end_workers, it
    ends quietly by SystemExit wherever the signal finds it, where a
    Terminated outside run_part's try reached multiprocessing's own
    except, which printed it and ended the part with 1."""
    if multiprocessing.parent_process() is not None:
        raise SystemExit(128 + signum)
    raise Terminated()


def end_workers():
    """The parts' live processes ended and waited for, each a child of
    this process, so a run that ends, however it ends, leaves none
    behind, not even one unreaped: a run that ends by its own SIGTERM
    runs no exit handler, which waited for them before, and where nothing
    else reaps an orphan, a container's first process that is no init,
    it stays. Ten seconds a part bounds the wait."""
    children = multiprocessing.active_children()
    for child in children:
        child.terminate()
    for child in children:
        child.join(10)


def run(settings, out=sys.stdout):
    """The whole lane; the results, written to <out>/deep.json as they
    come, and exit code 1 when any part is red. A part that raises is
    recorded, red, and the others go on. A SIGTERM to the runner ends the
    workers and removes the marker, as Ctrl-C and a signal to the whole
    process group do, and then ends the process by the signal; the
    handler can be set from the main thread alone, where the script and
    the tests call this. The results file says whether the run finished,
    every part any invocation of it asked for landed or shown red, and
    one that did not is red. From the marker's taking on, whatever
    ends the run, a refusal to resume included, the finally removes the
    marker."""
    take_marker(settings)
    marker = settings.out / RUNNING
    in_main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, stop_on_term) if in_main_thread else None
    terminated = False
    try:
        results, began = kept_results(settings)
        results["asked"] = sorted(set(results.get("asked", ())) | set(settings.only or PARTS))
        results["finished"], results["owed"] = False, None
        write_results(settings, results, began)
        for wave in (first_wave, second_wave):
            run_jobs(settings, wave(settings, results), results, began, out)
        results["owed"] = owed(settings, results)
        results["finished"] = not results["owed"]
        write_results(settings, results, began)
    except Terminated:
        terminated = True
    finally:
        end_workers()
        if in_main_thread:
            signal.signal(signal.SIGTERM, previous)
        marker.unlink(missing_ok=True)
    if terminated:
        os.kill(os.getpid(), signal.SIGTERM)
        raise SystemExit(128 + signal.SIGTERM)
    for line in summary_lines(results):
        print(line, file=out)
    for line in results["red"]:
        print("  RED " + line, file=out)
    print(f"deep lane: {'found something' if results['red'] else 'ok'} in {results['seconds']} s; "
          f"{settings.out / 'deep.json'}", file=out)
    return 1 if results["red"] else 0
