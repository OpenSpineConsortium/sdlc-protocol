"""The mutation lane (DESIGN.md, section 7, and step 2 of section 14).

A guarantee the harness never sees break is not shown to hold, so the
harness is tested the way it tests the library. A mutant changes one rule
of the library inside this process, by patching one attribute for as long
as the mutant is on, never in the files, the way a careless change could.
The lane runs every mutant against the searches below and requires every
one to be caught: some search must report a violation under the mutant
that it does not report on the library as it stands.

Three lanes come before the searches, since they are cheap and judge rules
no search runs: the regression traces, one per finding (replay.py, since
build step 5), which catch the mutants that undo a fix of 0.4.1 on the
trace of the finding fixed; the guard grammar against git's recorded
answers (guard_grammar.judge, over the plain commands alone here); and
dealing (dealing.run_lane); each reports its failures as the searches
report violations. The searches are breadth first (explore.explore), each from a
short prefix: a few verbs that put the repository where a family of rules
applies (a held claim, a draft, two claims on one file), then every
interleaving of the scope's verbs to a small depth. A prefix holds no
board run of its own, so it reaches the same state with or without a
mutant, and every step after it is searched, not chosen. Two lanes come
after the searches, each wider and slower than any search, so a mutant
reaches them only past every search: the clerk's deal against Pick
(dealing.picking_lane), from one look at every state a person can label
an issue into, held to the spec's Pick and Planning as the oracle reads
them (F18), and, last, every label mix on one issue (labels_lane): a
board run and a due claim's release on an issue carrying each mix of
labels the domain gives, the widest and the slowest catcher. The
hand-written traces in tests/test_validation_mutants.py are each
mutant's shortest example, kept so a failure names the rule that broke;
the lane is what shows the searches bite.

`python3 -m validation.mutants` runs the lane and prints, for each
mutant, the search that caught it with the shortest violation it met and
that violation's trace, then the count caught. The guarantee a mutant
names is the rule it changes; the violation reported is whatever the
search met first, often a comment the run owed or made as a result.
"""

import contextlib
import dataclasses
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from unittest import mock

from sdlc_protocol import board, claims, coordinator, guard, labels, team
from sdlc_protocol.adapters import github
from sdlc_protocol.workers import fits

from . import clerk, cycle, dealing, domains, explore, guard_grammar, invariants, model as M, oracle, replay, verbs
from .verbs import CAROL, DANA, SAM, Scope, Verb


class Mutant(NamedTuple):
    """One rule changed: the attribute module.name replaced, while the
    mutant is on, by replacement; the guarantee it should break, which the
    lane reports and does not require; and why the rule matters."""

    module: object
    name: str
    replacement: object
    guarantee: str
    why: str


class Lane(NamedTuple):
    """A lane that judges a rule no search runs: run() gives what it finds,
    as a mapping from a key, (guarantee, detail), to how it was found, in
    the shape a search's violations have."""

    name: str
    run: object
    known: tuple = ()


class Search(NamedTuple):
    """One breadth-first search: the verbs that bring the seeded repository
    to its start, the scope searched from there and how deep, and the
    violations, as (guarantee, detail), the library as it stands is known
    to meet in it; opaque_pulls has GitHub call every pull request not in
    conflict blocked, as a rule that holds it does (model.RepoModel),
    lenient_rules has it call a branch that is behind clean, as a rule that
    does not require an up-to-date branch does, labels are what the
    seeded issues carry, and unanswered names the logins GitHub will not
    say whether they may write, as for a token without the right (F19's
    review)."""

    name: str
    prefix: tuple
    scope: Scope
    depth: int
    issues: tuple = (1,)
    known: tuple = ()
    opaque_pulls: bool = False
    labels: tuple = ("todo",)
    lenient_rules: bool = False
    unanswered: tuple = ()


# --- the replacements ---------------------------------------------------------

ORIGINAL_IS_IDLE = claims.is_idle
ORIGINAL_LABELS_RELEASED = coordinator.labels_released
ORIGINAL_IS_RELEASED = board.is_released
ORIGINAL_ISSUE_ROWS = board.issue_rows
ORIGINAL_COMMENTS_DUE = board.comments_due
ORIGINAL_OVERLAPS_DUE = coordinator.overlap_comments_due
ORIGINAL_REFUSAL = guard.refusal
ORIGINAL_TOLD = coordinator.told_numbers
ORIGINAL_CLAIMS_MADE = coordinator.claims_made
ORIGINAL_PUT_BACK = github.put_back
ORIGINAL_AFTER_RELEASE = coordinator.after_release
ORIGINAL_IS_CANDIDATE = coordinator.is_candidate
ORIGINAL_PULL_ROWS = board.pull_rows
ORIGINAL_PULLS_BEHIND_MAIN = board.pulls_behind_main
ORIGINAL_POOL = team.Snapshot.pool
ORIGINAL_CLAIM_ROWS = board.claim_rows
ORIGINAL_OPEN_WORK = board.open_work
ORIGINAL_DEAL_MOVES = team.deal_moves
ORIGINAL_CLAIM_RELEASED = github.claim_released
ORIGINAL_RELEASE_ONE = github.release_one


def comment_then_labels(api, snap, key, now):
    """release_one with the spec's two steps swapped, the comment first, as
    before 0.4.1 (F1): the labels then come off and todo goes on where
    labels_put_on says, and every refusal is still handled as release_one
    handles it, so the order is the one rule changed."""
    release, names, taken_off = snap.releases[key], coordinator.labels_released(snap, key), []
    put_on = coordinator.labels_put_on(snap, key)
    text = coordinator.release_text(snap, key, github.holding(api, snap.default_branch, release.sha))
    try:
        released_at = (api.post(f"/repos/{api.repo}/issues/{key}/comments", {"body": text}) or {}).get("created_at")
    except github.GitHubError as problem:
        try:
            released_at = github.landed_release(api, key, release)
        except github.GitHubError as unread:
            return f"#{key}: release unsettled, the comment failed: {problem}; the comments unread: {unread}"
        if not released_at:
            return f"#{key}: not released, the comment failed: {problem}"
    try:
        for name in names:
            if github.take_label_off(api, key, name):
                taken_off.append(name)
    except github.GitHubError as problem:
        return f"#{key}: taking the labels off failed: {problem}" + github.put_back(api, key, taken_off)
    refused = github.put_labels_on(api, key, put_on, taken_off)
    if refused:
        return refused
    coordinator.after_release(snap, key, released_at or now.isoformat(), names, put_on)
    return f"#{key}: claim released"


def issue_rows_but_blockers(snap, label, unclaimed_only):
    """board.issue_rows with the Blockers section left empty, as a page
    that left a blocker to the Queue would have it."""
    return [] if label == labels.BLOCKER else ORIGINAL_ISSUE_ROWS(snap, label, unclaimed_only)


def issue_rows_unclaimed_blockers(snap, label, unclaimed_only):
    """board.issue_rows with the Blockers section listing only the blockers
    nobody has claimed, as the Queue lists its issues."""
    return ORIGINAL_ISSUE_ROWS(snap, label, unclaimed_only or label == labels.BLOCKER)


def issue_rows_hiding(hidden):
    """board.issue_rows with the label hidden left out of each row's
    labels, so an issue it keeps a worker off reads as free to take."""
    def rows(snap, label, unclaimed_only):
        return [[number, title, ", ".join(n for n in names.split(", ") if n != hidden)]
                for number, title, names in ORIGINAL_ISSUE_ROWS(snap, label, unclaimed_only)]
    return rows


def issue_rows_dropping(section, label_carried):
    """board.issue_rows with the issues carrying label_carried left out of
    the section that lists the issues labelled section, as a page that
    took them for another section's would have it."""
    def rows(snap, label, unclaimed_only):
        found = ORIGINAL_ISSUE_ROWS(snap, label, unclaimed_only)
        return [r for r in found if label != section or label_carried not in r[2].split(", ")]
    return rows


def issue_rows_without_given_blockers(snap, label, unclaimed_only):
    """board.issue_rows with the Blockers section leaving out a blocker
    shown under Given to workers, as if a section listed each issue once."""
    found = ORIGINAL_ISSUE_ROWS(snap, label, unclaimed_only)
    given = {row[0] for row in board.given_rows(snap)}
    return [r for r in found if label != labels.BLOCKER or r[0] not in given]


def relabelled_before_the_comment(api, snap, key, now):
    """github.release_one with one change: the board's copy of the issue is
    relabelled as soon as the labels are off and todo is on, before the
    comment, and left so when the comment fails and the labels go back on,
    so the page of a run whose release GitHub refused shows the issue as
    released while GitHub holds its labels again."""
    release = snap.releases[key]
    names, put_on = coordinator.labels_released(snap, key), coordinator.labels_put_on(snap, key)
    taken_off = []
    try:
        for name in names:
            if github.take_label_off(api, key, name):
                taken_off.append(name)
    except github.GitHubError as problem:
        return f"#{key}: not released, taking the labels off failed: {problem}" + github.put_back(api, key, taken_off)
    refused = github.put_labels_on(api, key, put_on, taken_off)
    if refused:
        return refused
    text = coordinator.release_text(snap, key, github.holding(api, snap.default_branch, release.sha))
    for issue in [snap.claim_issues[key]] + [i for i in snap.issues if str(i.number) == key]:
        issue.labels = coordinator.relabelled(issue.labels, names, put_on)
    try:
        released_at = (api.post(f"/repos/{api.repo}/issues/{key}/comments", {"body": text}) or {}).get("created_at")
    except github.GitHubError as problem:
        try:
            released_at = github.landed_release(api, key, release)
        except github.GitHubError as unread:
            return github.unsettled(snap, key, names, put_on, problem, unread)
        if not released_at:
            return f"#{key}: not released, the comment failed: {problem}" + github.put_back(api, key, taken_off, put_on)
    coordinator.after_release(snap, key, released_at or now.isoformat(), names, put_on)
    return f"#{key}: claim released"


def released_with_claimed_on(snap, key, idle_after=claims.IDLE_AFTER):
    """board.is_released as if the label claimed were always off."""
    issue = snap.claim_issues.get(key)
    if issue is None:
        return ORIGINAL_IS_RELEASED(snap, key, idle_after)
    kept = issue.labels
    issue.labels = [name for name in kept if name != "claimed"]
    try:
        return ORIGINAL_IS_RELEASED(snap, key, idle_after)
    finally:
        issue.labels = kept


def ahead_read_as_behind(api, pull, default_branch=None):
    """github.behind_by reading the comparison's other count, the commits
    of the head its base lacks: a draft one commit ahead and one behind
    reads the same either way, and one whose branch is level with main
    but holds its claim commit reads as behind (F9)."""
    base = (pull.get("base") or {}).get("ref") or default_branch
    head = (pull.get("head") or {}).get("sha")
    if not head or not base:
        return None
    found = api.get(f"/repos/{api.repo}/compare/{github.quoted(base)}...{head}", missing_ok=True)
    return (found or {}).get("ahead_by")


def main_moved_only_in_the_push_s_run(snapshot, event_name, ref_name, now, idle_after=claims.IDLE_AFTER):
    """board.comments_due as 0.4.1 had it: "main moved" owed only by the run
    a push to the default branch started, which the concurrency group
    loses (F12)."""
    snap = board.as_snapshot(snapshot)
    due = ORIGINAL_COMMENTS_DUE(snap, event_name, ref_name, now, idle_after)
    if event_name == "push" and ref_name == snap.default_branch:
        return due
    return [c for c in due if c.marker == board.IDLE_MARKER]


def any_line_is_a_marker(comment):
    """claims.starts_with_release_marker reading any comment as a release."""
    return bool(((comment or {}).get("body") or "").strip())


def nobody_vouched_for(api, asked=None):
    """github.may_write as if GitHub refused every permission asked."""
    return lambda login: False


def renamed_unread(api, old, new, sha):
    """github.free_name renaming without reading the branch again, so a
    holder who came back between the release comment and the rename loses
    the claim's name (spec, Release: "a commit pushed to the branch before
    the rename, its holder coming back, keeps the claim"); a name that
    keeps the released head already is still deleted, as free_name does,
    so the one rule changed is the reading."""
    try:
        api.post(f"/repos/{api.repo}/branches/{urllib.parse.quote(old, safe='')}/rename", {"new_name": new})
        return f"claim released; {old} renamed to {new}"
    except github.GitHubError as problem:
        if problem.status != 422 or github.branch_sha(api, new) != sha:
            raise
    api.delete(f"/repos/{api.repo}/git/refs/heads/{github.quoted(old)}")
    return f"claim released; {new} already keeps {sha[:7]}, so {old} was deleted"


def force_let_through(args, current_branch, default_branch, claim_file_at=None):
    """guard.refusal passing --force, as a change that dropped one rule."""
    reason = ORIGINAL_REFUSAL(args, current_branch, default_branch, claim_file_at)
    return None if reason and reason.startswith("--force overwrites") else reason


def lease_let_through_anywhere(args, current_branch, default_branch, claim_file_at=None):
    """guard.refusal passing --force-with-lease outside claude/."""
    reason = ORIGINAL_REFUSAL(args, current_branch, default_branch, claim_file_at)
    return None if reason and reason.startswith("--force-with-lease to") else reason


def lease_onto_any_claim(name, source, expected, claim_file_at, remote="origin"):
    """guard.other_holder judging nothing: the guard as it was before F7's
    fix, passing a lease onto any claude/ branch, whoever claimed it."""
    return None


def each_leg_from_its_own_look(clients, workers, looks, areas=None):
    """The workers' workflow as built until F4's fix: each leg asks for
    itself at its own moment, so two looks that differ on whether the
    holder's pull request is behind deal its issue twice."""
    return {w: team.next_for(clients[look], w, list(workers), areas) for w, look in zip(workers, looks)}


def put_labels_on_then(put_back_of):
    """github.put_labels_on with one change: once GitHub refuses the todo,
    put_back_of(taken_off, put_on) says which labels go back on and which
    come off again, where the library puts back every label it took off and
    takes the todo off again."""
    def put_labels_on(api, key, put_on, taken_off):
        if not put_on:
            return ""
        try:
            api.post(f"/repos/{api.repo}/issues/{key}/labels", {"labels": put_on})
        except github.GitHubError as problem:
            return (f"#{key}: not released, putting todo on failed: {problem}"
                    + github.put_back(api, key, *put_back_of(taken_off, put_on)))
        return ""
    return put_labels_on


def standing_of_every_word(api, detail, default_branch=None):
    """github.standing_of reading every word but behind and dirty through
    the comparison, as the first build of F16's answer did: a pull request
    GitHub calls clean, unstable or has_hooks while its branch is behind,
    as under a rule that does not require an up-to-date branch, reads
    behind and hears "main moved", though the spec says it may merge as it
    stands."""
    word = detail.get("mergeable_state")
    if word in ("behind", "dirty"):
        return None
    base = (detail.get("base") or {}).get("ref")
    if default_branch and base and base != default_branch:
        return None
    if word in github.OPAQUE_STATES and not detail.get("mergeable"):
        return github.standing_word(detail)
    try:
        behind = github.behind_by(api, detail, default_branch)
    except github.GitHubError:
        behind = None
    if word in github.OPAQUE_STATES:
        return github.standing_word(detail, behind)
    return "behind" if behind else None


def given_rows_needing_todo(snap):
    """board.given_rows as built until F14's fix: an issue a worker's label
    names was shown under Given to workers only when it also carried todo
    and was not an epic, so one labelled worker:<name> alone was shown in
    neither section of the board."""
    claimed = board.claimed_numbers(snap.branches, board.released_keys(snap))
    rows = []
    for issue in sorted(snap.issues, key=lambda i: i.number):
        names = sorted(issue.labels)
        workers = labels.holder_names(names)
        if labels.TODO not in names or labels.CLAIMED in names or labels.EPIC in names or not workers:
            continue
        if issue.number not in claimed:
            rows.append([f"#{issue.number}", issue.title, ", ".join(workers)])
    return rows


def deal_without_the_taken_set(members, pool, areas=None):
    """team.deal forgetting which issues it dealt already, so two members
    get the first issue that fits them both."""
    areas, dealt = areas or {}, {}
    for member in members:
        for issue in pool:
            if fits(areas.get(member, ()), claims.issue_files(issue.get("body"))):
                dealt[member] = issue
                break
    return dealt


def told_on_any_line(comments, trusted=None):
    """coordinator.told_numbers reading a marker on any line, so a reply
    that quotes one counts as the coordinator's."""
    told = set()
    for comment in comments:
        for line in ((comment or {}).get("body") or "").splitlines():
            match = coordinator.OVERLAP_LINE.fullmatch(line.strip())
            numbers = [claims.number_in(number) for number in match.group(1).split()] if match else []
            if numbers and None not in numbers and claims.is_trusted_author(comment, trusted):
                told.update(numbers)
    return told


def told_by_anyone(comments, trusted=None):
    """coordinator.told_numbers trusting every author."""
    return ORIGINAL_TOLD(comments, trusted=lambda login: True)


def number_with_any_zeros(key):
    """claims.number_in taking leading zeros, as before 0.4.1 (F5)."""
    text = str(key)
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 9 else None


def sort_key_with_int_after_isdigit(value):
    """claims.by_number testing with isdigit and then calling int, as
    before 0.4.1 (F6): a superscript passes the test and int raises, so a
    hand-made branch name stops the render."""
    return (0, int(value), "") if str(value).isdigit() else (1, 0, str(value))


def releases_skipping_finished(api, heads, found_issues, pulls, now=None, asked=None, every_head=None, refused=None,
                               started_by=""):
    """github.releases leaving every finished draft out before it reads
    any release comment, as before 0.4.1 (F10), so a person's release of
    one is never seen; every other reading is release's own."""
    now = now or datetime.now(timezone.utc)
    ready, trusted = github.ready_branches(pulls), github.may_write(api, asked)
    drafts = {(p.get("head") or {}).get("ref"): p for p in pulls if p.get("draft") and github.is_claims(p)}
    every_head = every_head or {}
    found = {}
    for branch, sha in sorted(heads.items()):
        key = claims.key_of(branch)
        if not sha or branch in ready or not github.may_be_due(found_issues.get(key)):
            continue
        kept = every_head.get(claims.released_branch(key, sha)) == sha
        pushed, committed = github.head_since(api, branch, sha, refused, now, started_by)
        release = board.Release(sha=sha, committed_at=committed, pushed_at=pushed, kept=kept)
        if not kept and (not release.last_commit or not claims.is_idle(release.last_commit, now)):
            continue
        if github.finished_draft(api, drafts.get(branch)):
            continue
        release.released_at = claims.latest_release(api.get_all(f"/repos/{api.repo}/issues/{key}/comments"), trusted)
        found[key] = release.to_dict()
    return found


def comments_unguarded(api, due):
    """github.post_comments letting a refused comment's error escape, as
    before 0.4.1 (F11), so one refusal fails the run."""
    lines = []
    for number, marker, text in due:
        kind = "idle" if marker == board.IDLE_MARKER else "main-moved"
        if github.comment_once(api, number, marker, text):
            lines.append(f"PR #{number}: {kind} comment posted")
    return lines


def began_times_unguarded(api, found):
    """github.began_times letting a refused read of the repository's
    activity escape, so one branch GitHub will not date fails the run (F13's
    review)."""
    wanted = []
    for works in board.overlaps(found).values():
        wanted += [work.branch for work in works if work.branch and work.branch not in wanted]
    times = github.newest_creations(api, wanted) if wanted else {}
    for branch in wanted:
        began = times.get(branch) or github.branch_made_at(api, branch)
        if began:
            times[branch] = began
    return times, {}


def reading(original, name, rule):
    """original with board.<name> read as rule while it runs, so the one
    rule goes back to an old reading at that call alone."""
    def replacement(*args, **kwargs):
        with mock.patch.object(board, name, rule):
            return original(*args, **kwargs)
    return replacement


def of_claim_by_name(pull, key):
    """board.of_claim reading the branch's name alone, as before F17's fix,
    so a pull request from a fork whose branch bears a claim's name is
    taken for the claim's."""
    return pull.branch == claims.branch_of(key)


def closed_by_name(pull, released):
    """board.closed_by_release reading the branch's name alone, as before
    F17's fix, so a release's rename is taken to close a pull request from
    a fork whose branch bears the released claim's name."""
    return pull.branch in released


def claim_rows_by_name(snap):
    """board.claim_rows as before F17's fix: a claim's row names the pull
    request whose branch bears the claim's name, one from a fork too."""
    pulls_by_branch = {p.branch: p for p in snap.pulls}
    released = board.released_keys(snap)
    rows = []
    for branch in sorted(board.claim_branches(snap.branches), key=lambda b: claims.by_number(claims.key_of(b))):
        key = claims.key_of(branch)
        if key in released:
            continue
        rows.append(board.claim_row(key, branch, snap.claims.get(key), pulls_by_branch.get(branch),
                                    snap.claim_issues.get(key)))
    return rows


def open_work_by_name(snapshot):
    """board.open_work as before F17's fix: a pull request from a fork whose
    branch bears a claim's name is that claim's work, left out with its
    release and taking the claim's place, so the claim is no piece of its
    own."""
    snap = board.as_snapshot(snapshot)
    released = board.released_keys(snap)
    pulls = {p.branch: p for p in snap.pulls}
    work = []
    for pull in snap.pulls:
        key = claims.key_of(pull.branch) if claims.is_claim_branch(pull.branch) else None
        if key in released:
            continue
        files = board.own_files(pull.files) or (
            board.own_files(claims.claim_files(snap.claims.get(key) or "")) if key else ())
        work.append(board.Work(pull.number, files, not pull.draft, bool(key) and board.is_blocker(snap, key), True,
                               "" if pull.fork else pull.branch, board.pull_began(snap, pull),
                               claims.number_of(pull.branch) if key else None))
    for branch in board.claim_branches(snap.branches):
        key, number = claims.key_of(branch), claims.number_of(branch)
        if branch in pulls or key in released or number is None:
            continue
        files = board.own_files(claims.claim_files(snap.claims.get(key) or ""))
        work.append(board.Work(number, files, False, board.is_blocker(snap, key), False, branch,
                               snap.began.get(branch, ""), number))
    return sorted(work, key=lambda w: w.number)


def ready_branches_by_name(pulls):
    """github.ready_branches counting a ready pull request from a fork whose
    branch bears a claim's name as the claim's, as before F17's fix."""
    return {(p.get("head") or {}).get("ref") for p in pulls if not p.get("draft")}


def releases_reading_a_fork_s_draft(api, heads, found_issues, pulls, now=None, asked=None, every_head=None, refused=None,
                                    started_by=""):
    """github.releases taking a draft from a fork whose branch bears a
    claim's name for the claim's own, as before F17's fix, so its author's
    "finished; ready to mark" holds the claim; every other reading is
    release's own."""
    return releases_reading_drafts(api, heads, found_issues, pulls, now, asked,
                                   lambda p: not board.from_stranger(p), every_head, refused, started_by)


def releases_reading_a_stranger_s_draft(api, heads, found_issues, pulls, now=None, asked=None, every_head=None,
                                        refused=None, started_by=""):
    """github.releases taking a draft a stranger opened from a claim's own
    branch for the claim's, as before F19's fix, so its author's "finished;
    ready to mark" holds the claim; every other reading is release's
    own."""
    return releases_reading_drafts(api, heads, found_issues, pulls, now, asked,
                                   lambda p: not board.from_fork(p), every_head, refused, started_by)


def release_counts_by_time_alone(release, idle_after=claims.IDLE_AFTER):
    """board.release_counts reading every release by its time alone, a
    head a release kept included, which F8's fix exempts: such a branch,
    pushed again, is dated by that push and stands a day."""
    return bool(release.released_at) and claims.was_released(release.released_at, release.last_commit, idle_after)


def release_counts_kept_after_push(release, idle_after=claims.IDLE_AFTER):
    """board.release_counts counting the release of a kept head only when
    it came at or after the push of that head, where the spec counts it
    whenever it came, before that push or after (SPEC["pushed again"]), so
    a branch pushed again at a head its release kept stands as a claim
    for a day."""
    if not release.released_at:
        return False
    if release.kept and claims.parse_time(release.released_at) >= claims.parse_time(release.last_commit):
        return True
    return claims.was_released(release.released_at, release.last_commit, idle_after)


def kept_released_by_the_comment(snap, key):
    """coordinator.kept_released as F8's fix first built it: a kept head
    counted released by its release comment alone (board.release_counts),
    the labels unread, so a release the label claimed undid holds the
    claim for good, where the board reads that claim as held and the
    coordinator owes it a release once it goes a day without a commit."""
    release = snap.releases[key]
    return release.kept and board.release_counts(release)


def head_since_by_the_committer_date(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since as before F8's fix: the head's committer date,
    which whoever pushes sets, with no read of the activity, so a commit
    dated ahead holds its claim past its day and one dated back has it
    released at once."""
    return "", github.committed_at(api, sha)


def both_floors(read, first):
    """The floor head_since dates a claim no earlier than: the later of the
    newest entries the two reads listed (github.latest)."""
    return github.latest(read.floor, first.floor)


def strictly_later(floor, committed):
    """Whether the floor dates the claim, as head_since asks: later than
    the head's committer date, the same instant being no later."""
    return github.latest(committed, floor) != committed


def head_since_copy(api, branch, sha, refused, now, started_by="", again=True, now_again=True, floor_of=both_floors,
                    later=strictly_later, by_the_run=True, older=True):
    """github.head_since with six of its rules taken as arguments, so each
    mutant below changes one and the rest stay the library's, which a test
    holds this copy to line by line: whether a first read that may tell
    more is made again (again), whether the second read is judged by when
    the run began as the first is (now_again), which reads' newest entries
    the floor is (floor_of), when the floor dates the claim (later),
    whether the run a branch's push started dates its undated head at its
    start (by_the_run) and whether it does so where a read dates the head
    by an older push of it (older)."""
    now = now or datetime.now(timezone.utc)
    read = first = github.read_push(api, branch, sha, now)
    if first.again and again:
        api.sleep(github.PUSH_RETRY_SECONDS)
        read = github.read_push(api, branch, sha, now if now_again else None)
    if read.pushed and (branch != started_by or not (by_the_run and older) or github.came_since(read.pushed, now)):
        return read.pushed, ""
    floor, why = floor_of(read, first), read.why or (first.why if first.floor else "")
    if branch == started_by and by_the_run:
        began = github.github_time(now)
        why = github.OLDER_PUSH_LISTED if read.pushed else why
        if why and refused is not None:
            refused[branch] = f"{why}, so its idleness is read from the start of this run, {began}, which its push started"
        return began, ""
    committed = github.committed_at(api, sha)
    if floor and later(floor, committed):
        if refused is not None:
            refused[branch] = (f"{why}, so its idleness is read from its newest entry's time, {floor}, "
                               + ("later than its committer date" if committed else "GitHub not showing its commit"))
        return floor, ""
    if why and refused is not None:
        refused[branch] = f"{why}, so its idleness is read from its committer date"
    return "", committed


def head_since_reading_once(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since as F8's fix first built it in this: one read of
    the push, so a first read GitHub would answer after a pause, or one
    made before the activity lists the push that set the head, leaves the
    claim to the floor or the clock its pusher set."""
    return head_since_copy(api, branch, sha, refused, now, started_by, again=False)


def head_since_with_no_floor(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since as F8's second review left it: where neither read
    dates the push, the committer date stands in whatever the reads
    listed, so where both lag a commit dated back has its claim released
    in the run its own push started; the build at 286ba37 held so where
    the stale entry moved the branch off the head, a head pushed back, and
    no build before the floor held where it named the head on neither
    side, as at F8's trace's step 41 (F8's fix check, its third review
    narrowing the claim)."""
    return head_since_copy(api, branch, sha, refused, now, started_by, floor_of=lambda read, first: "")


def head_since_with_the_last_floor(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since taking the floor from the last read alone, where
    the spec takes the newest entry either read listed, so a first read a
    push behind and a second GitHub refuses leave the committer date to
    stand in, earlier than the entry the first read listed (SPEC["floor"];
    F8's fix check)."""
    return head_since_copy(api, branch, sha, refused, now, started_by, floor_of=lambda read, first: read.floor)


def head_since_with_the_first_floor(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since taking the floor from the first read alone, the
    mirror of head_since_with_the_last_floor, so a first read GitHub
    refuses or that lists nothing of the branch and a second a push behind
    leave the committer date to stand in, earlier than the entry the
    second read listed (SPEC["floor"]: "either read"; F8's third review,
    which found no lane catching it)."""
    return head_since_copy(api, branch, sha, refused, now, started_by, floor_of=lambda read, first: first.floor)


def head_since_with_the_earlier_floor(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since taking the earlier of the two reads' newest entries
    for the floor where both listed one, where the spec takes the newest
    (SPEC["floor"]), so an activity catching up, two pushes behind on the
    first read and one on the second, dates the claim by the older entry
    and releases a claim its holder is pushing to (F8's third review,
    which found no lane catching it)."""
    def earlier(read, first):
        if read.floor and first.floor:
            return first.floor if github.latest(read.floor, first.floor) == read.floor else read.floor
        return github.latest(read.floor, first.floor)
    return head_since_copy(api, branch, sha, refused, now, started_by, floor_of=earlier)


def head_since_ignoring_the_run_s_event(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since as F8's fix check left it: the branch whose push
    started the run is dated as any other, by the floor or the committer
    date, where the spec dates a head no read dates at the run's start in
    that run, so a fresh branch whose activity lags both reads, an empty
    list with no floor, has its claim commit dated back released in the
    run its own push started (SPEC["started by"]; F8's third review)."""
    return head_since_copy(api, branch, sha, refused, now, started_by, by_the_run=False)


def head_since_trusting_an_older_push(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since as F8's follow-up first built it: in the run a
    branch's push started, a read that dates the head by an entry from
    more than a few minutes before the run began, an older push of the
    same head that an activity two pushes behind lists newest where the
    branch was pushed back to a head it had before, dates it by that
    entry, so the claim is released in the run its own push started
    (SPEC["started by"]: "and so is one a read dates by an entry that set
    it more than a few minutes before the run began"; F8's follow-up's
    second review, which found no lane catching it)."""
    return head_since_copy(api, branch, sha, refused, now, started_by, older=False)


def head_since_with_no_moment_again(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since making its second read with no moment of the run,
    so that read dates only an entry that set the head, and a push after
    the run read the branch, listed by the second read where GitHub
    refused the first, dates nothing: the floor dates the claim at its
    minute, and the log says no push was listed (SPEC["moved off"]; F8's
    fix check)."""
    return head_since_copy(api, branch, sha, refused, now, started_by, now_again=False)


def head_since_with_a_floor_at_the_committer_date(api, branch, sha, refused=None, now=None, started_by=""):
    """github.head_since dating a claim by the floor where it is the same
    instant as the head's committer date, which is no later, so the log
    says the claim is read from a floor later than its committer date
    where the spec has the committer date stand in; no release turns on
    it, the two minutes being one (SPEC["floor"]; F8's fix check)."""
    return head_since_copy(api, branch, sha, refused, now, started_by,
                           later=lambda floor, committed: github.latest(floor, committed) == floor)


def pushed_by_after_alone(entry, sha, now):
    """github.pushed_by reading only the head an entry set, as F8's fix
    first built it, so a push after the run read the branch, from the
    head it read, dates nothing: the run reads the activity again, which
    lists that push again, since a push after the run read the branch
    answers every read of it in the run (fake_github.MOVED)."""
    at = github.entry_time(entry)
    return at if at and entry.get("after") == sha else ""


def pushed_by_whatever_its_age(entry, sha, now):
    """github.pushed_by as F8's review first built it, at 286ba37, for an
    entry that moved the branch off the head: dated however old it is, so
    where a later push brought the branch back to that head, one the
    activity does not list yet, the stale entry dates the claim, which is
    then read as idle while its holder pushes, and the run reads no more
    (SPEC["moved off"], F8's second review); every other entry is read as
    the library reads it."""
    at = github.entry_time(entry)
    if at and (sha in (entry.get("after"), entry.get("before")) or github.came_since(at, now)):
        return at
    return ""


def pushed_by_off_the_head_alone(entry, sha, now):
    """github.pushed_by as F8's second review built it, at d952605: an
    entry that names another head dates the claim only where it moved the
    branch off the head the run read, so after two pushes since the run
    read the branch, the newest naming that head on neither side, a push
    after the run began dates nothing and the run reads again (SPEC["moved
    off"]: an entry that set the branch to another head; F8's fix
    check)."""
    at = github.entry_time(entry)
    if at and (entry.get("after") == sha or (entry.get("before") == sha and github.came_since(at, now))):
        return at
    return ""


def pushed_by_but_a_delete(entry, sha, now):
    """github.pushed_by dating a fresh entry only where it is no delete, so
    the branch's delete after the run read it, which the spec has date the
    claim as a push after the read does ("or deleted it"), dates nothing:
    the run reads again, which lists the delete still, and its log says
    the claim is read from the floor, at the delete's own minute, where
    its reads owe no line (SPEC["moved off"]; F8's third review, which
    found no lane catching it)."""
    at = github.entry_time(entry)
    if at and (entry.get("after") == sha
               or (entry.get("activity_type") != "branch_deletion" and github.came_since(at, now))):
        return at
    return ""


def landed_release_finding_none_unread(api, key, release):
    """github.landed_release as every build before F20's fix had it: a
    read of the issue's comments GitHub refuses finds no comment, as a
    read that listed none does, so release_one puts the labels back
    beside a comment that may stand, which holds the claim for good, the
    comment telling every later coordinator the claim is released and the
    label claimed telling the board it is held (F20)."""
    try:
        comments = api.get_all(f"/repos/{api.repo}/issues/{key}/comments")
    except github.GitHubError:
        return ""
    released_at = claims.latest_release(comments, github.may_write(api))
    if not released_at or not github.newer(released_at, release.released_at):
        return ""
    return released_at if board.release_counts(dataclasses.replace(release, released_at=released_at)) else ""


def release_one_counting_unsettled(api, snap, key, now):
    """github.release_one counting a release it cannot tell stands: where
    GitHub refused the comment and then the read of the issue's comments
    that would say whether it wrote it anyway, the snapshot counts the
    release at the run's start, so the same run renames the branch and
    says where it went, where the spec has the run count no release it
    cannot tell stands and the next run settle it (F20)."""
    def counted(snap, key, names, put_on, problem, unread):
        coordinator.after_release(snap, key, github.github_time(now), names, put_on)
        return f"#{key}: claim released, the comment failed and its issue's comments unread: {problem}; {unread}"
    with mock.patch.object(github, "unsettled", counted):
        return ORIGINAL_RELEASE_ONE(api, snap, key, now)


def unsettled_raising(snap, key, names, put_on, problem, unread):
    """github.unsettled letting the refused read through, as release_one
    would without the catch F20's fix put around landed_release, which
    raises where GitHub refuses the read: the board's run fails, its page
    unwritten and every claim after this one left unreleased, where the
    coordinator never raises (coordinate) and the next run settles the
    release (F20)."""
    raise unread


def landed_release_whenever_it_came(api, key, release):
    """github.landed_release without the newer check, as F8's review left
    it: after a post of the release comment that GitHub answered with an
    error, the latest release comment found counts by the rule the next
    run reads it with, for a kept head whenever it came, so an earlier
    release of that head passes for the comment this run's post would
    have written, the labels are not put back and the release counts
    whole with no comment of its own (F8's second review). A read GitHub
    refuses raises, as the library's does since F20's fix, so the newer
    check is the one rule changed."""
    comments = api.get_all(f"/repos/{api.repo}/issues/{key}/comments")
    released_at = claims.latest_release(comments, github.may_write(api))
    return released_at if board.release_counts(dataclasses.replace(release, released_at=released_at)) else ""


def claim_released_by_committer_date(api, branch, now=None):
    """github.claim_released dating the branch's last commit by its head's
    committer date alone, as before F8's fix, where the board dates it by
    when GitHub received the push (head_since), so the clerk holds a claim
    whose commit is dated ahead that the board counts released."""
    with mock.patch.object(github, "head_since", head_since_by_the_committer_date):
        return ORIGINAL_CLAIM_RELEASED(api, branch, now)


def claim_released_ignoring_kept(api, branch, now=None):
    """github.claim_released reading no head as kept, as before F8's fix, so
    the clerk holds a branch pushed again at a head a release kept, dated
    by that push, where the board counts it released; every other reading
    is claim_released's own."""
    name = board.branch_name(branch)
    sha = github.branch_sha(api, name)
    if not sha:
        return False
    now = now or datetime.now(timezone.utc)
    pushed, committed = github.head_since(api, name, sha, now=now)
    release = board.Release(sha=sha, committed_at=committed, pushed_at=pushed)
    if not release.last_commit or not claims.is_idle(release.last_commit, now):
        return False
    comments = api.get_all(f"/repos/{api.repo}/issues/{github.quoted(claims.key_of(name))}/comments")
    release.released_at = claims.latest_release(comments, github.may_write(api))
    return board.release_counts(release)


def releases_reading_drafts(api, heads, found_issues, pulls, now, asked, counted, every_head=None, refused=None,
                            started_by=""):
    """github.releases with the drafts it reads as claims' chosen by
    counted, where the library counts only the claim's own (github.is_claims)."""
    now = now or datetime.now(timezone.utc)
    ready, trusted = github.ready_branches(pulls), github.may_write(api, asked)
    drafts = {(p.get("head") or {}).get("ref"): p for p in pulls if p.get("draft") and counted(p)}
    every_head = every_head or {}
    found = {}
    for branch, sha in sorted(heads.items()):
        key = claims.key_of(branch)
        if not sha or branch in ready or not github.may_be_due(found_issues.get(key)):
            continue
        kept = every_head.get(claims.released_branch(key, sha)) == sha
        pushed, committed = github.head_since(api, branch, sha, refused, now, started_by)
        release = board.Release(sha=sha, committed_at=committed, pushed_at=pushed, kept=kept)
        if not kept and (not release.last_commit or not claims.is_idle(release.last_commit, now)):
            continue
        finished = github.finished_draft(api, drafts.get(branch))
        if finished and labels.CLAIMED in found_issues[key]["labels"]:
            continue
        release.released_at = claims.latest_release(api.get_all(f"/repos/{api.repo}/issues/{key}/comments"), trusted)
        if finished and not board.release_counts(release):
            continue
        found[key] = release.to_dict()
    return found


def claim_of_but_strangers(pull):
    """board.claim_of as F17's fix built it: a pull request from a fork is
    no claim's, but one a stranger opened from the claim's own branch here
    is taken for the claim's, as before F19's fix."""
    return None if pull.fork else claims.key_of(pull.branch)


def of_claim_but_strangers(pull, key):
    """board.of_claim through claim_of_but_strangers."""
    return claim_of_but_strangers(pull) == key


def closed_by_release_as_f17(pull, released):
    """board.closed_by_release as F17's fix built it: a release's rename
    closes the claim's own pull request alone, so a stranger's from the
    released branch, which GitHub closes with it, is taken to stay open, as
    before F19's fix."""
    return board.claim_of(pull) is not None and pull.branch in released


def claim_rows_but_strangers(snap):
    """board.claim_rows reading whose a pull request is as F17's fix left
    it, a stranger's from the claim's own branch taken for the claim's, as
    before F19's fix, and nothing else: its releases are the library's,
    where a reading swapped for the whole call reached board.is_released
    through released_keys too (the fix's review)."""
    pulls_by_branch = {p.branch: p for p in snap.pulls if claim_of_but_strangers(p) is not None}
    released = board.released_keys(snap)
    rows = []
    for branch in sorted(board.claim_branches(snap.branches), key=lambda b: claims.by_number(claims.key_of(b))):
        key = claims.key_of(branch)
        if key in released:
            continue
        rows.append(board.claim_row(key, branch, snap.claims.get(key), pulls_by_branch.get(branch),
                                    snap.claim_issues.get(key)))
    return rows


def open_work_but_strangers(snapshot):
    """board.open_work reading whose a pull request is as F17's fix left
    it, a stranger's from the claim's own branch taken for the claim's
    work, as before F19's fix, and nothing else (claim_rows_but_strangers
    says why)."""
    snap = board.as_snapshot(snapshot)
    released = board.released_keys(snap)
    renamed = board.released_branches(snap)
    pulls = {p.branch: p for p in snap.pulls if claim_of_but_strangers(p) is not None}
    work = []
    for pull in snap.pulls:
        key = claim_of_but_strangers(pull)
        if board.closed_by_release(pull, renamed):
            continue
        files = board.own_files(pull.files) or (
            board.own_files(claims.claim_files(snap.claims.get(key) or "")) if key else ())
        work.append(board.Work(pull.number, files, not pull.draft, bool(key) and board.is_blocker(snap, key), True,
                               "" if pull.fork else pull.branch, board.pull_began(snap, pull),
                               claims.number_of(pull.branch) if key else None))
    for branch in board.claim_branches(snap.branches):
        key, number = claims.key_of(branch), claims.number_of(branch)
        if branch in pulls or key in released or number is None:
            continue
        files = board.own_files(claims.claim_files(snap.claims.get(key) or ""))
        work.append(board.Work(number, files, False, board.is_blocker(snap, key), False, branch,
                               snap.began.get(branch, ""), number))
    return sorted(work, key=lambda w: w.number)


def may_open_doubting_a_refusal(asked):
    """github.may_open reading a permission call GitHub refused as no, so a
    pull request whose author GitHub will not answer for is taken for a
    stranger's, which frees a finished claim (F19's review)."""
    return lambda login: asked(login) is True


def may_write_trusting_a_refusal(api, asked=None):
    """github.may_write reading a permission call GitHub refused as yes, so
    a release comment or an overlap marker by an author GitHub will not
    answer for counts (F19's review)."""
    asked = asked or getattr(api, "asked", None) or github.permissions(api)
    return lambda login: asked(login) is not False


def ready_branches_but_strangers(pulls):
    """github.ready_branches counting a ready pull request a stranger opened
    from a claim's own branch as the claim's, as before F19's fix."""
    return {(p.get("head") or {}).get("ref") for p in pulls if not p.get("draft") and not board.from_fork(p)}


def behind_main_but_forks(pulls):
    """board.pulls_behind_main leaving out every pull request from a fork, as
    a reading that took a fork's pull request, no claim's, for nobody's
    would: such a pull request behind main never hears so, though the spec
    has the board tell it what it tells any other (F17's review)."""
    return [p for p in ORIGINAL_PULLS_BEHIND_MAIN(pulls) if not board.as_pull(p).fork]


def holder_by_labels(snap, issue):
    """team.Snapshot.named reading whose a claim is from the issue's labels
    alone, the worker its label names (labels.worker_of), never from the
    claim file, so a worker whose label the issue carries is told to carry
    a claim another worker pushed, and the claim's holder is told nothing.
    The clerk as built until F18's fix read the labels alone too, though
    by holders(issue) == {worker}, so where two workers' labels named the
    issue it carried the claim for neither."""
    return labels.worker_of(issue)


def named_by_the_number_shown(work):
    """board.identity as before F2's fix: a piece of open work named by the
    number the board shows it by, the pull request's as soon as it has
    one, so a claim that opens its pull request is a new piece to the
    coordinator and is told again."""
    return work.number


def no_place_but_the_identity(snapshot, identity):
    """coordinator.told_places as F2's fix first built it: a piece read on
    its identity alone, so a claim the coordinator told on its pull
    request before the fix is told again on its issue at the upgrade."""
    return []


def no_claim_dated(snapshot):
    """coordinator.claims_made as F2's fix first built it: no claim dated, so
    every marker counts whenever it came, and a claim on an issue released
    and claimed anew is taken as told by the markers that spoke of the
    claim before it, and never told, nor told of."""
    return {}


def undated_claims_take_no_marker(snapshot):
    """coordinator.claims_made dating a claim GitHub does not date as the
    newest of all, so no marker counts for it and it is told on every run
    GitHub will not date it, where the spec has it take every marker (F2's
    second review)."""
    snap = board.as_snapshot(snapshot)
    made = ORIGINAL_CLAIMS_MADE(snap)
    for work in board.open_work(snap):
        if work.issue is not None and work.branch and work.issue not in made:
            made[work.issue] = board.UNDATED
    return made


def blank_names_a_worker(text):
    """claims.claim_worker, as the clerk reads it (team.claim_worker), as
    it read a Worker line until F18's review: a name that is empty once
    the note and the markup are gone is still a name, so the clerk takes
    "" for the holder and carries the claim for nobody."""
    for line in text.splitlines():
        match = claims.WORKER_LINE.match(line)
        if match:
            return claims.TRAILING_NOTE.sub("", match.group(1)).strip("`*_ ")
    return claims.UNNAMED


def run_dealt_alone(client, team_names, areas=None, worker_labels=None, run=None):
    """team.deal_moves dealing a run that starts some of the team as though
    they were the whole team, so a worker outside the run loses its share,
    a task several workers' labels give among them, to one inside it, and
    two runs that look at one state can deal one issue to two workers
    (F18's review)."""
    return ORIGINAL_DEAL_MOVES(client, team_names if run is None else run, areas, worker_labels)


def carrying_blocked_claims(snap, agent):
    """team.Snapshot.carry carrying a claim labelled blocked too, whose
    holder waits on a peer or the coordinator."""
    for issue in snap.issues:
        number = issue["number"]
        if number in snap.claimed and snap.holds(issue, agent) and snap.claim_needs_work(number):
            return f"carry {number}"
    return None


def pool_by_number(snap):
    """team.Snapshot.pool in number order, a blocker no sooner than the
    todos before it, where Pick takes a blocker before the Queue since a
    blocker stops everyone (F15's fifth review)."""
    return sorted(ORIGINAL_POOL(snap), key=lambda issue: issue["number"])


def free_though_blocked(snap, issue):
    """team.Snapshot.free leaving the label blocked out, so the clerk deals
    an issue that waits on a peer or the coordinator, which Pick never has
    anyone take (F15's fifth review)."""
    return issue["number"] not in snap.claimed and labels.CLAIMED not in labels.label_names(issue)


MUTANTS = {
    "idle at exactly 24 hours": Mutant(
        claims, "is_idle", lambda last, now, idle_after=claims.IDLE_AFTER: now - claims.parse_time(last) >= idle_after,
        "G2", "spec, Release: idle is more than 24 hours, so a claim at exactly 24 is not yet due"),
    "a 36-hour idle time": Mutant(
        claims, "is_idle", lambda last, now, idle_after=claims.IDLE_AFTER:
        ORIGINAL_IS_IDLE(last, now, timedelta(hours=36)), "G3", "a claim idle for more than 24 hours is due"),
    "the worker label left on at release": Mutant(
        coordinator, "labels_released", lambda snap, key:
        [n for n in ORIGINAL_LABELS_RELEASED(snap, key) if n == "claimed"],
        "G3", "a release takes claimed and every worker's label off"),
    "a release comment accepted from any author": Mutant(
        claims, "is_trusted_author", lambda comment, trusted=None: True,
        "G5", "a release comment by anyone who may not write frees nothing"),
    "an idle comment to a released claim's pull request": Mutant(
        board, "released_branches", lambda snap: set(),
        "G10", "a released claim's pull request hears nothing, since renaming its branch closes it"),
    "the comment before the labels": Mutant(
        github, "release_one", comment_then_labels, "G3", "the labels come off first, then the comment (F1)"),
    "a refused rename never said": Mutant(
        github, "explain_refusal", lambda *a, **k: "",
        "G10", "a rename GitHub refused for good is said once on the issue"),
    "a release ignores the label claimed": Mutant(
        board, "is_released", released_with_claimed_on,
        "G5", "putting the label claimed back undoes a release, and a comment alone frees nothing"),
    "a finished draft released by the coordinator": Mutant(
        github, "finished_draft", lambda api, pull: False,
        "G6", "the coordinator never releases a draft its author said is finished"),
    "any comment counts as a release": Mutant(
        claims, "starts_with_release_marker", any_line_is_a_marker,
        "G3", "only a comment whose first line is the marker releases a claim"),
    "a board comment posted again": Mutant(
        github, "has_marker", lambda api, number, marker: False,
        "G10", "each board comment is posted once, as its marker says"),
    "the landing order by number": Mutant(
        board, "landing_key", lambda work: (not work.blocker, not work.ready, work.number),
        "G7", "the oldest work lands first, by when GitHub says the push that made its branch came, not by its number (F13)"),
    "a pull request dated by its opening": Mutant(
        board, "pull_began", lambda snap, pull: pull.created_at,
        "G7", "a claim and the pull request later opened from its branch are one piece that began at the claim (F13)"),
    "a refused activity read stops the run": Mutant(
        github, "began_times", began_times_unguarded,
        "G15", "a branch GitHub will not date leaves its work undated, never a failed run (F13)"),
    "work nothing dates landing first": Mutant(
        board, "UNDATED", datetime.min.replace(tzinfo=timezone.utc),
        "G7", "work whose beginning nothing says lands last, a claim opening its draft at once (F13's review)"),
    "a branch never asked about on its own": Mutant(
        github, "branch_made_at", lambda api, branch: "",
        "G7", "a refused read of the newest creations leaves each branch to its own read, which dates it (F13's review)"),
    "an opaque state read by GitHub's word alone": Mutant(
        github, "standing_of", lambda api, detail, default_branch=None: None, "G10",
        "a pull request GitHub only calls blocked, or a draft, is read through mergeable and the comparison, as the gate reads it, so it hears main moved (F9)"),
    "the comparison read by ahead_by": Mutant(
        github, "behind_by", ahead_read_as_behind, "G10",
        "how far a held branch is behind is the comparison's behind_by, the commits of main it lacks, never its own commits (F9)"),
    "a clean word compared": Mutant(
        github, "standing_of", standing_of_every_word, "G10",
        "a pull request GitHub calls clean, unstable or has_hooks is not held for being behind under the "
        "repository's rules and hears nothing, however far main has moved (F16)"),
    "main moved told only in the push's run": Mutant(
        board, "comments_due", main_moved_only_in_the_push_s_run,
        "G10", "main moved is told by whichever run finds the branch behind, once per commit of main (F12)"),
    "the rename skipped": Mutant(
        github, "release_claims", lambda api, snapshot: [],
        "G3", "a released claim's branch is renamed, so the name is free"),
    "overlaps told on every run": Mutant(
        coordinator, "overlap_comments_due", lambda snapshot, told: ORIGINAL_OVERLAPS_DUE(snapshot, {}),
        "G7", "overlapping work is told once"),
    "the labels not put back": Mutant(
        github, "put_back", lambda api, key, names, put_on=(): "",
        "G3L", "a run that fails between the two puts back the labels it took off"),
    "a lost comment's release not seen": Mutant(
        github, "landed_release", lambda api, key, release: "",
        "G3L", "a comment GitHub wrote and lost its answer to makes the release whole"),
    "an unsettled release put back": Mutant(
        github, "landed_release", landed_release_finding_none_unread,
        "G3L", "a run that cannot read whether its release comment stands leaves the labels as the release left "
               "them (F20)"),
    "an unsettled release counted": Mutant(
        github, "release_one", release_one_counting_unsettled,
        "G3L", "a run that cannot read whether its release comment stands counts no release, the next run "
               "settling it (F20)"),
    "an unsettled release failing the run": Mutant(
        github, "unsettled", unsettled_raising,
        "G15", "a run that cannot read whether its release comment stands goes on and writes its page (F20)"),
    "a writer's release not vouched for": Mutant(
        github, "may_write", nobody_vouched_for,
        "G5", "anyone who may write may release, whatever author_association GitHub shows"),
    "a claim number with leading zeros": Mutant(
        claims, "number_in", number_with_any_zeros,
        "G1", "claude/01 is no claim on #1 (F5)"),
    "the guard passes a misnamed claim": Mutant(
        guard, "misnamed_claim", lambda name: None,
        "G11a", "the push guard refuses a name that reads as a claim and claims nothing"),
    "the rename made without reading the head again": Mutant(
        github, "free_name", renamed_unread,
        "G3", "a commit pushed before the rename, its holder coming back, keeps the claim"),
    "the guard passes --force": Mutant(
        guard, "refusal", force_let_through,
        "G11a", "a plain forced push is refused anywhere (board rules)"),
    "the guard passes a lease outside claude/": Mutant(
        guard, "refusal", lease_let_through_anywhere,
        "G11a", "the one forced push allowed is a lease on one's own claude/ branch (board rules)"),
    "the guard passes a lease onto another's claim": Mutant(
        guard, "other_holder", lease_onto_any_claim,
        "G11a", "a lease onto a claim whose file on origin names another worker is refused (F7)"),
    "dealing without its taken set": Mutant(
        team, "deal", deal_without_the_taken_set,
        "G14", "the workers of one run are dealt distinct issues"),
    "each leg dealt from its own look": Mutant(
        clerk, "workflow_deals", each_leg_from_its_own_look,
        "G14", "the workers' workflow deals once for the run, from one look, in its first job (F4)"),
    "given to workers needing the label todo": Mutant(
        board, "given_rows", given_rows_needing_todo,
        "G9", "every open, unclaimed issue a worker's label names is shown under Given to workers, todo or not (F14)"),
    "a release that leaves todo off": Mutant(
        coordinator, "labels_put_on", lambda snapshot, key: [],
        "G3", "a release puts todo on an issue that carries none of todo, epic and blocker, so the Queue lists it "
              "again (F15)"),
    "a failed release leaves its todo on": Mutant(
        github, "put_back", lambda api, key, names, put_on=(): ORIGINAL_PUT_BACK(api, key, names),
        "G3L", "a release that fails after putting todo on takes it off again, so the issue is as the run found it (F15)"),
    "a refused todo puts no label back": Mutant(
        github, "put_labels_on", put_labels_on_then(lambda taken_off, put_on: ([], put_on)),
        "G3L", "a release whose todo GitHub refuses puts back the labels it took off (F15's review)"),
    "a todo written and lost stays on": Mutant(
        github, "put_labels_on", put_labels_on_then(lambda taken_off, put_on: (taken_off, ())),
        "G3L", "a todo GitHub wrote and lost its answer to is taken off again with the labels put back (F15's review)"),
    "an epic gets todo": Mutant(
        coordinator, "NEEDS_NO_TODO", frozenset({labels.TODO, labels.BLOCKER}),
        "G3", "a release puts no todo on an epic, which the Queue never lists (F15's review)"),
    "a blocker gets todo": Mutant(
        coordinator, "NEEDS_NO_TODO", frozenset({labels.TODO, labels.EPIC}),
        "G3", "a release puts no todo on a blocker, which the board lists under Blockers (F15's review)"),
    "a blocked task gets no todo": Mutant(
        coordinator, "NEEDS_NO_TODO", frozenset({labels.TODO, labels.EPIC, labels.BLOCKER, labels.BLOCKED}),
        "G3", "a release puts todo on a task labelled blocked too, which the Queue then lists with blocked beside "
              "it (F15's second review)"),
    "the Blockers section left empty": Mutant(
        board, "issue_rows", issue_rows_but_blockers,
        "G9", "the board lists every open issue labelled blocker under Blockers, claimed or not, where a "
              "released blocker is listed (F15's second review)"),
    "the Blockers section without the held blockers": Mutant(
        board, "issue_rows", issue_rows_unclaimed_blockers,
        "G9", "the board lists every open issue labelled blocker under Blockers, claimed or not, so a worker sees "
              "a held blocker is held (F15's third review)"),
    "a held blocker's row without claimed": Mutant(
        board, "issue_rows", issue_rows_hiding(labels.CLAIMED),
        "G9", "a row of the Blockers shows its issue's labels, which a worker reads to leave a held blocker to its "
              "holder (F15's third review)"),
    "a row without blocked": Mutant(
        board, "issue_rows", issue_rows_hiding(labels.BLOCKED),
        "G9", "a row of the Queue or the Blockers shows blocked, which keeps every worker off the issue (F15's "
              "fourth review)"),
    "a row without epic": Mutant(
        board, "issue_rows", issue_rows_hiding(labels.EPIC),
        "G9", "a row of the Blockers shows epic, which keeps every worker off the issue (F15's fourth review)"),
    "the Blockers section without epic blockers": Mutant(
        board, "issue_rows", issue_rows_dropping(labels.BLOCKER, labels.EPIC),
        "G9", "the board lists every open issue labelled blocker under Blockers, an epic among them (F15's fourth "
              "review)"),
    "the Blockers section without blocked blockers": Mutant(
        board, "issue_rows", issue_rows_dropping(labels.BLOCKER, labels.BLOCKED),
        "G9", "the board lists every open issue labelled blocker under Blockers, one labelled blocked among them "
              "(F15's fourth review)"),
    "the Blockers section without the given blockers": Mutant(
        board, "issue_rows", issue_rows_without_given_blockers,
        "G9", "the board lists every open issue labelled blocker under Blockers, one a worker's label gives among "
              "them (F15's fourth review)"),
    "the Queue without the blockers labelled todo": Mutant(
        board, "issue_rows", issue_rows_dropping(labels.TODO, labels.BLOCKER),
        "G5", "the Queue lists every open, unclaimed issue labelled todo that no worker's label gives and that is "
              "no epic, a blocker among them (F15's fourth review)"),
    "a release relabels the board before its comment": Mutant(
        github, "release_one", relabelled_before_the_comment,
        "G9", "the page of a run whose release failed shows the issue's labels as GitHub holds them (F15's fourth "
              "review)"),
    "a bare worker: taken off at release": Mutant(
        labels, "is_holder_label", lambda name: name.startswith(labels.HOLDER_PREFIXES),
        "G3", "a release takes off claimed and the labels that name a worker, and a bare worker: names none (F15's "
              "fourth review)"),
    "the release's own page without its todo": Mutant(
        coordinator, "after_release",
        lambda snapshot, key, released_at, taken_off, put_on=(): ORIGINAL_AFTER_RELEASE(snapshot, key, released_at,
                                                                                        taken_off),
        "G5", "the run that releases a claim lists its issue in the Queue on the page it writes (F15)"),
    "an overlap marker read on any line": Mutant(
        coordinator, "told_numbers", told_on_any_line,
        "G7", "a comment that quotes the marker further down told nothing"),
    "an overlap marker trusted from any author": Mutant(
        coordinator, "told_numbers", told_by_anyone,
        "G7", "only the coordinator's own marker tells a piece of work"),
    "a sort key that calls int after isdigit": Mutant(
        claims, "by_number", sort_key_with_int_after_isdigit,
        "G15", "a branch name never raises, so one hand-made name cannot stop the board (F6)"),
    "a branch name sent unquoted": Mutant(
        github, "quoted", lambda name: name,
        "G15", "a branch name is quoted into the request path, so a hand-made name cannot stop a live run (F6)"),
    "a finished draft's release by hand not seen": Mutant(
        github, "releases", releases_skipping_finished,
        "G9", "a person's release of a finished draft counts, and the board shows it (F10)"),
    "a refused comment stops the run": Mutant(
        github, "post_comments", comments_unguarded,
        "G15", "a refused comment is a line in the log, never a failed run (F11)"),
    "a fork's ready pull request finishes a claim": Mutant(
        coordinator, "is_candidate", reading(ORIGINAL_IS_CANDIDATE, "of_claim", of_claim_by_name),
        "G3", "a pull request from a fork is no claim's, whatever its branch is called, so marked ready it keeps "
              "no claim from its release (F17)"),
    "a fork's ready pull request skipped as a claim's": Mutant(
        github, "ready_branches", ready_branches_by_name,
        "G3", "a claim whose branch a ready pull request from a fork is named like is still read for its release "
              "(F17)"),
    "a fork's finished draft holds a claim": Mutant(
        github, "releases", releases_reading_a_fork_s_draft,
        "G3", "only the claim's own draft, said finished by its author, waits for review; a stranger's draft from "
              "a fork finishes no claim (F17)"),
    "a fork's ready pull request keeps a release from counting": Mutant(
        board, "is_released", reading(ORIGINAL_IS_RELEASED, "of_claim", of_claim_by_name),
        "G3", "a released claim counts as released whatever a fork's pull request named like its branch is "
              "(F17)"),
    "a fork's pull request silenced with a released claim": Mutant(
        board, "comments_due", reading(ORIGINAL_COMMENTS_DUE, "closed_by_release", closed_by_name),
        "G10", "a pull request from a fork hears what any other does, since a release's rename leaves it open "
               "(F17)"),
    "a fork's pull request left off with a released claim": Mutant(
        board, "pull_rows", reading(ORIGINAL_PULL_ROWS, "closed_by_release", closed_by_name),
        "G9", "the board lists a pull request from a fork as one of its own, open after a release's rename "
              "(F17)"),
    "a fork's pull request named on a claim's row": Mutant(
        board, "claim_rows", claim_rows_by_name,
        "G9", "a claim's row names the claim's own pull request, never a fork's named like its branch (F17)"),
    "a fork's pull request taken for a claim's work": Mutant(
        board, "open_work", open_work_by_name,
        "G7", "a pull request from a fork is a piece of open work of its own, which meets the claim named like "
              "its branch (F17)"),
    "a fork's pull request never told main moved": Mutant(
        board, "pulls_behind_main", behind_main_but_forks,
        "G10", "a pull request from a fork hears \"main moved\" as any other does (F17)"),
    "no pull request taken for a stranger's": Mutant(
        github, "mark_strangers", lambda pulls, asked: pulls,
        "G3", "the adapter marks a pull request someone who may not write opened from a claim's branch, so no "
              "reading takes it for the claim's (F19)"),
    "a stranger's ready pull request finishes a claim": Mutant(
        coordinator, "is_candidate", reading(ORIGINAL_IS_CANDIDATE, "of_claim", of_claim_but_strangers),
        "G3", "a pull request a stranger opens from a claim's branch is no claim's, so marked ready it keeps no "
              "claim from its release (F19)"),
    "a stranger's ready pull request skipped as a claim's": Mutant(
        github, "ready_branches", ready_branches_but_strangers,
        "G3", "a claim a stranger's ready pull request from its branch stands beside is still read for its "
              "release (F19)"),
    "a stranger's finished draft holds a claim": Mutant(
        github, "releases", releases_reading_a_stranger_s_draft,
        "G3", "only the claim's own draft, said finished by its author, waits for review; a stranger's draft from "
              "the claim's branch finishes no claim (F19)"),
    "a stranger's ready pull request keeps a release from counting": Mutant(
        board, "is_released", reading(ORIGINAL_IS_RELEASED, "of_claim", of_claim_but_strangers),
        "G3", "a released claim counts as released whatever a stranger's pull request from its branch is (F19)"),
    "a stranger's pull request named on a claim's row": Mutant(
        board, "claim_rows", claim_rows_but_strangers,
        "G9", "a claim's row names the claim's own pull request, never a stranger's from its branch (F19)"),
    "a stranger's pull request taken for a claim's work": Mutant(
        board, "open_work", open_work_but_strangers,
        "G7", "a stranger's pull request from a claim's branch is a piece of open work of its own, which meets "
              "the claim (F19)"),
    "a stranger's pull request listed after its release": Mutant(
        board, "pull_rows", reading(ORIGINAL_PULL_ROWS, "closed_by_release", closed_by_release_as_f17),
        "G9", "a release's rename closes a stranger's pull request from the claim's branch with the claim's own, "
              "so the board lists it no more (F19)"),
    "a stranger's pull request spoken to after its release": Mutant(
        board, "comments_due", reading(ORIGINAL_COMMENTS_DUE, "closed_by_release", closed_by_release_as_f17),
        "G10", "a stranger's pull request that a release's rename closed hears nothing more (F19)"),
    "a refused permission answer taken as no for a pull request": Mutant(
        github, "may_open", may_open_doubting_a_refusal,
        "G6", "a pull request whose author GitHub will not say may write stays the claim's, so a lost answer never "
              "frees a finished claim (F19's review)"),
    "a refused permission answer taken as yes for a release comment": Mutant(
        github, "may_write", may_write_trusting_a_refusal,
        "G3", "a release comment or an overlap marker whose author GitHub will not say may write counts for "
              "nothing, so a lost answer frees no claim and keeps no work from being told (F19's review)"),
    "the clerk carrying by labels alone": Mutant(
        team.Snapshot, "named", holder_by_labels,
        "G14", "a worker carries the claims whose claim files name it, whatever the issue's labels say, and no "
               "other's (F18)"),
    "a shared task dealt to none": Mutant(
        team, "given_to", team.is_mine,
        "G14", "a task the labels of several workers give goes to the first of them in the team's order with "
               "nothing else to do (F18)"),
    "a shared task before one given alone": Mutant(
        team.Snapshot, "own_move", lambda snap, agent: snap.carry(agent),
        "G14", "a worker takes a task given to it alone before one the labels of several workers give (F18)"),
    "a blocked claim carried": Mutant(
        team.Snapshot, "carry", carrying_blocked_claims,
        "G14", "a claim labelled blocked waits on a peer or the coordinator, so its holder is not told to carry "
               "it (F18)"),
    "the clerk dealing blockers in number order": Mutant(
        team.Snapshot, "pool", pool_by_number,
        "G14", "the clerk deals the unclaimed blockers before the Queue, as Pick has a worker take them (F15's "
               "fifth review)"),
    "the clerk dealing an issue labelled blocked": Mutant(
        team.Snapshot, "free", free_though_blocked,
        "G14", "Pick never has anyone take an issue labelled blocked, so the clerk deals none (F15's fifth "
               "review)"),
    "the Overlaps landing a blocker after other work": Mutant(
        board, "landing_key", lambda work: (not work.ready, board.began_at(work.began), work.number),
        "G7", "a blocker lands first, since it stops everyone (F15's fifth review)"),
    "a released claim carried by its holder": Mutant(
        team.Snapshot, "released", lambda snap, issue: False,
        "G14", "a claim that counts as released is nobody's, though its branch stands until the board renames it, "
               "so the clerk sends nobody back to it (F18's review)"),
    "a Worker line naming nobody taken for a name": Mutant(
        team, "claim_worker", blank_names_a_worker,
        "G14", "a claim file whose Worker line names nobody leaves whose the claim is to the issue's label (F18's "
               "review)"),
    "a run dealt as though it were the whole team": Mutant(
        team, "deal_moves", run_dealt_alone,
        "G14", "a run that starts some of the team is dealt in the team's order, so a worker outside it keeps its "
               "share (F18's review)"),
    "a piece of work named by the number it is shown by": Mutant(
        board, "identity", named_by_the_number_shown,
        "G7", "a claim and the pull request from its branch are one piece, named by the issue while the claim "
              "lives and told on it, so opening the pull request tells nothing again; named by the pull "
              "request's number as soon as it has one, as before the fix, the claim is told again of the work it "
              "was told of, and that work of it (F2)"),
    "the markers on a claim's own pull request never read": Mutant(
        coordinator, "told_places", no_place_but_the_identity,
        "G7", "a marker on a claim's own pull request counts as one on the issue, so a claim told there before "
              "F2's fix is told nothing again at the upgrade (F2's review)"),
    "a marker counted whenever it came": Mutant(
        coordinator, "claims_made", no_claim_dated,
        "G7", "an issue's number names each claim on it in turn, so a marker counts for a claim only from the "
              "push that made its branch, and a claim on an issue released and claimed anew is told, and told of, "
              "again (F2's review)"),
    "an undated claim taking no marker": Mutant(
        coordinator, "claims_made", undated_claims_take_no_marker,
        "G7", "a claim whose push GitHub does not date takes every marker, so a run that cannot read the activity "
              "tells nothing again (F2's second review)"),
    "idleness by the committer date": Mutant(
        github, "head_since", head_since_by_the_committer_date,
        "G3", "a claim's last commit is dated by when GitHub received the push of its head, so a head dated ahead "
              "holds no claim past its day and one dated back has none released at once (F8)"),
    "a kept head dated by its push": Mutant(
        board, "release_counts", release_counts_by_time_alone,
        "G5", "the release that kept a head counts for a branch pushed again at it, so the board deletes that "
              "branch at once and the coordinator releases no work twice (F8)"),
    "kept counts only at or after the push": Mutant(
        board, "release_counts", release_counts_kept_after_push,
        "G5", "the release that kept a head counts for a branch pushed again at it whenever it came, before that "
              "push or after, so the board deletes the branch at once (F8's review)"),
    "a kept head released again": Mutant(
        coordinator, "kept_released", lambda snap, key: False,
        "G2", "a branch pushed again at a head a release kept, which the board counts released, is owed no "
              "release, so the coordinator never releases the same work twice (F8)"),
    "a kept release undone holds for good": Mutant(
        coordinator, "kept_released", kept_released_by_the_comment,
        "G3", "a kept head whose release the label claimed undid is released again once its branch goes a day "
              "without a commit, so a person's undoing never holds a claim for good (F8's review)"),
    "the push read once": Mutant(
        github, "head_since", head_since_reading_once,
        "G2", "a first read of a push that GitHub may answer later, or that does not list the push yet, is made "
              "once more before the committer date stands in, so the push a run was started by dates its claim "
              "(F8's review)"),
    "the before side ignored": Mutant(
        github, "pushed_by", pushed_by_after_alone,
        "G2", "an entry that came after the run began, as one that moved the branch from the head the run read, "
              "says the branch had a commit then, so the run dates the claim by it and reads no more (F8's "
              "review)"),
    "a stale entry off the head dates it": Mutant(
        github, "pushed_by", pushed_by_whatever_its_age,
        "G2", "an entry that names another head dates it only when it came after the run began: an older one is "
              "stale, a later push the activity does not list yet having set the head the run read, so the run "
              "reads once more and dates the claim by that push (F8's second review)"),
    "a fresh entry dating only off the head": Mutant(
        github, "pushed_by", pushed_by_off_the_head_alone,
        "G2", "any entry of the branch that came after the run began dates the claim, whatever heads it names, so "
              "after two pushes since the run read the branch the run dates the claim by the newest and reads no "
              "more (F8's fix check)"),
    "the floor dropped": Mutant(
        github, "head_since", head_since_with_no_floor,
        "G3", "where neither read dates the push the claim is dated no earlier than the newest entry either read "
              "listed, so a commit dated back has no claim released in the run its own push started (F8's fix "
              "check)"),
    "the floor of the last read alone": Mutant(
        github, "head_since", head_since_with_the_last_floor,
        "G3", "the floor is the newest entry either read listed, so a second read GitHub refuses keeps the floor "
              "the first listed (F8's fix check)"),
    "the second read judged with no moment of the run": Mutant(
        github, "head_since", head_since_with_no_moment_again,
        "G2", "the second read is judged by when the run began, as the first is, so a push after the run read the "
              "branch that only the second lists dates the claim, and the log says nothing of it (F8's fix "
              "check)"),
    "a floor at the committer date taken for later": Mutant(
        github, "head_since", head_since_with_a_floor_at_the_committer_date,
        "G2", "the floor dates a claim only where it came later than the head's committer date, and the log says "
              "which the run read, so a floor at the committer date's own minute leaves the committer date to "
              "stand in (F8's fix check)"),
    "the floor of the first read alone": Mutant(
        github, "head_since", head_since_with_the_first_floor,
        "G3", "the floor is the newest entry either read listed, so a first read GitHub refuses, or that lists "
              "nothing of the branch, and a second a push behind keep the floor the second listed (F8's third "
              "review)"),
    "the earlier floor": Mutant(
        github, "head_since", head_since_with_the_earlier_floor,
        "G3", "the floor is the newest of the entries the two reads listed, so an activity catching up, two "
              "pushes behind and then one, dates the claim by the later entry and holds it (F8's third review)"),
    "a fresh delete dates nothing": Mutant(
        github, "pushed_by", pushed_by_but_a_delete,
        "G2", "the branch's delete after the run read it dates the claim as a push after the read does, so the "
              "run reads no more and its log says nothing of it (F8's third review)"),
    "the run's event ignored": Mutant(
        github, "head_since", head_since_ignoring_the_run_s_event,
        "G3", "in the run a branch's push started a head no read dates is dated at the run's start, so a claim "
              "commit dated back, whose fresh branch the activity lists nothing of yet, is not released in the "
              "run its own push started (F8's third review)"),
    "an older push trusted in the run its push started": Mutant(
        github, "head_since", head_since_trusting_an_older_push,
        "G3", "in the run a branch's push started a head a read dates by an entry from more than a few minutes "
              "before the run began, an older push of the same head, is dated at the run's start, so a claim "
              "pushed back to a head it had a day before is not released in the run its own push started (F8's "
              "follow-up's second review)"),
    "a kept head's old release taken for the lost comment": Mutant(
        github, "landed_release", landed_release_whenever_it_came,
        "G3L", "a release comment found after a post GitHub refused is this run's only when it is newer than the "
               "latest the run held, so a kept head's earlier release does not make a failed release whole and "
               "its labels are put back (F8's second review)"),
    "the clerk dating by the committer date": Mutant(
        github, "claim_released", claim_released_by_committer_date,
        "G14", "the clerk dates a claim's last commit as the board does, by when GitHub received its push, so a "
               "claim the board counts released is dealt as released (F8)"),
    "the clerk ignoring a kept head": Mutant(
        github, "claim_released", claim_released_ignoring_kept,
        "G14", "the clerk counts a release that kept a head for a branch pushed again at it, as the board does, "
               "so the claim is dealt as released (F8)"),
}


# --- the searches -------------------------------------------------------------

CLAIM = Verb("claim", "a", (1,))
CLAIMED = Verb("label-on", "a", (1, "claimed"))
WORKER = Verb("label-on", "a", (1, "worker:a"))
# A claim held and idle past the release: a claims #1, announces it and the
# clock runs 25 hours.
DUE = (CLAIM, CLAIMED, WORKER, Verb("wait", "clock", (25,)))
SHARED = ("src/shared.py",)

# Cheapest first, since a mutant is judged by the first search that
# catches it. The traces met F4, dealing from differing looks, on the
# library itself until the workflow's one look answered it, and the
# careless search met F7, a lease after a fetch taking another's claim,
# until the guard's fence refused it; nothing is known on the library now.
# The draft's search has GitHub call it blocked, as a rule that holds it
# does (F9), so the adapter's reading of how a held branch stands is
# searched and not only traced. The holder's search runs each board run in
# its two halves (DESIGN.md, 3.3), so a rule that reads the repository
# again in the second half is met. The released blocker's search under
# every fault comes after the blocker's two without faults, which are
# cheaper and so are credited with the mutants they were added for; it
# keeps the one only a refused comment shows, a release that relabels the
# board before its comment. The fork's search has a stranger open a pull
# request from a fork whose branch bears the due claim's name (F17), as a
# draft or ready, and say a draft finished, so every reading of which pull
# request is a claim's is searched; no search before it opens one. The
# second search on one file has the owner label the later claim's issue a
# blocker, so the board's landing order is searched with a blocker in it,
# which lands first though it began last (F15's fifth review).
PLAIN_COMMANDS = [c for c in guard_grammar.commands() if c.prefix == "" and c.wrapper == "{}"]


def traces_lane():
    """Every regression trace played as built; a violation's trace is the
    finding's id and the verbs up to the red step."""
    found = {}
    for finding, trace in replay.traces().items():
        record = replay.replay(trace)
        for at, violation in record.violations:
            verbs_so_far = [v for step in record.steps[:at + 1] for v in step.verbs]
            found.setdefault((violation.guarantee, violation.detail), (finding, *verbs_so_far))
    return found


def guard_lane():
    judgement = guard_grammar.judge(commands=PLAIN_COMMANDS)
    found = {("G11a", f"{d.kind}: {d.why}"): (d.example,) for d in judgement.failures}
    found.update({("G11a", "no answer recorded"): (argv,) for argv in judgement.missing})
    return found


def dealing_lane():
    lane = dealing.run_lane()
    return {("G14", f"#{n} dealt twice"): (" then ".join(deal.order), str(deal.looks))
            for deal in dealing.doubled(lane) for n in deal.doubled}


def labels_lane():
    """One issue carrying each mix of labels the domain gives
    (domains.label_sets), a board run on it and a due claim's release on
    it, each step checked as a search checks one: every list the page
    shows and each row's labels, and every label a release takes off or
    puts on, held to the oracle for every mix a person can label an issue
    with, where each search seeds one (F15's fourth review)."""
    found = {}
    now = Verb("board", verbs.BOARD, (cycle.SCHEDULE, ""))
    for names in domains.label_sets():
        for steps in ((now,), DUE + (now,)):
            model = M.seeded((1,), writers=("a", "b"), labels=tuple(names))
            cycle.board_run(model)
            for step in steps:
                before = model.clone()
                outcome = verbs.apply(model, step)
                for v in invariants.step_checks(before, model, step, outcome):
                    found.setdefault((v.guarantee, v.detail), (f"#1 labelled {', '.join(names) or 'nothing'}",) + steps)
    return found


LANES = (Lane("the regression traces", traces_lane),
         Lane("the guard grammar against git's answers", guard_lane),
         Lane("dealing at every look", dealing_lane))
# Two lanes come after the searches, each wider and slower than any of
# them, so a mutant reaches them only past every search: the clerk's deal
# against Pick, from one look at every state a person can label an issue
# into (dealing.picking_lane, F18), and every label mix on one issue, the
# slowest, last.
PICKING = Lane("the clerk's deal against Pick", dealing.picking_lane)
LABEL_MIXES = Lane("every label mix on one issue", labels_lane)
SEARCHES = (
    Search("a released claim under every fault", DUE, Scope(workers=("a",), people=(), waits=(25,),
                                                             faults=tuple(verbs.FAULTS)), 2),
    Search("a released task given by a label alone, under every fault", DUE,
           Scope(workers=("a",), people=(), waits=(25,), faults=tuple(verbs.FAULTS)), 2, labels=("worker:a",)),
    Search("a released epic", DUE, Scope(workers=("a",), people=(), waits=(25,)), 1, labels=("epic",)),
    Search("a released blocker", DUE, Scope(workers=("a",), people=(), waits=(25,)), 1, labels=("blocker",)),
    Search("a released blocked task", DUE, Scope(workers=("a",), people=(), waits=(25,)), 1, labels=("blocked",)),
    Search("a held blocker", (CLAIM, CLAIMED, WORKER), Scope(workers=("a",), people=(), waits=(25,)), 2,
           labels=("blocker",)),
    Search("a released blocker under every fault", DUE,
           Scope(workers=("a",), people=(), waits=(25,), faults=tuple(verbs.FAULTS)), 2, labels=("blocker",)),
    Search("careless workers on a held claim", (CLAIM, CLAIMED),
           Scope(workers=("a", "b"), people=(M.OWNER,), waits=(25,), careless=True), 3),
    Search("two claims on one file", (CLAIM, Verb("claim", "b", (2,))),
           Scope(workers=("a", "b"), issues=(1, 2), people=(), waits=(25,),
                 files=((1, SHARED), (2, SHARED))), 3, issues=(1, 2)),
    Search("two claims on one file, the later a blocker",
           (CLAIM, Verb("claim", "b", (2,)), Verb("label-on", M.OWNER, (2, "blocker"))),
           Scope(workers=("a", "b"), issues=(1, 2), people=(), waits=(25,),
                 files=((1, SHARED), (2, SHARED))), 1, issues=(1, 2)),
    Search("a held claim, the clock and a release by hand", (CLAIM, CLAIMED, WORKER),
           Scope(workers=("a",), people=(M.OWNER, DANA, SAM), waits=(24, 25)), 4),
    Search("a draft GitHub calls blocked", (CLAIM, Verb("open-draft", "a", (1,))),
           Scope(workers=("a",), people=(M.OWNER,), waits=(25,)), 3, opaque_pulls=True),
    Search("a draft GitHub calls clean while it is behind", (CLAIM, Verb("open-draft", "a", (1,))),
           Scope(workers=("a",), people=(M.OWNER,), waits=(25,)), 3, lenient_rules=True),
    Search("a stranger's pull request from a fork on a due claim", DUE,
           Scope(workers=("a",), people=(M.OWNER,), waits=(25,), forks=True), 3),
    Search("a stranger's pull request from a due claim's own branch", DUE,
           Scope(workers=("a",), people=(M.OWNER,), waits=(25,), strangers=True), 3),
    # GitHub will not say whether sam or dana may write: sam's pull request
    # from the claim's branch stays the claim's and dana's release marker
    # counts for nothing (SPEC["unanswered"]; F19's review).
    Search("a stranger's pull request from a due claim's own branch, GitHub refusing to say who may write", DUE,
           Scope(workers=("a",), people=(M.OWNER, DANA), waits=(25,), strangers=True), 3, unanswered=(SAM, DANA)),
    Search("the holder back between the halves of a run", (CLAIM, CLAIMED, WORKER, Verb("wait", "clock", (25,))),
           Scope(workers=("a",), people=(), waits=(25,), split_board=True), 3),
    Search("a stranger's marker on overlapping claims", (CLAIM, Verb("claim", "b", (2,))),
           Scope(workers=("a", "b"), issues=(1, 2), people=(SAM,), waits=(25,),
                 files=((1, SHARED), (2, SHARED))), 2, issues=(1, 2)),
)
CATCHERS = LANES + SEARCHES + (PICKING, LABEL_MIXES)


# --- running ------------------------------------------------------------------

def start(search):
    """The search's starting state, with the violations met on its prefix."""
    model = M.seeded(search.issues, writers=("a", "b", "c", CAROL, DANA), labels=search.labels)
    model.opaque_pulls = search.opaque_pulls
    model.lenient_rules = search.lenient_rules
    model.unanswered = frozenset(search.unanswered)
    cycle.board_run(model)
    found = []
    for step in search.prefix:
        before = model.clone()
        outcome = verbs.apply(model, step, search.scope)
        found += invariants.step_checks(before, model, step, outcome)
    return model, found


def violations(search):
    """Every distinct violation the search or lane meets, with its first
    trace."""
    if isinstance(search, Lane):
        return search.run()
    model, on_prefix = start(search)
    found = {(v.guarantee, v.detail): search.prefix for v in on_prefix}
    for f in explore.explore(model, search.scope, search.depth).found:
        found.setdefault((f.guarantee, f.detail), search.prefix + tuple(f.trace))
    return found


@contextlib.contextmanager
def switched_on(mutant):
    with mock.patch.object(mutant.module, mutant.name, mutant.replacement):
        yield


class Verdict(NamedTuple):
    mutant: str
    search: str
    guarantee: str
    detail: str
    trace: tuple
    seconds: float

    @property
    def caught(self):
        return bool(self.search)


def judge(name, baselines, searches=CATCHERS):
    """Whether a search catches the mutant: the first search, in order,
    that meets a violation the library itself does not, and the shortest
    such violation it met."""
    began = time.perf_counter()
    for search in searches:
        with switched_on(MUTANTS[name]):
            found = violations(search)
        new = {key: trace for key, trace in found.items() if key not in baselines[search.name]}
        if new:
            (guarantee, detail), trace = min(new.items(), key=lambda item: (len(item[1]), item[0]))
            return Verdict(name, search.name, guarantee, detail, trace, time.perf_counter() - began)
    return Verdict(name, "", "", "", (), time.perf_counter() - began)


def baselines(searches=CATCHERS):
    """What each search meets on the library as it stands: nothing, since
    the open findings are read as built and the rest are fixed, but what a
    search or lane knows (none now; F4 from differing looks and F7 under
    the careless scope until their fixes)."""
    return {search.name: violations(search) for search in searches}


def surprises(plain, searches=CATCHERS):
    """By search, what the library itself breaks beyond the search's known
    findings: a search that is red on the library cannot tell a mutant."""
    found = {}
    for search in searches:
        unknown = sorted(key for key in plain[search.name] if key not in search.known)
        if unknown:
            found[search.name] = unknown
    return found


def run_lane(names=None, searches=CATCHERS):
    plain = baselines(searches)
    return plain, [judge(name, plain, searches) for name in (names or MUTANTS)]


def main(argv=None):
    names = (argv if argv is not None else sys.argv[1:]) or None
    plain, verdicts = run_lane(names)
    status = 0
    for search in CATCHERS:
        for key in search.known:
            if key in plain[search.name]:
                print(f"known    on the library itself in {search.name!r}: {key[0]}, {key[1]}")
    for search, unknown in surprises(plain).items():
        status = 1
        print(f"the library itself breaks a guarantee in {search!r}: {unknown[0]}")
    for v in verdicts:
        if v.caught:
            print(f"caught   {v.mutant}: {v.guarantee} in {v.search!r}, {v.seconds:.1f} s; {v.detail}")
            print(f"         trace: {', '.join(str(step) for step in v.trace)}")
        else:
            status = 1
            print(f"SLIPPED  {v.mutant}: no search caught it ({v.seconds:.1f} s)")
    print(f"{sum(v.caught for v in verdicts)} of {len(verdicts)} mutants caught")
    return status


if __name__ == "__main__":
    sys.exit(main())
