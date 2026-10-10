"""The labels are spelled once, with the colours a repository is set up
with; the seven-label test is the template's template_init test, ported."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import labels  # noqa: E402


class Labels(unittest.TestCase):
    def test_the_seven_labels_and_their_colours(self):
        self.assertEqual(
            [(name, color) for name, color, _ in labels.LABELS],
            [("todo", "0e8a16"), ("claimed", "fbca04"), ("in-review", "1d76db"), ("blocker", "b60205"),
             ("account:main", "5319e7"), ("epic", "8b5cf6"), ("blocked", "e99695")],
        )

    def test_the_labels_are_the_templates_word_for_word(self):
        # tools/template_init.py's LABELS, copied: a repository set up by
        # the template and one set up by this library get the same labels.
        self.assertEqual(labels.LABELS, (
            ("todo", "0e8a16", "In the queue, open for a claim"),
            ("claimed", "fbca04", "A claude/<issue> branch holds it"),
            ("in-review", "1d76db", "A pull request is open and waiting"),
            ("blocker", "b60205", "Stops everyone until it is settled"),
            ("account:main", "5319e7", "Work for the main Claude account"),
            ("epic", "8b5cf6", "A spec the team lead plans into tasks (docs/agents/TEAM.md)"),
            ("blocked", "e99695", "A member waits on a peer or the lead (docs/agents/TEAM.md)"),
        ))

    def test_every_label_has_a_description_and_a_six_digit_colour(self):
        for name, colour, description in labels.LABELS:
            self.assertEqual(len(colour), 6, name)
            int(colour, 16)
            self.assertTrue(description, name)

    def test_the_names_and_the_prefixes(self):
        self.assertEqual((labels.TODO, labels.CLAIMED, labels.IN_REVIEW, labels.BLOCKER), ("todo", "claimed", "in-review", "blocker"))
        self.assertEqual((labels.EPIC, labels.BLOCKED), ("epic", "blocked"))
        self.assertEqual(labels.ACCOUNT_PREFIX, "account:")
        self.assertEqual(labels.AGENT_PREFIX, "agent:")
        self.assertEqual([name for name, _, _ in labels.LABELS][:4], [labels.TODO, labels.CLAIMED, labels.IN_REVIEW, labels.BLOCKER])


class Names(unittest.TestCase):
    def test_label_names_are_sorted_from_objects_or_strings(self):
        self.assertEqual(labels.label_names({"labels": [{"name": "todo"}, {"name": "blocker"}]}), ["blocker", "todo"])
        self.assertEqual(labels.label_names({"labels": ["todo", "account:main"]}), ["account:main", "todo"])
        self.assertEqual(labels.label_names({"labels": None}), [])
        self.assertEqual(labels.label_names({}), [])
        self.assertEqual(labels.label_names(None), [])

    def test_the_account_and_the_agent_a_label_names(self):
        issue = {"labels": [{"name": "todo"}, {"name": "account:main"}]}
        self.assertEqual(labels.account_label(issue), "main")
        self.assertIsNone(labels.agent_label(issue))
        task = {"labels": ["agent:w1", "todo"]}
        self.assertEqual(labels.agent_label(task), "w1")
        self.assertIsNone(labels.account_label(task))
        self.assertIsNone(labels.account_label({"labels": []}))
        self.assertIsNone(labels.account_label(None))

    def test_holders_are_everyone_named(self):
        self.assertEqual(labels.holders({"labels": ["agent:w1", "account:main", "todo"]}), {"w1", "main"})
        self.assertEqual(labels.holders({"labels": ["todo"]}), set())

    def test_team_work_is_reserved(self):
        self.assertTrue(labels.reserved_for_team(["epic", "todo"]))
        self.assertTrue(labels.reserved_for_team({"agent:w1", "todo"}))
        self.assertFalse(labels.reserved_for_team(["todo", "account:main"]))
        self.assertFalse(labels.reserved_for_team([]))


if __name__ == "__main__":
    unittest.main()
