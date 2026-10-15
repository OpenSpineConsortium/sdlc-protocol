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


def fork_pull():
    """sam's pull request #50, marked ready, from a branch of a fork named
    claude/12 there (F17)."""
    return board.Pull(50, "Fix the board", "claude/12", "ddddddd4444444", "clean", "2026-09-19T12:00:00Z",
                      ["docs/agents/README.md"], draft=False, fork=True)


def stranger_pull():
    """sam's pull request #50, marked ready, from claim 12's own branch,
    which the adapter marked as a stranger's (F19)."""
    return board.Pull(50, "Fix the board", "claude/12", "ddddddd4444444", "clean", "2026-09-19T12:00:00Z",
                      ["docs/agents/README.md"], draft=False, stranger=True)


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

    def test_a_ready_pull_request_from_a_fork_named_like_the_claim_keeps_no_claim_from_its_release(self):
        """F17: a stranger's pull request from a fork's claude/12 is no
        claim's, so marked ready it finishes nothing; the claim's own, marked
        ready, still does."""
        for own in (idle_claim(), idle_claim(pulls=[])):
            own.pulls.insert(0, fork_pull())
            self.assertEqual(coordinator.releases_due(own, NOW), ["12"])
        ready = idle_claim()
        ready.pulls[0].draft = False
        ready.pulls.insert(0, fork_pull())
        self.assertEqual(coordinator.releases_due(ready, NOW), [])

    def test_a_ready_pull_request_a_stranger_opened_from_the_claim_s_branch_keeps_no_claim_from_its_release(self):
        """F19: a pull request someone who may not write opened from
        claude/12 itself is no claim's, so marked ready it finishes nothing;
        the claim's own, marked ready, still does."""
        for own in (idle_claim(), idle_claim(pulls=[])):
            own.pulls.insert(0, stranger_pull())
            self.assertEqual(coordinator.releases_due(own, NOW), ["12"])
        ready = idle_claim()
        ready.pulls[0].draft = False
        ready.pulls.insert(0, stranger_pull())
        self.assertEqual(coordinator.releases_due(ready, NOW), [])


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

    def test_the_release_names_the_label_s_worker_where_the_claim_file_names_nobody(self):
        """A Worker line with nothing after it but markup or a note names
        nobody (F18's review): the release then names the worker the
        issue's label names, as it does where the file was not read, and
        says nobody named one where no label does either; until the review
        such a file named "", and the release said "held by "."""
        for line in ("- Worker: ", "- Worker: (to be filled)", "- **Worker:** __"):
            snap = idle_claim(claims={"12": CLAIM_TEXT.replace("- Worker: nightly", line)})
            self.assertEqual(coordinator.holder(snap, "12"), "nightly", line)
            self.assertIn("held by nightly,", coordinator.release_text(snap, "12", coordinator.Holding(1, "x", ())))
        snap = idle_claim(claims={"12": CLAIM_TEXT.replace("- Worker: nightly", "- Worker: ")},
                          claim_issues={"12": {"state": "open", "labels": ["claimed", "todo"]}})
        self.assertEqual(coordinator.holder(snap, "12"), "a worker nobody named")

    def test_the_release_names_the_claim_s_own_pull_request_never_a_fork_s(self):
        """F17: the pull request a release names is the claim's own, whatever
        a fork's branch is called."""
        snap = idle_claim()
        snap.pulls.insert(0, fork_pull())
        self.assertIn("its pull request #40 is still a draft", coordinator.release_text(snap, "12", coordinator.Holding()))
        alone = idle_claim(pulls=[])
        alone.pulls.append(fork_pull())
        self.assertIn("and it has no pull request, so", coordinator.release_text(alone, "12", coordinator.Holding()))

    def test_the_release_names_the_claim_s_own_pull_request_never_a_stranger_s(self):
        """F19: the pull request a release names is the claim's own, never
        one a stranger opened from the claim's branch."""
        snap = idle_claim()
        snap.pulls.insert(0, stranger_pull())
        self.assertIn("its pull request #40 is still a draft", coordinator.release_text(snap, "12", coordinator.Holding()))
        alone = idle_claim(pulls=[])
        alone.pulls.append(stranger_pull())
        self.assertIn("and it has no pull request, so", coordinator.release_text(alone, "12", coordinator.Holding()))

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

    def test_a_bare_worker_prefix_stays_on(self):
        """F15's fourth review: a bare worker: names no worker, so a release
        leaves it on, as the board and the spec read it."""
        snap = idle_claim()
        snap.claim_issues["12"].labels = ["claimed", "todo", "worker:", "worker:nightly"]
        self.assertEqual(coordinator.labels_released(snap, "12"), ["claimed", "worker:nightly"])

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
        its worker labelled blocked gets todo too: the Queue lists it with
        blocked beside it, and nobody takes it, neither a worker reading
        the board (Pick) nor the clerk, until blocked comes off."""
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
    """A piece of open work is named by its identity, the issue for a
    claim's work and the pull request's number for one that is no claim's
    (board.identity): #40 is claim 12's, #42 claim 16's, and claim 17 has
    no pull request yet (F2)."""

    def test_each_piece_of_work_meets_the_others_with_their_shared_files_in_landing_order(self):
        found = coordinator.meeting(meeting_work())
        self.assertEqual({identity: [(other.number, files) for other, files in others]
                          for identity, others in found.items()},
                         {12: [(42, ("a.md",))], 16: [(40, ("a.md",)), (17, ("b.md",))], 17: [(42, ("b.md",))]})

    def test_each_piece_is_told_once_on_its_identity_and_again_only_of_work_it_was_not_told_of(self):
        """F2: the marker names the identities of the work met, the comment
        goes on the piece's identity, and a claim that opens its pull
        request is told nothing again."""
        due = coordinator.overlap_comments_due(meeting_work(), {})
        self.assertEqual([(c.number, c.marker) for c in due], [
            (12, "<!-- sdlc-coordinator: overlap 16 -->"),
            (16, "<!-- sdlc-coordinator: overlap 12 17 -->"),
            (17, "<!-- sdlc-coordinator: overlap 16 -->"),
        ])
        told = {12: {16}, 16: {12}, 17: {16}}
        self.assertEqual([c.number for c in coordinator.overlap_comments_due(meeting_work(), told)], [16])
        told[16] = {12, 17, 99}
        self.assertEqual(coordinator.overlap_comments_due(meeting_work(), told), [])

    def test_a_marker_that_names_a_claim_s_pull_request_names_the_claim(self):
        """The markers written before F2's fix named a claim's work by its
        pull request's number once it had one: #40 read as claim 12 and
        #42 as claim 16, so nothing is told again at the fix."""
        told = {12: {42}, 16: {40, 17}, 17: {42}}
        self.assertEqual(coordinator.overlap_comments_due(meeting_work(), told), [])
        self.assertEqual(coordinator.identities(meeting_work()), {17: 17, 40: 12, 41: 41, 42: 16})

    def test_a_pull_request_that_is_no_claim_s_is_a_piece_of_its_own_told_on_itself(self):
        snap = meeting_work()
        snap.pulls[1].files = ["a.md"]
        due = {c.number: c for c in coordinator.overlap_comments_due(snap, {})}
        self.assertEqual(sorted(due), [12, 16, 17, 41])
        self.assertEqual(due[41].marker, "<!-- sdlc-coordinator: overlap 12 16 -->")
        self.assertTrue(due[41].text.startswith("overlap: other open work changes the same files as this pull "
                                                "request:\n"))
        self.assertEqual(due[12].marker, "<!-- sdlc-coordinator: overlap 16 41 -->")

    def test_two_pull_requests_of_one_claim_are_one_piece_that_names_each_file_once(self):
        """F2's review: GitHub opens a second pull request from one branch to
        another base, so claim 16's #42 and #43 are one piece: the files it
        shares are named once, the marker names each piece once, and the
        comment speaks of both pull requests and lands both."""
        snap = meeting_work()
        snap.pulls.append(board.Pull(43, "43", "claude/16", "", "clean", "2026-09-20T00:00:00Z", ["a.md"], True))
        found = coordinator.meeting(snap)
        self.assertEqual([(other.number, files) for other, files in found[12]], [(42, ("a.md",)), (43, ("a.md",))])
        self.assertEqual([(other.number, files) for other, files in found[16]], [(40, ("a.md",)), (17, ("b.md",))])
        due = {c.number: c for c in coordinator.overlap_comments_due(snap, {})}
        self.assertEqual(due[12].marker, "<!-- sdlc-coordinator: overlap 16 -->")
        self.assertTrue(due[16].text.startswith(
            "overlap: other open work changes the same files as this claim's pull requests #42 and #43:\n"
            "- #40, a pull request marked ready: `a.md`\n"
            "- #17, a claim with no pull request yet: `b.md`\n"), due[16].text)
        self.assertIn("Land them in this order: #40, #17, #42, #43:", due[16].text)
        self.assertEqual(coordinator.told_places(snap, 16), [42, 43])

    def test_a_file_the_pull_requests_of_one_claim_alone_change_meets_nothing(self):
        """F2's second review: the Overlaps list a file more than one piece
        changes, and a claim's two pull requests are one piece."""
        snap = meeting_work()
        snap.pulls[2].files = ["a.md", "b.md", "z.md"]
        snap.pulls.append(board.Pull(43, "43", "claude/16", "", "clean", "2026-09-20T00:00:00Z", ["z.md"], True))
        self.assertEqual(sorted(board.overlaps(snap)), ["a.md", "b.md"])
        self.assertEqual(sorted(coordinator.meeting(snap)), [12, 16, 17])

    def test_a_claim_s_own_open_pull_requests_are_where_it_was_told_before_the_fix(self):
        """F2's review: the coordinator told a claim's work on its pull
        request before the fix, so those markers count as the issue's; a
        pull request that is no claim's has no place but itself."""
        snap = meeting_work()
        self.assertEqual([coordinator.told_places(snap, n) for n in (12, 16, 17, 41)], [[40], [42], [], []])

    def test_a_marker_counts_for_a_claim_only_from_the_push_that_made_its_branch(self):
        """F2's review: an issue's number names each claim on it in turn.
        Claim 12 was released and claimed anew at 10:00, so the markers
        from before, on issue 12 and on issue 16 naming 12, tell the new
        claim nothing, and both are told again; a marker from after
        counts, and a claim GitHub does not date takes every marker."""
        snap = meeting_work()
        snap.began = {"claude/12": "2026-09-20T10:00:00Z", "claude/16": "2026-09-20T08:00:00Z"}
        before, after = "2026-09-20T09:00:00Z", "2026-09-20T10:00:00Z"
        told = {12: {16: before}, 16: {12: before, 17: after}, 17: {16: after}}
        self.assertEqual([c.number for c in coordinator.overlap_comments_due(snap, told)], [12, 16])
        told = {12: {16: after}, 16: {12: after, 17: after}, 17: {16: after}}
        self.assertEqual(coordinator.overlap_comments_due(snap, told), [])
        self.assertEqual(coordinator.claims_made(snap),
                         {12: board.began_at(after), 16: board.began_at("2026-09-20T08:00:00Z")})
        snap.began = {}
        told = {12: {16: before}, 16: {12: before, 17: before}, 17: {16: before}}
        self.assertEqual(coordinator.overlap_comments_due(snap, told), [])

    def test_told_times_keep_the_latest_marker_naming_each_number(self):
        """A marker with no time, or one that does not read as a time,
        counts as the latest, so the work it names is never told again for
        GitHub's odd answer."""
        bot = {"author_association": "NONE", "user": {"login": "github-actions[bot]"}}
        comments = [dict(bot, body="<!-- sdlc-coordinator: overlap 12 16 -->", created_at="2026-09-20T09:00:00Z"),
                    dict(bot, body="<!-- sdlc-coordinator: overlap 16 -->", created_at="2026-09-20T10:00:00Z"),
                    dict(bot, body="<!-- sdlc-coordinator: overlap 12 -->", created_at="2026-09-20T08:00:00Z"),
                    dict(bot, body="<!-- sdlc-coordinator: overlap 17 -->"),
                    dict(bot, body="overlap: no marker", created_at="2026-09-20T11:00:00Z")]
        self.assertEqual(coordinator.told_times(comments),
                         {12: "2026-09-20T09:00:00Z", 16: "2026-09-20T10:00:00Z", 17: ""})
        self.assertEqual(coordinator.merged_times({12: "2026-09-20T09:00:00Z"}, {12: "", 16: "x"}), {12: "", 16: "x"})

    def test_the_comment_names_the_other_work_its_files_and_the_order(self):
        due = {c.number: c for c in coordinator.overlap_comments_due(meeting_work(), {})}
        self.assertEqual(due[16].text, (
            "overlap: other open work changes the same files as this claim's pull request #42:\n"
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
        self.assertIn("Land them in this order: #40, #42, #17:", due[16].text)
        self.assertEqual([other.number for other, _ in coordinator.meeting(snap)[16]], [40, 17])

    def test_a_blocker_is_said_and_lands_first(self):
        snap = meeting_work()
        snap.claim_issues["17"] = board.ClaimIssue("open", ["blocker", "claimed"])
        due = {c.number: c for c in coordinator.overlap_comments_due(snap, {})}
        self.assertIn("- #17, a claim with no pull request yet, for a blocker: `b.md`", due[16].text)
        self.assertIn("Land them in this order: #17, #40, #42:", due[16].text)

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
