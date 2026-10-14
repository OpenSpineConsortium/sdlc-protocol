"""The dealing lane (DESIGN.md, 9.1): three workers ask the clerk at once.

The workers of one run of workers.yml are dealt the shared work so that
no two start on one claim (G14: the shared work is dealt out, each issue
to at most one worker). The lane starts from one state, a claimed issue
with a ready and clean pull request and two free ones, and plays every
ordering of the state changes that matter between the moments a worker
could look: main moves, so the ready pull request falls behind and its
holder has work again, and a worker outside the run claims one of the
free issues. Each worker's leg is given one of the moments between the
changes; every assignment of moments to workers is tried, and the deal
is the workflow's (clerk.workflow_deals). A deal in which two workers of
the run get one issue is a double deal.

What it found, as built until F4's fix, when each leg asked team.next_for
from its own look: from one look, never a double deal; from differing
looks, one (F4): the holder's pull request was clean at one worker's
look, so the holder was dealt an issue and the worker the next, and
behind at another's, so the holder carried its own claim and that worker
was dealt what the first worker's deal gave the holder. Since the fix the
workflow looks once for the run, in its first job (team.deals), and the
lane fails on any double deal, from one look or from differing looks.
"""

import itertools
from typing import NamedTuple

from . import clerk, model as M, verbs
from .clerk import ModelClient
from .model import OWNER

WORKERS = ("a", "b", "c")
OUTSIDER = "d"
HELD, FREE = 4, (5, 6)
CHANGES = ("main-moves", "outsider-claims")


class Deal(NamedTuple):
    order: tuple  # the changes in the order they happened
    looks: tuple  # the moment each worker looked, 0 before any change
    moves: dict  # worker -> the clerk's answer

    @property
    def doubled(self):
        """The issues dealt to more than one worker."""
        taken = [m.split()[1] for m in self.moves.values() if m.startswith("take ")]
        return sorted({n for n in taken if taken.count(n) > 1})

    @property
    def one_look(self):
        return len(set(self.looks)) == 1


def start():
    """A model where a holds #4 with a ready, clean pull request, #5 and #6
    are free, and the board has run."""
    model = M.seeded((HELD,) + FREE, writers=WORKERS + (OUTSIDER,))
    scope = verbs.Scope(workers=WORKERS, issues=(HELD,) + FREE)
    for verb in (("claim", "a", (HELD,)), ("label-on", "a", (HELD, "claimed")), ("label-on", "a", (HELD, "worker:a")),
                 ("label-off", "a", (HELD, "todo")), ("commit", "a", (HELD,)), ("drop-claim-file", "a", (HELD,)),
                 ("open-draft", "a", (HELD,)), ("mark-ready", "a", (M.FIRST_PULL,)),
                 ("board", "board", ("schedule", ""))):
        outcome = verbs.apply(model, verbs.Verb(*verb), scope)
        assert outcome.taken, verb
    return model


def change(model, name):
    if name == "main-moves":
        verbs.move_main(model)
    elif name == "outsider-claims":
        verbs.claim(model, OUTSIDER, FREE[0], ("src/5.py",))
    else:
        raise ValueError(name)


def moments(order, model=None):
    """The clients at each moment: before any change, after the first,
    after both; from the lane's own start, or from a model a trace built."""
    model = start() if model is None else model.clone()
    found = [ModelClient(model.clone())]
    for name in order:
        change(model, name)
        found.append(ModelClient(model.clone()))
    return found


def play(model, order, looks, workers=WORKERS):
    """One deal from the model as a trace left it: the changes in order,
    each worker's leg at the moment its look names, dealt as the workflow
    deals (replay.py's "deal" step)."""
    clients = moments(order, model)
    return Deal(tuple(order), tuple(looks), clerk.workflow_deals(clients, workers, looks))


def violations(deal):
    """G14 on one deal, as (guarantee, detail) pairs: an issue dealt to two
    workers, from one look or from differing looks (F4, until its fix)."""
    looks = "one look" if deal.one_look else "differing looks"
    return [("G14", f"#{n} dealt twice from {looks}: {deal.moves}") for n in deal.doubled]


def deals():
    for order in itertools.permutations(CHANGES):
        clients = moments(order)
        for looks in itertools.product(range(len(clients)), repeat=len(WORKERS)):
            yield Deal(order, looks, clerk.workflow_deals(clients, WORKERS, looks))


class Lane(NamedTuple):
    deals: int
    doubled_from_one_look: tuple
    doubled_from_differing_looks: tuple


def run_lane():
    one, differing, count = [], [], 0
    for deal in deals():
        count += 1
        if not deal.doubled:
            continue
        (one if deal.one_look else differing).append(deal)
    return Lane(count, tuple(one), tuple(differing))


def doubled(lane):
    """Every double deal, whatever the looks: none since F4's fix."""
    return lane.doubled_from_one_look + lane.doubled_from_differing_looks


def report(lane):
    lines = [f"{lane.deals} deals; {len(lane.doubled_from_one_look)} doubled from one look, "
             f"{len(lane.doubled_from_differing_looks)} from differing looks (F4 until its fix)"]
    for deal in doubled(lane)[:4]:
        lines.append(f"  after {' then '.join(deal.order)}, looks at {deal.looks}: {deal.moves}")
    return "\n".join(lines)


def main():
    lane = run_lane()
    print(report(lane))
    return 1 if doubled(lane) else 0


if __name__ == "__main__":
    raise SystemExit(main())
