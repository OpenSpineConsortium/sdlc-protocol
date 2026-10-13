"""Estimate how the state count grows when the scope carries more of the verbs
the design's section 6.2 lists than the prototype has: bring main in by a
merge, a stranger's marker comment, a second fault kind (the release comment's
POST) and closing the issue without a merge. Scratch only."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore, explore_variants
from model import Comment, board_run, claims
base_enabled, base_apply = explore.enabled, explore.apply
RAISED = []

def enabled(m, workers):
    acts = base_enabled(m, workers)
    if m.pending is None:
        acts.append(("board_fault_post",))
    for n in m.issues:
        ref = f"claude/{n}"
        holder = explore.holder_of(m, n)
        if ref in m.refs and holder in workers and not m.is_ancestor(m.refs["main"], m.refs[ref]):
            acts.append(("bring_main", holder, n))
        if m.issues[n].state == "open":
            acts.append(("stranger_marker", n))
            acts.append(("close_issue", n))
    return acts

def apply(m, act):
    kind = act[0]
    if kind == "board_fault_post":
        m = m.clone(); m.fail_next = "POST /issues/"
        try:
            note = "; ".join(board_run(m))
        except Exception as raised:
            RAISED.append(type(raised).__name__ + ": " + str(raised))
            note = "RAISED"
        m.fail_next = ""
        return m, note
    if kind == "bring_main":
        m = m.clone(); _, w, n = act; ref = f"claude/{n}"
        tree = m.tree(m.refs["main"]); tree.update(m.tree(m.refs[ref]))
        m.set_ref(ref, m.commit((m.refs[ref], m.refs["main"]), w, tree, "Merge main"))
        return m, ""
    if kind == "stranger_marker":
        m = m.clone(); n = act[1]
        m.issues[n].comments += (Comment("stranger", "NONE", m.now, claims.RELEASE_MARKER + "\nmine now"),)
        return m, ""
    if kind == "close_issue":
        m = m.clone(); m.issues[act[1]].state = "closed"
        return m, ""
    return base_apply(m, act)

explore_variants.enabled, explore_variants.apply = enabled, apply
for depth in (4, 5, 6):
    for prune, capped, history, symmetry in [(False, False, True, False), (False, True, True, True)]:
        s, t, v, kinds, sec = explore_variants.run((1,), ("a", "b"), depth, prune, capped, history, symmetry)
        print(f"  board runs that raised: {len(RAISED)}, e.g. {RAISED[:1]}"); RAISED.clear()
        print(f"depth {depth} capped+symmetry={capped}: states {s:7d} transitions {t:8d} violating {v:6d} {kinds} {sec:6.1f} s", flush=True)
