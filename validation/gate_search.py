"""The gate's lane (DESIGN.md, 3.5, 5.2's S05g, 6.8's E-gate): G13.

The merge gate (sdlc_protocol.gate) decides over a World; this lane runs
every interleaving of two agents' claims, pushes, pull requests, marks and
landings, with main moving and the check changing, over MemoryWorld,
under the two presets, the coordbench arm's policy and a gated check, to
a depth, and checks the gate's guarantee after every act: a refusal
carries the exit code of its kind and moves main not at all; a landing
moves main exactly when it says it merged; the gate merges only on
mark_ready, and never without a green required check; an issue is closed
exactly when its pull request merged; a pull request is its claimant's;
no branch merges twice. The prototype of it is
validation/prototype/gate_explore.py.

Over BareGitWorld, where every merge is git's own, the same acts run as
sequences rather than a search, since a real repository cannot be copied
per state: the S05g scenario of DESIGN.md 5.2, the arm's gate over two
branches that change one line, and seeded random sequences of the acts
under each policy, a bare repository per sequence, with the same checks
after every act. A push that stands clean changes a file of the agent's
own; one that conflicts changes the first line of app.py, as the owner's
move of main does, so git finds the conflict the memory world declares.
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

from .git_store import ENV, settings_arguments

POLICIES = {
    "account": gate.ACCOUNT_POLICY,
    "team": gate.TEAM_POLICY,
    "arm": gate.Policy(bring_main_in=gate.MERGE, who_merges=gate.GATE, required_check="", remote="team"),
    "gated-check": gate.Policy(who_merges=gate.GATE),
}
EXIT_CODES = {gate.SomeoneElses: gate.EXIT_SOMEONE_ELSES, gate.MainMoved: gate.EXIT_MAIN_MOVED,
              gate.GateFailed: gate.EXIT_GATE_FAILED, gate.Refused: gate.EXIT_REFUSED,
              gate.MergeRefused: gate.EXIT_REFUSED}
BASE_TEXT = "def greet():\n    return 'hello'\n\n\ndef part():\n    return 'base'\n"


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


def apply(policy, world, queue, act, pusher):
    """One act over a world, with pusher making the push: the step."""
    main_before = world.main_sha()
    outcome, error = None, None
    try:
        if act.kind == "move_main":
            pusher.move_main(world)
        elif act.kind == "claim":
            gate.claim(policy, queue, act.issue, act.agent)
        elif act.kind == "push":
            if queue.issues[act.issue].claimant != act.agent:
                raise gate.Refused("not your claim")  # an agent pushes only the branch it claimed
            pusher.push(world, policy.branch_of(act.issue), act.agent, act.word)
        elif act.kind == "open":
            gate.open_pull(policy, world, queue, act.issue, act.agent)
        elif act.kind == "ready":
            outcome = gate.mark_ready(policy, world, queue, act.issue, act.agent)
        elif act.kind == "land":
            if policy.who_merges == gate.GATE:
                raise gate.NotAllowed("the gate lands on mark_ready")
            outcome = gate.land(policy, world, queue, act.issue)
        elif act.kind == "check":
            pusher.set_check(world, policy.branch_of(act.issue), act.word)
    except gate.GateError as caught:
        error = caught
    return Step(act, outcome, error, main_before, world.main_sha())


def problems(policy, queue, step, merged_before):
    """G13 after one act; merged_before counts each branch's landings so
    far, before this act."""
    act, outcome, error = step.act, step.outcome, step.error
    moved = step.main_after != step.main_before
    found = []
    if error is not None:
        expected = next(code for cls, code in EXIT_CODES.items() if isinstance(error, cls))
        if error.exit_code != expected:
            found.append(f"exit code {error.exit_code} for {type(error).__name__}")
        if act.kind not in ("move_main", "push") and moved:
            found.append("a refusal moved main")
    if outcome is not None:
        if outcome.merged != moved:
            found.append(f"outcome {outcome.result} but main {'moved' if moved else 'stayed'}")
        if outcome.result == gate.MAIN_MOVED and moved:
            found.append("main moved answer, yet main moved")
        if outcome.merged and policy.who_merges == gate.GATE and act.kind != "ready":
            found.append("the gate merged outside mark_ready")
        if outcome.merged and policy.required_check and step.check_at_landing != gate.GREEN:
            found.append("merged without a green required check")
        if outcome.merged and merged_before.get(policy.branch_of(outcome.issue)):
            found.append(f"{policy.branch_of(outcome.issue)} merged twice")
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


def check_before(world, policy, act):
    """The required check's state as the act found it, for the landing
    rule, read before the act moves anything."""
    if act.kind in ("ready", "land"):
        return world.check(policy.branch_of(act.issue))
    return None


class StepWithCheck(NamedTuple):
    """A step with the required check's state as the landing found it,
    which the landing rule reads (problems)."""

    act: Act
    outcome: object
    error: object
    main_before: str
    main_after: str
    check_at_landing: object


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


def explore(name, depth, agents=("a", "b"), issues=(1, 2), budget=None):
    """Every interleaving of the acts to depth over MemoryWorld under the
    policy, breadth first, keyed by the world and the queue."""
    policy, pusher = POLICIES[name], MemoryPusher()
    world, queue = World(), gate.Queue({n: "a" for n in issues}, open_queue=True)
    seen, frontier = {key(world, queue)}, deque([(world, queue, (), {})])
    violations, transitions, began = [], 0, time.perf_counter()
    acts = actions(agents, issues)
    while frontier:
        if budget is not None and time.perf_counter() - began > budget:
            break
        world, queue, trace, merged = frontier.popleft()
        if len(trace) >= depth:
            continue
        for act in acts:
            next_world, next_queue = copy.deepcopy(world), copy.deepcopy(queue)
            check = check_before(next_world, policy, act)
            step = StepWithCheck(*apply(policy, next_world, next_queue, act, pusher), check)
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
    return Exploration(name, len(seen), transitions, depth, time.perf_counter() - began, tuple(violations))


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
    tests/test_bare_git.py builds them."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sdlc-gate-lane-")
        self.root = Path(self.tmp.name)
        self.bare = self.root / "team.git"
        git(self.root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.bare))
        seed = self.root / "seed"
        seed.mkdir()
        git(seed, "init", "--quiet", "--initial-branch=main")
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
            self.clones[agent] = path
        return self.clones[agent]

    def main(self):
        return git(self.bare, "rev-parse", "refs/heads/main")

    def close(self):
        self.tmp.cleanup()


class GitPusher:
    """The acts made with git: a push that stands clean adds a line to the
    agent's own file; one that conflicts rewrites app.py's first line, as
    the owner's move of main does, so the two meet in git."""

    def __init__(self, remote):
        self.remote = remote
        self.pushes = 0

    def move_main(self, world):
        self.remote.moves += 1
        clone = self.remote.clone("_owner")
        git(clone, "fetch", "--quiet", "team", actor="owner")
        git(clone, "switch", "--quiet", "-C", "main", "team/main", actor="owner")
        (clone / "app.py").write_text(BASE_TEXT.replace("'hello'", f"'moved {self.remote.moves}'"), encoding="utf-8")
        git(clone, "add", "app.py", actor="owner")
        git(clone, "commit", "--quiet", "-m", f"elsewhere {self.remote.moves}", actor="owner")
        git(clone, "push", "--quiet", "team", "HEAD:main", actor="owner")

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
    the sequence to reach a merge; one step in five is any act at all, so
    refusals are tried over git too."""
    world, queue, taken = World(), gate.Queue({n: "a" for n in issues}, open_queue=True), []
    for _ in range(length):
        possible = [a for a in acts if goes_through(policy, world, queue, a)]
        if rng.random() < STRAY or not possible:
            act = rng.choice(acts)
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
                    check = check_before(world, policy, act)
                    step = StepWithCheck(*apply(policy, world, queue, act, pusher), check)
                steps.append(step)
                found += [(str(act), p) for p in problems(policy, queue, step, merged)]
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
                    check = check_before(world, policy, one)
                    step = StepWithCheck(*apply(policy, world, queue, one, pusher), check)
                found.extend((str(one), p) for p in problems(policy, queue, step, merged))
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
                                  "depth": found.depth, "seconds": round(found.seconds, 1),
                                  "violations": [{"trace": t, "problems": p} for t, p in found.violations[:20]],
                                  "violation_count": len(found.violations)})
    results["s05g"] = s05g()
    results["git"] = sequences_over_git(count, length, seed)
    return results


def red(results):
    return (any(r["violation_count"] for r in results["memory"]) or results["s05g"]["mismatches"]
            or results["s05g"]["violations"] or results["git"]["violations"])
