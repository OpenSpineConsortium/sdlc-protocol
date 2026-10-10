"""The board renders the same bytes for the same snapshot and speaks only to
the pull requests the rules name. No network: the fixture stands in for the
API, and comments_due says what an adapter would post without posting it.
The rendering and rule tests are the template's, ported as they were, so
the page this module renders is byte for byte the page tools/board.py did."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board  # noqa: E402
from sdlc_protocol.board import DueComment, Issue, Merged, Pull, Snapshot  # noqa: E402

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
        for heading in (board.CLAIM_HEADING, board.PULL_HEADING, board.QUEUE_HEADING, board.BLOCKER_HEADING, board.MERGED_HEADING):
            self.assertIn(heading, lines)
        self.assertIn("| Issue | Branch | Account | PR | Files it touches |", lines)
        self.assertIn("| PR | Title | Branch | State (clean, behind, conflicting, blocked, unknown) | Last update |", lines)
        self.assertIn("| Issue | Title | Labels |", lines)
        self.assertTrue(self.text.endswith("|\n"))
        self.assertNotIn("\n\n\n", self.text)

    def test_claims_name_the_account_or_the_missing_file(self):
        self.assertIn("| #12 | claude/12 | main | #40 | docs/agents/README.md, docs/agents/STATE.md |", self.text)
        self.assertIn("| #13 | claude/13 | no claim file yet | #41 | .claude/skills/remember/SKILL.md |", self.text)
        self.assertNotIn("| main |", self.text.split(board.PULL_HEADING)[0].split("| #12")[0])

    def test_a_claim_whose_issue_is_closed_says_so_and_asks_for_the_branch_to_go(self):
        snap = snapshot()
        snap["branches"].append("claude/3")
        snap["claim_issues"] = {"3": {"state": "closed", "labels": ["account:main", "claimed", "todo"]}}
        self.assertIn("| #3 | claude/3 | main | issue closed | delete the branch |", board.render(snap))

    def test_a_released_claim_file_takes_the_account_from_the_issue_label(self):
        snap = snapshot()
        snap["claim_issues"] = {"13": {"state": "open", "labels": ["account:second", "claimed"]}}
        self.assertIn("| #13 | claude/13 | second | #41 | .claude/skills/remember/SKILL.md |", board.render(snap))

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
        self.assertEqual(text.count("| (none) |"), 5)

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

    def test_team_work_stays_off_the_queue(self):
        snap = snapshot()
        snap["issues"].append({"number": 20, "title": "An epic", "labels": [{"name": "todo"}, {"name": "epic"}]})
        snap["issues"].append({"number": 21, "title": "A member's task", "labels": [{"name": "todo"}, {"name": "agent:w1"}]})
        queue = board.render(snap).split(board.QUEUE_HEADING)[1].split(board.BLOCKER_HEADING)[0]
        self.assertNotIn("#20", queue)
        self.assertNotIn("#21", queue)
        self.assertIn("#14", queue)

    def test_the_default_branch_argument_wins_over_the_snapshots(self):
        self.assertIn("As of commit 0123abc on trunk.", board.render(snapshot(), "trunk"))
        self.assertIn("As of commit 0123abc on main.", board.render(snapshot(), None))


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

    def test_any_other_event_speaks_only_to_the_idle_ones(self):
        for event, ref in (("pull_request", "main"), ("push", "claude/12"), ("schedule", "")):
            due = board.comments_due(snapshot(), event, ref, self.NOW)
            self.assertEqual([(c.number, c.marker) for c in due], [(40, board.IDLE_MARKER), (41, board.IDLE_MARKER)], event)

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


class Parity(unittest.TestCase):
    """The page and the comments are the template's, byte for byte."""

    def test_the_templates_fixture_renders_the_templates_page(self):
        self.assertEqual(board.render(fixture(FIXTURE)).encode("ascii"), golden("board-STATE.md"))

    def test_the_wide_fixture_renders_the_templates_page(self):
        self.assertEqual(board.render(fixture(WIDE)).encode("ascii"), golden("board-wide-STATE.md"))

    def test_the_page_survives_a_trip_through_json(self):
        for path, name in ((FIXTURE, "board-STATE.md"), (WIDE, "board-wide-STATE.md")):
            again = Snapshot.from_json(Snapshot.from_dict(fixture(path)).to_json())
            self.assertEqual(board.render(again).encode("ascii"), golden(name), name)

    def test_comments_due_are_what_the_template_posted(self):
        recorded = fixture(FIXTURES / "board-wide-comments.json")["cases"]
        self.assertEqual(len(recorded), 4)
        for case in recorded:
            now = datetime.fromisoformat(case["now"].replace("Z", "+00:00"))
            due = board.comments_due(fixture(WIDE), case["event_name"], case["ref_name"], now)
            self.assertEqual([list(c) for c in due], case["comments"], case["event_name"])


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


if __name__ == "__main__":
    unittest.main()
