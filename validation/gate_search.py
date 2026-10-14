"""The gate's lane (DESIGN.md, 3.5, 5.2's S05g, 6.8's E-gate): G13.

The merge gate (sdlc_protocol.gate) decides over a World; this lane runs
every interleaving of two agents' claims, pushes, pull requests, marks and
landings, with main moving and the check changing, over MemoryWorld,
under the two presets, the coordbench arm's policy and a gated check, to
a depth, and checks the gate's guarantee (G13's eight parts, DESIGN.md
2.3) after every act: a pull request is its claimant's and an issue is
closed exactly when its pull request merged (1); a mark that goes through
is its author's and releases the claim (2); a merge comes from
mark_ready where the gate merges and from a landing where the owner or
auto-merge does (3); never without a green required check (4); only of a
branch standing clean or behind as the act found it, and a main moved
answer only for one standing in conflict (5); a main moved answer moves
nothing, and a refusal moves nothing (6); over git, main only moves
forward, its new commit a descendant of the old (7), which MemoryWorld,
whose commits have no ancestry, cannot show; a refusal carries the exit
code of the kind the state owes, another's work or a plain refusal (8);
and no branch merges twice. The lane offers no push of a branch its
agent did not claim and no landing where the gate merges on mark_ready:
those are the lane's own exclusions (offered), not refusals of the gate's,
so they are neither judged by part 8 nor counted. The prototype of it is
validation/prototype/gate_explore.py.

Over BareGitWorld, where every merge is git's own, the same acts run as
sequences rather than a search, since a real repository cannot be copied
per state: the S05g scenario of DESIGN.md 5.2, the arm's gate over two
branches that change one line, and seeded random sequences of the acts
under each policy, a bare repository per sequence, with the same checks
after every act. A push that stands clean adds to a file of the agent's
own; one that conflicts rewrites app.py's second line, which the owner's
move of main rewrites too. A sequence is drawn over MemoryWorld, which
declares the conflict at the push, while git finds it only where main
changed that line after the branch began, and finds one where an agent's
clean pushes on two issues each add its own file; the checks read git's
own standing, so each act is judged by what git found.
"""

import copy
import os
import random
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import NamedTuple

from sdlc_protocol import gate
from sdlc_protocol.adapters.bare_git import BareGitWorld
from sdlc_protocol.adapters.memory import MemoryWorld

from .git_store import ENV, configure, settings_arguments

POLICIES = {
    "account": gate.ACCOUNT_POLICY,
    "team": gate.TEAM_POLICY,
    "arm": gate.Policy(bring_main_in=gate.MERGE, who_merges=gate.GATE, required_check="", remote="team"),
    "gated-check": gate.Policy(who_merges=gate.GATE),
}
BASE_TEXT = "def greet():\n    return 'hello'\n\n\ndef part():\n    return 'base'\n"
# The code each of the gate's answers exits with (part 8): an answer's
# code is its result's, as a refusal's is its kind's.
ANSWER_CODES = {gate.MERGED: gate.EXIT_DONE, gate.READY: gate.EXIT_DONE, gate.MAIN_MOVED: gate.EXIT_MAIN_MOVED,
                gate.NOT_MERGED: gate.EXIT_REFUSED, gate.GATE_FAILED: gate.EXIT_GATE_FAILED}


class Act(NamedTuple):
    kind: str
    agent: str = ""
    issue: int = 0
    word: str = ""  # a push's standing, or a check's state

    def __str__(self):
        return " ".join(str(p) for p in (self.kind, self.agent, self.issue, self.word) if p)


def actions(agents, issues, checks=True):
    """Every act of the lane: main moves; for each issue and agent a claim,
    a pull request opened, a push that stands clean or conflicting and a
    mark; and for each issue a landing and the check's three states."""
    acts = [Act("move_main")]
    for n in issues:
        for a in agents:
            acts += [Act("claim", a, n), Act("open", a, n), Act("ready", a, n),
                     Act("push", a, n, gate.CLEAN), Act("push", a, n, gate.CONFLICTING)]
        acts.append(Act("land", issue=n))
        if checks:
            acts += [Act("check", issue=n, word=s) for s in (gate.GREEN, gate.RED, gate.PENDING)]
    return acts


class Step(NamedTuple):
    """What one act did: the outcome or the error, main before and after."""

    act: Act
    outcome: object
    error: object
    main_before: str
    main_after: str


class NotOffered(Exception):
    """An act the lane does not make from this state: the lane's own
    exclusion, not a refusal of the gate's, so it is neither judged by
    part 8 nor counted among the gate's refusals."""


def offered(policy, queue, act):
    """Whether the lane makes the act from this state: an agent pushes
    only the branch it claimed, since a push is git's and not the gate's
    to refuse, and nobody lands where the gate merges on mark_ready, since
    the owner's and auto-merge's act does not come under that policy."""
    if act.kind == "push":
        return queue.issues[act.issue].claimant == act.agent
    if act.kind == "land":
        return policy.who_merges != gate.GATE
    return True


def anothers(queue, act):
    """Whether the act asks for work another agent holds, read from the
    state before the act, which the gate refuses with the code of its
    own (EXIT_SOMEONE_ELSES), in the order the gate reads the state: a
    claim on an issue given to another when the queue is closed, or
    claimed by another while open; a pull request opened on another's
    claim; a mark by anyone but the pull request's author. Every other
    refusal is the plain one."""
    if act.kind == "claim":
        issue = queue.issues[act.issue]
        if not queue.open_queue and issue.given_to != act.agent:
            return True
        return not issue.closed and bool(issue.claimant) and issue.claimant != act.agent
    if act.kind == "open":
        claimant = queue.issues[act.issue].claimant
        return bool(claimant) and claimant != act.agent
    if act.kind == "ready":
        pull = queue.pulls.get(act.issue)
        return pull is not None and pull.author != act.agent
    return False


def apply(policy, world, queue, act, pusher):
    """One act over a world, with pusher making the push: the step. An
    act the lane does not offer from this state raises NotOffered."""
    if not offered(policy, queue, act):
        raise NotOffered(str(act))
    main_before = world.main_sha()
    outcome, error = None, None
    try:
        if act.kind == "move_main":
            pusher.move_main(world)
        elif act.kind == "claim":
            gate.claim(policy, queue, act.issue, act.agent)
        elif act.kind == "push":
            pusher.push(world, policy.branch_of(act.issue), act.agent, act.word)
        elif act.kind == "open":
            gate.open_pull(policy, world, queue, act.issue, act.agent)
        elif act.kind == "ready":
            outcome = gate.mark_ready(policy, world, queue, act.issue, act.agent)
        elif act.kind == "land":
            outcome = gate.land(policy, world, queue, act.issue)
        elif act.kind == "check":
            pusher.set_check(world, policy.branch_of(act.issue), act.word)
    except gate.GateError as caught:
        error = caught
    return Step(act, outcome, error, main_before, world.main_sha())


def problems(policy, queue, step, merged_before):
    """G13 after one act; merged_before counts each branch's landings so
    far, before this act. Part 8's expected code comes from the state the
    act found (step.anothers), not from the error raised, whose class and
    code always agree: a gate that refused another's claim with the plain
    code would otherwise pass. An answer's code is its result's
    (ANSWER_CODES), and an attempt on a branch standing in conflict
    answers main moved, whose code is 3: a gate that answered a second
    main moved with 0 would otherwise pass. An act on another's work that
    goes through breaks part 1 or 2 at once, before any pull request
    shows it."""
    act, outcome, error = step.act, step.outcome, step.error
    moved = step.main_after != step.main_before
    found = []
    if error is not None:
        expected = gate.EXIT_SOMEONE_ELSES if step.anothers else gate.EXIT_REFUSED
        if error.exit_code != expected:
            owed = "another holds the work" if step.anothers else "the act is refused"
            found.append(f"exit code {error.exit_code} for {type(error).__name__} where {owed}, which owes {expected}")
        if act.kind not in ("move_main", "push") and moved:
            found.append("a refusal moved main")
    elif step.anothers:
        found.append(f"{act} went through on another's work")
    if outcome is not None:
        owed = ANSWER_CODES.get(outcome.result)
        if outcome.exit_code != owed:
            found.append(f"exit code {outcome.exit_code} for the answer {outcome.result}, which owes {owed}")
        attempted = act.kind == "land" or policy.who_merges == gate.GATE
        if attempted and step.standing_at_landing == gate.CONFLICTING and outcome.result != gate.MAIN_MOVED:
            found.append(f"answer {outcome.result} for a branch standing conflicting, which owes main moved")
        if outcome.merged != moved:
            found.append(f"outcome {outcome.result} but main {'moved' if moved else 'stayed'}")
        if outcome.result == gate.MAIN_MOVED and moved:
            found.append("main moved answer, yet main moved")
        if outcome.merged and (act.kind == "ready") != (policy.who_merges == gate.GATE):
            found.append(f"merged by {act.kind} where {policy.who_merges} merges")
        if outcome.merged and policy.required_check and step.check_at_landing != gate.GREEN:
            found.append("merged without a green required check")
        if outcome.merged and step.standing_at_landing not in (gate.CLEAN, gate.BEHIND):
            found.append(f"merged a branch standing {step.standing_at_landing}")
        if outcome.result == gate.MAIN_MOVED and step.standing_at_landing != gate.CONFLICTING:
            found.append(f"main moved answer for a branch standing {step.standing_at_landing}")
        if outcome.merged and merged_before.get(policy.branch_of(outcome.issue)):
            found.append(f"{policy.branch_of(outcome.issue)} merged twice")
    if act.kind == "ready" and error is None:
        pull = queue.pulls.get(act.issue)
        if pull is None or pull.author != act.agent:
            found.append(f"#{act.issue} marked ready by {act.agent}, not its author")
        if not queue.issues[act.issue].claim_released:
            found.append(f"#{act.issue} marked ready with its claim held")
    for n, issue in queue.issues.items():
        pull = queue.pulls.get(n)
        if issue.closed != (pull is not None and pull.state == gate.MERGED):
            found.append(f"#{n} closed is {issue.closed} but its pull request is {pull.state if pull else 'none'}")
        if pull is not None and pull.author != issue.claimant:
            found.append(f"#{n}'s pull request is {pull.author}'s, the claim {issue.claimant}'s")
    return found


# --- the memory world ---------------------------------------------------------

class World(MemoryWorld):
    def __deepcopy__(self, memo):
        other = World.__new__(World)
        for name, value in self.__dict__.items():
            setattr(other, name, value if name == "lock" else copy.deepcopy(value, memo))
        return other


class MemoryPusher:
    """The acts the world itself makes in memory."""

    def move_main(self, world):
        world.move_main()

    def push(self, world, branch, agent, word):
        world.push(branch, word, ("app.py",) if word == gate.CONFLICTING else ())

    def set_check(self, world, branch, state):
        world.set_check(branch, state)


def check_before(world, policy, queue, act):
    """The required check's state and the branch's standing as the act
    found them, for the landing rules, read before the act moves
    anything, none for an act that lands nothing; and whether the act
    asks for another's work, for part 8."""
    found = (None, None)
    if act.kind in ("ready", "land"):
        branch = policy.branch_of(act.issue)
        found = (world.check(branch), world.standing(branch))
    return found + (anothers(queue, act),)


class StepWithCheck(NamedTuple):
    """A step with the required check's state and the branch's standing
    as the landing found them, which the landing rules read (problems),
    and whether the act asked for another's work, which part 8 reads."""

    act: Act
    outcome: object
    error: object
    main_before: str
    main_after: str
    check_at_landing: object
    standing_at_landing: object = None
    anothers: bool = False


def forward(repository, step):
    """G13's part 7 over git: main, when the act moved it, moved to a
    descendant of where it was."""
    if not step.main_before or step.main_after == step.main_before:
        return []
    done = subprocess.run(["git", "merge-base", "--is-ancestor", step.main_before, step.main_after], cwd=repository,
                          capture_output=True, text=True, env=ENV, timeout=120)
    if done.returncode != 0:
        return [f"main moved from {step.main_before[:7]} to {step.main_after[:7]}, not forward"]
    return []


def key(world, queue):
    return (world.main_commit, tuple(sorted(world.branches.items())), tuple(sorted(world.words.items())),
            tuple(sorted(world.checks.items())),
            tuple((i.number, i.claimant, i.claim_released, i.closed) for i in queue.issues.values()),
            tuple((p.issue, p.author, p.state, p.attempts) for p in queue.pulls.values()))


class Exploration(NamedTuple):
    policy: str
    states: int
    transitions: int
    depth: int
    seconds: float
    violations: tuple  # (trace of acts as strings, problems)
    stopped: bool = False  # True when the budget ended it before it was done


def explore(name, depth, agents=("a", "b"), issues=(1, 2), budget=None):
    """Every interleaving of the acts to depth over MemoryWorld under the
    policy, breadth first, keyed by the world and the queue; stopped, and
    so not a check of its space, when the budget runs out."""
    policy, pusher = POLICIES[name], MemoryPusher()
    world, queue = World(), gate.Queue({n: "a" for n in issues}, open_queue=True)
    seen, frontier = {key(world, queue)}, deque([(world, queue, (), {})])
    violations, transitions, began = [], 0, time.perf_counter()
    acts = actions(agents, issues)
    stopped = False
    while frontier:
        if budget is not None and time.perf_counter() - began > budget:
            stopped = True
            break
        world, queue, trace, merged = frontier.popleft()
        if len(trace) >= depth:
            continue
        for act in acts:
            if not offered(policy, queue, act):
                continue
            next_world, next_queue = copy.deepcopy(world), copy.deepcopy(queue)
            check = check_before(next_world, policy, next_queue, act)
            step = StepWithCheck(*apply(policy, next_world, next_queue, act, pusher), *check)
            transitions += 1
            found = problems(policy, next_queue, step, merged)
            next_merged = dict(merged)
            if step.outcome is not None and step.outcome.merged:
                branch = policy.branch_of(step.outcome.issue)
                next_merged[branch] = next_merged.get(branch, 0) + 1
            if found:
                violations.append(([str(a) for a in trace + (act,)], found))
            k = key(next_world, next_queue)
            if k not in seen:
                seen.add(k)
                frontier.append((next_world, next_queue, trace + (act,), next_merged))
    return Exploration(name, len(seen), transitions, depth, time.perf_counter() - began, tuple(violations), stopped)


# --- the git world --------------------------------------------------------------

def git(cwd, *args, check=True, actor="agent"):
    done = subprocess.run(["git", *settings_arguments(), "-c", f"user.name={actor}", "-c",
                           f"user.email={actor}@example.invalid", *args], cwd=cwd, capture_output=True, text=True,
                          env=ENV, timeout=120)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


class SharedRemote:
    """A bare repository seeded with one commit on main and a clone per
    agent with the bare repository as its remote `team`, as
    tests/test_bare_git.py builds them. Each repository is told
    git_store.SETTINGS in its own config, as the git world's are, since
    git hands the receive-pack it starts for a local remote none of the
    settings the pushing side carries, and git's automatic maintenance,
    detached, would otherwise still write into the bare repository while
    the folder is removed; the folder's removal ignores what it cannot
    remove for the same reason."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sdlc-gate-lane-", ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.bare = self.root / "team.git"
        git(self.root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.bare))
        configure(self.bare)
        seed = self.root / "seed"
        seed.mkdir()
        git(seed, "init", "--quiet", "--initial-branch=main")
        configure(seed)
        (seed / "app.py").write_text(BASE_TEXT, encoding="utf-8")
        git(seed, "add", "app.py")
        git(seed, "commit", "--quiet", "-m", "base")
        git(seed, "push", "--quiet", str(self.bare), "HEAD:refs/heads/main")
        self.base = git(seed, "rev-parse", "HEAD")
        self.clones = {}
        self.moves = 0

    def clone(self, agent):
        if agent not in self.clones:
            path = self.root / agent
            git(self.root, "clone", "--quiet", "--origin", "team", str(self.bare), str(path), actor=agent)
            configure(path)
            self.clones[agent] = path
        return self.clones[agent]

    def main(self):
        return git(self.bare, "rev-parse", "refs/heads/main")

    def close(self):
        self.tmp.cleanup()


class GitPusher:
    """The acts made with git: a push that stands clean adds a line to the
    agent's own file; one that conflicts rewrites app.py's second line, as
    the owner's move of main does, so the two meet in git once main has
    moved after the branch began. Each push goes
    round the world's hold, which fetches once a hold, so the pusher
    tells the world its next read fetches, as the world does after its
    own merge: else the step would record main after the owner's move as
    it was before it, and forward() would never examine the move."""

    def __init__(self, remote):
        self.remote = remote
        self.pushes = 0

    def pushed(self, world):
        if world is not None:
            world.fresh = False

    def move_main(self, world):
        self.remote.moves += 1
        clone = self.remote.clone("_owner")
        git(clone, "fetch", "--quiet", "team", actor="owner")
        git(clone, "switch", "--quiet", "-C", "main", "team/main", actor="owner")
        (clone / "app.py").write_text(BASE_TEXT.replace("'hello'", f"'moved {self.remote.moves}'"), encoding="utf-8")
        git(clone, "add", "app.py", actor="owner")
        git(clone, "commit", "--quiet", "-m", f"elsewhere {self.remote.moves}", actor="owner")
        git(clone, "push", "--quiet", "team", "HEAD:main", actor="owner")
        self.pushed(world)

    def push(self, world, branch, agent, word):
        self.pushes += 1
        clone = self.remote.clone(agent)
        git(clone, "fetch", "--quiet", "team", actor=agent)
        if git(clone, "rev-parse", "--verify", "--quiet", f"team/{branch}", check=False, actor=agent):
            git(clone, "switch", "--quiet", "-C", branch, f"team/{branch}", actor=agent)
        else:
            git(clone, "switch", "--quiet", "-C", branch, "team/main", actor=agent)
        if word == gate.CONFLICTING:
            (clone / "app.py").write_text(BASE_TEXT.replace("'hello'", f"'{agent} {self.pushes}'"), encoding="utf-8")
            git(clone, "add", "app.py", actor=agent)
        else:
            own = clone / f"{agent}.py"
            own.write_text((own.read_text(encoding="utf-8") if own.exists() else "") + f"PUSH = {self.pushes}\n",
                           encoding="utf-8")
            git(clone, "add", own.name, actor=agent)
        git(clone, "commit", "--quiet", "-m", f"push {self.pushes}", actor=agent)
        git(clone, "push", "--quiet", "team", branch, actor=agent)
        self.pushed(world)

    def set_check(self, world, branch, state):
        pass  # a bare repository runs no checks: the world answers NO_CHECK


GIT_POLICIES = {
    "arm": POLICIES["arm"],
    "owner": gate.Policy(required_check="", remote="team"),
    "auto-merge": gate.Policy(bring_main_in=gate.MERGE, who_merges=gate.AUTO_MERGE, required_check="", remote="team"),
}
WEIGHTS = {"ready": 4, "land": 4, "open": 3, "push": 1, "claim": 2, "move_main": 1}
STRAY = 0.2


def goes_through(policy, world, queue, act):
    """Whether the gate takes the act from this state, tried on copies."""
    return apply(policy, copy.deepcopy(world), copy.deepcopy(queue), act, MemoryPusher()).error is None


def random_sequence(rng, acts, length, policy, issues=(1, 2)):
    """A seeded sequence of acts that mostly go through: at each step the
    acts the gate would take from here, tried over MemoryWorld, are drawn
    from with the later steps of a claim weighted higher, so claims,
    pushes, pull requests and landings follow each other often enough for
    the sequence to reach a merge; one step in five is any act the lane
    offers, so refusals are tried over git too."""
    world, queue, taken = World(), gate.Queue({n: "a" for n in issues}, open_queue=True), []
    for _ in range(length):
        on_offer = [a for a in acts if offered(policy, queue, a)]
        possible = [a for a in on_offer if goes_through(policy, world, queue, a)]
        if rng.random() < STRAY or not possible:
            act = rng.choice(on_offer)
        else:
            act = rng.choices(possible, [WEIGHTS[a.kind] for a in possible])[0]
        apply(policy, world, queue, act, MemoryPusher())
        taken.append(act)
    return taken


def play_over_git(policy, acts_taken, issues=(1, 2)):
    """A sequence of acts under the policy over a fresh bare repository;
    the steps and the problems met."""
    remote = SharedRemote()
    try:
        world = BareGitWorld(str(remote.bare))
        try:
            queue = gate.Queue({n: "a" for n in issues}, open_queue=True, base_ref=remote.base)
            pusher, merged, steps, found = GitPusher(remote), {}, [], []
            for act in acts_taken:
                with world:
                    check = check_before(world, policy, queue, act)
                    step = StepWithCheck(*apply(policy, world, queue, act, pusher), *check)
                steps.append(step)
                found += [(str(act), p) for p in problems(policy, queue, step, merged) + forward(remote.bare, step)]
                if step.outcome is not None and step.outcome.merged:
                    branch = policy.branch_of(step.outcome.issue)
                    merged[branch] = merged.get(branch, 0) + 1
            return steps, found
        finally:
            world.close()
    finally:
        remote.close()


def sequences_over_git(count=40, length=12, seed=0, agents=("a", "b"), issues=(1, 2)):
    """Seeded random sequences over git under each policy of GIT_POLICIES
    in turn: the arm's, the owner merging and auto-merge, each with no
    required check, since a bare repository runs none and the gate merges
    nothing a required check has not passed (the memory lane covers the
    check's three states)."""
    results = {"sequences": 0, "acts": 0, "merges": 0, "main_moved": 0, "refusals": 0, "violations": []}
    began = time.perf_counter()
    acts = actions(agents, issues, checks=False)
    for n in range(count):
        name = list(GIT_POLICIES)[n % len(GIT_POLICIES)]
        rng = random.Random(seed + n)
        taken = random_sequence(rng, acts, length, GIT_POLICIES[name], issues)
        steps, found = play_over_git(GIT_POLICIES[name], taken, issues)
        results["sequences"] += 1
        results["acts"] += len(steps)
        results["merges"] += sum(1 for s in steps if s.outcome is not None and s.outcome.merged)
        results["main_moved"] += sum(1 for s in steps if s.outcome is not None and s.outcome.result == gate.MAIN_MOVED)
        results["refusals"] += sum(1 for s in steps if s.error is not None)
        results["violations"] += [{"policy": name, "seed": seed + n, "sequence": [str(a) for a in taken], "act": act,
                                   "problem": problem} for act, problem in found]
    results["seconds"] = round(time.perf_counter() - began, 1)
    return results


def s05g():
    """S05g (DESIGN.md, 5.2): the gate over BareGitWorld under the arm's
    policy. mark_ready(#1) merges; mark_ready(#2) answers main moved,
    exit 3, main unchanged, app.py named; r merges team/main in and
    pushes; mark_ready(#2) then merges; main moved forward twice and both
    issues are closed. The four parts are checked as expectations and
    G13 after every act."""
    remote = SharedRemote()
    try:
        world = BareGitWorld(str(remote.bare))
        try:
            policy = POLICIES["arm"]
            queue = gate.Queue({1: "m", 2: "r"}, {1: "Greeting", 2: "Farewell"}, False, remote.base)
            pusher, merged, found, timeline = GitPusher(remote), {}, [], []

            def act(one):
                with world:
                    check = check_before(world, policy, queue, one)
                    step = StepWithCheck(*apply(policy, world, queue, one, pusher), *check)
                found.extend((str(one), p) for p in problems(policy, queue, step, merged) + forward(remote.bare, step))
                if step.outcome is not None and step.outcome.merged:
                    merged[policy.branch_of(step.outcome.issue)] = 1
                timeline.append(f"{one}: {step.outcome.result if step.outcome else step.error or 'done'}")
                return step

            mismatches = []
            for agent, issue in (("m", 1), ("r", 2)):
                act(Act("claim", agent, issue))
                act(Act("push", agent, issue, gate.CONFLICTING))
                act(Act("open", agent, issue))
            first = act(Act("ready", "m", 1))
            if not (first.outcome and first.outcome.merged and first.outcome.exit_code == 0):
                mismatches.append(f"mark_ready(#1) did not merge with exit 0: {first.outcome}")
            main_after_first = remote.main()
            second = act(Act("ready", "r", 2))
            if not (second.outcome and second.outcome.result == gate.MAIN_MOVED and second.outcome.exit_code == 3):
                mismatches.append(f"mark_ready(#2) did not answer main moved, exit 3: {second.outcome}")
            if remote.main() != main_after_first:
                mismatches.append("main changed on a main moved answer")
            if second.outcome and tuple(second.outcome.conflicts) != ("app.py",):
                mismatches.append(f"the conflicts named are {second.outcome.conflicts}, not ('app.py',)")
            # r merges team/main in, resolves, and pushes.
            clone = remote.clone("r")
            git(clone, "fetch", "--quiet", "team", actor="r")
            git(clone, "switch", "--quiet", "claude/2", actor="r")
            git(clone, "merge", "team/main", check=False, actor="r")
            (clone / "app.py").write_text(BASE_TEXT.replace("'hello'", "'m and r'"), encoding="utf-8")
            git(clone, "add", "app.py", actor="r")
            git(clone, "commit", "--quiet", "--no-edit", "-m", "Merge team/main into claude/2", actor="r")
            git(clone, "push", "--quiet", "team", "claude/2", actor="r")
            timeline.append("r merges team/main in and pushes")
            third = act(Act("ready", "r", 2))
            if not (third.outcome and third.outcome.merged):
                mismatches.append(f"mark_ready(#2) after bringing main in did not merge: {third.outcome}")
            merges = git(remote.bare, "rev-list", "--merges", "--count", "main")
            if merges != "3":
                mismatches.append(f"main holds {merges} merge commits, not 3 (two of the gate's, one of r's)")
            if not all(queue.issues[n].closed for n in (1, 2)):
                mismatches.append("an issue is still open")
            return {"timeline": timeline, "mismatches": mismatches, "violations": [list(f) for f in found]}
        finally:
            world.close()
    finally:
        remote.close()


def lane(depth=8, count=40, length=8, seed=0, budget=None):
    """E-gate over MemoryWorld under the four policies, S05g and the
    sequences over BareGitWorld."""
    results = {"memory": [], "s05g": None, "git": None}
    for name in POLICIES:
        found = explore(name, depth, budget=budget)
        results["memory"].append({"policy": name, "states": found.states, "transitions": found.transitions,
                                  "depth": found.depth, "seconds": round(found.seconds, 1), "stopped": found.stopped,
                                  "violations": [{"trace": t, "problems": p} for t, p in found.violations[:20]],
                                  "violation_count": len(found.violations)})
    results["s05g"] = s05g()
    results["git"] = sequences_over_git(count, length, seed)
    return results


def red(results):
    return (any(r["violation_count"] or r.get("stopped") for r in results["memory"]) or results["s05g"]["mismatches"]
            or results["s05g"]["violations"] or results["git"]["violations"])
