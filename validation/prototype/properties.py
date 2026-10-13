"""Properties of symphony-protocol's pure rules, as plain functions whose
leading asserts are preconditions and whose later asserts are the property:
CrossHair's "asserts" analysis reads exactly that, and a unittest can call
the same functions over an enumerated domain with nothing installed."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import board, claims, guard, team  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def a_claim_branch_names_one_issue(key: str) -> None:
    """Two branch names never claim one issue: the number a claim branch
    names spells that branch back."""
    assert len(key) <= 4
    number = claims.number_of("claude/" + key)
    assert number is None or claims.branch_of(number) == "claude/" + key


def ordering_claims_never_raises(key: str) -> None:
    """The board orders every claim branch, whatever follows claude/."""
    assert len(key) <= 3
    claims.issue_order("claude/" + key)


def a_release_is_not_due_twice(commit_h: int, released_h: int, now_h: int) -> None:
    """A claim a release comment released is never due again, until a new
    commit."""
    assert 0 <= commit_h <= released_h <= now_h <= 1000
    commit = T0 + timedelta(hours=commit_h)
    released = T0 + timedelta(hours=released_h)
    now = T0 + timedelta(hours=now_h)
    due = claims.release_due({"5": commit}, now, released={"5": released.isoformat()})
    if claims.was_released(released.isoformat(), commit):
        assert due == []


def an_idle_unreleased_claim_is_due(commit_h: int, now_h: int) -> None:
    """A claim with no commit for more than a day and no release comment is
    due."""
    assert 0 <= commit_h <= now_h <= 1000
    due = claims.release_due({"5": T0 + timedelta(hours=commit_h)}, T0 + timedelta(hours=now_h))
    assert due == (["5"] if now_h - commit_h > 24 else [])


def dealing_gives_each_issue_once(first: int, second: int, third: int, members: int) -> None:
    """The clerk deals one issue to one worker at most."""
    assert 1 <= members <= 3 and len({first, second, third}) == 3
    pool = [{"number": n, "body": ""} for n in (first, second, third)]
    dealt = team.deal([f"w{i}" for i in range(members)], pool)
    numbers = [issue["number"] for issue in dealt.values()]
    assert len(numbers) == len(set(numbers))


def a_forced_refspec_is_refused(spec: str) -> None:
    """A refspec with a leading + forces the update as --force does, and the
    board rules refuse a plain forced push anywhere."""
    assert 0 < len(spec) <= 6 and not spec.startswith("+") and not spec.startswith("-")
    assert guard.refusal(["origin", "+" + spec], "claude/7", "main") is not None


def the_board_renders_any_branch(name: str) -> None:
    """The board never fails on a branch name a hand made."""
    assert len(name) <= 3
    board.render({"sha": "", "branches": ["claude/" + name]})
