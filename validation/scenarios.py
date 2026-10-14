"""The five scenarios of DESIGN.md section 5.2, as tables of steps.

A scenario is a seeded repository, the workers and people in it, and the
steps of section 5.2 in order: who does what at which hour, and what the
spec says the state is afterwards. run.run_scenario plays one and checks
every guarantee on every step; the expectations here name structure (who
holds what, which markers were posted, which rows the board lists), never
a sentence, so a wording changed on purpose moves only the page's parsers
(page.py). Times are hours from model.T0, as the design's tables count.

A step is one verb of verbs.py by one actor; "act" lets the actor's
scripted worker (policies.py) decide from the page alone until it has
nothing to do, so the scenario shows what a worker that reads only
STATE.md does; "look" changes nothing and checks what a worker would pick;
"guard" asks the push guard about a command the actor might run. A step
that names a finding (shows) is red for it with that finding's switch
off in the oracle (oracle.AS_BUILT), for the findings the oracle reads
as built; S01c's careless lease showed F7 as built until the guard's
fence refused it, and a step red as built would be recorded the same
way. known.json records each such step with its guarantee
(known.py, since build step 5), and tests/test_validation_traces.py holds
the two to each other.
"""

from typing import NamedTuple

from . import cycle, policies
from .model import OWNER
from .verbs import SAM

SHARED = ("src/shared.py",)


class Expect(NamedTuple):
    """What the spec says the state is after a step; a field left None is
    not checked. Counts of the board's comments are by target and kind
    (oracle.comment_kind) for comments by the bot alone; markers counts
    release markers on an issue by any author; told maps a number to the
    numbers the bot's overlap markers on it name; mentions names phrases the
    bot's latest comment on a target must hold, a date, a number or who held
    the claim, each long enough that the prose cannot hold it by chance;
    released counts the released/<n>-* branches of an issue; picks is the
    issue each worker's scripted self would take from the page it holds."""

    taken: bool | None = None
    queue: list | None = None
    given: dict | None = None
    claims: dict | None = None
    not_claims: list | None = None
    holders: dict | None = None
    pulls: list | None = None
    overlaps: dict | None = None
    labels: dict | None = None
    comments: dict | None = None
    markers: dict | None = None
    told: dict | None = None
    mentions: dict | None = None
    refs: dict | None = None
    released: dict | None = None
    merged: list | None = None
    standing: dict | None = None
    no_writes: bool | None = None
    picks: dict | None = None
    refused: bool | None = None
    kept: dict | None = None


class Step(NamedTuple):
    at: int
    actor: str
    verb: str
    args: tuple = ()
    expect: Expect = Expect()
    shows: str = ""


class Scenario(NamedTuple):
    """One scenario: its issues with their labels, its workers with the
    policy each acts by (None for one driven by explicit steps alone), the
    people beside them, the files each issue's claim lists, whether the
    workers may be careless, and its steps. guarantees names the ones the
    scenario was written to exercise; every guarantee is checked on every
    step regardless."""

    name: str
    title: str
    guarantees: tuple
    issues: dict
    workers: dict
    steps: tuple
    people: tuple = (OWNER,)
    files: tuple = ()
    careless: bool = False
    events: tuple = (cycle.SCHEDULE,)
    split_board: bool = False


def board(at, event=cycle.SCHEDULE, ref_name="", **expect):
    shows = expect.pop("shows", "")
    return Step(at, "board", "board", (event, ref_name), Expect(**expect), shows)


def step(at, actor, verb, *args, shows="", **expect):
    return Step(at, actor, verb, tuple(args), Expect(**expect), shows)


def announce(at, worker, number):
    """The Announce steps as explicit verbs: claimed, the worker's label,
    the plan comment."""
    return (step(at, worker, "label-on", number, "claimed"), step(at, worker, "label-on", number, f"worker:{worker}"),
            step(at, worker, "plan", number))


HELD = {"todo", "claimed"}


def labelled(worker, *more):
    return HELD | {f"worker:{worker}"} | set(more)


# --- S01: two workers race for one issue --------------------------------------

def s01(name="S01", third=("todo", "worker:c"), careless=False):
    steps = (
        board(0, queue=[1, 2], given={3: ["c"]}, claims={}),
        step(0, "a", "look", picks={"a": 1, "b": 1}),
        step(0, "a", "claim", 1, taken=True, holders={1: "a"}),
        step(0, "b", "claim", 1, taken=False, holders={1: "a"}),
    )
    if careless:
        steps += (step(0, "b", "lease-onto", 1, taken=False, holders={1: "a"}),
                  board(0, claims={"claude/1": "a"}))
        return Scenario(name, "a careless lease after a fetch is refused by the guard", ("G1", "G11a"),
                        {1: ("todo",), 2: ("todo",), 3: third}, {"a": None, "b": None, "c": None}, steps,
                        careless=True)
    steps += (
        step(0, "b", "look", picks={"b": 1}),
        step(0, "b", "claim", 2, taken=True, holders={1: "a", 2: "b"}),
        step(0, "c", "look", picks={"c": 3}),
        step(0, "c", "claim", 3, taken=True, holders={3: "c"}),
    )
    steps += announce(0, "a", 1) + announce(0, "b", 2) + announce(0, "c", 3)
    steps += (
        step(0, "c", "look", labels={1: labelled("a"), 2: labelled("b"), 3: labelled("c") | set(third)},
             picks={"a": 1}),
        board(0, cycle.CREATE, "claude/3", claims={"claude/1": "a", "claude/2": "b", "claude/3": "c"}, queue=[],
              given={}, no_writes=True),
        step(0, "a", "look", picks={"a": None}),
    )
    return Scenario(name, "two workers race for one issue, and a worker's label names its issue",
                    ("G1", "G8", "G9", "G11a"), {1: ("todo",), 2: ("todo",), 3: third},
                    {"a": None, "b": None, "c": None}, steps)


# --- S02: an idle holder is released, the work kept, the issue claimed again --

def s02(name="S02", first=("todo",)):
    given = {1: ["a"]} if len(first) > 1 else {}
    steps = (
        board(0, queue=[] if given else [1], given=given),
        step(0, "a", "act", labels={1: labelled("a") | set(first)}, pulls=[100], holders={1: "a"}),
        step(1, "a", "commit", 1, taken=True),
        board(1, cycle.PULL_REQUEST, claims={"claude/1": "a"}, pulls=[100], queue=[]),
        board(24, no_writes=True),
        board(25, no_writes=True),
        board(26, comments={1: {"claim-released": 1, "released": 1}, 100: {}}, labels={1: {"todo"}},
              refs={"claude/1": False}, released={1: 1}, pulls=[], queue=[1],
              mentions={1: {"claim-released": ["held by a", "2026-10-01T01:00:00Z", "#100"]}}),
        board(27, no_writes=True),
        step(27, "c", "claim", 1, taken=True, holders={1: "c"}),
        step(27, "c", "carry-on", 1, ("released", 1), taken=True, kept={1: "src/1.py"}),
    ) + announce(27, "c", 1) + (
        step(27, "c", "look", labels={1: labelled("c")}),
        board(27, claims={"claude/1": "c"}, comments={1: {"claim-released": 1, "released": 1}}),
    )
    return Scenario(name, "an idle claim is released once after a day, goes back to the queue, its work is kept, "
                    "and someone else claims it", ("G2", "G3", "G4", "G6", "G9", "G10"), {1: first},
                    {"a": policies.IdleHolder(), "c": None}, steps)


# --- S03: a person releases by hand, a stranger tries, the holder comes back --

RELEASED_1 = ("released", 1)
S03 = Scenario(
    "S03", "a person releases too soon, a stranger tries, the holder comes back, and the label undoes a release",
    ("G2", "G3", "G5"), {1: ("todo",)},
    {"a": policies.IdleHolder(), "b": policies.IdleHolder(returns_at=58), SAM: policies.Stranger()},
    (
        board(0),
        step(0, "a", "act", labels={1: labelled("a")}, pulls=[100]),
        step(10, OWNER, "marker", 1),
        step(10, OWNER, "label-off", 1, "claimed"),
        step(10, OWNER, "label-off", 1, "worker:a", labels={1: {"todo"}}),
        board(10, claims={"claude/1": "a"}, queue=[], comments={1: {}}, markers={1: 1}, refs={"claude/1": True}),
        step(30, SAM, "act", markers={1: 2}),
        board(30, comments={1: {"claim-released": 1, "released": 1}}, refs={"claude/1": False}, released={1: 1},
              queue=[1], pulls=[]),
        step(31, "b", "act", labels={1: labelled("b")}, pulls=[101], holders={1: "b"}),
        step(58, OWNER, "marker", 1),
        step(58, OWNER, "label-off", 1, "claimed"),
        step(58, OWNER, "label-off", 1, "worker:b", labels={1: {"todo"}}),
        step(58, "b", "act", taken=True),
        board(58, claims={"claude/1": "b"}, queue=[], refs={"claude/1": True},
              comments={1: {"claim-released": 1, "released": 1}}),
        board(85, comments={1: {"claim-released": 2, "released": 2}}, released={1: 2}, refs={"claude/1": False},
              pulls=[], queue=[1]),
        step(85, OWNER, "label-on", 1, "claimed"),
        step(85, OWNER, "rename-back", 1, RELEASED_1, refs={"claude/1": True}),
        step(85, OWNER, "reopen", 101, pulls=[101]),
        board(85, claims={"claude/1": "b"}, queue=[], comments={1: {"claim-released": 2, "released": 2}}),
        board(120, comments={1: {"claim-released": 2, "released": 2}}, markers={1: 5}, holders={1: "b"},
              claims={"claude/1": "b"}),
    ),
    people=(OWNER, SAM),
)


# --- S04: a finished draft waits; an abandoned draft is released --------------

S04 = Scenario(
    "S04", "a finished draft waits for review however long it takes; an abandoned draft is released",
    ("G6", "G2", "G3", "G10", "G12"), {1: ("todo",), 2: ("todo",)},
    {"f": policies.Finisher(), "d": policies.Abandoner(draft=True)},
    (
        board(0),
        step(0, "f", "act", labels={1: labelled("f")}, pulls=[100], holders={1: "f"}),
        board(0, cycle.CREATE, "claude/1", queue=[2], claims={"claude/1": "f"}),
        step(0, "d", "act", labels={2: {"todo"}}, pulls=[100, 101], holders={2: "d"}),
        step(1, "f", "act", comments={100: {}}, kept={1: "src/1.py"}),
        board(26, released={2: 1}, refs={"claude/1": True, "claude/2": False},
              comments={1: {}, 2: {"claim-released": 1, "released": 1}, 100: {"idle": 1}}, pulls=[100], queue=[2]),
        board(50, no_writes=True),
        board(74, no_writes=True),
        board(98, no_writes=True),
        step(99, OWNER, "drop-claim-file", 1, taken=True),
        step(99, OWNER, "mark-ready", 100),
        step(99, OWNER, "label-on", 1, "in-review", labels={1: labelled("f", "in-review")}),
        board(200, claims={"claude/1": "f"}, pulls=[100], comments={1: {}, 100: {"idle": 1}}),
        step(201, OWNER, "merge", 100, refs={"claude/1": False}),
        board(201, claims={}, merged=[100], queue=[2], pulls=[]),
    ),
)


# --- S05: overlaps, main moved, and main brought in ---------------------------

def s05(name="S05", how="rebase", split=False):
    bringer = policies.MainBringer(how, k=1)
    told_once = {101: {"overlap": 1, "main-moved": 1}}
    after_merge = (board(3, cycle.PUSH, "main", comments=told_once, standing={101: "dirty"},
                         mentions={101: {"main-moved": ["conflicting"]}}),
                   board(3, cycle.PULL_REQUEST, comments=told_once))
    if split:
        # The merge's four events fire at once; the delete's run starts and
        # the pull request's replaces the push's, which waited (F12). Since
        # the fix the delete's run tells #101, the word depending on the
        # state and not on the event, and the pull request's run owes no
        # second word for the same commit of main.
        after_merge = (board(3, cycle.DELETE, "main", comments=told_once, standing={101: "dirty"},
                             mentions={101: {"main-moved": ["conflicting"]}}),
                       board(3, cycle.PULL_REQUEST, comments=told_once))
    steps = (
        board(0),
        step(0, "m", "claim", 1, taken=True),
        step(0, "r", "claim", 2, taken=True),
    ) + announce(0, "m", 1) + announce(0, "r", 2) + (
        board(0, overlaps={"src/shared.py": [1, 2]}, comments={1: {"overlap": 1}, 2: {"overlap": 1}},
              told={1: {2}, 2: {1}}),
        step(1, "m", "open-draft", 1, pulls=[100]),
        step(1, "r", "open-draft", 2, pulls=[100, 101]),
        board(1, cycle.PULL_REQUEST, overlaps={"src/shared.py": [100, 101]},
              comments={100: {"overlap": 1}, 101: {"overlap": 1}}, shows="F2"),
        step(2, "m", "commit", 1, taken=True),
        step(2, "m", "drop-claim-file", 1, taken=True),
        step(2, "m", "mark-ready", 100),
        step(2, "m", "label-on", 1, "in-review"),
        step(2, "r", "commit", 2, taken=True),
        step(2, "r", "drop-claim-file", 2, taken=True),
        step(2, "r", "mark-ready", 101),
        step(2, "r", "label-on", 2, "in-review"),
        board(2, cycle.PULL_REQUEST, overlaps={"src/shared.py": [100, 101]},
              comments={100: {"overlap": 1}, 101: {"overlap": 1}}),
        step(3, OWNER, "merge", 100, refs={"claude/1": False}, merged=[]),
    ) + after_merge + (
        step(4, "r", "guard", "git push --force origin claude/2", refused=True),
        step(4, "r", "guard", "git push origin +claude/2", refused=True),
        step(4, "r", "guard", "git push --force-with-lease origin claude/2", refused=False),
        step(4, "r", "guard", "git push origin claude/2", refused=False),
        step(4, "r", "act", taken=True, standing={101: "clean"}),
        board(4, cycle.PULL_REQUEST, overlaps={}, comments=told_once),
        step(5, OWNER, "merge", 101),
        board(5, cycle.PUSH, "main", merged=[101, 100], claims={}, queue=[], pulls=[]),
    )
    return Scenario(name, "overlapping work is told once and lands in order; main moved is told once; main is "
                    "brought in by rebase or by merge", ("G7", "G10", "G11a", "G13"), {1: ("todo",), 2: ("todo",)},
                    {"m": None, "r": bringer}, steps, files=((1, SHARED), (2, SHARED)),
                    events=cycle.EVENTS if split else (cycle.SCHEDULE,))


SCENARIOS = {
    "S01": s01(),
    "S01l-account": s01("S01l-account", ("todo", "account:c")),
    "S01l-agent": s01("S01l-agent", ("todo", "agent:c")),
    "S01c": s01("S01c", careless=True),
    "S02": s02(),
    "S02l": s02("S02l", ("todo", "account:a", "agent:a")),
    "S03": S03,
    "S04": S04,
    "S05a": s05("S05a", "rebase"),
    "S05b": s05("S05b", "merge"),
    "S05q": s05("S05q", "rebase", split=True),
}
