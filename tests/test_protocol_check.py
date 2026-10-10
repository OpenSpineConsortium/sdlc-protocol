"""The protocol check holds a claude/N pull request to its claim file,
its Closes line and its home repository, and touches nothing else. The
template's tests, ported: the claim file's presence is now a fact the
caller states, so the temporary checkout they made becomes a bool."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import protocol_check  # noqa: E402


def pull(branch="claude/7", body="Closes #7", draft=True,
         head_repo="octo/widgets", base_repo="octo/widgets"):
    return {
        "head": {"ref": branch, "repo": {"full_name": head_repo}},
        "base": {"repo": {"full_name": base_repo}},
        "body": body,
        "draft": draft,
    }


class CheckProtocolTest(unittest.TestCase):
    def with_claim(self, present, the_pull):
        return protocol_check.problems_for(the_pull, present)

    def test_another_branch_is_no_claim(self):
        problems = self.with_claim(False, pull(branch="dependabot/actions/checkout-8"))
        self.assertEqual(problems, [])

    def test_draft_with_claim_file_and_closes_line_is_clean(self):
        self.assertEqual(self.with_claim(True, pull()), [])

    def test_draft_without_claim_file_is_named(self):
        problems = self.with_claim(False, pull())
        self.assertTrue(any("claims/7.md is missing" in p for p in problems))

    def test_ready_with_claim_file_is_named(self):
        problems = self.with_claim(True, pull(draft=False))
        self.assertTrue(any("still here" in p for p in problems))

    def test_ready_without_claim_file_is_clean(self):
        self.assertEqual(self.with_claim(False, pull(draft=False)), [])

    def test_missing_closes_line_is_named(self):
        problems = self.with_claim(True, pull(body="A fine change."))
        self.assertTrue(any("does not close #7" in p for p in problems))

    def test_closing_the_wrong_issue_is_named(self):
        problems = self.with_claim(True, pull(body="Closes #8"))
        self.assertTrue(any("does not close #7" in p for p in problems))

    def test_the_other_closing_forms_count(self):
        for body in ("fixes #7", "Fixed #7 at last", "resolves #7", "close #7"):
            self.assertEqual(self.with_claim(True, pull(body=body)), [], body)

    def test_a_fork_head_is_named(self):
        problems = self.with_claim(True, pull(head_repo="someone/widgets"))
        self.assertTrue(any("not in a fork" in p for p in problems))

    def test_an_event_without_a_pull_request_passes(self):
        self.assertEqual(protocol_check.problems_for({}, False), [])
        self.assertEqual(protocol_check.problems_for(None, True), [])

    def test_the_messages_are_the_templates(self):
        problems = self.with_claim(True, pull(draft=False, body="", head_repo="someone/widgets"))
        self.assertEqual(problems, [
            "claude/7: a claim branch lives in this repository, not in a fork (docs/agents/PROTOCOL.md, Claim)",
            'the body does not close #7, so the merge would leave the issue open; write "Closes #7" (docs/agents/PROTOCOL.md, Finish)',
            "docs/agents/claims/7.md is still here: a pull request marked ready has deleted its claim file (docs/agents/PROTOCOL.md, Finish)",
        ])
        self.assertEqual(self.with_claim(False, pull()), [
            "docs/agents/claims/7.md is missing: a draft carries its claim file (docs/agents/PROTOCOL.md, Claim)",
        ])

    def test_the_claim_file_may_be_asked_for_by_number(self):
        asked = []

        def exists(number):
            asked.append(number)
            return True

        self.assertEqual(protocol_check.problems_for(pull(), exists), [])
        self.assertEqual(asked, [7])
        self.assertEqual(protocol_check.problems_for(pull(branch="main"), exists), [])
        self.assertEqual(asked, [7])

    def test_the_claim_number_comes_from_the_head_branch(self):
        self.assertEqual(protocol_check.claim_number(pull()), 7)
        self.assertEqual(protocol_check.claim_number(pull(branch="claude/42")), 42)
        self.assertIsNone(protocol_check.claim_number(pull(branch="claude/x")))
        self.assertIsNone(protocol_check.claim_number({}))


if __name__ == "__main__":
    unittest.main()
