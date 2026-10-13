#!/usr/bin/env python3
"""Run the validation harness's lanes by hand (validation/README.md).

    python3 scripts/validate.py scenarios     the five scenarios, with their timelines
    python3 scripts/validate.py explore       the fast lane's searches, with their counts
    python3 scripts/validate.py guard         the guard against git's recorded answers
    python3 scripts/validate.py guard --record  run git first and write the answers
    python3 scripts/validate.py deal          the dealing lane
    python3 scripts/validate.py properties    the property lane
    python3 scripts/validate.py fast          every lane above but the recording
    python3 scripts/validate.py mutants       the mutation lane (minutes)

The tests under tests/ run the same lanes with their budgets; this script
prints what they measure. Exit code 1 when a lane finds something.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cycle, dealing, explore, guard_grammar, model as M, mutants, properties, run, scenarios  # noqa: E402
from validation import verbs  # noqa: E402

LANES = ("scenarios", "explore", "guard", "deal", "properties")


def run_scenarios(out):
    status = 0
    for name, scenario in scenarios.SCENARIOS.items():
        record = run.run_scenario(scenario)
        shows = sorted({step.shows for step in scenario.steps if step.shows})
        print(f"{name}: {record.checks} checks, {len(record.red_steps)} red steps, {len(record.mismatches)} "
              f"mismatches{', shows ' + ' '.join(shows) if shows else ''}", file=out)
        for line in record.timeline:
            if "mismatch" in line or "red" in line:
                print("  " + line, file=out)
        if record.mismatches:
            status = 1
    return status


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


RUNNERS = {"scenarios": run_scenarios, "explore": run_explore, "guard": run_guard, "deal": run_deal,
           "properties": run_properties}


def main(argv=None, out=sys.stdout):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in LANES + ("fast", "mutants"):
        print(__doc__, file=out)
        return 2
    lane, options = args[0], args[1:]
    if lane == "mutants":
        return mutants.main([])
    lanes = LANES if lane == "fast" else (lane,)
    status, began = 0, time.perf_counter()
    for name in lanes:
        print(f"--- {name}", file=out)
        started = time.perf_counter()
        status |= RUNNERS[name](out, record=True) if name == "guard" and "--record" in options else RUNNERS[name](out)
        print(f"    {time.perf_counter() - started:.1f} s", file=out)
    print(f"{'fast lane' if lane == 'fast' else lane}: {'found something' if status else 'ok'} in "
          f"{time.perf_counter() - began:.1f} s", file=out)
    return status


if __name__ == "__main__":
    sys.exit(main())
