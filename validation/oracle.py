"""The oracle: spec/PROTOCOL.md's rules restated over the model (DESIGN.md, 2.2 and 2.6).

Every term here is read from the spec's text, over what RepoModel holds:
refs, trees, labels, comments and the label events. Nothing here reads a
board.Snapshot or imports from sdlc_protocol, not even a constant: the
marker, the branch names, the label names, "finished; ready to mark", the
overlap marker and the 24 hours are spelled as the spec spells them, and
tests/test_validation_guarantees.py checks that each, and each sentence
SPEC quotes, is in spec/PROTOCOL.md word for word. So a change to a
library constant shows here as a disagreement, and a change to the spec
breaks that test until this file follows.

The oracle answers two kinds of question: what the state is, by the spec
(due, released, the Queue, the claims the board lists, the overlaps), and
what a board run must do from a state (expected_effects), as Effect
records the invariants compare with the writes the run made.

A finding the library does not yet answer is a switch, in known: with its
id in known, the oracle reads that rule as the library is built, so the
search goes on past it and reports it once, as known; without it, the
oracle reads the spec, and the finding shows as a disagreement. AS_BUILT
holds the open findings the board's run can reach (DESIGN.md, 13), read
from validation/known.json since build step 5 (known.py): F2,
overlapping work told again once a claim opens its pull request; F9, a
draft never told "main moved"; F13, the landing order's "oldest" read as
the lowest number; F14, Given to workers needing the label todo and
leaving epics out. F12, "main moved" told only in the run the push to
main started, was a switch until its fix, which tells it in whichever
run finds the branch behind. F13 and F14 are what the harness of build step 1 found when its
oracle was held to the spec's words (DESIGN.md, 13).
"""

import re
from typing import NamedTuple

from .known import AS_BUILT
from .model import BOT, HOUR, MAIN, Comment

# --- the spec's words -------------------------------------------------------

SPEC = {
    "claim branch": "The claim branch of issue N is exactly `claude/N`, with N in one to nine ASCII digits and no "
                    "leading zero",
    "no claim": "`claude/03` is no claim on #3. The board shows such a branch as not a claim, and the push guard "
                "refuses a push to one",
    "claim file": "whose first commit adds `docs/agents/claims/<issue number>.md`",
    "files to touch": "## Files to touch",
    "worker label": "its label is `worker:<name>`",
    "older spellings": "The older spellings account:<name> and agent:<name> are read as the same label.",
    "given": "An issue a worker's label names is that worker's alone: it is left out of the board's Queue and "
             "shown under Given to workers until the worker claims it.",
    "todo": "An issue labelled todo and shown unclaimed on the board is free to take.",
    "idle": "A claim whose branch has had no commit for more than 24 hours, by the committer date of its head, is "
            "released by the coordinator, unless it is finished",
    "finished": "an issue labelled in-review, a pull request marked ready, or a draft whose author's latest comment "
                "on it starts with \"finished; ready to mark\" waits for review however long it takes.",
    "order": "The coordinator first takes the label claimed and every worker's label off the issue, a label "
             "already gone counting as taken off; then it posts a comment on the issue whose first line is "
             "`<!-- sdlc-coordinator: claim-released -->`",
    "put back": "A run that fails between the two puts back the labels it took off",
    "candidates": "It takes its candidates from the open issues that have a branch `claude/<issue>`, whatever "
                  "their labels, and skips a claim whose latest release comment came more than 24 hours after "
                  "the branch's last commit, which is a claim already released",
    "released": "A claim counts as released when its issue is open, labelled neither claimed nor in-review, its "
                "pull request, if any, is still a draft, and its latest release comment came more than 24 hours "
                "after the branch's last commit, with no commit since.",
    "trusted": "A release comment counts when it is by the repository's owner, a member of its organization or a "
               "collaborator, by the repository's own workflows (the bot `github-actions[bot]`, which only a "
               "workflow someone who may write put there can write as), or by anyone else who may write to the "
               "repository.",
    "rename": "renames the branch to `released/<issue>-<short sha of its head>`",
    "pushed again": "A branch pushed again at the head a release already kept is deleted instead, since its work "
                    "is already kept.",
    "overlap": "the coordinator tells each piece of open work about the work it meets, once, in a comment whose "
               "first line is `<!-- sdlc-coordinator: overlap <numbers> -->` naming every piece it told of",
    "told again": "tells it again only when work it has not been told of starts to meet it",
    "open work": "an open pull request by its files or, while it changes nothing but its claim file, by the files "
                 "its claim lists, and a claim with no pull request yet by the files its claim lists",
    "landing order": "a blocker first, since it stops everyone; then work marked ready, which waits only for its "
                     "merge; then the oldest",
    "rename refused": "When GitHub refuses the rename for good, for a ruleset whose target takes in the claim "
                      "branches or a token without the right, the board says so once on the issue",
}

CLAIM_PREFIX = "claude/"
CLAIM_NUMBER = re.compile(r"[1-9][0-9]{0,8}")
CLAIM_DIR = "docs/agents/claims/"
FILES_HEADING = "## Files to touch"
TODO, CLAIMED, IN_REVIEW, BLOCKER, EPIC = "todo", "claimed", "in-review", "blocker", "epic"
WORKER_SPELLINGS = ("worker:", "account:", "agent:")
IDLE_MINUTES = 24 * HOUR
FINISHED = "finished; ready to mark"
RELEASE_MARKER = "<!-- sdlc-coordinator: claim-released -->"
OVERLAP_MARKER = re.compile(r"<!-- sdlc-coordinator: overlap ([0-9 ]+) -->")
RELEASED_PREFIX = "released/"
# GitHub's author_association words for the owner, a member and a
# collaborator: the API's spelling of the spec's "owner, a member of its
# organization or a collaborator".
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")

# The board's own words, which the spec does not spell: the markers of the
# comments the board posts on pull requests and issues. They are the
# library's interface (its CLAUDE.md: a rendered string is part of it), and
# tests/test_validation_guarantees.py pins them to board.py, so a change to
# one is made on purpose in both places.
BOARD_MARKERS = {
    "main-moved": "<!-- sdlc-board: main-moved {sha} -->",
    "idle": "<!-- sdlc-board: idle -->",
    "released": "<!-- sdlc-board: released {branch} -->",
    "rename-refused": "<!-- sdlc-board: rename-refused {branch} -->",
}

# The answers GitHub gives a rename it refuses for good (DESIGN.md, G10): a
# rule on the branch, or a token without the right; held to the library's
# own list by tests/test_validation_guarantees.py.
REFUSED_FOR_GOOD = frozenset({403, 422})

# The open findings the oracle reads as the library is built are
# known.json's "as_built", imported above as AS_BUILT.


class Effect(NamedTuple):
    """One write a board run makes, by kind and target: ("label-off", 1,
    "claimed"), ("comment", 1, "claim-released"), ("rename", "claude/1",
    "released/1-abc1234"), ("delete-ref", "claude/1", "")."""

    kind: str
    target: object
    detail: object = ""


# --- terms ------------------------------------------------------------------

def claim_number(branch):
    """The issue a branch claims, by the spec's spelling, or None."""
    key = branch[len(CLAIM_PREFIX):] if branch.startswith(CLAIM_PREFIX) else None
    return int(key) if key is not None and CLAIM_NUMBER.fullmatch(key) else None


def reads_as_claim(branch):
    """A branch that reads as a claim and claims nothing: digits after
    claude/, of any script, that are not N as SPEC["claim branch"] spells
    it, as claude/03 is no claim on #3."""
    key = branch[len(CLAIM_PREFIX):] if branch.startswith(CLAIM_PREFIX) else ""
    return bool(key) and key.isdigit() and not CLAIM_NUMBER.fullmatch(key)


def branch_of(number):
    return f"{CLAIM_PREFIX}{number}"


def first_line(body):
    lines = (body or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def is_worker_label(name):
    return any(name.startswith(prefix) and name[len(prefix):] for prefix in WORKER_SPELLINGS)


def workers_named(labels):
    found = []
    for name in sorted(labels):
        for prefix in WORKER_SPELLINGS:
            if name.startswith(prefix) and name[len(prefix):] and name[len(prefix):] not in found:
                found.append(name[len(prefix):])
    return found


def trusted(model, comment):
    """SPEC["trusted"]."""
    return (comment.association in TRUSTED_ASSOCIATIONS or comment.author == BOT
            or comment.author in model.writers)


def head_time(model, ref):
    return model.commits[model.refs[ref]].committed_at


def idle(since, now):
    """More than 24 hours (SPEC["idle"])."""
    return now - since > IDLE_MINUTES


def release_times(model, number):
    """When each release comment that counts was made on the issue."""
    issue = model.issues.get(number)
    return [c.at for c in (issue.comments if issue else ()) if first_line(c.body) == RELEASE_MARKER
            and trusted(model, c)]


def latest_release(model, number):
    times = release_times(model, number)
    return max(times) if times else None


def already_released(model, number):
    """SPEC["candidates"]: the latest release comment came more than 24
    hours after the branch's last commit."""
    latest = latest_release(model, number)
    return latest is not None and idle(head_time(model, branch_of(number)), latest)


def finished(model, number):
    """SPEC["finished"]."""
    issue, pull = model.issues.get(number), model.open_pull_from(branch_of(number))
    if issue is not None and IN_REVIEW in issue.labels:
        return True
    if pull is None:
        return False
    if not pull.draft:
        return True
    theirs = [c for c in pull.comments if c.author == pull.author]
    return bool(theirs) and max(theirs, key=lambda c: c.at).body.strip().startswith(FINISHED)


def due(model, number, now=None):
    """Whether the coordinator owes this claim a release now (SPEC["idle"],
    SPEC["candidates"])."""
    now = model.now if now is None else now
    ref, issue = branch_of(number), model.issues.get(number)
    if ref not in model.refs or issue is None or issue.state != "open" or finished(model, number):
        return False
    return idle(head_time(model, ref), now) and not already_released(model, number)


def released(model, number):
    """SPEC["released"]."""
    ref, issue = branch_of(number), model.issues.get(number)
    if ref not in model.refs or issue is None or issue.state != "open":
        return False
    if CLAIMED in issue.labels or IN_REVIEW in issue.labels:
        return False
    pull = model.open_pull_from(ref)
    if pull is not None and not pull.draft:
        return False
    return already_released(model, number)


def held_numbers(model):
    """The issues a claim branch holds: every canonical claim branch that is
    not released."""
    return {n for ref in model.refs if (n := claim_number(ref)) is not None and not released(model, n)}


def queue(model):
    """The Queue: open issues labelled todo, unclaimed, and given to no
    worker (SPEC["todo"], SPEC["given"])."""
    held = held_numbers(model)
    return sorted(n for n, i in model.issues.items() if i.state == "open" and TODO in i.labels
                  and CLAIMED not in i.labels and n not in held and not workers_named(i.labels))


def given(model, known=AS_BUILT):
    """Given to workers: open issues a worker's label names, not yet
    claimed, with the workers named (SPEC["given"]). The spec's sentence
    asks for no other label; with F14 known, the issue must carry todo and
    not epic, as the library reads it."""
    held = held_numbers(model)
    return {n: workers_named(i.labels) for n, i in model.issues.items()
            if i.state == "open" and CLAIMED not in i.labels and n not in held and workers_named(i.labels)
            and ("F14" not in known or (TODO in i.labels and EPIC not in i.labels))}


def claims_listed(model):
    """The branches the board's Claims section lists: every branch under
    claude/ but a released claim's."""
    return sorted(ref for ref in model.refs if ref.startswith(CLAIM_PREFIX)
                  and not ((n := claim_number(ref)) is not None and released(model, n)))


def released_name(number, sha):
    """SPEC["rename"], with the short sha git's seven characters."""
    return f"{RELEASED_PREFIX}{number}-{sha[:7]}"


def labels_released(model, number):
    """The labels a release takes off: claimed and every worker's label in
    the three spellings, the ones still on."""
    return sorted(n for n in model.issues[number].labels if n == CLAIMED or is_worker_label(n))


# --- overlaps -----------------------------------------------------------------

class Piece(NamedTuple):
    """One piece of open work: its identity (the issue a claim's work is
    for, else the pull request's number), the number it is told on and
    shown by now, its files, the landing order's two words, and the minute
    the work began: the push that made its branch, or the opening of a
    pull request from a branch that is no claim."""

    identity: int
    number: int
    files: tuple
    ready: bool
    blocker: bool
    began: int = 0


def listed_files(text):
    """The paths a claim file lists under SPEC["files to touch"]."""
    lines, inside = [], False
    for line in (text or "").splitlines():
        if line.strip() == FILES_HEADING:
            inside = True
            continue
        if inside and line.startswith("#"):
            break
        if inside and line.strip().startswith("- "):
            lines.append(line.strip()[2:].strip())
    return sorted(lines)


def own(paths):
    return tuple(sorted(p for p in paths if not p.startswith(CLAIM_DIR)))


def claim_file_text(model, ref, number):
    return model.tree(model.refs.get(ref, "")).get(f"{CLAIM_DIR}{number}.md")


def open_work(model, known=AS_BUILT):
    """Every piece of open work (SPEC["open work"]) but a released claim's.
    A claim and the pull request from its branch are one piece, which
    keeps the issue's number as its identity; with F2 known, the identity
    is the number it is shown by, as the library reads it."""
    pieces = []
    pulled = set()
    for pull in sorted(model.pulls.values(), key=lambda p: p.number):
        if pull.state != "open":
            continue
        number = claim_number(pull.head)
        if number is not None and released(model, number):
            continue
        pulled.add(pull.head)
        listed = listed_files(claim_file_text(model, pull.head, number)) if number is not None else []
        files = own(model.files_changed(pull.head)) or own(listed)
        identity = number if number is not None and "F2" not in known else pull.number
        began = model.created.get(pull.head, pull.opened_at)
        pieces.append(Piece(identity, pull.number, files, not pull.draft, is_blocker(model, number), began))
    for ref in sorted(model.refs):
        number = claim_number(ref)
        if number is None or ref in pulled or released(model, number):
            continue
        files = own(listed_files(claim_file_text(model, ref, number)))
        pieces.append(Piece(number, number, files, False, is_blocker(model, number), model.created.get(ref, 0)))
    return pieces


def is_blocker(model, number):
    issue = model.issues.get(number)
    return issue is not None and BLOCKER in issue.labels


def landing(piece, known=AS_BUILT):
    """SPEC["landing order"]: "the oldest" is the work that began first,
    the number breaking a tie; with F13 known, the lowest number, as the
    library reads it, an issue's and a pull request's alike."""
    age = piece.number if "F13" in known else piece.began
    return (not piece.blocker, not piece.ready, age, piece.number)


def overlaps(model, known=AS_BUILT):
    """Each file more than one piece changes, with the numbers the pieces
    are shown by, in landing order."""
    meeting = {}
    for piece in open_work(model, known):
        for path in piece.files:
            meeting.setdefault(path, []).append(piece)
    return {path: [p.number for p in sorted(pieces, key=lambda p: landing(p, known))]
            for path, pieces in sorted(meeting.items()) if len(pieces) > 1}


def told_of(model, piece, by_number, known=AS_BUILT):
    """The identities a piece has been told of, from the overlap markers by
    trusted authors on every number the piece has been shown by; with F2
    known, on its current number alone, numbers as written."""
    places = {piece.number}
    if "F2" not in known:
        places |= {piece.identity} | {p.number for p in model.pulls.values() if claim_number(p.head) == piece.identity}
    told = set()
    for place in places:
        for comment in model.comments_on(place):
            match = OVERLAP_MARKER.fullmatch(first_line(comment.body))
            if match and trusted(model, comment):
                told |= {by_number.get(int(n), int(n)) for n in match.group(1).split()}
    return told


def overlap_effects(model, known=AS_BUILT):
    pieces = open_work(model, known)
    by_number = {p.number: p.identity for p in pieces}
    if "F2" not in known:
        for pull in model.pulls.values():
            if claim_number(pull.head) is not None:
                by_number.setdefault(pull.number, claim_number(pull.head))
    effects = []
    for piece in sorted(pieces, key=lambda p: p.number):
        meets = {other.identity for other in pieces if other.identity != piece.identity
                 and set(other.files) & set(piece.files)}
        if meets and not meets <= told_of(model, piece, by_number, known):
            effects.append(Effect("comment", piece.number, "overlap"))
    return effects


# --- comments on pull requests ----------------------------------------------

def has_marker(model, number, marker):
    return any(first_line(c.body) == marker for c in model.comments_on(number))


def short_sha(sha):
    return sha[:7]


def main_moved_effects(model, event, ref_name, known=AS_BUILT):
    """Every open pull request but a released claim's that is behind or
    conflicting with main hears so once for main's commit, in whichever
    run finds it so (DESIGN.md, G10; the event and the ref no longer
    decide, since F12's fix); with F9 known, never a draft."""
    marker = BOARD_MARKERS["main-moved"].format(sha=short_sha(model.refs[MAIN]))
    found = []
    for pull in pulls_spoken_to(model):
        if "F9" in known and pull.draft:
            continue
        if model.standing(pull.head) in ("behind", "dirty") and not has_marker(model, pull.number, marker):
            found.append(Effect("comment", pull.number, "main-moved"))
    return found


def idle_effects(model):
    marker = BOARD_MARKERS["idle"]
    return [Effect("comment", p.number, "idle") for p in pulls_spoken_to(model)
            if idle(p.updated_at, model.now) and not has_marker(model, p.number, marker)]


def pulls_spoken_to(model):
    """The open pull requests the board speaks to: all but a released
    claim's, which the rename closes."""
    return [p for p in sorted(model.pulls.values(), key=lambda p: p.number) if p.state == "open"
            and not ((n := claim_number(p.head)) is not None and released(model, n))]


# --- what a board run must do -------------------------------------------------

def expected_effects(before, event="schedule", ref_name="", known=AS_BUILT):
    """The writes a fault-free board run owes from the state before it: the
    releases due, in the spec's order, the overlap comments, the renames of
    the released claims and the word on each issue, then the comments on
    pull requests. The oracle applies its own releases to a copy to see
    what the rest of the run reads."""
    model = before.clone()
    effects = []
    for number in sorted(n for n in model.issues if due(model, n)):
        for name in labels_released(model, number):
            effects.append(Effect("label-off", number, name))
            model.issues[number].labels = model.issues[number].labels - {name}
        effects.append(Effect("comment", number, "claim-released"))
        model.issues[number].comments += (Comment(BOT, "NONE", model.now, RELEASE_MARKER),)
    effects += overlap_effects(model, known)
    spoken = pulls_spoken_to(model)
    for number in sorted(n for ref in model.refs if (n := claim_number(ref)) is not None and released(model, n)):
        ref, sha = branch_of(number), model.refs[branch_of(number)]
        new = released_name(number, sha)
        if model.refs.get(new) == sha:
            effects.append(Effect("delete-ref", ref))
        elif new not in model.refs:
            effects.append(Effect("rename", ref, new))
        else:
            continue
        if not has_marker(model, number, BOARD_MARKERS["released"].format(branch=new)):
            effects.append(Effect("comment", number, "released"))
    effects += [e for e in main_moved_effects(model, event, ref_name, known) if e.target in {p.number for p in spoken}]
    effects += [e for e in idle_effects(model) if e.target in {p.number for p in spoken}]
    return effects


def rename_refused_effects(before, refused):
    """SPEC["rename refused"]: for each rename of a claim branch GitHub
    refused for good in a run, given as (old name, new name, status), one
    comment on the issue, unless one for that new name stands already."""
    effects = []
    for old, new, status in refused:
        number = claim_number(old)
        if status in REFUSED_FOR_GOOD and number is not None \
                and not has_marker(before, number, BOARD_MARKERS["rename-refused"].format(branch=new)):
            effects.append(Effect("comment", number, "rename-refused"))
    return effects


def comment_kind(body):
    """Which of the run's comments a body is, by its first line."""
    line = first_line(body)
    if line == RELEASE_MARKER:
        return "claim-released"
    if OVERLAP_MARKER.fullmatch(line):
        return "overlap"
    for kind, marker in BOARD_MARKERS.items():
        start = marker.partition("{")[0]
        if line == marker or ("{" in marker and line.startswith(start) and line.endswith(" -->")):
            return kind
    return "other"


def half_released(model):
    """DESIGN.md, G3L: claims whose release comment by the board's bot
    stands, more than 24 hours after the last commit and with no commit
    since, whose label claimed is still on and was neither taken off nor
    put back since the comment: a release no rule completes."""
    stuck = []
    for number, issue in sorted(model.issues.items()):
        ref = branch_of(number)
        if ref not in model.refs or issue.state != "open" or CLAIMED not in issue.labels:
            continue
        head = head_time(model, ref)
        for comment in issue.comments:
            if comment.author != BOT or first_line(comment.body) != RELEASE_MARKER or not idle(head, comment.at):
                continue
            if not any(e.issue == number and e.label == CLAIMED and e.at >= comment.at for e in model.label_events):
                stuck.append(number)
                break
    return stuck
