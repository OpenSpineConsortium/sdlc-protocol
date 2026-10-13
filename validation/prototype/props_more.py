"""Properties for rows of the design's table 8.4 marked "not run yet", in the
asserts shape, to measure what CrossHair 0.0.111 does with them at 20 s."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library
from sdlc_protocol import board, claims, gate, labels, protocol_check  # noqa: E402


def state_word_is_one_of_five(state: str) -> None:
    assert len(state) <= 12
    assert board.state_word(state) in board.STATE_WORDS


def holder_names_each_once(first: str, second: str, third: str) -> None:
    assert len(first) + len(second) + len(third) <= 30
    names = [first, second, third]
    found = labels.holder_names(names)
    assert len(found) == len(set(found))


def a_release_comment_has_the_marker_first(body: str, association: str) -> None:
    assert len(body) <= 60
    comment = {"body": body, "author_association": association, "user": {"login": "x"}}
    if claims.is_release_comment(comment):
        assert body.strip().splitlines()[0].strip() == claims.RELEASE_MARKER


def issue_number_raises_only_refused(value: str) -> None:
    assert len(value) <= 6
    try:
        number = gate.issue_number(value)
    except gate.Refused:
        return
    assert 0 <= number <= 999999999


def a_ready_pull_with_its_claim_file_is_refused(number: int, body: str) -> None:
    assert 1 <= number <= 9999 and len(body) <= 20
    pull = {"head": {"ref": f"claude/{number}", "repo": {"full_name": "o/r"}}, "base": {"repo": {"full_name": "o/r"}},
            "body": body, "draft": False}
    assert protocol_check.problems_for(pull, True) != []
