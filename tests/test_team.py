"""Team mode's clerk decides with no Claude usage, so its decisions are
pinned here against an in-memory GitHub: when the lead plans and when it
integrates, what a member carries or takes, and what seed writes. The
fake below is the Client the clerk's rules are written against; the
adapter over REST implements the same methods."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import team  # noqa: E402


class FakeClient:
    """Just enough of GitHub for team.py, kept in dictionaries: the Client
    methods the clerk calls, over the dictionaries GitHub would return."""

    def __init__(self):
        self.issues, self.branches, self.pulls, self.checks = {}, set(), {}, {}
        self.labels, self.sub_issues = {"todo", "epic"}, []
        self.next_number = 100

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

    def test_a_claimed_task_is_not_taken_again(self):
        self.api.add_issue(2, ["todo"])
        self.api.branches.add("claude/2")
        self.assertEqual(team.next_for(self.api, "w1"), "none")

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

    def test_a_task_another_account_claimed_is_never_carried(self):
        self.api.add_issue(6, ["todo", "agent:w1", "claimed", "account:second"])
        self.api.branches.add("claude/6")
        self.assertEqual(team.next_for(self.api, "w1"), "none")

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


class SeedTest(unittest.TestCase):
    PLAN = {"plan": "w1 owns the parser, w2 the printer.",
            "tasks": [{"title": "Parser", "owner": "w1", "files": ["src/parse.py"], "body": "Parse it."},
                      {"title": "Printer", "owner": "nobody", "files": [], "body": "Print it."}]}

    def test_seed_opens_owned_tasks_and_writes_the_plan_once(self):
        api = FakeClient()
        api.add_issue(1, ["epic"], body="Build the widget.")
        self.assertEqual(team.seed(api, 1, self.PLAN, ["w1", "w2"]), "seeded 101 102")
        parser, printer = api.issues[101], api.issues[102]
        self.assertEqual(team.agent_of(parser), "w1")
        self.assertEqual(team.agent_of(printer), "w2")
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
        self.assertEqual(team.label_names(api.issues[1]), {"epic"})

    def test_seed_makes_each_members_label(self):
        api = FakeClient()
        api.add_issue(1, ["epic"])
        team.seed(api, 1, self.PLAN, ["w1", "w2"])
        self.assertEqual(api.labels, {"todo", "epic", "agent:w1", "agent:w2"})

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
        self.assertIn("- (the lead named none)", team.task_body(7, {"body": "x"}))


class MembersTest(unittest.TestCase):
    def test_team_members_reads_the_variable(self):
        self.assertEqual(team.team_members('["w1", "w2"]'), ["w1", "w2"])
        self.assertEqual(team.team_members(""), [])
        for bad in ("w1,w2", '{"w1": 1}', '[""]', "[1]"):
            with self.assertRaises(team.TeamError, msg=bad):
                team.team_members(bad)


if __name__ == "__main__":
    unittest.main()
