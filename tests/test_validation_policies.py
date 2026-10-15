"""The harness's scripted workers (validation/policies.py), acting on the
page alone: an idle holder released once after a day and the issue
claimed again; a finished draft waiting for review however long it takes;
a stranger's marker freeing nothing; and the issue a worker picks from
the page, as the spec's Pick says. Every step is checked against the
guarantees as it goes."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cycle, invariants, model as M, oracle, page, policies, verbs  # noqa: E402
from validation.model import HOUR  # noqa: E402


def drive(model, workers, hours, test):
    """policies.drive, with every step checked."""
    for hour in range(hours + 1):
        model.now = hour * HOUR
        for name, policy in workers.items():
            for _ in range(8):
                verb = policy.decide(policies.view(model, name))
                if verb is None:
                    break
                before = model.clone()
                outcome = verbs.apply(model, verb)
                test.assertEqual(invariants.step_checks(before, model, verb, outcome), [], str(verb))
        before = model.clone()
        board = verbs.Verb("board", "board", ("schedule", ""))
        outcome = verbs.apply(model, board)
        test.assertEqual(invariants.step_checks(before, model, board, outcome), [], f"board at hour {hour}")


def bot_releases(model, number):
    return [c for c in model.issues[number].comments if c.author == M.BOT
            and oracle.first_line(c.body) == oracle.RELEASE_MARKER]


class Workers(unittest.TestCase):
    def test_an_idle_holder_is_released_once_and_the_issue_claimed_again(self):
        model = M.seeded((1,), writers=("a", "c"))
        drive(model, {"a": policies.IdleHolder()}, 30, self)
        self.assertEqual(len(bot_releases(model, 1)), 1)
        self.assertEqual(page.queue(model.board_text), [1])
        drive_from = model.now // HOUR + 1
        claimer = policies.EagerClaimer(k=1)
        for hour in range(drive_from, drive_from + 3):
            model.now = hour * HOUR
            for _ in range(8):
                verb = claimer.decide(policies.view(model, "c"))
                if verb is None:
                    break
                verbs.apply(model, verb)
        self.assertEqual(model.creators["claude/1"], "c")
        self.assertTrue(any(ref.startswith("released/1-") for ref in model.refs))

    def test_announce_takes_every_other_worker_s_label_off_in_any_spelling(self):
        """F18's second review: the spec's Labels reads account:<name> and
        agent:<name> as the same label as worker:<name>, and its Announce
        takes every other worker's label off, so from the claim on the
        labels name the holder alone; the policy took off worker: labels
        alone, and left a peer's older spelling on. A bare worker: and the
        holder's own labels, in any spelling, stay."""
        model = M.seeded((1,), writers=("a", "b"), labels=("todo", "account:a", "worker:b", "agent:b", "worker:"))
        cycle.board_run(model)
        claimer, applied = policies.EagerClaimer(k=1), []
        for _ in range(8):
            verb = claimer.decide(policies.view(model, "b"))
            if verb is None or verb.name == "commit":
                break
            applied.append(verb)
            before = model.clone()
            outcome = verbs.apply(model, verb)
            self.assertEqual(invariants.step_checks(before, model, verb, outcome), [], str(verb))
        self.assertEqual([v for v in applied if v.name == "label-off"], [verbs.Verb("label-off", "b", (1, "account:a"))])
        self.assertEqual(model.issues[1].labels, frozenset({"claimed", "todo", "worker:b", "agent:b", "worker:"}))

    def test_a_finished_draft_waits_however_long_it_takes(self):
        model = M.seeded((1,), writers=("f",))
        drive(model, {"f": policies.Finisher()}, 100, self)
        self.assertEqual(bot_releases(model, 1), [])
        self.assertIn("claude/1", model.refs)

    def test_a_stranger_s_marker_frees_nothing(self):
        model = M.seeded((1,), writers=("a",))
        drive(model, {"a": policies.EagerClaimer(k=30, pace=1), verbs.SAM: policies.Stranger()}, 30, self)
        self.assertEqual(model.creators["claude/1"], "a")
        self.assertEqual(bot_releases(model, 1), [])


class Pick(unittest.TestCase):
    """free_issue as the spec's Pick reads (spec/PROTOCOL.md): the issue the
    Given to workers section gives the worker, else a blocker the Blockers
    section lists that nobody has claimed and no worker's label gives to
    another, whether or not it carries todo, else the first in the Queue;
    never an issue labelled blocked. The second review of F15's fix found
    that a worker reading the board never took a blocker carrying no todo,
    as a released blocker or one the Blocker form makes, which the clerk
    deals first."""

    def picked(self, labelled, worker="a", steps=()):
        model = M.seeded(tuple(sorted(labelled)), writers=("a", "b"))
        for number, names in labelled.items():
            model.issues[number].labels = frozenset(names)
        for step in steps:
            verbs.apply(model, step)
        cycle.board_run(model)
        return policies.free_issue(policies.view(model, worker))

    def test_an_unclaimed_blocker_comes_before_the_queue_with_or_without_todo(self):
        self.assertEqual(self.picked({1: {"todo"}, 2: {"blocker"}}), 2)
        self.assertEqual(self.picked({1: {"todo"}, 2: {"blocker", "todo"}}), 2)

    def test_a_blocker_someone_holds_or_was_given_is_left_to_them(self):
        self.assertEqual(self.picked({1: {"todo"}, 2: {"blocker", "claimed"}}), 1)
        self.assertEqual(self.picked({1: {"todo"}, 2: {"blocker"}}, steps=(verbs.Verb("claim", "b", (2,)),)), 1)
        self.assertEqual(self.picked({1: {"todo"}, 2: {"blocker", "worker:b"}}), 1)
        self.assertEqual(self.picked({1: {"todo"}, 2: {"blocker", "worker:b"}}, worker="b"), 2)

    def test_an_issue_labelled_blocked_or_an_epic_blocker_is_never_taken(self):
        self.assertEqual(self.picked({1: {"todo", "blocked"}, 2: {"todo"}}), 2)
        self.assertEqual(self.picked({1: {"blocker", "blocked"}, 2: {"blocker", "epic"}}), None)

    def test_an_epic_or_a_blocked_issue_given_to_the_worker_is_left_where_it_is(self):
        """The third review of F15's fix: Pick takes what Given to workers
        gives the worker first, but never an epic, which the coordinator
        plans, nor an issue labelled blocked; that section shows no labels,
        so the worker reads its own issue's (View.given)."""
        self.assertEqual(self.picked({1: {"todo"}, 2: {"epic", "worker:a"}}), 1)
        self.assertEqual(self.picked({1: {"todo"}, 2: {"blocked", "worker:a"}}), 1)
        self.assertEqual(self.picked({1: {"todo"}, 2: {"worker:a"}}), 2)

    def test_a_worker_prefix_with_no_name_gives_the_issue_to_nobody(self):
        """As the board reads one (labels.holder_names), a label worker:
        with nothing after it names no worker, so its issue stays in the
        Queue and free to take."""
        self.assertEqual(self.picked({1: {"todo", "worker:"}}), 1)


if __name__ == "__main__":
    unittest.main()
