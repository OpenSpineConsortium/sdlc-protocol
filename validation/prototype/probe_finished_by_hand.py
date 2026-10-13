"""F10 reproduction: a person releases a finished draft by hand (the marker
comment, claimed and the worker's label off, more than a day after the last
commit). The spec's second Release paragraph counts it released; the board
never does, because github.releases skips a finished draft before the release
comment is read (adapters/github.py:394)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore
from explore import apply, seeded, claim_branches_shown, queue_numbers, oracle_released, expected_queue
from model import board_run

m = seeded((1,), ("a", "b"))
for act in [("claim", "a", 1), ("wait",), ("announce", "a", 1), ("draft", "a", 1), ("finished", "a", 1),
            ("release_by_hand", 1), ("board",)]:
    m, note = apply(m, act)
print("labels:", sorted(m.issues[1].labels), "| refs:", sorted(m.refs))
print("board claims:", sorted(claim_branches_shown(m.board_text)), "| board queue:", sorted(queue_numbers(m.board_text)))
print("oracle: released =", oracle_released(m, "claude/1"), "| expected queue:", sorted(expected_queue(m)))
for _ in range(30):
    m.now += 24
    board_run(m)
print("30 days later -> board claims:", sorted(claim_branches_shown(m.board_text)),
      "| queue:", sorted(queue_numbers(m.board_text)), "| refs:", sorted(m.refs))
