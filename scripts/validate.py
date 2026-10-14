#!/usr/bin/env python3
"""Run the validation harness's lanes by hand (validation/README.md).

    python3 scripts/validate.py scenarios     the five scenarios, with their timelines
    python3 scripts/validate.py traces        the regression traces, one per finding, against known.json
    python3 scripts/validate.py replay <file> one trace file (validation/traces/F1.json), with its timeline
        ... --world git                       the same, over the git world (scenarios, traces and replay)
    python3 scripts/validate.py explore       the fast lane's searches, with their counts
    python3 scripts/validate.py guard         the guard against git's recorded answers
    python3 scripts/validate.py guard --record  run git first and write the answers
    python3 scripts/validate.py deal          the dealing lane
    python3 scripts/validate.py properties    the property lane
    python3 scripts/validate.py fast          every lane above but the recording
    python3 scripts/validate.py mutants       the mutation lane (minutes)
    python3 scripts/validate.py deep          the deep lane (hours): the searches, the soak, the gate,
                                              the git world, the guard recorded again, conformance
        --out DIR                             where the results and the counterexamples go (validation-out)
        --only search,soak,gate,git,guard,conformance   some parts only
        --jobs N                              processes at once (the processors)
        --budget SECONDS                      stop each search there; a stopped search is red
        --quick                               a few steps of every part, for a look

The tests under tests/ run the same lanes with their budgets; this script
prints what they measure. Exit code 1 when a lane finds something.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cycle, dealing, explore, guard_grammar, known, lanes, model as M, mutants, oracle  # noqa: E402
from validation import properties, replay, run, scenarios, verbs  # noqa: E402

LANES = ("scenarios", "traces", "explore", "guard", "deal", "properties")


def run_scenarios(out, world=run.MEMORY):
    status = 0
    for name, scenario in scenarios.SCENARIOS.items():
        record = run.run_scenario(scenario, world=world)
        shows = sorted({step.shows for step in scenario.steps if step.shows})
        print(f"{name}: {record.checks} checks, {len(record.red_steps)} red steps, {len(record.mismatches)} "
              f"mismatches{', shows ' + ' '.join(shows) if shows else ''}", file=out)
        for line in record.timeline:
            if "mismatch" in line or "red" in line:
                print("  " + line, file=out)
        if record.mismatches:
            status = 1
    return status


def run_traces(out, world=run.MEMORY):
    """Every trace as built and with each switch of the oracle off, against
    what known.json records; a difference is printed and fails the lane."""
    status = 0
    for finding, trace in replay.traces().items():
        for switched_off in (None, *sorted(known.AS_BUILT)):
            reading = oracle.AS_BUILT if switched_off is None else oracle.AS_BUILT - {switched_off}
            record = replay.replay(trace, reading, world)
            wrong = known.differences("trace", finding, replay.results(record), switched_off)
            wrong += [f"trace {finding}: mismatch, {m}" for _, m in record.mismatches]
            if switched_off is None:
                red = ", ".join(f"step {s} {g}" for s, g in sorted(replay.results(record))) or "none"
                print(f"{finding} ({trace.status}): red as built: {red}", file=out)
            for line in wrong:
                print("  " + line, file=out)
            status |= bool(wrong)
    return status


def run_replay(out, path, world=run.MEMORY):
    trace = replay.load(path)
    record = replay.replay(trace, world=world)
    print(replay.report(trace, record), file=out)
    wrong = known.differences("trace", trace.finding, replay.results(record)) if trace.path.parent == replay.TRACES else []
    for line in wrong:
        print("  " + line, file=out)
    return 1 if wrong or record.mismatches else 0


def run_explore(out):
    status = 0
    searches = [("E-fast, 2 workers, 1 issue, depth 5", lambda: (fresh(), verbs.Scope(), 5)),
                ("E-three, 3 workers, 1 issue, depth 4", lambda: (fresh(), verbs.Scope(workers=("a", "b", "c")), 4))]
    for name, upto in (("S02", 6), ("S03", 12), ("S04", 6), ("S05a", 23)):
        scenario = scenarios.SCENARIOS[name]
        searches.append((f"E-prefix, {name} after step {upto}, depth 3",
                         lambda s=scenario, u=upto: (run.prefix(s, u), run.scope_of(s), 3)))
    for name, make in searches:
        model, scope, depth = make()
        found = explore.explore(model, scope, depth)
        print(f"{name}: {found.states} states, {found.transitions} transitions, {found.seconds:.1f} s, "
              f"{len(found.found)} violations", file=out)
        for f in found.found[:5]:
            print(f"  {f.guarantee}: {f.detail}; {', '.join(f.trace)}", file=out)
        if found.found:
            status = 1
    return status


def fresh():
    model = M.seeded()
    cycle.board_run(model)
    return model


def run_guard(out, record=False):
    if record:
        print(f"recorded {guard_grammar.record()} argument lists", file=out)
    judgement = guard_grammar.judge()
    print(guard_grammar.report(judgement), file=out)
    return 1 if judgement.failures or judgement.missing else 0


def run_deal(out):
    lane = dealing.run_lane()
    print(dealing.report(lane), file=out)
    return 1 if lane.doubled_from_one_look else 0


def run_properties(out):
    stale = properties.stale_unproven()
    outcomes = properties.run_lane()
    print(properties.report(outcomes), file=out)
    if stale:
        print(f"stale unproven entries: {stale}", file=out)
    return 1 if stale or any(o.failures or not o.checked for o in outcomes) else 0


RUNNERS = {"scenarios": run_scenarios, "traces": run_traces, "explore": run_explore, "guard": run_guard,
           "deal": run_deal, "properties": run_properties}


def option(options, name, default=None):
    """The value after --name in the options, or default; a flag with no
    value answers True."""
    if name not in options:
        return default
    at = options.index(name)
    if at + 1 < len(options) and not options[at + 1].startswith("--"):
        return options[at + 1]
    return True


def world_of(options):
    world = option(options, "--world", run.MEMORY)
    if world not in run.WORLDS:
        raise SystemExit(f"--world takes {' or '.join(run.WORLDS)}, not {world}")
    return world


def run_deep(out, options):
    only = option(options, "--only", "")
    jobs = option(options, "--jobs")
    budget = option(options, "--budget")
    settings = lanes.Settings(option(options, "--out", str(lanes.deep.OUT)), [p for p in only.split(",") if p],
                             int(jobs) if jobs else None, float(budget) if budget else None, "--quick" in options)
    return lanes.run(settings, out)


def main(argv=None, out=sys.stdout):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in LANES + ("fast", "mutants", "replay", "deep") or (args[0] == "replay" and len(args) < 2):
        print(__doc__, file=out)
        return 2
    lane, options = args[0], args[1:]
    if lane == "mutants":
        return mutants.main([])
    if lane == "deep":
        return run_deep(out, options)
    if lane == "replay":
        return run_replay(out, options[0], world_of(options))
    lanes_to_run = LANES if lane == "fast" else (lane,)
    status, began = 0, time.perf_counter()
    for name in lanes_to_run:
        print(f"--- {name}", file=out)
        started = time.perf_counter()
        if name == "guard" and "--record" in options:
            status |= run_guard(out, record=True)
        elif name in ("scenarios", "traces"):
            status |= RUNNERS[name](out, world_of(options))
        else:
            status |= RUNNERS[name](out)
        print(f"    {time.perf_counter() - started:.1f} s", file=out)
    print(f"{'fast lane' if lane == 'fast' else lane}: {'found something' if status else 'ok'} in "
          f"{time.perf_counter() - began:.1f} s", file=out)
    return status


if __name__ == "__main__":
    sys.exit(main())
