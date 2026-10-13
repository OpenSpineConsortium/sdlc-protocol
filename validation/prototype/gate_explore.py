"""Prototype of the gate scope: every interleaving of two agents' claims,
pushes, pull requests and marks over the library's MemoryWorld, under the
two presets and the coordbench arm's policy, each state checked against the
gate's guarantees (spec/PROTOCOL.md, Policy and worlds). Scratch only."""

import copy
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository's root: the library

from sdlc_protocol import gate  # noqa: E402
from sdlc_protocol.adapters.memory import MemoryWorld  # noqa: E402

POLICIES = {
    "account": gate.ACCOUNT_POLICY,
    "team": gate.TEAM_POLICY,
    "arm": gate.Policy(bring_main_in=gate.MERGE, who_merges=gate.GATE, required_check="", remote="team"),
    "gated-check": gate.Policy(who_merges=gate.GATE),
}
EXCEPTION_CODES = {gate.SomeoneElses: 2, gate.MainMoved: 3, gate.GateFailed: 4, gate.Refused: 1, gate.MergeRefused: 1}


class World(MemoryWorld):
    def __deepcopy__(self, memo):
        other = World.__new__(World)
        for name, value in self.__dict__.items():
            setattr(other, name, value if name == "lock" else copy.deepcopy(value, memo))
        return other


def key(world, queue):
    return (world.main_commit, tuple(sorted(world.branches.items())), tuple(sorted(world.words.items())),
            tuple(sorted(world.checks.items())),
            tuple((i.number, i.claimant, i.claim_released, i.closed) for i in queue.issues.values()),
            tuple((p.issue, p.author, p.state, p.attempts) for p in queue.pulls.values()))


def actions(policy, agents, issues):
    acts = [("move_main",)]
    for n in issues:
        branch = policy.branch_of(n)
        for a in agents:
            acts += [("claim", a, n), ("open", a, n), ("ready", a, n)]
            acts += [("push", a, n, word) for word in (gate.CLEAN, gate.CONFLICTING)]
        acts += [("land", n)] + [("check", n, s) for s in (gate.GREEN, gate.RED, gate.PENDING)]
    return acts


def apply(policy, world, queue, act):
    world, queue = copy.deepcopy(world), copy.deepcopy(queue)
    main_before = world.main_commit
    outcome, error = None, None
    try:
        kind = act[0]
        if kind == "move_main":
            world.move_main()
        elif kind == "claim":
            gate.claim(policy, queue, act[2], act[1])
        elif kind == "push":
            _, a, n, word = act
            if queue.issues[n].claimant != a:      # an agent pushes only the branch it claimed
                raise gate.Refused("not your claim")
            world.push(policy.branch_of(n), word, ("app.py",) if word == gate.CONFLICTING else ())
        elif kind == "open":
            gate.open_pull(policy, world, queue, act[2], act[1])
        elif kind == "ready":
            outcome = gate.mark_ready(policy, world, queue, act[2], act[1])
        elif kind == "land":
            if policy.who_merges == gate.GATE:
                raise gate.NotAllowed("the gate lands on mark_ready")
            outcome = gate.land(policy, world, queue, act[1])
        elif kind == "check":
            world.set_check(policy.branch_of(act[1]), act[2])
    except gate.GateError as caught:
        error = caught
    return world, queue, outcome, error, main_before


def problems(policy, world, queue, act, outcome, error, main_before):
    found = []
    if error is not None:
        expected = next(code for cls, code in EXCEPTION_CODES.items() if isinstance(error, cls))
        if error.exit_code != expected:
            found.append(f"exit code {error.exit_code} for {type(error).__name__}")
        if act[0] not in ("move_main", "push") and world.main_commit != main_before:
            found.append("a refusal moved main")
    if outcome is not None:
        moved = world.main_commit != main_before
        if outcome.merged != moved:
            found.append(f"outcome {outcome.result} but main {'moved' if moved else 'stayed'}")
        if outcome.result == gate.MAIN_MOVED and moved:
            found.append("main moved answer, yet main moved")
        if outcome.merged and policy.required_check and world.check(policy.branch_of(outcome.issue)) != gate.GREEN:
            found.append("merged without a green required check")
        if outcome.merged and policy.who_merges == gate.GATE and act[0] != "ready":
            found.append("the gate merged outside mark_ready")
    for n, issue in queue.issues.items():
        pull = queue.pulls.get(n)
        if issue.closed != (pull is not None and pull.state == gate.MERGED):
            found.append(f"#{n} closed is {issue.closed} but its pull request is {pull.state if pull else 'none'}")
        if pull is not None and pull.author != issue.claimant:
            found.append(f"#{n}'s pull request is {pull.author}'s, the claim {issue.claimant}'s")
    merged_branches = [b for b, _ in world.merges]
    if len(merged_branches) != len(set(merged_branches)):
        found.append(f"a branch merged twice: {merged_branches}")
    return found


def explore(name, depth, agents=("a", "b"), issues=(1, 2)):
    policy = POLICIES[name]
    world, queue = World(), gate.Queue({n: "a" for n in issues}, open_queue=True)
    seen, frontier, violations, transitions = {key(world, queue)}, deque([(world, queue, ())]), [], 0
    began = time.perf_counter()
    while frontier:
        world, queue, trace = frontier.popleft()
        if len(trace) >= depth:
            continue
        for act in actions(policy, agents, issues):
            nxt_world, nxt_queue, outcome, error, main_before = apply(policy, world, queue, act)
            transitions += 1
            found = problems(policy, nxt_world, nxt_queue, act, outcome, error, main_before)
            if found:
                violations.append((trace + (act,), found))
                continue
            k = key(nxt_world, nxt_queue)
            if k not in seen:
                seen.add(k)
                frontier.append((nxt_world, nxt_queue, trace + (act,)))
    return len(seen), transitions, time.perf_counter() - began, violations


if __name__ == "__main__":
    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    for name in POLICIES:
        states, transitions, seconds, violations = explore(name, depth)
        print(f"{name:12s} depth {depth}: states {states}, transitions {transitions}, {seconds:.1f} s, violations {len(violations)}")
        for trace, found in violations[:3]:
            print("   ", found, "<-", " -> ".join("/".join(map(str, a)) for a in trace))
