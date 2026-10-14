"""The soak (DESIGN.md, 6.8): long seeded random walks with many workers.

A search reaches every state within a few steps; the soak reaches the
states that take a hundred: five workers on six issues over days of the
scenario clock, claims released and claimed again, released heads
pushed again, branches renamed and renamed back, main moving under open pull requests,
every fault kind, the owner releasing by hand, a stranger posting
markers. Each walk is driven by a seed: every worker gets a scripted
policy (policies.py) drawn by the seed, and at each step the seed picks
who acts, a worker by its policy or by a verb of its own, a person, the
clock or the board, so a walk replays exactly from its seed, and the
scope is verbs.enabled's, so every verb taken is one the search could
have taken. Every guarantee is checked after every verb
(invariants.step_checks); a violation is kept once, at the step it first
shows, with the whole walk up to it, which the deep lane shortens
(shrink.py) and writes as a trace file: a wrong cell or an open pull
request that stays wrong is the same fault at every board run after it,
and the search keeps it once too. The walks are independent, so the lane
runs them over several processes.
"""

import random
import time
from collections import Counter
from typing import NamedTuple

from . import cycle, deep, invariants, oracle, policies, verbs
from .model import OWNER
from .verbs import CLOCK, DANA, SAM, Scope

WORKERS = ("a", "b", "c", "d", "e")
ISSUES = (1, 2, 3, 4, 5, 6)
SHARED = ("src/shared.py",)
# Two issues share a file, two more share another, two are alone.
FILES = ((1, SHARED), (2, SHARED), (3, ("src/three.py",)), (4, ("src/three.py",)), (5, ("src/5.py",)),
         (6, ("src/6.py",)))
WAITS = (1, 2, 3, 6, 12, 23, 24, 25, 30)
SCOPE = Scope(workers=WORKERS, issues=ISSUES, people=(OWNER, DANA, SAM), faults=tuple(verbs.FAULTS), waits=WAITS,
              events=cycle.EVENTS, files=FILES)
# The soak as a search space with no prefix, so its start and its
# counterexample files are the deep lane's (deep.Space).
SPACE = deep.Space("soak", SCOPE, (), 0)
# How often a board run meets a fault, and who acts when the group is idle.
FAULT_SHARE = 0.2
SHARES = (("worker", 0.5), ("person", 0.15), ("clock", 0.15), ("board", 0.2))


class Walk(NamedTuple):
    seed: int
    steps: int
    seconds: float
    verbs: tuple  # the verbs taken, as Verb records
    violations: tuple  # (guarantee, detail, step)
    counts: dict  # verb names and the bot's comment kinds, counted


def policy_for(rng):
    """A worker's policy drawn by the seed."""
    kind = rng.choice(("eager", "eager", "idle", "abandoner", "finisher", "bringer"))
    if kind == "eager":
        return policies.EagerClaimer(pace=rng.choice((1, 2, 6)), k=rng.choice((1, 2, 3)))
    if kind == "idle":
        return policies.IdleHolder(returns_at=rng.choice((None, 30, 58, 100)))
    if kind == "abandoner":
        return policies.Abandoner(draft=rng.random() < 0.5)
    if kind == "finisher":
        return policies.Finisher()
    return policies.MainBringer(rng.choice(("merge", "rebase")), k=rng.choice((1, 2)))


def choose(rng, model, scope, workers_policies):
    """The next verb: when a run is queued or in progress the board's, else
    by share a worker's (its policy's choice when the scope enables it,
    else one of its own verbs), a person's, the clock's or the board's,
    and any enabled verb when the share has none."""
    offered = verbs.enabled(model, scope)
    if not offered:
        return None
    board = [v for v in offered if v.actor == verbs.BOARD]
    if model.running is not None or model.queued:
        return pick_board(rng, board)
    roll, total = rng.random(), 0.0
    for who, share in SHARES:
        total += share
        if roll < total:
            break
    if who == "worker":
        worker = rng.choice(scope.workers)
        wanted = workers_policies[worker].decide(policies.view(model, worker))
        if wanted is not None and wanted in offered:
            return wanted
        own = [v for v in offered if v.actor == worker]
        if own:
            return rng.choice(own)
    elif who == "person":
        theirs = [v for v in offered if v.actor in scope.people]
        if theirs:
            return rng.choice(theirs)
    elif who == "clock":
        waits = [v for v in offered if v.actor == CLOCK]
        if waits:
            return rng.choice(waits)
    elif board:
        return pick_board(rng, board)
    return rng.choice(offered)


def pick_board(rng, board):
    plain = [v for v in board if len(v.args) < 3]
    faulty = [v for v in board if len(v.args) >= 3]
    if faulty and rng.random() < FAULT_SHARE:
        return rng.choice(faulty)
    return rng.choice(plain or faulty)


def walk(seed, steps=200, space=SPACE, known=oracle.AS_BUILT):
    """One walk from the space's start, the seeded repository after a
    board run."""
    began = time.perf_counter()
    rng = random.Random(seed)
    model, scope = space.start(), space.scope
    workers_policies = {w: policy_for(rng) for w in scope.workers}
    taken, violations, counts = [], [], Counter()
    for at in range(steps):
        verb = choose(rng, model, scope, workers_policies)
        if verb is None:
            break
        before = model.clone()
        outcome = verbs.apply(model, verb, scope)
        taken.append(verb)
        counts[verb.name] += 1
        if outcome.run is not None:
            for effect in invariants.effects_of(outcome.run.calls):
                counts[f"{effect.kind}:{effect.detail}" if effect.kind == "comment" else effect.kind] += 1
        for problem in invariants.step_checks(before, model, verb, outcome, known):
            key = (problem.guarantee, invariants.identity(problem.detail))
            if key not in {(g, invariants.identity(d)) for g, d, _ in violations}:
                violations.append((problem.guarantee, problem.detail, at))
    return Walk(seed, len(taken), time.perf_counter() - began, tuple(taken), tuple(violations), dict(counts))


def summary(walks):
    """What a set of walks did, in counts."""
    counts = Counter()
    for one in walks:
        counts.update(one.counts)
    return {"walks": len(walks), "steps": sum(w.steps for w in walks), "seconds": round(sum(w.seconds for w in walks), 1),
            "violations": sum(len(w.violations) for w in walks), "counts": dict(sorted(counts.items()))}
