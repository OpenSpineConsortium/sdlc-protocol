"""The deep lane (DESIGN.md, 6.1, 6.6, 6.8 and 14, step 6).

The searches that take too long for the required check, and the checks
that need git: a depth-first search with a depth-aware digest set, from
a start the fast lane's searches share (a seeded repository after one
board run, then a prefix of verbs), over the scopes of DESIGN.md 6.8
(E-two, E-deep and E-faults); the scenarios and the regression traces
played over the git world (git_store.py), held to known.json as the
memory world's are; conformance, every counterexample the searches
wrote and sampled paths of the search scopes, each played in both
worlds and compared step by step; and git's answers for the guard
grammar recorded again and compared with the file the fast lane judges
the guard by. The soak and the gate's lanes are soak.py's and
gate_search.py's; scripts/validate.py deep runs them all and writes what
each found under an output folder, with every violation shortened
(shrink.py) and written as a trace file replay.py plays
(validation-out/traces/<guarantee>-<space>-<n>.json, DESIGN.md 6.6), so a
finding is replayed with `python3 scripts/validate.py replay <file>`,
in memory or over git.

The search keeps, for each state's 16-byte digest, the shallowest depth
it was reached at, and expands a state again when it is reached
shallower, since a plain seen-set is incomplete for a depth-bounded
search: a state first reached deep would never be expanded when reached
again shallower (DESIGN.md, 6.1). It goes on past a violation, as the
breadth-first search does, and keeps each distinct violation once with
the first trace it met it on. The reductions of DESIGN.md 6.4 are not
applied: the time cap gave 5 percent at depth 5 (step 4) and a reduced
key is a soundness risk the owner's rule, correctness over cost, does
not take; the lane runs for as long as it takes.
"""

import hashlib
import json
import pickle
import random
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from sdlc_protocol import __version__

from . import cycle, guard_grammar, invariants, known, model as M, oracle, page, replay, run, scenarios, shrink, verbs
from .model import BOT, HOUR, MAIN, OWNER, RELEASED_PREFIX
from .scenarios import Scenario, Step
from .verbs import DANA, SAM, Scope, Verb

SHARED = ("src/shared.py",)
OUT = Path("validation-out")
# What the traces folder of the output is called.
TRACES = "traces"


class Space(NamedTuple):
    """One search of the deep lane: its name, the scope, the verbs that
    bring the seeded repository (after one board run) to its start, and
    how deep it goes."""

    name: str
    scope: Scope
    prefix: tuple
    depth: int

    def scenario(self):
        """The seeded repository as a scenario with no steps, the shape a
        trace file names (replay.py), so a counterexample reproduces the
        start exactly."""
        issues = {n: ("todo",) for n in self.scope.issues}
        return Scenario(self.name, f"the deep lane's {self.name}", (), issues, {w: None for w in self.scope.workers},
                        (), tuple(self.scope.people), tuple(self.scope.files), self.scope.careless,
                        tuple(self.scope.events), self.scope.split_board)

    def start(self):
        """A fresh starting state: the seeded repository after one board
        run, then the prefix."""
        model = run.seeded(self.scenario())
        cycle.board_run(model)
        for verb in self.prefix:
            verbs.apply(model, verb, self.scope)
        return model


CLAIM = Verb("claim", "a", (1,))
HELD = (CLAIM, Verb("label-on", "a", (1, "claimed")), Verb("label-on", "a", (1, "worker:a")))
SPACES = {
    "E-two": Space("E-two", Scope(issues=(1, 2), files=((1, SHARED), (2, SHARED))), (), 7),
    "E-deep": Space("E-deep", Scope(), (), 8),
    "E-faults": Space("E-faults", Scope(people=(OWNER, DANA, SAM), faults=tuple(verbs.FAULTS), careless=True,
                                        skews=(-48, 72)), HELD, 4),
}


class Found(NamedTuple):
    guarantee: str
    detail: str
    trace: tuple  # Verb records
    count: int


class Deep(NamedTuple):
    name: str
    states: int
    transitions: int
    depth: int
    seconds: float
    found: tuple
    stopped: bool  # True when the budget ended the search before it was done

    @property
    def complete(self):
        return not self.stopped


# How often a search writes its checkpoint, in seconds.
CHECKPOINT_EVERY = 900.0
# The code a checkpoint or a results file was made under: the harness and
# the library, every .py file of each, and the harness's data files, the
# .json files the searches and the git parts read (known.json, whose
# as_built the oracle takes its switches from, guard_git.json, which the
# guard part compares with, unproven.json and the regression traces); the
# docs and the kept runs under validation/runs/ shape no result, so a run
# goes on under a changed README or a run copied in beside it.
CODE_DIRS = ("validation", "sdlc_protocol")
CODE_SUFFIXES = (".py", ".json")
NOT_CODE = ("validation/runs/",)
ROOT = Path(__file__).resolve().parent.parent


def code_files(root=ROOT):
    """The files the digest covers, in a fixed order, relative to root."""
    found = []
    for folder in CODE_DIRS:
        for file in sorted((root / folder).rglob("*")):
            relative = file.relative_to(root).as_posix()
            if file.is_file() and file.suffix in CODE_SUFFIXES and not relative.startswith(NOT_CODE):
                found.append(relative)
    return found


def code_digest(root=ROOT):
    """A digest of the harness's and the library's source and the
    harness's data, so a checkpoint or a results file made under other
    code is told from one made under this: a search continued under
    changed code would skip every state the old code had seen and report
    the new code's result from the old's."""
    digest = hashlib.sha256()
    for relative in code_files(root):
        digest.update(relative.encode("ascii"))
        digest.update(b"\0" + (root / relative).read_bytes() + b"\0")
    return digest.hexdigest()


def stamp_of(start, scope, depth):
    """What makes a search the one it is: its start, its scope, its depth
    and the code; a checkpoint is continued only by the search that wrote
    it."""
    return {"start": start.digest(), "scope": hashlib.sha256(repr(scope).encode("ascii")).hexdigest(),
            "depth": depth, "library": __version__, "code": code_digest()}


def write_checkpoint(path, stamp, seen, stack, found, transitions, seconds):
    """The search's whole state, so a restart continues it: written beside
    the path and moved into place, so a restart during the write finds the
    one before."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("wb") as file:
        pickle.dump({"stamp": stamp, "seen": seen, "stack": stack, "found": found, "transitions": transitions,
                     "seconds": seconds}, file, protocol=pickle.HIGHEST_PROTOCOL)
    partial.replace(path)


def read_checkpoint(path):
    with Path(path).open("rb") as file:
        return pickle.load(file)


def stamp_differences(kept, stamp):
    """Why a checkpoint is not this search's: each field of its stamp that
    differs, by name; none when it is."""
    return [name for name in stamp if kept.get("stamp", {}).get(name) != stamp[name]]


def search(start, scope, depth, known=oracle.AS_BUILT, budget=None, say=None, every=200000, checkpoint=None,
           checkpoint_every=CHECKPOINT_EVERY):
    """Every interleaving of the scope's verbs from start, to depth, depth
    first; stopped, and so incomplete, when a budget in seconds runs
    out. say, when given, hears a line of progress every `every`
    transitions. checkpoint, when given, is a file the search's state is
    written to every checkpoint_every seconds and continued from when the
    file is there, so a search of hours survives a restart of the
    machine; the search is the same one, since its order is fixed, and
    the budget and the seconds count from the first start. The file
    carries a stamp of the search that wrote it (stamp_of: the start, the
    scope, the depth, the library and the code), and a file another
    search wrote, or this one under other code, is left and the search
    starts over, since continuing it would skip every state the other
    had seen. The caller removes the file once it has kept the result. A
    model with a run between its halves holds a client and cannot be
    pickled, so the checkpoint is for the lane's spaces, none of which
    splits runs."""
    began = time.perf_counter()
    seen = {start.digest(): 0}
    stack = [(start, ())]
    found, transitions, before = {}, 0, 0.0
    stamp = stamp_of(start, scope, depth) if checkpoint is not None else None
    if checkpoint is not None and Path(checkpoint).exists():
        kept = read_checkpoint(checkpoint)
        differing = stamp_differences(kept, stamp)
        if differing:
            if say is not None:
                say(f"the checkpoint is another search's ({', '.join(differing)} differ), so this one starts over")
        else:
            seen, stack, found, transitions, before = (kept["seen"], kept["stack"], kept["found"],
                                                       kept["transitions"], kept["seconds"])
            if say is not None:
                say(f"continued from the checkpoint: {len(seen)} states, {transitions} transitions, {before:.0f} s")
    written = began
    stopped = False

    def elapsed():
        return before + time.perf_counter() - began

    while stack:
        if budget is not None and elapsed() > budget:
            stopped = True
            break
        if checkpoint is not None and time.perf_counter() - written > checkpoint_every:
            write_checkpoint(checkpoint, stamp, seen, stack, found, transitions, elapsed())
            written = time.perf_counter()
            if say is not None:
                say(f"checkpoint written: {len(seen)} states, {transitions} transitions, {elapsed():.0f} s")
        model, trace = stack.pop()
        here = len(trace)
        for verb in reversed(verbs.enabled(model, scope)):
            after = model.clone()
            outcome = verbs.apply(after, verb, scope)
            transitions += 1
            path = trace + (verb,)
            for problem in invariants.step_checks(model, after, verb, outcome, known):
                key = (problem.guarantee, problem.detail)
                first = found.get(key)
                found[key] = Found(problem.guarantee, problem.detail, first.trace if first else path,
                                   (first.count if first else 0) + 1)
            digest = after.digest()
            reached = seen.get(digest)
            if reached is None or reached > here + 1:
                seen[digest] = here + 1
                if here + 1 < depth:
                    stack.append((after, path))
            if say is not None and transitions % every == 0:
                say(f"{len(seen)} states, {transitions} transitions, {elapsed():.0f} s")
    if checkpoint is not None and stopped:
        write_checkpoint(checkpoint, stamp, seen, stack, found, transitions, elapsed())
    return Deep("", len(seen), transitions, depth, elapsed(), tuple(found.values()), stopped)


def search_space(space, known=oracle.AS_BUILT, budget=None, say=None, depth=None, checkpoint=None,
                 checkpoint_every=CHECKPOINT_EVERY):
    found = search(space.start(), space.scope, depth or space.depth, known, budget, say, checkpoint=checkpoint,
                   checkpoint_every=checkpoint_every)
    return found._replace(name=space.name)


# --- traces a person can replay ----------------------------------------------

def hours(minutes):
    return minutes // HOUR


def symbolic_trace(space, trace):
    """The trace with every released branch a verb names written
    symbolically, as run.symbolic writes it, so the trace can be shortened
    (shrink.py) with the clock moving under the names."""
    model, found = space.start(), []
    for verb in trace:
        verb = run.concrete(model, verb)
        found.append(verb._replace(args=run.symbolic(model, verb.args)))
        verbs.apply(model, verb, space.scope)
    return tuple(found)


def step_json(model, verb):
    """One verb as a step of a trace file, at the model's hour before it;
    the verb's released branches, if any, named symbolically."""
    name, actor, args = run.concrete(model, verb)
    at = hours(model.now)
    if name in ("board", "board-start"):
        event, ref_name = args[0], args[1] if len(args) > 1 else ""
        faults = list(args[2]) if len(args) > 2 else []
        return {"verb": name, "event": event, "ref": ref_name, "faults": faults, "at": at}
    if name == "board-finish":
        return {"verb": name, "at": at}
    if name == "wait":
        return {"verb": name, "args": [args[0]], "at": at}
    listed = [list(a) if isinstance(a, tuple) else a for a in run.symbolic(model, args)]
    return {"verb": name, "actor": actor, "args": listed, "at": at}


def trace_json(space, trace, violated="", title="", depth=None):
    """A counterexample as a trace file (replay.py's shape): the seeded
    repository, the first board run, the prefix and the trace, each step
    at its hour, with released branches named symbolically so the file
    plays in any world; depth is the search's, which --quick lowers, else
    the space's."""
    scenario = space.scenario()
    model = run.seeded(scenario)
    steps = [{"verb": "board", "event": cycle.SCHEDULE, "ref": "", "faults": [], "at": 0}]
    cycle.board_run(model)
    for verb in tuple(space.prefix) + tuple(trace):
        steps.append(step_json(model, verb))
        verbs.apply(model, run.concrete(model, verb), space.scope)
    return {
        "finding": "", "title": title or f"a counterexample of the deep lane's {space.name}",
        "status": "counterexample", "violated": violated, "breaks": [violated.split()[0]] if violated else [],
        "library": {"version": __version__},
        "scope": {"name": space.name, "depth": depth or space.depth, "workers": list(space.scope.workers),
                  "issues": list(space.scope.issues), "faults": list(space.scope.faults),
                  "waits": list(space.scope.waits), "skews": list(space.scope.skews)},
        "issues": {str(n): list(labels) for n, labels in scenario.issues.items()},
        "workers": list(scenario.workers), "people": list(scenario.people),
        "files": {str(n): list(paths) for n, paths in scenario.files},
        "careless": scenario.careless, "events": list(scenario.events), "split_board": scenario.split_board,
        "world": "memory", "steps": steps,
    }


def write_trace(folder, name, data):
    folder = Path(folder) / TRACES
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=True) + "\n", encoding="ascii")
    return path


def write_counterexamples(folder, space, found, known=oracle.AS_BUILT, budget=60.0, depth=None, total=None):
    """Each violation of a search, shortened and written as a trace file;
    the files' paths with what each holds. budget bounds the shortening
    of one violation and total, when given, of them all, so a search that
    met many cannot spend the hours after it shortening them: a violation
    the total has no time left for is written as the search met it."""
    written = []
    ends = None if total is None else time.perf_counter() + total
    for at, violation in enumerate(sorted(found, key=lambda f: (f.guarantee, f.detail)), 1):
        target = (violation.guarantee, violation.detail)
        allowed = budget if ends is None else max(0.0, min(budget, ends - time.perf_counter()))
        shorter = shrink.shrink(space.start, space.scope, symbolic_trace(space, violation.trace), target, known, allowed)
        data = trace_json(space, shorter.trace, f"{violation.guarantee} {violation.detail}", depth=depth)
        path = write_trace(folder, f"{violation.guarantee}-{space.name}-{at}", data)
        written.append({"guarantee": violation.guarantee, "detail": violation.detail, "count": violation.count,
                        "trace": [str(v) for v in shorter.trace], "found_at": len(violation.trace),
                        "shortened_to": len(shorter.trace), "file": str(path)})
    return written


# --- the git world ------------------------------------------------------------

def scenarios_over_git(known_reading=oracle.AS_BUILT):
    """Every scenario played over the git world, as built: its mismatches,
    and its red results against known.json, which records the memory
    world's; a difference between the worlds is a finding."""
    results = []
    for name, scenario in scenarios.SCENARIOS.items():
        began = time.perf_counter()
        record = run.run_scenario(scenario, known_reading, run.GIT)
        red = sorted(replay.results(record))
        results.append({"scenario": name, "seconds": round(time.perf_counter() - began, 2), "checks": record.checks,
                        "mismatches": [m for _, m in record.mismatches], "red": [list(r) for r in red],
                        "differences": known.differences("scenario", name, set(red))})
    return results


def traces_over_git(known_reading=oracle.AS_BUILT):
    results = []
    for finding, trace in replay.traces().items():
        began = time.perf_counter()
        record = run.play(trace.scenario, trace.scenario.steps, known_reading, run.GIT)[1]
        red = sorted(replay.results(record))
        results.append({"trace": finding, "seconds": round(time.perf_counter() - began, 2), "checks": record.checks,
                        "mismatches": [m for _, m in record.mismatches], "red": [list(r) for r in red],
                        "differences": known.differences("trace", finding, set(red))})
    return results


def guard_recorded_again(scratch):
    """Git's answers for the guard grammar recorded again into scratch and
    compared with the file the fast lane judges the guard by: the lists
    git answers differently now, and the lists of the file the grammar no
    longer makes."""
    scratch = Path(scratch)
    began = time.perf_counter()
    count = guard_grammar.record(scratch)
    fresh = json.loads(scratch.read_text(encoding="ascii"))
    kept = json.loads(guard_grammar.ANSWERS.read_text(encoding="ascii"))
    differing = sorted(argv for argv, answer in fresh["answers"].items() if kept["answers"].get(argv) != answer)
    missing = sorted(set(kept["answers"]) - set(fresh["answers"]))
    return {"recorded": count, "seconds": round(time.perf_counter() - began, 1), "git": fresh["git"],
            "recorded_git": kept["git"], "differing": differing, "missing": missing}


# --- conformance ----------------------------------------------------------------

def observe(model, outcomes):
    """What a step left that both worlds must agree on: whether the step
    was taken and, when not, whether the guard or git refused it (git's
    words differ from the model's), the refs by name with a released name's
    sha replaced, each claim branch's tree and standing, the issues'
    labels and states, the pull requests, the bot's comments by kind, and
    the board's sections. The shas themselves differ between the worlds,
    so none is compared."""
    shown = model.board_text
    named = {}
    for ref in sorted(model.refs):
        key = ref
        if ref.startswith(RELEASED_PREFIX):
            key = f"{RELEASED_PREFIX}{ref[len(RELEASED_PREFIX):].split('-')[0]}-*"
        named[key] = named.get(key, 0) + 1
    trees = {ref: tuple(sorted(model.tree(sha).items())) for ref, sha in sorted(model.refs.items())
             if not ref.startswith(RELEASED_PREFIX)}
    return {
        "taken": [(o.taken, o.refused_by_guard is not None) for o in outcomes],
        "refs": named,
        "trees": trees,
        "standing": {ref: model.standing(ref) for ref in sorted(model.refs) if ref.startswith(oracle.CLAIM_PREFIX)},
        "issues": {n: (i.state, tuple(sorted(i.labels))) for n, i in sorted(model.issues.items())},
        "pulls": {n: (p.head, p.draft, p.state) for n, p in sorted(model.pulls.items())},
        "comments": {n: tuple(sorted(Counter(oracle.comment_kind(c.body) for c in model.comments_on(n)
                                             if c.author == BOT).items()))
                     for n in sorted(set(model.issues) | set(model.pulls))},
        "page": (page.queue(shown), tuple(sorted(page.given(shown).items())), tuple(sorted(page.claim_workers(shown).items())),
                 page.not_claims(shown), tuple(sorted(page.overlaps(shown).items())), page.merged(shown), page.pulls(shown)),
    }


def scenario_of_path(space, trace):
    """The space's start and a path of verbs as a scenario both worlds
    play, with the board run and the prefix first."""
    data = trace_json(space, trace)
    return replay.scenario_of(data)


def compare(space, trace, known_reading=oracle.AS_BUILT):
    """The path played in both worlds; the steps and fields where they
    differ, empty when they agree."""
    scenario = scenario_of_path(space, trace)
    memory = run.play(scenario, scenario.steps, known_reading, run.MEMORY, observe)[1]
    git = run.play(scenario, scenario.steps, known_reading, run.GIT, observe)[1]
    differences = []
    for at, (one, two) in enumerate(zip(memory.observations, git.observations)):
        for field in one:
            if one[field] != two[field]:
                differences.append({"step": at, "field": field, "memory": repr(one[field]), "git": repr(two[field])})
    red_memory, red_git = sorted(replay.results(memory)), sorted(replay.results(git))
    if red_memory != red_git:
        differences.append({"step": -1, "field": "red", "memory": repr(red_memory), "git": repr(red_git)})
    return differences


def sample_paths(space, count, length, seed=0):
    """Random walks of the scope's enabled verbs from the space's start,
    one per seed from seed on, each as far as length or until nothing is
    enabled."""
    paths = []
    for n in range(count):
        rng = random.Random(seed + n)
        model, path = space.start(), []
        for _ in range(length):
            offered = verbs.enabled(model, space.scope)
            if not offered:
                break
            verb = rng.choice(offered)
            model = model.clone()
            verbs.apply(model, verb, space.scope)
            path.append(verb)
        paths.append(tuple(path))
    return paths


def conformance(spaces, counterexamples=(), count=200, length=8, seed=0):
    """Every counterexample file given and `count` sampled paths over the
    spaces, each played in both worlds and compared; what differs."""
    began = time.perf_counter()
    results = {"paths": 0, "counterexamples": 0, "disagreements": [], "seconds": 0.0}
    for file in counterexamples:
        trace = replay.load(file)
        memory = run.play(trace.scenario, trace.scenario.steps, oracle.AS_BUILT, run.MEMORY, observe)[1]
        git = run.play(trace.scenario, trace.scenario.steps, oracle.AS_BUILT, run.GIT, observe)[1]
        results["counterexamples"] += 1
        for at, (one, two) in enumerate(zip(memory.observations, git.observations)):
            for field in one:
                if one[field] != two[field]:
                    results["disagreements"].append({"file": str(file), "step": at, "field": field,
                                                     "memory": repr(one[field]), "git": repr(two[field])})
    spaces = list(spaces)
    share, left = divmod(count, len(spaces)) if spaces else (0, 0)
    for n, space in enumerate(spaces):
        for path in sample_paths(space, share + (n < left), length, seed + 1000 * n):
            results["paths"] += 1
            for difference in compare(space, path):
                results["disagreements"].append({"space": space.name, "path": [str(v) for v in path], **difference})
    results["seconds"] = round(time.perf_counter() - began, 1)
    return results
