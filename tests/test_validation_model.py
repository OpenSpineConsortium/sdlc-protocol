"""The harness's memory world (validation/model.py): git's rule for a push
as validation/prototype/gitprobe.py measured it, how a branch stands to
main, the rename and the delete GitHub make, the board workflow's queue
in its concurrency group, and a state key that keeps everything a
guarantee reads."""

import os
import pickle
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cycle, model as M, verbs  # noqa: E402
from validation.verbs import Verb  # noqa: E402


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


class CommitsInOrder(unittest.TestCase):
    def test_a_parent_comes_before_its_child_made_in_the_same_minute(self):
        model = M.seeded()
        first = claim_commit(model, "a")
        second = model.commit((first,), "a", {**model.tree(first), "src/1.py": "a 0\n"}, "Work on #1")
        third = model.commit((second,), "a", {**model.tree(second), "src/1.py": "a 0\na 0\n"}, "Work on #1")
        for shas in (frozenset({first, second, third}), frozenset({third, first, second})):
            self.assertEqual(model.in_order(shas), [first, second, third])

    def test_a_rebase_replays_them_in_that_order_and_keeps_the_work(self):
        """Replayed by date alone, an older commit landed last and its text
        overwrote the newer one's, so a rebase lost work git keeps."""
        model = M.seeded()
        for verb in (Verb("claim", "a", (1,)), Verb("commit", "a", (1,)), Verb("commit", "a", (1,)),
                     Verb("move-main", M.OWNER)):
            verbs.apply(model, verb)
        own = model.changed(model.merge_base(model.refs[M.MAIN], model.refs["claude/1"]), model.refs["claude/1"])
        before = model.tree(model.refs["claude/1"])
        verbs.apply(model, Verb("rebase-main", "a", (1,)))
        after = model.tree(model.refs["claude/1"])
        self.assertEqual(own, ["docs/agents/claims/1.md", "src/1.py"])
        self.assertEqual({p: after.get(p) for p in own}, {p: before[p] for p in own})
        self.assertTrue(model.is_ancestor(model.refs[M.MAIN], model.refs["claude/1"]))

    def test_a_rebase_after_a_merge_of_main_drops_the_merge_as_git_does(self):
        """Replayed, the merge's change against its first parent wrote
        main's old text over main as it is now; git's rebase drops the
        merge and keeps main's text, so the branch changes only its own
        files (the review of the deep lane, played over both worlds)."""
        model = M.seeded()
        for verb in (Verb("claim", "a", (1,)), Verb("move-main", M.OWNER), Verb("merge-main", "a", (1,)),
                     Verb("move-main", M.OWNER), Verb("rebase-main", "a", (1,))):
            verbs.apply(model, verb)
        main, branch = model.tree(model.refs[M.MAIN]), model.tree(model.refs["claude/1"])
        self.assertEqual(branch["README.md"], main["README.md"])
        self.assertEqual(model.files_changed("claude/1"), ["docs/agents/claims/1.md"])
        self.assertTrue(all(len(model.commits[sha].parents) < 2
                            for sha in model.ancestors(model.refs["claude/1"]) - model.ancestors(model.refs[M.MAIN])))


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

    def test_leaves_open_a_pull_request_from_a_fork_whose_branch_bears_the_name(self):
        model = M.seeded()
        model.push("a", "claude/1", claim_commit(model, "a"))
        model.pulls[100] = M.PullRequest(100, "claude/1", "sam", "Closes #1", fork=True)
        model.rename_ref("claude/1", "released/1-abcdef0")
        self.assertEqual(model.pulls[100].state, "open")


class Delete(unittest.TestCase):
    """GitHub closes the open pull requests a deleted branch was the head of
    (managing-branches-within-your-repository.md:71), as the board's
    delete of a branch pushed again at the head a release kept meets."""

    def setUp(self):
        self.model = M.seeded()
        self.model.push("a", "claude/1", claim_commit(self.model, "a"))

    def test_closes_the_open_pull_request_whose_head_it_was(self):
        self.model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1")
        self.model.delete_ref("claude/1")
        self.assertEqual(self.model.pulls[100].state, "closed")
        self.assertNotIn("claude/1", self.model.refs)
        self.assertNotIn("claude/1", self.model.creators)

    def test_leaves_a_merged_pull_request_merged_and_a_fork_s_open(self):
        self.model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1", state="merged")
        self.model.pulls[101] = M.PullRequest(101, "claude/1", "sam", "Closes #1", fork=True)
        self.model.delete_ref("claude/1")
        self.assertEqual(self.model.pulls[100].state, "merged")
        self.assertEqual(self.model.pulls[101].state, "open")


class Delete(unittest.TestCase):
    def test_closes_the_open_pull_request_as_github_does(self):
        # The soak's G9 lead (seed 427): the board deleted a branch pushed
        # again at a released head, and the model kept its draft open, which
        # the next run told "idle".
        model = M.seeded()
        model.push("a", "claude/1", claim_commit(model, "a"))
        model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1")
        model.pulls[101] = M.PullRequest(101, "claude/1", "a", "Closes #1")
        model.pulls[101].state = "merged"
        model.delete_ref("claude/1")
        self.assertEqual(model.pulls[100].state, "closed")
        self.assertEqual(model.pulls[101].state, "merged")
        self.assertNotIn("claude/1", model.refs)
        self.assertNotIn("claude/1", model.creators)


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

    def test_keeps_the_board_s_page_and_when_each_branch_began(self):
        """A scripted worker acts on the page, and the landing order on
        when the work began, so neither may be folded."""
        self.assertTrue(self.differs(lambda m: setattr(m, "board_text", "# Board\n")))
        self.assertTrue(self.differs(lambda m: m.created.__setitem__("claude/1", 5)))

    def test_the_digest_survives_a_pickle_into_another_interpreter(self):
        """A search's checkpoint (deep.py) pickles the models on its stack,
        and a resume reads it in another process, whose hash seed orders a
        set another way, so every set in the key is sorted. A round trip
        within one process keeps a set's order and caught an unsorted set
        about one run in six; fresh interpreters under eight seeds each
        read the pickle as this process wrote it."""
        self.model.add_label(1, "a", "claimed")
        self.model.add_label(1, "a", "worker:a")
        script = ("import pickle, sys\n"
                  f"sys.path.insert(0, {str(ROOT)!r})\n"
                  "print(pickle.loads(sys.stdin.buffer.read()).digest().hex())\n")
        written = pickle.dumps(self.model)
        read = set()
        for seed in range(8):
            done = subprocess.run([sys.executable, "-c", script], input=written, capture_output=True, check=True,
                                  env={**os.environ, "PYTHONHASHSEED": str(seed)})
            read.add(done.stdout.decode("ascii").strip())
        self.assertEqual(read, {self.model.digest().hex()})

    def test_keeps_whether_github_hides_a_held_branch_s_standing(self):
        """The fake answers blocked or the branch's own word by it (F9), so
        two models alike but for it behave differently under the board."""
        self.assertTrue(self.differs(lambda m: setattr(m, "opaque_pulls", True)))

    def test_keeps_whether_github_calls_a_branch_behind_clean(self):
        """The fake answers clean or behind for a branch that is behind by
        it (F16), so two models alike but for it behave differently."""
        self.assertTrue(self.differs(lambda m: setattr(m, "lenient_rules", True)))
        self.assertTrue(self.model.clone().lenient_rules is False)
        lenient = self.model.clone()
        lenient.lenient_rules = True
        self.assertTrue(lenient.clone().lenient_rules)

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

    def test_keeps_the_runs_queued_and_the_run_between_its_halves(self):
        self.assertTrue(self.differs(lambda m: m.fire((("push", "main"),))))
        self.assertTrue(self.differs(lambda m: cycle.start_run(m)))
        one, two = self.model.clone(), self.model.clone()
        cycle.start_run(one)
        cycle.start_run(two, faults=(verbs.FAULTS["rename-403"],))
        self.assertNotEqual(one.canonical(), two.canonical())
        cycle.finish_run(one)
        cycle.finish_run(two)
        self.assertEqual(one.canonical(), two.canonical())


class TheWorkflowsQueue(unittest.TestCase):
    """board.yml's concurrency group (DESIGN.md, 3.3): the run that starts
    first is never cancelled, one run waits behind it at most, a newer one
    replaces it, and of several fired at once any may be the one that
    starts or the one left waiting."""

    def setUp(self):
        self.model = M.seeded()

    def test_the_first_run_into_an_idle_group_is_never_cancelled(self):
        self.model.fire((("push", "main"),))
        self.model.fire((("create", "claude/1"), ("push", "claude/1"), ("pull_request", "")))
        self.assertEqual(self.model.queued, (("push", "main"),))
        self.assertEqual(self.model.pending, (("create", "claude/1"), ("pull_request", "")))

    def test_a_push_to_another_branch_takes_the_slot_and_runs_nothing(self):
        """board.yml's job skips a push to any branch but the default, and
        whether the skipped run still cancels the one waiting is inferred,
        not measured (canary C2); into an idle group it runs and is gone."""
        self.model.fire((("push", "claude/1"),))
        self.assertEqual((self.model.queued, self.model.pending), ((), ()))
        self.model.fire((("push", "main"),))
        self.model.starts("push", "main")
        self.model.running = "the push's run, between its halves"
        self.model.fire((("issues", ""),))
        self.assertEqual(self.model.pending, (("issues", ""),))
        self.model.fire((("push", "claude/1"),))
        self.assertEqual(self.model.pending, ())

    def test_a_skipped_push_is_a_run_where_the_scope_can_start_it(self):
        """With skipped_runs, which the split scope sets, the skipped run is
        kept, since it holds the group while it is in progress: fired with
        the claim's create event it may be the one that starts, leaving
        the create run pending, and a newer event's run replaces that."""
        self.model.fire((("push", "claude/1"), ("create", "claude/1")), skipped_runs=True)
        self.assertEqual(self.model.queued, (("create", "claude/1"), ("push", "claude/1")))
        self.model.starts("push", "claude/1")
        self.assertEqual((self.model.queued, self.model.pending), ((), (("create", "claude/1"),)))
        self.model.running = "the skipped run, in progress"
        self.model.fire((("push", "main"),), skipped_runs=True)
        self.assertEqual(self.model.pending, (("push", "main"),))
        self.model.fire((("push", "claude/1"),), skipped_runs=True)
        self.assertEqual(self.model.pending, (("push", "claude/1"),))

    def test_of_the_events_of_one_act_one_starts_and_one_of_the_rest_waits(self):
        self.model.fire((("create", "claude/1"), ("pull_request", "")))
        self.model.starts("create", "claude/1")
        self.assertEqual((self.model.queued, self.model.pending), ((), (("pull_request", ""),)))
        self.model.finished()
        self.assertEqual((self.model.queued, self.model.pending), ((("pull_request", ""),), ()))
        self.model.starts("pull_request", "")
        self.model.finished()
        self.assertEqual((self.model.queued, self.model.pending), ((), ()))

    def test_the_pending_run_that_starts_was_the_one_left_of_its_fellows(self):
        self.model.fire((("push", "main"),))
        self.model.starts("push", "main")
        self.model.running = "the push's run, between its halves"
        self.model.fire((("create", "claude/1"), ("pull_request", "")))
        self.model.running = None
        self.model.finished()
        self.model.starts("pull_request", "")
        self.assertEqual((self.model.queued, self.model.pending), ((), ()))

    def test_a_run_from_elsewhere_replaced_what_waited(self):
        self.model.fire((("create", "claude/1"), ("pull_request", "")))
        self.model.starts("create", "claude/1")
        self.model.fire((("issues", ""),))
        self.model.starts("schedule", "")
        self.assertEqual(self.model.pending, ())


if __name__ == "__main__":
    unittest.main()
