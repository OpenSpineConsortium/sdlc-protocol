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
from validation.fake_github import Fault, FakeGitHub, Unserved  # noqa: E402


def claimed(worker="a", number=1, text=None):
    model = M.seeded()
    tree = model.tree(model.refs[M.MAIN])
    tree[f"docs/agents/claims/{number}.md"] = text if text is not None else M.claim_text(number, worker)
    model.push(worker, f"claude/{number}", model.commit((model.refs[M.MAIN],), worker, tree, "Claim"))
    return model


class TheActivity(unittest.TestCase):
    """F8: the activity lists each branch's making and the push that last
    set its head, which the release rule dates a claim by, and a fault's
    query tells that read from the reads of a branch's making (F13)."""

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
        self.assertEqual([(e["activity_type"], e["after"]) for e in listed],
                         [("push", self.later), ("branch_creation", self.later)])
        oldest = api.get(f"/repos/{M.REPO}/activity", {"ref": "refs/heads/claude/1", "direction": "asc"})
        self.assertEqual([e["activity_type"] for e in oldest], ["branch_creation", "push"])
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.later), M.iso(3 * M.HOUR))
        self.assertEqual(github.head_pushed_at(api, "claude/1", self.made), "")
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
