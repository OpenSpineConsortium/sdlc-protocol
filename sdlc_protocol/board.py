"""The board: what a repository's open work looks like, as one page.

The board is the file STATE.md on the branch "board": the open claims, the
open pull requests and how each stands to the default branch, the files
that more than one piece of open work changes and the order that work
should land in, the queue of unclaimed issues, the issues given to
workers, the blockers and the pull requests merged last, so every session
and every worker reads the same page instead of asking the API.
This module is the pure part. A Snapshot holds what GitHub knows, filled by
an adapter or read back from JSON; render turns it into the page, with no
wall-clock value and every row sorted, so the same state gives the same
bytes and a workflow writes the branch only when something moved; and
comments_due says which pull requests the board would speak to, and what
it would say, for the adapter to post once each. A claim the coordinator
released (released_keys) leaves the page as soon as the snapshot shows
the release, its issue back in the queue, and the adapter renames its
branch so the name is free for the next claim. Nothing here calls GitHub.
"""

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from . import labels
from .claims import (
    CLAIM_DIR, IDLE_AFTER, PREFIX, RELEASED_PREFIX, branch_of, by_number, claim_files, claim_worker,
    is_claim_branch, is_idle, key_of, number_in, number_of, was_released,
)

RECENT_MERGED = 5
# Where a workflow writes the board when nothing else is named: a file in
# the current directory, since the workflow checks out the branch "board".
STATE_PATH = Path("STATE.md")
NO_CLAIM_FILE = "no claim file yet"
# A claim file GitHub gave no text of: its contents API gives the text of a
# file of up to 1 MB, and answers a bigger one with an empty content and
# the encoding "none". The snapshot holds it as False, where a text is the
# file's and None is no file yet, and the Worker cell says so when no label
# names the worker either.
UNREAD_CLAIM = False
CLAIM_FILE_NOT_READ = "claim file over 1 MB, not read"
# A claim branch that outlived its issue: the row stays, and says what to do.
ISSUE_CLOSED = "issue closed"
DELETE_BRANCH = "delete the branch"
NO_PULL_YET = "(no pull request yet)"
# The issue cell of a branch under claude/ that names no issue, as
# claude/x does, or claude/03, which reads as a claim on #3 and is none.
NOT_A_CLAIM = "(not a claim)"

MAIN_MOVED_MARKER = "<!-- sdlc-board: main-moved {sha} -->"
IDLE_MARKER = "<!-- sdlc-board: idle -->"
# The coordinator releases a draft's claim in the same run the board would
# call it idle, so the comment reaches a pull request marked ready, which
# waits however long its review takes, or one from a branch that is no claim.
IDLE_TEXT = (
    "idle: no change for more than a day. A pull request marked ready waits for its review and merge, "
    "however long that takes; a draft whose branch goes a day without a commit is released by the coordinator."
)
# What the board says on an issue once it has renamed a released claim's
# branch; the marker names the new branch, so each release is told once.
RELEASED_MARKER = "<!-- sdlc-board: released {branch} -->"
# What the board says on an issue when GitHub refused for good to rename
# its released claim's branch; the marker names the branch the rename was
# to make, so each refusal is told once, and a newer head's again.
RENAME_REFUSED_MARKER = "<!-- sdlc-board: rename-refused {branch} -->"

CLAIM_HEADING = "## Claims (open claude/<issue> branches and docs/agents/claims/<issue>.md)"
CLAIM_COLUMNS = ("Issue", "Branch", "Worker", "PR", "Files it touches")
PULL_HEADING = "## Open pull requests"
PULL_COLUMNS = ("PR", "Title", "Branch", "State (clean, behind, conflicting, blocked, unknown)", "Last update")
OVERLAP_HEADING = ("## Overlaps (files that more than one piece of open work changes; land the work in this "
                   "order: a blocker first, then work marked ready, then the oldest)")
OVERLAP_COLUMNS = ("File", "Open work, in the order it should land")
QUEUE_HEADING = "## Queue (open issues labelled todo, unclaimed and given to no worker)"
ISSUE_COLUMNS = ("Issue", "Title", "Labels")
GIVEN_HEADING = "## Given to workers (open issues labelled todo that a worker's label names, not yet claimed)"
GIVEN_COLUMNS = ("Issue", "Title", "Worker")
BLOCKER_HEADING = "## Blockers (open issues labelled blocker)"
MERGED_HEADING = f"## Recently merged (the last {RECENT_MERGED})"
MERGED_COLUMNS = ("PR", "Title", "Branch", "Merged")

# The five words the board uses for how a pull request stands to the
# default branch, and GitHub's mergeable_state words that mean each.
STATE_WORDS = ("clean", "behind", "conflicting", "blocked", "unknown")
GITHUB_STATES = {"has_hooks": "clean", "unstable": "clean", "dirty": "conflicting"}


# --- the snapshot -----------------------------------------------------------

@dataclass
class Issue:
    """An open issue as the queue and the blockers show it."""

    number: int
    title: str = ""
    labels: list = field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data):
        return cls(
            number=int(data["number"]),
            title=data.get("title") or "",
            labels=labels.label_names(data),
            updated_at=data.get("updated_at") or "",
        )

    def to_dict(self):
        return {"number": self.number, "title": self.title, "labels": list(self.labels), "updated_at": self.updated_at}


@dataclass
class Pull:
    """An open pull request with its head branch, GitHub's word for how it
    stands to the default branch (mergeable_state, as the API gives it),
    the paths it changes, and whether it is still a draft: one marked ready
    waits for review however long it takes, so its claim is never released,
    and a pull request whose draft state is not given counts as ready. The
    dict shape is the API's, head and all, so a pull request detail from
    GitHub is a fixture as it is."""

    number: int
    title: str = ""
    branch: str = ""
    head_sha: str = ""
    mergeable_state: str | None = None
    updated_at: str = ""
    files: list = field(default_factory=list)
    draft: bool = False

    @property
    def state(self):
        """The board's word for the pull request's standing."""
        return state_word(self.mergeable_state)

    @classmethod
    def from_dict(cls, data):
        head = data.get("head") or {}
        return cls(
            number=int(data["number"]),
            title=data.get("title") or "",
            branch=head.get("ref") or "",
            head_sha=head.get("sha") or "",
            mergeable_state=data.get("mergeable_state"),
            updated_at=data.get("updated_at") or "",
            files=[file_name(f) for f in data.get("files") or []],
            draft=bool(data.get("draft")),
        )

    def to_dict(self):
        return {
            "number": self.number,
            "title": self.title,
            "head": {"ref": self.branch, "sha": self.head_sha},
            "mergeable_state": self.mergeable_state,
            "updated_at": self.updated_at,
            "files": list(self.files),
            "draft": self.draft,
        }


@dataclass
class Merged:
    """A pull request that landed: enough for the recently merged table."""

    number: int
    title: str = ""
    branch: str = ""
    merged_at: str = ""

    @classmethod
    def from_dict(cls, data):
        return cls(
            number=int(data["number"]),
            title=data.get("title") or "",
            branch=(data.get("head") or {}).get("ref") or "",
            merged_at=data.get("merged_at") or "",
        )

    def to_dict(self):
        return {"number": self.number, "title": self.title, "head": {"ref": self.branch}, "merged_at": self.merged_at}


@dataclass
class ClaimIssue:
    """The issue behind a claim branch: its state and its label names, so
    the board can say that a branch outlived its issue, and can name the
    account from the issue's label once the claim file is gone."""

    state: str = ""
    labels: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        return cls(state=data.get("state") or "", labels=labels.label_names(data))

    def to_dict(self):
        return {"state": self.state, "labels": list(self.labels)}


@dataclass
class Release:
    """What the release rule reads about one claim: when the latest release
    comment on its issue was made (claims.latest_release), "" when none
    stands, and the head of its branch, the commit and when it was
    committed. An adapter fills it for every claim a release can be due on
    or can have freed (adapters.github.releases): one gone more than a day
    without a commit, whose issue is open and not labelled in-review, and
    whose pull request, if it has one, is a draft its author has not called
    finished, or one its author called finished that a person has released.
    The coordinator releases those that no release comment has released yet
    (coordinator.releases_due), and the board counts a release once the
    label claimed is off too (released_keys)."""

    released_at: str = ""
    sha: str = ""
    committed_at: str = ""

    @classmethod
    def from_dict(cls, data):
        return cls(released_at=data.get("released_at") or "", sha=data.get("sha") or "",
                   committed_at=data.get("committed_at") or "")

    def to_dict(self):
        return {"released_at": self.released_at, "sha": self.sha, "committed_at": self.committed_at}


@dataclass
class Snapshot:
    """Everything the board renders, in the shape a --dry-run fixture has:
    the default branch's commit and name, the open issues (pull requests
    left out), the open pull requests with their standing and files, the
    branch names, the claim files by issue key (None for a branch with no
    file yet, UNREAD_CLAIM for one GitHub gave no text of), the issue
    behind each claim branch, what a release reads about a claim by issue
    key, and the merges last."""

    sha: str = ""
    default_branch: str = "main"
    issues: list = field(default_factory=list)
    pulls: list = field(default_factory=list)
    branches: list = field(default_factory=list)
    claims: dict = field(default_factory=dict)
    claim_issues: dict = field(default_factory=dict)
    merged: list = field(default_factory=list)
    releases: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        """A snapshot from its JSON shape, which is also what the API gives:
        labels may be objects or names, branches objects or names."""
        return cls(
            sha=data.get("sha") or "",
            default_branch=data.get("default_branch") or "main",
            issues=[Issue.from_dict(i) for i in data.get("issues") or []],
            pulls=[Pull.from_dict(p) for p in data.get("pulls") or []],
            branches=[branch_name(b) for b in data.get("branches") or []],
            claims={str(k): v for k, v in (data.get("claims") or {}).items()},
            claim_issues={str(k): ClaimIssue.from_dict(v or {}) for k, v in (data.get("claim_issues") or {}).items()},
            merged=[Merged.from_dict(m) for m in data.get("merged") or []],
            releases={str(k): Release.from_dict(v or {}) for k, v in (data.get("releases") or {}).items()},
        )

    def to_dict(self):
        return {
            "sha": self.sha,
            "default_branch": self.default_branch,
            "issues": [i.to_dict() for i in self.issues],
            "pulls": [p.to_dict() for p in self.pulls],
            "branches": list(self.branches),
            "claims": dict(self.claims),
            "claim_issues": {k: v.to_dict() for k, v in self.claim_issues.items()},
            "merged": [m.to_dict() for m in self.merged],
            "releases": {k: v.to_dict() for k, v in self.releases.items()},
        }

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(json.loads(text))

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2) + "\n"


def branch_name(branch):
    """A branch as a name, whether the API's object or a fixture's string."""
    return branch if isinstance(branch, str) else branch["name"]


def file_name(changed):
    """A changed file as a path, whether the API's object from the pull
    request's files or a fixture's string."""
    return changed if isinstance(changed, str) else changed["filename"]


def as_snapshot(value):
    """A Snapshot from one, or from the dict shape a fixture or an API
    adapter holds, so render takes either."""
    return value if isinstance(value, Snapshot) else Snapshot.from_dict(value)


def as_pull(value):
    return value if isinstance(value, Pull) else Pull.from_dict(value)


# --- the rules --------------------------------------------------------------

def state_word(mergeable_state):
    """One of the five words the board uses for a pull request, from
    GitHub's mergeable_state. GitHub's has_hooks and unstable are mergeable
    states (the checks are what they are), so they read as clean; dirty is
    conflicting; blocked is a computed state of its own; unknown is kept for
    a state GitHub has not computed yet. The board's own words pass through,
    so a snapshot that already speaks them renders the same."""
    if mergeable_state in STATE_WORDS:
        return mergeable_state
    return GITHUB_STATES.get(mergeable_state, "unknown")


def pr_state(pull):
    """The board's word for a pull request, given as a Pull or as the dict
    the API returns."""
    return state_word(pull.mergeable_state if isinstance(pull, Pull) else pull.get("mergeable_state"))


def short_sha(sha):
    return (sha or "")[:7] or "unknown"


def issue_ref(key):
    """The issue cell of a claim's row: #<number>, or NOT_A_CLAIM for a key
    that names no issue, which shown as itself could read as one (03, #3,
    or a digit of another script that the page folds into an ASCII one)."""
    return f"#{key}" if number_in(key) is not None else NOT_A_CLAIM


def escaped(name):
    """A name with each character outside ASCII written as its escape, so
    the page, which folds such characters into ASCII ones (cell), never
    shows a branch such as claude/ and a fullwidth 3 as claude/3."""
    return "".join(ch if ch.isascii() else escape(ch) for ch in name)


def escape(ch):
    """One character as Python writes it in a string: \\uXXXX, or \\UXXXXXXXX
    for one past U+FFFF."""
    code = ord(ch)
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def claim_branches(branches):
    return [branch_name(b) for b in branches if is_claim_branch(branch_name(b))]


def claimed_numbers(branches, released=()):
    """The issue numbers that have a claim branch, leaving out the claims
    named in released, by key, since a released claim holds nothing; a
    branch whose key is no number (claims.number_in) claims none."""
    numbers = (number_of(b) for b in claim_branches(branches) if key_of(b) not in released)
    return {number for number in numbers if number is not None}


def released_keys(snapshot, idle_after=IDLE_AFTER):
    """The claims released, by the coordinator or by a person who may
    write, by issue key: a claim branch whose issue is open and no longer
    labelled claimed, whose latest release comment came after the branch
    had gone without a commit for more than idle_after, and which nothing
    has been committed to since (claims.was_released). It takes both acts
    of a release, the comment and the label claimed taken off, so a stray
    comment frees nothing; a commit after the release, its holder's or a
    new claim's, ends it; and putting the label claimed back undoes it. A
    finished claim is never released, however long it waits for review:
    one whose issue is labelled in-review or whose pull request is marked
    ready; a draft its author called finished only by a person, since the
    coordinator never releases one. The board leaves a released claim off
    the page and puts its issue back in the queue, and the GitHub adapter
    renames its branch, so the name is free for the next claim; the page
    reads the same before the rename and after it, so one release rewrites
    the board once."""
    snap = as_snapshot(snapshot)
    return {key_of(b) for b in claim_branches(snap.branches) if is_released(snap, key_of(b), idle_after)}


def is_released(snap, key, idle_after=IDLE_AFTER):
    """Whether the claim with this key was released, by the coordinator
    or by a person who may write, by released_keys' rule; a claim with no
    head commit known is not, since its branch could not be renamed."""
    release, issue = snap.releases.get(key), snap.claim_issues.get(key)
    if release is None or issue is None or not release.sha:
        return False
    if issue.state != "open" or labels.CLAIMED in issue.labels or labels.IN_REVIEW in issue.labels:
        return False
    if any(p.branch == branch_of(key) and not p.draft for p in snap.pulls):
        return False
    return was_released(release.released_at, release.committed_at, idle_after)


def released_branches(snap):
    """The claim branches of the released claims, whose pull requests the
    board no longer lists or speaks to: renaming the branch closes them."""
    return {branch_of(key) for key in released_keys(snap)}


def released_text(old, new):
    """What the board says on an issue once it has renamed the branch of
    its released claim."""
    return (
        f"released: the claim on this issue was released, so its branch {old} is now {new}, "
        f"which keeps its work; GitHub closes a pull request from the branch with the rename. "
        f"The issue is back in the queue, and the next claim pushes {old} anew from the default branch. "
        f"To undo the release, put the label claimed back on the issue first, then rename the branch "
        f"back and reopen its pull request."
    )


class Ruleset(NamedTuple):
    """A ruleset whose rules apply to a branch the board could not rename:
    its name, the page where an owner changes it, "" when there is none to
    link, and the types of the rules it applies to the branch."""

    name: str
    link: str
    types: tuple


def rename_refused_text(old, new, rulesets, words):
    """What the board says on an issue when GitHub refused for good to
    rename the branch of its released claim, in GitHub's words: the name
    stays taken, so nobody can claim the issue, until a board run renames
    it. A ruleset over the claim branches is what makes a rename an
    administrator's, so each one that applies to either name is named with
    its rules; rulesets is None when the board could not read them, and
    empty when none applies, which leaves the token's own right."""
    said = " ".join(str(words).split())
    lines = [f"rename refused: the claim on this issue was released, but GitHub refused to rename its "
             f"branch {old} to {new} ({said}), so the name {old} is still taken and nobody can claim the "
             f"issue yet. Every board run tries the rename again."]
    if rulesets:
        lines += ["", "These rulesets apply to the two names:"]
        lines += [f"- {f'[{r.name}]({r.link})' if r.link else r.name}: {', '.join(r.types)}" for r in rulesets]
        lines += ["", f"Take `{PREFIX}**` and `{RELEASED_PREFIX}**` out of their targets, so that they hold the "
                      f"default branch alone, and the next board run renames the branch."]
    elif rulesets is None:
        lines += ["", f"The board could not read which rulesets apply to the two names. A ruleset whose target "
                      f"takes in `{PREFIX}**` or `{RELEASED_PREFIX}**` makes the rename an administrator's: take "
                      f"those names out of its target, and the next board run renames the branch."]
    else:
        lines += ["", "No ruleset applies to the two names, so the board's token may lack the right to rename "
                      "a branch: its workflow needs contents: write."]
    return "\n".join(lines)


def pulls_behind_main(pulls):
    """The pull requests the main-moved rule speaks to: behind or
    conflicting, and only those."""
    return [p for p in pulls if pr_state(p) in ("behind", "conflicting")]


def idle_pulls(pulls, now, idle_after=IDLE_AFTER):
    """The pull requests with no change for more than a day."""
    return [p for p in pulls if pull_is_idle(as_pull(p), now, idle_after)]


def pull_is_idle(pull, now, idle_after=IDLE_AFTER):
    """Whether one pull request has waited too long. One with no update
    time is not idle rather than failing the run: there is nothing to
    measure, and GitHub always gives one."""
    return bool(pull.updated_at) and is_idle(pull.updated_at, now, idle_after)


def main_moved_text(state, default_branch, sha7):
    how = "behind" if state == "behind" else "conflicting with"
    return (
        f"main moved: this branch is {how} {default_branch} at {sha7}; "
        f"rebase or merge {default_branch} before the next push."
    )


class DueComment(NamedTuple):
    """A comment the board owes a pull request: posted only when no comment
    on it carries the marker, which is the adapter's job."""

    number: int
    marker: str
    text: str

    @property
    def kind(self):
        return "idle" if self.marker == IDLE_MARKER else "main-moved"


def comments_due(snapshot, event_name, ref_name, now, idle_after=IDLE_AFTER):
    """What the board says after this event, as (pull number, marker, text).
    After a push to the default branch, every pull request behind it or in
    conflict with it hears so, once per commit of the default branch (the
    marker carries the short sha); a pull request nobody touched for a day
    is asked once whether it is parked. Main-moved comments come first,
    then idle ones, each in the order the snapshot lists the pull requests,
    as the template's board posted them. A released claim's pull request
    hears nothing: renaming its branch closes it."""
    snap = as_snapshot(snapshot)
    released = released_branches(snap)
    pulls = [p for p in snap.pulls if p.branch not in released]
    due = []
    if event_name == "push" and ref_name == snap.default_branch:
        sha7 = short_sha(snap.sha)
        marker = MAIN_MOVED_MARKER.format(sha=sha7)
        for pull in pulls_behind_main(pulls):
            due.append(DueComment(pull.number, marker, main_moved_text(pull.state, snap.default_branch, sha7)))
    for pull in idle_pulls(pulls, now, idle_after):
        due.append(DueComment(pull.number, IDLE_MARKER, IDLE_TEXT))
    return due


# --- the page ---------------------------------------------------------------

def cell(value):
    """A table cell: one line, ASCII, pipes escaped, so the board stays a
    valid table and passes a repository's own ASCII check."""
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().strip()


def table(columns, rows):
    """A Markdown table; an empty one keeps its header and says (none), so
    the layout is the same whatever the repository holds."""
    lines = ["| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)]
    for row in rows or [["(none)"] + [""] * (len(columns) - 1)]:
        lines.append("| " + " | ".join(cell(c) for c in row) + " |")
    return lines


def claim_row(key, branch, text, pull, issue):
    """One claim as the board shows it. The worker is the claim file's, or
    the issue's label once the finishing pull request has deleted the file
    or when GitHub gave no text of it (UNREAD_CLAIM).
    A branch whose issue is closed is a leftover: the row says so in place
    of the pull request and asks for the branch to go in place of the files.
    A branch whose key names no issue claims nothing, and the row says so in
    place of the issue, with the branch's name escaped (escaped)."""
    closed = issue is not None and issue.state == "closed"
    if pull:
        files = sorted(pull.files)
    else:
        files = claim_files(text) if text else []
    if pull:
        pull_cell = f"#{pull.number}"
    elif closed:
        pull_cell = ISSUE_CLOSED
    else:
        pull_cell = NO_PULL_YET
    if files:
        files_cell = ", ".join(files)
    elif closed and not pull:
        files_cell = DELETE_BRANCH
    else:
        files_cell = "-"
    if text:
        worker = claim_worker(text)
    else:
        missing = CLAIM_FILE_NOT_READ if text is UNREAD_CLAIM else NO_CLAIM_FILE
        worker = labels.worker_of({"labels": issue.labels} if issue else None) or missing
    return [issue_ref(key), escaped(branch), worker, pull_cell, files_cell]


def claim_rows(snap):
    """A row per claim branch, in issue order, but a released claim's."""
    pulls_by_branch = {p.branch: p for p in snap.pulls}
    released = released_keys(snap)
    rows = []
    for branch in sorted(claim_branches(snap.branches), key=lambda b: by_number(key_of(b))):
        key = key_of(branch)
        if key in released:
            continue
        rows.append(claim_row(key, branch, snap.claims.get(key), pulls_by_branch.get(branch), snap.claim_issues.get(key)))
    return rows


def pull_rows(snap):
    """A row per open pull request, in number order, but one from a
    released claim's branch, which the rename of that branch closes."""
    released = released_branches(snap)
    return [
        [f"#{p.number}", p.title, p.branch, p.state, p.updated_at]
        for p in sorted(snap.pulls, key=lambda p: p.number) if p.branch not in released
    ]


def issue_rows(snap, label, unclaimed_only):
    """The issues carrying a label, in number order; the queue leaves out
    an issue with the label claimed, a claim branch that is not released,
    an epic, or a worker's label, which gives the issue to that worker."""
    claimed = claimed_numbers(snap.branches, released_keys(snap))
    rows = []
    for issue in sorted(snap.issues, key=lambda i: i.number):
        names = sorted(issue.labels)
        if label not in names:
            continue
        if unclaimed_only and (labels.CLAIMED in names or issue.number in claimed or labels.reserved(names)):
            continue
        rows.append([f"#{issue.number}", issue.title, ", ".join(names)])
    return rows


def given_rows(snap):
    """The open issues labelled todo that a worker's label gives to that
    worker and that nobody has claimed yet, in number order: the queue
    leaves them out, since only that worker takes them, and this is where
    the worker and everyone else see them. An epic is the coordinator's
    to plan, not a worker's to take, so it is left out with its labels."""
    claimed = claimed_numbers(snap.branches, released_keys(snap))
    rows = []
    for issue in sorted(snap.issues, key=lambda i: i.number):
        names = sorted(issue.labels)
        workers = labels.holder_names(names)
        if labels.TODO not in names or labels.CLAIMED in names or labels.EPIC in names or not workers:
            continue
        if issue.number not in claimed:
            rows.append([f"#{issue.number}", issue.title, ", ".join(workers)])
    return rows


class Work(NamedTuple):
    """One piece of open work, as the overlaps read it: its number, the pull
    request's or, for a claim with no pull request yet, the issue's; the
    files it changes or, before it changes any, the files its claim says it
    will touch; whether it is marked ready and whether its issue is a
    blocker; and whether it is a pull request at all."""

    number: int
    files: tuple
    ready: bool = False
    blocker: bool = False
    is_pull: bool = True


def landing_key(work):
    """The order open work that meets in a file should land in: a blocker
    first, since it stops everyone; then work marked ready, which is done
    and waits only for its merge; then the oldest, which started first."""
    return (not work.blocker, not work.ready, work.number)


def own_files(files):
    """The files a piece of work changes, without claim files: each claim
    has its own, so they never meet."""
    return tuple(sorted(f for f in files if not f.startswith(CLAIM_DIR + "/")))


def is_blocker(snap, key):
    issue = snap.claim_issues.get(key)
    return bool(issue) and labels.BLOCKER in issue.labels


def open_work(snapshot):
    """Every piece of open work, in number order: each open pull request but
    a released claim's, with the files it changes, or the files its claim
    file lists while it changes nothing but that file; and each claim with
    no pull request yet, with the files its claim file lists."""
    snap = as_snapshot(snapshot)
    released = released_keys(snap)
    pulls = {p.branch: p for p in snap.pulls}
    work = []
    for pull in snap.pulls:
        key = key_of(pull.branch) if is_claim_branch(pull.branch) else None
        if key in released:
            continue
        files = own_files(pull.files) or (own_files(claim_files(snap.claims.get(key) or "")) if key else ())
        work.append(Work(pull.number, files, not pull.draft, bool(key) and is_blocker(snap, key)))
    for branch in claim_branches(snap.branches):
        key, number = key_of(branch), number_of(branch)
        if branch in pulls or key in released or number is None:
            continue
        files = own_files(claim_files(snap.claims.get(key) or ""))
        work.append(Work(number, files, False, is_blocker(snap, key), False))
    return sorted(work, key=lambda w: w.number)


def overlaps(snapshot):
    """The files that more than one piece of open work changes, each with
    that work in the order it should land (landing_key), in file order."""
    meeting = {}
    for work in open_work(snapshot):
        for path in work.files:
            meeting.setdefault(path, []).append(work)
    return {path: sorted(works, key=landing_key) for path, works in sorted(meeting.items()) if len(works) > 1}


def overlap_rows(snap):
    return [[path, ", ".join(f"#{w.number}" for w in works)] for path, works in overlaps(snap).items()]


def merged_rows(snap):
    return [
        [f"#{m.number}", m.title, m.branch, m.merged_at]
        for m in sorted(snap.merged, key=lambda m: m.merged_at, reverse=True)
    ]


def render(snapshot, default_branch=None):
    """STATE.md for a snapshot: the layout the protocol's STATE.md describes.
    The default branch named here wins over the snapshot's own, for a
    workflow that knows it from the event; None takes the snapshot's."""
    snap = as_snapshot(snapshot)
    branch = cell(default_branch or snap.default_branch or "main")
    lines = [
        "# Board",
        "",
        f"As of commit {cell(short_sha(snap.sha))} on {branch}. "
        "Rewritten by .github/workflows/board.yml; do not edit by hand.",
    ]
    sections = (
        (CLAIM_HEADING, CLAIM_COLUMNS, claim_rows(snap)),
        (PULL_HEADING, PULL_COLUMNS, pull_rows(snap)),
        (OVERLAP_HEADING, OVERLAP_COLUMNS, overlap_rows(snap)),
        (QUEUE_HEADING, ISSUE_COLUMNS, issue_rows(snap, labels.TODO, True)),
        (GIVEN_HEADING, GIVEN_COLUMNS, given_rows(snap)),
        (BLOCKER_HEADING, ISSUE_COLUMNS, issue_rows(snap, labels.BLOCKER, False)),
        (MERGED_HEADING, MERGED_COLUMNS, merged_rows(snap)),
    )
    for heading, columns, rows in sections:
        lines += ["", heading, ""] + table(columns, rows)
    return "\n".join(lines) + "\n"


def write_if_changed(path, text):
    """Write the board only when its bytes differ, so a caller comparing
    files sees no change when nothing moved; True when it was written. The
    one thing in this module that touches a file, kept here because the
    rendered page is ASCII by construction and this is where that is known."""
    path = Path(path)
    data = text.encode("ascii")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True
