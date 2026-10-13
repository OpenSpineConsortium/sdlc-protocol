"""Find the shortest trace to a T3 disagreement (board versus oracle) with the
full-body key, and print it with the board's and the oracle's answers."""
import sys
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore
from explore import enabled, apply, state_problems, transition_problems, seeded
from explore_variants import key

def main(depth=7, want="T3"):
    start = seeded((1,), ("a", "b"))
    frontier, seen = deque([(start, ())]), {key(start, False, True)}
    while frontier:
        m, trace = frontier.popleft()
        if len(trace) >= depth:
            continue
        for act in enabled(m, ("a", "b")):
            nxt, note = apply(m, act)
            probs = transition_problems(m, nxt, act)
            hits = [p for p in probs if p.startswith(want)]
            if hits:
                print("trace:", " -> ".join("/".join(map(str, a)) for a in trace + (act,)))
                print("problems:", hits)
                print("note:", note)
                i = nxt.issues[1]
                print("issue labels:", sorted(i.labels))
                for c in i.comments:
                    print("  comment", c.author, c.association, c.at, c.body.splitlines()[0][:70])
                print("refs:", {r: s[:7] for r, s in nxt.refs.items()})
                print("commit times:", {r: nxt.commits[s].at for r, s in nxt.refs.items()})
                print("pulls:", [(p.number, p.head, p.draft, p.state) for p in nxt.pulls.values()])
                print("now:", nxt.now)
                print(nxt.board_text)
                return
            k = key(nxt, False, True)
            if k not in seen:
                seen.add(k)
                frontier.append((nxt, trace + (act,)))

main()
