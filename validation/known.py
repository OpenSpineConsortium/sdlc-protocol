"""validation/known.json: the results that are red on purpose (DESIGN.md, 5.1).

The file has two parts. "as_built" names the open findings the oracle
reads as the library is built (oracle.AS_BUILT took them from here from
build step 5 on): with such a finding's switch on, the oracle reads its
rule as built and nothing is red; with the switch off, the oracle reads
the spec and the finding shows. "red" lists each result that is red on
purpose: a step of a regression trace (validation/traces/<finding>.json,
played by replay.py) or of a scenario (scenarios.py), numbered from zero
by its place among the steps as played, an "announce" of a trace counting
as its three verbs, with the guarantee it breaks and the finding that
breaks it. An entry for a finding in
"as_built" is red when that finding's switch is off and green as built;
an entry for any other finding is red as built, since no rule of the
library answers it, as F4's (dealing from differing looks) and F7's (the
careless lease) were until their fixes removed them; none is left.

The suite passes when every result is what the file records
(tests/test_validation_traces.py, tests/test_validation_scenarios.py):
a red result the file does not list fails, and so does a listed result
that is green, so a fix removes its entries in the same pull request, and
a finding's switch leaves "as_built" with them. Nothing is skipped.
"""

import json
from pathlib import Path
from typing import NamedTuple

KNOWN = Path(__file__).resolve().parent / "known.json"
KINDS = ("trace", "scenario")


class Entry(NamedTuple):
    kind: str  # "trace" or "scenario"
    name: str  # the finding's id, or the scenario's name
    step: int  # the index of the step that is red
    guarantee: str
    finding: str


def read(path=KNOWN):
    data = json.loads(path.read_text(encoding="ascii"))
    entries = []
    for raw in data["red"]:
        kind = next(k for k in KINDS if k in raw)
        entries.append(Entry(kind, raw[kind], raw["step"], raw["guarantee"], raw["finding"]))
    return frozenset(data["as_built"]), tuple(entries)


AS_BUILT, ENTRIES = read()


def entries_of(kind, name):
    return [e for e in ENTRIES if e.kind == kind and e.name == name]


def findings_named(kind, name):
    """The findings the file names on the trace or scenario, in order."""
    return sorted({e.finding for e in entries_of(kind, name)})


def expected(kind, name, switched_off=None):
    """The results, as (step, guarantee), that must be red on the trace or
    scenario when the oracle reads the spec for switched_off (None: as
    built): the entries of findings no switch covers, and the entries of
    the finding switched off."""
    return {(e.step, e.guarantee) for e in entries_of(kind, name)
            if e.finding not in AS_BUILT or e.finding == switched_off}


def differences(kind, name, found, switched_off=None):
    """What differs between the results found, as (step, guarantee), and
    what the file records, one line each; empty when they agree."""
    wanted = expected(kind, name, switched_off)
    reading = f"with {switched_off} read from the spec" if switched_off else "as built"
    lines = [f"{kind} {name} {reading}: red and not recorded: step {s}, {g}" for s, g in sorted(set(found) - wanted)]
    lines += [f"{kind} {name} {reading}: recorded red and green: step {s}, {g}" for s, g in sorted(wanted - set(found))]
    return lines
