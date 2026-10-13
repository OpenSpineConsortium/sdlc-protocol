"""The harness's scripted workers (validation/policies.py), acting on the
page alone: an idle holder released once after a day and the issue
claimed again; a finished draft waiting for review however long it takes;
a stranger's marker freeing nothing. Every step is checked against the
guarantees as it goes."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import invariants, model as M, oracle, page, policies, verbs  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
