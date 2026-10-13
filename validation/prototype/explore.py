"""Prototype of the exhaustive small-scope checker: every interleaving of the
protocol's verbs for a few workers, up to a depth, each reached state checked
against invariants written from spec/PROTOCOL.md, a violation printed as the
shortest trace that reaches it (breadth first). Scratch only."""

import argparse
import time
from collections import deque

from model import BOT, OWNER, Comment, Pull, board_first_half, board_run, board_second_half, claim_text, claims, labels, seeded

MARKER = claims.RELEASE_MARKER
CARELESS = False
SPLIT = False
OVERLAP = "<!-- sdlc-coordinator: overlap "


# --- reading the board the way a worker does ------------------------------------------

def section_rows(text, heading_start):
    """The first cells of a board section's table rows, '(none)' left out."""
    lines = text.splitlines()
    rows, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = line.startswith(heading_start)
            continue
        if inside and line.startswith("| ") and not line.startswith("| Issue") and not line.startswith("| ---"):
            first = line.split("|")[1].strip()
            if first != "(none)":
                rows.append(line)
    return rows


def queue_numbers(text):
    return {int(r.split("|")[1].strip().lstrip("#")) for r in section_rows(text, "## Queue")}


def claim_branches_shown(text):
    return {r.split("|")[2].strip() for r in section_rows(text, "## Claims")}


# --- the oracle: the spec's rules restated over the model, not over the snapshot ------

def trusted(c, m):
    return c.association in ("OWNER", "MEMBER", "COLLABORATOR") or c.author == BOT or c.author in m.writers


def head_time(m, ref):
    return m.commits[m.refs[ref]].at


def finished(m, ref):
    pull = m.open_pull_from(ref)
    if pull is None:
        return False
    if not pull.draft:
        return True
    mine = [c for c in pull.comments if c.author == pull.author]
    return bool(mine) and mine[-1].body.lower().startswith(claims.FINISHED_SIGNAL)


def release_comments(m, n):
    issue = m.issues[n]
    return [c for c in issue.comments if c.body.splitlines()[0] == MARKER and trusted(c, m)]


def already_released(m, n, ref):
    head = head_time(m, ref)
    return any(c.at - head > 24 for c in release_comments(m, n))


def oracle_due(m, n):
    """spec/PROTOCOL.md, Release, first paragraph."""
    ref = f"claude/{n}"
    issue = m.issues.get(n)
    if ref not in m.refs or issue is None or issue.state != "open" or labels.IN_REVIEW in issue.labels:
        return False
    if finished(m, ref) or not m.now - head_time(m, ref) > 24:
        return False
    return not already_released(m, n, ref)


def oracle_released(m, ref):
    """spec/PROTOCOL.md, Release, second paragraph."""
    n = claims.number_of(ref)
    issue = m.issues.get(n)
    if issue is None or issue.state != "open" or {labels.CLAIMED, labels.IN_REVIEW} & issue.labels:
        return False
    pull = m.open_pull_from(ref)
    if pull is not None and not pull.draft:
        return False
    return already_released(m, n, ref)


def expected_queue(m):
    held = {claims.number_of(r) for r in m.refs if claims.is_claim_branch(r) and not oracle_released(m, r)}
    return {n for n, i in m.issues.items()
            if i.state == "open" and labels.TODO in i.labels and labels.CLAIMED not in i.labels
            and not labels.reserved(sorted(i.labels)) and n not in held}


# --- the verbs --------------------------------------------------------------------------

def holder_of(m, n):
    ref = f"claude/{n}"
    if ref not in m.refs:
        return None
    first = [sha for sha in m.ancestors(m.refs[ref]) if sha not in m.ancestors(m.refs["main"])]
    authors = {m.commits[sha].author for sha in first}
    return authors.pop() if len(authors) == 1 else (None if not authors else "several")


def enabled(m, workers):
    if m.pending is not None:
        acts = [("board_rename",)]   # one board run at a time: the workflow's concurrency group
    else:
        acts = [("wait",), ("board",), ("board_fault",), ("main_moves",)] + ([("board_collect",)] if SPLIT else [])
    shown = queue_numbers(m.board_text)
    for n in m.issues:
        ref = f"claude/{n}"
        holder = holder_of(m, n)
        pull = m.open_pull_from(ref) if ref in m.refs else None
        for w in workers:
            if n in shown and m.issues[n].state == "open":
                acts.append(("claim", w, n))
                if CARELESS:
                    acts.append(("claim_padded", w, n))
            if holder == w:
                acts.append(("work", w, n))
                if labels.CLAIMED not in m.issues[n].labels or labels.worker_label(w) not in m.issues[n].labels:
                    acts.append(("announce", w, n))
                if pull is None:
                    acts.append(("draft", w, n))
                elif pull.draft and pull.author == w:
                    acts.append(("ready", w, n))
                    acts.append(("finished", w, n))
        if pull is not None and not pull.draft:
            acts.append(("merge", n))
        if ref in m.refs and m.issues[n].state == "open" and labels.CLAIMED in m.issues[n].labels:
            acts.append(("release_by_hand", n))
        if m.issues[n].state == "open" and labels.CLAIMED not in m.issues[n].labels and release_comments(m, n):
            acts.append(("undo", n))
    return acts


def apply(m, act):
    """Apply one verb to a copy of the model; the copy and a note."""
    m = m.clone()
    kind = act[0]
    note = ""
    if kind == "wait":
        m.now += 25
    elif kind in ("board", "board_fault"):
        if kind == "board_fault":
            m.fail_next = "DELETE /issues/"
        note = "; ".join(board_run(m))
        m.fail_next = ""
    elif kind == "board_collect":
        board_first_half(m)
    elif kind == "board_rename":
        note = "; ".join(board_second_half(m))
    elif kind == "main_moves":
        main = m.refs["main"]
        tree = m.tree(main)
        tree["README.md"] = tree.get("README.md", "") + "more\n"
        m.refs["main"] = m.commit((main,), OWNER, tree, "elsewhere")
    elif kind == "claim":
        _, w, n = act
        ref = f"claude/{n}"
        if ref in m.refs:
            note = "push refused: the branch exists"
        else:
            tree = m.tree(m.refs["main"])
            tree[claims.claim_path(n)] = claim_text(n, w)
            m.set_ref(ref, m.commit((m.refs["main"],), w, tree, f"Claim #{n}"))
    elif kind == "claim_padded":
        _, w, n = act
        ref = f"claude/0{n}"
        if ref in m.refs:
            note = "push refused: the branch exists"
        else:
            tree = m.tree(m.refs["main"])
            tree[claims.claim_path(n)] = claim_text(n, w)
            m.set_ref(ref, m.commit((m.refs["main"],), w, tree, f"Claim #{n}"))
    elif kind == "announce":
        _, w, n = act
        issue = m.issues[n]
        others = {name for name in issue.labels if name.startswith(labels.HOLDER_PREFIXES)} - {labels.worker_label(w)}
        issue.labels = (issue.labels - others) | {labels.CLAIMED, labels.worker_label(w)}
        m.label_events += tuple((m.now, n, w, "+", name) for name in (labels.CLAIMED, labels.worker_label(w)))
        issue.comments += (Comment(w, "COLLABORATOR", m.now, f"Plan for #{n}"),)
    elif kind == "work":
        _, w, n = act
        ref = f"claude/{n}"
        tree = m.tree(m.refs[ref])
        tree[f"src/{n}.py"] = tree.get(f"src/{n}.py", "") + "x\n"
        tree["src/shared.py"] = tree.get("src/shared.py", "") + f"{n}\n"
        m.set_ref(ref, m.commit((m.refs[ref],), w, tree, f"work on #{n}"))
    elif kind == "draft":
        _, w, n = act
        m.pulls[m.next_number] = Pull(m.next_number, f"claude/{n}", w, m.now, body=f"Closes #{n}")
        m.next_number += 1
    elif kind == "ready":
        _, w, n = act
        ref = f"claude/{n}"
        tree = m.tree(m.refs[ref])
        tree.pop(claims.claim_path(n), None)
        m.set_ref(ref, m.commit((m.refs[ref],), w, tree, f"Finish #{n}"))
        m.open_pull_from(ref).draft = False
        m.issues[n].labels = m.issues[n].labels | {labels.IN_REVIEW}
    elif kind == "finished":
        _, w, n = act
        pull = m.open_pull_from(f"claude/{n}")
        pull.comments += (Comment(w, "COLLABORATOR", m.now, "finished; ready to mark"),)
    elif kind == "merge":
        _, n = act
        ref = f"claude/{n}"
        pull = m.open_pull_from(ref)
        main, head = m.refs["main"], m.refs[ref]
        tree = m.tree(main)
        base = m.tree(m.merge_base(head, main))
        for path in set(base) | set(m.tree(head)):
            if m.tree(head).get(path) != base.get(path):
                if path in m.tree(head):
                    tree[path] = m.tree(head)[path]
                else:
                    tree.pop(path, None)
        m.refs["main"] = m.commit((main, head), OWNER, tree, f"Merge #{pull.number}")
        pull.state, pull.merged_at = "merged", m.now
        m.issues[n].state = "closed"
        del m.refs[ref]
    elif kind == "release_by_hand":
        _, n = act
        issue = m.issues[n]
        issue.comments += (Comment(OWNER, "OWNER", m.now, MARKER + "\nreleased by hand"),)
        taken = {name for name in issue.labels if name == labels.CLAIMED or name.startswith(labels.HOLDER_PREFIXES)}
        issue.labels = issue.labels - taken
        m.label_events += tuple((m.now, n, OWNER, "-", name) for name in sorted(taken))
    elif kind == "undo":
        _, n = act
        m.issues[n].labels = m.issues[n].labels | {labels.CLAIMED}
        m.label_events += ((m.now, n, OWNER, "+", labels.CLAIMED),)
    return m, note


# --- the invariants ------------------------------------------------------------------

def state_problems(m):
    problems = []
    for n in m.issues:
        refs = [r for r in m.refs if claims.number_of(r) == n]
        if len(refs) > 1:
            problems.append(f"S1 two branches claim #{n}: {refs}")
    problems += told_twice(m)
    lost = m.ever_heads - m.reachable()
    if lost:
        problems.append(f"S2 work lost: {sorted(sha[:7] for sha in lost)}")
    return problems


def identity(m, number):
    """The issue a piece of work belongs to: a pull request's head branch's
    issue, or the issue itself."""
    pull = m.pulls.get(number)
    return claims.number_of(pull.head) if pull else number


def told_twice(m):
    """S8: a piece of work told again of work it had been told of, counting a
    claim and the pull request it opens as one piece."""
    problems = []
    told = {}
    places = [(n, i.comments) for n, i in m.issues.items()] + [(p.number, p.comments) for p in m.pulls.values()]
    for number, comments in sorted(places):
        for c in comments:
            first = c.body.splitlines()[0]
            if not first.startswith(OVERLAP):
                continue
            named = {identity(m, int(x)) for x in first[len(OVERLAP):].split(" -->")[0].split()}
            work = identity(m, number)
            if named <= told.get(work, set()):
                problems.append(f"S8 #{work}'s work was told again of {sorted(named)} (on #{number})")
            told.setdefault(work, set()).update(named)
    return problems


def half_released(m):
    """Claims whose release comment stands, with no commit since, whose label
    claimed nobody took off and nobody put back: a release no rule completes."""
    stuck = []
    for n, issue in m.issues.items():
        ref = f"claude/{n}"
        if ref not in m.refs or issue.state != "open" or labels.CLAIMED not in issue.labels:
            continue
        head = head_time(m, ref)
        for c in release_comments(m, n):
            if c.at - head <= 24:
                continue
            events = [e for e in m.label_events if e[1] == n and e[0] >= c.at and e[4] == labels.CLAIMED]
            if not events:
                stuck.append(n)
    return stuck


def transition_problems(before, after, act):
    """What a board run must have done, by the oracle."""
    if act[0] not in ("board", "board_fault"):
        return []
    problems = []
    due = {n for n in before.issues if oracle_due(before, n)}
    posted = {n for n in after.issues
              if sum(1 for c in release_comments(after, n) if c.author == BOT) > sum(1 for c in release_comments(before, n) if c.author == BOT)}
    if posted - due:
        problems.append(f"T1 released a claim that was not due: {sorted(posted - due)}")
    if act[0] == "board":
        if due - posted:
            problems.append(f"T2 a due claim was not released: {sorted(due - posted)}")
        for n in due:
            if f"claude/{n}" in after.refs:
                problems.append(f"T2 a released claim kept its name: claude/{n}")
        shown, expected = claim_branches_shown(after.board_text), {r for r in after.refs if claims.is_claim_branch(r) and not oracle_released(after, r)}
        if shown != expected:
            problems.append(f"T3 the board's claims {sorted(shown)} are not the oracle's {sorted(expected)}")
        if queue_numbers(after.board_text) != expected_queue(after):
            problems.append(f"T3 the board's queue {sorted(queue_numbers(after.board_text))} is not the oracle's {sorted(expected_queue(after))}")
        again = after.clone()
        lines = board_run(again)
        if again.key() != after.key() or again.board_text != after.board_text:
            problems.append(f"T4 a second board run with nothing new changed something: {lines}")
        stuck = half_released(after)
        if stuck:
            problems.append(f"L1 a release no rule completes: #{stuck}")
    return problems


# --- the search -----------------------------------------------------------------------

def explore(issues, workers, depth, stop_at_first=True):
    start = seeded(issues, workers)
    frontier = deque([(start, ())])
    seen = {start.key()}
    found, transitions = [], 0
    began = time.perf_counter()
    while frontier:
        m, trace = frontier.popleft()
        if len(trace) >= depth:
            continue
        for act in enabled(m, workers):
            nxt, note = apply(m, act)
            transitions += 1
            problems = state_problems(nxt) + transition_problems(m, nxt, act)
            if problems:
                found.append((trace + (act,), problems))
                if stop_at_first:
                    return found, len(seen), transitions, time.perf_counter() - began
                continue
            key = nxt.key()
            if key not in seen:
                seen.add(key)
                frontier.append((nxt, trace + (act,)))
    return found, len(seen), transitions, time.perf_counter() - began


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issues", type=int, default=1)
    parser.add_argument("--workers", default="a,b")
    parser.add_argument("--depth", type=int, default=6)
    parser.add_argument("--all", action="store_true", help="keep going after the first violation")
    parser.add_argument("--careless", action="store_true", help="workers may push claude/0<n>")
    parser.add_argument("--split", action="store_true", help="other verbs may happen inside a board run")
    args = parser.parse_args()
    workers = tuple(args.workers.split(","))
    global CARELESS
    CARELESS = args.careless
    global SPLIT
    SPLIT = args.split
    found, states, transitions, seconds = explore(tuple(range(1, args.issues + 1)), workers, args.depth, not args.all)
    print(f"states {states}, transitions {transitions}, {seconds:.1f} s, violations {len(found)}")
    kinds = {}
    for trace, problems in found:
        for p in problems:
            kinds.setdefault(p.split(":")[0], (trace, p))
    for kind, (trace, p) in sorted(kinds.items()):
        print(f"- {p}\n  trace: {' -> '.join('/'.join(map(str, a)) for a in trace)}")


if __name__ == "__main__":
    main()
