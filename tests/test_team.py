"""The clerk decides with no Claude usage, so its decisions are pinned
here against an in-memory GitHub: when the coordinator plans and when it
integrates, what a worker carries or takes, how the shared work is dealt
by area, and what seed writes. The fake below is the Client the clerk's
rules are written against; the adapter over REST implements the same
methods."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims, labels, team  # noqa: E402


class FakeClient:
    """Just enough of GitHub for team.py, kept in dictionaries: the Client
    methods the clerk calls, over the dictionaries GitHub would return."""

    def __init__(self):
        self.issues, self.branches, self.pulls, self.checks = {}, set(), {}, {}
        self.labels, self.sub_issues = {"todo", "epic"}, []
        self.claims, self.released_claims, self.released_asked = {}, set(), []
        self.next_number = 100

    def add_claim(self, issue, worker):
        """A claim branch whose claim file names the worker."""
        self.branches.add(f"claude/{issue}")
        self.claims[f"claude/{issue}"] = f"# Claim: #{issue}\n\n- Worker: {worker}\n- Branch: claude/{issue}\n"

    def add_issue(self, number, labels=(), body="", state="open", title="t"):
        self.issues[number] = {"number": number, "id": 9000 + number, "title": title, "body": body,
                               "state": state, "labels": [{"name": n} for n in labels]}
        return self.issues[number]

    def add_pull(self, issue, draft=False, state="clean", conclusion="success", check="check"):
        sha = f"sha{issue}"
        self.pulls[f"claude/{issue}"] = {"number": 500 + issue, "draft": draft,
                                         "mergeable_state": state, "head": {"sha": sha}}
        self.checks[sha] = [{"name": check, "conclusion": conclusion}]

    def issue(self, number):
        return self.issues[number]

    def open_issues(self):
        return [i for i in self.issues.values() if i["state"] == "open"]

    def branch_names(self):
        return sorted(self.branches)

    def open_pull(self, branch):
        return self.pulls.get(branch)

    def check_runs(self, sha):
        return self.checks.get(sha, [])

    def claim_text(self, branch):
        return self.claims.get(branch)

    def released(self, branch):
        self.released_asked.append(branch)
        return branch in self.released_claims

    def create_label(self, name, colour):
        self.labels.add(name)

    def create_issue(self, title, body, labels):
        self.next_number += 1
        return self.add_issue(self.next_number, labels, body, title=title)

    def add_sub_issue(self, parent, child_id):
        self.sub_issues.append((parent, child_id))

    def set_body(self, number, body):
        self.issues[number]["body"] = body

    def remove_label(self, number, name):
        issue = self.issues[number]
        issue["labels"] = [label for label in issue["labels"] if label["name"] != name]


class ClientTest(unittest.TestCase):
    def test_the_fake_is_a_client(self):
        """The fake has every method the Protocol names, so a rule the
        tests pin is one the REST adapter can also be asked for."""
        self.assertIsInstance(FakeClient(), team.Client)

    def test_the_clerk_reads_the_labels_the_package_spells(self):
        """One spelling of the queue's labels and the holder prefixes, so
        the board and the clerk can never disagree on them."""
        self.assertEqual((team.TODO_LABEL, team.EPIC_LABEL, team.BLOCKER_LABEL, team.BLOCKED_LABEL),
                         (labels.TODO, labels.EPIC, labels.BLOCKER, labels.BLOCKED))
        self.assertEqual((team.AGENT_PREFIX, team.ACCOUNT_PREFIX), ("agent:", "account:"))
        self.assertEqual(team.worker_label("w1"), "worker:w1")
        self.assertEqual(team.CLAIM_PREFIX, "claude/")


class RouteTest(unittest.TestCase):
    def test_a_new_epic_is_planned_once(self):
        api = FakeClient()
        api.add_issue(1, ["epic"], body="Build the widget.")
        self.assertEqual(team.route(api, "labeled", 1, "epic"), "plan 1")
        api.issues[1]["body"] += "\n" + team.PLAN_MARKER
        self.assertEqual(team.route(api, "labeled", 1, "epic"), "none")

    def test_other_labels_and_closed_epics_plan_nothing(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        api.add_issue(2, ["epic"], state="closed")
        self.assertEqual(team.route(api, "labeled", 1, "todo"), "none")
        self.assertEqual(team.route(api, "labeled", 2, "epic"), "none")

    def test_the_last_task_closing_calls_for_integration(self):
        api = FakeClient()
        api.add_issue(1, ["epic"], body="spec\n<!-- team:tasks 2 3 -->")
        api.add_issue(2, body="Epic: #1\n\nfirst", state="closed")
        api.add_issue(3, body="Epic: #1\n\nsecond")
        self.assertEqual(team.route(api, "closed", 2), "none")
        api.issues[3]["state"] = "closed"
        self.assertEqual(team.route(api, "closed", 3), "integrate 1")

    def test_integration_can_be_called_with_the_epics_own_number(self):
        api = FakeClient()
        api.add_issue(1, ["epic"], body="spec\n<!-- team:tasks 2 -->")
        api.add_issue(2, body="Epic: #1", state="closed")
        self.assertEqual(team.route(api, "closed", 1), "integrate 1")

    def test_an_integration_already_claimed_is_not_started_twice(self):
        api = FakeClient()
        api.add_issue(1, ["epic"], body="spec\n<!-- team:tasks 2 -->")
        api.add_issue(2, body="Epic: #1", state="closed")
        api.branches.add("claude/1")
        api.add_pull(1)
        self.assertEqual(team.route(api, "closed", 2), "none")
        api.add_pull(1, draft=True)
        self.assertEqual(team.route(api, "closed", 2), "integrate 1")

    def test_a_blocked_task_calls_the_lead(self):
        api = FakeClient()
        api.add_issue(2, ["todo", "agent:w1", "blocked"], body="Epic: #1")
        api.add_issue(3, ["blocked"], body="not a task")
        self.assertEqual(team.route(api, "labeled", 2, "blocked"), "unblock 2")
        self.assertEqual(team.route(api, "labeled", 3, "blocked"), "none")
        api.issues[2]["labels"] = [{"name": "todo"}]
        self.assertEqual(team.route(api, "labeled", 2, "blocked"), "none")

    def test_a_closed_issue_outside_any_epic_routes_nowhere(self):
        api = FakeClient()
        api.add_issue(5, body="no epic here", state="closed")
        self.assertEqual(team.route(api, "closed", 5), "none")


class NextTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeClient()
        self.api.add_issue(1, ["epic"])

    def test_a_member_takes_its_own_task_before_the_open_queue(self):
        self.api.add_issue(2, ["todo"])
        self.api.add_issue(3, ["todo", "agent:w1"])
        self.assertEqual(team.next_for(self.api, "w1"), "take 3")
        self.assertEqual(team.next_for(self.api, "w2"), "take 2")

    def test_another_members_task_and_the_epic_are_never_taken(self):
        self.api.add_issue(2, ["todo", "agent:w2"])
        self.assertEqual(team.next_for(self.api, "w1"), "none")

    def test_a_task_given_without_todo_is_taken_and_an_epic_given_is_not(self):
        """F14: the board shows an issue a worker's label names under Given
        to workers whether or not it carries todo, and the worker takes it
        first; an epic given to a worker is the coordinator's to plan."""
        self.api.add_issue(2, ["todo"])
        self.api.add_issue(3, ["worker:w1"])
        self.assertEqual(team.next_for(self.api, "w1"), "take 3")
        self.api.issues[1]["labels"] = [{"name": "epic"}, {"name": "worker:w1"}]
        self.assertEqual(team.next_for(self.api, "w1"), "take 3")
        self.api.issues[3]["labels"] = [{"name": "worker:w1"}, {"name": "blocked"}]
        self.assertEqual(team.next_for(self.api, "w1"), "take 2")

    def test_an_epic_is_dealt_to_nobody_with_todo_or_as_a_blocker(self):
        """Pick never takes an epic, whatever section lists it, since the
        coordinator plans it (F15's fourth review): the clerk keeps an epic
        labelled todo or blocker out of the shared pool, and deals the next
        issue in its place."""
        self.api.issues[1]["labels"] = [{"name": "epic"}, {"name": "todo"}]
        self.api.add_issue(2, ["epic", "blocker"])
        self.assertEqual(team.next_for(self.api, "w1"), "none")
        self.api.add_issue(3, ["todo"])
        self.assertEqual(team.deals(self.api, ["w1", "w2"]), {"w1": "take 3", "w2": "none"})

    def test_a_claimed_task_is_not_taken_again(self):
        self.api.add_issue(2, ["todo"])
        self.api.branches.add("claude/2")
        self.assertEqual(team.next_for(self.api, "w1"), "none")

    def test_an_issue_labelled_claimed_is_dealt_to_nobody_branch_or_no_branch(self):
        """The board shows an issue labelled claimed in neither its Queue
        nor Given to workers, as one reopened after its merged branch was
        deleted, so a worker reading the board never takes it and the clerk
        never deals it; the worker it names takes its share of the queue."""
        self.api.add_issue(2, ["claimed", "in-review", "worker:w1"])
        self.api.add_issue(3, ["todo", "claimed"])
        self.assertEqual(team.next_for(self.api, "w1"), "none")
        self.assertEqual(team.next_for(self.api, "w2"), "none")
        self.api.add_issue(4, ["todo"])
        self.assertEqual(team.next_for(self.api, "w1"), "take 4")
        self.assertEqual(team.deals(self.api, ["w1", "w2"]), {"w1": "take 4", "w2": "none"})

    def test_a_branch_that_writes_the_number_with_a_leading_zero_claims_nothing(self):
        # Only claude/2 holds #2: GitHub would take a first push of claude/2
        # beside claude/02, so a worker sent to #2 can still claim it.
        self.api.add_issue(2, ["todo"])
        self.api.branches.add("claude/02")
        self.assertEqual(team.claim_branches(self.api), set())
        self.assertEqual(team.next_for(self.api, "w1"), "take 2")

    def test_a_claim_without_a_pull_request_or_in_draft_is_carried(self):
        self.api.add_issue(2, ["claimed", "agent:w1"])
        self.api.branches.add("claude/2")
        self.assertEqual(team.next_for(self.api, "w1"), "carry 2")
        self.api.add_pull(2, draft=True)
        self.assertEqual(team.next_for(self.api, "w1"), "carry 2")

    def test_a_claim_that_fell_behind_or_failed_is_carried(self):
        self.api.add_issue(2, ["claimed", "agent:w1"])
        self.api.branches.add("claude/2")
        self.api.add_pull(2, state="behind")
        self.assertEqual(team.next_for(self.api, "w1"), "carry 2")
        self.api.add_pull(2, state="blocked", conclusion="failure")
        self.assertEqual(team.next_for(self.api, "w1"), "carry 2")

    def test_a_ready_claim_a_check_or_a_review_holds_is_carried_once_its_branch_is_behind(self):
        """Where GitHub's word says only that a check or a review holds the
        pull request, the standing the adapter reads says whether its branch
        is behind its base or conflicts with it; the board tells it "main
        moved" then (F9), so the clerk deals the claim back for the same
        reason, and leaves it waiting while the branch is level."""
        self.api.add_issue(2, ["claimed", "agent:w1"])
        self.api.branches.add("claude/2")
        self.api.add_pull(2, state="blocked", conclusion=None)
        for standing, move in ((None, "none"), ("clean", "none"), ("unknown", "none"),
                               ("behind", "carry 2"), ("conflicting", "carry 2")):
            self.api.pulls["claude/2"]["standing"] = standing
            self.assertEqual(team.next_for(self.api, "w1"), move, standing)

    def test_a_claim_waiting_on_its_checks_or_merge_costs_no_run(self):
        self.api.add_issue(2, ["claimed", "agent:w1"])
        self.api.branches.add("claude/2")
        self.api.add_pull(2, state="blocked", conclusion=None)
        self.assertEqual(team.next_for(self.api, "w1"), "none")
        self.api.add_pull(2, state="clean")
        self.assertEqual(team.next_for(self.api, "w1"), "none")

    def test_a_blocked_task_waits_for_help(self):
        self.api.add_issue(2, ["todo", "agent:w1", "blocked"])
        self.assertEqual(team.next_for(self.api, "w1"), "none")

    def test_an_issue_labelled_blocked_is_dealt_from_neither_the_queue_nor_the_blockers(self):
        """F15: Pick never takes an issue labelled blocked, which waits on a
        peer or the coordinator, whether it carries todo or blocker."""
        self.api.add_issue(2, ["todo", "blocked"])
        self.api.add_issue(3, ["blocker", "blocked"])
        self.assertEqual(team.next_for(self.api, "w1"), "none")
        self.assertEqual(team.deals(self.api, ["w1", "w2"]), {"w1": "none", "w2": "none"})

    def test_a_task_another_account_claimed_is_never_carried(self):
        self.api.add_issue(6, ["todo", "agent:w1", "claimed", "account:second"])
        self.api.branches.add("claude/6")
        self.assertEqual(team.next_for(self.api, "w1"), "none")

    def test_a_claim_is_carried_by_the_worker_its_claim_file_names_whatever_the_labels_say(self):
        """F18: the issue still carries a's label, but b pushed the claim and
        has not announced it yet, so the claim is b's, as the board's Claims
        row and a release name it; until the fix the clerk read the labels
        alone, told a to carry b's branch and told b nothing."""
        self.api.add_issue(2, ["todo", "worker:a"])
        self.api.add_claim(2, "b")
        self.assertEqual(team.next_for(self.api, "a"), "none")
        self.assertEqual(team.next_for(self.api, "b"), "carry 2")
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "none", "b": "carry 2"})
        self.api.add_issue(3, ["todo"])
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "take 3", "b": "carry 2"})

    def test_where_the_claim_file_says_nothing_the_label_says_whose_the_claim_is(self):
        """The finishing pull request deletes the claim file, a hand may
        leave its Worker line out, and GitHub gives no text of a file over
        1 MB: the issue's label then names the holder, as a release names
        whose the claim was (coordinator.holder)."""
        self.api.add_issue(2, ["claimed", "in-review", "worker:a"])
        self.api.branches.add("claude/2")
        self.api.add_pull(2, state="behind")
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "carry 2", "b": "none"})
        for text in ("# Claim: #2\n\n## Plan\n\nNo worker line.\n", False):
            self.api.claims["claude/2"] = text
            self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "carry 2", "b": "none"}, text)

    def test_a_task_several_workers_labels_give_goes_to_the_first_of_them_in_the_run_s_order(self):
        """F18: the board shows such a task to each of them under Given to
        workers, and Pick has each take it, so the clerk deals it to the
        first of them with nothing else to do, and the others take their
        share of the rest; until the fix it went to none of them."""
        self.api.add_issue(2, ["todo", "worker:a", "agent:b"])
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "take 2", "b": "none"})
        self.assertEqual(team.deals(self.api, ["b", "a"]), {"b": "take 2", "a": "none"})
        self.assertEqual(team.deals(self.api, ["c", "b", "a"]), {"c": "none", "b": "take 2", "a": "none"})
        self.api.add_issue(3, ["todo"])
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "take 2", "b": "take 3"})
        for member in ("a", "b"):
            self.assertEqual(team.next_for(self.api, member, ["a", "b"]), team.deals(self.api, ["a", "b"])[member])

    def test_a_worker_carrying_its_claim_leaves_a_shared_task_to_the_next_of_them(self):
        self.api.add_issue(2, ["claimed", "worker:a"])
        self.api.add_claim(2, "a")
        self.api.add_issue(3, ["todo", "worker:a", "worker:b"])
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "carry 2", "b": "take 3"})
        self.assertEqual(team.next_for(self.api, "b", ["a", "b"]), "take 3")

    def test_a_released_claim_is_nobody_s_until_the_board_renames_its_branch(self):
        """The release took the labels off and the branch still stands: the
        board shows the issue unclaimed again, so the clerk sends its former
        holder back to it no more, and deals the issue to nobody either,
        since the name claude/N is not free until the rename (F18's review);
        the Client is asked once a look."""
        self.api.add_issue(2, ["todo"])
        self.api.add_claim(2, "a")
        self.api.released_claims.add("claude/2")
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "none", "b": "none"})
        self.assertEqual(self.api.released_asked, ["claude/2"])
        self.api.released_claims.clear()
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "carry 2", "b": "none"})

    def test_whether_a_claim_is_released_is_asked_only_where_a_release_is_possible(self):
        """A claim whose issue is labelled claimed or in-review, or whose own
        pull request is ready, cannot count as released (board.is_released),
        so the Client is not asked, which over REST costs the branch's head,
        its commit and the issue's comments."""
        self.api.add_claim(2, "a")
        self.api.released_claims.add("claude/2")
        for labels in (["claimed"], ["in-review"], ["claimed", "worker:b"]):
            self.api.add_issue(2, labels)
            self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "carry 2", "b": "none"}, labels)
        self.api.add_issue(2, ["todo"])
        self.api.add_pull(2, draft=False, state="behind")
        self.assertEqual(team.deals(self.api, ["a", "b"]), {"a": "carry 2", "b": "none"})
        self.assertEqual(self.api.released_asked, [])

    def test_a_task_given_to_one_alone_comes_before_a_shared_one(self):
        """A worker with a task of its own has something else to do, so a
        task it shares goes to the next of the workers it names, whatever
        the numbers' order; next_for answers a task given alone from the
        worker's own look, since no peer's move can change it, and a shared
        one as the run deals it."""
        self.api.add_issue(2, ["todo", "worker:a", "worker:b"])
        self.api.add_issue(3, ["todo", "worker:a"])
        for order in (["a", "b"], ["b", "a"]):
            self.assertEqual(team.deals(self.api, order), {"a": "take 3", "b": "take 2"}, order)
            for member in order:
                self.assertEqual(team.next_for(self.api, member, order), team.deals(self.api, order)[member])
        self.assertEqual(team.deals(self.api, ["a"]), {"a": "take 3"})
        self.assertEqual(team.deals(self.api, ["a", "c"]), {"a": "take 3", "c": "none"})

    def test_a_failure_outside_the_required_check_costs_no_run(self):
        self.api.add_issue(2, ["claimed", "agent:w1"])
        self.api.branches.add("claude/2")
        self.api.add_pull(2, state="unstable", conclusion="failure", check="board")
        self.assertEqual(team.next_for(self.api, "w1"), "none")

    def test_the_plain_worker_carries_claims_labelled_with_its_account(self):
        self.api.add_issue(5, ["claimed", "account:main"])
        self.api.branches.add("claude/5")
        self.assertEqual(team.next_for(self.api, "main"), "carry 5")

    def test_an_unclaimed_blocker_comes_first_even_without_todo(self):
        self.api.add_issue(3, ["todo"])
        self.api.add_issue(9, ["blocker"])
        self.assertEqual(team.next_for(self.api, "main"), "take 9")

    def test_members_with_nothing_of_their_own_divide_the_shared_work(self):
        self.api.add_issue(4, ["todo"])
        self.api.add_issue(5, ["todo"])
        members = ["w1", "w2", "w3"]
        answers = [team.next_for(self.api, m, members) for m in members]
        self.assertEqual(answers, ["take 4", "take 5", "none"])

    def test_a_member_busy_with_its_own_task_leaves_its_share_to_the_rest(self):
        self.api.add_issue(3, ["todo", "agent:w1"])
        self.api.add_issue(4, ["todo"])
        members = ["w1", "w2", "w3"]
        answers = [team.next_for(self.api, m, members) for m in members]
        self.assertEqual(answers, ["take 3", "take 4", "none"])

    def test_pull_requests_mixed_into_the_issue_list_are_left_out(self):
        self.api.add_issue(4, ["todo"])
        self.api.issues[4]["pull_request"] = {"url": "x"}
        self.api.add_issue(5, ["todo"])
        self.assertEqual(team.next_for(self.api, "w1"), "take 5")

    def test_a_worker_label_gives_a_task_as_the_older_spellings_did(self):
        self.api.add_issue(2, ["todo"])
        self.api.add_issue(3, ["todo", "worker:nightly"])
        self.assertEqual(team.next_for(self.api, "nightly", ["nightly", "w2"]), "take 3")
        self.assertEqual(team.next_for(self.api, "w2", ["nightly", "w2"]), "take 2")

    def test_shared_work_is_dealt_by_area(self):
        self.api.add_issue(4, ["todo"], body="### Files likely touched\n\nweb/app.js\n")
        self.api.add_issue(5, ["todo"], body="### Files likely touched\n\napi/server.py\n")
        self.api.add_issue(6, ["todo"], body="### Goal\n\nNo files named.\n")
        members = ["api", "web", "any"]
        areas = {"api": ("api/",), "web": ("web/**",)}
        answers = [team.next_for(self.api, m, members, areas) for m in members]
        self.assertEqual(answers, ["take 5", "take 4", "take 6"])

    def test_work_that_reaches_outside_an_area_waits_for_a_worker_it_fits(self):
        self.api.add_issue(4, ["todo"], body="## Files to touch\n\n- api/a.py\n- web/b.js\n")
        self.assertEqual(team.next_for(self.api, "api", ["api"], {"api": ("api/",)}), "none")
        self.assertEqual(team.next_for(self.api, "any", ["api", "any"], {"api": ("api/",)}), "take 4")

    def test_dealing_takes_each_issue_once_in_the_order_given(self):
        pool = [{"number": 1, "body": ""}, {"number": 2, "body": "### Files likely touched\n\ndocs/x.md\n"}]
        dealt = team.deal(["a", "b", "c"], pool, {"a": ("src/",)})
        self.assertEqual({member: issue["number"] for member, issue in dealt.items()}, {"a": 1, "b": 2})

    def test_main_makes_the_members_label_before_a_take(self):
        api = FakeClient()
        api.add_issue(4, ["todo"])
        self.assertEqual(team.next_move(api, "main", team.team_members(""), "account:main"), "take 4")
        self.assertIn("account:main", api.labels)

    def test_a_carry_or_a_wait_makes_no_label(self):
        self.api.add_issue(5, ["claimed", "agent:w1"])
        self.api.branches.add("claude/5")
        self.assertEqual(team.next_move(self.api, "w1", None, "agent:w1"), "carry 5")
        self.assertEqual(team.next_move(self.api, "w2", None, "agent:w2"), "none")
        self.assertEqual(self.api.labels, {"todo", "epic"})


class CountingClient(FakeClient):
    """The fake, counting the reads a look makes, and able to change what
    GitHub answers between two reads, as a push to main does between two
    workers' looks."""

    def __init__(self):
        super().__init__()
        self.reads = {"open_issues": 0, "branch_names": 0, "open_pull": 0}
        self.after_first_pull = None

    def open_issues(self):
        self.reads["open_issues"] += 1
        return super().open_issues()

    def branch_names(self):
        self.reads["branch_names"] += 1
        return super().branch_names()

    def open_pull(self, branch):
        self.reads["open_pull"] += 1
        pull = super().open_pull(branch)
        pull = None if pull is None else dict(pull)
        if self.after_first_pull:
            self.after_first_pull()  # what GitHub answers after this read
            self.after_first_pull = None
        return pull


class DealsTest(unittest.TestCase):
    """deals answers for every worker of a run from one look (F4): the
    workers' workflow asks once, in its first job, and hands each leg its
    move."""

    def held_ready_and_two_free(self, state="clean"):
        """F4's state: a holds #5, its pull request ready and, as given,
        clean or behind main; #6 and #7 are free."""
        api = CountingClient()
        api.add_issue(5, ["claimed", "worker:a"])
        api.branches.add("claude/5")
        api.add_pull(5, state=state)
        api.add_issue(6, ["todo"])
        api.add_issue(7, ["todo"])
        return api

    def test_one_look_gives_each_worker_what_next_for_gives_it(self):
        """c was given #8 and takes it whatever the pool holds; the pool
        goes to the idle members in team order, a among them only while
        its pull request is clean; and a worker asking alone gets the
        same move from the same state."""
        expected = {"clean": {"a": "take 6", "b": "take 7", "c": "take 8", "d": "none"},
                    "behind": {"a": "carry 5", "b": "take 6", "c": "take 8", "d": "take 7"}}
        for state, moves in expected.items():
            api = self.held_ready_and_two_free(state)
            api.add_issue(8, ["todo", "worker:c"])
            members = ["a", "b", "c", "d"]
            with self.subTest(state=state):
                self.assertEqual(team.deals(api, members), moves)
                self.assertEqual({m: team.next_for(api, m, members) for m in members}, moves)

    def test_the_run_reads_the_issues_the_branches_and_each_claim_once(self):
        api = self.held_ready_and_two_free()
        team.deals(api, ["a", "b", "c"])
        self.assertEqual(api.reads, {"open_issues": 1, "branch_names": 1, "open_pull": 1})

    def test_one_issue_is_never_dealt_to_two_workers_of_one_run(self):
        """F4: a leg that looked while a's pull request was clean dealt a
        the next issue and b the one after; a leg that looked after main
        moved saw a carrying #5 and dealt b's leg #6, the same issue. From
        one look the run's takes are distinct, whichever state it saw."""
        members = ["a", "b", "c"]
        clean, behind = self.held_ready_and_two_free("clean"), self.held_ready_and_two_free("behind")
        self.assertEqual(team.deals(clean, members), {"a": "take 6", "b": "take 7", "c": "none"})
        self.assertEqual(team.deals(behind, members), {"a": "carry 5", "b": "take 6", "c": "take 7"})
        from_own_looks = [team.next_for(clean, "a", members), team.next_for(behind, "b", members)]
        self.assertEqual(from_own_looks, ["take 6", "take 6"], "the double deal each leg's own look made")

    def test_a_worker_with_work_of_its_own_reads_no_peer_s_pull_request(self):
        """next_for answers a worker's own work before it looks at the
        peers, as it did before F4's fix: deals gives such a worker its own
        move whatever the others do, and each peer's pull request costs a
        call, which a leg that still asks for itself would pay for nothing."""
        api = self.held_ready_and_two_free("behind")
        api.add_issue(8, ["claimed", "worker:b"])
        api.branches.add("claude/8")
        api.add_pull(8, state="clean")
        self.assertEqual(team.next_for(api, "a", ["a", "b", "c"]), "carry 5")
        self.assertEqual(api.reads["open_pull"], 1, "a's own pull request alone")
        self.assertEqual(team.deals(api, ["a", "b", "c"]), {"a": "carry 5", "b": "take 6", "c": "take 7"})

    def test_a_name_repeated_is_one_member_dealt_once(self):
        """A repetition dealt its own issue would leave that issue to
        nobody this run."""
        api = self.held_ready_and_two_free()
        self.assertEqual(team.deals(api, ["b", "b", "c"]), {"b": "take 6", "c": "take 7"})
        self.assertEqual(team.deal_moves(api, ["a"], run=["z", "z"]), {"z": "take 7"})

    def test_a_change_during_the_look_cannot_split_the_run(self):
        """Main moves past a's pull request right after the run's one read
        of it: the run deals what it saw, a idle and the pool to a and b,
        and reads the pull request no second time; a leg looking for
        itself afterwards sees a carrying #5 and is dealt #6 again."""
        api = self.held_ready_and_two_free("clean")

        def main_moves():
            api.pulls["claude/5"]["mergeable_state"] = "behind"

        api.after_first_pull = main_moves
        self.assertEqual(team.deals(api, ["a", "b", "c"]), {"a": "take 6", "b": "take 7", "c": "none"})
        self.assertEqual(api.reads["open_pull"], 1)
        self.assertEqual(team.next_for(api, "b", ["a", "b", "c"]), "take 6", "what a later look deals b")

    def test_the_moves_of_one_run_and_the_labels_made_for_its_takes(self):
        api = self.held_ready_and_two_free()
        labels = {"a": "worker:a", "b": "worker:b", "c": "worker:c"}
        moves = team.deal_moves(api, ["a", "b", "c"], labels=labels, run=["b", "c"])
        self.assertEqual(moves, {"b": "take 7", "c": "none"}, "a is dealt #6 though it is not in this run")
        self.assertEqual(api.labels, {"todo", "epic", "worker:b"})

    def test_a_worker_of_the_run_outside_the_team_is_dealt_after_it(self):
        api = self.held_ready_and_two_free()
        self.assertEqual(team.deal_moves(api, ["a"], run=["z", "a"]), {"z": "take 7", "a": "take 6"})
        self.assertEqual(team.deal_moves(api, ["a", "b"]), {"a": "take 6", "b": "take 7"})

    def test_shared_work_is_dealt_by_area_in_one_look(self):
        api = CountingClient()
        api.add_issue(4, ["todo"], body="### Files likely touched\n\nweb/app.js\n")
        api.add_issue(5, ["todo"], body="### Files likely touched\n\napi/server.py\n")
        areas = {"api": ("api/",), "web": ("web/**",)}
        self.assertEqual(team.deals(api, ["api", "web"], areas), {"api": "take 5", "web": "take 4"})


class SeedTest(unittest.TestCase):
    PLAN = {"plan": "w1 owns the parser, w2 the printer.",
            "tasks": [{"title": "Parser", "owner": "w1", "files": ["src/parse.py"], "body": "Parse it."},
                      {"title": "Printer", "owner": "nobody", "files": [], "body": "Print it."}]}

    def test_seed_opens_owned_tasks_and_writes_the_plan_once(self):
        api = FakeClient()
        api.add_issue(1, ["epic"], body="Build the widget.")
        self.assertEqual(team.seed(api, 1, self.PLAN, ["w1", "w2"]), "seeded 101 102")
        parser, printer = api.issues[101], api.issues[102]
        self.assertEqual(team.label_names(parser), ["todo", "worker:w1"])
        self.assertEqual(labels.worker_of(printer), "w2")
        self.assertIn("- [ ] #101 Parser (worker:w1)", api.issues[1]["body"])
        self.assertEqual(team.epic_of(parser), 1)
        self.assertIn("- src/parse.py", parser["body"])
        self.assertEqual(team.task_numbers(api.issues[1]), [101, 102])
        self.assertIn("w1 owns the parser", api.issues[1]["body"])
        self.assertEqual(len(api.sub_issues), 2)
        self.assertEqual(team.seed(api, 1, self.PLAN, ["w1", "w2"]), "already planned")
        self.assertEqual(len(api.issues), 3)

    def test_seed_takes_the_epic_off_the_queue(self):
        api = FakeClient()
        api.add_issue(1, ["epic", "todo"])
        team.seed(api, 1, self.PLAN, ["w1", "w2"])
        self.assertEqual(team.label_names(api.issues[1]), ["epic"])

    def test_seed_makes_each_members_label(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        team.seed(api, 1, self.PLAN, ["w1", "w2"])
        self.assertEqual(api.labels, {"todo", "epic", "worker:w1", "worker:w2"})

    def test_with_no_worker_registered_the_tasks_go_to_the_open_queue(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        self.assertEqual(team.seed(api, 1, self.PLAN, []), "seeded 101 102")
        self.assertEqual(team.label_names(api.issues[101]), ["todo"])
        self.assertIn("- [ ] #101 Parser\n", api.issues[1]["body"])
        self.assertEqual(api.labels, {"todo", "epic"})

    def test_a_sub_issue_link_that_fails_does_not_fail_the_seed(self):
        class Unlinked(FakeClient):
            def add_sub_issue(self, parent, child_id):
                raise team.ClientError("POST sub_issues: HTTP 404")
        api = Unlinked()
        api.add_issue(1, ["epic"])
        self.assertEqual(team.seed(api, 1, self.PLAN, ["w1", "w2"]), "seeded 101 102")
        self.assertEqual(api.sub_issues, [])

    def test_an_owner_off_the_team_goes_to_a_member_without_a_task(self):
        tasks = [{"owner": "w2"}, {"owner": "W1"}, {"owner": "w3"}]
        self.assertEqual(team.owners_for(tasks, ["w1", "w2", "w3"]), ["w2", "w1", "w3"])
        self.assertEqual(team.owners_for([{}, {}, {}], ["w1", "w2"]), ["w1", "w2", "w1"])

    def test_seeded_tasks_route_to_integration_when_all_close(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        team.seed(api, 1, self.PLAN, ["w1", "w2"])
        for number in (101, 102):
            api.issues[number]["state"] = "closed"
        self.assertEqual(team.route(api, "closed", 102), "integrate 1")

    def test_a_plan_without_tasks_is_refused(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        with self.assertRaises(team.TeamError):
            team.seed(api, 1, {"plan": "x", "tasks": []}, ["w1"])

    def test_a_task_body_names_the_epic_and_the_files(self):
        body = team.task_body(7, {"body": " Parse it. ", "files": ["a.py", "b.py"]})
        self.assertTrue(body.startswith("Epic: #7\n\nParse it.\n\n## Files to touch\n\n- a.py\n- b.py\n"))
        self.assertIn("- (none named)", team.task_body(7, {"body": "x"}))
        self.assertEqual(claims.issue_files(team.task_body(7, {"body": "x"})), [])
        self.assertEqual(claims.issue_files(team.task_body(7, {"files": ["b.py", "a.py"]})), ["a.py", "b.py"])


class PlanSchemaTest(unittest.TestCase):
    def test_a_task_goes_to_a_registered_worker_or_with_none_to_anyone(self):
        given = team.plan_schema(["alice", "nightly"])
        task = given["properties"]["tasks"]["items"]
        self.assertEqual(task["properties"]["owner"], {"type": "string", "enum": ["alice", "nightly"]})
        self.assertEqual(task["required"], ["title", "owner", "files", "body"])
        self.assertEqual(given["required"], ["plan", "tasks"])
        self.assertEqual(team.plan_schema([])["properties"]["tasks"]["items"]["properties"]["owner"], {"type": "string"})

    def test_a_plan_the_schema_admits_is_one_seed_reads(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        plan = {"plan": "p", "tasks": [{"title": "T", "owner": "alice", "files": ["a.py"], "body": "b"}]}
        self.assertEqual(team.seed(api, 1, plan, ["alice"]), "seeded 101")


class MembersTest(unittest.TestCase):
    def test_team_members_reads_the_variable(self):
        self.assertEqual(team.team_members('["w1", "w2"]'), ["w1", "w2"])
        self.assertEqual(team.team_members(""), [])
        for bad in ("w1,w2", '{"w1": 1}', '[""]', "[1]"):
            with self.assertRaises(team.TeamError, msg=bad):
                team.team_members(bad)


if __name__ == "__main__":
    unittest.main()
