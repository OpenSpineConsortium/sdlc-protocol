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
CHECKED = frozenset(GUARANTEES) - {"G11b", "G13", "G14"}
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
    """G1."""
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
        if shown not in (model.creators.get(ref), board.NO_CLAIM_FILE, board.CLAIM_FILE_NOT_READ):
            problems.append(Violation("G1", f"the board names {shown} for {ref}, whose holder is "
                                            f"{model.creators.get(ref)}"))
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

def cycle_checks(before, after, record, known=oracle.AS_BUILT):
    """Every cycle guarantee for one board run. The writes are compared
    with the oracle's, and a half release or a noisy second run counted,
    only for a run no injected fault touched, read from the calls, since
    a verb may name a fault that never fires; the release order, the
    rename refused and the rest hold under faults too."""
    if record.exit_code != 0:
        if reads_failed(record.calls):
            return []
        return [Violation("G15", f"the run exited {record.exit_code}: {record.raised}")]
    problems = []
    actual = effects_of(record.calls)
    if not any(c.injected for c in record.calls):
        problems += effects_agree(before, record, actual, known)
        problems += [Violation("G3L", f"a release no rule completes: #{n}") for n in oracle.half_released(after)]
        problems += quiet_second_run(after, record)
    problems += release_order(actual)
    problems += rename_refused_said(before, record, actual)
    problems += releases_only_due(before, actual)
    problems += labels_put_back(before, after, record, actual)
    problems += nothing_lost(before, after, actual)
    problems += labels_name_the_holder(after, actual)
    problems += board_renders_the_state(after, record, known)
    if after.board_text != record.page:
        problems.append(Violation("G15", "the run exited 0 and its board did not reach the branch board"))
    return problems


def effects_agree(before, record, actual, known):
    """The run's writes are the oracle's, and each release keeps the spec's
    order: the labels off, then the comment, then the rename, then the word
    on the issue."""
    expected = oracle.expected_effects(before, record.event, record.ref_name, known)
    made = [e for e in actual if e != oracle.Effect("comment", e.target, "rename-refused")]
    problems = []
    missing, extra = Counter(expected) - Counter(made), Counter(made) - Counter(expected)
    for effect in sorted(missing, key=repr):
        problems.append(Violation(guarantee_of(effect), f"owed and not made: {tuple(effect)}"))
    for effect in sorted(extra, key=repr):
        problems.append(Violation(guarantee_of(effect), f"made and not owed: {tuple(effect)}"))
    return problems


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


def labels_put_back(before, after, record, actual):
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
                               for c in record.calls)
        if not put_back_refused and after.issues[number].labels != before.issues[number].labels:
            problems.append(Violation("G3L", f"#{number}: the release failed and its labels were not put back"))
    return problems


def nothing_lost(before, after, actual):
    """G4."""
    problems = []
    lost = before.reachable() - after.reachable()
    if lost:
        problems.append(Violation("G4", f"the run made {len(lost)} commits unreachable"))
    for effect in actual:
        if effect.kind == "delete-ref":
            number, sha = oracle.claim_number(effect.target), before.refs.get(effect.target, "")
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
    """Every check one step owes: the state's, the command's, the cycle's."""
    problems = state_checks(after) + command_checks(outcome)
    if outcome.run is not None:
        problems += cycle_checks(before, after, outcome.run, known)
    return problems
