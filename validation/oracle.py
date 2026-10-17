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
from validation/known.json since build step 5 (known.py): none since
F2's fix. Five were switches until their fixes: F2, overlapping work
told again once a claim opened its pull request, which the issue now
names for the whole claim; F12, "main moved" told only in the run
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
    "idle": "A claim whose branch has had no commit for more than 24 hours is released by the coordinator, unless "
            "it is finished",
    "last commit": "When a branch's last commit came, here and below, is when GitHub received the push that set its "
                   "head, as the repository's activity records that push, so no worker's clock decides it",
    "moved off": "an entry that set the branch to another head, or deleted it, dates it too when it came after the "
                 "run began, a push after the run read the branch, while an older one is stale and lists no push "
                 "that set the head",
    "undated push": "the head's committer date stands in where the activity, read once more after a pause when the "
                    "first read lists no push that set the head or GitHub gives it no answer that holds, still lists "
                    "none, or where GitHub refuses to read it",
    "floor": "but never earlier than the newest entry either read listed for the branch, which the push that set the "
             "head came no sooner than.",
    "started by": "In the run that a push to the branch started, which the workflow's event names, a head no read "
                  "dates is dated at the run's start instead, and so is one a read dates by an entry that set it "
                  "more than a few minutes before the run began, an older push of the same head that the activity "
                  "lists while it does not list that push yet, or that push itself where the run, queued behind "
                  "others, began that much after it, for that run alone, since that push came just before the run, "
                  "whatever the two reads list, so no claim is released in the run its own push started; the next "
                  "run reads the activity again.",
    "finished": "an issue labelled in-review, a pull request marked ready, or a draft whose author's latest comment "
                "on it starts with \"finished; ready to mark\" waits for review however long it takes.",
    "order": "The coordinator first takes the label claimed and every worker's label off the issue, a label "
             "already gone counting as taken off, and puts the label todo on it when it carries none of todo, "
             "epic and blocker, so the Queue lists it again even when a worker's label alone gave it to the "
             "worker, while a blocker is listed under Blockers without it; then it posts a comment on the issue "
             "whose first line is `<!-- sdlc-coordinator: claim-released -->`",
    "put back": "A run that fails at the labels, taking one off or putting todo on, puts back the labels it took "
                "off and takes off the todo it put on, even one whose putting on GitHub answered with an error, "
                "since GitHub may have written it",
    "whole": "A run whose comment fails reads the issue's comments, and the comment there after all, one GitHub "
             "wrote and lost its answer to, makes the release whole.",
    "refused": "Where they list none and GitHub refused the comment, with an answer from 400 to 499 other than a "
               "rate limit's, on a try that followed none GitHub refused for a rate limit, which GitHub gives in "
               "place of writing anything, the run puts back the labels it took off and takes off the todo it put on",
    "listed late": "Where they list none after any other answer to the comment, a rate limit's refusal, a 429 or a "
                   "403 that names a rate limit or a wait in retry-after, on the last try or on one the run made the "
                   "comment again after, which GitHub's documentation never says it gives in place of acting on the "
                   "call, a server error, a redirect, an answer the run could not read or no answer at all, none of "
                   "which says GitHub wrote nothing, and GitHub may list a comment it wrote only later",
    "unread": "or where the run cannot read them, whatever GitHub answered the comment with",
    "unsettled": "the run cannot tell whether a release comment stands, its own, an earlier run's or one someone "
                 "else wrote: it leaves the labels as the release left them and counts no release of its own, so "
                 "its page lists the claim as held and the branch keeps its name",
    "settled next": "the next run, reading the comments, finds the release whole where a release comment that "
                    "counts stands, or releases the claim again where none does, unless its holder pushed since, "
                    "which keeps the claim, its labels as the release left them, naming no holder, until it "
                    "finishes, a person puts them back or it goes a day without a commit and is released again, or "
                    "a person put the label claimed back beside a comment that stands, which undoes that release "
                    "(below)",
    "unsettled kept": "though a branch at a head a release already kept is released in that same run by its latest "
                      "release comment, where one that counts stands, which counts for such a branch whenever it "
                      "came, with no word on the issue of this run's release where GitHub wrote none",
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
    "pushed again": "A branch pushed again at the head a release already kept, one a branch "
                    "`released/<issue>-<short sha>` stands at, brings nothing new: its latest release comment counts "
                    "for it whenever it came, before that push or after",
    "kept deleted": "so the board deletes it instead of renaming it, since its work is already kept, and the "
                    "coordinator releases it only where the board counts no release of it, no release comment "
                    "counting or the label claimed put back, once it has gone a day without a commit.",
    "overlap": "the coordinator tells each piece of open work about the work it meets, once, in a comment whose "
               "first line is `<!-- sdlc-coordinator: overlap <numbers> -->` naming every piece it told of",
    "told again": "tells it again only when work it has not been told of starts to meet it",
    "one piece": "The marker names each piece by its identity: a claim and its own pull requests are one piece, the "
                 "issue's number, told on that issue, so opening a pull request tells nothing again",
    "own pull marker": "a marker on one of the claim's own open pull requests counts as one on the issue",
    "no claim's piece": "a pull request that is no claim's is a piece of its own, its number, told on itself",
    "in turn": "An issue's number names each claim on it in turn, so a marker tells a claim's work, or tells of it, "
               "only when it came no earlier than the push that made that claim's branch, as GitHub dates that push "
               "for the order above, and every marker counts for a claim whose push GitHub does not date",
    "open work": "an open pull request by its files or, while it changes nothing but its claim file, by the files "
                 "its claim lists, and a claim with no pull request yet by the files its claim lists",
    "landing order": "a blocker first, since it stops everyone; then work marked ready, which waits only for its "
                     "merge; then the oldest",
    "oldest": "the work that began first, when GitHub says the push that made its branch came, or, for a pull "
              "request from a fork or from a branch it says nothing of, when the pull request was opened; work "
              "whose beginning nothing says, which can only be a claim with no pull request yet, counts as the "
              "newest, since a claim opens its draft at once (Work in the open), and the number breaks a tie",
    "rename first": "Until the board has renamed the branch, a push of the name is still refused",
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
    "stranger": "A claim's pull request is opened by the repository's owner, a member of its organization, a "
                "collaborator or anyone else who may write to the repository, or by a GitHub App, which opens one "
                "only where it is installed, as the workers in Actions open theirs; a pull request anyone else opens "
                "from a claim's branch, as GitHub's documentation says anyone who may read the repository can, is no "
                "claim's either: the board lists it as a pull request of its own and tells it what it tells any "
                "other, it finishes no claim, marked ready or said finished, and a release's rename closes it with "
                "the claim's own, since GitHub closes each open pull request whose branch it renames.",
    "unanswered": "Where GitHub will not answer whether someone may write, a pull request they opened stays the "
                  "claim's, and a release comment or an overlap marker they wrote counts for nothing, so a lost "
                  "answer never frees a finished claim and never keeps work from being told",
    "pick": "Take an issue the Given to workers section gives you first. Else take a blocker, with or without todo, "
            "that the Blockers section lists, that nobody has claimed and that no worker's label gives to another, "
            "since a blocker comes before the Queue. Else take an open issue labelled todo that the Queue section "
            "shows as unclaimed and that no open blocker stands in the way of.",
    "never": "Wherever an issue is listed, never take an epic, which the coordinator plans (Planning), nor an issue "
             "labelled blocked, which waits on a peer or the coordinator.",
    "another's": "An issue with a branch `claude/<issue>` on origin, or a file `docs/agents/claims/<issue>.md` on the "
                 "default branch, is another worker's, whatever its labels say.",
    "several": "Where the labels of several workers name one issue, it is theirs alone in the same way: given to "
               "each of them, and held by the first of them to claim it, whose Announce takes the others' labels off.",
    "blocked": "- blocked: a worker waits on a peer or the coordinator.",
    "held blocker": "a second worker that sees it held waits for it, helps on the one pull request by review, or "
                    "takes other work it does not stand in the way of (Pick)",
    "worker line": "the `- Worker:` line (or the older `- Account:`), whose name is the worker's label without the "
                   "`worker:` prefix",
    "nameless": "a line with nothing after it but markup or a note in parentheses names nobody: the board shows such "
                "a claim as unnamed, since the file is there to be corrected, while the clerk and a release, which "
                "must name a worker, take the one the issue's label names",
    "own claim": "each takes its own claim that needs work, one whose claim file names it, whatever the issue's "
                 "labels say, or, where no claim file names a worker, one whose issue's label names it, a "
                 "worker:<name> label before the older spellings and the first by name among labels of one "
                 "spelling, as a release names whose the claim was",
    "not released": "but never a claim that counts as released, which no worker carries and none takes until the "
                    "board has renamed its branch (Pick, Release)",
    "not blocked": "nor a claim labelled blocked, whose holder waits on a peer or the coordinator (Labels) and so has "
                   "nothing else to do",
    "given first": "else the task it was given, one given to it alone first, and a task the labels of several workers "
                   "give going to the first of them with nothing else to do",
    "team's order": "The order is the team's: every worker the deal is for, in the workers' workflow every registered "
                    "worker in Actions, whether or not the run starts it, so a worker outside the run keeps its share, "
                    "and two runs that look at one state deal no issue to two workers.",
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
# The lines of a claim file that name its worker (SPEC["worker line"]),
# and a note in parentheses after the name, which names nobody
# (SPEC["nameless"]).
WORKER_LINES = ("- Worker:", "- Account:")
NOTE = re.compile(r"\([^()]*\)\s*$")
IDLE_MINUTES = 24 * HOUR
# How long before a run began an entry that set a branch to another head
# may come and still count as having come after (SPEC["moved off"]), in
# minutes: the allowance the library makes for a runner's clock ahead of
# GitHub's, which the spec does not spell and
# tests/test_validation_guarantees.py pins to github.PUSH_SINCE_SLACK. The
# model's pushes come at whole hours, so only an entry at the run's own
# minute is let in by it.
MOVED_SLACK_MINUTES = 5
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
# How a GitHub App's bot login ends (SPEC["stranger"]: "a GitHub App").
APP_SUFFIX = "[bot]"

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

# The run's own words in its log for a claim whose head no read dated by
# the push that set it (SPEC["undated push"], SPEC["floor"], SPEC["started
# by"]), which the spec does not spell: why, the activity listing no push
# of the head or GitHub refusing the read (its answer follows in
# parentheses), and what the run read the claim's idleness from in its
# place, the committer date, the floor, the newest entry's time as GitHub
# wrote it, or the start of the run the branch's push started, as GitHub
# would write it. Each line is the branch's name, ": ", the why and the
# clause. They are the library's interface, as the board's markers are,
# and tests/test_validation_guarantees.py pins them to github.py's lines;
# which clause a line owes is the oracle's (floor_dates, pushed_branch).
LOG_WORDS = {
    "listed none": "the repository's activity lists no push that set the branch's head",
    "older push": "the newest push the repository's activity lists of the branch's head came before this run began",
    "refused": "GitHub refused to say when the push that set the branch's head came (",
    "read from": ", so its idleness is read from ",
    "committer date": ", so its idleness is read from its committer date",
    "floor": ", so its idleness is read from its newest entry's time, {floor}, later than its committer date",
    "run start": ", so its idleness is read from the start of this run, {at}, which its push started",
}
# The events of the board workflow whose run a push to the ref named
# started (SPEC["started by"]: "which the workflow's event names"): a push,
# whose run the job skips for any branch but the default (model.skipped),
# and a branch's creation, the run a claim's first push starts. A pull
# request's synchronize names its head branch too, in the library
# (cli.pushed_branch), which the model, whose pull request events name no
# branch, never plays.
STARTED_BY_EVENTS = ("push", "create")

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


def label_worker(labels):
    """The worker the issue's label names (spec, Labels: `worker:<name>`,
    with the older spellings read as the same label), worker: before the
    older and the first by name among the labels of one spelling; None
    when no label names one. The oracle's own reading, so neither the
    Worker cell nor the clerk's holder (claim_holder) is judged by the
    library's labels.worker_of, which the board and the clerk use; one
    function for both readers, so the two cannot drift apart (F18's second
    review, which found a second copy of it)."""
    for prefix in WORKER_SPELLINGS:
        named = sorted(name[len(prefix):] for name in labels if name.startswith(prefix) and name[len(prefix):])
        if named:
            return named[0]
    return None


def workers_named(labels):
    found = []
    for name in sorted(labels):
        for prefix in WORKER_SPELLINGS:
            if name.startswith(prefix) and name[len(prefix):] and name[len(prefix):] not in found:
                found.append(name[len(prefix):])
    return found


def trusted(model, comment):
    """SPEC["trusted"]; a writer GitHub will not answer for, whom no
    association vouches for, counts for nothing (SPEC["unanswered"])."""
    return (comment.association in TRUSTED_ASSOCIATIONS or comment.author == BOT
            or (comment.author in model.writers and comment.author not in model.unanswered))


def head_time(model, ref):
    """SPEC["last commit"]: the minute GitHub received the push that set
    the branch's head (model.pushed, F8); its committer date where the
    model holds no push that set it, or for a branch whose push the run
    could not date and whose floor is no later, which a view of the model
    leaves out (dated, SPEC["undated push"], SPEC["floor"]), unless the
    run was started by that branch's push, which dates it at the run's
    start (SPEC["started by"], pushed_branch)."""
    head, pushed = model.refs[ref], model.pushed.get(ref)
    if pushed is not None and pushed[1] == head:
        return pushed[0]
    return model.commits[head].committed_at


def entry_dates(sets_head, minute, began):
    """The minute one entry of a branch's activity, the newest a read
    lists, dates the push of the head the run read at, or None: an entry
    that set that head dates it (SPEC["last commit"]: "when GitHub
    received the push that set its head"), and so does every other entry
    of the branch, one that set the branch to another head or deleted it,
    whatever heads it names, when it came after the run began, a push
    after the run read the branch (SPEC["moved off"]), MOVED_SLACK_MINUTES
    before allowed: one that moved the branch off the head, and one that
    names that head on neither side, as the second of two pushes since the
    run read the branch does (F8's fix check, which found the library
    dating nothing by it). An older one is stale, a later push the
    activity does not list yet having set the head the run read, and, as
    any other entry, "lists no push that set the head", which owes a
    second read and, read so last, leaves the committer date to stand in,
    no earlier than the floor (SPEC["undated push"], floor_dates)."""
    if sets_head or minute >= began - MOVED_SLACK_MINUTES:
        return minute
    return None


def pushed_branch(event, ref_name):
    """The branch whose push started a board run for the event, as the
    workflow names it, "" for none (SPEC["started by"]): the ref of a push
    or of a branch's creation (STARTED_BY_EVENTS), as cli.pushed_branch
    reads GITHUB_REF. A claim on it whose push no read of the run dates is
    dated at the run's start, for that run alone, so a claim commit dated
    back, whose fresh branch the activity lists nothing of yet, is not
    released in the run its own push started (F8's third review); and so
    is one a read dates by an entry from before the run began
    (own_push_dates)."""
    return ref_name if event in STARTED_BY_EVENTS and ref_name else ""


def own_push_dates(minute, began):
    """Whether the minute a read dates the push of a head at stands in the
    run that the branch's push started, which began at the minute began:
    only when it came after the run began, MOVED_SLACK_MINUTES before
    allowed, the library's few minutes, as for an entry that names another
    head (entry_dates), since an entry that set the head before that is an
    older push of the same head, the run's own not listed yet, and the
    run's start dates the head in its place (SPEC["started by"]: "and so
    is one a read dates by an entry that set it more than a few minutes
    before the run began"; F8's follow-up's second review, which found the
    claim released in the run its own push started, the oracle agreeing
    with the library). The model's runs begin at the minute of the push
    that starts them, so the run's own push, which a run queued behind
    others reads the same way, is always within the slack here."""
    return minute >= began - MOVED_SLACK_MINUTES


def floor_dates(floor, committed):
    """The minute a claim's last commit came at where the run's last read
    of its push dates none, by the floor (SPEC["floor"]: "never earlier
    than the newest entry either read listed for the branch, which the push
    that set the head came no sooner than"): floor, the minute of the later
    of the newest entries the run's reads listed for the branch (None where
    neither listed one), where it is later than committed, the head's
    committer date; None where it is not, so the committer date stands in
    (SPEC["undated push"]), a floor at the committer date's own minute
    among them, which is no later and so dates nothing the committer date
    does not. Until F8's fix check the committer date stood in whatever the
    reads listed, so where both lagged a commit dated back had its claim
    released in the run its own push started."""
    return floor if floor is not None and floor > committed else None


def dated(model, undated, pushed_at=None):
    """The model as a run saw it that read when the claims' heads were
    pushed so: the undated branches, whose push no read the run made last
    dates and whose floor is no later than the head's committer date,
    GitHub refusing the read or listing no push of the head, a stale entry
    among them, dated by their committer dates (SPEC["undated push"]);
    each branch pushed_at names dated by the minute it gives, the push the
    run read where the model holds another, as an entry after the run
    began that set the branch to a commit the model never took, a push
    after the run read the branch, says the branch had a commit then
    (SPEC["moved off"]; the faults pushes-moved, pushes-moved-twice and
    pushes-500-then-moved), or the floor where no read dated a push and
    the newest entry listed came later than the head's committer date
    (SPEC["floor"], floor_dates; the faults pushes-lagging-twice,
    pushes-lagging-then-500, pushes-catching-up and
    pushes-500-then-lagging), or the run's start where the branch's own
    push started the run and no read dated it (SPEC["started by"],
    pushed_branch); the model itself when nothing differs. A first read
    answered by the second dates the branch by its push, so it is in
    neither (invariants.push_dates, which reads each entry by entry_dates
    and each floor by floor_dates)."""
    moved = {ref: minute for ref, minute in (pushed_at or {}).items()
             if ref in model.refs and ref not in undated and head_time(model, ref) != minute}
    gone = [ref for ref in undated if ref in model.pushed]
    if not moved and not gone:
        return model
    view = model.clone()
    view.pushed = {ref: push for ref, push in view.pushed.items() if ref not in undated}
    for ref, minute in moved.items():
        view.pushed[ref] = (minute, view.refs[ref], "push", view.refs[ref])
    return view


def kept(model, ref):
    """SPEC["pushed again"]: a released branch of the claim's issue stands
    at the claim branch's head."""
    number, head = claim_number(ref), model.refs.get(ref)
    return number is not None and head is not None and model.refs.get(released_name(number, head)) == head


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
    hours after the branch's last commit; or, for a head a release already
    kept, any release comment that counts (SPEC["pushed again"],
    SPEC["kept deleted"])."""
    latest, ref = latest_release(model, number), branch_of(number)
    return latest is not None and (kept(model, ref) or idle(head_time(model, ref), latest))


def finished(model, number):
    """SPEC["finished"], by the claim's own pull request (own_pull)."""
    issue, pull = model.issues.get(number), own_pull(model, branch_of(number))
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
    SPEC["candidates"]): its branch has gone a day without a commit and no
    release comment came more than a day after its last commit; and, for
    a head a release kept, the board counts no release of it, no release
    comment counting or the label claimed put back (SPEC["kept deleted"]),
    so a kept head is not released twice for the same work and a release
    the label undid is made again once the branch has gone a day without
    a commit, where until F8's review any release comment that counts
    held such a claim for good."""
    now = model.now if now is None else now
    ref, issue = branch_of(number), model.issues.get(number)
    if ref not in model.refs or issue is None or issue.state != "open" or finished(model, number):
        return False
    if kept(model, ref) and released(model, number):
        return False
    last, latest = head_time(model, ref), latest_release(model, number)
    return idle(last, now) and not (latest is not None and idle(last, latest))


def released(model, number):
    """SPEC["released"]."""
    ref, issue = branch_of(number), model.issues.get(number)
    if ref not in model.refs or issue is None or issue.state != "open":
        return False
    if CLAIMED in issue.labels or IN_REVIEW in issue.labels:
        return False
    pull = own_pull(model, ref)
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
    """One piece of open work: its identity (SPEC["one piece"]: the issue a
    claim's work is for, else the pull request's number), which the markers
    name it by and which it is told on, the number it is shown by now, its
    files, the landing order's two words, and the minute the work began
    (SPEC["oldest"]): the push that made its branch, or the opening of a
    pull request from a branch the model records no push of; None when
    nothing says; and whether it is a claim's work, whose identity names
    each claim on its issue in turn (SPEC["in turn"])."""

    identity: int
    number: int
    files: tuple
    ready: bool
    blocker: bool
    began: int | None = None
    claim: bool = False


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


def claim_of(model, pull):
    """The issue whose claim the pull request is: its branch's number here,
    and never one from a fork, whose branch is another whatever it is
    called (SPEC["fork"]), nor one that someone who may not write opened
    from the claim's own branch (SPEC["stranger"])."""
    if pull.fork or not opened_by_writer(model, pull):
        return None
    return claim_number(pull.head)


def opened_by_writer(model, pull):
    """SPEC["stranger"]: the owner, a member of the organization or a
    collaborator, by GitHub's author_association, anyone else who may
    write, or a GitHub App; and anyone GitHub will not answer for, whose
    pull request stays the claim's (SPEC["unanswered"])."""
    return (pull.association in TRUSTED_ASSOCIATIONS or pull.author.endswith(APP_SUFFIX)
            or pull.author in model.writers or pull.author in model.unanswered)


def own_pull(model, ref):
    """The claim's own open pull request from the branch of this name here:
    the open one, since GitHub opens one at a time between two branches and
    every pull request of the model's is to main, when someone who may
    write opened it (SPEC["stranger"]); never one from a fork (SPEC["fork"])."""
    pull = model.open_pull_from(ref)
    return pull if pull is not None and opened_by_writer(model, pull) else None


def closed_by_release(model, pull):
    """Whether a release's rename closes the pull request, or, where GitHub
    refused the rename, would have: every one from a released claim's branch
    here, the claim's own and a stranger's alike (SPEC["stranger"]), never
    one from a fork (SPEC["fork"])."""
    number = None if pull.fork else claim_number(pull.head)
    return number is not None and released(model, number)


def open_work(model, known=AS_BUILT, unsaid=frozenset()):
    """Every piece of open work (SPEC["open work"]) but a released claim's.
    A claim and the pull request from its branch are one piece, which
    keeps the issue's number as its identity (SPEC["one piece"]; until
    F2's fix the library named it by the number it was shown by, which the
    switch F2 read). A pull request from a fork is a piece of its own,
    whatever its branch is called, and began when it was opened
    (SPEC["fork"], SPEC["oldest"]); so is a stranger's from a claim's branch
    here, which began when that branch was made, as any pull request from a
    branch here (SPEC["stranger"]). unsaid names the branches GitHub refused
    to date in the run, whose work then began when nothing else says
    (SPEC["oldest"]): a pull request's at its opening, and a claim's never.
    No finding switches this rule now, and known is taken as every rule of
    the page takes it."""
    pieces = []
    pulled = set()
    for pull in sorted(model.pulls.values(), key=lambda p: p.number):
        if pull.state != "open" or closed_by_release(model, pull):
            continue
        number = claim_of(model, pull)
        if number is not None:
            pulled.add(pull.head)
        listed = listed_files(claim_file_text(model, pull.head, number)) if number is not None else []
        files = own(model.pull_files(pull)) or own(listed)
        identity = number if number is not None else pull.number
        began = None if pull.fork or pull.head in unsaid else model.created.get(pull.head)
        began = pull.opened_at if began is None else began
        pieces.append(Piece(identity, pull.number, files, not pull.draft, is_blocker(model, number), began,
                            number is not None))
    for ref in sorted(model.refs):
        number = claim_number(ref)
        if number is None or ref in pulled or released(model, number):
            continue
        files = own(listed_files(claim_file_text(model, ref, number)))
        began = None if ref in unsaid else model.created.get(ref)
        pieces.append(Piece(number, number, files, False, is_blocker(model, number), began, True))
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
    are shown by, in landing order, the pull requests of one claim being
    one piece (SPEC["one piece"]); unsaid as open_work takes it."""
    meeting = {}
    for piece in open_work(model, known, unsaid):
        for path in piece.files:
            meeting.setdefault(path, []).append(piece)
    return {path: [p.number for p in sorted(pieces, key=lambda p: landing(p, known))]
            for path, pieces in sorted(meeting.items()) if len({p.identity for p in pieces}) > 1}


def claims_made(model, pieces, unsaid=frozenset()):
    """When the push that made each claim's branch came, by its issue, for
    the claims among the pieces whose push GitHub dates in the run: the
    model records one, and the run's own read of the branch's activity was
    not refused (unsaid, invariants.unsaid_branches). A claim GitHub does
    not date takes every marker (SPEC["in turn"])."""
    return {p.identity: model.created[branch_of(p.identity)] for p in pieces
            if p.claim and branch_of(p.identity) in model.created and branch_of(p.identity) not in unsaid}


def told_of(model, identity, places, by_number, made):
    """The identities a piece has been told of, from the overlap markers by
    trusted authors on its identity and on the claim's own open pull
    requests (SPEC["one piece"], SPEC["own pull marker"]), each number a
    marker names read as the identity of the piece shown by it now, so a
    marker that names a claim's pull request names the claim, and counted
    only when the marker came no earlier than the push that made the
    branch of the claim it is on and of the claim it names, where the
    model records one (SPEC["in turn"])."""
    told = set()
    for place in places:
        for comment in model.comments_on(place):
            match = OVERLAP_MARKER.fullmatch(first_line(comment.body))
            if not (match and trusted(model, comment)):
                continue
            for other in {by_number.get(int(n), int(n)) for n in match.group(1).split()}:
                if all(comment.at >= made[i] for i in (identity, other) if i in made):
                    told.add(other)
    return told


def overlap_effects(model, known=AS_BUILT, unsaid=frozenset()):
    """SPEC["overlap"], SPEC["told again"], SPEC["one piece"], SPEC["own
    pull marker"], SPEC["no claim's piece"] and SPEC["in turn"]: a comment
    on the identity of each piece that meets work it has not been told of,
    a piece with two pull requests being one, a marker's numbers read as
    the identities of the pieces shown by them now, so one naming a pull
    request closed since names nothing; unsaid names the branches whose
    push the run could not date (claims_made). No finding switches this
    rule now, and known is taken as every rule of the page takes it."""
    pieces = open_work(model, known, unsaid)
    by_number = {p.number: p.identity for p in pieces}
    files, places = {}, {}
    for piece in pieces:
        files.setdefault(piece.identity, set()).update(piece.files)
        places.setdefault(piece.identity, {piece.identity}).add(piece.number)
    made = claims_made(model, pieces, unsaid)
    effects = []
    for identity in sorted(files):
        meets = {other for other in files if other != identity and files[other] & files[identity]}
        if meets and not meets <= told_of(model, identity, sorted(places[identity]), by_number, made):
            effects.append(Effect("comment", identity, "overlap"))
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
    """The open pull requests the board speaks to: all but those from a
    released claim's branch, which the rename closes, a stranger's among
    them, and one from a fork staying (closed_by_release)."""
    return [p for p in sorted(model.pulls.values(), key=lambda p: p.number) if p.state == "open"
            and not closed_by_release(model, p)]


# --- what a board run must do -------------------------------------------------

def expected_effects(before, event="schedule", ref_name="", known=AS_BUILT, unsaid=frozenset()):
    """The writes a fault-free board run owes from the state before it: the
    releases due, in the spec's order, the overlap comments, the renames of
    the released claims and the word on each issue, then the comments on
    pull requests. The oracle applies its own releases to a copy to see
    what the rest of the run reads. unsaid names the branches whose push the
    run could not date, for a run whose only faults were on the activity
    (overlap_effects)."""
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
    effects += overlap_effects(model, known, unsaid)
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
    the first `- Worker:`, or the older `- Account:`, that names one, a
    line with nothing after it but markup or a note in parentheses naming
    nobody (SPEC["nameless"]); None for a file with no such line, or no
    file. Markup is Markdown's code and emphasis marks around the name."""
    for line in (text or "").splitlines():
        for spelling in WORKER_LINES:
            if line.startswith(spelling):
                name = NOTE.sub("", line[len(spelling):]).strip().strip("`*_").strip()
                if name:
                    return name
    return None


def claim_holder(model, number):
    """Whose the claim on the issue is (SPEC["own claim"]): the worker its
    claim file names, whatever the issue's labels say, or, where no claim
    file names a worker, the one its label names (label_worker); None for
    an issue no claim branch holds, a claim that counts as released, which
    is nobody's though its branch stands (SPEC["not released"]), or a claim
    nothing names."""
    if number not in held_numbers(model) or number not in model.issues:
        return None
    named = claim_file_worker(claim_file_text(model, branch_of(number), number))
    return named or label_worker(model.issues[number].labels)


def carries(model, worker):
    """The claims Planning has the worker carry before it takes anything
    (SPEC["own claim"]): its own, on an open issue, and not labelled
    blocked, whose holder waits on a peer or the coordinator and so has
    nothing else to do (SPEC["not blocked"], from F18's second review, which
    found Planning silent on it, and SPEC["blocked"]). The clerk carries a
    claim only when it needs work,
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
    label claimed, which the board's Queue and Given to workers leave out.
    A released claim's branch counts until the board renames it, though
    the board lists its issue again: Pick reads the branch on origin as
    another worker's, whatever the labels say, and the claim it would push
    is refused until the rename (SPEC["rename first"]). Until F18's review
    the oracle let anyone take such an issue, which no lane reached."""
    issue = model.issues.get(number)
    return (issue is not None and issue.state == "open" and EPIC not in issue.labels
            and BLOCKED not in issue.labels and CLAIMED not in issue.labels
            and branch_of(number) not in model.refs and claim_file_text(model, MAIN, number) is None)


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
    workers the deal is for in the team's order (team, SPEC["team's
    order"]) and the clerk's answer for each (moves: "carry <n>", "take
    <n>" or "none"), as sentences. Each worker with a claim of its own
    carries one of its own and nothing else; one with none takes only from
    its place by Pick, the others' takes left out, a task given to it alone
    before any other (SPEC["given first"]), and takes nothing only where
    Pick leaves it nothing or lets it wait (may_wait); a task given to
    several goes to the first of them in the team's order with nothing
    else to do, and to none only when each has something else; and no
    issue goes to two workers. A run that starts only some of the team is
    held to the team's deal by the lane (dealing.judge_case)."""
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
            problems.append(f"#{number} goes to {taker}, though {idle[0]} comes before it in the team "
                            f"with nothing else to do")
    return problems
