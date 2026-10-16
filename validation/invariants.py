"""The guarantees, one check each, at the moment each is checked (DESIGN.md, 2.3).

Three kinds of moment are checked in this step of the build:
- state, after every step: G1 (one name, one holder, by what every reader
  shows), G10's "once" (no board marker posted twice to one target), G12
  (the protocol check passes exactly what the protocol prescribes);
- command, on every push a verb runs: G11a, for the shapes the rules name
  as allowed, which the guard must pass;
- cycle, on every board run, given the state before and after it and the
  calls the run made: G2, G3, G3L, G4, G5, G6, G7, G8, G9, G10 and G15, and
  the run's effects against the oracle's.

The effects check is the one the prototype lacked. From the state before a
fault-free run the oracle computes every write the run owes (releases,
overlap comments, renames and their word on the issue, "main moved" and
idle comments), and the writes the run made must be the same, as a
multiset, with each release in the spec's order. A difference is reported
under the guarantee its kind belongs to. The gate's guarantee (G13) and
dealing (G14) are later steps' (section 14), and G11a's grammar judged by
git is step 4's.

A run made in two halves with other verbs between them (DESIGN.md, 3.3)
is judged by the states it read, half by half, so a search judges a first
half at its last level as it judges a whole run. The first half is judged
when it is made (first_half_checks): its writes, the labels off and the
comments of the coordinator, against the state it started from, its
labels and its page against the state it left. The second half is judged
when it is made (cycle_checks, with the record carrying the states): its
renames and its comments against the state the run started from, less
the renames of branches that moved between the halves, which it rightly
leaves alone (spec, Release: "a commit pushed to the branch before the
rename, its holder coming back, keeps the claim"), and against the state
it found; and a second run's quiet only when nothing changed between the
halves.
"""

import re
from collections import Counter
from datetime import datetime
from typing import NamedTuple

from sdlc_protocol import board, claims, protocol_check, team

from . import cycle, oracle, page
from .fake_github import head_repo
from .model import BOT, REPO, T0, iso

GUARANTEES = {
    "G1": "state", "G2": "cycle", "G3": "cycle", "G3L": "cycle", "G4": "cycle", "G5": "cycle", "G6": "cycle",
    "G7": "cycle", "G8": "cycle", "G9": "cycle", "G10": "cycle", "G11a": "command", "G11b": "command",
    "G12": "state", "G13": "gate", "G14": "dealing", "G15": "cycle",
}
# The guarantees this step checks; the rest wait for the steps that build
# their worlds (G13, the gate's; G14, dealing; G11b, the second fence,
# which only git and GitHub show).
CHECKED = frozenset(GUARANTEES) - {"G11b"}
# Which guarantee a difference in a run's effects belongs to, by its kind.
EFFECT_GUARANTEE = {
    "label-off": "G3", "label-on": "G3", "rename": "G3", "delete-ref": "G4",
    ("comment", "claim-released"): "G3", ("comment", "overlap"): "G7", ("comment", "released"): "G10",
    ("comment", "rename-refused"): "G10", ("comment", "main-moved"): "G10", ("comment", "idle"): "G10",
}
# Statuses that mean a read did not succeed, as against an answer such as
# 404 that the adapter reads as absence.
FAILED_READ = 500
# The reads a board run does without when GitHub refuses them: how a held
# branch stands (F9) and when a branch began (F13), so a run that fails
# over one of them breaks G15.
READS_DONE_WITHOUT = ("/compare/", "/activity")
# A body that closes issue N, as GitHub reads one: a closing keyword, in
# any case, then #N and no further digit (spec, Finish: the body says
# "Closes #N"; Rules: "the body closes its issue").
CLOSES = r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#%d\b"


class Violation(NamedTuple):
    guarantee: str
    detail: str


# A sha inside a violation's detail: a released branch's name, and main's
# commit in the "main moved" marker.
SHA_IN_DETAIL = re.compile(r"(released/\d+-)[0-9a-f]{7,40}|(main-moved )[0-9a-f]{7,40}")


def identity(detail):
    """The detail with each sha in it read as any sha, what the deep lane
    keys a violation by: a released branch's name and main's commit move
    with the clock and the tree, so one fault met from several prefixes
    showed as a violation per sha, each shortened and written on its own,
    and a shrink could take out no wait before the branch's head, since
    the sha then differed. A trace file names the detail its own steps
    show, sha and all (shrink.shown)."""
    return SHA_IN_DETAIL.sub(lambda m: (m.group(1) or m.group(2)) + "*", detail)


# --- what a run wrote ---------------------------------------------------------

def effects_of(calls):
    """The writes that took effect, as oracle.Effect records."""
    found = []
    for call in calls:
        if not call.applied:
            continue
        parts = call.path.strip("/").split("/")
        if call.method == "DELETE" and parts[0] == "issues" and parts[2] == "labels":
            found.append(oracle.Effect("label-off", int(parts[1]), "/".join(parts[3:])))
        elif call.method == "POST" and parts[0] == "issues" and parts[2] == "labels":
            found.append(oracle.Effect("label-on", int(parts[1]), tuple(sorted(call.body["labels"]))))
        elif call.method == "POST" and parts[0] == "issues" and parts[2] == "comments":
            found.append(oracle.Effect("comment", int(parts[1]), oracle.comment_kind(call.body["body"])))
        elif call.method == "POST" and parts[0] == "branches" and parts[-1] == "rename":
            found.append(oracle.Effect("rename", "/".join(parts[1:-1]), call.body["new_name"]))
        elif call.method == "DELETE" and parts[:3] == ["git", "refs", "heads"]:
            found.append(oracle.Effect("delete-ref", "/".join(parts[3:])))
    return found


def guarantee_of(effect):
    return EFFECT_GUARANTEE.get((effect.kind, effect.detail), EFFECT_GUARANTEE.get(effect.kind, "G3"))


def reads_failed(calls):
    """Whether GitHub refused a read the run needs, which excuses a run that
    fails; a refused read the run does without excuses nothing."""
    return any(c.method == "GET" and (c.status >= FAILED_READ or c.status in (403, 429))
               and not c.path.startswith(READS_DONE_WITHOUT) for c in calls)


# --- state ----------------------------------------------------------------------

class Branches:
    """The one team.Client method team.claim_branches calls."""

    def __init__(self, names):
        self.names = names

    def branch_names(self):
        return list(self.names)


def readers_of(model):
    """Each reader's map from a branch to the issue it takes the branch to
    claim: the board's rows and its claimed numbers, the clerk's claim
    branches and the protocol check."""
    found = {}
    for ref in sorted(model.refs):
        if not ref.startswith(claims.PREFIX):
            continue
        shown = page.number(board.issue_ref(claims.key_of(ref)))
        names = board.claimed_numbers([ref]) | team.claim_branches(Branches([ref]))
        found[ref] = names | ({shown} if shown is not None else set())
    for pull in model.pulls.values():
        if pull.state == "open":
            number = protocol_check.claim_number({"head": {"ref": pull.head}})
            if number is not None:
                found.setdefault(pull.head, set()).add(number)
    return found


def one_name_one_holder(model):
    """G1. The board's Worker cell names the branch's holder while the
    claim file is on the branch, since the file is what the board reads
    the worker from (spec, Claim: "the `- Worker:` line ... whose name is
    the worker's label without the `worker:` prefix"); once the finishing
    push has deleted the file (spec, Finish: "The pull request deletes its
    claim file"), the spec says nothing of the cell, and the cell is held
    to the library's documented reading (board.claim_row): the issue's
    worker label, which the Announce step put there and which the spec's
    Labels line gives the holder, as the spec has the board read a file
    over 1 MB, so the cell names that label's worker, by the oracle's own
    reading of the labels, else says there is no claim file. The cell is
    held to that one value, so a board that
    drops the label, or says there is no claim file while one is there, is
    seen. The soak found the harness holding the cell to the holder after
    the file was gone, on a closed issue's leftover branch whose stale
    label named the worker that had merged, which the spec's words do not
    ask of the board. A file over 1 MB, which the board does not read, is
    one no verb writes. A file whose Worker line names nobody, or that has
    none (spec, Claim: "a line with nothing after it but markup or a note
    in parentheses names nobody"), owes the cell the library's documented
    word for it, unnamed (tests/test_board.py), since the file is there to
    be corrected; the clerk and a release fall back to the issue's label
    for such a file, which the spec's Claim says, and the board does not
    (F18's second review, where the cell was held to the pusher)."""
    problems = []
    for ref, numbers in sorted(readers_of(model).items()):
        for number in numbers:
            if number in model.issues and model.issues[number].state == "open" and ref != oracle.branch_of(number):
                problems.append(Violation("G1", f"a reader takes {ref} to claim #{number}"))
    for ref in sorted(model.refs):
        number = oracle.claim_number(ref)
        if number is None or number not in model.issues or oracle.released(model, number):
            continue
        shown = shown_worker(model, ref, number)
        text = oracle.claim_file_text(model, ref, number)
        if text:
            nameless = oracle.claim_file_worker(text) is None
            owed = claims.UNNAMED if nameless else model.creators.get(ref)
            if shown != owed:
                problems.append(Violation("G1", f"the board names {shown} for {ref}, whose holder is "
                                                f"{model.creators.get(ref)}"
                                                + (" and whose claim file names nobody" if nameless else "")))
        else:
            owed = oracle.label_worker(model.issues[number].labels) or board.NO_CLAIM_FILE
            if shown != owed:
                problems.append(Violation("G1", f"the board names {shown} for {ref}, whose claim file is gone "
                                                f"and whose issue's label names {owed}"))
    return problems


def shown_worker(model, ref, number):
    """The Worker cell the board's row for the branch would show, with the
    claim's own pull request (oracle.own_pull)."""
    issue = model.issues[number]
    pull = oracle.own_pull(model, ref)
    row = board.claim_row(str(number), ref, oracle.claim_file_text(model, ref, number),
                          board.Pull(pull.number, branch=ref, draft=pull.draft) if pull else None,
                          board.ClaimIssue(issue.state, sorted(issue.labels)))
    return row[2]


def markers_once(model):
    """G10's "once": no marker of the board's posted twice to one target."""
    problems = []
    for number in sorted(set(model.issues) | set(model.pulls)):
        lines = Counter(oracle.first_line(c.body) for c in model.comments_on(number) if c.author == BOT)
        for line, count in sorted(lines.items()):
            if count > 1 and oracle.comment_kind(line) in oracle.BOARD_MARKERS:
                problems.append(Violation("G10", f"#{number} heard {line} {count} times"))
    return problems


def protocol_check_agrees(model):
    """G12: the check's verdict on each open claim pull request is the
    spec's, its claim file read at the head the pull request carries; one
    from a fork fails it whatever else holds, since the branch lives in the
    repository itself, never in a fork (spec, Rules)."""
    problems = []
    for pull in sorted(model.pulls.values(), key=lambda p: p.number):
        number, head = oracle.claim_number(pull.head), model.pull_head(pull)
        if pull.state != "open" or number is None or not head:
            continue
        has_file = model.tree(head).get(f"{oracle.CLAIM_DIR}{number}.md") is not None
        found = protocol_check.problems_for({"head": {"ref": pull.head, "repo": head_repo(pull)},
                                             "base": {"repo": {"full_name": REPO}}, "draft": pull.draft,
                                             "body": pull.body}, has_file)
        closes = re.search(CLOSES % number, pull.body) is not None
        owed = pull.fork or (pull.draft and not has_file) or (not pull.draft and has_file) or not closes
        if bool(found) != owed:
            problems.append(Violation("G12", f"the protocol check says {found or 'no problem'} on #{pull.number}"))
    return problems


def state_checks(model):
    return one_name_one_holder(model) + markers_once(model) + protocol_check_agrees(model)


# --- commands ---------------------------------------------------------------

def command_checks(outcome):
    """G11a for the shapes the verbs run through the guard: those the rules
    allow (a plain push of a claim branch, a lease on one's own claim
    branch, a released head pushed again under its claim name) pass, and
    those they forbid that the command shows (a name that reads as a claim
    and claims nothing) are refused. A push by hand meets no guard."""
    if not outcome.command or not outcome.guarded:
        return []
    if outcome.refused_by_guard and outcome.allowed_shape:
        return [Violation("G11a", f"the guard refused {outcome.command}: {outcome.refused_by_guard}")]
    if not outcome.refused_by_guard and not outcome.allowed_shape:
        return [Violation("G11a", f"the guard passed {outcome.command}, which the rules forbid")]
    return []


# --- cycles -----------------------------------------------------------------

def of_first_half(effect):
    """Whether a write is the first half's, the coordinator's: a label off
    or put back, a release comment, an overlap comment. The rest, the
    renames and the comments on pull requests, are the second half's."""
    return effect.kind in ("label-off", "label-on") or (effect.kind == "comment"
                                                        and effect.detail in ("claim-released", "overlap"))


WRITES_KEPT = ("/activity",)


def injected(calls, but=()):
    """Whether an injected fault touched the run, read from its calls,
    since a verb may name a fault that never fires, leaving out the faults
    on the paths but names; the writes are compared with the oracle's, and
    a half release or a noisy second run counted, only for a run no fault
    touched, while the release order, the rename refused and the rest hold
    under faults too. A fault on the activity (WRITES_KEPT) changes only
    what the run reads of when work began and when each claim's head was
    pushed: which work it tells of an overlap, which the oracle reads with
    the branches whose making the run could not read (unsaid_branches),
    and which claims are due and released, which the oracle reads from
    the pushes as the run read them (push_dates, oracle.dated), so the
    writes of a run it alone touched are compared too (F2's second review,
    F8); its second run is not, since that one reads the activity whole."""
    return any(c.injected and c.path not in but for c in calls)


def first_half_checks(before, after, half, known=oracle.AS_BUILT):
    """The guarantees a run's first half answers by itself, given the state
    it started from and the state it left: its writes are the
    coordinator's share of what the oracle owes, each release due and in
    the spec's order, each claim's push read as often as the spec says
    (pushes_read_again) and its log saying what each claim no read dated
    by its push was read from (pushes_logged), the labels put back where a
    release failed, no label left on a released claim, and the page the
    state's, every claim dated as the run read its push (push_dates)."""
    if half.raised is not None:
        return [] if reads_failed(half.fake.calls) else [Violation("G15", f"the run's first half raised: {half.raised}")]
    actual = effects_of(half.fake.calls)
    started_by = oracle.pushed_branch(half.event, half.ref_name)
    dates = push_dates(half.fake.calls, before, started_by)
    seen_before, seen_after = seen(before, dates), seen(after, dates)
    problems = (pushes_read_again(half.fake.calls, before)
                + pushes_logged(half.fake.calls, before, half.lines, started_by))
    if not injected(half.fake.calls, but=WRITES_KEPT):
        expected = oracle.expected_effects(seen_before, half.event, half.ref_name, known,
                                           unsaid_branches(half.fake.calls))
        problems += differences([e for e in expected if of_first_half(e)], actual)
        problems += [Violation("G3L", f"a release no rule completes: #{n}") for n in oracle.half_released(seen_after)]
    problems += release_order(actual)
    problems += releases_only_due(seen_before, actual)
    problems += labels_put_back(before, after, half.fake.calls, actual)
    problems += labels_name_the_holder(after, actual)
    problems += board_renders_the_state(seen_after, half, known)
    return problems


def cycle_checks(before, after, record, known=oracle.AS_BUILT):
    """Every cycle guarantee for one board run made whole, given the state
    before it and the state after; for a run made in two halves, judged
    by first_half_checks when its first half was made, the guarantees its
    second half answers, given the state it found and the state it left,
    with the record carrying the states the run started from and its
    first half left (cycle.RunRecord). For a run made whole, after is the
    state its first half left as far as labels and the page go, since the
    second half changes neither."""
    split = record.started_from is not None
    if record.exit_code != 0:
        if (split and record.raised_in == "plan") or reads_failed(record.calls):
            return []
        return [Violation("G15", f"the run exited {record.exit_code}: {record.raised}")]
    start = record.started_from if split else before
    planned = record.planned if split else after
    started_by = oracle.pushed_branch(record.event, record.ref_name)
    dates = push_dates(record.calls, start, started_by)
    seen_start, seen_planned = seen(start, dates), seen(planned, dates)
    # A run made in two halves had its reads of the pushes judged, and its
    # log, when its first half was made (first_half_checks).
    problems = [] if split else (pushes_read_again(record.calls, start)
                                 + pushes_logged(record.calls, start, record.lines, started_by))
    if not injected(record.calls, but=WRITES_KEPT):
        unsaid = unsaid_branches(record.calls)
        expected = kept_renames(oracle.expected_effects(seen_start, record.event, record.ref_name, known, unsaid),
                                start, before)
        if split:
            # The second half's writes are the calls after the first half's,
            # whatever their kind, so a label or a release comment it wrote
            # is made and not owed, since the first half's share is judged
            # at its end by first_half_checks.
            expected = [e for e in expected if not of_first_half(e)]
        problems += differences(expected, effects_of(record.calls[record.first_calls:] if split else record.calls))
        if not split:
            problems += [Violation("G3L", f"a release no rule completes: #{n}")
                         for n in oracle.half_released(seen(after, dates))]
        if not injected(record.calls) and (not split or before.repository_key() == planned.repository_key()):
            problems += quiet_second_run(after, record)
    actual = effects_of(record.calls)
    problems += rename_refused_said(before, record, actual)
    if not split:
        problems += release_order(actual)
        problems += releases_only_due(seen_start, actual)
        problems += labels_put_back(start, planned, record.calls, actual)
        problems += labels_name_the_holder(planned, actual)
        problems += board_renders_the_state(seen_planned, record, known)
    problems += nothing_lost(start, planned, before, after, actual)
    if after.board_text != record.page:
        problems.append(Violation("G15", "the run exited 0 and its board did not reach the branch board"))
    return problems


def differences(expected, actual):
    """The writes owed and not made, and made and not owed, as a
    multiset, each under the guarantee its kind belongs to; a rename said
    refused is judged by rename_refused_said."""
    made = [e for e in actual if e != oracle.Effect("comment", e.target, "rename-refused")]
    problems = []
    missing, extra = Counter(expected) - Counter(made), Counter(made) - Counter(expected)
    for effect in sorted(missing, key=repr):
        problems.append(Violation(guarantee_of(effect), f"owed and not made: {tuple(effect)}"))
    for effect in sorted(extra, key=repr):
        problems.append(Violation(guarantee_of(effect), f"made and not owed: {tuple(effect)}"))
    return problems


def kept_renames(expected, start, found):
    """The expected effects without the rename or delete of each claim
    branch that is not where the run read it, and without the "released"
    word that the oracle owes right after it."""
    kept, skipping = [], None
    for effect in expected:
        if effect.kind in ("rename", "delete-ref"):
            skipping = oracle.claim_number(effect.target) if found.refs.get(effect.target) != start.refs.get(
                effect.target) else None
            if skipping is not None:
                continue
        elif effect == oracle.Effect("comment", skipping, "released"):
            continue
        else:
            skipping = None
        kept.append(effect)
    return kept


def rename_refused_said(before, record, actual):
    """G10: a rename GitHub refused for good is said once on the issue, in
    the run it was refused, and said at no other time."""
    owed = oracle.rename_refused_effects(before, names_not_freed(before, record))
    said = [e for e in actual if e == oracle.Effect("comment", e.target, "rename-refused")]
    problems = []
    for effect in sorted(Counter(owed) - Counter(said), key=repr):
        problems.append(Violation("G10", f"a refused rename not said on #{effect.target}"))
    for effect in sorted(Counter(said) - Counter(owed), key=repr):
        problems.append(Violation("G10", f"a rename said refused on #{effect.target} with none refused for good"))
    return problems


def names_not_freed(before, record):
    """Each claim name a run failed to free, as (old name, new name, status).
    A rename GitHub refused with 422 because the new name keeps the old
    name's commit already is not one: the branch was pushed again at the
    head a release kept, and the run deletes it instead (spec, Release:
    "A branch pushed again at the head a release already kept is deleted
    instead"); a refusal of that delete leaves the name taken, as a refused
    rename does."""
    found = []
    for call in record.calls:
        if call.applied:
            continue
        if call.method == "POST" and call.path.endswith("/rename"):
            old, new = call.path[len("/branches/"):-len("/rename")], call.body["new_name"]
            if not (call.status == 422 and before.refs.get(new) is not None
                    and before.refs.get(new) == before.refs.get(old)):
                found.append((old, new, call.status))
        elif call.method == "DELETE" and call.path.startswith("/git/refs/heads/" + oracle.CLAIM_PREFIX):
            old = call.path[len("/git/refs/heads/"):]
            number = oracle.claim_number(old)
            if number is not None and old in before.refs:
                found.append((old, oracle.released_name(number, before.refs[old]), call.status))
    return found


def release_order(actual):
    problems = []
    for at, effect in enumerate(actual):
        if effect == oracle.Effect("comment", effect.target, "claim-released"):
            later = actual[at + 1:]
            if any(e.kind == "label-off" and e.target == effect.target for e in later):
                problems.append(Violation("G3", f"#{effect.target}: a label came off after the release comment"))
            if oracle.Effect("label-on", effect.target, (oracle.TODO,)) in later:
                problems.append(Violation("G3", f"#{effect.target}: todo went on after the release comment"))
        if effect.kind == "rename":
            number = oracle.claim_number(effect.target)
            if oracle.Effect("comment", number, "claim-released") in actual[at + 1:]:
                problems.append(Violation("G3", f"{effect.target} renamed before its release comment"))
    return problems


def releases_only_due(before, actual):
    """G2 and G6: every release comment the run posted is on a claim that
    was due, and none on a finished claim."""
    problems = []
    for effect in actual:
        if effect.kind == "comment" and effect.detail == "claim-released":
            if oracle.finished(before, effect.target):
                problems.append(Violation("G6", f"released the finished claim on #{effect.target}"))
            elif not oracle.due(before, effect.target):
                problems.append(Violation("G2", f"released #{effect.target}, which was not due"))
    return problems


def is_put_back(effect, number):
    """A label put on the issue that is no release's todo: what a failed
    release puts back (spec, Release)."""
    return effect.kind == "label-on" and effect.target == number and effect.detail != (oracle.TODO,)


def labels_put_back(before, after, calls, actual):
    """A run that fails between the labels and the comment puts back what it
    took off and takes off the todo it put on (spec, Release), unless GitHub
    refused the putting back too; and one whose comment was written, its
    answer lost or not, puts back nothing, since the comment makes the
    release whole (G3L)."""
    problems = []
    todo_on = {e.target for e in actual if e == oracle.Effect("label-on", e.target, (oracle.TODO,))}
    for number in sorted({e.target for e in actual if e.kind == "label-off"} | todo_on):
        if oracle.Effect("comment", number, "claim-released") in actual:
            taken_back = oracle.Effect("label-off", number, oracle.TODO) in actual
            if taken_back or any(is_put_back(e, number) for e in actual):
                problems.append(Violation("G3L", f"#{number}: the release comment stands and its labels "
                                                 f"were put back"))
            continue
        if not put_back_refused(calls, number) and after.issues[number].labels != before.issues[number].labels:
            problems.append(Violation("G3L", f"#{number}: the release failed and its labels were not put back"))
    return problems


def put_back_refused(calls, number):
    """Whether GitHub refused a step of putting the issue back: the labels
    taken off, put on again, or the todo put on, taken off again. A refused
    todo is the release failing, not its putting back; and a 404 to taking
    the todo off is no refusal, since it says the todo is not there, as
    after a refused todo GitHub did not write, which the library counts as
    taken off (github.take_label_off)."""
    for c in calls:
        if c.applied:
            continue
        if c.method == "POST" and c.path == f"/issues/{number}/labels" and c.body["labels"] != [oracle.TODO]:
            return True
        if c.method == "DELETE" and c.path == f"/issues/{number}/labels/{oracle.TODO}" and c.status != 404:
            return True
    return False


def nothing_lost(start, planned, found, after, actual):
    """G4: neither half made a commit unreachable, and a claim branch the
    run deleted is kept under its released name."""
    problems = []
    lost = (start.reachable() - planned.reachable()) | (found.reachable() - after.reachable())
    if lost:
        problems.append(Violation("G4", f"the run made {len(lost)} commits unreachable"))
    for effect in actual:
        if effect.kind == "delete-ref":
            number, sha = oracle.claim_number(effect.target), found.refs.get(effect.target, "")
            if number is None or after.refs.get(oracle.released_name(number, sha)) != sha:
                problems.append(Violation("G4", f"deleted {effect.target} with no released branch keeping it"))
    return problems


def labels_name_the_holder(after, actual):
    """G8: an issue the run released carries neither claimed nor a worker's
    label, in any spelling."""
    problems = []
    for effect in actual:
        if effect.kind == "comment" and effect.detail == "claim-released":
            left = oracle.labels_released(after, effect.target)
            if left and not any(is_put_back(e, effect.target) for e in actual):
                problems.append(Violation("G8", f"#{effect.target} keeps {left} after its release"))
    return problems


def board_renders_the_state(after, record, known):
    """G9 (and G5 and G7 through it): the page lists what the oracle says,
    its Claims, each with its own pull request, Queue, Given to workers,
    Blockers, Open pull requests and Overlaps, each row of the Queue and
    the Blockers with its issue's labels, is ASCII, and renders again to
    the same bytes."""
    shown = record.page
    calls = record.fake.calls if hasattr(record, "fake") else record.calls
    problems = []
    if not shown.isascii():
        problems.append(Violation("G9", "the page is not ASCII"))
    if board.render(record.snapshot) != shown:
        problems.append(Violation("G9", "one snapshot rendered two pages"))
    listed = sorted(board.escaped(b) for b in oracle.claims_listed(after))
    if page.claim_branches(shown) != listed:
        problems.append(Violation("G5", f"the board lists claims {page.claim_branches(shown)}, the oracle {listed}"))
    if page.queue(shown) != oracle.queue(after):
        problems.append(Violation("G5", f"the board's Queue is {page.queue(shown)}, the oracle's {oracle.queue(after)}"))
    if page.given(shown) != oracle.given(after, known):
        problems.append(Violation("G9", f"Given to workers is {page.given(shown)}, the oracle's "
                                        f"{oracle.given(after, known)}"))
    if sorted(page.blockers(shown)) != oracle.blockers(after):
        problems.append(Violation("G9", f"the board's Blockers are {sorted(page.blockers(shown))}, the oracle's "
                                        f"{oracle.blockers(after)}"))
    problems += rows_show_labels(after, shown, calls)
    spoken = [p.number for p in oracle.pulls_spoken_to(after)]
    if page.pulls(shown) != spoken:
        problems.append(Violation("G9", f"the board's Open pull requests are {page.pulls(shown)}, the oracle's "
                                        f"{spoken}"))
    problems += claim_rows_name_their_own(after, shown)
    expected = oracle.overlaps(after, known, unsaid_branches(calls))
    if page.overlaps(shown) != expected:
        problems.append(Violation("G7", f"the board's Overlaps are {page.overlaps(shown)}, the oracle's {expected}"))
    return problems


def rows_show_labels(after, shown, calls=()):
    """G9: each row of the Queue and the Blockers shows its issue's labels,
    which a worker reads to pick (spec, Pick: never an epic, an issue
    labelled blocked, or a blocker someone claimed or a worker's label
    gives to another). Judged for every row but an issue's whose putting
    back GitHub refused in the run (put_back_refused): a run that gave a
    release up and could not put its labels back renders the labels it
    read, which the next run corrects. Until F15's fourth review a run any
    fault touched was not judged at all, which hid a release that relabels
    the board before its comment and leaves it so when the comment fails."""
    problems = []
    for name, rows in (("Queue", page.queue_labelled(shown)), ("Blockers", page.blockers(shown))):
        for number, names in sorted(rows.items()):
            issue = after.issues.get(number)
            if issue is None or put_back_refused(calls, number):
                continue
            if sorted(names) != sorted(issue.labels):
                problems.append(Violation("G9", f"the {name} row of #{number} shows {sorted(names)}, its issue "
                                                f"carries {sorted(issue.labels)}"))
    return problems


def claim_rows_name_their_own(after, shown):
    """G9: each claim's row names the claim's own pull request, the open one
    from its branch here that someone who may write opened, or none; never
    one from a fork whose branch bears the same name, nor a stranger's from
    the branch itself, which are no claim's (SPEC["fork"],
    SPEC["stranger"])."""
    problems = []
    for _, branch, _, cell in page.claims(shown):
        if oracle.claim_number(branch) is None or branch not in after.refs:
            continue
        own = oracle.own_pull(after, branch)
        if page.number(cell) != (own.number if own else None):
            problems.append(Violation("G9", f"the row of {branch} names {cell}, its own pull request is "
                                            f"{f'#{own.number}' if own else 'none'}"))
    return problems


def unsaid_branches(calls):
    """The branches whose own read of their making in the repository's
    activity GitHub refused in a run, whose work then began when nothing
    else says (SPEC["oldest"]); a refused read of the newest creations of
    the whole repository makes no branch unsaid, since each branch is then
    asked about on its own."""
    return refused_activity(calls, "branch_creation")


def undated_branches(calls, model, started_by=""):
    """The claim branches whose push of their head a run could not date,
    which it then dated by their committer dates (SPEC["undated push"],
    F8): those whose last read in the run GitHub refused or answered with
    a list that names no push of the head the run read from the model,
    and whose floor, the newest entry either read listed, came no later
    than the head's committer date (SPEC["floor"]); a first read answered
    by the second leaving the branch dated by its push, a floor later
    than the committer date dating it by the floor, which the run's log
    still says (pushes_logged), and the branch whose push started the run,
    started_by, dated at the run's start (SPEC["started by"])."""
    return frozenset(branch for branch, minute in push_dates(calls, model, started_by).items() if minute is None)


def refused_activity(calls, kind):
    """The branches whose own read of the repository's activity, of the
    kind asked for (None for no kind named), GitHub refused in a run."""
    return frozenset(branch for branch, reads in activity_reads(calls, kind).items()
                     if any(read.status != 200 for read in reads))


def activity_reads(calls, kind):
    """Each branch's own reads of the repository's activity in a run, of
    the kind asked for (None for no kind named, a read of the push that
    set a claim's head, github.read_push), in the order made, by
    branch name."""
    found = {}
    for call in calls:
        query = dict(call.query)
        ref = query.get("ref", "")
        if (call.method == "GET" and call.path == "/activity" and ref.startswith("refs/heads/")
                and query.get("activity_type") == kind):
            found.setdefault(ref[len("refs/heads/"):], []).append(call)
    return found


class PushReading(NamedTuple):
    """What a run's reads of when GitHub received the push that set one
    claim's head say, by the spec's words: the minute the run dates the
    claim's last commit at, None where the committer date stands in; what
    dates it, "push" where the last read dated the push (SPEC["last
    commit"], SPEC["moved off"]), "run start" where no read did and the
    branch's own push started the run (SPEC["started by"]), "floor" where
    neither and the newest entry the reads listed came later than the
    head's committer date (SPEC["floor"]), and "committer date" where
    none of those; why no read dated the push, by its key in
    oracle.LOG_WORDS ("listed none" or "refused"), "" where nothing is to
    be said, a read having dated the push, GitHub keeping no activity of
    the repository (a 404), or the second read saying so after a first
    that listed nothing; and the time the run's log quotes, the floor's
    as GitHub wrote it or the run's start as GitHub would write it, ""
    where neither dates the claim."""

    minute: int | None
    by: str
    why: str = ""
    stamp: str = ""


def push_readings(calls, model, started_by=""):
    """Each claim branch's PushReading, by branch name, from the reads of
    its head's push a run made (activity_reads), the heads the run read in
    the model and the minute it began, the model's clock, which the run is
    handed as its now (cycle.start_run, clerk.ModelClient.released), and
    the branch whose push started the run, started_by, "" for none
    (oracle.pushed_branch): the last read dates the push, unless it dates
    the branch whose push started the run by an entry from before the run
    began (oracle.own_push_dates); or else the run's start dates the
    branch whose push started the run (SPEC["started by"]), the why then
    "older push" where the last read dated it so; or else the floor, the
    later of the newest entries the reads listed, dates the claim where it
    came later than the head's committer date (oracle.floor_dates); or
    else the committer date
    stands in. Why nothing dated the push is the last read's reason, or
    the first's where the last said nothing and the first listed an entry
    (github.head_since). Until F8's fix check the harness read the last
    read alone, and the committer date stood in wherever it dated no
    push."""
    found = {}
    for branch, reads in activity_reads(calls, None).items():
        head = model.refs.get(branch)
        pushed = read_dates(reads[-1], branch, head, model.now)
        if pushed is not None and (branch != started_by or oracle.own_push_dates(pushed, model.now)):
            found[branch] = PushReading(pushed, "push")
            continue
        why = read_why(reads[-1]) or (read_why(reads[0]) if read_floor(reads[0], branch) is not None else "")
        if branch == started_by:
            found[branch] = PushReading(model.now, "run start", why if pushed is None else "older push", iso(model.now))
            continue
        floor = max((f for f in (read_floor(read, branch) for read in reads) if f is not None), default=None,
                    key=lambda f: f[0])
        committed = model.commits[head].committed_at if head in model.commits else None
        minute = None if floor is None or committed is None else oracle.floor_dates(floor[0], committed)
        found[branch] = (PushReading(minute, "floor", why, floor[1]) if minute is not None
                         else PushReading(None, "committer date", why))
    return found


def push_dates(calls, model, started_by=""):
    """What a run read of when each claim branch's last commit came, by
    branch name (push_readings): the minute its reads date it at, by the
    push the last of them dates, by the run's start for the branch whose
    push started the run (SPEC["started by"]) or by the floor
    (SPEC["floor"]), or None where none dates it, so the run dates the
    branch by its committer date (SPEC["undated push"])."""
    return {branch: reading.minute for branch, reading in push_readings(calls, model, started_by).items()}


def read_dates(call, branch, head, began):
    """The minute one read of the activity, by a run that began at the
    minute began, dates the push of the head at: by its newest entry, the
    branch's, as oracle.entry_dates reads one from the spec's words, an
    entry that set the head, or any other entry of the branch that came
    after the run began, whatever heads it names (SPEC["last commit"],
    SPEC["moved off"]); None for a read GitHub refused, one whose newest
    entry is another branch's, and one whose newest entry names another
    head and came before the run began, a stale one, as a list that does
    not show the latest push yet gives, which lists no push that set the
    head (SPEC["undated push"])."""
    newest = newest_listed(call, branch)
    if newest is None or head is None:
        return None
    return oracle.entry_dates(newest.get("after") == head, minute_of(newest["timestamp"]), began)


def newest_listed(call, branch):
    """The newest entry one read of the activity listed for the branch,
    None for a read GitHub refused or one that listed none of it."""
    newest = call.answered[0] if call.status == 200 and call.answered else None
    return newest if isinstance(newest, dict) and newest.get("ref") == f"refs/heads/{branch}" else None


def read_floor(call, branch):
    """The floor one read sets (SPEC["floor"]), as (minute, its time as
    GitHub wrote it): the newest entry it listed for the branch, which the
    push that set the head came no sooner than; None where it listed none."""
    newest = newest_listed(call, branch)
    return None if newest is None else (minute_of(newest["timestamp"]), newest["timestamp"])


def read_why(call):
    """Why one read that dates no push dates none, by its key in
    oracle.LOG_WORDS: "listed none" for a list that names no push of the
    head, "" for a 404, a repository GitHub keeps no activity of, whose
    committer dates were always the rule, and "refused" for any other
    answer."""
    if call.status == 200:
        return "listed none"
    return "" if call.status == 404 else "refused"


def pushes_logged(calls, model, lines, started_by=""):
    """G2's log: the run says in its log, for each claim whose idleness it
    read from something other than the push that set its head, why and
    what it read it from, the start of the run where the claim's own push
    started it (SPEC["started by"]), else the floor where the newest entry
    either read listed came later than the head's committer date and the
    committer date where not (SPEC["undated push"], SPEC["floor"]), in its
    own words (oracle.LOG_WORDS); and it says nothing of a claim a read
    dated by its push, nor of one GitHub keeps no activity of. The page
    says none of it, so the log is where a token that may not read the
    activity shows; and since the floor dates a claim at the minute a push
    after the run began would, a run that reads such a push as none, or
    takes a floor at the committer date's own minute for a later one,
    shows here alone (F8's fix check)."""
    said = {}
    for line in lines:
        if oracle.LOG_WORDS["read from"] in line:
            said.setdefault(line.split(": ", 1)[0], []).append(line)
    problems = []
    for branch, reading in sorted(push_readings(calls, model, started_by).items()):
        found, owed = said.pop(branch, []), owed_line(branch, reading)
        if owed is None and found:
            problems.append(Violation("G2", f"the run's log says {branch}'s idleness is read from "
                                            f"{line_reads(found[0])}, where its reads owe no line"))
        elif owed is not None and len(found) != 1:
            problems.append(Violation("G2", f"the run's log has {len(found)} lines on {branch}, whose reads owe "
                                            f"one saying its idleness is read from {line_reads(''.join(owed))}"))
        elif owed is not None and not says(found[0], *owed):
            problems.append(Violation("G2", f"the run's log says {branch}'s idleness is read from "
                                            f"{line_reads(found[0])}, where its reads owe "
                                            f"{line_reads(''.join(owed))}{floor_differs(found[0], owed)}"))
    for branch in sorted(said):
        problems.append(Violation("G2", f"the run's log speaks of {branch}, whose push it never read"))
    return problems


def owed_line(branch, reading):
    """The line the run's log owes a claim, as its start and its end, with
    GitHub's answer to a refused read between them; None where none is
    owed, a read having dated the push or no reason being said."""
    if reading.by == "push" or not reading.why:
        return None
    start = f"{branch}: {oracle.LOG_WORDS[reading.why]}"
    end = (oracle.LOG_WORDS["floor"].format(floor=reading.stamp) if reading.by == "floor"
           else oracle.LOG_WORDS["run start"].format(at=reading.stamp) if reading.by == "run start"
           else oracle.LOG_WORDS["committer date"])
    return start, end


def says(line, start, end):
    """Whether a log line is the one owed: its start and its end, with
    GitHub's answer, closed by a parenthesis, between them after a refused
    read, and nothing between them after a list that names no push."""
    if not (line.startswith(start) and line.endswith(end)) or len(line) < len(start) + len(end):
        return False
    between = line[len(start):len(line) - len(end)]
    return between.endswith(")") if start.endswith("(") else between == ""


def line_reads(line):
    """What a log line says a claim's idleness is read from, and after
    what, in words that name no time and no answer, so a fault met at many
    minutes is one violation."""
    why = ("a refusal" if oracle.LOG_WORDS["refused"] in line
           else "a list naming no push" if oracle.LOG_WORDS["listed none"] in line
           else "a list naming a push from before the run" if oracle.LOG_WORDS["older push"] in line
           else "words not the run's")
    floor_end, start_end = oracle.LOG_WORDS["floor"].split("{floor}")[1], oracle.LOG_WORDS["run start"].split("{at}")[1]
    by = ("its committer date" if line.endswith(oracle.LOG_WORDS["committer date"])
          else "its newest entry's time" if line.endswith(floor_end)
          else "the start of this run" if line.endswith(start_end) else "nothing the run says")
    return f"{by}, after {why}"


def floor_differs(line, owed):
    """", the floor's time differing" where a line reads the floor, or the
    run's start, after the reason owed but quotes another time than the
    newest entry the reads listed, or than the run's start."""
    return ", the floor's time differing" if line_reads(line) == line_reads("".join(owed)) else ""


def minute_of(stamp):
    """A time as GitHub writes one, as a model minute (model.iso)."""
    return int((datetime.fromisoformat(stamp.replace("Z", "+00:00")) - T0).total_seconds()) // 60


def may_tell_more(call, branch, head, began):
    """Whether a second read may tell more than this first one, of a run
    that began at the minute began (SPEC["undated push"]): GitHub gave no
    answer that holds, a 5xx, a 429 or a 403 saying a rate limit was
    reached, or answered a list that "lists no push that set the head",
    a stale entry among them, one that names another head and came before
    the run began (read_dates, SPEC["moved off"]); a 404, a repository
    GitHub keeps no activity of, and any other refusal are its answer for
    good."""
    if call.status == 200:
        return read_dates(call, branch, head, began) is None
    said = (call.fault.message if call.fault is not None else "") or ""
    return call.status >= 500 or call.status == 429 or (call.status == 403 and "rate limit" in said.lower())


def pushes_read_again(calls, model):
    """G2's dating: a run reads the push of a claim's head once more, after
    a pause, exactly where its first read may tell more (may_tell_more),
    and otherwise once, so a first read GitHub answers later still dates
    the claim by its push and no answer that holds costs a second call
    (SPEC["undated push"])."""
    problems = []
    for branch, reads in activity_reads(calls, None).items():
        owed = 2 if may_tell_more(reads[0], branch, model.refs.get(branch), model.now) else 1
        if len(reads) != owed:
            problems.append(Violation("G2", f"the run read the push of {branch}'s head {len(reads)} times, "
                                            f"where its first read owes {owed}"))
    return problems


def seen(model, dates):
    """The model as a run saw it that read the pushes of the claims' heads
    as dates says (push_dates, from the state the run started from):
    oracle.dated, with the branches it dated by their committer dates
    undated, and each it dated by a push or by the floor dated by that
    minute."""
    undated = frozenset(branch for branch, minute in dates.items() if minute is None)
    return oracle.dated(model, undated, {branch: m for branch, m in dates.items() if m is not None})


def quiet_second_run(after, record):
    """G9: a second run with nothing new makes no write call."""
    again = after.clone()
    second = cycle.board_run(again, record.event, record.ref_name)
    writes = [f"{c.method} {c.path}" for c in second.calls if c.method != "GET"]
    return [Violation("G9", f"a second run with nothing new wrote: {writes}")] if writes else []


# --- one step -----------------------------------------------------------------

def step_checks(before, after, verb, outcome, known=oracle.AS_BUILT):
    """Every check one step owes: the state's, the command's, and a board
    run's, whole or by the half the step made."""
    problems = state_checks(after) + command_checks(outcome)
    if outcome.half is not None:
        problems += first_half_checks(before, after, outcome.half, known)
    if outcome.run is not None:
        problems += cycle_checks(before, after, outcome.run, known)
    return problems
