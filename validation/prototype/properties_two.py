"""Second CrossHair probe: the release property with the idle time made
inside the property, and the board's render given a longer bound."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import board, claims  # noqa: E402


def a_release_is_not_due_twice(commit_h: int, released_h: int, now_h: int) -> None:
    assert 0 <= commit_h <= released_h <= now_h <= 1000
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day = timedelta(hours=24)
    commit = t0 + timedelta(hours=commit_h)
    released = (t0 + timedelta(hours=released_h)).isoformat()
    due = claims.release_due({"5": commit}, t0 + timedelta(hours=now_h), day, {"5": released})
    if claims.was_released(released, commit, day):
        assert due == []


def the_board_renders_any_branch(name: str) -> None:
    assert len(name) <= 2
    board.render({"sha": "", "branches": ["claude/" + name]})
