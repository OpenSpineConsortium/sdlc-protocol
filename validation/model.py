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
events, every head a claim branch ever had, the tracking refs, the
board's last page, whether GitHub hides a held branch's standing
(opaque_pulls) and whether it calls a branch that is behind clean
(lenient_rules) included, so two states with one key behave the same
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


# The ref name the model gives the run a pull request from a fork queues,
# whose job board.yml skips, since that run's token is read-only; GitHub's
# own ref for it is the pull request's merge ref, which no run here reads.
FORK_RUN = "fork"


def skipped(run):
    """Whether the run, as (event, ref name), is one board.yml's job skips:
    a push to any branch but the default, or a pull request's from a fork
    (FORK_RUN)."""
    return (run[0] == "push" and run[1] != MAIN) or run == ("pull_request", FORK_RUN)


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
    """One pull request. fork says its branch stands in its author's fork,
    where no ref of the model's names it, so sha is its head there: a
    branch of the same name here is another branch (F17)."""

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
    fork: bool = False
    sha: str = ""


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
    # Whether GitHub's word for an open pull request that is not in conflict
    # is "blocked", as a rule that holds it gives: a required check pending
    # or red, as on a draft whose check has not passed, or a review not yet
    # given; rather than clean or behind (F9). Part of the key, since the
    # fake's answers turn on it.
    opaque_pulls: bool = False
    # Whether GitHub's word for an open pull request whose branch is behind
    # the default branch is "clean", as GitHub gives under a rule that does
    # not require the branch to be up to date before merging, as the
    # template's crowd ruleset, or under no rule; rather than "behind",
    # which it gives only under one that does (F16). Part of the key.
    lenient_rules: bool = False
    # The board branch's STATE.md: written only by a board run that exits 0.
    board_text: str = ""
    # The board workflow's concurrency group (DESIGN.md, 3.3), each run as
    # (event, ref name): the run that starts next, which GitHub never
    # cancels, as the candidates one of which it is; the run waiting behind
    # it, as the candidates one of which it is, which the next event
    # replaces; and the run in progress between its two halves, a
    # cycle.HalfRun, while one is.
    queued: tuple = ()
    pending: tuple = ()
    running: object = None

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
        """GitHub's delete: the ref goes, and the open pull requests whose
        head it was are closed (managing-branches-within-your-repository.md:71),
        never one from a fork whose branch bears the same name, which is
        another branch."""
        self.refs.pop(ref, None)
        self.creators.pop(ref, None)
        self.created.pop(ref, None)
        for pull in self.pulls.values():
            if pull.head == ref and pull.state == "open" and not pull.fork:
                pull.state = "closed"
                pull.updated_at = self.now

    def rename_ref(self, old, new):
        """GitHub's rename: the ref moves, and the open pull request whose
        head it was is closed (renaming-a-branch.md:19), never one from a
        fork whose branch bears the same name, which is another branch. Who
        made the old name made the new one."""
        self.set_ref(new, self.refs.pop(old))
        if old in self.creators:
            self.creators[new] = self.creators.pop(old)
            self.created[new] = self.created.pop(old)
        for pull in self.pulls.values():
            if pull.head == old and pull.state == "open" and not pull.fork:
                pull.state = "closed"
                pull.updated_at = self.now

    def fetch(self, worker):
        self.tracking[worker] = dict(self.refs)

    # --- how a branch stands ------------------------------------------------

    def standing(self, ref):
        """How the branch of this name here stands to main (standing_at)."""
        return self.standing_at(self.refs.get(ref, ""))

    def standing_at(self, head):
        """clean, behind or dirty against main: behind when main is not in
        the commit, dirty when a path both sides changed since the merge
        base differs between them."""
        main = self.refs[MAIN]
        if not head or self.is_ancestor(main, head):
            return CLEAN
        base = self.merge_base(head, main)
        ours, theirs = set(self.changed(base, head)), set(self.changed(base, main))
        head_tree, main_tree = self.tree(head), self.tree(main)
        if any(head_tree.get(p) != main_tree.get(p) for p in ours & theirs):
            return DIRTY
        return BEHIND

    def files_changed(self, ref):
        """The paths the branch of this name here changes (files_changed_at)."""
        return self.files_changed_at(self.refs.get(ref, ""))

    def files_changed_at(self, head):
        """The paths a commit changes beside its merge base with main, as a
        pull request's files are."""
        if not head:
            return []
        return self.changed(self.merge_base(head, self.refs[MAIN]), head)

    def pull_head(self, pull):
        """The commit a pull request's branch points at: the ref of that
        name here, or a fork's head, which no ref here names."""
        return pull.sha if pull.fork else self.refs.get(pull.head, "")

    def pull_standing(self, pull):
        return self.standing_at(self.pull_head(pull))

    def pull_files(self, pull):
        return self.files_changed_at(self.pull_head(pull))

    def open_pull_from(self, ref):
        """The open pull request from the branch of this name here; never
        one from a fork, whose branch is another (F17)."""
        return next((p for p in self.pulls.values() if p.head == ref and p.state == "open" and not p.fork), None)

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

    # --- the board workflow's queue -------------------------------------------

    def fire(self, events, skipped_runs=False):
        """The last act's events, as (event, ref name), reached the board
        workflow, which queues a run for each (board.yml's push, create,
        delete, pull_request and issues). With the group idle the first
        starts, and GitHub cancels no run in progress; a run queued behind
        one is pending, and a newer pending run replaces it, so whatever
        waited is cancelled (github/docs, actions-group-concurrency.md).
        Which of several fired at once starts, and which of the rest is the
        one left pending, is not known, so all are kept as candidates until
        one starts. A push to any branch but the default queues a run whose
        job is skipped (board.yml's condition): it holds the group for the
        seconds the skip takes and does nothing. With skipped_runs, which
        the split scope sets, it is kept as a run like any other, since
        something can happen while it holds the group; without, it is left
        out, which is exact for a run made whole, since nothing happens
        while one is in progress: with the group idle it runs and is gone,
        and with the group busy it takes the pending slot, so whatever
        waited is cancelled."""
        runs = tuple(sorted(set(events)))
        if not skipped_runs:
            runs = tuple(e for e in runs if not skipped(e))
        if self.running is None and not self.queued:
            self.queued = runs
            self.pending = runs if len(runs) > 1 else ()
        else:
            self.pending = runs

    def starts(self, event, ref_name):
        """A board run for the event starts: the one queued leaves the queue
        and its fellows are the candidates for the run pending behind it,
        since nothing fires between a run's queuing and its start (the
        verbs offer no other act while a run is queued); the pending one
        leaves its fellows cancelled; any other, the schedule's or one a
        trace names, came as a newer event and replaced what waited."""
        run = (event, ref_name)
        if run in self.queued:
            self.queued = ()
            self.pending = tuple(e for e in self.pending if e != run)
        else:
            self.pending = ()

    def finished(self):
        """The run in progress is done: the one pending behind it starts,
        unless one a trace started out of turn left the queued run waiting."""
        if not self.queued:
            self.queued, self.pending = self.pending, ()

    # --- the key ------------------------------------------------------------

    def canonical(self):
        """Everything any guarantee or the oracle reads, as one hashable
        value: the repository, the runs queued, and the run in progress by
        what it started from, since what its first half did and what its
        second will do follow from that."""
        return self.repository_key() + (self.queued, self.pending, self.running.key() if self.running else None)

    def repository_key(self):
        """The repository alone, without the runs queued or the run in
        progress: HalfRun.key and the checks of a run in two halves compare
        the model before and after a run starts, when the queue has moved
        but nothing the run reads has."""
        return (
            self.now,
            tuple(sorted(self.refs.items())),
            tuple(sorted((n, i.state, tuple(sorted(i.labels)), i.comments, i.body) for n, i in self.issues.items())),
            tuple(sorted((n, p.head, p.author, p.body, p.draft, p.state, p.opened_at, p.updated_at, p.merged_at,
                          p.comments, p.fork, p.sha) for n, p in self.pulls.items())),
            self.next_pull,
            self.writers,
            tuple(sorted(self.ever_heads)),
            self.label_events,
            tuple(sorted(self.creators.items())),
            tuple(sorted(self.created.items())),
            tuple(sorted((w, tuple(sorted(refs.items()))) for w, refs in self.tracking.items())),
            self.board_text,
            self.opaque_pulls,
            self.lenient_rules,
        )

    def digest(self):
        """A 16-byte digest of the key, for the deep scopes' seen-set."""
        return hashlib.sha256(repr(self.canonical()).encode("utf-8")).digest()[:16]

    def clone(self):
        """A copy no verb on it changes this one through. The commits, the
        comments and the label events are immutable records, so they are
        shared; the issues, the pull requests and the maps are copied one
        level deep, which is every level a verb writes. A run between its
        halves holds a client over this model, so a model with one is
        copied whole."""
        if self.running is not None:
            return copy.deepcopy(self)
        return RepoModel(
            now=self.now, commits=dict(self.commits), refs=dict(self.refs),
            issues={n: copy.copy(i) for n, i in self.issues.items()},
            pulls={n: copy.copy(p) for n, p in self.pulls.items()},
            next_pull=self.next_pull, writers=self.writers, ever_heads=self.ever_heads,
            label_events=self.label_events, creators=dict(self.creators), created=dict(self.created),
            tracking={w: dict(refs) for w, refs in self.tracking.items()}, board_text=self.board_text,
            queued=self.queued, pending=self.pending, running=None, opaque_pulls=self.opaque_pulls,
            lenient_rules=self.lenient_rules,
        )


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
