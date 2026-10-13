"""A small mutation run against the prototype checker: each mutant breaks one
rule of the library the way a careless change could, and the checker either
reports something the unmutated library does not (caught) or not (slipped).
Scratch only; patches modules in this process, never the files."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the prototype's own modules
import explore_variants
from sdlc_protocol import board, claims, coordinator, labels
from sdlc_protocol.adapters import github
from datetime import timedelta

def m_idle_ge():
    def is_idle(last_change, now, idle_after=claims.IDLE_AFTER):
        return now - claims.parse_time(last_change) >= idle_after
    claims.is_idle = board.is_idle = is_idle

def m_keep_worker_label():
    coordinator.labels_released = lambda snapshot, key: [n for n in (board.as_snapshot(snapshot).claim_issues[key].labels) if n == labels.CLAIMED]

def m_ignore_claimed_in_release():
    orig = board.is_released
    def is_released(snap, key, idle_after=claims.IDLE_AFTER):
        release, issue = snap.releases.get(key), snap.claim_issues.get(key)
        if release is None or issue is None or not release.sha:
            return False
        if issue.state != "open" or labels.IN_REVIEW in issue.labels:
            return False
        if any(p.branch == claims.branch_of(key) and not p.draft for p in snap.pulls):
            return False
        return claims.was_released(release.released_at, release.committed_at, idle_after)
    board.is_released = is_released

def m_release_ready_prs():
    def is_candidate(snap, key):
        issue, release = snap.claim_issues.get(key), snap.releases.get(key)
        if issue is None or release is None or not release.sha or not release.committed_at:
            return False
        return claims.branch_of(key) in snap.branches and issue.state == "open"
    coordinator.is_candidate = is_candidate
    github.ready_branches = lambda pulls: set()

def m_idle_comment_to_released():
    orig = board.released_branches
    def comments_due(snapshot, event_name, ref_name, now, idle_after=claims.IDLE_AFTER):
        snap = board.as_snapshot(snapshot)
        return [board.DueComment(p.number, board.IDLE_MARKER, board.IDLE_TEXT) for p in board.idle_pulls(snap.pulls, now, idle_after)]
    board.comments_due = comments_due

def m_tell_overlaps_every_run():
    coordinator.overlap_comments_due.__globals__  # keep the name
    orig = coordinator.overlap_comments_due
    coordinator.overlap_comments_due = lambda snapshot, told: orig(snapshot, {})

def m_any_later_comment_releases():
    def was_released(released_at, last_commit, idle_after=claims.IDLE_AFTER):
        if not released_at or not last_commit:
            return False
        return claims.parse_time(released_at) > claims.parse_time(last_commit)
    claims.was_released = board.was_released = was_released

def m_idle_after_12h():
    twelve = timedelta(hours=12)
    orig = claims.release_due
    claims.release_due = lambda c, now, idle_after=twelve, released=None: orig(c, now, twelve, released)

MUTANTS = [None, m_idle_ge, m_keep_worker_label, m_ignore_claimed_in_release, m_release_ready_prs,
           m_idle_comment_to_released, m_tell_overlaps_every_run, m_any_later_comment_releases, m_idle_after_12h]

if __name__ == "__main__":
    import importlib
    name = sys.argv[1]
    issues = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    depth = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    mutant = None if name == "none" else globals()[name]
    if mutant:
        mutant()
    s, t, v, kinds, sec = explore_variants.run(tuple(range(1, issues + 1)), ("a", "b"), depth, False, False, True, False)
    print(f"{name:32s} issues={issues} depth={depth}: violating {v:6d} kinds {kinds}  ({s} states, {sec:.1f} s)", flush=True)
