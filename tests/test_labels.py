"""The labels are spelled once, with the colours a repository is set up
with, and a worker's label is read under all three of its spellings; the
label-list test is the template's template_init test, ported."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import labels  # noqa: E402


class Labels(unittest.TestCase):
    def test_the_six_labels_and_their_colours(self):
        self.assertEqual(
            [(name, color) for name, color, _ in labels.LABELS],
            [("todo", "0e8a16"), ("claimed", "fbca04"), ("in-review", "1d76db"), ("blocker", "b60205"),
             ("epic", "8b5cf6"), ("blocked", "e99695")],
        )

    def test_the_labels_are_the_templates_word_for_word(self):
        # tools/template_init.py's LABELS, copied: a repository set up by
        # the template and one set up by this library get the same labels.
        self.assertEqual(labels.LABELS, (
            ("todo", "0e8a16", "In the queue, open for a claim"),
            ("claimed", "fbca04", "A claude/<issue> branch holds it"),
            ("in-review", "1d76db", "A pull request is open and waiting"),
            ("blocker", "b60205", "Stops everyone until it is settled"),
            ("epic", "8b5cf6", "A request the coordinator plans into tasks (docs/agents/WORKERS.md)"),
            ("blocked", "e99695", "A worker waits on a peer or the coordinator (docs/agents/WORKERS.md)"),
        ))

    def test_every_label_has_a_description_and_a_six_digit_colour(self):
        for name, colour, description in labels.LABELS:
            self.assertEqual(len(colour), 6, name)
            int(colour, 16)
            self.assertTrue(description, name)

    def test_the_names_and_the_prefixes(self):
        self.assertEqual((labels.TODO, labels.CLAIMED, labels.IN_REVIEW, labels.BLOCKER), ("todo", "claimed", "in-review", "blocker"))
        self.assertEqual((labels.EPIC, labels.BLOCKED), ("epic", "blocked"))
        self.assertEqual(labels.WORKER_PREFIX, "worker:")
        self.assertEqual(labels.ACCOUNT_PREFIX, "account:")
        self.assertEqual(labels.AGENT_PREFIX, "agent:")
        self.assertEqual(labels.worker_label("nightly"), "worker:nightly")
        self.assertEqual(len(labels.WORKER_COLOUR), 6)
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

    def test_holders_are_everyone_named_under_any_spelling(self):
        self.assertEqual(labels.holders({"labels": ["agent:w1", "account:main", "todo"]}), {"w1", "main"})
        self.assertEqual(labels.holders({"labels": ["worker:nightly", "account:nightly"]}), {"nightly"})
        self.assertEqual(labels.holders({"labels": ["todo", "worker:"]}), set())
        self.assertEqual(labels.holders({"labels": ["todo"]}), set())

    def test_the_worker_an_issue_names_prefers_the_worker_label(self):
        self.assertEqual(labels.worker_of({"labels": ["account:main", "worker:nightly"]}), "nightly")
        self.assertEqual(labels.worker_of({"labels": ["account:main", "agent:w1"]}), "main")
        self.assertEqual(labels.worker_of({"labels": ["agent:w1"]}), "w1")
        self.assertIsNone(labels.worker_of({"labels": ["todo"]}))
        self.assertIsNone(labels.worker_of(None))

    def test_the_labels_a_release_takes_off_are_every_spelling(self):
        issue = {"labels": ["claimed", "todo", "worker:a", "account:b", "agent:c", "blocker"]}
        self.assertEqual(labels.holder_labels(issue), ["account:b", "agent:c", "worker:a"])

    def test_an_epic_or_a_worker_label_keeps_an_issue_off_the_queue(self):
        for names in (["epic", "todo"], {"agent:w1", "todo"}, ["todo", "account:main"], ["todo", "worker:nightly"]):
            self.assertTrue(labels.reserved(names), names)
        self.assertFalse(labels.reserved(["todo", "blocker"]))
        self.assertFalse(labels.reserved([]))
        self.assertIs(labels.reserved_for_team, labels.reserved)


if __name__ == "__main__":
    unittest.main()
