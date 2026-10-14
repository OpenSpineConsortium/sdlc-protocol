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
hour (deep.search), removed once its result is in the file; `--resume`
starts a run again from the file and the checkpoints, running only the
parts the file does not hold and continuing each search from where its
checkpoint left it, so a restart of the machine costs a quarter of an
hour and not the hours. The run is red, exit code 1, on any violation,
mismatch, disagreement, differing guard answer, or a search a budget
stopped before it was done: a stopped search has not checked its space,
and the owner's rule is that a missed violation costs more than the
hours.
"""

import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

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
# Where the searches keep their checkpoints under the output folder.
CHECKPOINTS = "checkpoints"

# What --quick runs: a few steps of everything, for a look and for the tests.
QUICK = {"depths": {"E-two": 3, "E-deep": 4, "E-faults": 2}, "soak_seeds": 4, "soak_steps": 40, "soak_chunk": 2,
         "conformance_paths": 6, "conformance_length": 4, "gate_depth": 4, "gate_sequences": 3, "gate_length": 8}


class Settings:
    """How much of each part a run does."""

    def __init__(self, out, only=(), jobs=None, budget=None, quick=False, resume=False,
                 checkpoint_every=deep.CHECKPOINT_EVERY):
        self.out = Path(out)
        self.only = tuple(only)
        self.jobs = jobs or os.cpu_count() or 2
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


# --- the parts, each a function a process runs ---------------------------------

def say(line):
    print(line, file=sys.stderr, flush=True)


def run_search(name, out, depth, budget, checkpoint=None, checkpoint_every=deep.CHECKPOINT_EVERY):
    space = deep.SPACES[name]
    found = deep.search_space(space, oracle.AS_BUILT, budget, lambda line: say(f"{name}: {line}"), depth, checkpoint,
                              checkpoint_every)
    written = deep.write_counterexamples(out, space, found.found, oracle.AS_BUILT, SHRINK_BUDGET)
    for violation in written:
        violation["finding"] = known.search_finding(name, violation["guarantee"], violation["detail"])
    return {"space": name, "depth": found.depth, "states": found.states, "transitions": found.transitions,
            "seconds": round(found.seconds, 1), "stopped": found.stopped, "violations": written,
            "differences": known.search_differences(name, [(v["guarantee"], v["detail"]) for v in written])}


def run_soak(seeds, steps, out):
    """A chunk of the soak's seeds; each violation shortened from the walk
    that met it and written as a trace file."""
    walks, written = [], []
    for seed in seeds:
        walk = soak.walk(seed, steps)
        walks.append(walk)
        for guarantee, detail, at in walk.violations:
            target = (guarantee, detail)
            shorter = shrink.shrink(soak.SPACE.start, soak.SCOPE, deep.symbolic_trace(soak.SPACE, walk.verbs[:at + 1]),
                                    target, oracle.AS_BUILT, SHRINK_BUDGET)
            data = deep.trace_json(soak.SPACE, shorter.trace, f"{guarantee} {detail}")
            path = deep.write_trace(out, f"{guarantee}-soak-{seed}-{at}", data)
            written.append({"seed": seed, "step": at, "guarantee": guarantee, "detail": detail,
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


def run_conformance(name, counterexamples, count, length, seed):
    results = deep.conformance([deep.SPACES[name]], counterexamples, count, length, seed)
    return {"space": name, **results}


# --- the run ---------------------------------------------------------------------

def done_parts(results):
    """The parts a results file already holds, by name, which a resumed
    run does not run again."""
    done = {f"search {s['space']}" for s in results["searches"]}
    done |= {f"soak {c['seeds'][0]}" for c in results["soak"]["chunks"]}
    done |= {f"conformance {c['space']}" for c in results["conformance"]}
    if results["gate"] is not None:
        done.add("gate")
    if results["git"]["scenarios"]:
        done.add("git scenarios")
    if results["git"]["traces"]:
        done.add("git traces")
    if results["guard"] is not None:
        done.add("guard")
    return done


def first_wave(settings, pool, results):
    """The parts that depend on nothing, submitted, but those the results
    already hold; a future for each, with its part's name."""
    done = done_parts(results)
    futures = {}

    def submit(part, function, *args):
        if part not in done:
            futures[pool.submit(function, *args)] = part

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
    return futures


def second_wave(settings, pool, results):
    """Conformance, once the searches have written their counterexamples."""
    futures = {}
    if not settings.wanted("conformance"):
        return futures
    done = done_parts(results)
    files = [v["file"] for s in results["searches"] for v in s["violations"]]
    files += [v["file"] for s in results["soak"]["chunks"] for v in s["violations"]]
    spaces = list(deep.SPACES)
    share, left = divmod(settings.conformance_paths, len(spaces))
    for n, name in enumerate(spaces):
        if f"conformance {name}" not in done:
            futures[pool.submit(run_conformance, name, files if n == 0 else (), share + (n < left),
                                settings.conformance_length, 1000 * n)] = f"conformance {name}"
    return futures


def fresh_results():
    return {"searches": [], "soak": {"chunks": []}, "gate": None, "git": {"scenarios": [], "traces": []},
            "guard": None, "conformance": [], "seconds": 0.0, "red": []}


def take(results, part, result):
    if part.startswith("search "):
        results["searches"].append(result)
    elif part.startswith("soak "):
        results["soak"]["chunks"].append(result)
    elif part == "gate":
        results["gate"] = result
    elif part == "git scenarios":
        results["git"]["scenarios"] = result
    elif part == "git traces":
        results["git"]["traces"] = result
    elif part == "guard":
        results["guard"] = result
    elif part.startswith("conformance "):
        results["conformance"].append(result)


def red_lines(results):
    """Why the run is red, one line each; none when it is green."""
    lines = []
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
    if guard is not None and guard["missing"]:
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
    (settings.out / "deep.json").write_text(json.dumps(results, indent=1, ensure_ascii=True) + "\n", encoding="ascii")


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
                         f"{m['seconds']} s, {m['violation_count']} violations")
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
                     f"{len(results['guard']['missing'])} of the file's missing")
    if results["conformance"]:
        lines.append(f"conformance: {sum(c['paths'] for c in results['conformance'])} paths and "
                     f"{sum(c['counterexamples'] for c in results['conformance'])} counterexamples in both worlds, "
                     f"{sum(len(c['disagreements']) for c in results['conformance'])} disagreements, "
                     f"{round(sum(c['seconds'] for c in results['conformance']), 1)} s")
    return lines


def kept_results(settings):
    """A resumed run's results so far, from the file the last run left,
    counting its seconds on; a fresh run's otherwise."""
    file = settings.out / "deep.json"
    if settings.resume and file.exists():
        results = json.loads(file.read_text(encoding="ascii"))
        return results, time.perf_counter() - results["seconds"]
    return fresh_results(), time.perf_counter()


def run(settings, out=sys.stdout):
    """The whole lane; the results, written to <out>/deep.json as they
    come, and exit code 1 when any part is red."""
    results, began = kept_results(settings)
    write_results(settings, results, began)
    with ProcessPoolExecutor(max_workers=settings.jobs) as pool:
        for wave in (first_wave, second_wave):
            futures = wave(settings, pool, results)
            for future in as_completed(futures):
                part = futures[future]
                take(results, part, future.result())
                write_results(settings, results, began)
                if part.startswith("search "):
                    settings.checkpoint(part[len("search "):]).unlink(missing_ok=True)
                print(f"    {part} done at {results['seconds']} s", file=out, flush=True)
    for line in summary_lines(results):
        print(line, file=out)
    for line in results["red"]:
        print("  RED " + line, file=out)
    print(f"deep lane: {'found something' if results['red'] else 'ok'} in {results['seconds']} s; "
          f"{settings.out / 'deep.json'}", file=out)
    return 1 if results["red"] else 0
