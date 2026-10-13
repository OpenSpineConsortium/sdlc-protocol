"""The mutation lane (DESIGN.md, section 7, and step 2 of section 14).

A guarantee the harness never sees break is not shown to hold, so the
harness is tested the way it tests the library. A mutant changes one rule
of the library inside this process, by patching one attribute for as long
as the mutant is on, never in the files, the way a careless change could.
The lane runs every mutant against the searches below and requires every
one to be caught: some search must report a violation under the mutant
that it does not report on the library as it stands.

The searches are breadth first (explore.explore), each from a short
prefix: a few verbs that put the repository where a family of rules
applies (a held claim, a draft, two claims on one file), then every
interleaving of the scope's verbs to a small depth. A prefix holds no
board run of its own, so it reaches the same state with or without a
mutant, and every step after it is searched, not chosen. The hand-written
traces in tests/test_validation_mutants.py are each mutant's shortest
example, kept so a failure names the rule that broke; the lane is what
shows the searches bite.

`python3 -m validation.mutants` runs the lane and prints one line per
mutant, the search that caught it and the first violation it found.
"""

import contextlib
import sys
import time
from datetime import timedelta
from typing import NamedTuple
from unittest import mock

from sdlc_protocol import board, claims, coordinator, guard
from sdlc_protocol.adapters import github

from . import cycle, explore, invariants, model as M, oracle, verbs
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


class Search(NamedTuple):
    """One breadth-first search: the verbs that bring the seeded repository
    to its start, the scope searched from there and how deep, and the
    guarantees the library as it stands is known to break in it."""

    name: str
    prefix: tuple
    scope: Scope
    depth: int
    issues: tuple = (1,)
    known: tuple = ()


# --- the replacements ---------------------------------------------------------

ORIGINAL_IS_IDLE = claims.is_idle
ORIGINAL_LABELS_RELEASED = coordinator.labels_released
ORIGINAL_IS_RELEASED = board.is_released
ORIGINAL_COMMENTS_DUE = board.comments_due
ORIGINAL_OVERLAPS_DUE = coordinator.overlap_comments_due
ORIGINAL_MISNAMED_CLAIM = guard.misnamed_claim


def comment_then_labels(api, snap, key, now):
    """release_one with the spec's order reversed, as cf3bdb0 had it (F1)."""
    release = snap.releases[key]
    text = coordinator.release_text(snap, key, github.holding(api, snap.default_branch, release.sha))
    api.post(f"/repos/{api.repo}/issues/{key}/comments", {"body": text})
    names = coordinator.labels_released(snap, key)
    for name in names:
        github.take_label_off(api, key, name)
    coordinator.after_release(snap, key, now.isoformat(), names)
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


def main_moved_on_any_event(snapshot, event_name, ref_name, now, idle_after=claims.IDLE_AFTER):
    """board.comments_due as if every run had been started by a push to main."""
    snap = board.as_snapshot(snapshot)
    return ORIGINAL_COMMENTS_DUE(snap, "push", snap.default_branch, now, idle_after)


def any_line_is_a_marker(comment):
    """claims.starts_with_release_marker reading any comment as a release."""
    return bool(((comment or {}).get("body") or "").strip())


def nobody_vouched_for(api):
    """github.may_write as if GitHub refused every permission asked."""
    return lambda login: False


def number_with_any_zeros(key):
    """claims.number_in taking leading zeros, as before 0.4.1 (F5)."""
    text = str(key)
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 9 else None


MUTANTS = {
    "idle at exactly 24 hours": Mutant(
        claims, "is_idle", lambda last, now, idle_after=claims.IDLE_AFTER: now - claims.parse_time(last) >= idle_after,
        "G2", "spec, Release: idle is more than 24 hours, so a claim at exactly 24 is not yet due"),
    "a 12-hour idle time": Mutant(
        claims, "is_idle", lambda last, now, idle_after=claims.IDLE_AFTER:
        ORIGINAL_IS_IDLE(last, now, timedelta(hours=12)), "G2", "the idle time is the spec's 24 hours"),
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
    "main moved told after any event": Mutant(
        board, "comments_due", main_moved_on_any_event,
        "G10", "main moved is told after a push to the default branch"),
    "the rename skipped": Mutant(
        github, "release_claims", lambda api, snapshot: [],
        "G3", "a released claim's branch is renamed, so the name is free"),
    "overlaps told on every run": Mutant(
        coordinator, "overlap_comments_due", lambda snapshot, told: ORIGINAL_OVERLAPS_DUE(snapshot, {}),
        "G7", "overlapping work is told once"),
    "the labels not put back": Mutant(
        github, "put_back", lambda api, key, names: "",
        "G3L", "a run that fails between the two puts back the labels it took off"),
    "a lost comment's release not seen": Mutant(
        github, "landed_release", lambda api, key, release: "",
        "G3L", "a comment GitHub wrote and lost its answer to makes the release whole"),
    "a writer's release not vouched for": Mutant(
        github, "may_write", nobody_vouched_for,
        "G5", "anyone who may write may release, whatever author_association GitHub shows"),
    "a claim number with leading zeros": Mutant(
        claims, "number_in", number_with_any_zeros,
        "G1", "claude/01 is no claim on #1 (F5)"),
    "the guard passes a misnamed claim": Mutant(
        guard, "misnamed_claim", lambda name: None,
        "G1", "the push guard refuses a name that reads as a claim and claims nothing"),
}


# --- the searches -------------------------------------------------------------

CLAIM = Verb("claim", "a", (1,))
CLAIMED = Verb("label-on", "a", (1, "claimed"))
WORKER = Verb("label-on", "a", (1, "worker:a"))
SHARED = ("src/shared.py",)

# Cheapest first, since a mutant is judged by the first search that
# catches it. The careless search meets F7 on the library itself: a lease
# after a fetch takes another's claim (G1), which no board rule answers.
SEARCHES = (
    Search("a released claim under every fault", (CLAIM, CLAIMED, WORKER, Verb("wait", "clock", (25,))),
           Scope(workers=("a",), people=(), waits=(25,), faults=tuple(verbs.FAULTS)), 2),
    Search("careless workers on a held claim", (CLAIM, CLAIMED),
           Scope(workers=("a", "b"), people=(M.OWNER,), waits=(25,), careless=True), 3, known=("G1",)),
    Search("two claims on one file", (CLAIM, Verb("claim", "b", (2,))),
           Scope(workers=("a", "b"), issues=(1, 2), people=(), waits=(25,),
                 files=((1, SHARED), (2, SHARED))), 3, issues=(1, 2)),
    Search("a held claim, the clock and a release by hand", (CLAIM, CLAIMED, WORKER),
           Scope(workers=("a",), people=(M.OWNER, DANA, SAM), waits=(24, 25)), 4),
)


# --- running ------------------------------------------------------------------

def start(search):
    """The search's starting state, with the violations met on its prefix."""
    model = M.seeded(search.issues, writers=("a", "b", "c", CAROL, DANA))
    cycle.board_run(model)
    found = []
    for step in search.prefix:
        before = model.clone()
        outcome = verbs.apply(model, step, search.scope)
        found += invariants.step_checks(before, model, step, outcome)
    return model, found


def violations(search):
    """Every distinct violation the search meets, with its first trace."""
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


def judge(name, baselines, searches=SEARCHES):
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


def baselines(searches=SEARCHES):
    """What each search meets on the library as it stands; the lane
    expects nothing, since the open findings are read as built."""
    return {search.name: violations(search) for search in searches}


def surprises(plain, searches=SEARCHES):
    """By search, what the library itself breaks beyond the search's known
    findings: a search that is red on the library cannot tell a mutant."""
    found = {}
    for search in searches:
        unknown = sorted(key for key in plain[search.name] if key[0] not in search.known)
        if unknown:
            found[search.name] = unknown
    return found


def run_lane(names=None, searches=SEARCHES):
    plain = baselines(searches)
    return plain, [judge(name, plain, searches) for name in (names or MUTANTS)]


def main(argv=None):
    names = (argv if argv is not None else sys.argv[1:]) or None
    plain, verdicts = run_lane(names)
    status = 0
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
