"""The board renders the same bytes for the same snapshot and speaks only to
the pull requests the rules name. No network: the fixture stands in for the
API, and comments_due says what an adapter would post without posting it.
The rendering and rule tests are the template's, ported as they were, so
the page this module renders is byte for byte the page tools/board.py did.
A released claim leaves the page and its issue returns to the queue; the
last class shows it end to end over a real git remote, where another
account then claims the issue by the protocol's own steps."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims, coordinator  # noqa: E402
from sdlc_protocol.board import DueComment, Issue, Merged, Pull, Release, Snapshot  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
FIXTURE = FIXTURES / "board.json"
# A snapshot that walks every branch of the renderer, and the pages and
# comments the template's tools/board.py gave for these two snapshots,
# recorded from it once so this repository holds the template to its word
# without needing the template.
WIDE = FIXTURES / "board-wide.json"

# A claim file in the shape the protocol's claim template gives.
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


def snapshot():
    """The fixture as the dict a --dry-run run reads; render takes it as is."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Rendering(unittest.TestCase):
    def setUp(self):
        self.text = board.render(snapshot())

    def test_same_input_renders_the_same_bytes(self):
        self.assertEqual(board.render(snapshot()).encode(), self.text.encode())

    def test_the_layout(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[0], "# Board")
        self.assertEqual(lines[2], "As of commit 0123abc on main. Rewritten by .github/workflows/board.yml; do not edit by hand.")
        headings = (board.CLAIM_HEADING, board.PULL_HEADING, board.OVERLAP_HEADING, board.QUEUE_HEADING,
                    board.GIVEN_HEADING, board.BLOCKER_HEADING, board.MERGED_HEADING)
        self.assertEqual([line for line in lines if line.startswith("## ")], list(headings))
        self.assertIn("| Issue | Branch | Worker | PR | Files it touches |", lines)
        self.assertIn("| PR | Title | Branch | State (clean, behind, conflicting, blocked, unknown) | Last update |", lines)
        self.assertIn("| File | Open work, in the order it should land |", lines)
        self.assertIn("| Issue | Title | Labels |", lines)
        self.assertIn("| Issue | Title | Worker |", lines)
        self.assertTrue(self.text.endswith("|\n"))
        self.assertNotIn("\n\n\n", self.text)

    def test_claims_name_the_worker_or_the_missing_file(self):
        self.assertIn("| #12 | claude/12 | main | #40 | docs/agents/README.md, docs/agents/STATE.md |", self.text)
        self.assertIn("| #13 | claude/13 | no claim file yet | #41 | .claude/skills/remember/SKILL.md |", self.text)
        self.assertNotIn("| main |", self.text.split(board.PULL_HEADING)[0].split("| #12")[0])

    def test_a_claim_file_github_gave_no_text_of_is_named_so_unless_a_label_names_the_worker(self):
        snap = snapshot()
        snap["claims"]["12"] = board.UNREAD_CLAIM
        snap["claim_issues"] = {"12": {"state": "open", "labels": ["claimed", "todo"]}}
        text = board.render(snap)
        self.assertIn("| #12 | claude/12 | claim file over 1 MB, not read | #40 |", text)
        snap["claim_issues"]["12"]["labels"].append("worker:nightly")
        self.assertIn("| #12 | claude/12 | nightly | #40 |", board.render(snap))
        again = Snapshot.from_json(Snapshot.from_dict(snap).to_json())
        self.assertIs(again.claims["12"], board.UNREAD_CLAIM)

    def test_a_claim_file_whose_worker_line_names_nobody_is_shown_as_unnamed(self):
        """A hand may leave the name off the line ("- Worker: ", "- Worker:
        (to be filled)"): until F18's review such a file named "", which the
        row showed as the worker; it names nobody now, as a file with no
        Worker line does, and the row says so rather than read a label,
        since the file is there to be corrected."""
        snap = snapshot()
        snap["claim_issues"] = {"12": {"state": "open", "labels": ["claimed", "todo", "worker:nightly"]}}
        for line in ("- Worker: ", "- Worker: (to be filled)", "- **Worker:** __", ""):
            snap["claims"]["12"] = f"# Claim: #12\n\n{line}\n\n## Plan\n\nDescribe the board.\n"
            self.assertIn("| #12 | claude/12 | unnamed | #40 |", board.render(snap), line)

    def test_a_claim_whose_issue_is_closed_says_so_and_asks_for_the_branch_to_go(self):
        snap = snapshot()
        snap["branches"].append("claude/3")
        snap["claim_issues"] = {"3": {"state": "closed", "labels": ["account:main", "claimed", "todo"]}}
        self.assertIn("| #3 | claude/3 | main | issue closed | delete the branch |", board.render(snap))

    def test_a_released_claim_file_takes_the_worker_from_the_issue_label(self):
        snap = snapshot()
        snap["claim_issues"] = {"13": {"state": "open", "labels": ["account:second", "claimed"]}}
        self.assertIn("| #13 | claude/13 | second | #41 | .claude/skills/remember/SKILL.md |", board.render(snap))
        snap["claim_issues"] = {"13": {"state": "open", "labels": ["agent:w2", "claimed", "worker:nightly"]}}
        self.assertIn("| #13 | claude/13 | nightly | #41 |", board.render(snap))

    def test_a_claim_file_names_its_worker_on_a_worker_line(self):
        snap = snapshot()
        snap["claims"]["12"] = snap["claims"]["12"].replace("- Account: main", "- Worker: nightly")
        self.assertIn("| #12 | claude/12 | nightly | #40 |", board.render(snap))

    def test_merged_rows_come_newest_first(self):
        snap = snapshot()
        snap["merged"] = [
            {"number": 30, "title": "Older", "head": {"ref": "claude/30"}, "merged_at": "2026-09-20T10:00:00Z"},
            {"number": 29, "title": "Newer", "head": {"ref": "claude/29"}, "merged_at": "2026-09-21T10:00:00Z"},
        ]
        text = board.render(snap)
        self.assertLess(text.index("| #29 |"), text.index("| #30 |"))

    def test_a_claim_without_a_pull_request_shows_the_files_it_names(self):
        snap = snapshot()
        snap["branches"].append("claude/16")
        snap["claims"]["16"] = CLAIM_TEXT
        snap["branches"].append("claude/17")
        snap["claims"]["17"] = CLAIM_TEXT.replace("## Files to touch\n\n- docs/b.md\n- `docs/a.md`\n", "")
        text = board.render(snap)
        self.assertIn("| #16 | claude/16 | main | (no pull request yet) | docs/a.md, docs/b.md |", text)
        self.assertIn("| #17 | claude/17 | main | (no pull request yet) | - |", text)

    def test_pull_rows_carry_the_state_word_and_the_escaped_title(self):
        self.assertIn("| #40 | Describe the board \\| its tables | claude/12 | behind | 2026-09-18T12:00:00Z |", self.text)
        self.assertIn("| #41 | Add the remember skill | claude/13 | clean | 2026-09-22T08:00:00Z |", self.text)

    def test_queue_and_blockers(self):
        queue = self.text.split(board.QUEUE_HEADING)[1].split(board.BLOCKER_HEADING)[0]
        self.assertIn("| #14 | Write the release notes | todo |", queue)
        self.assertNotIn("#15", queue)
        blockers = self.text.split(board.BLOCKER_HEADING)[1].split(board.MERGED_HEADING)[0]
        self.assertIn("| #15 | CI is red on every branch | blocker |", blockers)

    def test_merged_section(self):
        self.assertIn("| #39 | Seed the memory index | claude/11 | 2026-09-17T16:45:00Z |", self.text)

    def test_no_wall_clock_value(self):
        now = datetime.now(timezone.utc)
        for stamp in (now.strftime("%Y-%m-%dT%H"), now.strftime("%d %B %Y"), now.strftime("%Y-%m-%d ")):
            self.assertNotIn(stamp, self.text)

    def test_the_file_is_ascii_with_lf(self):
        self.text.encode("ascii")
        self.assertNotIn("\r", self.text)

    def test_an_empty_snapshot_keeps_every_table(self):
        text = board.render({"sha": "", "default_branch": "main", "issues": [], "pulls": [], "branches": [], "claims": {}, "merged": []})
        self.assertIn("As of commit unknown on main.", text)
        self.assertEqual(text.count("| (none) |"), 7)

    def test_the_intro_line_is_folded_to_ascii_too(self):
        snap = snapshot()
        snap["default_branch"] = "tr\u00fcnk"
        snap["sha"] = "\u00e9123abcdef"
        text = board.render(snap)
        self.assertIn("As of commit e123abc on trunk.", text)
        text.encode("ascii")

    def test_rows_sort_by_number_not_by_string(self):
        snap = snapshot()
        snap["pulls"].append({"number": 9, "title": "Nine", "head": {"ref": "claude/9"}, "mergeable_state": "clean", "updated_at": "2026-09-01T00:00:00Z", "files": []})
        snap["pulls"].append({"number": 100, "title": "Hundred", "head": {"ref": "claude/100"}, "mergeable_state": "clean", "updated_at": "2026-09-01T00:00:00Z", "files": []})
        pulls = board.render(snap).split(board.PULL_HEADING)[1].split(board.QUEUE_HEADING)[0]
        self.assertLess(pulls.index("| #9 |"), pulls.index("| #40 |"))
        self.assertLess(pulls.index("| #41 |"), pulls.index("| #100 |"))

    def test_a_non_ascii_title_is_folded(self):
        self.assertEqual(board.cell("caf\u00e9 \u2014 \u201cquoted\u201d"), "cafe  quoted")

    def test_a_claimed_todo_leaves_the_queue(self):
        snap = snapshot()
        snap["issues"].append({"number": 12, "title": "Claimed by branch", "labels": [{"name": "todo"}]})
        snap["issues"].append({"number": 16, "title": "Claimed by label", "labels": [{"name": "todo"}, {"name": "claimed"}]})
        queue = board.render(snap).split(board.QUEUE_HEADING)[1].split(board.BLOCKER_HEADING)[0]
        self.assertNotIn("#12", queue)
        self.assertNotIn("#16", queue)
        self.assertIn("#14", queue)

    def test_work_given_to_a_worker_stays_off_the_queue_and_shows_as_given(self):
        snap = snapshot()
        snap["issues"].append({"number": 20, "title": "An epic", "labels": [{"name": "todo"}, {"name": "epic"}]})
        snap["issues"].append({"number": 21, "title": "A member's task", "labels": [{"name": "todo"}, {"name": "agent:w1"}]})
        snap["issues"].append({"number": 22, "title": "For nightly", "labels": ["todo", "worker:nightly"]})
        snap["issues"].append({"number": 23, "title": "For main", "labels": ["account:main", "todo"]})
        snap["issues"].append({"number": 24, "title": "Taken", "labels": ["claimed", "todo", "worker:nightly"]})
        snap["issues"].append({"number": 25, "title": "Not queued", "labels": ["worker:nightly"]})
        snap["issues"].append({"number": 26, "title": "An epic for nightly", "labels": ["epic", "worker:nightly"]})
        page = board.render(snap)
        queue = page.split(board.QUEUE_HEADING)[1].split(board.GIVEN_HEADING)[0]
        given = page.split(board.GIVEN_HEADING)[1].split(board.BLOCKER_HEADING)[0]
        for number in ("#20", "#21", "#22", "#23", "#24", "#25", "#26"):
            self.assertNotIn(number, queue)
        self.assertIn("#14", queue)
        self.assertIn("| #21 | A member's task | w1 |", given)
        self.assertIn("| #22 | For nightly | nightly |", given)
        self.assertIn("| #23 | For main | main |", given)
        # An issue a worker's label names is that worker's whatever else it
        # is labelled (F14): with no todo, and an epic given to a worker.
        self.assertIn("| #25 | Not queued | nightly |", given)
        self.assertIn("| #26 | An epic for nightly | nightly |", given)
        for number in ("#20", "#24", "#14"):
            self.assertNotIn(number, given)

    def test_work_given_to_a_worker_leaves_the_given_rows_once_claimed_by_branch(self):
        snap = snapshot()
        snap["issues"].append({"number": 12, "title": "Given and claimed", "labels": ["todo", "worker:main"]})
        given = board.render(snap).split(board.GIVEN_HEADING)[1].split(board.BLOCKER_HEADING)[0]
        self.assertNotIn("#12", given)

    def test_the_default_branch_argument_wins_over_the_snapshots(self):
        self.assertIn("As of commit 0123abc on trunk.", board.render(snapshot(), "trunk"))
        self.assertIn("As of commit 0123abc on main.", board.render(snapshot(), None))


class Overlaps(unittest.TestCase):
    """The files more than one piece of open work changes, and the order
    that work should land in."""

    def overlapping(self):
        """The fixture with a third pull request that meets #40 in STATE.md
        and a claim with no pull request that plans to touch README.md."""
        snap = snapshot()
        snap["pulls"].append({"number": 42, "title": "Reword the board", "head": {"ref": "claude/16"},
                              "mergeable_state": "clean", "updated_at": "2026-09-22T09:00:00Z", "draft": True,
                              "files": ["docs/agents/STATE.md", "docs/agents/claims/16.md"]})
        snap["branches"] += ["claude/16", "claude/17"]
        snap["claims"]["17"] = CLAIM_TEXT.replace("- docs/b.md\n- `docs/a.md`", "- docs/agents/README.md")
        return snap

    def test_open_work_is_every_pull_request_and_every_claim_without_one(self):
        work = board.open_work(self.overlapping())
        self.assertEqual([(w.number, w.files, w.ready, w.is_pull) for w in work], [
            (17, ("docs/agents/README.md",), False, False),
            (40, ("docs/agents/README.md", "docs/agents/STATE.md"), True, True),
            (41, (".claude/skills/remember/SKILL.md",), True, True),
            (42, ("docs/agents/STATE.md",), False, True),
        ])

    def test_a_pull_request_that_changes_only_its_claim_file_counts_the_files_its_claim_plans(self):
        snap = snapshot()
        snap["pulls"][0]["files"] = ["docs/agents/claims/12.md"]
        self.assertEqual(board.open_work(snap)[0].files, ("docs/agents/README.md",))

    def test_each_file_lists_its_work_blocker_first_then_ready_then_oldest(self):
        found = board.overlaps(self.overlapping())
        self.assertEqual({path: [w.number for w in works] for path, works in found.items()},
                         {"docs/agents/README.md": [40, 17], "docs/agents/STATE.md": [40, 42]})
        snap = self.overlapping()
        snap["claim_issues"] = {"17": {"state": "open", "labels": ["blocker", "claimed"]}}
        self.assertEqual([w.number for w in board.overlaps(snap)["docs/agents/README.md"]], [17, 40])

    def test_the_page_shows_each_file_with_its_work_in_landing_order(self):
        section = board.render(self.overlapping()).split(board.OVERLAP_HEADING)[1].split(board.QUEUE_HEADING)[0]
        self.assertIn("| docs/agents/README.md | #40, #17 |", section)
        self.assertIn("| docs/agents/STATE.md | #40, #42 |", section)
        self.assertNotIn("claims/16.md", section)

    def drafts_meeting(self, began=None, opened="", fork=False):
        """The numbers of the work on docs/agents/STATE.md in landing order,
        in drafts_snapshot."""
        return [w.number for w in board.overlaps(self.drafts_snapshot(began, opened, fork))["docs/agents/STATE.md"]]

    def drafts_snapshot(self, began=None, opened="", fork=False):
        """#42, a draft from claude/16, and the claim on #17, with no pull
        request, both touching docs/agents/STATE.md, and #40 out of it:
        neither is a blocker or ready, so the oldest lands first. began is
        the snapshot's times by branch, opened #42's created_at, and fork
        puts #42's branch in a fork, as the API's head and base name it."""
        snap = self.overlapping()
        snap["pulls"][0]["files"] = ["docs/agents/README.md"]
        snap["claims"]["17"] = CLAIM_TEXT.replace("- docs/b.md\n- `docs/a.md`", "- docs/agents/STATE.md")
        if opened:
            snap["pulls"][2]["created_at"] = opened
        if fork:
            snap["pulls"][2]["head"]["repo"] = {"full_name": "someone/fork"}
            snap["pulls"][2]["base"] = {"ref": "main", "repo": {"full_name": "o/r"}}
        if began is not None:
            snap["began"] = began
        return snap

    def test_the_oldest_is_the_work_that_began_first_and_not_the_lowest_number(self):
        """F13: a claim is shown by its issue's number and a pull request by
        its own, two numbers that say nothing of which work began first;
        the time GitHub gives for the push that made each branch does."""
        self.assertEqual(self.drafts_meeting({"claude/16": "2026-09-22T08:00:00Z",
                                              "claude/17": "2026-09-22T09:00:00Z"}), [42, 17])
        self.assertEqual(self.drafts_meeting({"claude/16": "2026-09-22T10:00:00Z",
                                              "claude/17": "2026-09-22T09:00:00Z"}), [17, 42])
        self.assertEqual(self.drafts_meeting({"claude/16": "2026-09-22T08:00:00+00:00",
                                              "claude/17": "2026-09-22T09:00:00Z"}), [42, 17])

    def test_work_nothing_dates_counts_as_the_newest_and_the_number_breaks_a_tie(self):
        """In a live run only a claim with no pull request yet can be such
        work, since a pull request began at its opening where GitHub says
        nothing of its branch, and a claim opens its draft at once, so it
        is all but always the newest (F13's review); #42 has no created_at
        here, as a fixture written by hand may have none."""
        self.assertEqual(self.drafts_meeting(), [17, 42])
        self.assertEqual(self.drafts_meeting({"claude/16": "2026-09-22T08:00:00Z"}), [42, 17])
        self.assertEqual(self.drafts_meeting({"claude/17": "2026-09-22T08:00:00Z"}), [17, 42])
        same = "2026-09-22T08:00:00Z"
        self.assertEqual(self.drafts_meeting({"claude/16": same, "claude/17": same}), [17, 42])

    def test_a_pull_request_whose_branch_github_says_nothing_of_began_when_it_was_opened(self):
        claim = {"claude/17": "2026-09-22T09:00:00Z"}
        self.assertEqual(self.drafts_meeting(claim, opened="2026-09-22T08:30:00Z"), [42, 17])
        self.assertEqual(self.drafts_meeting(claim, opened="2026-09-22T09:30:00Z"), [17, 42])
        self.assertEqual(self.drafts_meeting(dict(claim, **{"claude/16": "2026-09-22T09:10:00Z"}),
                                             opened="2026-09-22T08:30:00Z"), [17, 42],
                         "the push that made the branch wins over the opening of its pull request")
        self.assertEqual(self.drafts_meeting(dict(claim, **{"claude/16": "yesterday"}), opened="2026-09-22T08:30:00Z"),
                         [42, 17], "a time for the branch that does not read as one says nothing, so the opening counts")

    def test_a_time_that_does_not_read_as_one_counts_as_nothing_said(self):
        for word in ("yesterday", "2026-13-40T00:00:00Z", "\u0661\u0662", 1695369600, ["2026-09-22T08:00:00Z"]):
            with self.subTest(word=word):
                self.assertEqual(self.drafts_meeting({"claude/16": word, "claude/17": "2026-09-22T09:00:00Z"}),
                                 [17, 42])
        for opened in (1695369600, ["2026-09-22T08:00:00Z"], {"at": "2026-09-22T08:00:00Z"}):
            with self.subTest(opened=opened):
                self.assertEqual(self.drafts_meeting({"claude/17": "2026-09-22T09:00:00Z"}, opened=opened), [17, 42])
                # The coordinator keys the work it tells by the Work itself,
                # so a value that is no text must not reach it whole (F13's
                # second review: a list stopped the run there).
                snap = self.drafts_snapshot({"claude/17": "2026-09-22T09:00:00Z"}, opened=opened)
                told = {n: [w.number for w, _ in others] for n, others in coordinator.meeting(snap).items()}
                self.assertEqual((told[17], told[16]), ([42], [17]))
                self.assertEqual(sorted(due.number for due in coordinator.overlap_comments_due(snap, {})),
                                 sorted(told))

    def test_a_pull_request_from_a_fork_began_when_it_was_opened_whatever_a_branch_here_of_its_name_says(self):
        """F13's review: the fork's branch is none of this repository's, so
        the push that made claude/16 here says nothing of when #42's work
        began."""
        began = {"claude/16": "2026-09-22T10:00:00Z", "claude/17": "2026-09-22T09:00:00Z"}
        self.assertEqual(self.drafts_meeting(began, opened="2026-09-22T08:30:00Z"), [17, 42])
        self.assertEqual(self.drafts_meeting(began, opened="2026-09-22T08:30:00Z", fork=True), [42, 17])
        self.assertEqual(self.drafts_meeting(began, fork=True), [17, 42])
        snap = self.overlapping()
        snap["pulls"][2]["head"]["repo"] = {"full_name": "someone/fork"}
        snap["pulls"][2]["base"] = {"ref": "main", "repo": {"full_name": "o/r"}}
        self.assertEqual([(w.number, w.branch) for w in board.open_work(snap) if w.is_pull],
                         [(40, "claude/12"), (41, "claude/13"), (42, "")])

    def test_a_fork_is_read_from_the_api_s_head_and_base_and_round_trips(self):
        def pull(head_repo="o/r", base_repo="o/r"):
            data = {"number": 5, "head": {"ref": "fix", "sha": "abc"}}
            if head_repo != "unnamed":
                data["head"]["repo"] = {"full_name": head_repo} if head_repo else None
            if base_repo:
                data["base"] = {"ref": "main", "repo": {"full_name": base_repo}}
            return Pull.from_dict(data)

        self.assertFalse(pull().fork)
        self.assertFalse(pull("O/R").fork)
        self.assertTrue(pull("someone/r").fork)
        self.assertTrue(pull(None).fork, "a fork since deleted, whose head names no repository")
        self.assertFalse(pull("unnamed").fork, "a head written by hand, which names no repository")
        self.assertFalse(pull("someone/r", base_repo="").fork)
        self.assertEqual(Pull.from_dict(pull("someone/r").to_dict()), pull("someone/r"))
        self.assertTrue(Pull.from_dict({"number": 5, "fork": True}).fork)
        self.assertNotIn("fork", pull().to_dict())
        ours, theirs = {"full_name": "o/r"}, {"ref": "main", "repo": {"full_name": "o/r"}}
        for head_repo, base in ((ours, "main"), ("o/r", theirs), (ours, {"ref": "main", "repo": "o/r"}),
                                ({"full_name": 7}, theirs), (ours, {"ref": "main", "repo": {"full_name": ["o/r"]}}),
                                ({}, theirs)):
            data = {"number": 5, "head": {"ref": "fix", "sha": "abc", "repo": head_repo}, "base": base}
            with self.subTest(head_repo=head_repo, base=base):
                # A shape written by hand that names no repository as the
                # API does says so by the key fork (F13's second review).
                self.assertFalse(Pull.from_dict(data).fork)
                self.assertTrue(Pull.from_dict(dict(data, fork=True)).fork)

    def test_when_work_began_round_trips_and_is_left_out_where_nothing_says(self):
        snap = Snapshot.from_dict(dict(snapshot(), began={"claude/12": "2026-09-22T08:00:00Z", "claude/13": ""}))
        self.assertEqual(snap.began, {"claude/12": "2026-09-22T08:00:00Z"})
        self.assertEqual(Snapshot.from_json(snap.to_json()), snap)
        self.assertEqual(Snapshot.from_dict(snapshot()).began, {})
        opened = Pull(5, "Five", "claude/5", "abc", "clean", created_at="2026-09-22T08:00:00Z")
        self.assertEqual(Pull.from_dict(opened.to_dict()), opened)
        self.assertNotIn("created_at", Pull(6, "Six", "claude/6", "def", "clean").to_dict())
        refused = Snapshot.from_dict(dict(snapshot(), began_refused={"claude/12": "HTTP 502"}))
        self.assertEqual(refused.began_refused, {"claude/12": "HTTP 502"})
        self.assertEqual(Snapshot.from_json(refused.to_json()), refused)
        self.assertNotIn("began_refused", Snapshot.from_dict(snapshot()).to_dict())

    def test_a_released_claims_pull_request_meets_nothing(self):
        snap = self.overlapping()
        snap["pulls"][2]["draft"] = True
        snap["claim_issues"] = {"16": {"state": "open", "labels": ["todo"]}}
        snap["releases"] = {"16": {"released_at": "2026-09-20T09:00:00Z", "sha": "ccccccc3333333",
                                   "committed_at": "2026-09-18T12:00:00Z"}}
        self.assertNotIn("docs/agents/STATE.md", board.overlaps(snap))


class SnapshotShape(unittest.TestCase):
    """The dataclasses hold what the fixture holds, and a snapshot that goes
    through JSON comes back equal and renders the same page."""

    def test_from_dict_reads_the_fixture(self):
        snap = Snapshot.from_dict(snapshot())
        self.assertEqual(snap.sha, "0123abcdef0123abcdef0123abcdef0123abcdef")
        self.assertEqual(snap.default_branch, "main")
        self.assertEqual([i.number for i in snap.issues], [14, 15])
        self.assertEqual(snap.issues[1].labels, ["blocker"])
        pull = snap.pulls[0]
        self.assertEqual((pull.number, pull.branch, pull.head_sha), (40, "claude/12", "aaaaaaa1111111"))
        self.assertEqual((pull.mergeable_state, pull.state), ("behind", "behind"))
        self.assertEqual(pull.files, ["docs/agents/README.md", "docs/agents/STATE.md"])
        self.assertFalse(pull.draft)
        self.assertTrue(Pull.from_dict({"number": 1, "draft": True}).draft)
        self.assertEqual(snap.branches, ["main", "claude/12", "claude/13"])
        self.assertIsNone(snap.claims["13"])
        self.assertTrue(snap.claims["12"].startswith("# Claim: #12"))
        self.assertEqual(snap.claim_issues, {})
        self.assertEqual((snap.merged[0].number, snap.merged[0].branch), (39, "claude/11"))

    def test_a_snapshot_round_trips_through_json(self):
        snap = Snapshot.from_dict(snapshot())
        snap.claim_issues["3"] = board.ClaimIssue("closed", ["account:main", "todo"])
        again = Snapshot.from_json(snap.to_json())
        self.assertEqual(again, snap)
        self.assertEqual(board.render(again), board.render(snap))
        # The issue behind a branch that is not there adds no row.
        self.assertEqual(board.render(json.loads(snap.to_json())), board.render(snapshot()))

    def test_the_dataclass_and_the_dict_render_the_same_page(self):
        self.assertEqual(board.render(Snapshot.from_dict(snapshot())), board.render(snapshot()))

    def test_branches_may_be_api_objects_and_labels_may_be_names(self):
        snap = Snapshot.from_dict({
            "branches": [{"name": "main"}, {"name": "claude/4"}],
            "issues": [{"number": 4, "title": "Four", "labels": ["todo", "account:main"]}],
            "claim_issues": {"4": {"state": "open", "labels": [{"name": "todo"}]}},
        })
        self.assertEqual(snap.branches, ["main", "claude/4"])
        self.assertEqual(snap.issues[0].labels, ["account:main", "todo"])
        self.assertEqual(snap.claim_issues["4"].labels, ["todo"])
        self.assertEqual(snap.sha, "")
        self.assertEqual(snap.default_branch, "main")

    def test_a_snapshot_built_by_hand_renders(self):
        snap = Snapshot(
            sha="abcdef0123456789",
            default_branch="main",
            issues=[Issue(7, "Seven", ["todo"])],
            pulls=[Pull(50, "Fifty", "claude/6", "ffff", "dirty", "2026-09-22T08:00:00Z", ["b.md", "a.md"])],
            branches=["claude/6"],
            claims={"6": None},
            merged=[Merged(49, "Landed", "claude/5", "2026-09-21T08:00:00Z")],
        )
        text = board.render(snap)
        self.assertIn("As of commit abcdef0 on main.", text)
        self.assertIn("| #6 | claude/6 | no claim file yet | #50 | a.md, b.md |", text)
        self.assertIn("| #50 | Fifty | claude/6 | conflicting | 2026-09-22T08:00:00Z |", text)
        self.assertIn("| #7 | Seven | todo |", text)
        self.assertIn("| #49 | Landed | claude/5 | 2026-09-21T08:00:00Z |", text)

    def test_labels_are_sorted_in_the_page_however_an_issue_was_built(self):
        snap = Snapshot(issues=[Issue(7, "Seven", ["todo", "blocker"])])
        self.assertIn("| #7 | Seven | blocker, todo |", board.render(snap))


class Rules(unittest.TestCase):
    def test_state_words(self):
        cases = (
            ("clean", "clean"), ("behind", "behind"), ("has_hooks", "clean"), ("unstable", "clean"),
            ("dirty", "conflicting"), ("blocked", "blocked"), ("unknown", "unknown"), ("draft", "unknown"), (None, "unknown"),
        )
        for given, word in cases:
            self.assertEqual(board.pr_state({"mergeable_state": given}), word, given)
            self.assertEqual(board.pr_state(Pull(1, mergeable_state=given)), word, given)

    def test_the_boards_own_words_pass_through(self):
        # An adapter that already speaks the board's words, like the
        # in-memory world, renders the same page; the template's board had
        # no such caller and read "conflicting" as unknown.
        for word in board.STATE_WORDS:
            self.assertEqual(board.state_word(word), word)

    def test_main_moved_picks_behind_and_dirty_only(self):
        pulls = [
            {"number": 1, "mergeable_state": "behind"},
            {"number": 2, "mergeable_state": "dirty"},
            {"number": 3, "mergeable_state": "clean"},
            {"number": 4, "mergeable_state": "unknown"},
            {"number": 5, "mergeable_state": "blocked"},
            {"number": 6, "mergeable_state": "unstable"},
        ]
        picked = [p["number"] for p in board.pulls_behind_main(pulls)]
        self.assertEqual(picked, [1, 2])

    def test_main_moved_text_and_marker(self):
        self.assertEqual(
            board.main_moved_text("behind", "main", "0123abc"),
            "main moved: this branch is behind main at 0123abc; rebase or merge main before the next push.",
        )
        self.assertEqual(
            board.main_moved_text("conflicting", "main", "0123abc"),
            "main moved: this branch is conflicting with main at 0123abc; rebase or merge main before the next push.",
        )
        self.assertEqual(board.MAIN_MOVED_MARKER.format(sha="0123abc"), "<!-- sdlc-board: main-moved 0123abc -->")

    def test_idle_picks_pulls_older_than_a_day(self):
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        pulls = [
            {"number": 1, "updated_at": "2026-09-22T11:00:00Z"},
            {"number": 2, "updated_at": "2026-09-22T13:00:00Z"},
            {"number": 3, "updated_at": (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        ]
        self.assertEqual([p["number"] for p in board.idle_pulls(pulls, now)], [1])


class Standing(unittest.TestCase):
    """How the board reads a pull request whose GitHub word says only that
    something else stops the merge, once the adapter has read how its branch
    stands (F9): a draft hears "main moved" like any other pull request."""

    def test_a_blocked_pull_takes_the_standing_main_moved_speaks_to(self):
        """GitHub answers "blocked" for a pull request a rule holds and says
        nothing of its branch, so the board read every such pull request as
        blocked and never told it "main moved" (F9). The adapter's standing
        says whether the branch is behind or conflicting, and that wins."""
        self.assertEqual(Pull(1, mergeable_state="blocked", standing="behind").state, "behind")
        self.assertEqual(Pull(1, mergeable_state="blocked", standing="conflicting").state, "conflicting")

    def test_a_blocked_pull_whose_branch_is_clean_stays_blocked(self):
        """A clean branch a rule holds is not behind, so the main-moved rule
        has nothing to say to it, and "blocked" says what still stops it
        where "clean" would say nothing does (F9)."""
        self.assertEqual(Pull(1, mergeable_state="blocked", standing="clean").state, "blocked")

    def test_a_draft_word_with_a_standing_reads_the_standing(self):
        """state_word reads GitHub's older answer "draft" as unknown, since
        the word says nothing of the branch (F9). With the standing read the
        board knows, so a clean draft reads clean rather than unknown and a
        draft behind reads behind."""
        self.assertEqual(board.state_word("draft"), "unknown")
        self.assertEqual(Pull(1, mergeable_state="draft", standing="clean").state, "clean")
        self.assertEqual(Pull(1, mergeable_state="draft", standing="behind").state, "behind")

    def test_a_pull_with_no_standing_reads_as_before(self):
        """A snapshot with no standing, the fixtures' and the memory world's
        with opaque_pulls off among them, gets the word state_word gave
        before F9, for every word it knows, so nothing that already
        rendered renders otherwise."""
        for given in (*board.STATE_WORDS, *board.GITHUB_STATES, "draft", None):
            pull = Pull(1, mergeable_state=given)
            self.assertIsNone(pull.standing)
            self.assertEqual(pull.state, board.state_word(given), given)

    def test_main_moved_reaches_a_blocked_pull_once_its_standing_is_read(self):
        """pulls_behind_main took only a GitHub word of behind or dirty, so a
        pull request GitHub called blocked, as it does while a required
        check is pending or red or a review is not yet given, a draft's
        included, never heard "main moved" (F9). With the standing read it
        does, as a dict or a Pull alike, and one whose branch is clean, or
        whose standing was not read, still does not."""
        pulls = [
            {"number": 1, "mergeable_state": "blocked", "standing": "behind"},
            {"number": 2, "mergeable_state": "blocked", "standing": "clean"},
            Pull(3, mergeable_state="blocked", standing="conflicting"),
            Pull(4, mergeable_state="blocked", standing="clean"),
            Pull(5, mergeable_state="blocked"),
            {"number": 6, "mergeable_state": "draft", "standing": "behind"},
        ]
        picked = [board.as_pull(p).number for p in board.pulls_behind_main(pulls)]
        self.assertEqual(picked, [1, 3, 6])

    def test_the_standing_is_written_only_when_set_and_read_back(self):
        """A snapshot goes through JSON and a Pull detail keeps the API's own
        shape (F9): a standing the adapter read comes back the same both
        ways, and one it did not read adds no key, so a fixture recorded
        before F9 is a fixture still."""
        read = Pull(1, "One", "claude/1", "abc", "blocked", standing="behind")
        self.assertEqual(read.to_dict()["standing"], "behind")
        self.assertEqual(Pull.from_dict(read.to_dict()), read)
        unread = Pull(2, "Two", "claude/2", "def", "blocked")
        self.assertNotIn("standing", unread.to_dict())
        self.assertEqual(Pull.from_dict(unread.to_dict()), unread)
        data = {
            "number": 3, "title": "Three", "head": {"ref": "claude/3", "sha": "fed"},
            "mergeable_state": "draft", "updated_at": "2026-10-14T09:00:00Z", "files": ["a.md"],
            "draft": True, "standing": "clean",
        }
        self.assertEqual(Pull.from_dict(data).standing, "clean")
        self.assertEqual(Pull.from_dict(data).to_dict(), data)
        self.assertIsNone(Pull.from_dict({"number": 4}).standing)

    def test_pr_state_reads_a_dicts_standing(self):
        """pr_state takes the dict the API returns as it takes a Pull, so the
        standing key the adapter wrote on the dict reads as the field does
        and the rule picks the same pull requests from either (F9)."""
        self.assertEqual(board.pr_state({"mergeable_state": "blocked", "standing": "behind"}), "behind")
        self.assertEqual(board.pr_state({"mergeable_state": "blocked", "standing": "conflicting"}), "conflicting")
        self.assertEqual(board.pr_state({"mergeable_state": "blocked", "standing": "clean"}), "blocked")
        self.assertEqual(board.pr_state({"mergeable_state": "draft", "standing": "clean"}), "clean")
        self.assertEqual(board.pr_state({"mergeable_state": "blocked"}), "blocked")


class CommentsDue(unittest.TestCase):
    """What post_comments posted, now as a list for the adapter to post."""

    NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

    def test_a_push_to_the_default_branch_speaks_to_the_pulls_behind_it_then_the_idle_ones(self):
        due = board.comments_due(snapshot(), "push", "main", self.NOW)
        self.assertEqual(due, [
            DueComment(40, "<!-- sdlc-board: main-moved 0123abc -->",
                       "main moved: this branch is behind main at 0123abc; rebase or merge main before the next push."),
            DueComment(40, board.IDLE_MARKER, board.IDLE_TEXT),
            DueComment(41, board.IDLE_MARKER, board.IDLE_TEXT),
        ])
        self.assertEqual([c.kind for c in due], ["main-moved", "idle", "idle"])
        number, marker, text = due[0]
        self.assertEqual((number, marker), (40, board.MAIN_MOVED_MARKER.format(sha="0123abc")))

    def test_any_event_speaks_to_the_same_pulls_since_the_word_depends_on_the_state(self):
        """The run a push to the default branch starts is lost when the
        concurrency group replaces it (F12), so whichever run finds a pull
        request behind tells it, once per commit of the default branch."""
        on_push = board.comments_due(snapshot(), "push", "main", self.NOW)
        for event, ref in (("pull_request", "main"), ("push", "claude/12"), ("schedule", ""), ("delete", "main")):
            self.assertEqual(board.comments_due(snapshot(), event, ref, self.NOW), on_push, event)
        self.assertEqual([(c.number, c.kind) for c in on_push], [(40, "main-moved"), (40, "idle"), (41, "idle")])

    def test_a_snapshot_with_no_commit_tells_nobody_that_main_moved(self):
        snap = Snapshot.from_dict(snapshot())
        snap.sha = ""
        self.assertEqual([c.kind for c in board.comments_due(snap, "push", "main", self.NOW)], ["idle", "idle"])

    def test_a_conflicting_pull_hears_the_conflicting_wording(self):
        snap = Snapshot.from_dict(snapshot())
        snap.pulls[1].mergeable_state = "dirty"
        due = board.comments_due(snap, "push", "main", datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc))
        self.assertEqual([(c.number, c.kind) for c in due], [(40, "main-moved"), (41, "main-moved"), (40, "idle")])
        self.assertIn("conflicting with main at 0123abc", due[1].text)

    def test_nothing_is_due_for_a_fresh_clean_repository(self):
        snap = Snapshot(sha="abc", pulls=[Pull(1, branch="claude/1", mergeable_state="clean", updated_at="2026-09-23T11:00:00Z")])
        self.assertEqual(board.comments_due(snap, "push", "main", self.NOW), [])


def fixture(path):
    return json.loads(path.read_text(encoding="utf-8"))


def golden(name):
    return (FIXTURES / name).read_bytes()


# The idle comment as the template's board posted it, which the recorded
# comments hold; the library's words since are board.IDLE_TEXT.
TEMPLATE_IDLE_TEXT = ("idle: no change for more than a day; if this work is parked, say so here, or the "
                      "coordinator may release the claim.")


class Parity(unittest.TestCase):
    """The page is the one the template's tests compare with, byte for byte,
    and the comments are what the template's board posted, in the idle
    comment's words since."""

    def test_the_templates_fixture_renders_the_templates_page(self):
        self.assertEqual(board.render(fixture(FIXTURE)).encode("ascii"), golden("board-STATE.md"))

    def test_the_wide_fixture_renders_the_templates_page(self):
        self.assertEqual(board.render(fixture(WIDE)).encode("ascii"), golden("board-wide-STATE.md"))

    def test_the_page_survives_a_trip_through_json(self):
        for path, name in ((FIXTURE, "board-STATE.md"), (WIDE, "board-wide-STATE.md")):
            again = Snapshot.from_json(Snapshot.from_dict(fixture(path)).to_json())
            self.assertEqual(board.render(again).encode("ascii"), golden(name), name)

    def test_comments_due_are_what_the_template_posted_on_a_push_to_its_default_branch(self):
        """The template told "main moved" only in the run of the push to
        its default branch (the recorded case for push to trunk) and the
        idle ones on every run; since F12's fix every run owes what that
        run owed, so each recorded case is held to the push's answer."""
        recorded = fixture(FIXTURES / "board-wide-comments.json")["cases"]
        self.assertEqual(len(recorded), 4)
        on_push = next(c for c in recorded if (c["event_name"], c["ref_name"]) == ("push", "trunk"))
        self.assertEqual([m.split()[2] for n, m, t in on_push["comments"]], ["main-moved", "main-moved", "idle", "idle"])
        for case in recorded:
            now = datetime.fromisoformat(case["now"].replace("Z", "+00:00"))
            self.assertEqual(now, datetime.fromisoformat(on_push["now"].replace("Z", "+00:00")))
            due = board.comments_due(fixture(WIDE), case["event_name"], case["ref_name"], now)
            expected = [[n, m, board.IDLE_TEXT if t == TEMPLATE_IDLE_TEXT else t] for n, m, t in on_push["comments"]]
            self.assertEqual([list(c) for c in due], expected, case["event_name"])
            if case is not on_push:
                self.assertEqual([m.split()[2] for n, m, t in case["comments"]], ["idle", "idle"],
                                 "the template told main moved only on the push to its default branch")


class Leniency(unittest.TestCase):
    """Inputs the template's board crashed on or missed, which an adapter
    or a Python caller may hand the library."""

    NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

    def test_changed_files_may_be_api_objects(self):
        pull = Pull.from_dict({"number": 5, "head": {"ref": "claude/5"}, "files": [{"filename": "b.md"}, "a.md"]})
        self.assertEqual(pull.files, ["b.md", "a.md"])

    def test_claim_keys_may_be_issue_numbers(self):
        snap = Snapshot.from_dict({"branches": ["claude/4"], "claims": {4: "- Account: main\n"},
                                   "claim_issues": {4: {"state": "open", "labels": []}}})
        self.assertEqual(list(snap.claims), ["4"])
        self.assertIn("| #4 | claude/4 | main | (no pull request yet) | - |", board.render(snap))

    def test_a_branch_named_in_digits_int_cannot_read_stops_neither_the_page_nor_the_queue(self):
        # A superscript or a circled digit passes isdigit() but not int(): a
        # single such branch raised in the claims' sort, the queue's claimed
        # numbers and the overlaps' open work, so no board was written.
        snap = Snapshot.from_dict({
            "branches": ["claude/3", "claude/\u00b2", "claude/\u27800", "claude/\u0663"],
            "claims": {"3": "- Worker: alice\n\n## Files to touch\n\n- a.md\n",
                       "\u00b2": "- Worker: bob\n\n## Files to touch\n\n- a.md\n"},
            "issues": [{"number": 2, "title": "Two", "labels": ["todo"]}],
        })
        page = board.render(snap)
        self.assertIn("| #3 | claude/3 | alice | (no pull request yet) | a.md |", page)
        self.assertIn("| #2 | Two | todo |", section(page, board.QUEUE_HEADING, board.GIVEN_HEADING))
        self.assertEqual(board.claimed_numbers(snap.branches), {3})
        self.assertEqual([w.number for w in board.open_work(snap)], [3])
        self.assertEqual(board.overlaps(snap), {})

    def test_a_branch_that_names_no_issue_never_reads_as_a_claim_on_the_page(self):
        # The page folds what is not ASCII into ASCII, so claude/ and a
        # fullwidth 3 read as claude/3 beside the real claim, a superscript
        # 2 as claude/2 while #2 sat in the queue, and the key #3 as #3.
        snap = Snapshot.from_dict({
            "branches": ["claude/3", "claude/\uff13", "claude/#3", "claude/\u00b2", "claude/x-notes"],
            "claims": {"3": "- Worker: alice\n", "\uff13": "- Worker: mallory\n", "#3": "- Worker: mallory\n"},
            "issues": [{"number": 2, "title": "Two", "labels": ["todo"]}],
        })
        rows = section(board.render(snap), board.CLAIM_HEADING, board.PULL_HEADING)
        self.assertIn("| #3 | claude/3 | alice |", rows)
        self.assertIn("| (not a claim) | claude/\\uff13 | mallory |", rows)
        self.assertIn("| (not a claim) | claude/#3 | mallory |", rows)
        self.assertIn("| (not a claim) | claude/\\u00b2 | no claim file yet |", rows)
        self.assertIn("| (not a claim) | claude/x-notes | no claim file yet |", rows)
        self.assertEqual(rows.count("| #"), 1, rows)
        self.assertEqual(rows.count("claude/3 "), 1, rows)
        self.assertNotIn("claude/2 ", rows)

    def test_a_second_name_for_an_issue_claims_nothing(self):
        # claude/03 is another name than claude/3, so GitHub takes a first
        # push of each: only the name branch_of writes holds #3, and the
        # other is shown as a branch under claude/ that claims nothing.
        both = Snapshot.from_dict({
            "branches": ["claude/03", "claude/3"],
            "claims": {"03": "- Worker: bob\n", "3": "- Worker: alice\n"},
        })
        rows = section(board.render(both), board.CLAIM_HEADING, board.PULL_HEADING)
        self.assertIn("| #3 | claude/3 | alice | (no pull request yet) | - |\n| (not a claim) | claude/03 | bob |", rows)
        alone = Snapshot.from_dict({"branches": ["claude/03"], "claims": {"03": "- Worker: bob\n"},
                                    "issues": [{"number": 3, "title": "Three", "labels": ["todo"]}]})
        self.assertEqual(board.claimed_numbers(alone.branches), set())
        self.assertEqual(board.open_work(alone), [])
        self.assertIn("| #3 | Three | todo |", section(board.render(alone), board.QUEUE_HEADING, board.GIVEN_HEADING))

    def test_a_pull_with_no_update_time_is_not_idle(self):
        pulls = [Pull(1, updated_at=""), {"number": 2, "updated_at": "2026-09-20T00:00:00Z"}]
        self.assertEqual([board.as_pull(p).number for p in board.idle_pulls(pulls, self.NOW)], [2])
        self.assertEqual(board.comments_due(Snapshot(pulls=[Pull(1)]), "schedule", "", self.NOW), [])

    def test_the_default_out_is_state_md_in_the_current_directory(self):
        self.assertEqual(board.STATE_PATH, Path("STATE.md"))


class Writing(unittest.TestCase):
    def test_write_if_changed_writes_once_for_the_same_bytes(self):
        text = board.render(snapshot())
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "deep" / "STATE.md"
            self.assertTrue(board.write_if_changed(out, text))
            self.assertEqual(out.read_bytes(), text.encode("ascii"))
            self.assertFalse(board.write_if_changed(str(out), text))
            self.assertTrue(board.write_if_changed(out, text + "\n"))


# The fixture's claude/12 (pull request #40, head aaaaaaa1111111, last
# committed on 2026-09-18 at noon), released by the coordinator two days
# later: the release comment on its issue, and the labels claimed and
# account:main taken off, leaving todo.
RELEASED_AT = "2026-09-20T09:00:00Z"
LAST_COMMIT = "2026-09-18T12:00:00Z"
HEAD_12 = "aaaaaaa1111111"


def released_snapshot(**release):
    """The fixture once the coordinator released claude/12, whose pull
    request #40 is still a draft."""
    snap = snapshot()
    snap["pulls"][0]["draft"] = True
    snap["issues"].append({"number": 12, "title": "Describe the board", "labels": [{"name": "todo"}]})
    snap["claim_issues"] = {"12": {"state": "open", "labels": ["todo"]}}
    snap["releases"] = {"12": {"released_at": RELEASED_AT, "sha": HEAD_12, "committed_at": LAST_COMMIT, **release}}
    return snap


def section(text, heading, next_heading):
    return text.split(heading)[1].split(next_heading)[0]


class Releases(unittest.TestCase):
    """A claim the coordinator released is gone from the board, and its issue
    is back in the queue, the moment the snapshot shows the release."""

    def test_a_released_claim_leaves_the_page_and_its_issue_returns_to_the_queue(self):
        text = board.render(released_snapshot())
        claims_section = section(text, board.CLAIM_HEADING, board.PULL_HEADING)
        self.assertNotIn("claude/12", claims_section)
        self.assertIn("| #13 | claude/13 | no claim file yet | #41 |", claims_section)
        self.assertNotIn("| #40 |", section(text, board.PULL_HEADING, board.QUEUE_HEADING))
        queue = section(text, board.QUEUE_HEADING, board.BLOCKER_HEADING)
        self.assertIn("| #12 | Describe the board | todo |", queue)
        self.assertIn("| #14 | Write the release notes | todo |", queue)
        self.assertEqual(board.released_keys(released_snapshot()), {"12"})
        self.assertEqual(board.released_branches(Snapshot.from_dict(released_snapshot())), {"claude/12"})

    def test_it_takes_the_comment_and_the_label_off_both(self):
        still_claimed = released_snapshot()
        still_claimed["claim_issues"]["12"]["labels"] = ["claimed", "todo"]
        no_comment = released_snapshot()
        del no_comment["releases"]["12"]
        closed = released_snapshot()
        closed["claim_issues"]["12"]["state"] = "closed"
        no_issue = released_snapshot()
        del no_issue["claim_issues"]["12"]
        for name, snap in (("still claimed, or claimed again", still_claimed), ("no release comment", no_comment),
                           ("issue closed", closed), ("issue unknown", no_issue)):
            self.assertEqual(board.released_keys(snap), set(), name)
            self.assertNotIn("| #12 | Describe the board |", section(board.render(snap), board.QUEUE_HEADING,
                                                                      board.BLOCKER_HEADING), name)

    def test_a_finished_claim_waiting_for_review_is_never_released(self):
        # The rename would close a pull request that only waits for the
        # owner, so a release comment on it frees nothing, however old.
        in_review = released_snapshot()
        in_review["claim_issues"]["12"]["labels"] = ["in-review", "todo"]
        ready = released_snapshot()
        ready["pulls"][0]["draft"] = False
        unknown = released_snapshot()
        del unknown["pulls"][0]["draft"]
        for name, snap in (("labelled in-review", in_review), ("marked ready", ready),
                           ("draft state not given", unknown)):
            self.assertEqual(board.released_keys(snap), set(), name)
            self.assertIn("| #12 | claude/12 | main | #40 |", board.render(snap), name)

    def test_a_release_too_soon_or_a_commit_since_frees_nothing(self):
        cases = (
            ("released within a day of the last commit", released_snapshot(released_at="2026-09-19T11:00:00Z")),
            ("committed after the release", released_snapshot(committed_at="2026-09-20T10:00:00Z")),
            ("head commit unknown", released_snapshot(sha="")),
            ("commit time unknown", released_snapshot(committed_at="")),
        )
        for name, snap in cases:
            self.assertEqual(board.released_keys(snap), set(), name)
            self.assertIn("| #12 | claude/12 | main | #40 |", board.render(snap), name)

    def test_the_last_commit_is_when_github_received_the_push_and_a_kept_head_counts_whenever(self):
        """F8: where the activity dates the push of the head, that time
        decides, whatever the committer date says; and a branch pushed
        again at a head a release already kept is released by that
        release whenever it came, before the push or after."""
        cases = (
            ("dated after the release, pushed two days before it", {"committed_at": "2026-09-21T10:00:00Z",
                                                                    "pushed_at": LAST_COMMIT}, {"12"}),
            ("dated two days before, pushed within the day", {"pushed_at": "2026-09-19T11:00:00Z"}, set()),
            ("dated two days before, pushed after the release", {"pushed_at": "2026-09-20T10:00:00Z"}, set()),
            ("a kept head pushed again after the release", {"pushed_at": "2026-09-20T10:00:00Z", "kept": True},
             {"12"}),
            ("a kept head with no release comment", {"released_at": "", "kept": True}, set()),
        )
        for name, release, keys in cases:
            snap = released_snapshot(**release)
            self.assertEqual(board.released_keys(snap), keys, name)
            self.assertEqual("| #12 | claude/12 |" in board.render(snap), not keys, name)
        self.assertEqual(Release(pushed_at="b", committed_at="a").last_commit, "b")
        self.assertEqual(Release(committed_at="a").last_commit, "a")
        self.assertEqual(Release().last_commit, "")

    def test_the_wait_can_be_set(self):
        snap = released_snapshot(released_at="2026-09-18T14:00:00Z")
        self.assertEqual(board.released_keys(snap), set())
        self.assertEqual(board.released_keys(snap, timedelta(hours=1)), {"12"})

    def test_the_page_is_the_same_before_the_rename_and_after_it(self):
        # The adapter renames claude/12 to released/12-aaaaaaa, which closes
        # #40; a board drawn after that reads as the one drawn before, so a
        # release rewrites the board once.
        after = released_snapshot()
        after["branches"] = ["main", claims.released_branch("12", HEAD_12), "claude/13"]
        after["pulls"] = [p for p in after["pulls"] if p["number"] != 40]
        del after["claims"]["12"], after["claim_issues"]["12"], after["releases"]["12"]
        self.assertEqual(board.render(after), board.render(released_snapshot()))

    def test_a_released_claims_pull_request_hears_nothing(self):
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        due = board.comments_due(released_snapshot(), "push", "main", now)
        self.assertEqual([(c.number, c.kind) for c in due], [(41, "idle")])

    def test_a_new_claim_after_the_release_holds_the_issue(self):
        snap = released_snapshot(sha="ccccccc3333333", committed_at="2026-09-20T10:00:00Z")
        snap["claims"]["12"] = snap["claims"]["12"].replace("- Account: main", "- Account: second")
        snap["pulls"] = [p for p in snap["pulls"] if p["number"] != 40]
        text = board.render(snap)
        self.assertIn("| #12 | claude/12 | second | (no pull request yet) | docs/agents/README.md |", text)
        self.assertNotIn("| #12 | Describe the board |", section(text, board.QUEUE_HEADING, board.BLOCKER_HEADING))

    def test_releases_round_trip_through_json(self):
        snap = Snapshot.from_dict(released_snapshot())
        self.assertEqual(snap.releases, {"12": Release(RELEASED_AT, HEAD_12, LAST_COMMIT)})
        self.assertEqual(Snapshot.from_json(snap.to_json()), snap)
        self.assertEqual(Snapshot.from_dict({"releases": {12: None}}).releases, {"12": Release()})

    def test_a_release_dated_by_its_push_round_trips_and_one_read_before_f8_is_written_as_it_was(self):
        """pushed_at and kept are written only when set, so a snapshot
        saved before F8's fix is written back unchanged; kept is read only
        when it is true itself, never from a string that reads as true."""
        dated = released_snapshot(pushed_at="2026-09-18T13:00:00Z", kept=True)
        dated["pushed_refused"] = {"claude/13": "HTTP 500"}
        snap = Snapshot.from_dict(dated)
        self.assertEqual(snap.releases["12"], Release(RELEASED_AT, HEAD_12, LAST_COMMIT, "2026-09-18T13:00:00Z", True))
        self.assertEqual(snap.pushed_refused, {"claude/13": "HTTP 500"})
        self.assertEqual(Snapshot.from_json(snap.to_json()), snap)
        self.assertEqual(snap.to_dict()["releases"]["12"]["kept"], True)
        old = Snapshot.from_dict(released_snapshot())
        self.assertEqual(old.to_dict()["releases"]["12"], {"released_at": RELEASED_AT, "sha": HEAD_12,
                                                           "committed_at": LAST_COMMIT})
        self.assertNotIn("pushed_refused", old.to_dict())
        for loose in ("true", 1, "yes"):
            self.assertFalse(Release.from_dict({"kept": loose}).kept, loose)

    def test_what_the_board_says_once_it_renamed_the_branch(self):
        self.assertEqual(board.RELEASED_MARKER.format(branch="released/12-aaaaaaa"),
                         "<!-- sdlc-board: released released/12-aaaaaaa -->")
        text = board.released_text("claude/12", "released/12-aaaaaaa")
        self.assertIn("claude/12 is now released/12-aaaaaaa", text)
        self.assertIn("back in the queue", text)
        self.assertIn("put the label claimed back", text)
        text.encode("ascii")

    def test_what_the_board_says_when_github_refused_the_rename(self):
        self.assertEqual(board.RENAME_REFUSED_MARKER.format(branch="released/12-aaaaaaa"),
                         "<!-- sdlc-board: rename-refused released/12-aaaaaaa -->")
        rulesets = [board.Ruleset("claims", "https://github.com/o/r/settings/rules/7", ("deletion", "update")),
                    board.Ruleset("ruleset 9", "", ("creation",))]
        named = board.rename_refused_text("claude/12", "released/12-aaaaaaa", rulesets,
                                          "HTTP 422: Repository rule violations found\n\nCannot update this ref.")
        unread = board.rename_refused_text("claude/12", "released/12-aaaaaaa", None, "HTTP 422")
        none = board.rename_refused_text("claude/12", "released/12-aaaaaaa", [], "HTTP 403")
        for text in (named, unread, none):
            self.assertTrue(text.startswith("rename refused: "), text)
            self.assertIn("the name claude/12 is still taken and nobody can claim the issue yet", text)
            self.assertIn("Every board run tries the rename again.", text)
            text.encode("ascii")
        self.assertIn("(HTTP 422: Repository rule violations found Cannot update this ref.)", named)
        self.assertIn("\n- [claims](https://github.com/o/r/settings/rules/7): deletion, update\n- ruleset 9: creation\n",
                      named)
        self.assertIn("Take `claude/**` and `released/**` out of their targets", named)
        self.assertIn("could not read which rulesets apply", unread)
        self.assertIn("`claude/**` or `released/**`", unread)
        self.assertIn("No ruleset applies to the two names", none)
        self.assertIn("contents: write", none)


def fork_pull(number=50, ready=True, updated_at="2026-09-19T12:00:00Z"):
    """A pull request sam opened from a fork, from a branch named claude/12
    there, as the API details it: its head and base name their repositories
    (F17)."""
    return {"number": number, "title": "Fix the board", "draft": not ready, "mergeable_state": "clean",
            "head": {"ref": "claude/12", "sha": "ddddddd4444444", "repo": {"full_name": "sam/widgets"}},
            "base": {"ref": "main", "repo": {"full_name": "octo/widgets"}},
            "updated_at": updated_at, "files": ["docs/agents/README.md"]}


class ForkPulls(unittest.TestCase):
    """A claim and its pull request live in the repository itself, so a pull
    request from a fork is no claim's, whatever its branch is called (spec,
    Rules that hold everywhere; F17): until the fix, sam's ready one from a
    fork's claude/12 kept claim 12 from ever being released, and the board
    took it for the claim's own on the page and in the Overlaps."""

    def test_a_pull_request_from_a_fork_is_no_claim_s_whatever_its_branch_is_called(self):
        own, fork = Pull(40, branch="claude/12"), Pull(50, branch="claude/12", fork=True)
        self.assertEqual((board.claim_of(own), board.claim_of(fork)), ("12", None))
        self.assertIsNone(board.claim_of(Pull(51, branch="feature/board")))
        self.assertEqual((board.of_claim(own, "12"), board.of_claim(fork, "12")), (True, False))
        self.assertFalse(board.of_claim(own, "13"))
        released = {"claude/12"}
        self.assertEqual((board.closed_by_release(own, released), board.closed_by_release(fork, released)),
                         (True, False))
        self.assertFalse(board.closed_by_release(own, set()))

    def test_a_ready_one_named_like_a_released_claim_keeps_no_release_from_counting_and_stays_listed(self):
        snap = released_snapshot()
        snap["pulls"].append(fork_pull())
        self.assertEqual(board.released_keys(snap), {"12"})
        text = board.render(snap)
        self.assertNotIn("claude/12 |", section(text, board.CLAIM_HEADING, board.PULL_HEADING))
        pulls = section(text, board.PULL_HEADING, board.QUEUE_HEADING)
        self.assertIn("| #50 | Fix the board | claude/12 |", pulls)
        self.assertNotIn("| #40 |", pulls)
        self.assertIn("| #12 | Describe the board | todo |", section(text, board.QUEUE_HEADING, board.BLOCKER_HEADING))

    def test_one_named_like_a_released_claim_hears_what_any_other_does(self):
        snap = released_snapshot()
        snap["pulls"].append(fork_pull(ready=False))
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        due = board.comments_due(snap, "push", "main", now)
        self.assertEqual([(c.number, c.kind) for c in due], [(41, "idle"), (50, "idle")])

    def test_a_claim_s_row_names_its_own_pull_request_never_a_fork_s(self):
        mine = snapshot()
        mine["pulls"].append(fork_pull())
        self.assertIn("| #12 | claude/12 | main | #40 |", board.render(mine))
        theirs_alone = snapshot()
        theirs_alone["pulls"] = [fork_pull()] + theirs_alone["pulls"][1:]
        text = board.render(theirs_alone)
        self.assertIn("| #12 | claude/12 | main | (no pull request yet) |", text)
        self.assertIn("| #50 | Fix the board | claude/12 |", section(text, board.PULL_HEADING, board.QUEUE_HEADING))

    def test_one_is_open_work_of_its_own_that_meets_the_claim_named_like_its_branch(self):
        snap = snapshot()
        snap["pulls"] = [fork_pull()] + snap["pulls"][1:]
        work = {w.number: w for w in board.open_work(snap)}
        self.assertEqual(sorted(work), [12, 41, 50])
        self.assertEqual((work[12].branch, work[50].branch), ("claude/12", ""))
        self.assertFalse(work[12].is_pull)
        self.assertTrue(work[50].ready)
        self.assertEqual([w.number for w in board.overlaps(snap)["docs/agents/README.md"]], [50, 12])


def stranger_pull(number=50, ready=True, updated_at="2026-09-19T12:00:00Z"):
    """A pull request sam, who may not write, opened from claim 12's own
    branch here, as the adapter details it, its board key stranger saying
    so (F19)."""
    return {"number": number, "title": "Fix the board", "draft": not ready, "mergeable_state": "clean",
            "head": {"ref": "claude/12", "sha": "ccccccc3333333", "repo": {"full_name": "octo/widgets"}},
            "base": {"ref": "main", "repo": {"full_name": "octo/widgets"}},
            "updated_at": updated_at, "files": ["docs/agents/README.md"], "stranger": True}


class StrangerPulls(unittest.TestCase):
    """A claim's pull request is opened by someone who may write to the
    repository, or by a GitHub App, so one that someone else opens from the
    claim's own branch is no claim's either (spec, Rules that hold
    everywhere; F19): until the fix, sam's ready one from claude/12 kept
    claim 12 from ever being released, as a fork's did until F17's, and the
    board took it for the claim's own on the page and in the Overlaps. A
    release's rename closes it with the claim's own, since GitHub closes
    each open pull request from a branch it renames."""

    def test_a_stranger_s_pull_request_is_no_claim_s_and_a_release_s_rename_closes_it(self):
        own, theirs = Pull(40, branch="claude/12"), Pull(50, branch="claude/12", stranger=True)
        self.assertEqual((board.claim_of(own), board.claim_of(theirs)), ("12", None))
        self.assertEqual((board.of_claim(own, "12"), board.of_claim(theirs, "12")), (True, False))
        released = {"claude/12"}
        self.assertEqual((board.closed_by_release(own, released), board.closed_by_release(theirs, released)),
                         (True, True))
        self.assertFalse(board.closed_by_release(theirs, set()))
        read = Pull.from_dict(stranger_pull())
        self.assertTrue(read.stranger)
        self.assertTrue(Pull.from_dict(read.to_dict()).stranger)
        self.assertNotIn("stranger", own.to_dict())

    def test_a_ready_one_keeps_no_release_from_counting_and_the_rename_takes_it_off_the_page(self):
        snap = released_snapshot()
        snap["pulls"].append(stranger_pull())
        self.assertEqual(board.released_keys(snap), {"12"})
        text = board.render(snap)
        self.assertNotIn("claude/12 |", section(text, board.CLAIM_HEADING, board.PULL_HEADING))
        self.assertNotIn("| #50 |", section(text, board.PULL_HEADING, board.QUEUE_HEADING))
        self.assertIn("| #12 | Describe the board | todo |", section(text, board.QUEUE_HEADING, board.BLOCKER_HEADING))
        self.assertNotIn(50, [w.number for w in board.open_work(snap)])

    def test_one_from_a_released_claim_s_branch_hears_nothing_more(self):
        snap = released_snapshot()
        snap["pulls"].append(stranger_pull(ready=False))
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        due = board.comments_due(snap, "push", "main", now)
        self.assertEqual([(c.number, c.kind) for c in due], [(41, "idle")])

    def test_a_claim_s_row_names_its_own_pull_request_never_a_stranger_s(self):
        theirs_alone = snapshot()
        theirs_alone["pulls"] = [stranger_pull()] + theirs_alone["pulls"][1:]
        text = board.render(theirs_alone)
        self.assertIn("| #12 | claude/12 | main | (no pull request yet) |", text)
        self.assertIn("| #50 | Fix the board | claude/12 |", section(text, board.PULL_HEADING, board.QUEUE_HEADING))

    def test_one_is_open_work_of_its_own_that_meets_the_claim_whose_branch_it_is_from(self):
        snap = snapshot()
        snap["pulls"] = [stranger_pull()] + snap["pulls"][1:]
        work = {w.number: w for w in board.open_work(snap)}
        self.assertEqual(sorted(work), [12, 41, 50])
        self.assertEqual((work[12].branch, work[50].branch), ("claude/12", "claude/12"))
        self.assertFalse(work[12].is_pull)
        self.assertTrue(work[50].ready)
        self.assertEqual([w.number for w in board.overlaps(snap)["docs/agents/README.md"]], [50, 12])

    def test_the_key_is_the_board_s_own_and_the_work_began_when_the_branch_was_pushed(self):
        """from_stranger reads only the adapter's key, so a fixture without
        it names no stranger; a stranger's pull request from a branch that
        is no claim's is no claim's either way; and its work began when
        the branch it is from was pushed, as the claim's did, where a
        fork's began when it was opened (pull_began, F13's review)."""
        self.assertFalse(board.from_stranger({}))
        self.assertFalse(board.from_stranger({"stranger": False}))
        self.assertIsNone(board.claim_of(Pull(50, branch="feature/x", stranger=True)))
        snap = snapshot()
        snap["pulls"] = [dict(stranger_pull(), created_at="2026-09-22T08:30:00Z")] + snap["pulls"][1:]
        snap["began"] = {"claude/12": "2026-09-22T10:00:00Z"}
        read = Snapshot.from_dict(snap)
        theirs = next(p for p in read.pulls if p.number == 50)
        self.assertEqual(board.pull_began(read, theirs), "2026-09-22T10:00:00Z")
        from_fork = Pull.from_dict(dict(fork_pull(), created_at="2026-09-22T08:30:00Z"))
        self.assertEqual(board.pull_began(read, from_fork), "2026-09-22T08:30:00Z")


ACCOUNT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
ISSUE = 5


def run_git(cwd, *args, when=None):
    """git as an account runs it, with an identity of its own so the host's
    configuration does not matter, and the commit dated `when` if given."""
    env = dict(ACCOUNT_ENV, **({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}))
    return subprocess.run(["git", "-c", "user.name=account", "-c", "user.email=account@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *args],
                          cwd=cwd, capture_output=True, text=True, timeout=60, env=env)


def git(cwd, *args, when=None):
    result = run_git(cwd, *args, when=when)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


class ReleasedClaimReturns(unittest.TestCase):
    """The defect this rule answers, end to end over a real git remote: once
    the coordinator has released a claim, the board shows its issue in the
    queue, and another account claims the issue by the protocol's steps,
    the first push of claude/<issue> from the default branch, which git
    refuses, as GitHub does, while the name is taken. The rename is made
    with git here; on GitHub the board's adapter makes it through the
    branch rename API (test_github)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sdlc-release-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.remote = self.root / "origin.git"
        git(self.root, "init", "--quiet", "--bare", "--initial-branch=main", str(self.remote))
        seed = self.root / "seed"
        seed.mkdir()
        git(seed, "init", "--quiet", "--initial-branch=main")
        (seed / "README.md").write_text("A project.\n", encoding="ascii")
        git(seed, "add", "README.md")
        git(seed, "commit", "--quiet", "-m", "Start", when="2026-09-01T09:00:00Z")
        git(seed, "push", "--quiet", str(self.remote), "HEAD:refs/heads/main")

    def claim(self, account, when):
        """The protocol's Claim: a new branch claude/<issue> from the default
        branch, whose first commit adds the claim file and nothing else,
        pushed without force; True when the push was taken. A refused push
        means another account has the issue, and the local branch goes."""
        clone = self.root / account
        if not clone.exists():
            git(self.root, "clone", "--quiet", str(self.remote), str(clone))
        git(clone, "fetch", "--quiet", "origin")
        branch = claims.branch_of(ISSUE)
        git(clone, "switch", "--quiet", "-c", branch, "origin/main")
        path = clone / claims.claim_path(ISSUE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Claim: #{ISSUE}\n\n- Account: {account}\n- Branch: {branch}\n\n## Files to touch\n\n"
                        f"- src/{account}.py\n", encoding="ascii")
        git(clone, "add", claims.claim_path(ISSUE))
        git(clone, "commit", "--quiet", "-m", f"Claim #{ISSUE}", when=when)
        push = run_git(clone, "push", "--quiet", "origin", branch)
        if push.returncode == 0:
            return True
        self.assertIn("[rejected]", push.stderr, "refused because the name is taken, and for nothing else")
        git(clone, "switch", "--quiet", "--detach", "origin/main")
        git(clone, "branch", "--quiet", "-D", branch)
        return False

    def remote_git(self, *args):
        return git(self.remote, *args)

    def snapshot(self, issue_labels, released_at=""):
        """The board's snapshot of the remote, as the GitHub adapter collects
        it: the issue with these labels, every branch, the claim file on the
        claim branch, and the release facts when the issue has a release
        comment, the labels claimed is off and the claim branch exists."""
        branches = self.remote_git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
        branch, key = claims.branch_of(ISSUE), str(ISSUE)
        snap = {
            "sha": self.remote_git("rev-parse", "main"),
            "issues": [{"number": ISSUE, "title": "Fix the parser", "labels": issue_labels}],
            "branches": branches,
            "claims": {},
            "claim_issues": {},
            "releases": {},
        }
        if branch in branches:
            snap["claims"][key] = self.remote_git("show", f"{branch}:{claims.claim_path(ISSUE)}")
            snap["claim_issues"][key] = {"state": "open", "labels": issue_labels}
            if released_at and "claimed" not in issue_labels:
                snap["releases"][key] = {"released_at": released_at, "sha": self.remote_git("rev-parse", branch),
                                         "committed_at": self.remote_git("show", "-s", "--format=%cI", branch)}
        return snap

    def test_a_released_claim_returns_to_the_queue_and_another_account_claims_it(self):
        held = ["account:alice", "claimed", "todo"]
        self.assertTrue(self.claim("alice", "2026-09-18T12:00:00Z"))
        self.assertFalse(self.claim("bob", "2026-09-19T08:00:00Z"), "the name is taken while alice holds it")
        page = board.render(self.snapshot(held))
        self.assertIn("| #5 | claude/5 | alice | (no pull request yet) | src/alice.py |", page)
        self.assertNotIn("| #5 |", section(page, board.QUEUE_HEADING, board.BLOCKER_HEADING))

        # The coordinator releases the idle claim: claimed and alice's label
        # off, then the release comment. The board shows the issue free.
        released_at = "2026-09-20T09:00:00Z"
        before_rename = self.snapshot(["todo"], released_at)
        self.assertEqual(board.released_keys(before_rename), {"5"})
        page = board.render(before_rename)
        self.assertNotIn("claude/5", page)
        self.assertIn("| #5 | Fix the parser | todo |", section(page, board.QUEUE_HEADING, board.BLOCKER_HEADING))

        # The board frees the name as the adapter does, and the page stays.
        head = self.remote_git("rev-parse", "claude/5")
        kept = claims.released_branch(ISSUE, head)
        self.remote_git("branch", "-m", "claude/5", kept)
        self.assertEqual(board.render(self.snapshot(["todo"], released_at)), page)

        # Bob claims by the protocol's steps, and the lock holds again.
        self.assertTrue(self.claim("bob", "2026-09-20T10:00:00Z"))
        self.assertFalse(self.claim("carol", "2026-09-20T10:05:00Z"), "the new claim is a lock like the first")
        for labels_now in (["todo"], ["account:bob", "claimed", "todo"]):
            page = board.render(self.snapshot(labels_now, released_at))
            self.assertIn("| #5 | claude/5 | bob | (no pull request yet) | src/bob.py |", page)
            self.assertNotIn("| #5 |", section(page, board.QUEUE_HEADING, board.BLOCKER_HEADING))

        # Alice's work is still there, under the released name.
        self.assertIn("- Account: alice", self.remote_git("show", f"{kept}:{claims.claim_path(ISSUE)}"))
        self.assertEqual(self.remote_git("rev-parse", kept), head)


if __name__ == "__main__":
    unittest.main()
