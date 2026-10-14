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
from typing import NamedTuple

from sdlc_protocol import board, claims, protocol_check, team

from . import cycle, oracle, page
from .model import BOT, REPO

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
# A body that closes issue N, as GitHub reads one: a closing keyword, in
# any case, then #N and no further digit (spec, Finish: the body says
# "Closes #N"; Rules: "the body closes its issue").
CLOSES = r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#%d\b"


class Violation(NamedTuple):
    guarantee: str
    detail: str


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
    return any(c.method == "GET" and (c.status >= FAILED_READ or c.status in (403, 429)) for c in calls)


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
    claim file"), the spec leaves the board the issue's worker label,
    which the Announce step put there, so the cell names that label's
    worker, by the oracle's own reading of the labels, else says there is
    no claim file. The cell is held to that one value, so a board that
    drops the label, or says there is no claim file while one is there, is
    seen. The soak found the harness holding the cell to the holder after
    the file was gone, on a closed issue's leftover branch whose stale
    label named the worker that had merged, which the spec's words do not
    ask of the board. A file over 1 MB, which the board does not read, is
    one no verb writes."""
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
        if oracle.claim_file_text(model, ref, number):
            if shown != model.creators.get(ref):
                problems.append(Violation("G1", f"the board names {shown} for {ref}, whose holder is "
                                                f"{model.creators.get(ref)}"))
        else:
            owed = oracle.label_worker(model.issues[number].labels) or board.NO_CLAIM_FILE
            if shown != owed:
                problems.append(Violation("G1", f"the board names {shown} for {ref}, whose claim file is gone "
                                                f"and whose issue's label names {owed}"))
    return problems


def shown_worker(model, ref, number):
    """The Worker cell the board's row for the branch would show."""
    issue = model.issues[number]
    pull = model.open_pull_from(ref)
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
    """G12: the check's verdict on each open claim pull request is the spec's."""
    problems = []
    for pull in sorted(model.pulls.values(), key=lambda p: p.number):
        number = oracle.claim_number(pull.head)
        if pull.state != "open" or number is None or pull.head not in model.refs:
            continue
        has_file = oracle.claim_file_text(model, pull.head, number) is not None
        side = {"repo": {"full_name": REPO}}
        found = protocol_check.problems_for({"head": {"ref": pull.head, **side}, "base": side, "draft": pull.draft,
                                             "body": pull.body}, has_file)
        closes = re.search(CLOSES % number, pull.body) is not None
        owed = (pull.draft and not has_file) or (not pull.draft and has_file) or not closes
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


def injected(calls):
    """Whether an injected fault touched the run, read from its calls,
    since a verb may name a fault that never fires; the writes are
    compared with the oracle's, and a half release or a noisy second run
    counted, only for a run no fault touched, while the release order, the
    rename refused and the rest hold under faults too."""
    return any(c.injected for c in calls)


def first_half_checks(before, after, half, known=oracle.AS_BUILT):
    """The guarantees a run's first half answers by itself, given the state
    it started from and the state it left: its writes are the
    coordinator's share of what the oracle owes, each release due and in
    the spec's order, the labels put back where a release failed, no label
    left on a released claim, and the page the state's."""
    if half.raised is not None:
        return [] if reads_failed(half.fake.calls) else [Violation("G15", f"the run's first half raised: {half.raised}")]
    actual = effects_of(half.fake.calls)
    problems = []
    if not injected(half.fake.calls):
        expected = oracle.expected_effects(before, half.event, half.ref_name, known)
        problems += differences([e for e in expected if of_first_half(e)], actual)
        problems += [Violation("G3L", f"a release no rule completes: #{n}") for n in oracle.half_released(after)]
    problems += release_order(actual)
    problems += releases_only_due(before, actual)
    problems += labels_put_back(before, after, half.fake.calls, actual)
    problems += labels_name_the_holder(after, actual)
    problems += board_renders_the_state(after, half, known)
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
    problems = []
    if not injected(record.calls):
        expected = kept_renames(oracle.expected_effects(start, record.event, record.ref_name, known), start, before)
        if split:
            # The second half's writes are the calls after the first half's,
            # whatever their kind, so a label or a release comment it wrote
            # is made and not owed, since the first half's share is judged
            # at its end by first_half_checks.
            expected = [e for e in expected if not of_first_half(e)]
        problems += differences(expected, effects_of(record.calls[record.first_calls:] if split else record.calls))
        if not split:
            problems += [Violation("G3L", f"a release no rule completes: #{n}") for n in oracle.half_released(after)]
        if not split or before.repository_key() == planned.repository_key():
            problems += quiet_second_run(after, record)
    actual = effects_of(record.calls)
    problems += rename_refused_said(before, record, actual)
    if not split:
        problems += release_order(actual)
        problems += releases_only_due(start, actual)
        problems += labels_put_back(start, planned, record.calls, actual)
        problems += labels_name_the_holder(planned, actual)
        problems += board_renders_the_state(planned, record, known)
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


def labels_put_back(before, after, calls, actual):
    """A run that fails between the labels and the comment puts back what it
    took off (spec, Release), unless GitHub refused the putting back too;
    and one whose comment was written, its answer lost or not, puts back
    nothing, since the comment makes the release whole (G3L)."""
    problems = []
    for number in sorted({e.target for e in actual if e.kind == "label-off"}):
        if oracle.Effect("comment", number, "claim-released") in actual:
            if any(e.kind == "label-on" and e.target == number for e in actual):
                problems.append(Violation("G3L", f"#{number}: the release comment stands and its labels "
                                                 f"were put back"))
            continue
        put_back_refused = any(c.method == "POST" and c.path == f"/issues/{number}/labels" and not c.applied
                               for c in calls)
        if not put_back_refused and after.issues[number].labels != before.issues[number].labels:
            problems.append(Violation("G3L", f"#{number}: the release failed and its labels were not put back"))
    return problems


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
            if left and not any(e.kind == "label-on" and e.target == effect.target for e in actual):
                problems.append(Violation("G8", f"#{effect.target} keeps {left} after its release"))
    return problems


def board_renders_the_state(after, record, known):
    """G9 (and G5 and G7 through it): the page lists what the oracle says,
    is ASCII, and renders again to the same bytes."""
    shown = record.page
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
    if page.overlaps(shown) != oracle.overlaps(after, known):
        problems.append(Violation("G7", f"the board's Overlaps are {page.overlaps(shown)}, the oracle's "
                                        f"{oracle.overlaps(after, known)}"))
    return problems


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
