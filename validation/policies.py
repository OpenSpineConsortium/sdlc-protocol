"""Scripted workers (DESIGN.md, 4.2).

A policy sees only what a worker sees (View): the last STATE.md on the
branch board, its own claims, issues and pull requests, and the comments on
them. It never sees the rest of the model, so a worker that acts on a wrong
board acts wrongly, and the run shows it. decide(view, rng) returns the
worker's next verb, or None when it has nothing to do now; a policy is
deterministic, or draws only from the rng it is given, seeded per run.

The workers in this step's tests drive whole releases and reclaims; the
scenarios of section 5 and the soak (build steps 4 and 6) run them at
length.
"""

from typing import NamedTuple

from . import page, verbs
from .model import CLAIM_PREFIX, HOUR, OWNER, RELEASED_PREFIX
from .verbs import CLAIM_DIR, Verb

WORKER_LABEL = "worker:"


class Mine(NamedTuple):
    """One claim of the worker's, as its own clone and issue show it."""

    number: int
    labels: frozenset
    planned: bool
    pull: int | None
    draft: bool
    standing: str
    commits: int
    last_commit: int
    claim_file: bool
    said_finished: bool


class View(NamedTuple):
    worker: str
    now: int
    page: str
    mine: tuple
    released: tuple  # (issue, released branch) for branches this worker's pushes made


def view(model, worker):
    """What the worker sees."""
    mine = []
    for ref, who in sorted(model.creators.items()):
        if who != worker or not ref.startswith(CLAIM_PREFIX) or ref not in model.refs:
            continue
        number = page.number("#" + ref[len(CLAIM_PREFIX):])
        if number is None or number not in model.issues:
            continue
        issue, pull = model.issues[number], model.open_pull_from(ref)
        head = model.refs[ref]
        own = model.ancestors(head) - model.ancestors(model.refs["main"])
        mine.append(Mine(number, issue.labels, any(c.author == worker for c in issue.comments),
                         pull.number if pull else None, bool(pull and pull.draft), model.standing(ref),
                         len(own) - 1, model.commits[head].committed_at,
                         f"{CLAIM_DIR}/{number}.md" in model.tree(head),
                         bool(pull and any(c.author == worker and c.body.startswith("finished") for c in pull.comments))))
    released = tuple(sorted((int(ref[len(RELEASED_PREFIX):].split("-")[0]), ref) for ref, who in model.creators.items()
                            if who == worker and ref.startswith(RELEASED_PREFIX)))
    return View(worker, model.now, model.board_text, tuple(mine), released)


# The labels that keep a worker off an issue the Blockers section or the
# Queue lists (spec/PROTOCOL.md, Pick): held, planned by the coordinator, or
# waiting on a peer; a worker's label gives the issue to that worker alone.
NOT_TO_TAKE = ("claimed", "epic", "blocked")
WORKER_PREFIXES = (WORKER_LABEL, "account:", "agent:")


def free_issue(seen, area=None):
    """The issue a worker takes (spec/PROTOCOL.md, Pick): one the Given to
    workers section gives it; else a blocker the Blockers section lists
    that nobody has claimed and no worker's label gives to another, since
    a blocker stops everyone, whether or not it carries todo; else the
    first in the Queue. Of the Blockers and the Queue, whose rows show an
    issue's labels, it takes only one in its area when it has an area, and
    never an epic or an issue labelled blocked."""
    for number, workers in sorted(page.given(seen.page).items()):
        if seen.worker in workers:
            return number
    held = set(page.claim_branches(seen.page))
    for number, names in sorted(page.blockers(seen.page).items()):
        if takable(names) and f"{CLAIM_PREFIX}{number}" not in held and (area is None or number in area):
            return number
    for number, names in sorted(page.queue_labelled(seen.page).items()):
        if takable(names) and (area is None or number in area):
            return number
    return None


def takable(names):
    """Whether an issue's labels leave it free to take."""
    return not any(name in NOT_TO_TAKE or name.startswith(WORKER_PREFIXES) for name in names)


def announce(seen, claim):
    """The Announce steps not yet done, one at a time."""
    for label in ("claimed", WORKER_LABEL + seen.worker):
        if label not in claim.labels:
            return Verb("label-on", seen.worker, (claim.number, label))
    for label in sorted(claim.labels):
        if label.startswith(WORKER_LABEL) and label != WORKER_LABEL + seen.worker:
            return Verb("label-off", seen.worker, (claim.number, label))
    if not claim.planned:
        return Verb("plan", seen.worker, (claim.number,))
    if claim.pull is None:
        return Verb("open-draft", seen.worker, (claim.number,))
    return None


class EagerClaimer:
    """Takes a Given row first, else the first Queue row in its area; claims,
    announces, opens a draft, commits every pace hours and finishes after k
    commits: deletes the claim file, marks ready, labels in-review."""

    def __init__(self, pace=1, k=2, area=None):
        self.pace, self.k, self.area = pace, k, area

    def decide(self, seen, rng=None):
        if not seen.mine:
            number = free_issue(seen, self.area)
            return Verb("claim", seen.worker, (number,)) if number is not None else None
        claim = seen.mine[0]
        step = announce(seen, claim)
        if step is not None:
            return step
        if claim.commits < self.k:
            due = claim.commits == 0 or seen.now - claim.last_commit >= self.pace * HOUR
            return Verb("commit", seen.worker, (claim.number,)) if due else None
        return self.finish(seen, claim)

    def finish(self, seen, claim):
        if claim.claim_file:
            return Verb("drop-claim-file", seen.worker, (claim.number,))
        if claim.draft:
            return Verb("mark-ready", seen.worker, (claim.pull,))
        if "in-review" not in claim.labels:
            return Verb("label-on", seen.worker, (claim.number, "in-review"))
        return None


class IdleHolder:
    """Claims, announces, opens a draft and stops; with returns_at, pushes
    one more commit at that hour, without announcing again."""

    def __init__(self, returns_at=None):
        self.returns_at, self.returned = returns_at, False

    def decide(self, seen, rng=None):
        if not seen.mine:
            if self.returned or (seen.released and self.returns_at is None):
                return None
            number = free_issue(seen)
            return Verb("claim", seen.worker, (number,)) if number is not None else None
        claim = seen.mine[0]
        if self.returned:
            return None
        if self.returns_at is not None and seen.now >= self.returns_at * HOUR:
            self.returned = True
            return Verb("commit", seen.worker, (claim.number,))
        return announce(seen, claim)


class Abandoner:
    """Claims and stops; with draft, also opens a draft."""

    def __init__(self, draft=False):
        self.draft = draft

    def decide(self, seen, rng=None):
        if not seen.mine:
            if seen.released:
                return None
            number = free_issue(seen)
            return Verb("claim", seen.worker, (number,)) if number is not None else None
        claim = seen.mine[0]
        if self.draft and claim.pull is None:
            return Verb("open-draft", seen.worker, (claim.number,))
        return None


class Finisher:
    """Does the work but may not mark ready: comments "finished; ready to
    mark" on its draft and stops."""

    def decide(self, seen, rng=None):
        if not seen.mine:
            number = free_issue(seen)
            return Verb("claim", seen.worker, (number,)) if number is not None else None
        claim = seen.mine[0]
        step = announce(seen, claim)
        if step is not None:
            return step
        if claim.commits < 1:
            return Verb("commit", seen.worker, (claim.number,))
        if not claim.said_finished:
            return Verb("finished", seen.worker, (claim.pull,))
        return None


class PersonByHand:
    """At the hours given, a person releases a claim by hand, the comment
    first and then the labels, or undoes a release: (hour, issue, "release"
    or "undo")."""

    def __init__(self, plan, who=OWNER):
        self.plan, self.who, self.queue = tuple(plan), who, []

    def decide(self, seen, rng=None):
        while self.plan and seen.now >= self.plan[0][0] * HOUR:
            _, number, act = self.plan[0]
            self.plan = self.plan[1:]
            if act == "release":
                self.queue += [Verb("marker", self.who, (number,)), Verb("label-off", self.who, (number, "claimed"))]
            else:
                self.queue.append(Verb("label-on", self.who, (number, "claimed")))
        return self.queue.pop(0) if self.queue else None


class MainBringer(EagerClaimer):
    """An EagerClaimer that brings main in, by a merge or by a rebase pushed
    with a lease, when its pull request is behind or conflicting."""

    def __init__(self, how="merge", **settings):
        super().__init__(**settings)
        self.how = how

    def decide(self, seen, rng=None):
        for claim in seen.mine:
            if claim.pull is not None and claim.standing in ("behind", "dirty"):
                return Verb(f"{self.how}-main", seen.worker, (claim.number,))
        return super().decide(seen, rng)


class Stranger:
    """No write access; posts the release marker on every claimed issue it
    sees on the board, once."""

    def __init__(self):
        self.done = set()

    def decide(self, seen, rng=None):
        for cells in page.claims(seen.page):
            number = page.number(cells[0])
            if number is not None and number not in self.done:
                self.done.add(number)
                return Verb("marker", seen.worker, (number,))
        return None


def drive(model, workers, hours, board_every=1):
    """Run the workers hour by hour, each acting until it has nothing to
    do, with a board run every board_every hours; the board runs' records."""
    records = []
    for hour in range(hours + 1):
        model.now = hour * HOUR
        for name, policy in workers.items():
            for _ in range(8):
                verb = policy.decide(view(model, name))
                if verb is None:
                    break
                verbs.apply(model, verb)
        if hour % board_every == 0:
            records.append(verbs.apply(model, Verb("board", "board", ("schedule", ""))).run)
    return records
