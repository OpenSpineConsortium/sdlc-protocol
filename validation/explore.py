"""The breadth-first checker, in its first form (DESIGN.md, 6.1 and 6.5).

explore applies every verb a scope enables, to a copy of each state it
reaches, up to a depth; checks every guarantee on every step; and
deduplicates states by RepoModel.canonical(), the sound key. Breadth first,
the first trace that breaks a guarantee is a shortest one.

No pruning at a violation: the prototype stopped at every violating state,
so nothing past a known finding was ever explored. Here a violation is
recorded with its trace and the search goes on from that state, so a known
red neither stops the search nor hides a second fault behind it. Each
distinct (guarantee, detail) is kept once, with its first and so shortest
trace, and counted.

The deep scopes, the reductions of section 6.4 and the replay files of
section 6.6 come with the fast and deep lanes (build steps 4 and 6).
"""

import time
from collections import deque
from typing import NamedTuple

from . import invariants, oracle, verbs


class Found(NamedTuple):
    guarantee: str
    detail: str
    trace: tuple
    count: int


class Exploration(NamedTuple):
    states: int
    transitions: int
    depth: int
    seconds: float
    found: tuple

    def guarantees(self):
        return sorted({f.guarantee for f in self.found})


def explore(start, scope, depth, known=oracle.AS_BUILT):
    """Every interleaving of the scope's verbs from start, to depth."""
    began = time.perf_counter()
    seen = {start.canonical()}
    frontier = deque([(start, ())])
    found, transitions = {}, 0
    while frontier:
        model, trace = frontier.popleft()
        if len(trace) >= depth:
            continue
        for verb in verbs.enabled(model, scope):
            after = model.clone()
            outcome = verbs.apply(after, verb, scope)
            transitions += 1
            path = trace + (str(verb),)
            for problem in invariants.step_checks(model, after, verb, outcome, known):
                first = found.get(problem)
                found[problem] = Found(problem.guarantee, problem.detail, first.trace if first else path,
                                       (first.count if first else 0) + 1)
            key = after.canonical()
            if key not in seen:
                seen.add(key)
                frontier.append((after, path))
    return Exploration(len(seen), transitions, depth, time.perf_counter() - began, tuple(found.values()))
