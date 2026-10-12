"""The coordinator's rules: which claims to release and what to say when it
does, and what to tell open work that meets other work in the same files.

The coordinator is a step of the board's own run, on the repository's own
token, so it needs no Claude account and no routine: before the board is
rendered, it releases every claim the release rule finds due, and after
the release it tells each piece of open work that changes a file another
piece changes which one should land first. What needs judgement, splitting
a request into issues, planning an epic, settling a blocked task, tidying
the shared memory, is Claude's, in the repository's coordinator workflow
or a coordinator routine, and is not here.

A release is the protocol's (spec/PROTOCOL.md, Release): a claim whose
branch has gone more than a day without a commit, whose issue is open and
not labelled in-review, and whose pull request, if it has one, is still a
draft. The coordinator posts one comment on the issue, whose first line is
claims.RELEASE_MARKER, saying why, whose the claim was, what its branch
holds and what its plan was, so the next worker can carry the work on or
start over; then it takes the label claimed and the holder's label off the
issue, and the board renames the branch in the same run. Like the board,
this module only decides and words; adapters/github.py makes the calls.
"""

import re
from typing import NamedTuple

from . import board, claims, labels

# The first line of the comment that tells a piece of open work about the
# other work it meets; the marker names every number it told of, so a later
# run tells again only when work it has not told of starts to meet it.
OVERLAP_MARKER = "<!-- sdlc-coordinator: overlap {numbers} -->"
OVERLAP_LINE = re.compile(r"<!-- sdlc-coordinator: overlap ([0-9 ]+) -->")
# How many commit subjects and files a release note names before it counts
# the rest, so a long-lived branch does not bury the comment.
FILES_NAMED = 20


# --- releases -----------------------------------------------------------------

def is_candidate(snap, key):
    """Whether a release can be due on the claim with this key: its branch
    exists, its issue is open and not labelled in-review, no pull request
    from its branch is marked ready, and its head commit and when it was
    committed are known."""
    issue, release = snap.claim_issues.get(key), snap.releases.get(key)
    if issue is None or release is None or not release.sha or not release.committed_at:
        return False
    if claims.branch_of(key) not in snap.branches or issue.state != "open" or labels.IN_REVIEW in issue.labels:
        return False
    return not any(p.branch == claims.branch_of(key) and not p.draft for p in snap.pulls)


def releases_due(snapshot, now, idle_after=claims.IDLE_AFTER):
    """The keys of the claims the coordinator releases now, in issue order:
    claims.release_due over every candidate, so a claim already released
    (claims.was_released) is not released twice, and one released too soon
    is released again."""
    snap = board.as_snapshot(snapshot)
    keys = [key for key in snap.releases if is_candidate(snap, key)]
    last_commits = {key: snap.releases[key].committed_at for key in keys}
    released = {key: snap.releases[key].released_at for key in keys}
    return claims.release_due(last_commits, now, idle_after, released)


class Holding(NamedTuple):
    """What a released branch holds, as a comparison with the default branch
    found it: how many commits it is ahead, the subject of its newest one
    and the files it changes; ahead is None when GitHub would not compare."""

    ahead: int | None = None
    last_subject: str = ""
    files: tuple = ()


def holder(snap, key):
    """Whose a claim was: the worker its claim file names, else the
    worker its issue's label names, else nobody known."""
    text = snap.claims.get(key)
    if text:
        named = claims.claim_worker(text)
        if named != claims.UNNAMED:
            return named
    issue = snap.claim_issues.get(key)
    return labels.worker_of({"labels": issue.labels} if issue else None) or "a worker nobody named"


def named_files(files):
    """Up to FILES_NAMED files, in code spans, and how many more there are."""
    shown = ", ".join(f"`{path}`" for path in files[:FILES_NAMED])
    rest = len(files) - FILES_NAMED
    return shown + (f" and {rest} more" if rest > 0 else "")


def holding_text(holding, default_branch):
    """What the branch holds, in one sentence."""
    files = board.own_files(holding.files)
    if holding.ahead is None:
        held = "GitHub would not compare the branch with the default branch, so look at the branch itself"
    else:
        held = f"{holding.ahead} commit{'s' if holding.ahead != 1 else ''} ahead of {default_branch}"
        if holding.last_subject:
            held += f", the newest \"{holding.last_subject}\""
    if files:
        return f"{held}; it changes {named_files(files)}."
    if holding.ahead is not None:
        return f"{held}; it changes nothing but its claim file."
    return held + "."


def release_text(snapshot, key, holding, idle_after=claims.IDLE_AFTER):
    """The comment that releases the claim with this key: the marker as
    its first line, then why, whose, what the branch holds, what the plan
    was, and how to carry the work on or undo the release."""
    snap = board.as_snapshot(snapshot)
    release = snap.releases[key]
    old, new = claims.branch_of(key), claims.released_branch(key, release.sha)
    hours = int(idle_after.total_seconds() // 3600)
    pull = next((p for p in snap.pulls if p.branch == old), None)
    pull_clause = f"its pull request #{pull.number} is still a draft" if pull else "it has no pull request"
    plan = claims.claim_plan(snap.claims.get(key) or "")
    return "\n".join([
        claims.RELEASE_MARKER,
        f"released: the claim on this issue, held by {holder(snap, key)}, has had no commit on {old} since "
        f"{release.committed_at}, more than {hours} hours, and {pull_clause}, so the coordinator released it. "
        f"The board renames the branch to {new}, which keeps the work, and the issue goes back to the queue.",
        "",
        f"What the branch holds: {holding_text(holding, snap.default_branch)}",
        f"The claim's plan: {plan}" if plan else "The claim file states no plan.",
        "",
        f"To carry the work on, claim the issue anew, a new {old} from {snap.default_branch}, and bring the "
        f"commits in from {new} after the claim commit; or start over. To undo the release, put the label "
        f"claimed back on the issue.",
    ]) + "\n"


def labels_released(snapshot, key):
    """The labels a release takes off the issue: claimed and every label
    that names a worker, so the issue is nobody's and, labelled todo, back
    in the open queue."""
    issue = board.as_snapshot(snapshot).claim_issues.get(key)
    names = issue.labels if issue else []
    return [name for name in names if name == labels.CLAIMED or name.startswith(labels.HOLDER_PREFIXES)]


def after_release(snapshot, key, released_at, taken_off):
    """The snapshot as GitHub knows it once the claim with this key is
    released: the comment made at released_at, the labels taken off its
    issue in both places the board reads them, so the same run renders the
    release and renames the branch without asking GitHub again. A Snapshot
    given is changed in place, and returned."""
    snap = board.as_snapshot(snapshot)
    release = snap.releases[key]
    snap.releases[key] = board.Release(released_at, release.sha, release.committed_at)
    issue = snap.claim_issues[key]
    issue.labels = [name for name in issue.labels if name not in taken_off]
    for listed in snap.issues:
        if str(listed.number) == key:
            listed.labels = [name for name in listed.labels if name not in taken_off]
    return snap


# --- overlaps -----------------------------------------------------------------

def meeting(snapshot):
    """For each piece of open work that meets other work in a file, by its
    number: the work it meets with the files they share, in landing order.
    """
    found = {}
    for path, works in board.overlaps(snapshot).items():
        for work in works:
            for other in works:
                if other.number != work.number:
                    found.setdefault(work.number, {}).setdefault(other, []).append(path)
    return {number: sorted(((other, tuple(sorted(paths))) for other, paths in others.items()),
                           key=lambda pair: board.landing_key(pair[0]))
            for number, others in sorted(found.items())}


def told_numbers(comments):
    """Every number an overlap comment among these has told of, read from
    the marker on its first line: a comment that quotes a marker further
    down talks about an overlap and told nothing."""
    told = set()
    for comment in comments:
        lines = ((comment or {}).get("body") or "").strip().splitlines()
        match = OVERLAP_LINE.fullmatch(lines[0].strip()) if lines else None
        if match:
            told.update(int(number) for number in match.group(1).split())
    return told


def describe(work):
    """A piece of work in a few words, for an overlap comment."""
    if not work.is_pull:
        kind = "a claim with no pull request yet"
    elif work.ready:
        kind = "a pull request marked ready"
    else:
        kind = "a draft pull request"
    return kind + (", for a blocker" if work.blocker else "")


def overlap_text(work, others):
    """The comment that tells a piece of open work about the other work it
    meets: each other piece with the files they share, and the order the
    whole group should land in."""
    order = sorted([work] + [other for other, _ in others], key=board.landing_key)
    this = "pull request" if work.is_pull else "claim"
    lines = [f"overlap: other open work changes the same files as this {this}:"]
    lines += [f"- #{other.number}, {describe(other)}: {named_files(list(paths))}" for other, paths in others]
    lines += ["", f"Land them in this order: {', '.join(f'#{w.number}' for w in order)}: a blocker first, then "
                  f"work marked ready, then the oldest. Each one after the first brings the default branch in "
                  f"once the one before it has landed, and the board's Overlaps section shows the order as it "
                  f"changes."]
    return "\n".join(lines) + "\n"


def overlap_comments_due(snapshot, told):
    """The overlap comments to post, as board.DueComment: one for each piece
    of open work that meets work it has not been told of, told maps a
    work's number to the numbers its earlier overlap comments named. The
    comment names all the work it meets, told before or not, so it reads
    whole on its own."""
    snap = board.as_snapshot(snapshot)
    works = {work.number: work for work in board.open_work(snap)}
    due = []
    for number, others in meeting(snap).items():
        numbers = sorted(other.number for other, _ in others)
        if set(numbers) <= told.get(number, set()):
            continue
        marker = OVERLAP_MARKER.format(numbers=" ".join(str(n) for n in numbers))
        due.append(board.DueComment(number, marker, overlap_text(works[number], others)))
    return due
