"""The verbs: GitHub's and git's real operations, one each (DESIGN.md, 4.1).

A verb is one atomic act by one actor: a push, a label put on or taken
off, a comment, a pull request opened, marked ready or merged, the clock
moved, or a board run. None is a bundle: the prototype's "ready" verb
deleted the claim file, marked the pull request ready and labelled the
issue in-review at once, and the label then hid a mutant that released
ready pull requests. apply() changes the model in place and says what
happened; enabled() lists every verb a scope allows in a state, which is
what the checker explores.

Every push a worker makes is a git command, and before the model takes it
the push guard judges it, by the library's own guard.refusal, as the
template's PreToolUse hook does; a push the guard refuses never happens.

Actors: the workers of the scope, each a person's session that may write
(author_association COLLABORATOR); the owner; carol, a collaborator; dana,
a writer GitHub shows as a contributor, whom only the permission API
vouches for; sam, a stranger with no write access; the clock; the board.
"""

from typing import NamedTuple

from sdlc_protocol import guard

from . import cycle, oracle
from .fake_github import Fault
from .model import CLAIM_PREFIX, HOUR, MAIN, OWNER, RELEASED_PREFIX, Comment, PullRequest, claim_text

CLOCK, BOARD = "clock", "board"
CAROL, DANA, SAM = "carol", "dana", "sam"
ASSOCIATIONS = {OWNER: "OWNER", CAROL: "COLLABORATOR", DANA: "CONTRIBUTOR", SAM: "NONE"}
WORKER_ASSOCIATION = "COLLABORATOR"
CLAIM_DIR = "docs/agents/claims"

# The faults a board run can meet, by name (DESIGN.md, 3.2).
FAULTS = {
    "comment-500": Fault("POST", "/issues/*/comments", 500),
    "comment-lost": Fault("POST", "/issues/*/comments", 502, applied=True),
    "comment-rate-limited": Fault("POST", "/issues/*/comments", 403, message="You have exceeded a secondary rate limit",
                                  retry_after="60"),
    "label-delete-500": Fault("DELETE", "/issues/*/labels/*", 500),
    "label-post-500": Fault("POST", "/issues/*/labels", 500),
    "rename-403": Fault("POST", "/branches/*/rename", 403),
    "rename-422": Fault("POST", "/branches/*/rename", 422),
    "ref-delete-422": Fault("DELETE", "/git/refs/heads/*", 422),
    "compare-404": Fault("GET", "/compare/*", 404),
    "comments-read-500": Fault("GET", "/issues/*/comments", 500),
}


class Verb(NamedTuple):
    name: str
    actor: str
    args: tuple = ()

    def __str__(self):
        return " ".join([self.name, self.actor] + [str(a) for a in self.args])


class Outcome(NamedTuple):
    """What a verb did: taken or not and why; the git command a push ran,
    the guard's refusal of it (None when it passed) and whether the rules
    allow the push's shape, which the guard must then pass; a board run's
    record."""

    taken: bool
    note: str = ""
    command: str = ""
    refused_by_guard: str | None = None
    run: object = None
    allowed_shape: bool = True


class Scope(NamedTuple):
    """What the checker may do (DESIGN.md, 6.2): which workers, issues and
    people act, which faults a board run may meet, how long the clock may
    wait, which events start a board run, whether workers may be careless
    (a lease onto another's claim, a misnamed claim), and the files each
    issue's claim lists, by issue."""

    workers: tuple = ("a", "b")
    issues: tuple = (1,)
    people: tuple = (OWNER,)
    faults: tuple = ()
    waits: tuple = (24, 25)
    events: tuple = (cycle.SCHEDULE,)
    careless: bool = False
    files: tuple = ()

    def files_of(self, number):
        return dict(self.files).get(number, (f"src/{number}.py",))


# --- helpers ------------------------------------------------------------------

def claim_ref(number):
    return f"{CLAIM_PREFIX}{number}"


def guard_says(command, current_branch):
    """The push guard's refusal of a command, or None when it passes."""
    for argv in guard.simple_commands(command):
        args = guard.push_arguments(argv)
        if args is not None:
            reason = guard.refusal(args, current_branch, MAIN)
            if reason:
                return reason
    return None


def pushed(model, worker, ref, sha, command, force=False, lease=False, allowed=True, guarded=True):
    """Run one push through the guard, unless it is made by hand outside a
    session, then git's rule."""
    refused = guard_says(command, ref) if guarded else None
    if refused:
        return Outcome(False, "refused by the guard", command, refused, allowed_shape=allowed)
    answer = model.push(worker, ref, sha, force=force, lease=lease)
    return Outcome(answer.taken, answer.why or "taken", command, allowed_shape=allowed)


def released_refs(model, number):
    return sorted(r for r in model.refs if r.startswith(f"{RELEASED_PREFIX}{number}-"))


def holder(model, number):
    return model.creators.get(claim_ref(number))


def edited(model, sha, worker, paths):
    tree = model.tree(sha)
    for path in paths:
        tree[path] = tree.get(path, "") + f"{worker} {model.now}\n"
    return tree


def apply_pull_change(model, pull):
    pull.updated_at = model.now


# --- the verbs ----------------------------------------------------------------

def claim(model, worker, number, files, skew=0, name=None, guarded=True):
    """Push the claim commit, the claim file alone, from main. A name that
    reads as a claim and claims nothing, as claude/03, is a shape the rules
    forbid (spec/PROTOCOL.md, Claim); a person's push by hand, outside a
    session, meets no guard."""
    ref = name or claim_ref(number)
    tree = model.tree(model.refs[MAIN])
    tree[f"{CLAIM_DIR}/{number}.md"] = claim_text(number, worker, files)
    sha = model.commit((model.refs[MAIN],), worker, tree, f"Claim #{number}", skew)
    return pushed(model, worker, ref, sha, f"git push origin {ref}", allowed=not oracle.reads_as_claim(ref),
                  guarded=guarded)


def commit(model, worker, number, files, skew=0):
    ref = claim_ref(number)
    head = model.refs[ref]
    sha = model.commit((head,), worker, edited(model, head, worker, files), f"Work on #{number}", skew)
    outcome = pushed(model, worker, ref, sha, f"git push origin {ref}")
    touch_pull(model, ref, outcome)
    return outcome


def drop_claim_file(model, worker, number):
    ref = claim_ref(number)
    head = model.refs[ref]
    tree = model.tree(head)
    tree.pop(f"{CLAIM_DIR}/{number}.md", None)
    outcome = pushed(model, worker, ref, model.commit((head,), worker, tree, f"Finish #{number}"),
                     f"git push origin {ref}")
    touch_pull(model, ref, outcome)
    return outcome


def merged_tree(model, head, main):
    """main's tree with the branch's own changes over it; where both changed
    a path, both texts, the branch's first, as a person resolves it."""
    base = model.merge_base(head, main)
    ours, theirs, result = model.tree(head), model.tree(main), model.tree(main)
    for path in model.changed(base, head):
        if path in ours and path in model.changed(base, main) and theirs.get(path) not in (None, ours[path]):
            result[path] = ours[path] + theirs[path]
        elif path in ours:
            result[path] = ours[path]
        else:
            result.pop(path, None)
    return result


def merge_main(model, worker, number):
    """Bring main in by a merge and a plain push, as a worker in Actions does."""
    ref = claim_ref(number)
    head, main = model.refs[ref], model.refs[MAIN]
    sha = model.commit((head, main), worker, merged_tree(model, head, main), f"Merge {MAIN} into {ref}")
    outcome = pushed(model, worker, ref, sha, f"git push origin {ref}")
    touch_pull(model, ref, outcome)
    return outcome


def rebase_main(model, worker, number):
    """Fetch, replay the branch's own commits onto main, and push with a
    lease, the one forced push the protocol allows."""
    model.fetch(worker)
    ref = claim_ref(number)
    head, main = model.refs[ref], model.refs[MAIN]
    own = model.in_order(model.ancestors(head) - model.ancestors(main))
    tip = main
    for sha in own:
        record = model.commits[sha]
        parent = record.parents[0] if record.parents else ""
        tree = model.tree(tip)
        for path in model.changed(parent, sha):
            if path in dict(record.tree):
                tree[path] = dict(record.tree)[path]
            else:
                tree.pop(path, None)
        tip = model.commit((tip,), record.author, tree, record.message)
    outcome = pushed(model, worker, ref, tip, f"git push --force-with-lease origin {ref}", lease=True)
    touch_pull(model, ref, outcome)
    return outcome


def lease_onto(model, worker, number, files):
    """The careless rebaser (F7): fetch, then a lease onto a claim another
    worker holds, with a claim file naming itself."""
    model.fetch(worker)
    ref = claim_ref(number)
    tree = model.tree(model.refs[MAIN])
    tree[f"{CLAIM_DIR}/{number}.md"] = claim_text(number, worker, files)
    sha = model.commit((model.refs[MAIN],), worker, tree, f"Claim #{number}")
    return pushed(model, worker, ref, sha, f"git push --force-with-lease origin {ref}", lease=True)


def push_old_head(model, worker, number, released_ref):
    """After a release, push the released head again as claude/<n>."""
    ref = claim_ref(number)
    return pushed(model, worker, ref, model.refs[released_ref], f"git push origin {released_ref}:{ref}")


def touch_pull(model, ref, outcome):
    pull = model.open_pull_from(ref)
    if outcome.taken and pull is not None:
        apply_pull_change(model, pull)


def comment(model, author, number, body):
    association = ASSOCIATIONS.get(author, WORKER_ASSOCIATION)
    model.add_comment(number, Comment(author, association, model.now, body))
    return Outcome(True, "commented")


def open_draft(model, worker, number):
    pull = PullRequest(model.next_pull, claim_ref(number), worker, f"Closes #{number}", f"Work on #{number}",
                       opened_at=model.now, updated_at=model.now)
    model.pulls[pull.number] = pull
    model.next_pull += 1
    return Outcome(True, f"opened #{pull.number}")


def mark_ready(model, number):
    pull = model.pulls[number]
    pull.draft = False
    apply_pull_change(model, pull)
    return Outcome(True, "marked ready")


def merge(model, number):
    """The owner merges a pull request: main gets a merge commit, the issue
    its body closes is closed, and the repository deletes the head."""
    pull = model.pulls[number]
    head, main = model.refs[pull.head], model.refs[MAIN]
    model.refs[MAIN] = model.commit((main, head), OWNER, merged_tree(model, head, main), f"Merge #{number}")
    pull.state, pull.merged_at = "merged", model.now
    apply_pull_change(model, pull)
    closed = oracle.claim_number(pull.head)
    if closed in model.issues:
        model.issues[closed].state = "closed"
    model.delete_ref(pull.head)
    return Outcome(True, "merged")


def move_main(model, path="README.md"):
    """Someone else lands a change on main."""
    main = model.refs[MAIN]
    model.refs[MAIN] = model.commit((main,), OWNER, edited(model, main, OWNER, (path,)), "Elsewhere")
    return Outcome(True, "main moved")


def tidy(model, number):
    """The tidy workflow: a closed issue loses claude/<n>, once no open pull
    request uses it, and every released/<n>-<sha> a release left."""
    for ref in [claim_ref(number)] + released_refs(model, number):
        if ref in model.refs and model.open_pull_from(ref) is None:
            model.delete_ref(ref)
    return Outcome(True, "tidied")


def rename_back(model, number, released_ref):
    model.rename_ref(released_ref, claim_ref(number))
    return Outcome(True, "renamed back")


def reopen(model, number):
    pull = model.pulls[number]
    pull.state = "open"
    apply_pull_change(model, pull)
    return Outcome(True, "reopened")


def board(model, event, ref_name="", faults=()):
    record = cycle.board_run(model, event, ref_name, tuple(FAULTS[f] for f in faults))
    return Outcome(True, f"exit {record.exit_code}", run=record)


# --- one verb on the model ----------------------------------------------------

def apply(model, verb, scope=Scope()):
    """Apply the verb to the model in place; what happened."""
    name, actor, args = verb
    if name == "wait":
        model.now += args[0] * HOUR
        return Outcome(True, "waited")
    if name == "board":
        return board(model, *args)
    if name == "move-main":
        return move_main(model, *args)
    number = args[0] if args else None
    if name == "claim":
        return claim(model, actor, number, scope.files_of(number), *args[1:])
    if name == "claim-misnamed":
        return claim(model, actor, number, scope.files_of(number), name=args[1])
    if name == "push-by-hand":
        return claim(model, actor, number, scope.files_of(number), name=args[1], guarded=False)
    if name == "commit":
        return commit(model, actor, number, scope.files_of(number), *args[1:])
    if name == "drop-claim-file":
        return drop_claim_file(model, actor, number)
    if name == "merge-main":
        return merge_main(model, actor, number)
    if name == "rebase-main":
        return rebase_main(model, actor, number)
    if name == "lease-onto":
        return lease_onto(model, actor, number, scope.files_of(number))
    if name == "push-old-head":
        return push_old_head(model, actor, number, args[1])
    if name == "label-on":
        model.add_label(number, actor, args[1])
        return Outcome(True, "labelled")
    if name == "label-off":
        return Outcome(model.remove_label(number, actor, args[1]), "label taken off")
    if name == "plan":
        return comment(model, actor, number, f"Plan for #{number}: do it in small commits.")
    if name == "finished":
        return comment(model, actor, number, "finished; ready to mark")
    if name == "marker":
        return comment(model, actor, number, oracle.RELEASE_MARKER + "\nreleased by hand")
    if name == "open-draft":
        return open_draft(model, actor, number)
    if name == "mark-ready":
        return mark_ready(model, number)
    if name == "merge":
        return merge(model, number)
    if name == "close-issue":
        model.issues[number].state = "closed"
        return Outcome(True, "closed")
    if name == "tidy":
        return tidy(model, number)
    if name == "rename-back":
        return rename_back(model, number, args[1])
    if name == "reopen":
        return reopen(model, number)
    raise ValueError(f"no verb {name}")


# --- what a scope allows ------------------------------------------------------

def enabled(model, scope):
    """Every verb the scope allows in this state, in a fixed order."""
    found = [Verb("wait", CLOCK, (hours,)) for hours in scope.waits]
    found += [Verb("board", BOARD, (event, MAIN if event == cycle.PUSH else "")) for event in scope.events]
    found += [Verb("board", BOARD, (scope.events[0], MAIN if scope.events[0] == cycle.PUSH else "", (fault,)))
              for fault in scope.faults]
    if OWNER in scope.people:
        found.append(Verb("move-main", OWNER))
    for number in scope.issues:
        found += issue_verbs(model, scope, number)
    return found


def issue_verbs(model, scope, number):
    issue, ref = model.issues.get(number), claim_ref(number)
    if issue is None:
        return []
    found = []
    pull = model.open_pull_from(ref) if ref in model.refs else None
    for worker in scope.workers:
        found += worker_verbs(model, scope, number, worker, pull)
    for person in scope.people:
        if ref in model.refs and issue.state == "open":
            found.append(Verb("marker", person, (number,)))
    if OWNER in scope.people:
        found += owner_verbs(model, number, pull)
    return found


def worker_verbs(model, scope, number, worker, pull):
    issue, ref = model.issues[number], claim_ref(number)
    found = []
    if issue.state == "open" and ref not in model.refs:
        found.append(Verb("claim", worker, (number,)))
        for old in released_refs(model, number):
            if model.creators.get(old) == worker:
                found.append(Verb("push-old-head", worker, (number, old)))
    if scope.careless and issue.state == "open":
        if ref in model.refs and holder(model, number) != worker:
            found.append(Verb("lease-onto", worker, (number,)))
        if f"{CLAIM_PREFIX}0{number}" not in model.refs:
            found.append(Verb("claim-misnamed", worker, (number, f"{CLAIM_PREFIX}0{number}")))
    if holder(model, number) != worker:
        return found
    found.append(Verb("commit", worker, (number,)))
    if f"{CLAIM_DIR}/{number}.md" in model.tree(model.refs[ref]):
        found.append(Verb("drop-claim-file", worker, (number,)))
    if not model.is_ancestor(model.refs[MAIN], model.refs[ref]):
        found += [Verb("merge-main", worker, (number,)), Verb("rebase-main", worker, (number,))]
    own = oracle.WORKER_SPELLINGS[0] + worker
    for label in (oracle.CLAIMED, own):
        if label not in issue.labels:
            found.append(Verb("label-on", worker, (number, label)))
    for label in sorted(issue.labels):
        if oracle.is_worker_label(label) and label != own:
            found.append(Verb("label-off", worker, (number, label)))
    if oracle.IN_REVIEW not in issue.labels:
        found.append(Verb("label-on", worker, (number, oracle.IN_REVIEW)))
    found.append(Verb("plan", worker, (number,)))
    if pull is None:
        found.append(Verb("open-draft", worker, (number,)))
    elif pull.author == worker and pull.draft:
        found += [Verb("mark-ready", worker, (pull.number,)), Verb("finished", worker, (pull.number,))]
    return found


def owner_verbs(model, number, pull):
    issue, ref = model.issues[number], claim_ref(number)
    found = []
    if pull is not None and not pull.draft and model.standing(ref) != "dirty":
        found.append(Verb("merge", OWNER, (pull.number,)))
    for label in sorted(issue.labels):
        if label == oracle.CLAIMED or oracle.is_worker_label(label):
            found.append(Verb("label-off", OWNER, (number, label)))
    if issue.state == "open" and oracle.CLAIMED not in issue.labels and oracle.release_times(model, number):
        found.append(Verb("label-on", OWNER, (number, oracle.CLAIMED)))
    for old in released_refs(model, number):
        if ref not in model.refs:
            found.append(Verb("rename-back", OWNER, (number, old)))
    for closed in sorted(model.pulls.values(), key=lambda p: p.number):
        if closed.state == "closed" and closed.head == ref and ref in model.refs and model.open_pull_from(ref) is None:
            found.append(Verb("reopen", OWNER, (closed.number,)))
    if issue.state == "open":
        found.append(Verb("close-issue", OWNER, (number,)))
    elif ref in model.refs or released_refs(model, number):
        found.append(Verb("tidy", OWNER, (number,)))
    return found
