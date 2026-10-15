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
The guard reads the claim files a lease is judged by (guard.other_holder)
from the model as git in the worker's clone would show them
(claim_files_in): the remote-tracking ref as the worker's last fetch or
push left it, and the branch pushed.

Actors: the workers of the scope, each a person's session that may write
(author_association COLLABORATOR); the owner; carol, a collaborator; dana,
a writer GitHub shows as a contributor, whom only the permission API
vouches for; sam, a stranger with no write access; the clock; the board.
Where the scope's forks is on, sam opens pull requests from a fork, from
a branch named there as a claim's, which is no claim's (F17); where its
strangers is on, sam opens them from a claim's own branch here, which are
no claim's either, and the claim's holder may close one to open its own
(F19).

Two comments a person can write that look like the coordinator's, offered
where the scope has more than one issue: a worker's comment that quotes
an overlap marker on a later line, as a reply does, and a stranger's
comment whose first line is one, naming the other issues. Neither tells
the work of anything (spec, Overlaps: the coordinator's own comment, by
the repository's workflows), so the coordinator still owes its comment;
a reader that took either for the coordinator's would stay silent. And
carry-on, the next worker's cherry-pick of a released claim's work after
its own claim commit (spec, Release: "may carry the released work on").

Every act GitHub tells the board workflow of fires its events, as
board.yml's triggers name them (a push, a branch made or deleted, a pull
request opened, pushed to, reopened or closed, an issue closed or
labelled; a comment fires none, and neither does marking a pull request
ready), and the model queues a run for each the scope listens for
(RepoModel.fire). The board verb starts one of the runs queued, or the
schedule's, which replaces them; with the scope's split_board on, a run is
two verbs, board-start and board-finish, with any verb but the clock's
between them.
"""

from typing import NamedTuple

from sdlc_protocol import guard

from . import cycle, model as M, oracle
from .fake_github import LAGGING, MOVED, MOVED_TWICE, Fault
from .model import CLAIM_PREFIX, HOUR, MAIN, OWNER, RELEASED_PREFIX, Comment, PullRequest, claim_text

CLOCK, BOARD = "clock", "board"
CAROL, DANA, SAM = "carol", "dana", "sam"
ASSOCIATIONS = {OWNER: "OWNER", CAROL: "COLLABORATOR", DANA: "CONTRIBUTOR", SAM: "NONE"}
WORKER_ASSOCIATION = "COLLABORATOR"
CLAIM_DIR = "docs/agents/claims"

# A read of the activity with no type named: the read of the push that set
# a claim's head (github.read_push), told from the reads of a
# branch's making, which name branch_creation (F13).
PUSHES = (("activity_type", None),)
# The faults a board run can meet, by name (DESIGN.md, 3.2).
FAULTS = {
    "comment-500": Fault("POST", "/issues/*/comments", 500),
    "comment-lost": Fault("POST", "/issues/*/comments", 502, applied=True),
    "comment-rate-limited": Fault("POST", "/issues/*/comments", 403, message="You have exceeded a secondary rate limit",
                                  retry_after="60"),
    "label-delete-500": Fault("DELETE", "/issues/*/labels/*", 500),
    "label-post-500": Fault("POST", "/issues/*/labels", 500),
    # A label GitHub writes and loses its answer to, as a release's todo
    # can be (F15's review).
    "label-post-lost": Fault("POST", "/issues/*/labels", 502, applied=True),
    "rename-403": Fault("POST", "/branches/*/rename", 403),
    "rename-422": Fault("POST", "/branches/*/rename", 422),
    "ref-delete-422": Fault("DELETE", "/git/refs/heads/*", 422),
    "compare-404": Fault("GET", "/compare/*", 404),
    "comments-read-500": Fault("GET", "/issues/*/comments", 500),
    # The first read of the branches' making in the run (F13), the newest
    # creations' or a branch's own.
    "activity-500": Fault("GET", "/activity", 500, query=(("activity_type", "branch_creation"),)),
    # Every read of the activity in the run, the newest creations, each
    # branch's own and the pushes of the claims' heads alike, as for a
    # token that may not read it: a claim's push is read twice, since a 500
    # may be answered later, and dated by its committer date.
    "activity-500-all": Fault("GET", "/activity", 500, times=100),
    # The first read of a claim's head's push in the run (F8), which GitHub
    # may answer later: the run reads it once more, which is answered, and
    # dates the claim by its push.
    "pushes-500": Fault("GET", "/activity", 500, query=PUSHES),
    # Every read of a claim's head's push refused for good, as for a token
    # that may not read the activity: each claim's read once and dated by
    # its committer date, which the run's log says.
    "pushes-403": Fault("GET", "/activity", 403, times=100, message="Resource not accessible by integration",
                        query=PUSHES),
    # The first read of a claim's head's push answered before the activity
    # lists the push that set the head: the run reads it once more, which
    # lists it, and dates the claim by its push.
    "pushes-lagging": Fault("GET", "/activity", 200, query=PUSHES, answer=LAGGING),
    # The first two such reads, a claim's first and the one more it takes,
    # both answered before the activity lists the push that set the head,
    # each a push behind: the entry before that push is newest, the push
    # before it or the branch's making, and stale, so neither dates the
    # push, and the claim is dated by that entry, the floor, where it came
    # later than the head's committer date, as for a commit dated back,
    # and by its committer date where not, each said in the run's log
    # (github.NO_PUSH_LISTED, SPEC["floor"]); until F8's fix check the
    # committer date stood in whatever the reads listed. A branch whose
    # making is its only entry is listed with none, so its committer date
    # stands in.
    "pushes-lagging-twice": Fault("GET", "/activity", 200, times=2, query=PUSHES, answer=LAGGING),
    # The first read of a claim's head's push listing a push that came
    # after the run read the branch, from the head it read to another: the
    # branch had a commit then, which dates the claim (SPEC["moved off"]).
    "pushes-moved": Fault("GET", "/activity", 200, query=PUSHES, answer=MOVED),
    # The first such read listing two pushes since the run read the
    # branch, the newest naming the head the run read on neither side: it
    # came after the run began, so it dates the claim, and the run reads no
    # more (SPEC["moved off"]: an entry that set the branch to another
    # head; F8's fix check, which found the library dating nothing by it).
    "pushes-moved-twice": Fault("GET", "/activity", 200, query=PUSHES, answer=MOVED_TWICE),
    # The first such read refused with a 500, which GitHub may answer
    # later, and the one more it takes listing a push after the run read
    # the branch: the run dates the claim by that push, judging the second
    # read by when the run began as it judged the first; one that reads it
    # with no such moment dates nothing by it and says so in its log, the
    # floor dating the claim at the same minute (F8's fix check).
    "pushes-500-then-moved": Fault("GET", "/activity", 500, query=PUSHES,
                                   then=Fault("GET", "/activity", 200, query=PUSHES, answer=MOVED)),
    # The first such read a push behind, its newest entry stale, and the
    # one more it takes refused with a 500: the second lists nothing, so
    # the floor is the first read's, which dates the claim where it came
    # later than the head's committer date (SPEC["floor"]: the newest
    # entry either read listed; F8's fix check).
    "pushes-lagging-then-500": Fault("GET", "/activity", 200, query=PUSHES, answer=LAGGING,
                                     then=Fault("GET", "/activity", 500, query=PUSHES)),
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
    record, or the first half of one (a cycle.HalfRun) when the run goes
    on; and the workflow events the act fired, as (event, ref name)."""

    taken: bool
    note: str = ""
    command: str = ""
    refused_by_guard: str | None = None
    run: object = None
    allowed_shape: bool = True
    guarded: bool = True
    events: tuple = ()
    half: object = None


class Scope(NamedTuple):
    """What the checker may do (DESIGN.md, 6.2): which workers, issues and
    people act, which faults a board run may meet, how long the clock may
    wait, which events start a board run (the schedule, which the checker
    fires when it likes, and the events the verbs fire, which queue runs
    only when listed here), whether a board run is made in two halves with
    other verbs between them, whether workers may be careless (a lease
    onto another's claim, a misnamed claim, and the owner's push of one by
    hand, which meets no guard), the files each issue's claim lists, by
    issue, the clock skews, in hours, a worker's commits may carry (F8: a
    pusher whose clock is ahead or behind stamps its commits so, which
    since F8's fix decides idleness only where GitHub will not date the
    push of the head and lists no entry of the branch later than the
    commit's date, as under the faults pushes-403 and activity-500-all,
    and pushes-lagging-twice for a commit dated ahead or a branch whose
    making is its only entry; a commit dated back under
    pushes-lagging-twice or pushes-lagging-then-500 is dated by the floor,
    the newest entry listed, since F8's fix check), whether a stranger
    opens pull requests from a fork whose branch bears a claim's name
    (F17), and whether a stranger opens them from a claim's own branch
    here (F19)."""

    workers: tuple = ("a", "b")
    issues: tuple = (1,)
    people: tuple = (OWNER,)
    faults: tuple = ()
    waits: tuple = (24, 25)
    events: tuple = (cycle.SCHEDULE,)
    careless: bool = False
    files: tuple = ()
    split_board: bool = False
    skews: tuple = ()
    forks: bool = False
    strangers: bool = False

    def files_of(self, number):
        return dict(self.files).get(number, (f"src/{number}.py",))


# --- helpers ------------------------------------------------------------------

def claim_ref(number):
    return f"{CLAIM_PREFIX}{number}"


def guard_says(command, current_branch, claim_file_at=None):
    """The push guard's refusal of a command, or None when it passes;
    claim_file_at reads the claim files a lease is judged by, as
    guard.refusal takes it."""
    for argv in guard.simple_commands(command):
        args = guard.push_arguments(argv)
        if args is not None:
            reason = guard.refusal(args, current_branch, MAIN, claim_file_at)
            if reason:
                return reason
    return None


def claim_file_at(model, head, number):
    """The claim file of issue number at a commit, at its tree, else where
    the commit's history last added it (a commit whose tree has it and
    whose parents' do not), as guard.claim_file_reader reads it with git;
    None when neither has it."""
    path = f"{CLAIM_DIR}/{number}.md"
    if not head:
        return None
    text = model.tree(head).get(path)
    if text is None:
        for commit in reversed(model.in_order(model.ancestors(head))):
            record = model.commits[commit]
            if path in dict(record.tree) and not any(path in model.tree(p) for p in record.parents):
                return dict(record.tree)[path]
    return text


def claim_files_in(model, worker, ref, sha):
    """What git in the worker's clone shows the guard before a push of sha
    to ref (guard.claim_file_reader): a claim file at refs/remotes/origin/
    is read at the worker's remote-tracking ref, as its last fetch or push
    left it; at a commit, at that commit; at anything else, the branch
    pushed, which is sha."""
    def read(name, number):
        if name.startswith(guard.REMOTE_REFS):
            head = model.tracking.get(worker, {}).get(name[len(guard.REMOTE_REFS):].partition("/")[2])
        elif name in model.commits:
            head = name
        else:
            head = sha
        return claim_file_at(model, head, number)
    return read


def pushed(model, worker, ref, sha, command, force=False, lease=False, allowed=True, guarded=True):
    """Run one push through the guard, unless it is made by hand outside a
    session, then git's rule. A push taken fires push, and create when it
    made the branch."""
    refused = guard_says(command, ref, claim_files_in(model, worker, ref, sha)) if guarded else None
    if refused:
        return Outcome(False, "refused by the guard", command, refused, allowed_shape=allowed)
    created = ref not in model.refs
    answer = model.push(worker, ref, sha, force=force, lease=lease)
    events = ((cycle.PUSH, ref),) + (((cycle.CREATE, ref),) if created else ()) if answer.taken else ()
    return Outcome(answer.taken, answer.why or "taken", command, allowed_shape=allowed, guarded=guarded,
                   events=events)


def overlap_marker(numbers):
    """The coordinator's overlap marker naming these numbers, as a person
    or a stranger might write it."""
    return f"<!-- sdlc-coordinator: overlap {' '.join(str(n) for n in numbers)} -->"


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
    return touch_pull(model, ref, pushed(model, worker, ref, sha, f"git push origin {ref}"))


def drop_claim_file(model, worker, number):
    ref = claim_ref(number)
    head = model.refs[ref]
    tree = model.tree(head)
    tree.pop(f"{CLAIM_DIR}/{number}.md", None)
    return touch_pull(model, ref, pushed(model, worker, ref, model.commit((head,), worker, tree, f"Finish #{number}"),
                                         f"git push origin {ref}"))


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
    return touch_pull(model, ref, pushed(model, worker, ref, sha, f"git push origin {ref}"))


def patch_of(model, sha):
    """A commit's change as git's patch-id reads it, near enough: each
    path it changes, with the text before and after."""
    record = model.commits[sha]
    parent = record.parents[0] if record.parents else ""
    before, after = model.tree(parent), dict(record.tree)
    return tuple((path, before.get(path), after.get(path)) for path in model.changed(parent, sha))


def rebase_main(model, worker, number):
    """Fetch, replay the branch's own commits onto main, and push with a
    lease, the one forced push the protocol allows. A merge commit the
    branch holds, one that brought main in, is dropped, as git's rebase
    drops it (the git world runs `git rebase origin/main`): replayed, its
    change against its first parent would write main's old text over main
    as it is now, where git keeps main's. A commit whose patch main holds
    already (patch_of: a copy carried on from a released branch, or the
    same change made on another claim at the same hour) is dropped too,
    as git's rebase drops a commit whose patch-id is upstream: replayed,
    it would write the commit's text over main's newer text."""
    model.fetch(worker)
    ref = claim_ref(number)
    head, main = model.refs[ref], model.refs[MAIN]
    own = model.in_order(model.ancestors(head) - model.ancestors(main))
    upstream = {patch_of(model, sha) for sha in model.ancestors(main) - model.ancestors(head)
                if len(model.commits[sha].parents) <= 1}
    tip = main
    for sha in own:
        record = model.commits[sha]
        if len(record.parents) > 1 or patch_of(model, sha) in upstream:
            continue
        parent = record.parents[0] if record.parents else ""
        tree = model.tree(tip)
        for path in model.changed(parent, sha):
            if path in dict(record.tree):
                tree[path] = dict(record.tree)[path]
            else:
                tree.pop(path, None)
        tip = model.commit((tip,), record.author, tree, record.message)
    return touch_pull(model, ref, pushed(model, worker, ref, tip, f"git push --force-with-lease origin {ref}",
                                         lease=True))


def lease_onto(model, worker, number, files):
    """The careless rebaser (F7): fetch, then a lease onto a claim another
    worker holds, with a claim file naming itself. Git would take it, so
    the guard must refuse it (spec/PROTOCOL.md, Work in the open: the
    claim file on origin names another worker)."""
    model.fetch(worker)
    ref = claim_ref(number)
    tree = model.tree(model.refs[MAIN])
    tree[f"{CLAIM_DIR}/{number}.md"] = claim_text(number, worker, files)
    sha = model.commit((model.refs[MAIN],), worker, tree, f"Claim #{number}")
    return pushed(model, worker, ref, sha, f"git push --force-with-lease origin {ref}", lease=True, allowed=False)


def claim_commit(model, number):
    """The claim commit of the branch claude/<number>: the first of the
    branch's own commits, those main does not hold, oldest first."""
    ref = claim_ref(number)
    own = model.in_order(model.ancestors(model.refs[ref]) - model.ancestors(model.refs[MAIN]))
    return own[0] if own else model.refs[ref]


def drop_commits(model, worker, number):
    """The holder drops every commit after its claim commit and pushes the
    branch back to it with a lease (git reset --hard, then git push
    --force-with-lease), the one forced push the protocol allows, onto its
    own claim: a push of a head the branch had before, which the activity
    lists as a forced push of it, and which an activity a push behind
    answers with the push that moved the branch off that head (F8's
    second review). No search offers it; F8's trace plays it."""
    model.fetch(worker)
    ref, sha = claim_ref(number), claim_commit(model, number)
    if sha == model.refs[ref]:
        return Outcome(True, "nothing to drop")
    return touch_pull(model, ref, pushed(model, worker, ref, sha, f"git push --force-with-lease origin {ref}",
                                         lease=True))


def push_old_head(model, worker, number, released_ref):
    """After a release, push the released head again as claude/<n>."""
    ref = claim_ref(number)
    return pushed(model, worker, ref, model.refs[released_ref], f"git push origin {released_ref}:{ref}")


def carry_on(model, worker, number, released_ref):
    """The next worker carries a released claim's work on: it cherry-picks
    the released branch's commits after its own claim commit, all but the
    old claim file, which its own claim file replaces (.claude/skills/claim
    in the template), and pushes. A merge commit on the released branch
    is skipped, as git's cherry-pick skips it (the git world's -n pick of
    one fails with no -m), since what it merged in is on main already."""
    ref = claim_ref(number)
    head, main = model.refs[ref], model.refs[MAIN]
    own = model.in_order(model.ancestors(model.refs[released_ref]) - model.ancestors(main))
    claim_path = f"{CLAIM_DIR}/{number}.md"
    tip = head
    for sha in own:
        record, parent = model.commits[sha], (model.commits[sha].parents or ("",))[0]
        if len(record.parents) > 1:
            continue
        changed = [p for p in model.changed(parent, sha) if p != claim_path]
        if not changed:
            continue
        tree = model.tree(tip)
        for path in changed:
            if path in dict(record.tree):
                tree[path] = dict(record.tree)[path]
            else:
                tree.pop(path, None)
        tip = model.commit((tip,), worker, tree, record.message)
    if tip == head:
        return Outcome(True, "nothing to carry on")
    return touch_pull(model, ref, pushed(model, worker, ref, tip, f"git push origin {ref}"))


def touch_pull(model, ref, outcome):
    """A push taken to a branch with an open pull request moves the pull
    request too, and GitHub tells the workflow of it (pull_request
    synchronize)."""
    pull = model.open_pull_from(ref)
    if outcome.taken and pull is not None:
        apply_pull_change(model, pull)
        return outcome._replace(events=outcome.events + ((cycle.PULL_REQUEST, ""),))
    return outcome


def comment(model, author, number, body):
    association = ASSOCIATIONS.get(author, WORKER_ASSOCIATION)
    model.add_comment(number, Comment(author, association, model.now, body))
    return Outcome(True, "commented")


def open_draft(model, worker, number):
    """The worker opens its draft from its claim branch to main, which
    GitHub refuses while a pull request between the two is open, as a
    stranger's may be (F19)."""
    if model.open_pull_from(claim_ref(number)) is not None:
        return Outcome(False, f"a pull request already exists for {claim_ref(number)}")
    pull = PullRequest(model.next_pull, claim_ref(number), worker, f"Closes #{number}", f"Work on #{number}",
                       opened_at=model.now, updated_at=model.now,
                       association=ASSOCIATIONS.get(worker, WORKER_ASSOCIATION))
    model.pulls[pull.number] = pull
    model.next_pull += 1
    return Outcome(True, f"opened #{pull.number}", events=((cycle.PULL_REQUEST, ""),))


def fork_pull(model, number, files, ready):
    """sam, a stranger, opens a pull request from a fork, from a branch named
    claude/<number> there: one commit on main's tip changing the issue's
    files, which stands in the fork, so no ref here names it. Its body
    closes the issue, as a claim's would, and it may be opened ready
    (F17). GitHub runs board.yml for it, and the job skips such a run,
    whose token is read-only (model.skipped)."""
    main = model.refs[MAIN]
    sha = model.commit((main,), SAM, edited(model, main, SAM, files), f"Fix #{number}")
    pull = PullRequest(model.next_pull, claim_ref(number), SAM, f"Closes #{number}", f"Fix #{number}",
                       draft=not ready, opened_at=model.now, updated_at=model.now, fork=True, sha=sha,
                       association=ASSOCIATIONS[SAM])
    model.pulls[pull.number] = pull
    model.next_pull += 1
    return Outcome(True, f"opened #{pull.number} from a fork", events=((cycle.PULL_REQUEST, M.FORK_RUN),))


def stranger_pull(model, number, ready):
    """sam, a stranger, opens a pull request from the claim's own branch
    here, as GitHub's documentation says anyone who may read the repository
    can (creating-a-pull-request.md:4): at the branch's head as it stands,
    a draft or ready, its body closing the issue as a claim's would (F19).
    GitHub opens one pull request at a time between two branches, so it is
    offered only while none from the branch is open. GitHub runs board.yml
    for it as for any pull request from a branch here."""
    pull = PullRequest(model.next_pull, claim_ref(number), SAM, f"Closes #{number}", f"Fix #{number}",
                       draft=not ready, opened_at=model.now, updated_at=model.now, association=ASSOCIATIONS[SAM])
    model.pulls[pull.number] = pull
    model.next_pull += 1
    return Outcome(True, f"opened #{pull.number} from {claim_ref(number)}", events=((cycle.PULL_REQUEST, ""),))


def close_pull(model, number):
    """The claim's holder closes a stranger's pull request from its branch,
    as anyone who may write can, to open its own (spec, Work in the open;
    F19); GitHub tells the board workflow (pull_request closed)."""
    pull = model.pulls[number]
    pull.state = "closed"
    apply_pull_change(model, pull)
    return Outcome(True, "closed", events=((cycle.PULL_REQUEST, ""),))


def open_fork_pull(model, number):
    """sam's open pull request from the fork's claude/<number>, or None."""
    return next((p for p in model.pulls.values() if p.fork and p.state == "open" and p.head == claim_ref(number)),
                None)


def mark_ready(model, number):
    pull = model.pulls[number]
    pull.draft = False
    apply_pull_change(model, pull)
    return Outcome(True, "marked ready")


def merge(model, number):
    """The owner merges a pull request: main gets a merge commit, the issue
    its body closes is closed, and the repository deletes the head; GitHub
    fires the four events at once (DESIGN.md, F12)."""
    pull = model.pulls[number]
    head, main = model.refs[pull.head], model.refs[MAIN]
    model.refs[MAIN] = model.commit((main, head), OWNER, merged_tree(model, head, main), f"Merge #{number}")
    pull.state, pull.merged_at = "merged", model.now
    apply_pull_change(model, pull)
    closed = oracle.claim_number(pull.head)
    if closed in model.issues:
        model.issues[closed].state = "closed"
    model.delete_ref(pull.head)
    return Outcome(True, "merged", events=((cycle.PUSH, MAIN), (cycle.PULL_REQUEST, ""), (cycle.ISSUES, ""),
                                           (cycle.DELETE, MAIN)))


def move_main(model, path="README.md"):
    """Someone else lands a change on main."""
    main = model.refs[MAIN]
    model.refs[MAIN] = model.commit((main,), OWNER, edited(model, main, OWNER, (path,)), "Elsewhere")
    return Outcome(True, "main moved", events=((cycle.PUSH, MAIN),))


def tidy(model, number):
    """The tidy workflow: a closed issue loses claude/<n>, once no open pull
    request uses it, and every released/<n>-<sha> a release left. Its
    deletes are made with GITHUB_TOKEN, which starts no workflow run."""
    for ref in [claim_ref(number)] + released_refs(model, number):
        if ref in model.refs and model.open_pull_from(ref) is None:
            model.delete_ref(ref)
    return Outcome(True, "tidied")


def rename_back(model, number, released_ref):
    """A person renames the branch back by hand: a branch made and one
    deleted, as GitHub tells the workflow of a rename."""
    model.rename_ref(released_ref, claim_ref(number))
    return Outcome(True, "renamed back", events=((cycle.CREATE, claim_ref(number)), (cycle.DELETE, MAIN)))


def reopen(model, number):
    pull = model.pulls[number]
    pull.state = "open"
    apply_pull_change(model, pull)
    return Outcome(True, "reopened", events=((cycle.PULL_REQUEST, ""),))


def board(model, event, ref_name="", faults=()):
    """A board run made whole for the event, which leaves the queue if it
    was there and replaces what waited if not; a run the job skips, a push
    to a claim branch, holds the group for a moment and does nothing."""
    model.starts(event, ref_name)
    if M.skipped((event, ref_name)):
        model.finished()
        return Outcome(True, "a run of nothing")
    record = cycle.board_run(model, event, ref_name, tuple(FAULTS[f] for f in faults))
    return Outcome(True, f"exit {record.exit_code}", run=record)


def board_start(model, event, ref_name="", faults=()):
    """The first half of a board run, which the checks judge at once; the
    run goes on in model.running. A run the job skips goes on there too,
    reading and writing nothing, so that what happens while it holds the
    group is seen."""
    model.starts(event, ref_name)
    if M.skipped((event, ref_name)):
        model.running = cycle.SkippedRun(event, ref_name)
        return Outcome(True, "a run of nothing in progress")
    half = cycle.start_run(model, event, ref_name, tuple(FAULTS[f] for f in faults))
    return Outcome(True, "first half done", half=half)


def board_finish(model):
    """The second half of the run in progress, whose record the checks
    judge, with the states it started from."""
    if isinstance(model.running, cycle.SkippedRun):
        model.running = None
        model.finished()
        return Outcome(True, "the run of nothing done")
    record = cycle.finish_run(model)
    return Outcome(True, f"exit {record.exit_code}", run=record)


# --- one verb on the model ----------------------------------------------------

def apply(model, verb, scope=Scope()):
    """Apply the verb to the model in place; what happened. The events the
    act fired queue board runs when the scope listens for them."""
    outcome = act(model, verb, scope)
    fired = tuple(e for e in outcome.events if e[0] in scope.events)
    if fired:
        model.fire(fired, skipped_runs=scope.split_board)
    return outcome


def act(model, verb, scope):
    name, actor, args = verb
    if model.store is not None and model.store.handles(name):
        return model.store.act(model, verb, scope)
    if name == "wait":
        model.now += args[0] * HOUR
        return Outcome(True, "waited")
    if name == "board":
        return board(model, *args)
    if name == "board-start":
        return board_start(model, *args)
    if name == "board-finish":
        return board_finish(model)
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
    if name == "drop-commits":
        return drop_commits(model, actor, number)
    if name == "label-on":
        if args[1] in model.issues[number].labels:
            return Outcome(True, "labelled already")
        model.add_label(number, actor, args[1])
        return Outcome(True, "labelled", events=((cycle.ISSUES, ""),))
    if name == "label-off":
        taken = model.remove_label(number, actor, args[1])
        return Outcome(taken, "label taken off", events=((cycle.ISSUES, ""),) if taken else ())
    if name == "plan":
        return comment(model, actor, number, f"Plan for #{number}: do it in small commits.")
    if name == "finished":
        return comment(model, actor, number, "finished; ready to mark")
    if name == "marker":
        return comment(model, actor, number, oracle.RELEASE_MARKER + "\nreleased by hand")
    if name == "overlap-marker":
        return comment(model, actor, number, overlap_marker(args[1]) + "\ntold by hand")
    if name == "overlap-quote":
        return comment(model, actor, number, "about the overlap, quoting it:\n" + overlap_marker(args[1]))
    if name == "carry-on":
        return carry_on(model, actor, number, args[1])
    if name == "open-draft":
        return open_draft(model, actor, number)
    if name == "fork-pull":
        return fork_pull(model, number, scope.files_of(number), args[1])
    if name == "stranger-pull":
        return stranger_pull(model, number, args[1])
    if name == "close-pull":
        return close_pull(model, number)
    if name == "mark-ready":
        return mark_ready(model, number)
    if name == "merge":
        return merge(model, number)
    if name == "close-issue":
        model.issues[number].state = "closed"
        return Outcome(True, "closed", events=((cycle.ISSUES, ""),))
    if name == "tidy":
        return tidy(model, number)
    if name == "rename-back":
        return rename_back(model, number, args[1])
    if name == "reopen":
        return reopen(model, number)
    raise ValueError(f"no verb {name}")


# --- what a scope allows ------------------------------------------------------

def enabled(model, scope):
    """Every verb the scope allows in this state, in a fixed order. A run
    queued into an idle group starts within seconds, before anything else
    happens, so while one is queued the only verbs are its starts; while a
    run is between its halves the clock stands, since a run takes half a
    minute, and no other run starts, since the concurrency group runs one
    at a time."""
    found = []
    if model.running is not None:
        found.append(Verb("board-finish", BOARD))
    elif model.queued:
        return board_verbs(model, scope)
    else:
        found += [Verb("wait", CLOCK, (hours,)) for hours in scope.waits]
        found += board_verbs(model, scope)
    if OWNER in scope.people:
        found.append(Verb("move-main", OWNER))
    for number in scope.issues:
        found += issue_verbs(model, scope, number)
    return found


def board_verbs(model, scope):
    """The runs that can start: the run queued, which GitHub starts before
    anything else, or, with the group idle, the schedule's, whenever the
    checker likes; the faults the scope names are met on each. The schedule
    fires only into an idle group: arriving behind a run it would do what
    any other event does, replace the run pending."""
    name = "board-start" if scope.split_board else "board"
    starts = list(model.queued) or ([(cycle.SCHEDULE, "")] if cycle.SCHEDULE in scope.events else [])
    found = [Verb(name, BOARD, start) for start in starts]
    found += [Verb(name, BOARD, start + ((fault,),)) for start in starts for fault in scope.faults]
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
            if person == SAM and len(scope.issues) > 1:
                found.append(Verb("overlap-marker", person, (number, others(scope, number))))
    if scope.forks and issue.state == "open":
        found += fork_verbs(model, number)
    if scope.strangers and issue.state == "open":
        found += stranger_verbs(model, number, pull)
    if OWNER in scope.people:
        found += owner_verbs(model, number, pull)
        misnamed = f"{CLAIM_PREFIX}0{number}"
        if scope.careless and issue.state == "open" and misnamed not in model.refs:
            found.append(Verb("push-by-hand", OWNER, (number, misnamed)))
    return found


def worker_verbs(model, scope, number, worker, pull):
    issue, ref = model.issues[number], claim_ref(number)
    found = []
    if issue.state == "open" and ref not in model.refs:
        found.append(Verb("claim", worker, (number,)))
        found += [Verb("claim", worker, (number, skew * HOUR)) for skew in scope.skews]
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
    found += [Verb("commit", worker, (number, skew * HOUR)) for skew in scope.skews]
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
    if len(scope.issues) > 1:
        found.append(Verb("overlap-quote", worker, (number, others(scope, number))))
    if pull is None:
        found.append(Verb("open-draft", worker, (number,)))
    elif not oracle.opened_by_writer(model, pull):
        found.append(Verb("close-pull", worker, (pull.number,)))
    elif pull.author == worker and pull.draft:
        found += [Verb("mark-ready", worker, (pull.number,)), Verb("finished", worker, (pull.number,))]
    return found


def fork_verbs(model, number):
    """sam's acts on the fork's pull request for the issue: open one, as a
    draft or ready, where none is open, and say such a draft finished,
    as a claim's author would (F17)."""
    fork = open_fork_pull(model, number)
    if fork is None:
        return [Verb("fork-pull", SAM, (number, False)), Verb("fork-pull", SAM, (number, True))]
    return [Verb("finished", SAM, (fork.number,))] if fork.draft else []


def stranger_verbs(model, number, pull):
    """sam's acts on a pull request from the claim's own branch here: open
    one, as a draft or ready, while the branch stands and none from it is
    open, and say such a draft finished, as a claim's author would (F19)."""
    if claim_ref(number) not in model.refs:
        return []
    if pull is None:
        return [Verb("stranger-pull", SAM, (number, False)), Verb("stranger-pull", SAM, (number, True))]
    return [Verb("finished", SAM, (pull.number,))] if pull.author == SAM and pull.draft else []


def others(scope, number):
    """The scope's other issues, as the numbers an overlap marker on this
    issue would name."""
    return tuple(n for n in scope.issues if n != number)


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
        if (closed.state == "closed" and closed.head == ref and not closed.fork and ref in model.refs
                and model.open_pull_from(ref) is None):
            found.append(Verb("reopen", OWNER, (closed.number,)))
    if issue.state == "open":
        found.append(Verb("close-issue", OWNER, (number,)))
    elif ref in model.refs or released_refs(model, number):
        found.append(Verb("tidy", OWNER, (number,)))
    return found
