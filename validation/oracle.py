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
overlapping work told again once a claim opens its pull request. Four
were switches until their fixes: F12, "main moved" told only in the run
the push to main started, which is now told in whichever run finds the
branch behind; F14, Given to workers needing the label todo and leaving
epics out, which now shows every open, unclaimed issue a worker's label
names; F9, a draft never told "main moved", which the adapter now reads
through mergeable and the comparison where GitHub's word is opaque, as
it reads a blocked one; and F13, the landing order's "oldest" read as
the lowest number, which now reads when the work began, as GitHub
records the push that made its branch. F13 and F14 are what the
harness of build step 1 found when its oracle was held to the spec's
words (DESIGN.md, 13).
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
    "todo": "An issue labelled todo and shown unclaimed on the board is free to take as Pick says.",
    "epic": "The board's Queue leaves an epic out, since nobody takes one whole.",
    "blockers": "The board lists every open issue labelled blocker under Blockers, claimed or not",
    "idle": "A claim whose branch has had no commit for more than 24 hours, by the committer date of its head, is "
            "released by the coordinator, unless it is finished",
    "finished": "an issue labelled in-review, a pull request marked ready, or a draft whose author's latest comment "
                "on it starts with \"finished; ready to mark\" waits for review however long it takes.",
    "order": "The coordinator first takes the label claimed and every worker's label off the issue, a label "
             "already gone counting as taken off, and puts the label todo on it when it carries none of todo, "
             "epic and blocker, so the Queue lists it again even when a worker's label alone gave it to the "
             "worker, while a blocker is listed under Blockers without it; then it posts a comment on the issue "
             "whose first line is `<!-- sdlc-coordinator: claim-released -->`",
    "put back": "A run that fails between the two puts back the labels it took off and takes off the todo it put on",
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
    "oldest": "the work that began first, when GitHub says the push that made its branch came, or, for a pull "
              "request from a fork or from a branch it says nothing of, when the pull request was opened; work "
              "whose beginning nothing says, which can only be a claim with no pull request yet, counts as the "
              "newest, since a claim opens its draft at once (Work in the open), and the number breaks a tie",
    "rename refused": "When GitHub refuses the rename for good, for a ruleset whose target takes in the claim "
                      "branches or a token without the right, the board says so once on the issue",
    "behind": "The board reads a branch as behind where GitHub calls its pull request behind, as it does under a "
              "rule that requires an up-to-date branch, or, where GitHub's word is blocked or draft, which says "
              "only that something else stops the merge, a rule still holding the pull request or the draft "
              "itself, where the comparison of the branch with the default branch says so.",
    "fork": "A claim and its pull request live in the repository itself, so a pull request from a fork is no "
            "claim's, whatever its branch is called: the board lists it as a pull request of its own and tells it "
            "what it tells any other, it finishes no claim, marked ready or said finished, and a release's rename, "
            "which closes the claim's own pull request, leaves it open",
    "pick": "Take an issue the Given to workers section gives you first. Else take a blocker, with or without todo, "
            "that the Blockers section lists, that nobody has claimed and that no worker's label gives to another, "
            "since a blocker comes before the Queue. Else take an open issue labelled todo that the Queue section "
            "shows as unclaimed and that no open blocker stands in the way of.",
    "never": "Wherever an issue is listed, never take an epic, which the coordinator plans (Planning), nor an issue "
             "labelled blocked, which waits on a peer or the coordinator.",
    "another's": "An issue with a branch `claude/<issue>` on origin, or a file `docs/agents/claims/<issue>.md` on the "
                 "default branch, is another worker's, whatever its labels say.",
    "several": "Where the labels of several workers name one issue, it is given to each of them, and the first of "
               "them to claim it holds it.",
    "blocked": "- blocked: a worker waits on a peer or the coordinator.",
    "held blocker": "a second worker that sees it held waits for it, helps on the one pull request by review, or "
                    "takes other work it does not stand in the way of (Pick)",
    "worker line": "the `- Worker:` line (or the older `- Account:`), whose name is the worker's label without the "
                   "`worker:` prefix",
    "own claim": "each takes its own claim that needs work, one whose claim file names it, whatever the issue's "
                 "labels say, or, where no claim file names a worker, one whose issue's label names it, a "
                 "worker:<name> label before the older spellings and the first by name among labels of one "
                 "spelling, as a release names whose the claim was",
    "given first": "else the task it was given, one given to it alone first and a task the labels of several workers "
                   "give going to the first of them in the run's order with nothing else to do",
    "as it stands": "A pull request GitHub calls clean, unstable or has_hooks, a draft among them, since GitHub gives "
                    "a draft the word its branch has, is not held for being behind under the repository's rules, so "
                    "it hears nothing however far the default branch has moved; one GitHub calls dirty, in conflict "
                    "with the default branch, hears it.",
}

CLAIM_PREFIX = "claude/"
CLAIM_NUMBER = re.compile(r"[1-9][0-9]{0,8}")
CLAIM_DIR = "docs/agents/claims/"
FILES_HEADING = "## Files to touch"
TODO, CLAIMED, IN_REVIEW, BLOCKER, EPIC, BLOCKED = "todo", "claimed", "in-review", "blocker", "epic", "blocked"
WORKER_SPELLINGS = ("worker:", "account:", "agent:")
# The lines of a claim file that name its worker (SPEC["worker line"]).
WORKER_LINES = ("- Worker:", "- Account:")
IDLE_MINUTES = 24 * HOUR
# Where work whose beginning nothing says stands, as the newest
# (SPEC["oldest"]): after every minute the model counts.
NOTHING_SAYS = float("inf")
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
    """The Queue: open issues labelled todo, unclaimed, given to no worker
    and no epic (SPEC["todo"], SPEC["given"], SPEC["epic"]). Until F15's
    fourth review the oracle listed an epic labelled todo, which no search
    seeded, and the library has always left out; the labels lane met it
    (mutants.labels_lane), and the spec's epic line now says so."""
    held = held_numbers(model)
    return sorted(n for n, i in model.issues.items() if i.state == "open" and TODO in i.labels
                  and EPIC not in i.labels and CLAIMED not in i.labels and n not in held
                  and not workers_named(i.labels))


def blockers(model):
    """Blockers: every open issue labelled blocker, claimed or not, in
    number order (SPEC["blockers"])."""
    return sorted(n for n, i in model.issues.items() if i.state == "open" and BLOCKER in i.labels)


def given(model, known=AS_BUILT):
    """Given to workers: open issues a worker's label names, not yet
    claimed, with the workers named (SPEC["given"]). The spec's sentence
    asks for no other label, and since F14's fix the library asks for none
    either; until it the issue had to carry todo and not epic, which the
    switch F14 read. No finding switches this rule now, and known is taken
    as every rule of the page takes it."""
    held = held_numbers(model)
    return {n: workers_named(i.labels) for n, i in model.issues.items()
            if i.state == "open" and CLAIMED not in i.labels and n not in held and workers_named(i.labels)}


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


def labels_put_on(model, number):
    """The label a release puts on (SPEC["order"]): todo, when the issue
    carries none of todo, epic and blocker."""
    names = model.issues[number].labels
    return () if names & {TODO, EPIC, BLOCKER} else (TODO,)


# --- overlaps -----------------------------------------------------------------

class Piece(NamedTuple):
    """One piece of open work: its identity (the issue a claim's work is
    for, else the pull request's number), the number it is told on and
    shown by now, its files, the landing order's two words, and the minute
    the work began (SPEC["oldest"]): the push that made its branch, or the
    opening of a pull request from a branch the model records no push of;
    None when nothing says."""

    identity: int
    number: int
    files: tuple
    ready: bool
    blocker: bool
    began: int | None = None


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


def claim_of(pull):
    """The issue whose claim the pull request is: its branch's number here,
    and never one from a fork, whose branch is another whatever it is
    called (SPEC["fork"])."""
    return None if pull.fork else claim_number(pull.head)


def open_work(model, known=AS_BUILT, unsaid=frozenset()):
    """Every piece of open work (SPEC["open work"]) but a released claim's.
    A claim and the pull request from its branch are one piece, which
    keeps the issue's number as its identity; with F2 known, the identity
    is the number it is shown by, as the library reads it. A pull request
    from a fork is a piece of its own, whatever its branch is called, and
    began when it was opened (SPEC["fork"], SPEC["oldest"]). unsaid names
    the branches GitHub refused to date in the run, whose work then began
    when nothing else says (SPEC["oldest"]): a pull request's at its
    opening, and a claim's never."""
    pieces = []
    pulled = set()
    for pull in sorted(model.pulls.values(), key=lambda p: p.number):
        if pull.state != "open":
            continue
        number = claim_of(pull)
        if number is not None and released(model, number):
            continue
        if not pull.fork:
            pulled.add(pull.head)
        listed = listed_files(claim_file_text(model, pull.head, number)) if number is not None else []
        files = own(model.pull_files(pull)) or own(listed)
        identity = number if number is not None and "F2" not in known else pull.number
        began = None if pull.fork or pull.head in unsaid else model.created.get(pull.head)
        began = pull.opened_at if began is None else began
        pieces.append(Piece(identity, pull.number, files, not pull.draft, is_blocker(model, number), began))
    for ref in sorted(model.refs):
        number = claim_number(ref)
        if number is None or ref in pulled or released(model, number):
            continue
        files = own(listed_files(claim_file_text(model, ref, number)))
        began = None if ref in unsaid else model.created.get(ref)
        pieces.append(Piece(number, number, files, False, is_blocker(model, number), began))
    return pieces


def is_blocker(model, number):
    issue = model.issues.get(number)
    return issue is not None and BLOCKER in issue.labels


def landing(piece, known=AS_BUILT):
    """SPEC["landing order"], with "the oldest" read as SPEC["oldest"]
    says: the work that began first, work whose beginning nothing says
    last of all, the number breaking a tie. Until F13's fix the library
    read it as the lowest number, an issue's and a pull request's alike,
    which the switch F13 read. No finding switches this rule now, and known
    is taken as every rule of the page takes it."""
    return (not piece.blocker, not piece.ready, NOTHING_SAYS if piece.began is None else piece.began, piece.number)


def overlaps(model, known=AS_BUILT, unsaid=frozenset()):
    """Each file more than one piece changes, with the numbers the pieces
    are shown by, in landing order; unsaid as open_work takes it."""
    meeting = {}
    for piece in open_work(model, known, unsaid):
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
        places |= {piece.identity} | {p.number for p in model.pulls.values() if claim_of(p) == piece.identity}
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
            if claim_of(pull) is not None:
                by_number.setdefault(pull.number, claim_of(pull))
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
    decide, since F12's fix, and a draft, or a pull request a rule or a
    review blocks, hears it like any other, since F9's fix), behind as
    owes_main_moved reads it."""
    marker = BOARD_MARKERS["main-moved"].format(sha=short_sha(model.refs[MAIN]))
    found = []
    for pull in pulls_spoken_to(model):
        if owes_main_moved(model, pull) and not has_marker(model, pull.number, marker):
            found.append(Effect("comment", pull.number, "main-moved"))
    return found


def owes_main_moved(model, pull):
    """Whether the pull request's branch stands so the board tells it "main
    moved": in conflict with main, or behind it where GitHub says behind,
    or says blocked, a rule still holding the pull request, so the
    comparison says it (SPEC["behind"]); GitHub gives a draft the word its
    branch has, never draft (C0). A branch behind main under a rule that
    does not require an up-to-date branch, the model's lenient_rules, with
    no rule holding it, which GitHub calls clean, draft or not, is not held
    for being behind and is owed nothing (SPEC["as it stands"], F16). A
    pull request from a fork stands as its own head does (SPEC["fork"])."""
    standing = model.pull_standing(pull)
    if standing == "dirty":
        return True
    return standing == "behind" and (model.opaque_pulls or not model.lenient_rules)


def idle_effects(model):
    marker = BOARD_MARKERS["idle"]
    return [Effect("comment", p.number, "idle") for p in pulls_spoken_to(model)
            if idle(p.updated_at, model.now) and not has_marker(model, p.number, marker)]


def pulls_spoken_to(model):
    """The open pull requests the board speaks to: all but a released
    claim's, which the rename closes, one from a fork being no claim's
    (SPEC["fork"])."""
    return [p for p in sorted(model.pulls.values(), key=lambda p: p.number) if p.state == "open"
            and not ((n := claim_of(p)) is not None and released(model, n))]


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
        put_on = labels_put_on(model, number)
        for name in labels_released(model, number):
            effects.append(Effect("label-off", number, name))
            model.issues[number].labels = model.issues[number].labels - {name}
        if put_on:
            effects.append(Effect("label-on", number, put_on))
            model.issues[number].labels = model.issues[number].labels | set(put_on)
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


# --- Pick and the clerk's deal (F18) ----------------------------------------

def claim_file_worker(text):
    """The worker a claim file names (SPEC["worker line"]): the name after
    `- Worker:`, or the older `- Account:`; None for a file with neither
    line, or no file."""
    for line in (text or "").splitlines():
        for spelling in WORKER_LINES:
            if line.startswith(spelling) and line[len(spelling):].strip():
                return line[len(spelling):].strip()
    return None


def label_holder(labels):
    """The worker the labels name for a claim whose file names none
    (SPEC["own claim"]): a worker:<name> label before the older spellings,
    and the first by name among the labels of one spelling; None when no
    label names a worker."""
    for prefix in WORKER_SPELLINGS:
        names = sorted(name[len(prefix):] for name in labels if name.startswith(prefix) and name[len(prefix):])
        if names:
            return names[0]
    return None


def claim_holder(model, number):
    """Whose the claim on the issue is (SPEC["own claim"]): the worker its
    claim file names, whatever the issue's labels say, or, where no claim
    file names a worker, the one its label names (label_holder); None for
    an issue no claim branch holds, or a claim nothing names."""
    if number not in held_numbers(model) or number not in model.issues:
        return None
    named = claim_file_worker(claim_file_text(model, branch_of(number), number))
    return named or label_holder(model.issues[number].labels)


def carries(model, worker):
    """The claims Planning has the worker carry before it takes anything
    (SPEC["own claim"]): its own, on an open issue, and not labelled
    blocked, whose holder waits on a peer or the coordinator
    (SPEC["blocked"]). The clerk carries a claim only when it needs work,
    which the spec does not define; a claim with no pull request yet has
    work, since Work in the open has its holder open the draft at once, and
    the lane that holds the clerk to this (dealing.picking_lane) makes no
    other kind."""
    return [n for n in sorted(held_numbers(model)) if n in model.issues and model.issues[n].state == "open"
            and BLOCKED not in model.issues[n].labels and claim_holder(model, n) == worker]


def takeable(model, number):
    """Whether Pick lets anyone take the issue: open, no epic and not
    labelled blocked (SPEC["never"]), and nobody's claim, by neither a claim
    branch, a claim file on the default branch (SPEC["another's"]) nor the
    label claimed, which the board's Queue and Given to workers leave out."""
    issue = model.issues.get(number)
    return (issue is not None and issue.state == "open" and EPIC not in issue.labels
            and BLOCKED not in issue.labels and CLAIMED not in issue.labels
            and number not in held_numbers(model) and claim_file_text(model, MAIN, number) is None)


def given_alone(model, worker):
    """The tasks the labels give the worker alone that Pick lets it take."""
    return [n for n, names in sorted(given(model).items()) if names == [worker] and takeable(model, n)]


def given_to_worker(model, number, worker):
    """Whether the labels give the issue to the worker, alone or beside
    others (SPEC["given"], SPEC["several"])."""
    issue = model.issues.get(number)
    return issue is not None and worker in workers_named(issue.labels)


def pick(model, worker, taken=()):
    """What Pick lets the worker take (SPEC["pick"]), leaving out what
    another worker of its run was dealt (taken), as (place, numbers): the
    first of Pick's places that holds anything for it, "given", the tasks
    the labels give it, alone or beside others (SPEC["several"]),
    "blocker", the unclaimed blockers no worker's label gives to another,
    or "queue", the Queue's issues; ("", ()) when none does. No place has
    an order of its own: the clerk takes the lowest number, which Pick
    allows."""
    def open_to(numbers):
        return tuple(n for n in sorted(numbers) if n not in taken and takeable(model, n))
    places = (("given", [n for n, names in given(model).items() if worker in names]),
              ("blocker", [n for n in blockers(model) if not workers_named(model.issues[n].labels)]),
              ("queue", queue(model)))
    for place, numbers in places:
        found = open_to(numbers)
        if found:
            return place, found
    return "", ()


def may_wait(model, place):
    """Whether a worker whose place is the Queue may take nothing: an open
    blocker it does not take stands in the way of work the spec does not
    name, so the worker may wait for it or take other work
    (SPEC["held blocker"], SPEC["pick"])."""
    return place == "queue" and bool(blockers(model))


def carry_problem(model, worker, number):
    """Why the worker may not carry the claim on the issue: it is another's
    or nobody's, its issue is closed, or it is labelled blocked and waits
    on a peer or the coordinator (carries)."""
    whose, issue = claim_holder(model, number), model.issues.get(number)
    if whose != worker:
        return f"{worker} carries #{number}, whose claim is {whose or 'nobody'}'s"
    if issue.state != "open":
        return f"{worker} carries #{number}, whose issue is closed"
    return f"{worker} carries #{number}, labelled blocked, which waits on a peer or the coordinator"


def deal_problems(model, team, moves):
    """What a deal from one look gets wrong by Pick and Planning, for the
    run's workers in the run's order (team) and the clerk's answer for each
    (moves: "carry <n>", "take <n>" or "none"), as sentences. Each worker
    with a claim of its own carries one of its own and nothing else; one
    with none takes only from its place by Pick, the others' takes left
    out, a task given to it alone before any other (SPEC["given first"]),
    and takes nothing only where Pick leaves it nothing or lets it wait
    (may_wait); a task given to several goes to the first of them in the
    run's order with nothing else to do, and to none only when each has
    something else; and no issue goes to two workers."""
    problems = []
    dealt = {member: move.split() for member, move in moves.items()}
    takes = {member: int(words[1]) for member, words in dealt.items() if words[0] == "take"}
    carried = {member: int(words[1]) for member, words in dealt.items() if words[0] == "carry"}
    for number in sorted(set(takes.values()) | set(carried.values())):
        to = sorted(m for m in team if number in (takes.get(m), carried.get(m)))
        if len(to) > 1:
            problems.append(f"#{number} dealt to {', '.join(to)}")
    for member in team:
        own = carries(model, member)
        if member in carried and carried[member] not in own:
            problems.append(carry_problem(model, member, carried[member]))
        elif own and member not in carried:
            problems.append(f"{member} carries nothing though its claim on #{own[0]} is its own")
        if own or member in carried:
            continue
        place, numbers = pick(model, member, {n for m, n in takes.items() if m != member})
        alone = given_alone(model, member)
        if member in takes and takes[member] not in numbers:
            problems.append(f"{member} takes #{takes[member]}, where Pick has it take from "
                            f"{place or 'nothing'} {list(numbers)}")
        elif member in takes and alone and takes[member] not in alone:
            problems.append(f"{member} takes #{takes[member]} though #{alone[0]} is given to it alone")
        elif member not in takes and numbers and not may_wait(model, place):
            problems.append(f"{member} takes nothing though Pick has it take from {place} {list(numbers)}")
    def free_for_it(member):
        return member not in carried and not (member in takes and given_to_worker(model, takes[member], member))
    for number, names in sorted(given(model).items()):
        named = [m for m in team if m in names]
        if len(names) < 2 or not named or not takeable(model, number):
            continue
        taker = next((m for m in named if takes.get(m) == number), None)
        idle = [m for m in named[:named.index(taker)] if free_for_it(m)] if taker else [m for m in named if free_for_it(m)]
        if taker is None and idle:
            problems.append(f"#{number}, given to {', '.join(names)}, goes to none though {idle[0]} "
                            f"has nothing else to do")
        elif idle:
            problems.append(f"#{number} goes to {taker}, though {idle[0]} comes before it in the run "
                            f"with nothing else to do")
    return problems
