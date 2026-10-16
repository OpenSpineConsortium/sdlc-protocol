"""What one board run costs in calls to GitHub's REST API, on the fake
(README.md, What a board run costs).

Each case is a repository the model seeds, as a trace file seeds one
(replay.scenario_of), and two board runs over it, one after the other: the
first does what the state asks of it, a release, a comment owed, and the
second finds it done, as most runs do, since every issue and pull request
event starts one. A run's cost is every call it made, a read (GET) or a
write (any other method), counted where the fake saw it (cycle.RunRecord.
calls).

The fake answers as GitHub does on the paths a run asks (fake_github.py):
100 items a page, a pull request's mergeability computed by its second
read in a run, where GitHub most often has it by the first. GitHub can
cost more than the fake: a run reads the activity of a claim's push once
more after a pause where GitHub has not listed it yet, and the head's
committer date where it lists none then either (F8); a pull request a
rule holds, which GitHub calls blocked, takes a comparison with the
default branch (F9); a release comment whose author GitHub's
author_association does not vouch for takes a read of that author's
permission, where in these cases every release comment is the
coordinator's own; and a claim's own pull request whose opener
author_association does not vouch for there takes a read of the
opener's permission, where every worker here is a collaborator. The
run's last log line counts the calls on GitHub itself (adapters.
github.calls_line).
"""

from typing import NamedTuple

from . import cycle, replay, run
from .model import FIRST_PULL, HOUR

WORKERS = ("a", "b", "c", "d", "e")
# When the claims are made, and when the runs are: a day and an hour later
# for a claim gone idle, an hour later for one at work.
CLAIMED_AT, BUSY_AT, IDLE_AT = 0, 1, 25


class Case(NamedTuple):
    """A repository: claims, how many of them have a draft pull request and
    how many of those drafts are marked ready, how many of the rest their
    authors said are finished, how many of the claims
    without one have a pull request a stranger opened from the claim's
    branch, which is no claim's, and how many of the rest have their issue
    closed; issues queued with no claim; whether the claims have gone a
    day without a commit, how many times main moved under the drafts, and
    whether every claim lists the same file."""

    name: str
    claims: int = 0
    drafts: int = 0
    ready: int = 0
    finished: int = 0
    strangers: int = 0
    closed: int = 0
    queued: int = 0
    idle: bool = False
    moves: int = 0
    shared_file: bool = False


class Cost(NamedTuple):
    """One run's calls, reads and writes."""

    calls: int
    reads: int
    writes: int


CASES = (
    Case("three issues queued, nothing claimed", queued=3),
    Case("one claim with no pull request", claims=1),
    Case("one claim with its draft", claims=1, drafts=1),
    Case("ten claims with their drafts", claims=10, drafts=10),
    Case("one claim with its draft, gone a day without a commit", claims=1, drafts=1, idle=True),
    Case("ten claims with their drafts, main moved under them", claims=10, drafts=10, moves=1),
    Case("two claims with their drafts on one file", claims=2, drafts=2, shared_file=True),
)


def steps(case):
    """The trace file's steps that make the case's repository: the claims
    in order, the first of them with their drafts, then the ready ones and
    the finished ones among the drafts, the strangers' pull requests and
    the closed issues among the claims that follow, so no claim is two of
    these at once."""
    made = []
    for number in range(1, case.claims + 1):
        worker = WORKERS[(number - 1) % len(WORKERS)]
        made += [{"verb": "claim", "actor": worker, "args": [number], "at": CLAIMED_AT},
                 {"verb": "announce", "actor": worker, "args": [number]}]
        if number <= case.drafts:
            made.append({"verb": "open-draft", "actor": worker, "args": [number]})
    made += [{"verb": "mark-ready", "actor": "owner", "args": [FIRST_PULL + n]} for n in range(case.ready)]
    made += [{"verb": "finished", "actor": WORKERS[n % len(WORKERS)], "args": [FIRST_PULL + n]}
             for n in range(case.ready, case.ready + case.finished)]
    strangers = range(case.drafts + 1, case.drafts + case.strangers + 1)
    made += [{"verb": "stranger-pull", "actor": "sam", "args": [number, False]} for number in strangers]
    closed = range(case.drafts + case.strangers + 1, case.drafts + case.strangers + case.closed + 1)
    made += [{"verb": "close-issue", "actor": "owner", "args": [number]} for number in closed]
    made += [{"verb": "move-main", "actor": "owner"}] * case.moves
    return made


def seeded(case):
    """The model after the case's steps, at the hour of its runs."""
    numbers = range(1, case.claims + case.queued + 1)
    data = {"finding": "cost", "title": case.name, "status": "", "breaks": [], "world": "memory",
            "issues": {str(n): ["todo"] for n in numbers}, "workers": list(WORKERS),
            "files": {str(n): ["src/shared.py"] for n in numbers} if case.shared_file else {},
            "steps": steps(case)}
    scenario = replay.scenario_of(data)
    model = run.prefix(scenario, len(scenario.steps))
    model.now = (IDLE_AT if case.idle else BUSY_AT) * HOUR
    return model


def cost_of(record):
    reads = sum(1 for call in record.calls if call.method == "GET")
    return Cost(len(record.calls), reads, len(record.calls) - reads)


def measure(case):
    """The costs of the case's first run and of the run after it."""
    model = seeded(case)
    first = cycle.board_run(model)
    then = cycle.board_run(model)
    if first.exit_code or then.exit_code:
        raise AssertionError(f"{case.name}: a run failed: {first.raised or then.raised}")
    return cost_of(first), cost_of(then)


def table():
    """The README's table, a row a case: what its first run and the run
    after it cost."""
    rows = ["| The repository | First run: calls (reads, writes) | The run after it |", "| --- | --- | --- |"]
    for case in CASES:
        first, then = measure(case)
        rows.append(f"| {case.name} | {first.calls} ({first.reads}, {first.writes}) | "
                    f"{then.calls} ({then.reads}, {then.writes}) |")
    return "\n".join(rows) + "\n"
