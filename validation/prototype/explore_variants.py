"""Measure the prototype explorer under variants the design proposes or needs:
pruning at violations or not, the key with absolute times or with gaps capped
at 25 h, with or without the history the invariants read (ever_heads, the
label timeline), and with worker symmetry. Scratch only; reads the prototype."""
import argparse, hashlib, itertools, sys, time
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore
from explore import enabled, apply, state_problems, transition_problems, seeded

def instants(m):
    found = {m.now}
    live = m.reachable()
    found |= {m.commits[s].at for s in live}
    for i in m.issues.values():
        found |= {c.at for c in i.comments}
    for p in m.pulls.values():
        found.add(p.opened)
        found |= {c.at for c in p.comments}
    found |= {e[0] for e in m.label_events}
    return sorted(found)

def time_map(m, capped):
    points = instants(m)
    if not capped:
        return {t: t for t in points}
    out, prev, cur = {}, None, 0
    for t in points:
        if prev is not None:
            cur += min(t - prev, 25)
        out[t] = cur
        prev = t
    return out

def rename(text, perm):
    if text in perm:
        return perm[text]
    for old, new in perm.items():
        text = text.replace(f"worker:{old}", f"worker:\0{new}").replace(f"Worker: {old}\n", f"Worker: \0{new}\n")
    return text.replace("\0", "")

def key(m, capped, history, perm=None):
    perm = perm or {}
    T = time_map(m, capped)
    memo = {}
    def canon(sha):
        if not sha:
            return ""
        if sha not in memo:
            c = m.commits[sha]
            files = tuple((p, rename(v, perm)) for p, v in c.files)
            memo[sha] = hashlib.sha1(repr((tuple(canon(p) for p in c.parents), rename(c.author, perm), T[c.at], files, c.message)).encode()).hexdigest()[:16]
        return memo[sha]
    refs = tuple(sorted((r, canon(s)) for r, s in m.refs.items()))
    issues = tuple(sorted((n, i.state, tuple(sorted(rename(l, perm) for l in i.labels)),
                           tuple((rename(c.author, perm), T[c.at], c.body) for c in i.comments)) for n, i in m.issues.items()))
    pulls = tuple(sorted((n, p.head, rename(p.author, perm), p.draft, p.state, T[p.opened],
                          tuple((rename(c.author, perm), T[c.at], c.body) for c in p.comments)) for n, p in m.pulls.items()))
    extra = ()
    if history:
        heads = tuple(sorted(canon(s) if s in m.commits else s for s in m.ever_heads))
        events = tuple((T[e[0]], e[1], rename(e[2], perm), e[3], rename(e[4], perm)) for e in m.label_events)
        extra = (heads, events)
    return (T[m.now], refs, issues, pulls, m.fail_next, m.pending is not None) + extra

def sym_key(m, capped, history, workers):
    best = None
    for p in itertools.permutations(workers):
        perm = dict(zip(workers, p))
        k = key(m, capped, history, perm)
        r = repr(k)
        if best is None or r < best:
            best = r
    return best

def run(issues, workers, depth, prune, capped, history, symmetry):
    start = seeded(issues, workers)
    kf = (lambda m: sym_key(m, capped, history, workers)) if symmetry else (lambda m: key(m, capped, history))
    frontier, seen = deque([(start, 0)]), {kf(start)}
    transitions, violating, kinds = 0, 0, set()
    began = time.perf_counter()
    while frontier:
        m, d = frontier.popleft()
        if d >= depth:
            continue
        for act in enabled(m, workers):
            nxt, _ = apply(m, act)
            transitions += 1
            probs = state_problems(nxt) + transition_problems(m, nxt, act)
            if probs:
                violating += 1
                kinds |= {p.split(":")[0].split(" ")[0] for p in probs}
                if prune:
                    continue
            k = kf(nxt)
            if k not in seen:
                seen.add(k)
                frontier.append((nxt, d + 1))
    return len(seen), transitions, violating, sorted(kinds), time.perf_counter() - began

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--issues", type=int, default=1); ap.add_argument("--workers", default="a,b")
    ap.add_argument("--depth", type=int, default=5); ap.add_argument("--split", action="store_true")
    ap.add_argument("--careless", action="store_true")
    a = ap.parse_args()
    explore.SPLIT, explore.CARELESS = a.split, a.careless
    w = tuple(a.workers.split(","))
    for prune, capped, history, symmetry in [(True, False, False, False), (False, False, False, False),
                                             (False, False, True, False), (False, True, True, False),
                                             (False, True, True, True)]:
        s, t, v, kinds, sec = run(tuple(range(1, a.issues + 1)), w, a.depth, prune, capped, history, symmetry)
        print(f"prune={prune!s:5} capped={capped!s:5} history={history!s:5} symmetry={symmetry!s:5}: "
              f"states {s:7d}  transitions {t:8d}  violating {v:6d} {kinds}  {sec:6.1f} s", flush=True)
