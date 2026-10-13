"""What GitHub holds for the claim protocol, in memory (DESIGN.md, 3.1).

RepoModel keeps the refs and the commits they name, the issues with their
labels, comments and label events, the pull requests with theirs, every
commit a claim branch ever pointed at, which worker's push made each
branch, and each worker's remote-tracking refs. The clock counts minutes
from T0 and moves only when a scenario or the checker moves it, and every
commit is stamped with it.

A push follows git's rule as validation/prototype/gitprobe.py measured it
with git 2.43: a push that creates the ref or fast-forwards it is taken;
any other is refused, in git's own words, unless it is forced, or it is a
lease that matches the pusher's own remote-tracking ref. So the claim lock
is the model's, as it is GitHub's, and the library under test never sees
it: the board reads only what the fake GitHub (fake_github.py) answers.

canonical() is the state key (DESIGN.md, 6.3): everything a guarantee,
the oracle or a scripted worker reads, comment bodies whole, the label
events, every head a claim branch ever had, the tracking refs and the
board's last page included, so two states with one key behave the same
under every verb. The reductions of section 6.4
(times capped, workers by symmetry) come with the checker's fast lane.
"""

import copy
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
HOUR = 60
MAIN = "main"
REPO = "o/r"
OWNER = "owner"
BOT = "github-actions[bot]"
CLAIM_PREFIX = "claude/"
RELEASED_PREFIX = "released/"
# The first number a pull request gets, kept clear of the issues' numbers
# so a trace reads #1 as an issue and #100 as a pull request; GitHub's
# numbers share one sequence, and so do the endpoints that take them.
FIRST_PULL = 100
# How a branch stands to the default branch, from the model's own trees.
CLEAN, BEHIND, DIRTY = "clean", "behind", "dirty"


def iso(minute):
    """A model minute as GitHub writes a time."""
    return (T0 + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")


def moment(minute):
    """A model minute as an aware datetime, the now the library is given."""
    return T0 + timedelta(minutes=minute)


@dataclass(frozen=True)
class Commit:
    parents: tuple
    author: str
    committed_at: int
    tree: tuple  # the whole tree, as sorted (path, text) pairs
    message: str


@dataclass(frozen=True)
class Comment:
    author: str
    association: str
    at: int
    body: str


@dataclass(frozen=True)
class LabelEvent:
    """One entry of the label history GitHub keeps on an issue's timeline."""

    at: int
    issue: int
    actor: str
    added: bool
    label: str


@dataclass
class Issue:
    number: int
    title: str
    state: str = "open"
    labels: frozenset = frozenset()
    comments: tuple = ()
    body: str = ""


@dataclass
class PullRequest:
    number: int
    head: str
    author: str
    body: str
    title: str = ""
    draft: bool = True
    state: str = "open"  # open, closed or merged
    opened_at: int = 0
    updated_at: int = 0
    merged_at: int | None = None
    comments: tuple = ()


class Push(NamedTuple):
    """What git answered a push: taken, or refused and why, in git's words."""

    taken: bool
    why: str = ""


@dataclass
class RepoModel:
    now: int = 0
    commits: dict = field(default_factory=dict)
    refs: dict = field(default_factory=dict)
    issues: dict = field(default_factory=dict)
    pulls: dict = field(default_factory=dict)
    next_pull: int = FIRST_PULL
    # Who may write, beside the owner: the logins the permission API vouches for.
    writers: frozenset = frozenset({OWNER})
    # Every commit a claude/ branch ever pointed at.
    ever_heads: frozenset = frozenset()
    label_events: tuple = ()
    # The worker whose push made each branch that stands, by branch name,
    # and the minute the push made it: when that piece of work began.
    creators: dict = field(default_factory=dict)
    created: dict = field(default_factory=dict)
    # Each worker's remote-tracking refs, as its last fetch or push left them.
    tracking: dict = field(default_factory=dict)
    # The board branch's STATE.md: written only by a board run that exits 0.
    board_text: str = ""

    # --- commits ------------------------------------------------------------

    def commit(self, parents, author, tree, message, skew=0):
        """A new commit stamped with the clock, moved by skew minutes for a
        pusher whose clock is wrong; its sha is its content's."""
        record = Commit(tuple(parents), author, self.now + skew, tuple(sorted(tree.items())), message)
        sha = hashlib.sha1(repr(record).encode("utf-8")).hexdigest()
        self.commits[sha] = record
        return sha

    def tree(self, sha):
        return dict(self.commits[sha].tree) if sha else {}

    def ancestors(self, sha):
        seen, todo = set(), [sha]
        while todo:
            one = todo.pop()
            if one and one not in seen:
                seen.add(one)
                todo.extend(self.commits[one].parents)
        return seen

    def is_ancestor(self, older, newer):
        return older in self.ancestors(newer)

    def merge_base(self, one, two):
        """The best common ancestor; the least sha when there are several."""
        common = self.ancestors(one) & self.ancestors(two)
        best = [c for c in common if not any(c != d and c in self.ancestors(d) for d in common)]
        return min(best) if best else ""

    def changed(self, base, head):
        """The paths whose text differs from base to head."""
        old, new = self.tree(base), self.tree(head)
        return sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))

    def in_order(self, shas):
        """The commits parents before children, as git lists a range; among
        commits neither of which descends from the other, the earlier
        committer date first, then the sha. Sorting by date alone put a
        parent after its child when both were made in one minute."""
        left, placed = set(shas), []
        while left:
            ready = [c for c in left if not (set(self.commits[c].parents) & left)]
            first = min(ready, key=lambda c: (self.commits[c].committed_at, c))
            placed.append(first)
            left.remove(first)
        return placed

    def reachable(self):
        found = set()
        for sha in self.refs.values():
            found |= self.ancestors(sha)
        return found

    # --- refs ---------------------------------------------------------------

    def push(self, worker, ref, sha, force=False, lease=False):
        """git's rule for one ref. A lease with no expected value takes the
        pusher's own remote-tracking ref, and is refused, fast-forward or
        not, when the remote's ref stands and that tracking ref is missing
        or elsewhere."""
        current = self.refs.get(ref)
        mine = self.tracking.setdefault(worker, {})
        if lease and current is not None and mine.get(ref) != current:
            return Push(False, "stale info")
        fast_forward = current is None or current == sha or self.is_ancestor(current, sha)
        if not (fast_forward or force or lease):
            return Push(False, "fetch first")
        if current is None:
            self.creators[ref] = worker
            self.created[ref] = self.now
        self.set_ref(ref, sha)
        mine[ref] = sha
        return Push(True)

    def set_ref(self, ref, sha):
        self.refs[ref] = sha
        if ref.startswith(CLAIM_PREFIX):
            self.ever_heads = self.ever_heads | {sha}

    def delete_ref(self, ref):
        self.refs.pop(ref, None)
        self.creators.pop(ref, None)
        self.created.pop(ref, None)

    def rename_ref(self, old, new):
        """GitHub's rename: the ref moves, and the open pull request whose
        head it was is closed (renaming-a-branch.md:19). Who made the old
        name made the new one."""
        self.set_ref(new, self.refs.pop(old))
        if old in self.creators:
            self.creators[new] = self.creators.pop(old)
            self.created[new] = self.created.pop(old)
        for pull in self.pulls.values():
            if pull.head == old and pull.state == "open":
                pull.state = "closed"
                pull.updated_at = self.now

    def fetch(self, worker):
        self.tracking[worker] = dict(self.refs)

    # --- how a branch stands ------------------------------------------------

    def standing(self, ref):
        """clean, behind or dirty against main: behind when main is not in
        the branch, dirty when a path both sides changed since the merge
        base differs between them."""
        head, main = self.refs.get(ref, ""), self.refs[MAIN]
        if not head or self.is_ancestor(main, head):
            return CLEAN
        base = self.merge_base(head, main)
        ours, theirs = set(self.changed(base, head)), set(self.changed(base, main))
        head_tree, main_tree = self.tree(head), self.tree(main)
        if any(head_tree.get(p) != main_tree.get(p) for p in ours & theirs):
            return DIRTY
        return BEHIND

    def files_changed(self, ref):
        """The paths a branch changes beside its merge base with main, as a
        pull request's files are."""
        head = self.refs.get(ref, "")
        if not head:
            return []
        return self.changed(self.merge_base(head, self.refs[MAIN]), head)

    def open_pull_from(self, ref):
        return next((p for p in self.pulls.values() if p.head == ref and p.state == "open"), None)

    # --- issues, labels and comments ----------------------------------------

    def add_label(self, number, actor, label):
        issue = self.issues[number]
        if label not in issue.labels:
            issue.labels = issue.labels | {label}
            self.label_events += (LabelEvent(self.now, number, actor, True, label),)

    def remove_label(self, number, actor, label):
        """True when the label was on and came off."""
        issue = self.issues[number]
        if label not in issue.labels:
            return False
        issue.labels = issue.labels - {label}
        self.label_events += (LabelEvent(self.now, number, actor, False, label),)
        return True

    def target(self, number):
        """The issue or the pull request with this number, or None."""
        return self.issues.get(number) or self.pulls.get(number)

    def add_comment(self, number, comment):
        found = self.target(number)
        found.comments = found.comments + (comment,)
        if isinstance(found, PullRequest):
            found.updated_at = self.now

    def comments_on(self, number):
        found = self.target(number)
        return found.comments if found else ()

    # --- the key ------------------------------------------------------------

    def canonical(self):
        """Everything any guarantee or the oracle reads, as one hashable value."""
        return (
            self.now,
            tuple(sorted(self.refs.items())),
            tuple(sorted((n, i.state, tuple(sorted(i.labels)), i.comments, i.body) for n, i in self.issues.items())),
            tuple(sorted((n, p.head, p.author, p.body, p.draft, p.state, p.opened_at, p.updated_at, p.merged_at,
                          p.comments) for n, p in self.pulls.items())),
            self.next_pull,
            self.writers,
            tuple(sorted(self.ever_heads)),
            self.label_events,
            tuple(sorted(self.creators.items())),
            tuple(sorted(self.created.items())),
            tuple(sorted((w, tuple(sorted(refs.items()))) for w, refs in self.tracking.items())),
            self.board_text,
        )

    def digest(self):
        """A 16-byte digest of the key, for the deep scopes' seen-set."""
        return hashlib.sha256(repr(self.canonical()).encode("utf-8")).digest()[:16]

    def clone(self):
        return copy.deepcopy(self)


# --- setting a world up -------------------------------------------------------

def claim_text(number, worker, files=None):
    """A claim file in the spec's shape (spec/PROTOCOL.md, Claim)."""
    listed = files if files is not None else (f"src/{number}.py",)
    lines = [f"# Claim: #{number}", "", f"- Issue: https://github.com/{REPO}/issues/{number}", f"- Worker: {worker}",
             f"- Branch: {CLAIM_PREFIX}{number}", "", "## Plan", "", f"Do issue {number}.", "", "## Files to touch", ""]
    return "\n".join(lines + [f"- {path}" for path in listed]) + "\n"


def seeded(issues=(1,), writers=("a", "b"), labels=("todo",)):
    """A repository with one commit on main and the issues open, labelled
    todo; the workers named may write, as a person's sessions on their own
    login do."""
    model = RepoModel(writers=frozenset(writers) | {OWNER})
    model.refs[MAIN] = model.commit((), OWNER, {"README.md": "base\n"}, "base")
    for number in issues:
        model.issues[number] = Issue(number, f"Issue {number}", labels=frozenset(labels))
    return model
