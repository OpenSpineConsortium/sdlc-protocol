"""Shortening a failing trace (DESIGN.md, 6.1): delta debugging.

A depth-first search records the first trace it meets a violation on,
which is seldom a shortest one; shrink takes the trace and the violation
and removes steps while the violation still shows, until no single step
can go. A candidate is replayed from the search's own start, each verb
taken only where the scope enables it, so the shortened trace is one the
search could have met, and the violation it shows is the same guarantee
with the same detail. The replay is the search's own step: a copy per
verb, the verb applied, every check run.
"""

import time
from typing import NamedTuple

from . import invariants, oracle, verbs


class Shrunk(NamedTuple):
    trace: tuple  # the verbs, as Verb records
    tried: int
    seconds: float


def reproduces(start, scope, trace, target, known=oracle.AS_BUILT):
    """Whether the trace, replayed from start, meets the violation target,
    as (guarantee, detail), at any step; a verb the scope does not enable
    where it comes ends the replay as not reproduced."""
    model = start()
    for verb in trace:
        if verb not in verbs.enabled(model, scope):
            return False
        after = model.clone()
        outcome = verbs.apply(after, verb, scope)
        if any((p.guarantee, p.detail) == target for p in invariants.step_checks(model, after, verb, outcome, known)):
            return True
        model = after
    return False


def shrink(start, scope, trace, target, known=oracle.AS_BUILT, budget=60.0):
    """The trace with every step taken out that can be, one at a time
    until none can, within the budget in seconds; start makes a fresh
    starting state each time it is called."""
    began, tried = time.perf_counter(), 0
    trace = tuple(trace)
    changed = True
    while changed and time.perf_counter() - began < budget:
        changed = False
        for at in range(len(trace)):
            candidate = trace[:at] + trace[at + 1:]
            tried += 1
            if reproduces(start, scope, candidate, target, known):
                trace, changed = candidate, True
                break
    return Shrunk(trace, tried, time.perf_counter() - began)
