"""The coordinator's rules: which claims are released and what the release
says, and what open work that meets other work in a file is told. No
network: snapshots stand in for GitHub, and the adapter's calls are
tests/test_github.py's."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims, coordinator  # noqa: E402

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
HEAD = "aaaaaaa1111111"
# Who the coordinator's comments are by: the repository's own workflows.
BOT = {"author_association": "NONE", "user": {"login": "github-actions[bot]"}}
CLAIM_TEXT = ("# Claim: #12\n\n- Issue: https://github.com/octo/widgets/issues/12\n- Worker: nightly\n"
              "- Branch: claude/12\n\n## Plan\n\nDescribe the board,\nsection by section.\n\n"
              "## Files to touch\n\n- docs/agents/README.md\n")


def idle_claim(**changes):
    """Issue 12, claimed by nightly, whose branch claude/12 has had no commit
    since two days before NOW, with a draft pull request #40."""
    snap = {
        "default_branch": "main",
        "issues": [{"number": 12, "title": "Describe the board",
                    "labels": ["claimed", "todo", "worker:nightly"]}],
        "pulls": [{"number": 40, "title": "Describe the board", "head": {"ref": "claude/12", "sha": HEAD},
                   "mergeable_state": "clean", "updated_at": "2026-09-18T12:00:00Z", "draft": True,
                   "files": ["docs/agents/README.md", "docs/agents/claims/12.md"]}],
        "branches": ["claude/12"],
        "claims": {"12": CLAIM_TEXT},
        "claim_issues": {"12": {"state": "open", "labels": ["claimed", "todo", "worker:nightly"]}},
        "releases": {"12": {"released_at": "", "sha": HEAD, "committed_at": "2026-09-18T12:00:00Z"}},
    }
    snap.update(changes)
    return board.Snapshot.from_dict(snap)


class ReleasesDue(unittest.TestCase):
    def test_a_claim_idle_for_more_than_a_day_is_due(self):
        self.assertEqual(coordinator.releases_due(idle_claim(), NOW), ["12"])

    def test_a_claim_with_a_commit_within_the_day_is_not(self):
        snap = idle_claim(releases={"12": {"released_at": "", "sha": HEAD, "committed_at": "2026-09-20T01:00:00Z"}})
        self.assertEqual(coordinator.releases_due(snap, NOW), [])

    def test_a_finished_claim_waits_for_review_however_long(self):
        ready = idle_claim()
        ready.pulls[0].draft = False
        in_review = idle_claim()
        in_review.claim_issues["12"].labels.append("in-review")
        for name, snap in (("ready", ready), ("in-review", in_review)):
            self.assertEqual(coordinator.releases_due(snap, NOW), [], name)

    def test_a_closed_issue_or_a_missing_branch_or_head_is_no_candidate(self):
        closed = idle_claim()
        closed.claim_issues["12"].state = "closed"
        gone = idle_claim(branches=[])
        headless = idle_claim(releases={"12": {"released_at": "", "sha": "", "committed_at": "2026-09-18T12:00:00Z"}})
        untimed = idle_claim(releases={"12": {"released_at": "", "sha": HEAD, "committed_at": ""}})
        for name, snap in (("closed", closed), ("gone", gone), ("headless", headless), ("untimed", untimed)):
            self.assertEqual(coordinator.releases_due(snap, NOW), [], name)

    def test_a_claim_already_released_is_not_released_again_but_one_released_too_soon_is(self):
        done = idle_claim(releases={"12": {"released_at": "2026-09-19T13:00:00Z", "sha": HEAD,
                                           "committed_at": "2026-09-18T12:00:00Z"}})
        self.assertEqual(coordinator.releases_due(done, NOW), [])
        too_soon = idle_claim(releases={"12": {"released_at": "2026-09-18T20:00:00Z", "sha": HEAD,
                                               "committed_at": "2026-09-18T12:00:00Z"}})
        self.assertEqual(coordinator.releases_due(too_soon, NOW), ["12"])

    def test_a_claim_with_no_pull_request_is_due_too(self):
        self.assertEqual(coordinator.releases_due(idle_claim(pulls=[]), NOW), ["12"])


class ReleaseText(unittest.TestCase):
    def test_the_release_says_why_whose_what_and_how_to_carry_on(self):
        holding = coordinator.Holding(3, "Describe the tables", ("docs/agents/README.md", "docs/agents/claims/12.md"))
        text = coordinator.release_text(idle_claim(), "12", holding)
        lines = text.splitlines()
        self.assertEqual(lines[0], claims.RELEASE_MARKER)
        self.assertTrue(claims.starts_with_release_marker({"body": text}))
        self.assertEqual(lines[1], (
            "released: the claim on this issue, held by nightly, has had no commit on claude/12 since "
            "2026-09-18T12:00:00Z, more than 24 hours, and its pull request #40 is still a draft, so the "
            "coordinator released it. The board renames the branch to released/12-aaaaaaa, which keeps the "
            "work, and the issue goes back to the queue."))
        self.assertIn('What the branch holds: 3 commits ahead of main, the newest "Describe the tables"; it '
                      'changes `docs/agents/README.md`.', text)
        self.assertIn("The claim's plan: Describe the board, section by section.", text)
        self.assertIn("a new claude/12 from main, and bring the commits in from released/12-aaaaaaa", text)
        self.assertTrue(text.endswith("put the label claimed back on the issue.\n"))

    def test_what_a_release_says_without_a_pull_request_a_comparison_or_a_claim_file(self):
        snap = idle_claim(pulls=[], claims={"12": None})
        text = coordinator.release_text(snap, "12", coordinator.Holding())
        self.assertIn("held by nightly,", text)
        self.assertIn("and it has no pull request, so", text)
        self.assertIn("GitHub would not compare the branch with the default branch", text)
        self.assertIn("The claim file states no plan.", text)
        nobody = idle_claim(pulls=[], claims={"12": None})
        nobody.claim_issues["12"].labels = ["todo"]
        self.assertIn("held by a worker nobody named,", coordinator.release_text(nobody, "12", coordinator.Holding()))

    def test_a_release_of_a_claim_whose_file_github_gave_no_text_of_says_where_the_plan_is(self):
        snap = idle_claim(claims={"12": board.UNREAD_CLAIM})
        text = coordinator.release_text(snap, "12", coordinator.Holding())
        self.assertIn("held by nightly,", text)
        self.assertIn("The claim file is over 1 MB, more than GitHub gives the text of, so its plan is not quoted; "
                      "docs/agents/claims/12.md on released/12-aaaaaaa has it.", text)
        self.assertNotIn("states no plan", text)

    def test_a_branch_with_only_its_claim_file_and_a_long_one_are_both_said_plainly(self):
        only = coordinator.Holding(1, "Claim #12", ("docs/agents/claims/12.md",))
        self.assertIn("1 commit ahead of main, the newest \"Claim #12\"; it changes nothing but its claim file.",
                      coordinator.release_text(idle_claim(), "12", only))
        many = coordinator.Holding(9, "", tuple(f"f{n:02d}.md" for n in range(25)))
        text = coordinator.release_text(idle_claim(), "12", many)
        self.assertIn("9 commits ahead of main; it changes `f00.md`", text)
        self.assertIn("`f19.md` and 5 more.", text)

    def test_what_a_release_quotes_is_cut_so_github_takes_the_comment_whatever_the_claim_holds(self):
        # GitHub refuses a comment over 65,536 characters, and a release
        # whose comment is refused never happens.
        huge = "x" * 100000
        snap = idle_claim(claims={"12": f"- Worker: {huge}\n\n## Plan\n\n{huge}\n"})
        holding = coordinator.Holding(3, huge, tuple(f"{n:02d}{huge}" for n in range(25)))
        text = coordinator.release_text(snap, "12", holding)
        self.assertLess(len(text), 65536 // 5)
        self.assertIn(f"held by {'x' * 200}..., has had no commit", text)
        self.assertIn(f'the newest "{"x" * 200}..."', text)
        self.assertIn(f"`00{'x' * 198}...`", text)
        self.assertIn("The claim's plan, cut short (docs/agents/claims/12.md on released/12-aaaaaaa has it whole): ",
                      text)

    def test_the_labels_taken_off_are_claimed_and_every_workers(self):
        snap = idle_claim()
        snap.claim_issues["12"].labels = ["account:main", "blocker", "claimed", "todo", "worker:nightly"]
        self.assertEqual(coordinator.labels_released(snap, "12"), ["account:main", "claimed", "worker:nightly"])

    def test_after_the_release_the_board_counts_it_and_the_queue_has_the_issue(self):
        snap = idle_claim()
        coordinator.after_release(snap, "12", "2026-09-20T12:00:05Z", ["claimed", "worker:nightly"])
        self.assertEqual(snap.claim_issues["12"].labels, ["todo"])
        self.assertEqual(snap.issues[0].labels, ["todo"])
        self.assertEqual(board.released_keys(snap), {"12"})
        self.assertIn("| #12 | Describe the board | todo |", board.render(snap).split(board.QUEUE_HEADING)[1])

    def test_a_release_puts_todo_on_an_issue_that_carries_none_of_todo_epic_and_blocker(self):
        """F15: the Queue lists only an issue labelled todo, and a task a
        worker's label alone gave carries none, so until the fix its
        released issue was shown nowhere; an epic gets none, since the
        Queue never lists one, and a blocker none, since the board lists it
        under Blockers and the clerk deals it first without todo. A task
        its worker labelled blocked gets todo, so the Queue lists it once
        the coordinator has settled what it needs."""
        snap = idle_claim()
        for names, put_on in ((["claimed", "worker:nightly"], ["todo"]), (["claimed", "todo", "worker:nightly"], []),
                              (["claimed", "epic", "worker:nightly"], []), (["blocker", "claimed"], []),
                              (["blocked", "claimed", "worker:nightly"], ["todo"]), ([], ["todo"])):
            with self.subTest(labels=names):
                snap.claim_issues["12"].labels = names
                self.assertEqual(coordinator.labels_put_on(snap, "12"), put_on)
        self.assertEqual(coordinator.labels_put_on(snap, "99"), [])

    def test_after_a_release_that_put_todo_on_the_queue_has_the_issue_given_by_a_label_alone(self):
        snap = idle_claim()
        snap.claim_issues["12"].labels = ["claimed", "worker:nightly"]
        snap.issues[0].labels = ["claimed", "worker:nightly"]
        self.assertNotIn("#12", board.render(snap).split(board.QUEUE_HEADING)[1])
        coordinator.after_release(snap, "12", "2026-09-20T12:00:05Z", ["claimed", "worker:nightly"], ["todo"])
        self.assertEqual(snap.claim_issues["12"].labels, ["todo"])
        self.assertEqual(snap.issues[0].labels, ["todo"])
        self.assertIn("| #12 | Describe the board | todo |", board.render(snap).split(board.QUEUE_HEADING)[1])
        self.assertEqual(coordinator.relabelled(["todo", "blocker"], ["blocker"], ["todo"]), ["todo"])


def meeting_work():
    """Three pull requests and a claim without one: #40 (ready) and #42
    (draft) both change a.md, #42 and the claim on issue 17 both plan b.md,
    #41 meets nobody."""
    def pull(number, branch, files, draft):
        return {"number": number, "title": str(number), "head": {"ref": branch}, "mergeable_state": "clean",
                "updated_at": "2026-09-20T00:00:00Z", "files": files, "draft": draft}
    return board.Snapshot.from_dict({
        "pulls": [pull(40, "claude/12", ["a.md"], False), pull(41, "dependabot/x", ["c.md"], False),
                  pull(42, "claude/16", ["a.md", "b.md"], True)],
        "branches": ["claude/12", "claude/16", "claude/17"],
        "claims": {"17": "## Files to touch\n\n- b.md\n"},
    })


class Overlaps(unittest.TestCase):
    def test_each_piece_of_work_meets_the_others_with_their_shared_files_in_landing_order(self):
        found = coordinator.meeting(meeting_work())
        self.assertEqual({number: [(other.number, files) for other, files in others]
                          for number, others in found.items()},
                         {17: [(42, ("b.md",))], 40: [(42, ("a.md",))], 42: [(40, ("a.md",)), (17, ("b.md",))]})

    def test_each_piece_is_told_once_and_again_only_of_work_it_was_not_told_of(self):
        due = coordinator.overlap_comments_due(meeting_work(), {})
        self.assertEqual([(c.number, c.marker) for c in due], [
            (17, "<!-- sdlc-coordinator: overlap 42 -->"),
            (40, "<!-- sdlc-coordinator: overlap 42 -->"),
            (42, "<!-- sdlc-coordinator: overlap 17 40 -->"),
        ])
        told = {17: {42}, 40: {42}, 42: {40}}
        self.assertEqual([c.number for c in coordinator.overlap_comments_due(meeting_work(), told)], [42])
        told[42] = {17, 40, 99}
        self.assertEqual(coordinator.overlap_comments_due(meeting_work(), told), [])

    def test_the_comment_names_the_other_work_its_files_and_the_order(self):
        due = {c.number: c for c in coordinator.overlap_comments_due(meeting_work(), {})}
        self.assertEqual(due[42].text, (
            "overlap: other open work changes the same files as this pull request:\n"
            "- #40, a pull request marked ready: `a.md`\n"
            "- #17, a claim with no pull request yet: `b.md`\n"
            "\n"
            "Land them in this order: #40, #17, #42: a blocker first, then work marked ready, then the oldest. "
            "Each one after the first brings the default branch in once the one before it has landed, and the "
            "board's Overlaps section shows the order as it changes.\n"))
        self.assertTrue(due[17].text.startswith("overlap: other open work changes the same files as this claim:\n"
                                                "- #42, a draft pull request: `b.md`\n"))

    def test_the_order_told_is_by_when_the_work_began(self):
        """F13: #42's branch was made before the claim on #17, so #42 lands
        first, though its number is the higher."""
        snap = meeting_work()
        snap.began = {"claude/16": "2026-09-20T08:00:00Z", "claude/17": "2026-09-20T09:00:00Z"}
        due = {c.number: c for c in coordinator.overlap_comments_due(snap, {})}
        self.assertIn("Land them in this order: #40, #42, #17:", due[42].text)
        self.assertEqual([other.number for other, _ in coordinator.meeting(snap)[42]], [40, 17])

    def test_a_blocker_is_said_and_lands_first(self):
        snap = meeting_work()
        snap.claim_issues["17"] = board.ClaimIssue("open", ["blocker", "claimed"])
        due = {c.number: c for c in coordinator.overlap_comments_due(snap, {})}
        self.assertIn("- #17, a claim with no pull request yet, for a blocker: `b.md`", due[42].text)
        self.assertIn("Land them in this order: #17, #40, #42:", due[42].text)

    def test_told_numbers_read_every_overlap_marker_and_nothing_else(self):
        comments = [dict(BOT, body="<!-- sdlc-coordinator: overlap 17 40 -->\noverlap: ..."),
                    dict(BOT, body="a person quoting <!-- sdlc-coordinator: overlap 3 -->"),
                    dict(BOT, body="<!-- sdlc-board: idle -->\nidle"), {"body": None}, {}]
        self.assertEqual(coordinator.told_numbers(comments), {17, 40})

    def test_a_marker_counts_only_from_an_author_the_repository_trusts(self):
        # Anyone may comment on a public repository's issues; a marker by a
        # stranger naming the other work would keep it from being told.
        stranger = {"body": "<!-- sdlc-coordinator: overlap 42 -->", "author_association": "NONE",
                    "user": {"login": "stranger"}}
        member = dict(stranger, author_association="MEMBER", body="<!-- sdlc-coordinator: overlap 17 -->")
        writer = dict(stranger, user={"login": "writer"}, body="<!-- sdlc-coordinator: overlap 40 -->")
        self.assertEqual(coordinator.told_numbers([stranger, member, writer]), {17})
        self.assertEqual(coordinator.told_numbers([stranger, member, writer], lambda login: login == "writer"),
                         {17, 40})

    def test_a_marker_whose_numbers_name_no_issue_counts_for_nothing_and_never_raises(self):
        # int() refuses more than 4,300 digits, and one such comment stopped
        # every board run before STATE.md was written.
        comments = [dict(BOT, body="<!-- sdlc-coordinator: overlap " + "1" * 4301 + " -->"),
                    dict(BOT, body="<!-- sdlc-coordinator: overlap 17 042 -->"),
                    dict(BOT, body="<!-- sdlc-coordinator: overlap 40 -->")]
        self.assertEqual(coordinator.told_numbers(comments), {40})


if __name__ == "__main__":
    unittest.main()
