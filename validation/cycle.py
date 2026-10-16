"""One board run over the model, as the board workflow makes it (DESIGN.md, 3.3).

board_run calls the library's own two halves of a run, cli.plan_board and
cli.act_on_board, in the order and on the arguments cli.board_live_run
calls them, with the model's clock in place of the wall clock: the first
half collects the snapshot, releases claims and tells overlaps (the
coordinator) and renders STATE.md; the second renames the released claims'
branches, then posts the comments board.comments_due names. The run's exit
code is the one cli.run_board would give, and the board branch takes the
page only when the run exits 0, as the workflow writes the branch only
after the step that rendered it succeeded. A call the fake does not model
(Unserved) is the harness's own failure and is raised, never counted as
the run's.

start_run and finish_run are the two halves apart: the checker puts other
verbs between them, as the half-minute of a workflow run allows, so a
holder coming back between the release comment and the rename is seen
(spec, Release: "a commit pushed to the branch before the rename, its
holder coming back, keeps the claim"). Between the halves the run is
model.running, a HalfRun that keeps what the first half read and did, and
the model as the run found it and as the first half left it, which the
checks read, since the writes of a run are judged from the state it read.
"""

from dataclasses import dataclass, field
from typing import NamedTuple

from sdlc_protocol import cli
from sdlc_protocol.adapters import github

from .fake_github import FakeGitHub, Unserved
from .model import MAIN, REPO, moment

# What the adapter sends as a bearer token to the fake, which reads none.
TOKEN = "fake-token-for-the-model"
# The events a board run can be started by, as GitHub names them: the
# workflow's triggers (board.yml), the schedule among them.
SCHEDULE, PUSH, CREATE, DELETE, PULL_REQUEST, ISSUES = "schedule", "push", "create", "delete", "pull_request", "issues"
EVENTS = (SCHEDULE, PUSH, CREATE, DELETE, PULL_REQUEST, ISSUES)


class RunRecord(NamedTuple):
    """What one board run did: the event that started it, when, its exit
    code, the exception that stopped it (None when none did), the page it
    rendered ("" when it stopped first), its log lines, every call it made
    in order, the snapshot it rendered, and, for a run made in two halves
    with other verbs between them, the model as the run found it and as its
    first half left it (None for a run made whole, whose caller holds the
    state before it), which half raised, and how many of its calls the
    first half made, so the second half's writes are judged by position,
    whatever their kind."""

    event: str
    ref_name: str
    now: int
    exit_code: int
    raised: str | None
    page: str
    lines: tuple
    calls: tuple
    snapshot: object
    started_from: object = None
    planned: object = None
    raised_in: str | None = None
    first_calls: int = 0


@dataclass(frozen=True)
class SkippedRun:
    """A run board.yml's job skips, a push to any branch but the default,
    while it is in progress: it holds the concurrency group for the
    seconds the skip takes, so a run queued behind it is pending and the
    next event's replaces it, and it reads and writes nothing."""

    event: str
    ref_name: str

    def key(self):
        return (self.event, self.ref_name)


@dataclass
class HalfRun:
    """A run between its halves: what the first half read and did, the
    client it did it with, whose fake keeps the faults still armed, and the
    model as the run found it and as the first half left it."""

    event: str
    ref_name: str
    now: int
    faults: tuple
    api: object
    fake: object
    snapshot: object = None
    page: str = ""
    lines: list = field(default_factory=list)
    raised: str | None = None
    # Which half raised: "plan" or "act", None while none did.
    raised_in: str | None = None
    started_from: object = None
    planned: object = None
    # How many calls the first half made: the second half's are the rest.
    first_calls: int = 0

    def key(self):
        """What the state key holds of the run: what its first half did,
        and so what its second will do, follow from the model it started
        from, its event and its faults."""
        return (self.event, self.ref_name, self.now, self.faults, self.started_from.repository_key())


def api_for(model, faults=()):
    """The library's client over a fake answering from the model, with the
    fake's sleep, which takes no time and keeps the client's waits for a
    rate limit on the calls they come before, and the fake's clock, the
    model's minute, which a rate limit's hour is told by (FakeGitHub.sleep,
    clock)."""
    fake = FakeGitHub(model, faults)
    return github.GitHub(TOKEN, REPO, opener=fake, sleep=fake.sleep, clock=fake.clock), fake


def failed(half, problem, where):
    half.raised, half.raised_in = f"{type(problem).__name__}: {problem}", where


def started_by(event, ref_name):
    """The branch whose push started the run, as the library reads it from
    the workflow's environment (cli.pushed_branch): the ref of a push or a
    branch's creation, which GITHUB_REF names as a branch's, and nothing
    on any other event; the oracle reads the same from the event and the
    ref (oracle.pushed_branch)."""
    environ = {"GITHUB_EVENT_NAME": event, "GITHUB_REF": f"refs/heads/{ref_name}" if ref_name else ""}
    return cli.pushed_branch(environ)


def start_run(model, event=SCHEDULE, ref_name="", faults=(), keep_states=True):
    """The first half: the model is changed by the coordinator's writes,
    and the run goes on in model.running until finish_run. A run that
    raises in its first half has nothing left to do, and finish_run then
    only records it. The states the record carries are copies, which a
    run made whole has no use for and does not pay for. The branch whose
    push started the run, where the event names one, goes to the library
    as the workflow's environment would give it (started_by)."""
    api, fake = api_for(model, tuple(faults))
    half = HalfRun(event, ref_name, model.now, tuple(faults), api, fake)
    if keep_states:
        half.started_from = model.copied_whole()
    try:
        half.snapshot, half.page = cli.plan_board(api, model.refs[MAIN], MAIN, moment(model.now),
                                                  say=half.lines.append, started_by=started_by(event, ref_name))
    except Unserved:
        raise
    except Exception as problem:  # the workflow's step fails on any exception, as Python exits 1
        failed(half, problem, "plan")
    half.first_calls = len(fake.calls)
    if keep_states:
        half.planned = model.copied_whole()
    model.running = half
    return half


def finish_run(model, half=None):
    """The second half of the run in progress, or of the half given, and
    its record; the board branch takes the page when the run exits 0."""
    half = half or model.running
    if half.raised is None:
        try:
            cli.act_on_board(half.api, half.snapshot, half.event, half.ref_name, moment(half.now),
                             say=half.lines.append)
        except Unserved:
            raise
        except Exception as problem:
            failed(half, problem, "act")
    model.running = None
    model.finished()
    exit_code = 0 if half.raised is None else 1
    if exit_code == 0:
        model.board_text = half.page
    return RunRecord(half.event, half.ref_name, half.now, exit_code, half.raised, half.page, tuple(half.lines),
                     tuple(half.fake.calls), half.snapshot, half.started_from, half.planned, half.raised_in,
                     half.first_calls)


def board_run(model, event=SCHEDULE, ref_name="", faults=()):
    """One board run made whole, both halves with nothing between them; the
    model is changed by its writes. The record carries no states, since
    the caller holds the state before the run and the state after it is
    the model."""
    return finish_run(model, start_run(model, event, ref_name, faults, keep_states=False))._replace(raised_in=None)
