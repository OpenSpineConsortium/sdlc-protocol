"""Shortening a failing trace (DESIGN.md, 6.1): delta debugging.

A depth-first search records the first trace it meets a violation on,
which is seldom a shortest one, and a soak walk is a hundred steps or
more before its violation; shrink takes the trace and the violation and
removes steps while the violation still shows. It removes chunks first,
as ddmin does (Zeller and Hildebrandt, 2002): the trace split in two,
each half tried without; when neither half can go, in four, and so on,
each success resetting the split one coarser, down to single steps, so a
walk of two hundred steps loses its idle stretches in a few replays
instead of one step per replay; the last pass tries every single step,
so the result is 1-minimal, as before, when the budget lets the passes
finish, and minimal for the detail as matched, a sha in it read as any
sha (invariants.identity), so a step that only moves a number but a sha
in the detail stays. A candidate is replayed from the search's own start, each verb taken only where the scope enables it, so
the shortened trace is one the search could have met, and the violation
it shows is the same guarantee with the same detail. The replay is the
search's own step: a copy per verb, the verb applied, every check run.
"""

import time
from typing import NamedTuple

from . import invariants, oracle, run, verbs


class Shrunk(NamedTuple):
    trace: tuple  # the verbs, as Verb records
    tried: int
    seconds: float


def reproduces(start, scope, trace, target, known=oracle.AS_BUILT):
    """Whether the trace, replayed from start, meets the violation target,
    as (guarantee, detail), at any step; a verb the scope does not enable
    where it comes ends the replay as not reproduced. A verb that names a
    released branch symbolically (run.symbolic) is resolved where it
    comes, since the sha in such a name is the clock's and the clock
    moves when a wait is taken out, and the target's detail is matched
    with its sha read as any sha for the same reason."""
    wanted = (target[0], invariants.identity(target[1]))
    model = start()
    for verb in trace:
        try:
            verb = run.concrete(model, verb)
        except (ValueError, IndexError):
            return False
        if verb not in verbs.enabled(model, scope):
            return False
        after = model.clone()
        outcome = verbs.apply(after, verb, scope)
        if any((p.guarantee, invariants.identity(p.detail)) == wanted
               for p in invariants.step_checks(model, after, verb, outcome, known)):
            return True
        model = after
    return False


def chunks(length, count):
    """The bounds of a trace of the length split into count chunks, as
    even as they can be, as (from, to) pairs."""
    size, extra = divmod(length, count)
    bounds, at = [], 0
    for n in range(count):
        end = at + size + (1 if n < extra else 0)
        bounds.append((at, end))
        at = end
    return bounds


# The widths of the windows of neighbouring steps tried after no single
# step can go: an act and the board run its event queued, which the run
# alone or the act alone cannot lose, since a run is offered only while
# it is queued and nothing else is offered then (verbs.enabled); a merge
# queues two.
WINDOWS = (2, 3)


def by_chunks(trace, fits, deadline):
    """ddmin's complement step: the trace without each chunk, the split
    one finer each time no chunk can go, down to single steps."""
    count = 2
    while len(trace) >= 2 and time.perf_counter() < deadline:
        removed = False
        for low, high in chunks(len(trace), count):
            if time.perf_counter() >= deadline:
                return trace
            candidate = trace[:low] + trace[high:]
            if fits(candidate):
                trace, count, removed = candidate, max(count - 1, 2), True
                break
        if not removed:
            if count >= len(trace):
                break
            count = min(2 * count, len(trace))
    return trace


def by_windows(trace, fits, deadline, widths=WINDOWS):
    """The trace without each window of neighbouring steps, the narrow
    windows first; the first that can go is taken out, and the caller
    starts over."""
    for width in widths:
        for at in range(len(trace) - width + 1):
            if time.perf_counter() >= deadline:
                return trace
            candidate = trace[:at] + trace[at + width:]
            if fits(candidate):
                return candidate
    return trace


def shrink(start, scope, trace, target, known=oracle.AS_BUILT, budget=60.0):
    """The trace with every chunk, then every step, then every window of
    neighbouring steps taken out that can be, over again until nothing
    can, within the budget in seconds; start makes a fresh starting state
    each time it is called."""
    began, tried = time.perf_counter(), 0
    deadline = began + budget
    trace = tuple(trace)

    def fits(candidate):
        nonlocal tried
        tried += 1
        return reproduces(start, scope, candidate, target, known)

    while time.perf_counter() < deadline:
        trace = by_chunks(trace, fits, deadline)
        narrower = by_windows(trace, fits, deadline)
        if len(narrower) == len(trace):
            break
        trace = narrower
    return Shrunk(trace, tried, time.perf_counter() - began)
