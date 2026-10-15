"""One replayable regression trace per finding (DESIGN.md, 5.3 and 14, step 5).

Each file under validation/traces/, <finding>.json, is the trace of
DESIGN.md's table in 5.3 for one finding F1 to F19: the repository it
starts from (its issues with their labels, its workers, the people beside
them, the files each claim lists, whether the workers are careless, the
events the board hears and whether a run is made in two halves) and its
steps, each a verb by an actor at an hour, with what the spec says the
state is afterwards, in the shape scenarios.py gives a step (Expect).
replay() plays one through run.play, so every guarantee is checked after
every step as a scenario's are, and the record holds the violations, the
mismatches and a timeline a person reads; scripts/validate.py replay
<file> prints it.

A trace of a fixed finding (F1, F3, F5, F6, F10 and F11 in 0.4.1; F2,
F4, F7, F9, F12, F13, F14, F15, F17, F18 and F19 after it; F16 in the
spec's words, which the library already kept) is green, and the mutant
that undoes its fix turns it red at the step where the finding showed,
for the guarantee the table names, which tests/test_validation_traces.py
holds it to. A trace of an open finding is red where known.json records
it, with the finding's switch off in the oracle for the ones the oracle
reads as built; none is left since F2's fix, and none is red as built, as
F4's was until its fix.
F8's trace breaks nothing and records the assumption under which
G2 and G3 hold, that idleness is the committer date the pusher sets.

The file's steps are objects: "verb" (a verb of verbs.py, or "announce",
the Announce verbs, claimed and the worker's label on, each other worker's
label the step's second argument lists off, and the plan; "guard" and
"deal", the two steps run.py plays without the model), "actor" (the board's and the clock's need none),
"args" as a list, "at", the hour, kept from the step before when left
out, "skew", hours added to a claim's or a commit's committer date,
"event", "ref" and "faults" for a board verb, "expect" in Expect's shape
with lists for its sets and numbers for its keys where JSON has strings,
"shows", the finding the step is red for, and "note" and the file's
"world", read by nobody. A step is numbered, in known.json, in the tests
and in a replay's report, by its place among the steps as played, from
zero, where an "announce" counts as each of its verbs. Every file is ASCII:
a character outside it is written as a \\u escape.
"""

import json
from pathlib import Path
from typing import NamedTuple

from . import cycle, run
from .known import AS_BUILT
from .model import HOUR, OWNER
from .scenarios import Expect, Scenario, Step

TRACES = Path(__file__).resolve().parent / "traces"
FINDINGS = tuple(f"F{n}" for n in range(1, 20))
SET_FIELDS = ("labels", "told")
BOARD_VERBS = ("board", "board-start", "board-finish")


class Trace(NamedTuple):
    finding: str
    title: str
    status: str
    breaks: tuple  # the guarantees DESIGN.md's table says the finding breaks
    scenario: Scenario
    path: Path


def numbered(mapping):
    """JSON's string keys as the numbers Expect's fields are keyed by, where
    they are numbers."""
    return {int(k) if isinstance(k, str) and k.isascii() and k.isdigit() else k: v for k, v in mapping.items()}


def expectation(raw):
    fields = {}
    for name, value in (raw or {}).items():
        if isinstance(value, dict):
            value = numbered(value)
            if name in SET_FIELDS:
                value = {k: set(v) for k, v in value.items()}
        fields[name] = value
    return Expect(**fields)


def arguments(raw, skew=0):
    args = tuple(tuple(a) if isinstance(a, list) else a for a in raw.get("args", ()))
    return args + ((skew * HOUR,) if skew else ())


def steps_of(raw_steps):
    steps, at = [], 0
    for raw in raw_steps:
        at = raw.get("at", at)
        verb, actor = raw["verb"], raw.get("actor", "board" if raw["verb"] in BOARD_VERBS else "clock")
        expect, shows = expectation(raw.get("expect")), raw.get("shows", "")
        if verb == "announce":
            number, off = raw["args"][0], raw["args"][1] if len(raw["args"]) > 1 else ()
            steps.append(Step(at, actor, "label-on", (number, "claimed")))
            steps.append(Step(at, actor, "label-on", (number, f"worker:{actor}")))
            for label in off:
                steps.append(Step(at, actor, "label-off", (number, label)))
            steps.append(Step(at, actor, "plan", (number,), expect, shows))
        elif verb in ("board", "board-start"):
            args = (raw.get("event", cycle.SCHEDULE), raw.get("ref", ""), tuple(raw.get("faults", ())))
            steps.append(Step(at, actor, verb, args, expect, shows))
        else:
            steps.append(Step(at, actor, verb, arguments(raw, raw.get("skew", 0)), expect, shows))
    return tuple(steps)


def scenario_of(data):
    issues = {int(n): tuple(labels) for n, labels in data["issues"].items()}
    return Scenario(data["finding"], data["title"], tuple(data.get("breaks", ())), issues,
                    {w: None for w in data["workers"]}, steps_of(data["steps"]), tuple(data.get("people", (OWNER,))),
                    tuple((int(n), tuple(paths)) for n, paths in data.get("files", {}).items()),
                    data.get("careless", False), tuple(data.get("events", (cycle.SCHEDULE,))),
                    data.get("split_board", False), data.get("opaque_pulls", False),
                    data.get("lenient_rules", False))


def load(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="ascii"))
    return Trace(data["finding"], data["title"], data["status"], tuple(data.get("breaks", ())), scenario_of(data), path)


def traces(folder=TRACES):
    paths = sorted(folder.glob("F*.json"), key=lambda p: int(p.stem[1:]))
    return {trace.finding: trace for trace in (load(p) for p in paths)}


def replay(trace, known=AS_BUILT, world=run.MEMORY):
    return run.play(trace.scenario, trace.scenario.steps, known, world)[1]


def results(record):
    """The red results of a record, as (step, guarantee)."""
    return {(at, v.guarantee) for at, v in record.violations}


def report(trace, record):
    red = ", ".join(f"step {s} {g}" for s, g in sorted(results(record))) or "none"
    lines = [f"{trace.finding} ({trace.status}): {trace.title}", f"  red: {red}; mismatches: {len(record.mismatches)}"]
    return "\n".join(lines + ["  " + line for line in record.timeline])
