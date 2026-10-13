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

import copy
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
    state before it)."""

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
    snap: object = None
    page: str = ""
    lines: list = field(default_factory=list)
    raised: str | None = None
    started_from: object = None
    planned: object = None

    def key(self):
        """What the state key holds of the run: what its first half did,
        and so what its second will do, follow from the model it started
        from, its event and its faults."""
        return (self.event, self.ref_name, self.now, self.faults, self.started_from.repository_key())


def api_for(model, faults=()):
    fake = FakeGitHub(model, faults)
    return github.GitHub(TOKEN, REPO, opener=fake, sleep=lambda seconds: None), fake


def failed(half, problem):
    half.raised = f"{type(problem).__name__}: {problem}"


def start_run(model, event=SCHEDULE, ref_name="", faults=(), keep_states=True):
    """The first half: the model is changed by the coordinator's writes,
    and the run goes on in model.running until finish_run. A run that
    raises in its first half has nothing left to do, and finish_run then
    only records it. The states the record carries are copies, which a
    run made whole has no use for and does not pay for."""
    api, fake = api_for(model, tuple(faults))
    half = HalfRun(event, ref_name, model.now, tuple(faults), api, fake)
    if keep_states:
        half.started_from = copy.deepcopy(model)
    try:
        half.snap, half.page = cli.plan_board(api, model.refs[MAIN], MAIN, moment(model.now), say=half.lines.append)
    except Unserved:
        raise
    except Exception as problem:  # the workflow's step fails on any exception, as Python exits 1
        failed(half, problem)
    if keep_states:
        half.planned = copy.deepcopy(model)
    model.running = half
    return half


def finish_run(model, half=None):
    """The second half of the run in progress, or of the half given, and
    its record; the board branch takes the page when the run exits 0."""
    half = half or model.running
    if half.raised is None:
        try:
            cli.act_on_board(half.api, half.snap, half.event, half.ref_name, moment(half.now), say=half.lines.append)
        except Unserved:
            raise
        except Exception as problem:
            failed(half, problem)
    model.running = None
    exit_code = 0 if half.raised is None else 1
    if exit_code == 0:
        model.board_text = half.page
    return RunRecord(half.event, half.ref_name, half.now, exit_code, half.raised, half.page, tuple(half.lines),
                     tuple(half.fake.calls), half.snap, half.started_from, half.planned)


def board_run(model, event=SCHEDULE, ref_name="", faults=()):
    """One board run made whole, both halves with nothing between them; the
    model is changed by its writes. The record carries no states, since
    the caller holds the state before the run and the state after it is
    the model."""
    return finish_run(model, start_run(model, event, ref_name, faults, keep_states=False))
