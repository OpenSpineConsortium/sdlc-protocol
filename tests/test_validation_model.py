"""The harness's memory world (validation/model.py): git's rule for a push
as validation/prototype/gitprobe.py measured it, how a branch stands to
main, the rename GitHub makes, and a state key that keeps everything a
guarantee reads."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import model as M  # noqa: E402


def claim_commit(model, worker, number=1):
    tree = model.tree(model.refs[M.MAIN])
    tree[f"docs/agents/claims/{number}.md"] = M.claim_text(number, worker)
    return model.commit((model.refs[M.MAIN],), worker, tree, f"Claim #{number}")


class Pushes(unittest.TestCase):
    def setUp(self):
        self.model = M.seeded()
        self.first = claim_commit(self.model, "a")

    def test_the_first_push_of_a_name_is_taken_and_makes_its_holder(self):
        self.assertEqual(self.model.push("a", "claude/1", self.first), M.Push(True))
        self.assertEqual(self.model.creators["claude/1"], "a")
        self.assertIn(self.first, self.model.ever_heads)

    def test_a_second_claim_from_main_is_refused_as_git_refuses_it(self):
        self.model.push("a", "claude/1", self.first)
        self.assertEqual(self.model.push("b", "claude/1", claim_commit(self.model, "b")), M.Push(False, "fetch first"))
        self.assertEqual(self.model.refs["claude/1"], self.first)

    def test_a_fast_forward_is_taken(self):
        self.model.push("a", "claude/1", self.first)
        later = self.model.commit((self.first,), "a", {"x": "1\n"}, "work")
        self.assertTrue(self.model.push("a", "claude/1", later).taken)

    def test_a_forced_push_is_taken(self):
        self.model.push("a", "claude/1", self.first)
        self.assertTrue(self.model.push("b", "claude/1", claim_commit(self.model, "b"), force=True).taken)

    def test_a_lease_with_no_tracking_ref_is_stale(self):
        self.model.push("a", "claude/1", self.first)
        self.assertEqual(self.model.push("b", "claude/1", claim_commit(self.model, "b"), lease=True),
                         M.Push(False, "stale info"))

    def test_a_lease_after_a_fetch_takes_someone_elses_claim(self):
        """F7, as git 2.43 does it."""
        self.model.push("a", "claude/1", self.first)
        self.model.fetch("b")
        self.assertTrue(self.model.push("b", "claude/1", claim_commit(self.model, "b"), lease=True).taken)

    def test_a_lease_is_refused_even_for_a_fast_forward_when_its_tracking_ref_is_stale(self):
        self.model.push("a", "claude/1", self.first)
        self.model.fetch("b")
        moved = self.model.commit((self.first,), "a", {"x": "1\n"}, "work")
        self.model.push("a", "claude/1", moved)
        later = self.model.commit((moved,), "b", {"x": "2\n"}, "more")
        self.assertEqual(self.model.push("b", "claude/1", later, lease=True), M.Push(False, "stale info"))


class Standing(unittest.TestCase):
    def setUp(self):
        self.model = M.seeded()
        self.model.push("a", "claude/1", claim_commit(self.model, "a"))

    def move_main(self, tree):
        main = self.model.refs[M.MAIN]
        self.model.refs[M.MAIN] = self.model.commit((main,), M.OWNER, {**self.model.tree(main), **tree}, "elsewhere")

    def test_clean_while_main_is_in_the_branch(self):
        self.assertEqual(self.model.standing("claude/1"), M.CLEAN)

    def test_behind_once_main_moves_elsewhere(self):
        self.move_main({"README.md": "other\n"})
        self.assertEqual(self.model.standing("claude/1"), M.BEHIND)

    def test_dirty_when_both_change_one_path_differently(self):
        head = self.model.refs["claude/1"]
        self.model.push("a", "claude/1", self.model.commit((head,), "a", {**self.model.tree(head), "README.md": "a\n"},
                                                           "work"))
        self.move_main({"README.md": "main\n"})
        self.assertEqual(self.model.standing("claude/1"), M.DIRTY)


class Rename(unittest.TestCase):
    def test_closes_the_open_pull_request_and_keeps_the_holder(self):
        model = M.seeded()
        model.push("a", "claude/1", claim_commit(model, "a"))
        model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1")
        model.rename_ref("claude/1", "released/1-abcdef0")
        self.assertEqual(model.pulls[100].state, "closed")
        self.assertNotIn("claude/1", model.refs)
        self.assertEqual(model.creators["released/1-abcdef0"], "a")


class TheKey(unittest.TestCase):
    """DESIGN.md, 6.3: the prototype's key left out what its guarantees read."""

    def setUp(self):
        self.model = M.seeded()
        self.model.push("a", "claude/1", claim_commit(self.model, "a"))

    def differs(self, change):
        other = self.model.clone()
        change(other)
        return other.canonical() != self.model.canonical()

    def test_keeps_whole_comment_bodies(self):
        self.model.add_comment(1, M.Comment("a", "COLLABORATOR", 0, "x" * 80))
        self.assertTrue(self.differs(lambda m: m.add_comment(1, M.Comment("a", "COLLABORATOR", 0, "x" * 80 + "y"))))

    def test_keeps_pull_request_comments(self):
        self.model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1")
        self.assertTrue(self.differs(lambda m: m.add_comment(100, M.Comment("a", "COLLABORATOR", 0, "finished"))))

    def test_keeps_the_label_events_every_head_and_the_tracking_refs(self):
        self.assertTrue(self.differs(lambda m: setattr(m, "label_events", m.label_events + (
            M.LabelEvent(0, 1, "a", True, "claimed"),))))
        self.assertTrue(self.differs(lambda m: setattr(m, "ever_heads", m.ever_heads | {"f" * 40})))
        self.assertTrue(self.differs(lambda m: m.fetch("b")))

    def test_two_paths_to_one_state_meet(self):
        one, two = self.model.clone(), self.model.clone()
        one.add_label(1, "a", "claimed")
        one.remove_label(1, "a", "claimed")
        two.add_label(1, "a", "claimed")
        two.remove_label(1, "a", "claimed")
        self.assertEqual(one.digest(), two.digest())

    def test_a_clone_shares_nothing(self):
        other = self.model.clone()
        other.issues[1].labels = frozenset({"claimed"})
        other.refs["x"] = "y"
        self.assertNotIn("claimed", self.model.issues[1].labels)
        self.assertNotIn("x", self.model.refs)


if __name__ == "__main__":
    unittest.main()
