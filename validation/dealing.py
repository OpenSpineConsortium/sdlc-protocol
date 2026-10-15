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
lane fails on any double deal, from one look or from differing looks. On
the library the 54 assignments of moments to legs give one deal per
ordering and run moment, 6 distinct, which the lane counts and the report
says; the assignments are still played, since the mutant that gives each
leg its own look is judged on the same 54 and doubles 15 of them.

Since F18's fix the lane also holds the clerk to Pick: from one look at
every state a person can label an issue into, each deal and each
worker's own answer are judged by the spec's Pick and Planning as the
oracle reads them (picking_lane, below), and so is a regression trace's
deal wherever no claim has a pull request yet (pick_problems). As built
until the fix the clerk read whose a claim is, and whose a task is, from
the labels alone, and the lane found fifteen problems in 7,256 of the
30,720 deals it then had (twelve in 4,888 of 20,480 before F18's review
added the nameless and the released claim; DESIGN.md's F18 entry has the
figures); F8's review added a claim committed ahead of its push and a
kept head pushed again, 40,960 deals.
"""

import itertools
from typing import NamedTuple

from sdlc_protocol import team

from . import clerk, domains, model as M, oracle, verbs
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
    problems: tuple = ()  # what a trace's deal gets wrong by Pick (pick_problems)

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
    deals (replay.py's "deal" step), and judged by Pick on the state the
    run looked at (pick_problems)."""
    clients = moments(order, model)
    moves = clerk.workflow_deals(clients, workers, looks)
    look = clients[min(looks)].model
    return Deal(tuple(order), tuple(looks), moves, tuple(pick_problems(look, workers, moves)))


def pick_problems(look, workers, moves):
    """What a deal gets wrong by Pick and Planning (oracle.deal_problems),
    on the state the run looked at, where the oracle can judge it: a look
    at which no claim has a pull request yet, so each has work for its
    holder, which is all the spec says of a claim that needs work
    (oracle.carries). A deal elsewhere, as F4's trace's, is held to G14's
    doubles alone."""
    if any(oracle.own_pull(look, oracle.branch_of(number)) for number in oracle.held_numbers(look)):
        return []
    return oracle.deal_problems(look, tuple(workers), moves)


def violations(deal):
    """G14 on one deal, as (guarantee, detail) pairs: an issue dealt to two
    workers, from one look or from differing looks (F4, until its fix), and
    what the deal gets wrong by Pick (F18, until its fix)."""
    looks = "one look" if deal.one_look else "differing looks"
    return ([("G14", f"#{n} dealt twice from {looks}: {deal.moves}") for n in deal.doubled]
            + [("G14", problem) for problem in deal.problems])


def deals():
    for order in itertools.permutations(CHANGES):
        clients = moments(order)
        for looks in itertools.product(range(len(clients)), repeat=len(WORKERS)):
            yield Deal(order, looks, clerk.workflow_deals(clients, WORKERS, looks))


class Lane(NamedTuple):
    deals: int
    distinct: int  # how many of the deals differ in their order or their moves
    doubled_from_one_look: tuple
    doubled_from_differing_looks: tuple


def run_lane():
    one, differing, count, seen = [], [], 0, set()
    for deal in deals():
        count += 1
        seen.add((deal.order, tuple(sorted(deal.moves.items()))))
        if not deal.doubled:
            continue
        (one if deal.one_look else differing).append(deal)
    return Lane(count, len(seen), tuple(one), tuple(differing))


def doubled(lane):
    """Every double deal, whatever the looks: none since F4's fix."""
    return lane.doubled_from_one_look + lane.doubled_from_differing_looks


def report(lane):
    lines = [f"{lane.deals} deals over every assignment of moments to the legs, {lane.distinct} distinct; "
             f"{len(lane.doubled_from_one_look)} doubled from one look, "
             f"{len(lane.doubled_from_differing_looks)} from differing looks (F4 until its fix)"]
    for deal in doubled(lane)[:4]:
        lines.append(f"  after {' then '.join(deal.order)}, looks at {deal.looks}: {deal.moves}")
    return "\n".join(lines)


# --- the clerk against Pick (F18) ---------------------------------------------
#
# The rest of the lane deals from moments of one start; this part deals
# from one look at every state a person can label an issue into, and holds
# each deal to Pick and Planning as the oracle reads them
# (oracle.deal_problems), with each worker's own answer from the same look
# (team.next_for), which the spec has dealt by the same rule. Three
# settings, the issue's labels each mix domains.label_sets gives but those
# with in-review, which neither Pick nor Planning reads (label_mixes): one
# issue, dealt to a and b in either order; a queued issue beside one of
# each mix, dealt to a alone, which holds the order of Pick's places; and
# one of each mix beside a task given to a alone, dealt to a and b in
# either order, which holds a task given alone before one given to
# several. The issue of each mix is held, in turn, by no claim, by a's
# claim or b's, each claim file naming its worker, by a claim whose file
# has no Worker line, or one whose Worker line names nobody, where the
# issue's labels say whose it is, by a's claim released a day after its
# last commit, by the coordinator's comment, whose branch the board has
# not yet renamed, by the same claim with its commit dated 72 hours ahead
# of its push, which GitHub dates it by (F8), and by a's released head
# pushed again as the claim's branch, which the board has not yet
# deleted, its release counting before that push (SPEC["pushed again"]):
# each released where the mix leaves the label claimed off, as the board
# counts it, and a's held claim where it does not. No claim has a pull
# request yet, so each has work for its holder (oracle.carries). In the
# first setting a run that starts one of the two is dealt too, and held
# to the team's deal (SPEC["team's order"]).

TEAMS = (("a", "b"), ("b", "a"))
CLAIM_STATES = ("none", "a", "b", "nobody", "blank", "released", "released-ahead", "kept")
# How far ahead of the clock the commit of the claim released-ahead is
# dated, in minutes: past the day a release waits, so read by its
# committer date the claim has had no day without a commit.
AHEAD = 72 * M.HOUR
# What a hand writes on a claim file's Worker line that names nobody.
BLANK_LINE = "- Worker: (to be named)"


class Case(NamedTuple):
    labels: dict  # issue number -> its labels
    held: int  # the issue the claim state applies to
    state: str  # one of CLAIM_STATES
    team: tuple

    def describe(self):
        issues = "; ".join(f"#{n} labelled {', '.join(sorted(names)) or 'nothing'}"
                           for n, names in sorted(self.labels.items()))
        claim = {"none": "no claim",
                 "nobody": f"a claim on #{self.held} with no Worker line",
                 "blank": f"a claim on #{self.held} whose Worker line names nobody",
                 "released": f"a's claim on #{self.held} released, its branch not yet renamed",
                 "released-ahead": f"a's claim on #{self.held} committed {AHEAD // M.HOUR} hours ahead, released "
                                   f"25 hours after its push, its branch not yet renamed",
                 "kept": f"a's claim on #{self.held} released, renamed and its head pushed again as its branch, "
                         f"not yet deleted"
                 }.get(self.state, f"{self.state}'s claim on #{self.held}")
        return f"{issues}; {claim}; dealt to {', '.join(self.team)}"


def label_mixes():
    """Every mix domains.label_sets gives but those with in-review, which
    neither Pick nor Planning reads, so the lane is half as long."""
    return [names for names in domains.label_sets() if oracle.IN_REVIEW not in names]


def picking_cases():
    """Each case, and whether its runs are dealt too: the first setting's."""
    for names in label_mixes():
        for state in CLAIM_STATES:
            for order in TEAMS:
                yield Case({1: tuple(names)}, 1, state, order), True
            yield Case({1: ("todo",), 2: tuple(names)}, 2, state, ("a",)), False
            for order in TEAMS:
                yield Case({1: tuple(names), 2: ("todo", "worker:a")}, 1, state, order), False


def push_claim(model, worker, number, line=None, skew=0):
    """worker's claim on the issue, its commit dated skew minutes off the
    clock, its claim file naming the worker or, with line given, with
    that in place of its Worker line, as a file a hand wrote: "" leaves
    the line out. The commit that changes the line is pushed as any
    other, so the model records that push (model.pushed_now), which a
    release dates the claim by (F8); unrecorded, the claim was dated by
    its committer date where the board dates it by its push."""
    verbs.claim(model, worker, number, (), skew)
    if line is not None:
        ref, path = oracle.branch_of(number), f"{oracle.CLAIM_DIR}{number}.md"
        before = model.refs[ref]
        tree = model.tree(before)
        tree[path] = "".join(line + "\n" if old.startswith("- Worker:") and line else old
                             for old in tree[path].splitlines(True) if line or not old.startswith("- Worker:"))
        model.set_ref(ref, model.commit((before,), worker, tree, f"Name nobody on #{number}"))
        model.pushed_now(ref, before)


def push_kept_head(model, number):
    """The board's rename of the released claim's branch, then a's push of
    its head again as the claim's branch an hour later, which the board
    deletes in its next run, since a release already kept that head
    (SPEC["kept deleted"])."""
    ref = oracle.branch_of(number)
    head = model.refs[ref]
    model.rename_ref(ref, oracle.released_name(number, head))
    model.now += M.HOUR
    model.push("a", ref, head)


def release(model, number):
    """The coordinator's release comment on the issue, a day and an hour
    after the claim's last commit, as the board's run posts it; the
    labels are the case's, and the board has not renamed the branch."""
    model.now += 25 * M.HOUR
    model.add_comment(number, M.Comment(M.BOT, "NONE", model.now, f"{oracle.RELEASE_MARKER}\nreleased"))


def case_model(case):
    model = M.seeded(tuple(sorted(case.labels)), writers=("a", "b"))
    for number, names in case.labels.items():
        model.issues[number].labels = frozenset(names)
    if case.state in ("a", "b"):
        push_claim(model, case.state, case.held)
    elif case.state in ("nobody", "blank"):
        push_claim(model, "a", case.held, "" if case.state == "nobody" else BLANK_LINE)
    elif case.state in ("released", "released-ahead", "kept"):
        push_claim(model, "a", case.held, skew=AHEAD if case.state == "released-ahead" else 0)
        release(model, case.held)
        if case.state == "kept":
            push_kept_head(model, case.held)
    return model


def judge_case(case, runs=False):
    """The problems of one case's deal, of each worker's own answer and,
    with runs, of a run that starts one worker of the team alone, which
    the team's deal binds (SPEC["team's order"])."""
    model = case_model(case)
    client = ModelClient(model)
    moves = team.deals(client, list(case.team))
    problems = oracle.deal_problems(model, case.team, moves)
    for member in case.team:
        alone = team.next_for(ModelClient(model), member, list(case.team))
        if alone != moves[member]:
            problems.append(f"{member} asking alone is told {alone}, where the team's deal gives it {moves[member]}")
        if runs and len(case.team) > 1:
            run = team.deal_moves(ModelClient(model), list(case.team), run=[member])
            if run != {member: moves[member]}:
                problems.append(f"a run of {member} alone is dealt {run}, where the team's deal gives it "
                                f"{moves[member]}")
    return moves, problems


def picking_lane():
    """Every case's problems, as the mutation lane reads a lane: by
    (guarantee, problem), the first case that showed it."""
    found = {}
    for case, runs in picking_cases():
        moves, problems = judge_case(case, runs)
        for problem in problems:
            found.setdefault(("G14", problem), (case.describe(), str(moves)))
    return found


def main():
    lane = run_lane()
    print(report(lane))
    picked = picking_lane()
    for (guarantee, problem), (case, moves) in sorted(picked.items())[:8]:
        print(f"{guarantee}: {problem}, in {case}: {moves}")
    print(f"the clerk against Pick: {len(picked)} problems")
    return 1 if doubled(lane) or picked else 0


if __name__ == "__main__":
    raise SystemExit(main())
