"""Playing a scenario (DESIGN.md, 5.1): run_scenario(scenario) -> RunRecord.

Each step of the scenario is applied to the model in turn, at its hour,
and after every verb every guarantee is checked (invariants.step_checks),
as the checker checks a search. After each step the expectations the
scenario states are compared with the state and the last page the board
wrote, and a difference is a mismatch. The record holds each step with
the verbs it ran, their outcomes, the violations met and the mismatches
found, every page the board rendered, how many checks ran and a timeline
a person reads.

A step by a scripted worker ("act") runs the worker's policy from the
page and its own clone alone until it has nothing to do, a few verbs at
most, so the scenario shows what a worker that never sees the model does;
an argument ("released", n) names the newest released branch of issue n,
which no scenario can spell in advance, since the name holds the head's
sha.

Two steps are no verb of the model's: "guard" asks the push guard about a
command and, when the command is one of the guard grammar's, judges the
guard's answer by git's recorded one (guard_grammar.judged_by_git, G11a);
"deal" deals the workers' legs, each given a moment, from the model as the
steps left it, the way the workers' workflow deals since F4's fix: one
look for the run, no later than the earliest leg (dealing.play, G14). Both
are what the regression traces of DESIGN.md 5.3 need (replay.py).
"""

import copy
from collections import Counter
from typing import NamedTuple

from . import dealing, guard_grammar, invariants, model as M, oracle, page, policies, verbs
from .model import HOUR, RELEASED_PREFIX
from .verbs import CAROL, DANA, Scope, Verb

MOST_ACTS = 8


class StepRecord(NamedTuple):
    step: object
    verbs: tuple
    outcomes: tuple
    violations: tuple
    mismatches: tuple


class RunRecord(NamedTuple):
    scenario: str
    steps: tuple
    pages: tuple
    checks: int
    timeline: tuple

    @property
    def violations(self):
        return [(at, v) for at, record in enumerate(self.steps) for v in record.violations]

    @property
    def mismatches(self):
        return [(at, m) for at, record in enumerate(self.steps) for m in record.mismatches]

    @property
    def red_steps(self):
        return sorted({at for at, _ in self.violations})


def scope_of(scenario):
    return Scope(workers=tuple(w for w in scenario.workers if w != verbs.SAM), issues=tuple(scenario.issues),
                 people=scenario.people, files=scenario.files, careless=scenario.careless, events=scenario.events,
                 split_board=scenario.split_board)


def seeded(scenario):
    writers = tuple(w for w in scenario.workers if w != verbs.SAM) + (CAROL, DANA)
    model = M.seeded(tuple(scenario.issues), writers=writers)
    for number, labels in scenario.issues.items():
        model.issues[number].labels = frozenset(labels)
    return model


def released_ref(model, number):
    """The newest released/<number>-* branch, by when its claim was made."""
    named = [r for r in model.refs if r.startswith(f"{RELEASED_PREFIX}{number}-")]
    if not named:
        raise ValueError(f"no released branch of #{number}")
    return max(named, key=lambda r: model.created.get(r, 0))


def resolve(model, args):
    return tuple(released_ref(model, a[1]) if isinstance(a, tuple) and a[:1] == ("released",) else a for a in args)


def current_branch(model, actor):
    """The branch the actor's clone is on: its own claim, else the default."""
    mine = sorted(ref for ref, who in model.creators.items() if who == actor and ref.startswith(M.CLAIM_PREFIX))
    return mine[0] if mine else M.MAIN


# --- the expectations ---------------------------------------------------------

def bot_comments(model, number):
    return Counter(oracle.comment_kind(c.body) for c in model.comments_on(number) if c.author == M.BOT)


def told(model, number):
    found = set()
    for comment in model.comments_on(number):
        match = oracle.OVERLAP_MARKER.fullmatch(oracle.first_line(comment.body))
        if match and comment.author == M.BOT:
            found |= {int(n) for n in match.group(1).split()}
    return found


def latest_bot_comment(model, number, kind):
    found = [c for c in model.comments_on(number) if c.author == M.BOT and oracle.comment_kind(c.body) == kind]
    return found[-1].body if found else ""


def open_pulls(model):
    return sorted(p.number for p in model.pulls.values() if p.state == "open")


def observed(model, expect, outcomes, scenario, step):
    """Each field the expectation names, as (name, expected, found)."""
    shown = model.board_text
    run = next((o.run for o in outcomes if o.run is not None), None)
    yield "taken", expect.taken, outcomes[-1].taken if outcomes else None
    yield "queue", expect.queue, page.queue(shown)
    yield "given", expect.given, page.given(shown)
    yield "claims", expect.claims, page.claim_workers(shown)
    yield "not_claims", expect.not_claims, page.not_claims(shown)
    yield "overlaps", expect.overlaps, page.overlaps(shown)
    yield "merged", expect.merged, page.merged(shown)
    yield "pulls", expect.pulls, open_pulls(model)
    if expect.holders is not None:
        yield "holders", expect.holders, {n: model.creators.get(oracle.branch_of(n)) for n in expect.holders}
    if expect.labels is not None:
        yield "labels", expect.labels, {n: set(model.issues[n].labels) for n in expect.labels}
    if expect.comments is not None:
        yield "comments", expect.comments, {n: dict(bot_comments(model, n)) for n in expect.comments}
    if expect.markers is not None:
        yield "markers", expect.markers, {n: len([c for c in model.comments_on(n)
                                                  if oracle.first_line(c.body) == oracle.RELEASE_MARKER])
                                          for n in expect.markers}
    if expect.told is not None:
        yield "told", expect.told, {n: told(model, n) for n in expect.told}
    if expect.mentions is not None:
        for number, kinds in expect.mentions.items():
            for kind, words in kinds.items():
                body = latest_bot_comment(model, number, kind)
                yield f"mentions on #{number}", words, [w for w in words if w in body]
    if expect.refs is not None:
        yield "refs", expect.refs, {name: name in model.refs for name in expect.refs}
    if expect.released is not None:
        yield "released", expect.released, {n: len(verbs.released_refs(model, n)) for n in expect.released}
    if expect.standing is not None:
        yield "standing", expect.standing, {n: model.standing(model.pulls[n].head) for n in expect.standing}
    if expect.no_writes is not None:
        writes = [f"{c.method} {c.path}" for c in (run.calls if run else ()) if c.method != "GET"]
        yield "no_writes", expect.no_writes, not writes
    if expect.picks is not None:
        yield "picks", expect.picks, {w: policies.free_issue(policies.view(model, w)) for w in expect.picks}
    if expect.refused is not None:
        yield "refused", expect.refused, bool(verbs.guard_says(step.args[0], current_branch(model, step.actor)))
    if expect.kept is not None:
        yield "kept", expect.kept, {n: path if path in model.tree(model.refs.get(oracle.branch_of(n), ""))
                                    else None for n, path in expect.kept.items()}


def mismatches(model, expect, outcomes, scenario, step):
    return tuple(f"{name}: expected {wanted!r}, found {found!r}"
                 for name, wanted, found in observed(model, expect, outcomes, scenario, step)
                 if wanted is not None and wanted != found)


# --- running --------------------------------------------------------------------

def run_scenario(scenario, known=oracle.AS_BUILT):
    return play(scenario, scenario.steps, known)[1]


def prefix(scenario, upto, known=oracle.AS_BUILT):
    """The model after the scenario's first upto steps, for a search that
    starts where a scenario left off (DESIGN.md, 6.3)."""
    return play(scenario, scenario.steps[:upto], known)[0]


def play(scenario, steps, known):
    """A scripted worker keeps state (a return made once), so each play
    gets its own copy of every policy and a scenario plays the same twice."""
    model, scope = seeded(scenario), scope_of(scenario)
    workers = {name: copy.deepcopy(policy) for name, policy in scenario.workers.items()}
    records, pages, timeline, checks = [], [], [], 0
    for step in steps:
        model.now = step.at * HOUR
        ran, outcomes, violations = [], [], []

        def apply(verb):
            nonlocal checks
            before = model.clone()
            outcome = verbs.apply(model, verb, scope)
            found = invariants.step_checks(before, model, verb, outcome, known)
            checks += 1
            ran.append(str(verb))
            outcomes.append(outcome)
            violations.extend(found)
            timeline.append(f"{step.at:>4} h  {verb}: {outcome.note}" + (f"; {len(found)} red" if found else ""))
            if outcome.run is not None:
                pages.append((step.at, outcome.run.page))

        if step.verb == "act":
            # The scripted worker decides from what it sees after each of
            # its own acts, as a worker does, until it has nothing to do.
            policy = workers[step.actor]
            for _ in range(MOST_ACTS):
                verb = policy.decide(policies.view(model, step.actor))
                if verb is None:
                    break
                apply(verb)
        elif step.verb == "guard":
            judged = guard_grammar.judged_by_git(step.args[0])
            found = [invariants.Violation(*pair) for pair in judged or ()]
            checks += judged is not None
            violations.extend(found)
            ran.append(f"guard {step.actor} {step.args[0]}")
            timeline.append(f"{step.at:>4} h  guard {step.actor} {step.args[0]}: "
                            + ("not in the grammar" if judged is None else "judged by git")
                            + (f"; {len(found)} red" if found else ""))
        elif step.verb == "deal":
            deal = dealing.play(model, tuple(step.args[0]), tuple(step.args[1]))
            found = [invariants.Violation(*pair) for pair in dealing.violations(deal)]
            checks += 1
            violations.extend(found)
            ran.append(f"deal {' then '.join(deal.order)} at {deal.looks}")
            timeline.append(f"{step.at:>4} h  deal after {' then '.join(deal.order)}, looks at {deal.looks}: "
                            f"{deal.moves}" + (f"; {len(found)} red" if found else ""))
        elif step.verb != "look":
            apply(Verb(step.verb, step.actor, resolve(model, step.args)))
        if not ran:
            timeline.append(f"{step.at:>4} h  {step.actor} {step.verb} {' '.join(str(a) for a in step.args)}")
        wrong = mismatches(model, step.expect, outcomes, scenario, step)
        timeline += [f"        mismatch, {m}" for m in wrong]
        records.append(StepRecord(step, tuple(ran), tuple(outcomes), tuple(violations), wrong))
    return model, RunRecord(scenario.name, tuple(records), tuple(pages), checks, tuple(timeline))


def run_all(scenarios, known=oracle.AS_BUILT):
    return {name: run_scenario(scenario, known) for name, scenario in scenarios.items()}
