"""One board run over the model, as the board workflow makes it (DESIGN.md, 3.3).

board_run calls the library's own functions in the order
cli.board_live_run calls them, on the model's clock in place of the wall
clock: collect the snapshot, release and tell overlaps (the coordinator),
render STATE.md, rename the released claims' branches, then post the
comments board.comments_due names. The run's exit code is the one
cli.run_board would give, and the board branch takes the page only when
the run exits 0, as the workflow writes the branch only after the step
that rendered it succeeded. A call the fake does not model (Unserved) is
the harness's own failure and is raised, never counted as the run's.

Until cli.py is split into a half that plans and a half that acts (build
step 3), these lines restate cli.board_live_run's order, and
tests/test_validation_cycle.py runs the real board_live_run against the
same fake and requires the same calls, so a change of order in cli.py
fails that test rather than passing here unseen.
"""

from typing import NamedTuple

from sdlc_protocol import board
from sdlc_protocol.adapters import github

from .fake_github import FakeGitHub, Unserved
from .model import MAIN, REPO, moment

# What the adapter sends as a bearer token to the fake, which reads none.
TOKEN = "fake-token-for-the-model"
# The events a board run can be started by, as GitHub names them.
SCHEDULE, PUSH, PULL_REQUEST, ISSUES = "schedule", "push", "pull_request", "issues"


class RunRecord(NamedTuple):
    """What one board run did: the event that started it, when, its exit
    code, the exception that stopped it (None when none did), the page it
    rendered ("" when it stopped first), its log lines, every call it made
    in order, and the snapshot it rendered."""

    event: str
    ref_name: str
    now: int
    exit_code: int
    raised: str | None
    page: str
    lines: tuple
    calls: tuple
    snapshot: object


def api_for(model, faults=()):
    fake = FakeGitHub(model, faults)
    return github.GitHub(TOKEN, REPO, opener=fake, sleep=lambda seconds: None), fake


def board_run(model, event=SCHEDULE, ref_name="", faults=()):
    """One board run; the model is changed by its writes."""
    api, fake = api_for(model, faults)
    now = moment(model.now)
    lines, page, snap, raised, exit_code = [], "", None, None, 0
    try:
        snap = github.snapshot(api, model.refs[MAIN], MAIN, now)
        lines += github.unread_claim_lines(snap)
        lines += github.coordinate(api, snap, now)
        page = board.render(snap)
        lines += github.release_claims(api, snap)
        lines += github.post_comments(api, board.comments_due(snap, event, ref_name, now))
    except Unserved:
        raise
    except Exception as problem:  # the workflow's step fails on any exception, as Python exits 1
        raised, exit_code = f"{type(problem).__name__}: {problem}", 1
    if exit_code == 0:
        model.board_text = page
    return RunRecord(event, ref_name, model.now, exit_code, raised, page, tuple(lines), tuple(fake.calls), snap)
