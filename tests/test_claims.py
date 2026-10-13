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

    def test_only_the_name_branch_of_writes_names_an_issue(self):
        # GitHub's lock on a claim is the first push of a name, so claude/03
        # beside claude/3 would be a second claim on #3: only the issue's
        # number as GitHub writes it, with no leading zero, names it.
        for name in ("claude/03", "claude/007", "claude/00", "claude/0123456789"):
            self.assertIsNone(claims.number_of(name), name)
        for name in ("claude/3", "claude/30", "claude/0", "claude/123456789"):
            self.assertEqual(claims.branch_of(claims.number_of(name)), name)
        self.assertIsNone(claims.number_of("agent/07", "agent/"))

    def test_a_name_of_digits_that_names_no_issue_reads_as_a_claim(self):
        for name in ("claude/03", "claude/00", "claude/1234567890", "claude/\u0663", "claude/\u00b2", "claude/\uff13"):
            self.assertTrue(claims.reads_as_claim(name), name)
        for name in ("claude/3", "claude/0", "claude/x", "claude/03-notes", "claude/#3", "claude/", "main", "03"):
            self.assertFalse(claims.reads_as_claim(name), name)

    def test_the_number_a_name_of_digits_means(self):
        self.assertEqual([claims.meant_number(key) for key in ("03", "00", "\u0663", "\u00b2", "\uff13\uff10")],
                         [3, 0, 3, 2, 30])
        for key in ("1234567890", "9" * 5000, "x", "", "3a"):
            self.assertIsNone(claims.meant_number(key), key[:12])

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

    def test_a_key_in_digits_int_cannot_read_sorts_as_a_name_and_never_raises(self):
        # A superscript or a circled digit passes isdigit() but not int();
        # the board sorts its claims by this key, so one such branch name
        # stopped the whole page.
        keys = ["10", "\u00b2", "3", "\u27800", "x"]
        self.assertEqual(sorted(keys, key=claims.by_number), ["3", "10", "x", "\u00b2", "\u27800"])
        branches = ["claude/\u27800", "claude/9", "claude/\u00b2"]
        self.assertEqual(sorted(branches, key=claims.issue_order), ["claude/9", "claude/\u00b2", "claude/\u27800"])

    def test_only_ascii_digits_name_an_issue_and_no_branch_name_raises(self):
        # Another script's digits, which int() reads as a number, name no
        # issue either, and a run of digits longer than int() reads is no
        # number rather than an error.
        for name in ("claude/\u00b2", "claude/\u27800", "claude/\u0663", "claude/1234567890", "claude/" + "9" * 5000):
            self.assertIsNone(claims.number_of(name), name[:20])
            claims.issue_order(name)
        self.assertEqual(claims.number_of("claude/123456789"), 123456789)


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

    def test_a_worker_line_names_the_worker_as_an_account_line_did(self):
        for text in ("Worker: nightly", "- Worker: nightly", "- **Worker:** `nightly` (label worker:nightly)",
                     "worker: nightly"):
            self.assertEqual(claims.claim_worker(text + "\nPlan: x\n"), "nightly", text)
        self.assertEqual(claims.claim_worker("- Worker: a\n- Account: b\n"), "a")
        self.assertIs(claims.claim_account, claims.claim_worker)

    def test_the_plan_is_the_prose_under_its_heading(self):
        self.assertEqual(claims.claim_plan(CLAIM_TEXT), "Two sentences on what changes.")
        self.assertEqual(claims.claim_plan("## Plan\n\nFirst line,\nsecond line.\n\n## Files to touch\n\n- a\n"),
                         "First line, second line.")
        self.assertEqual(claims.claim_plan("- Worker: x\n"), "")
        self.assertEqual(claims.claim_plan(None), "")

    def test_claim_files_reads_the_list_under_its_heading_only(self):
        self.assertEqual(claims.claim_files(CLAIM_TEXT), ["docs/a.md", "docs/b.md"])
        self.assertEqual(claims.claim_files("- Account: main\n- Branch: claude/1\n"), [])
        two_lists = "## Files to touch\n\n- x.md\n\n## Notes\n\n- not a file\n"
        self.assertEqual(claims.claim_files(two_lists), ["x.md"])


class SaidFinished(unittest.TestCase):
    def comment(self, login, body, at):
        return {"user": {"login": login}, "body": body, "created_at": at}

    def test_the_authors_latest_comment_decides(self):
        said = self.comment("alice", "Finished; ready to mark.", "2026-09-18T13:00:00Z")
        other = self.comment("bob", "finished; ready to mark", "2026-09-18T14:00:00Z")
        later = self.comment("alice", "Wait, one more thing.", "2026-09-18T15:00:00Z")
        self.assertTrue(claims.said_finished([said, other], "alice"))
        self.assertFalse(claims.said_finished([said, later], "alice"))
        self.assertFalse(claims.said_finished([other], "alice"))
        self.assertFalse(claims.said_finished([said], None))
        self.assertFalse(claims.said_finished([], "alice"))
        self.assertEqual(claims.FINISHED_SIGNAL, "finished; ready to mark")


class IssueFiles(unittest.TestCase):
    def test_the_task_forms_answer_lists_a_path_a_line(self):
        body = "### Goal\n\nDo it.\n\n### Files likely touched\n\nsrc/a.py\n`src/b.py`\nsrc/a.py\n"
        self.assertEqual(claims.issue_files(body), ["src/a.py", "src/b.py"])

    def test_a_planned_tasks_list_stops_at_the_prose_after_it(self):
        body = "Epic: #1\n\n## Files to touch\n\n- api/x.py\n- `api/y.py`\n\nStay inside these files.\n"
        self.assertEqual(claims.issue_files(body), ["api/x.py", "api/y.py"])

    def test_no_answer_names_no_file(self):
        for body in ("### Files likely touched\n\n_No response_\n", "## Files to touch\n\n- (none named)\n",
                     "### Goal\n\nNothing about files.\n", "", None):
            self.assertEqual(claims.issue_files(body), [], body)


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

    def test_a_claim_already_released_is_not_due_again(self):
        # The coordinator runs every hour; a claim it released yesterday is
        # still idle today, and releasing it again would count it twice.
        last_commits = {"claude/5": "2026-09-20T10:00:00Z", "claude/6": "2026-09-20T10:00:00Z"}
        released = {"claude/5": "2026-09-21T11:00:00Z"}
        self.assertEqual(claims.release_due(last_commits, self.NOW, released=released), ["claude/6"])

    def test_a_claim_taken_up_again_after_its_release_is_due_once_it_idles_again(self):
        # Its holder came back after the release and went quiet again: the
        # old release no longer speaks for the branch.
        last_commits = {"claude/5": "2026-09-21T12:00:00Z"}
        released = {"claude/5": "2026-09-21T11:00:00Z"}
        self.assertEqual(claims.release_due(last_commits, self.NOW, released=released), ["claude/5"])


def comment(body, association="OWNER", created_at="2026-09-21T11:00:00Z"):
    """An issue comment as the REST API lists it."""
    return {"body": body, "author_association": association, "created_at": created_at}


class ReleaseComments(unittest.TestCase):
    RELEASE = claims.RELEASE_MARKER + "\nReleased: no commit on claude/5 for a day."

    def test_the_marker_is_the_coordinators(self):
        self.assertEqual(claims.RELEASE_MARKER, "<!-- sdlc-coordinator: claim-released -->")

    def test_a_release_starts_with_the_marker_and_comes_from_the_repository(self):
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            self.assertTrue(claims.is_release_comment(comment(self.RELEASE, association)), association)
        self.assertTrue(claims.is_release_comment(comment("\n  " + self.RELEASE)))
        self.assertTrue(claims.is_release_comment(comment(claims.RELEASE_MARKER)))

    def test_a_stranger_or_a_quote_releases_nothing(self):
        for association in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE", None):
            self.assertFalse(claims.is_release_comment(comment(self.RELEASE, association)), association)
        self.assertFalse(claims.is_release_comment(comment("> " + self.RELEASE)))
        self.assertFalse(claims.is_release_comment(comment("Why was this released?\n" + self.RELEASE)))
        self.assertFalse(claims.is_release_comment(comment(claims.RELEASE_MARKER + " and more on the line")))
        self.assertFalse(claims.is_release_comment(comment(None)))
        self.assertFalse(claims.is_release_comment({}))
        self.assertFalse(claims.is_release_comment(None))

    def test_the_repositorys_own_workflows_release_as_their_bot(self):
        # The coordinator, a step of the board's run, posts as the bot of
        # the workflow's GITHUB_TOKEN, which GitHub shows with no
        # association that vouches for it.
        bot = dict(comment(self.RELEASE, "NONE"), user={"login": "github-actions[bot]", "type": "Bot"})
        self.assertTrue(claims.is_release_comment(bot))
        other_app = dict(bot, user={"login": "some-app[bot]", "type": "Bot"})
        self.assertFalse(claims.is_release_comment(other_app))
        self.assertFalse(claims.is_release_comment(dict(bot, body="Thanks")))

    def test_an_author_the_association_does_not_vouch_for_is_asked_about(self):
        # A private member of the organization reads as a contributor to a
        # workflow's token; the adapter then asks whether they may write.
        writers = {"private-member"}
        asked = []

        def trusted(login):
            asked.append(login)
            return login in writers

        member = dict(comment(self.RELEASE, "CONTRIBUTOR"), user={"login": "private-member"})
        stranger = dict(comment(self.RELEASE, "NONE"), user={"login": "passer-by"})
        self.assertTrue(claims.is_release_comment(member, trusted))
        self.assertFalse(claims.is_release_comment(stranger, trusted))
        self.assertFalse(claims.is_release_comment(member))
        self.assertFalse(claims.is_release_comment(dict(member, user=None), trusted))
        self.assertTrue(claims.is_release_comment(comment(self.RELEASE, "OWNER"), trusted))
        self.assertFalse(claims.is_release_comment(dict(member, body="Thanks"), trusted))
        self.assertEqual(asked, ["private-member", "passer-by"])
        self.assertEqual(claims.latest_release([member, stranger], trusted), "2026-09-21T11:00:00Z")

    def test_the_marker_must_open_the_comment(self):
        self.assertTrue(claims.starts_with_release_marker(comment("\n" + self.RELEASE, "NONE")))
        self.assertFalse(claims.starts_with_release_marker(comment("> " + self.RELEASE)))
        self.assertFalse(claims.starts_with_release_marker(None))

    def test_the_latest_release_is_the_newest_release_comment(self):
        comments = [
            comment(self.RELEASE, created_at="2026-09-21T11:00:00Z"),
            comment(self.RELEASE, created_at="2026-09-22T09:00:00+02:00"),
            comment("Still on it.", created_at="2026-09-23T08:00:00Z"),
            comment(self.RELEASE, "NONE", created_at="2026-09-23T09:00:00Z"),
        ]
        self.assertEqual(claims.latest_release(comments), "2026-09-22T09:00:00+02:00")
        self.assertEqual(claims.latest_release(comments[2:]), "")
        self.assertEqual(claims.latest_release([]), "")
        self.assertEqual(claims.latest_release([comment(self.RELEASE, created_at=None)]), "")


class Released(unittest.TestCase):
    LAST_COMMIT = "2026-09-20T10:00:00Z"

    def test_a_release_frees_a_claim_that_was_idle_when_it_came(self):
        self.assertTrue(claims.was_released("2026-09-21T10:00:01Z", self.LAST_COMMIT))
        self.assertTrue(claims.was_released(datetime(2026, 9, 25, tzinfo=timezone.utc), self.LAST_COMMIT))

    def test_a_release_made_too_soon_frees_nothing(self):
        self.assertFalse(claims.was_released("2026-09-21T10:00:00Z", self.LAST_COMMIT))
        self.assertFalse(claims.was_released("2026-09-20T12:00:00Z", self.LAST_COMMIT))
        self.assertTrue(claims.was_released("2026-09-20T12:00:00Z", self.LAST_COMMIT, timedelta(hours=1)))

    def test_a_commit_after_the_release_ends_it(self):
        self.assertFalse(claims.was_released("2026-09-21T11:00:00Z", "2026-09-21T11:30:00Z"))

    def test_with_either_time_unknown_nothing_is_released(self):
        self.assertFalse(claims.was_released("", self.LAST_COMMIT))
        self.assertFalse(claims.was_released("2026-09-25T10:00:00Z", ""))

    def test_the_released_branch_keeps_the_issue_and_names_the_head(self):
        self.assertEqual(claims.released_branch("5", "abcdef0123456789"), "released/5-abcdef0")
        self.assertEqual(claims.released_branch(12, "0123abc"), "released/12-0123abc")
        self.assertFalse(claims.is_claim_branch(claims.released_branch("5", "abcdef0")))


if __name__ == "__main__":
    unittest.main()
