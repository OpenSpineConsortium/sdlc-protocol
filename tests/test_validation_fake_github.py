"""The harness's fake GitHub (validation/fake_github.py): the library's real
adapter runs against it, and it is faithful to the transport where the
prototype's fake was not: an ASCII request line, pages with a Link header,
mergeable computed late, and faults of GitHub's shapes."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402
from validation import cycle, model as M  # noqa: E402
from validation.fake_github import LAGGING, MOVED, MOVED_TWICE, NO_SHA, Fault, FakeGitHub, Unserved  # noqa: E402


def claimed(worker="a", number=1, text=None):
    model = M.seeded()
    tree = model.tree(model.refs[M.MAIN])
    tree[f"docs/agents/claims/{number}.md"] = text if text is not None else M.claim_text(number, worker)
    model.push(worker, f"claude/{number}", model.commit((model.refs[M.MAIN],), worker, tree, "Claim"))
    return model


class TheActivity(unittest.TestCase):
    """F8: the activity lists each branch's making and the push that last
    set its head, which the release rule dates a claim by, with the entry
    before that push, which an activity a push behind shows newest, and a
    fault's query tells that read from the reads of a branch's making
    (F13)."""

    def setUp(self):
        self.model = claimed()
        self.made = self.model.refs["claude/1"]
        self.model.now = 3 * M.HOUR
        tree = self.model.tree(self.made) | {"src/x.py": "1\n"}
        self.later = self.model.commit((self.made,), "a", tree, "Work")
        self.model.push("a", "claude/1", self.later)

    def test_the_newest_entry_is_the_push_that_set_the_head(self):
        api, _ = cycle.api_for(self.model)
        listed = api.get(f"/repos/{M.REPO}/activity", {"ref": "refs/heads/claude/1", "direction": "desc"})
        self.assertEqual([(e["activity_type"], e["before"], e["after"]) for e in listed],
                         [("push", self.made, self.later), ("branch_creation", NO_SHA, self.made)])
        oldest = api.get(f"/repos/{M.REPO}/activity", {"ref": "refs/heads/claude/1", "direction": "asc"})
        self.assertEqual([e["activity_type"] for e in oldest], ["branch_creation", "push"])
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later), M.iso(3 * M.HOUR))
        # The head the push moved the branch from is dated by that push
        # too, by a run that read the branch before it, one that began no
        # later; one that began a day later finds it stale (F8's second
        # review), as does a reader that gives no moment of its own.
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.made, M.moment(3 * M.HOUR)), M.iso(3 * M.HOUR))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.made, M.moment(27 * M.HOUR)), "")
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.made), "")
        self.assertEqual(github.head_pushed_at(api, "claude/1", "f" * 40), "")
        self.assertEqual(github.branch_made_at(api, "claude/1"), M.iso(0))

    def test_a_fault_with_a_query_fires_on_that_read_alone(self):
        pushes = Fault("GET", "/activity", 500, query=(("activity_type", None),))
        api, fake = cycle.api_for(self.model, (pushes,))
        self.assertEqual(github.branch_made_at(api, "claude/1"), M.iso(0))
        with self.assertRaises(github.GitHubError):
            github.head_pushed_at(api, "claude/1", self.later)
        self.assertEqual([c.status for c in fake.calls], [200, 500])
        made = Fault("GET", "/activity", 500, query=(("activity_type", "branch_creation"),))
        api, _ = cycle.api_for(self.model, (made,))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later), M.iso(3 * M.HOUR))

    def test_a_lagging_answer_leaves_the_latest_push_out_and_records_what_it_answered(self):
        """The faults pushes-lagging: the read is answered, 200, before the
        activity lists the push that set the head, so the making is the
        newest entry and dates no push of the head; the next read lists
        it."""
        lagging = Fault("GET", "/activity", 200, query=(("activity_type", None),), answer=LAGGING)
        api, fake = cycle.api_for(self.model, (lagging,))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later), "")
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later), M.iso(3 * M.HOUR))
        self.assertEqual([(c.status, c.injected, c.fault is lagging) for c in fake.calls],
                         [(200, True, True), (200, False, False)])
        self.assertEqual([e["activity_type"] for e in fake.calls[0].answered], ["branch_creation"])
        self.assertEqual(fake.calls[1].answered[0]["after"], self.later)

    def test_a_moved_answer_lists_a_push_from_the_head_after_the_read(self):
        """The fault pushes-moved: the newest entry is a push at the clock's
        minute from the head the run read to a commit nobody made, which
        dates the head by its before side, as a push after the run read
        the branch, a run that began at that minute reading it; and every
        later read of the branch in the run lists it still, as GitHub
        would, while a read of the branch's making is answered from the
        model, and so is the next run's (F8's second review)."""
        self.model.now = 30 * M.HOUR
        began = M.moment(self.model.now)
        moved = Fault("GET", "/activity", 200, query=(("activity_type", None),), answer=MOVED)
        api, fake = cycle.api_for(self.model, (moved,))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later, began), M.iso(30 * M.HOUR))
        newest = fake.calls[0].answered[0]
        self.assertEqual(newest["before"], self.later)
        self.assertNotIn(newest["after"], self.model.commits)
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later, began), M.iso(30 * M.HOUR))
        self.assertEqual(fake.calls[1].answered[0], newest)
        self.assertTrue(fake.calls[1].injected)
        self.assertEqual(github.branch_made_at(api, "claude/1"), M.iso(0))
        self.assertFalse(fake.calls[2].injected)
        next_run, _ = cycle.api_for(self.model)
        self.assertEqual(github.head_pushed_at(next_run, "claude/1", self.later, began), M.iso(3 * M.HOUR))

    def test_a_lagging_answer_is_a_push_behind_the_branch_s_last(self):
        """F8's second review: GitHub's activity a push behind lists the
        push before the last newest, so a branch moved off its head and
        pushed back to it shows the push that moved it off, older than the
        run, and a branch made again after a delete shows that delete,
        which names the head it took away as the head before; each is a
        stale entry, which dates nothing, and the next read lists the
        push."""
        model, ref = claimed(), "claude/1"
        made = model.refs[ref]
        model.now = M.HOUR
        moved_off = model.commit((made,), "a", model.tree(made) | {"src/x.py": "1\n"}, "Work")
        model.push("a", ref, moved_off)
        model.now = 26 * M.HOUR
        model.fetch("a")
        self.assertTrue(model.push("a", ref, made, lease=True).taken)
        lagging = Fault("GET", "/activity", 200, query=(("activity_type", None),), answer=LAGGING)
        api, fake = cycle.api_for(model, (lagging,))
        began = M.moment(model.now)
        self.assertEqual(github.head_pushed_at(api, ref, made, began), "")
        newest = fake.calls[0].answered[0]
        self.assertEqual((newest["activity_type"], newest["before"], newest["after"]), ("push", made, moved_off))
        self.assertEqual(github.head_pushed_at(api, ref, made, began), M.iso(26 * M.HOUR))
        self.assertEqual(fake.calls[1].answered[0]["activity_type"], "force_push")
        model.delete_ref(ref)
        model.now = 51 * M.HOUR
        model.push("a", ref, made)
        api, fake = cycle.api_for(model, (lagging,))
        began = M.moment(model.now)
        self.assertEqual(github.head_pushed_at(api, ref, made, began), "")
        newest = fake.calls[0].answered[0]
        self.assertEqual((newest["activity_type"], newest["before"], newest["after"], newest["timestamp"]),
                         ("branch_deletion", made, NO_SHA, M.iso(26 * M.HOUR)))
        self.assertEqual(github.head_pushed_at(api, ref, made, began), M.iso(51 * M.HOUR))

    def test_a_moved_twice_answer_names_the_head_on_neither_side(self):
        """The fault pushes-moved-twice (F8's fix check): two pushes at the
        clock's minute after the run read the branch, the first from the
        head the run read and the second from the first's commit, so the
        newest names the head on neither side; it came after the run
        began, so it dates the head, and every later read of the branch in
        the run lists it still."""
        self.model.now = 30 * M.HOUR
        began = M.moment(self.model.now)
        twice = Fault("GET", "/activity", 200, query=(("activity_type", None),), answer=MOVED_TWICE)
        api, fake = cycle.api_for(self.model, (twice,))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later, began), M.iso(30 * M.HOUR))
        newest = fake.calls[0].answered[0]
        self.assertEqual(FakeGitHub(self.model).activity({"ref": "refs/heads/claude/1"}, MOVED_TWICE)[0], newest)
        first = FakeGitHub(self.model).activity({"ref": "refs/heads/claude/1"}, MOVED_TWICE)[1]
        self.assertEqual(newest["before"], first["after"])
        self.assertEqual(first["before"], self.later)
        self.assertNotIn(self.later, (newest["before"], newest["after"]))
        self.assertNotIn(newest["after"], self.model.commits)
        self.assertEqual((newest["timestamp"], first["timestamp"]), (M.iso(30 * M.HOUR), M.iso(30 * M.HOUR)))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later), "", "dated by no moment of a run")
        self.assertEqual(fake.calls[1].answered[0], newest)

    def test_a_fault_then_another_fires_each_in_turn(self):
        """Fault.then (F8's fix check): pushes-500-then-moved refuses the
        first read of a claim's push and answers the next with a push after
        the run read the branch, which later reads list still;
        pushes-lagging-then-500 answers the first a push behind and refuses
        the next; once both are spent the model answers."""
        self.model.now = 30 * M.HOUR
        began = M.moment(self.model.now)
        pushes = (("activity_type", None),)
        refused_then_moved = Fault("GET", "/activity", 500, query=pushes,
                                   then=Fault("GET", "/activity", 200, query=pushes, answer=MOVED))
        self.assertEqual([f.status for f in refused_then_moved.chain()], [500, 200])
        api, fake = cycle.api_for(self.model, (refused_then_moved,))
        with self.assertRaises(github.GitHubError):
            github.head_pushed_at(api, "claude/1", self.later, began)
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later, began), M.iso(30 * M.HOUR))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later, began), M.iso(30 * M.HOUR))
        self.assertEqual([(c.status, c.injected) for c in fake.calls], [(500, True), (200, True), (200, True)])
        lagging_then_refused = Fault("GET", "/activity", 200, query=pushes, answer=LAGGING,
                                     then=Fault("GET", "/activity", 500, query=pushes))
        api, fake = cycle.api_for(self.model, (lagging_then_refused,))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later, began), "")
        with self.assertRaises(github.GitHubError):
            github.head_pushed_at(api, "claude/1", self.later, began)
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later, began), M.iso(3 * M.HOUR))
        self.assertEqual([(c.status, c.injected) for c in fake.calls], [(200, True), (500, True), (200, False)])


class TheAdapterReadsTheModel(unittest.TestCase):
    def test_a_snapshot_holds_the_claim_and_leaves_pull_requests_out_of_the_issues(self):
        model = claimed()
        model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1", draft=True)
        api, _ = cycle.api_for(model)
        snap = github.snapshot(api, model.refs[M.MAIN], M.MAIN, M.moment(0))
        self.assertEqual([i.number for i in snap.issues], [1])
        self.assertEqual(snap.branches, ["claude/1"])
        self.assertIn("- Worker: a", snap.claims["1"])
        self.assertEqual([p.number for p in snap.pulls], [100])

    def test_a_claim_file_over_1_mb_is_given_without_its_text(self):
        model = claimed(text="x" * (1024 * 1024 + 1))
        api, _ = cycle.api_for(model)
        self.assertIs(github.claim_text(api, "claude/1"), board.UNREAD_CLAIM)

    def test_a_pull_request_names_its_author_and_the_permission_api_answers_by_the_writers(self):
        """F19: the adapter reads whose a pull request is from the list and
        the detail, as GitHub shows it, and asks the permission of an
        author no association vouches for, which the fake answers from
        the model's writers, once a client."""
        model = claimed()
        model.pulls[100] = M.PullRequest(100, "claude/1", "sam", "Closes #1", draft=False, association="NONE")
        api, fake = cycle.api_for(model)
        listed = api.get_all(f"/repos/{M.REPO}/pulls", {"state": "open"})
        self.assertEqual([(p["user"]["login"], p["author_association"]) for p in listed], [("sam", "NONE")])
        detail = api.get(f"/repos/{M.REPO}/pulls/100")
        self.assertEqual((detail["user"]["login"], detail["author_association"]), ("sam", "NONE"))
        self.assertEqual((api.asked("sam"), api.asked("a")), (False, True))
        snap = github.snapshot(api, model.refs[M.MAIN], M.MAIN, M.moment(0))
        self.assertEqual([(p.number, p.stranger) for p in snap.pulls], [(100, True)])
        self.assertEqual(sum(1 for c in fake.calls if c.path == "/collaborators/sam/permission"), 1)

    def test_the_permission_api_answers_admin_for_the_owner_and_403_for_a_login_github_will_not_answer_for(self):
        """F19's review: GitHub answers admin for an owner, and a token
        without the right meets a 403 on every call, which the adapter
        reads as no answer (github.permissions), so a stranger's pull
        request then stays the claim's (may_open) and nothing is marked."""
        model = claimed()
        model.unanswered = frozenset({"sam"})
        model.pulls[100] = M.PullRequest(100, "claude/1", "sam", "Closes #1", draft=False, association="NONE")
        api, fake = cycle.api_for(model)
        self.assertEqual(api.get(f"/repos/{M.REPO}/collaborators/{M.OWNER}/permission")["permission"], "admin")
        self.assertIsNone(api.asked("sam"))
        with self.assertRaises(github.GitHubError) as caught:
            api.get(f"/repos/{M.REPO}/collaborators/sam/permission")
        self.assertIn("403", str(caught.exception))
        snap = github.snapshot(api, model.refs[M.MAIN], M.MAIN, M.moment(0))
        self.assertEqual([(p.number, p.stranger) for p in snap.pulls], [(100, False)])
        self.assertEqual([c.status for c in fake.calls if c.path == "/collaborators/sam/permission"], [403, 403])


class TheTransport(unittest.TestCase):
    def test_a_path_outside_ascii_is_refused_before_it_is_sent(self):
        api, fake = cycle.api_for(M.seeded())
        with self.assertRaises(github.GitHubError) as caught:
            api.get(f"/repos/{M.REPO}/git/ref/heads/claude/caf\u00e9")
        self.assertIn("outside ASCII", str(caught.exception))
        self.assertEqual(fake.calls, [])

    def test_a_list_comes_in_pages_of_a_hundred(self):
        model = M.seeded()
        for n in range(150):
            model.refs[f"topic/{n:03d}"] = model.refs[M.MAIN]
        api, fake = cycle.api_for(model)
        self.assertEqual(len(api.branch_names()), 151)
        self.assertEqual([c.query for c in fake.calls if c.path == "/branches"][1], (("page", "2"), ("per_page", "100")))

    def test_mergeable_is_null_on_the_first_read_and_settled_on_the_next(self):
        model = claimed()
        model.pulls[100] = M.PullRequest(100, "claude/1", "a", "Closes #1", draft=False)
        api, fake = cycle.api_for(model)
        detail = github.pull_with_detail(api, 100)
        self.assertEqual(detail["mergeable_state"], "clean")
        self.assertEqual(sum(1 for c in fake.calls if c.path == "/pulls/100"), 2)

    def test_a_call_the_fake_does_not_model_is_loud(self):
        api, _ = cycle.api_for(M.seeded())
        with self.assertRaises(Unserved):
            api.get(f"/repos/{M.REPO}/hooks")


class Faults(unittest.TestCase):
    def test_a_fault_answers_its_status_the_times_it_is_given(self):
        model = M.seeded()
        api, fake = cycle.api_for(model, (Fault("POST", "/issues/*/comments", 403, times=1,
                                                message="You have exceeded a secondary rate limit", retry_after="60"),))
        with self.assertRaises(github.GitHubError) as caught:
            api.post(f"/repos/{M.REPO}/issues/1/comments", {"body": "one"})
        self.assertEqual((caught.exception.status, caught.exception.detail),
                         (403, "You have exceeded a secondary rate limit"))
        self.assertEqual(model.issues[1].comments, ())
        api.post(f"/repos/{M.REPO}/issues/1/comments", {"body": "two"})
        self.assertEqual([c.body for c in model.issues[1].comments], ["two"])

    def test_an_applied_fault_makes_the_write_and_loses_the_answer(self):
        model = M.seeded()
        api, fake = cycle.api_for(model, (Fault("POST", "/issues/*/comments", 502, applied=True),))
        with self.assertRaises(github.GitHubError):
            api.post(f"/repos/{M.REPO}/issues/1/comments", {"body": "kept"})
        self.assertEqual([c.body for c in model.issues[1].comments], ["kept"])
        self.assertTrue(fake.calls[-1].applied)

    def test_a_change_to_an_issue_s_body_is_unserved_not_a_fake_s_error(self):
        api, _ = cycle.api_for(M.seeded())
        with self.assertRaises(Unserved):
            api.set_body(1, "x")

    def test_a_label_the_issue_does_not_carry_is_a_404(self):
        model = M.seeded()
        api, _ = cycle.api_for(model)
        self.assertFalse(github.take_label_off(api, "1", "claimed"))

    def test_a_rename_onto_a_name_that_stands_is_a_422(self):
        model = claimed()
        model.refs["released/1-abcdef0"] = model.refs[M.MAIN]
        fake = FakeGitHub(model)
        api = github.GitHub("t", M.REPO, opener=fake)
        with self.assertRaises(github.GitHubError) as caught:
            api.post(f"/repos/{M.REPO}/branches/claude%2F1/rename", {"new_name": "released/1-abcdef0"})
        self.assertEqual(caught.exception.status, 422)


if __name__ == "__main__":
    unittest.main()
