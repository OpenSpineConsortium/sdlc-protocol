"""Probe how the claim rules read branch names that are not canonical."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library
from sdlc_protocol import board, claims, protocol_check

for name in ["claude/3", "claude/03", "claude/\u0663", "claude/\u00b2", "claude/3 ", "claude/+3"]:
    try:
        n = claims.number_of(name)
    except Exception as e:
        n = f"raises {type(e).__name__}"
    try:
        order = claims.issue_order(name)
    except Exception as e:
        order = f"raises {type(e).__name__}: {e}"
    print(repr(name), "number_of ->", repr(n), "| issue_order ->", order)

snap = {"sha": "a" * 40, "branches": ["claude/3", "claude/03"], "claims": {"3": "- Worker: alice\n", "03": "- Worker: bob\n"},
        "issues": [{"number": 3, "title": "t", "labels": ["todo", "claimed"]}]}
print(board.render(snap).split("## Open pull requests")[0])
print("claimed numbers:", board.claimed_numbers(["claude/3", "claude/03"]))
for b in ["claude/3", "claude/03"]:
    pr = {"head": {"ref": b, "repo": {"full_name": "o/r"}}, "base": {"repo": {"full_name": "o/r"}}, "body": "Closes #3", "draft": True}
    print(b, "protocol problems:", protocol_check.problems_for(pr, True))
try:
    board.render({"sha": "a" * 40, "branches": ["claude/\u00b2"]})
    print("render with claude/superscript-two: ok")
except Exception as e:
    print("render with claude/superscript-two raises", type(e).__name__, e)
