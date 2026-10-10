"""The claim conventions: the branch name, the claim file's path, what the
board reads from a claim file, and when the coordinator releases a claim.
The parsing tests are the template's board tests for claim_account,
claim_files and parse_time, ported as they were."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims  # noqa: E402

CLAIM_TEXT = "\n".join([
    "# Claim: #16",
    "",
    "- Issue: https://github.com/octo/widgets/issues/16",
    "- Account: main",
    "- Branch: claude/16",
    "",
    "## Plan",
    "",
    "Two sentences on what changes.",
    "",
    "## Files to touch",
    "",
    "- docs/b.md",
    "- `docs/a.md`",
    "",
])


class Branches(unittest.TestCase):
    def test_the_branch_for_an_issue(self):
        self.assertEqual(claims.branch_of(7), "claude/7")
        self.assertEqual(claims.branch_of("7"), "claude/7")
        self.assertEqual(claims.PREFIX, "claude/")

    def test_the_number_of_a_branch(self):
        self.assertEqual(claims.number_of("claude/7"), 7)
        self.assertEqual(claims.number_of("claude/100"), 100)
        self.assertIsNone(claims.number_of("claude/x"))
        self.assertIsNone(claims.number_of("claude/"))
        self.assertIsNone(claims.number_of("main"))
        self.assertIsNone(claims.number_of("dependabot/actions/checkout-8"))

    def test_the_key_keeps_what_follows_the_prefix(self):
        self.assertEqual(claims.key_of("claude/7"), "7")
        self.assertEqual(claims.key_of("claude/x"), "x")
        self.assertIsNone(claims.key_of("main"))
        self.assertTrue(claims.is_claim_branch("claude/7"))
        self.assertFalse(claims.is_claim_branch("claudette/7"))

    def test_a_digit_that_is_no_decimal_is_no_number(self):
        # The template's check matched \d, which a superscript does not;
        # isdigit() lets it through and int() then raises.
        self.assertIsNone(claims.number_of("claude/\u00b2"))
        self.assertEqual(claims.number_of("claude/007"), 7)

    def test_a_policy_may_name_another_prefix(self):
        self.assertEqual(claims.branch_of(7, "agent/"), "agent/7")
        self.assertEqual(claims.number_of("agent/7", "agent/"), 7)
        self.assertIsNone(claims.number_of("claude/7", "agent/"))
        self.assertEqual(claims.key_of("agent/x", prefix="agent/"), "x")
        self.assertTrue(claims.is_claim_branch("agent/7", "agent/"))

    def test_the_claim_file_path(self):
        self.assertEqual(claims.claim_path(7), "docs/agents/claims/7.md")
        self.assertEqual(claims.CLAIM_DIR, "docs/agents/claims")

    def test_numbers_sort_before_names(self):
        keys = ["x", "100", "9", "a", "12"]
        self.assertEqual(sorted(keys, key=claims.by_number), ["9", "12", "100", "a", "x"])


class ClaimFile(unittest.TestCase):
    def test_claim_account_spellings(self):
        spellings = (
            "Account: main",
            "- Account: main",
            "**Account**: main",
            "- **Account:** `main`",
            "- Account: main (label account:main)",
            "- **Account:** `main` (the label account:main)",
        )
        for text in spellings:
            self.assertEqual(claims.claim_account(text + "\nPlan: x\n"), "main", text)
        self.assertEqual(claims.claim_account("Plan: nothing named\n"), "unnamed")
        self.assertEqual(claims.claim_account(CLAIM_TEXT), "main")

    def test_claim_files_reads_the_list_under_its_heading_only(self):
        self.assertEqual(claims.claim_files(CLAIM_TEXT), ["docs/a.md", "docs/b.md"])
        self.assertEqual(claims.claim_files("- Account: main\n- Branch: claude/1\n"), [])
        two_lists = "## Files to touch\n\n- x.md\n\n## Notes\n\n- not a file\n"
        self.assertEqual(claims.claim_files(two_lists), ["x.md"])


class Times(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(claims.parse_time("2026-09-22T08:00:00Z"), datetime(2026, 9, 22, 8, tzinfo=timezone.utc))
        self.assertEqual(claims.parse_time("2026-09-22T10:00:00+02:00"), datetime(2026, 9, 22, 8, tzinfo=timezone.utc))

    def test_a_datetime_passes_through_and_a_naive_one_is_utc(self):
        aware = datetime(2026, 9, 22, 8, tzinfo=timezone.utc)
        self.assertIs(claims.parse_time(aware), aware)
        self.assertEqual(claims.parse_time(datetime(2026, 9, 22, 8)), aware)
        self.assertEqual(claims.parse_time("2026-09-22T08:00:00"), aware)

    def test_idle_means_strictly_more_than_the_wait(self):
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(claims.is_idle("2026-09-22T11:59:59Z", now))
        self.assertFalse(claims.is_idle("2026-09-22T12:00:00Z", now))
        self.assertFalse(claims.is_idle(now, now))
        self.assertTrue(claims.is_idle("2026-09-23T10:00:00Z", now, timedelta(hours=1)))
        self.assertEqual(claims.IDLE_AFTER, timedelta(hours=24))


class Release(unittest.TestCase):
    NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

    def test_claims_with_no_commit_for_a_day_are_due_in_issue_order(self):
        last_commits = {
            "claude/100": "2026-09-20T10:00:00Z",
            "claude/9": "2026-09-21T10:00:00Z",
            "claude/12": "2026-09-23T11:00:00Z",
            "claude/x": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "claude/8": self.NOW - timedelta(hours=24),
        }
        self.assertEqual(claims.release_due(last_commits, self.NOW), ["claude/9", "claude/100", "claude/x"])

    def test_issue_numbers_as_keys_work_too(self):
        last_commits = {"12": "2026-09-20T10:00:00Z", 3: "2026-09-20T10:00:00Z", "7": "2026-09-23T11:30:00Z"}
        self.assertEqual(claims.release_due(last_commits, self.NOW), [3, "12"])

    def test_pairs_work_as_well_as_a_mapping(self):
        pairs = [("claude/12", "2026-09-20T10:00:00Z"), ("claude/3", "2026-09-20T10:00:00Z"), ("claude/7", self.NOW)]
        self.assertEqual(claims.release_due(pairs, self.NOW), ["claude/3", "claude/12"])
        self.assertEqual(claims.release_due(iter(pairs), self.NOW), ["claude/3", "claude/12"])

    def test_the_wait_can_be_set(self):
        last_commits = {"claude/1": "2026-09-23T10:00:00Z"}
        self.assertEqual(claims.release_due(last_commits, self.NOW), [])
        self.assertEqual(claims.release_due(last_commits, self.NOW, timedelta(hours=1)), ["claude/1"])
        self.assertEqual(claims.release_due({}, self.NOW), [])


if __name__ == "__main__":
    unittest.main()
