"""The lock table's git side: reading LOCKS.json from the branch `locks`,
writing a new table as one commit on the tip it read, and pushing it, with
the claim branch beside it when the locks are taken at the claim.

Every change is read, decided and pushed again from the start when the
push is refused because the table moved, so the decision is always made
on the tip the push replaces; git refuses a push that is not a fast-forward
of the remote's tip, which makes the push a compare-and-swap. Nothing here
forces a push or deletes a ref: the table only moves forward, and a lock
ends by an entry leaving it (sdlc_protocol.locks).

A claim is live while its branch `claude/<n>` is on the remote and its
head is not yet in the default branch. That is what git alone can tell;
the board's run, which reads the issues, also ends the locks of a claim
whose issue is closed or whose release it counted (live_claims's
`ended`).
"""

import subprocess

from .. import claims, locks
from ..gate import GateFailed
from .bare_git import GIT_TIMEOUT_SECONDS, git_environment, git_options, last_line, run_git

LOCKS_NAME = "symphony locks"
LOCKS_EMAIL = "locks@symphony.local"
OPTIONS = git_options(LOCKS_NAME, LOCKS_EMAIL)
TRIES = 5

# The refusals git prints for a ref that some other ref of an atomic push
# took down with it, as against the ref that was itself refused.
SWEPT_ALONG = ("atomic push failed", "atomic transaction failed")


class LocksFailed(Exception):
    """The table could not be read or moved; the message says why."""


class ClaimTaken(Exception):
    """The claim branch was refused: another worker holds the issue."""


def git(repo, *args):
    try:
        return run_git(list(args), cwd=repo, options=OPTIONS)
    except GateFailed as problem:
        raise LocksFailed(str(problem))


def tracking_ref(remote):
    return f"refs/remotes/{remote}/{locks.LOCKS_BRANCH}"


def fetch_table(repo, remote):
    """The table's tip on the remote and its entries; no tip and no
    entries while the branch `locks` is not there yet."""
    fetched = git(repo, "fetch", "--quiet", "--no-tags", remote,
                  f"+refs/heads/{locks.LOCKS_BRANCH}:{tracking_ref(remote)}")
    if fetched.returncode != 0:
        if "couldn't find remote ref" in fetched.stderr:
            return "", []
        raise LocksFailed(f"could not fetch {locks.LOCKS_BRANCH}: {last_line(fetched)}")
    tip = git(repo, "rev-parse", tracking_ref(remote)).stdout.strip()
    shown = git(repo, "show", f"{tip}:{locks.TABLE_FILE}")
    if shown.returncode != 0:
        raise LocksFailed(f"{locks.LOCKS_BRANCH} at {tip[:12]} holds no {locks.TABLE_FILE}")
    try:
        return tip, locks.read_table(shown.stdout)
    except (ValueError, KeyError, TypeError) as problem:
        raise LocksFailed(f"{locks.TABLE_FILE} at {tip[:12]} cannot be read: {problem}")


def remote_heads(repo, remote, pattern):
    listed = git(repo, "ls-remote", remote, pattern)
    if listed.returncode != 0:
        raise LocksFailed(f"could not list {pattern} on {remote}: {last_line(listed)}")
    heads = {}
    for line in listed.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        heads[ref.removeprefix("refs/heads/")] = sha
    return heads


def live_claims(repo, remote, numbers, default_branch="main", ended=()):
    """Of the claims `numbers`, those still live: the branch on the
    remote, its head not in the default branch, and not among `ended`. A
    head whose ancestry cannot be read, as in a shallow clone, counts as
    live, since a lock dropped wrongly is worse than one kept a run
    longer."""
    wanted = {claims.branch_of(number): number for number in numbers if number not in set(ended)}
    if not wanted:
        return set()
    heads = remote_heads(repo, remote, "refs/heads/claude/*")
    present = {branch: heads[branch] for branch in wanted if branch in heads}
    if not present:
        return set()
    refspecs = [f"+refs/heads/{default_branch}:refs/remotes/{remote}/{default_branch}"]
    refspecs += [f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}" for branch in present]
    git(repo, "fetch", "--quiet", "--no-tags", remote, *refspecs)
    live = set()
    for branch, sha in present.items():
        landed = git(repo, "merge-base", "--is-ancestor", sha, f"refs/remotes/{remote}/{default_branch}")
        if landed.returncode != 0:
            live.add(wanted[branch])
    return live


def commit_table(repo, parent, entries, message):
    """A commit whose tree is LOCKS.json alone, on `parent` or as the
    branch's first commit."""
    blob = git_input(repo, ["hash-object", "-w", "--stdin"], locks.table_text(entries))
    tree = git_input(repo, ["mktree"], f"100644 blob {blob}\t{locks.TABLE_FILE}\n")
    parents = ["-p", parent] if parent else []
    return git_input(repo, ["commit-tree", tree, *parents], message)


def git_input(repo, args, text):
    try:
        done = subprocess.run(["git", *OPTIONS, *args], cwd=repo, input=text, capture_output=True, text=True,
                              errors="replace", timeout=GIT_TIMEOUT_SECONDS, env=git_environment())
    except (subprocess.TimeoutExpired, FileNotFoundError) as problem:
        raise LocksFailed(f"git {args[0]} failed: {problem}")
    if done.returncode != 0:
        raise LocksFailed(f"git {args[0]} failed: {last_line(done)}")
    return done.stdout.strip()


def push(repo, remote, refspecs):
    """Push the refspecs all or none, never forced; each destination ref
    with what git says of refusing it, such as "[rejected] (fetch
    first)", or "" where it was taken."""
    pushed = git(repo, "push", "--atomic", "--porcelain", remote, *refspecs)
    outcome = {}
    for line in pushed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] in ("*", " ", "+", "-", "!", "="):
            ref = parts[1].partition(":")[2]
            outcome[ref] = (parts[2] or "refused") if parts[0] == "!" else ""
    if pushed.returncode != 0 and not outcome:
        raise LocksFailed(f"could not push to {remote}: {last_line(pushed)}")
    return outcome


def change(repo, remote, decide, default_branch="main", claim_branch="", ended=(), tries=TRIES):
    """Read the table, let `decide(entries, live)` give the new entries,
    and push them as one commit on the tip read, with `claim_branch`'s
    local head pushed as that branch in the same atomic push when given.
    A refusal of the table alone starts over from a new read; a refusal
    of the claim branch is ClaimTaken. `decide` may raise locks.Held,
    which is passed on with nothing pushed."""
    for _ in range(tries):
        tip, entries = fetch_table(repo, remote)
        numbers = {entry.claim for entry in entries}
        alive = live_claims(repo, remote, numbers, default_branch, ended)
        after = decide(entries, lambda entry: entry.claim in alive)
        if after == entries and not claim_branch:
            return entries
        refspecs = []
        if after != entries or not tip:
            sha = commit_table(repo, tip, after, locks.change_message(entries, after))
            refspecs.append(f"{sha}:refs/heads/{locks.LOCKS_BRANCH}")
        if claim_branch:
            refspecs.append(f"refs/heads/{claim_branch}:refs/heads/{claim_branch}")
        outcome = push(repo, remote, refspecs)
        refused = {ref: why for ref, why in outcome.items() if why}
        if not refused:
            return after
        claim_ref = f"refs/heads/{claim_branch}"
        if claim_branch and refused.get(claim_ref) and not swept_along(refused[claim_ref]):
            raise ClaimTaken(f"{claim_branch} was refused ({refused[claim_ref]}): another worker holds the issue")
    raise LocksFailed(f"{locks.LOCKS_BRANCH} kept moving: {tries} tries were refused")


def swept_along(reason):
    return any(words in reason for words in SWEPT_ALONG)


def acquire(repo, remote, wanted, default_branch="main", claim_branch=""):
    return change(repo, remote, lambda entries, live: locks.acquire(entries, wanted, live),
                  default_branch, claim_branch)


def release(repo, remote, claim, default_branch="main"):
    return change(repo, remote, lambda entries, live: locks.release(locks.sweep(entries, live), claim),
                  default_branch)


def sweep(repo, remote, default_branch="main", ended=()):
    return change(repo, remote, lambda entries, live: locks.sweep(entries, live), default_branch, ended=ended)


def fork_point(repo, base, head):
    found = git(repo, "merge-base", base, head)
    if found.returncode != 0:
        raise LocksFailed(f"{base} and {head} share no history here: {last_line(found)}")
    return found.stdout.strip()


def added(repo, base, head):
    """The files the change adds, which no claim could have been editing
    before it."""
    names = git(repo, "diff", "--no-renames", "--diff-filter=A", "--name-only", "-z",
                fork_point(repo, base, head), head)
    return sorted(filter(None, names.stdout.split("\0")))


def touched(repo, base, head):
    """The keys the change from the merge base of `base` and `head` to
    `head` touches, file by file."""
    fork = fork_point(repo, base, head)
    names = git(repo, "diff", "--no-renames", "--name-only", "-z", fork, head)
    keys = set()
    for path in filter(None, names.stdout.split("\0")):
        shown = git(repo, "show", f"{fork}:{path}")
        base_text = shown.stdout if shown.returncode == 0 else None
        if git(repo, "cat-file", "-e", f"{head}:{path}").returncode != 0:
            base_text = None  # gone: the whole file
        diff = git(repo, "diff", "--no-renames", "--no-ext-diff", "-U0", fork, head, "--", path).stdout
        keys.update(locks.touched_keys(path, base_text, locks.hunks_of(diff)))
    return sorted(keys)
