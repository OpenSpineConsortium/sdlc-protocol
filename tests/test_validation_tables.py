"""The three table-driven tests of DESIGN.md 5.3: S06, the gate's presets
over MemoryWorld; S10, the clerk's planning over a client that reads and
writes the model, with the board rendered from it; S11, the protocol
check's cases. Each is a table crossed whole, so a row the library gets
wrong names itself."""

import itertools
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import gate, protocol_check, team  # noqa: E402
from sdlc_protocol.adapters.memory import MemoryWorld  # noqa: E402
from validation import cycle, model as M, page  # noqa: E402
from validation.clerk import ModelClient  # noqa: E402

PRESETS = {"ACCOUNT_POLICY": gate.ACCOUNT_POLICY, "TEAM_POLICY": gate.TEAM_POLICY}
CHECKS = (gate.GREEN, gate.RED, gate.PENDING, gate.NO_CHECK)


def new_queue(features=(1, 2), open_queue=False):
    given = {n: f"agent{n}" for n in features}
    return gate.Queue(given, {n: f"Feature {n}" for n in features}, open_queue, "0a1b2c3d4e")


def refusal(action, *args):
    try:
        answer = action(*args)
    except gate.GateError as error:
        return error
    raise AssertionError(f"{action.__name__} answered: {answer}")


class S06TheGatesPresets(unittest.TestCase):
    """A ready pull request waits for the owner under ACCOUNT_POLICY and
    lands by auto-merge on a green check under TEAM_POLICY; red, pending
    and missing checks refuse the landing with exit code 1 and leave the
    pull request ready; and three agents racing for one issue under the
    caller's lock give one winner and two exit codes 2."""

    def test_each_preset_crossed_with_each_check_state(self):
        for (name, policy), check in itertools.product(PRESETS.items(), CHECKS):
            with self.subTest(policy=name, check=check):
                world, queue = MemoryWorld(), new_queue()
                gate.claim(policy, queue, 1, "agent1")
                world.push("claude/1", gate.CLEAN, ())
                gate.open_pull(policy, world, queue, 1, "agent1")
                world.set_check("claude/1", check)
                ready = gate.mark_ready(policy, world, queue, 1, "agent1")
                self.assertEqual((ready.result, ready.exit_code, ready.merged), ("ready", gate.EXIT_DONE, False))
                waits = "waits for the owner" if policy.who_merges == gate.OWNER else "merges by itself once check is green"
                self.assertIn(waits, ready.text)
                self.assertEqual(world.merges, [], "marking ready moves main under neither preset")
                self.assertEqual(queue.issues[1].label(), "in-review")
                if check == gate.GREEN:
                    landed = gate.land(policy, world, queue, 1)
                    self.assertTrue(landed.merged)
                    self.assertEqual((landed.exit_code, queue.issues[1].label()), (gate.EXIT_DONE, "closed"))
                    self.assertEqual([branch for branch, _ in world.merges], ["claude/1"])
                else:
                    error = refusal(gate.land, policy, world, queue, 1)
                    self.assertEqual(error.exit_code, 1)
                    self.assertIn("the check check", str(error))
                    self.assertEqual((queue.pulls[1].state, world.merges), ("ready", []))

    def test_the_exit_codes_tell_the_refusals_apart(self):
        policy, world, queue = gate.ACCOUNT_POLICY, MemoryWorld(), new_queue()
        gate.claim(policy, queue, 1, "agent1")
        self.assertEqual(refusal(gate.claim, policy, queue, 1, "agent2").exit_code, gate.EXIT_SOMEONE_ELSES)
        self.assertEqual(refusal(gate.open_pull, policy, world, queue, 1, "agent1").exit_code, gate.EXIT_REFUSED)
        world.push("claude/1", gate.CONFLICTING, ("app.py",))
        gate.open_pull(policy, world, queue, 1, "agent1")
        ready = gate.mark_ready(policy, world, queue, 1, "agent1")
        self.assertEqual(ready.exit_code, gate.EXIT_DONE)
        world.set_check("claude/1", gate.GREEN)
        moved = gate.land(policy, world, queue, 1)
        self.assertEqual((moved.result, moved.exit_code, moved.merged), ("main moved", gate.EXIT_MAIN_MOVED, False))
        self.assertEqual(world.merges, [], "the gate left main as it was")
        self.assertIn("--force-with-lease", gate.main_moved_instruction(policy))
        self.assertNotIn("--force-with-lease", gate.main_moved_instruction(gate.TEAM_POLICY))

    def test_three_agents_racing_under_the_callers_lock_give_one_winner(self):
        for name, policy in PRESETS.items():
            with self.subTest(policy=name):
                queue, lock, start, answers = new_queue((1, 2, 3), open_queue=True), threading.Lock(), threading.Barrier(3), {}

                def race(agent):
                    start.wait()
                    try:
                        with lock:
                            answers[agent] = gate.claim(policy, queue, 3, agent)
                    except gate.GateError as error:
                        answers[agent] = error

                threads = [threading.Thread(target=race, args=(f"agent{n}",)) for n in (1, 2, 3)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(10)
                winners = [agent for agent, answer in answers.items() if isinstance(answer, gate.Issue)]
                self.assertEqual((len(answers), len(winners)), (3, 1))
                self.assertEqual(queue.issues[3].claimant, winners[0])
                self.assertEqual(sorted(a.exit_code for n, a in answers.items() if n != winners[0]), [2, 2])


PLAN = {"plan": "two pieces", "tasks": [
    {"title": "the first piece", "owner": "a", "files": ["src/one.py"], "body": "do one"},
    {"title": "the second piece", "owner": "nobody", "files": ["src/two.py"], "body": "do two"},
]}


def epic_model():
    model = M.seeded((1,), writers=("a", "b"))
    model.issues[1].labels = frozenset({"todo", "epic"})
    model.issues[1].title = "an epic"
    return model


class S10TheClerksPlanning(unittest.TestCase):
    """route's answer to each event, then seed over a client that reads and
    writes the model, after which each task is given to a registered
    worker, or goes to the open queue when none is registered, and the
    board rendered from the model lists every given task under Given to
    workers and not in the Queue."""

    def test_route_answers_each_event(self):
        client = ModelClient(epic_model())
        self.assertEqual(team.route(client, "labeled", 1, "epic"), "plan 1")
        self.assertEqual(team.route(client, "labeled", 1, "todo"), "none")
        team.seed(client, 1, PLAN, ["a", "b"])
        self.assertEqual(team.route(client, "labeled", 1, "epic"), "none", "planned already")
        self.assertEqual(team.route(client, "closed", 2), "none", "one task of two")
        client.model.issues[2].labels = client.model.issues[2].labels | {"blocked"}
        self.assertEqual(team.route(client, "labeled", 2, "blocked"), "unblock 2")
        self.assertEqual(team.route(client, "labeled", 1, "blocked"), "none", "an epic is no task")
        client.model.issues[2].state = client.model.issues[3].state = "closed"
        self.assertEqual(team.route(client, "closed", 3), "integrate 1", "the last task closing")
        self.assertEqual(team.route(client, "closed", 2), "integrate 1", "two closing in one run both say so")
        self.assertEqual(team.route(client, "closed", 1), "integrate 1", "the epic's own number recovers it")
        client.model.refs["claude/1"] = client.model.refs["main"]
        client.model.creators["claude/1"] = "a"
        self.assertEqual(team.route(client, "closed", 3), "integrate 1", "claimed, with work left to do")
        client.model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1", draft=False)
        self.assertEqual(team.route(client, "closed", 3), "none", "an integration claimed and ready")

    def test_seed_gives_each_task_to_a_registered_worker_or_to_the_open_queue(self):
        for agents, given, queued in ((["a", "b"], {2: ["a"], 3: ["b"]}, []), ([], {}, [2, 3])):
            with self.subTest(agents=agents):
                model = epic_model()
                client = ModelClient(model)
                self.assertEqual(team.seed(client, 1, PLAN, agents), "seeded 2 3")
                self.assertEqual(team.seed(client, 1, PLAN, agents), "already planned")
                self.assertNotIn("todo", model.issues[1].labels, "an epic is not taken whole")
                self.assertIn("<!-- team:tasks 2 3 -->", model.issues[1].body)
                self.assertEqual(model.issues[2].title, "the first piece")
                self.assertIn("Epic: #1", model.issues[3].body)
                record = cycle.board_run(model)
                self.assertEqual(page.given(record.page), given)
                self.assertEqual(page.queue(record.page), queued)
                self.assertNotIn(1, page.queue(record.page))

    def test_the_tasks_fit_their_workers_areas_when_dealt(self):
        model = epic_model()
        client = ModelClient(model)
        team.seed(client, 1, PLAN, [])
        self.assertEqual(team.next_for(client, "a", ["a", "b"], {"a": ("src/two.py",)}), "take 3")
        self.assertEqual(team.next_for(client, "b", ["a", "b"], {"a": ("src/two.py",)}), "take 2")


CLOSING_FORMS = ("Closes #{n}", "closes #{n}", "Fixes #{n}", "Resolves #{n}", "fixed #{n}", "see #{n}", "Closes #{m}", "")


class S11TheProtocolCheck(unittest.TestCase):
    """Every pull request state crossed with the claim file present or not,
    the body's closing forms, and a head in a fork."""

    def test_the_table(self):
        for draft, claim_file, form, fork in itertools.product((True, False), (True, False), CLOSING_FORMS, (False, True)):
            with self.subTest(draft=draft, claim_file=claim_file, form=form, fork=fork):
                body = form.format(n=7, m=8)
                pull = {"head": {"ref": "claude/7", "repo": {"full_name": "o/fork" if fork else "o/r"}},
                        "base": {"repo": {"full_name": "o/r"}}, "body": body, "draft": draft}
                problems = protocol_check.problems_for(pull, claim_file)
                closes = form in CLOSING_FORMS[:5]
                expected = {"fork"} if fork else set()
                if not closes:
                    expected.add("closes")
                if draft != claim_file:
                    expected.add("claim file")
                found = {("fork" if "fork" in p else "closes" if "close" in p else "claim file") for p in problems}
                self.assertEqual(found, expected, problems)
                self.assertEqual(len(problems), len(expected))

    def test_a_branch_that_is_no_claim_and_one_that_reads_as_one(self):
        for ref, problems in (("feature", 0), ("claude/07", 1), ("claude/\u0667", 1), ("claude/x", 0)):
            with self.subTest(ref=ref):
                pull = {"head": {"ref": ref, "repo": {"full_name": "o/r"}}, "base": {"repo": {"full_name": "o/r"}},
                        "body": "", "draft": True}
                self.assertEqual(len(protocol_check.problems_for(pull, True)), problems)


if __name__ == "__main__":
    unittest.main()
