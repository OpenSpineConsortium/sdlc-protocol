"""The dealing lane (validation/dealing.py, DESIGN.md 9.1): three workers'
legs at any moments of one model, dealt as the workers' workflow deals,
once for the run from one look; no issue is dealt twice (G14), where each
leg's own look dealt one twice until F4's fix; the clerk's deal from one
look at every state a person can label an issue into, held to the spec's
Pick and Planning, where the clerk parted from them until F18's fix; and
the clerk's client over the model answers as GitHub does."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import team  # noqa: E402
from validation import clerk, dealing, mutants  # noqa: E402
from validation.clerk import ModelClient  # noqa: E402


class TheLane(unittest.TestCase):
    def test_no_double_deal_from_any_looks(self):
        lane = dealing.run_lane()
        self.assertEqual(lane.deals, 54)
        self.assertEqual(lane.distinct, 6, "one deal per ordering and run moment, since the run looks once")
        self.assertEqual(lane.doubled_from_one_look, ())
        self.assertEqual(lane.doubled_from_differing_looks, ())
        self.assertIn("54 deals over every assignment of moments to the legs, 6 distinct", dealing.report(lane))
        self.assertEqual(dealing.doubled(lane), ())
        self.assertEqual(dealing.main(), 0)

    def test_each_leg_from_its_own_look_dealt_one_issue_twice_until_the_fix(self):
        """F4, as the lane found it: fifteen of the 54 deals doubled, the
        first after main moved and a's pull request fell behind, from looks
        that differ on it."""
        with mutants.switched_on(mutants.MUTANTS["each leg dealt from its own look"]):
            lane = dealing.run_lane()
        self.assertEqual(lane.doubled_from_one_look, ())
        self.assertEqual(len(lane.doubled_from_differing_looks), 15)
        self.assertEqual(lane.distinct, 14, "each leg's own look tells the moments apart")
        first = lane.doubled_from_differing_looks[0]
        self.assertEqual((first.order, first.looks), (("main-moves", "outsider-claims"), (0, 0, 1)))
        self.assertEqual(first.moves, {"a": "take 5", "b": "take 6", "c": "take 6"})
        self.assertIn("F4", dealing.report(lane))

    def test_the_run_looks_no_later_than_its_earliest_leg(self):
        clients = dealing.moments(("main-moves", "outsider-claims"))
        self.assertEqual(clerk.workflow_deals(clients, dealing.WORKERS, (2, 0, 1)),
                         {"a": "take 5", "b": "take 6", "c": "none"})
        self.assertEqual(clerk.workflow_deals(clients, dealing.WORKERS, (1, 2, 1)),
                         {"a": "carry 4", "b": "take 5", "c": "take 6"})
        self.assertEqual(clerk.workflow_deals(clients, dealing.WORKERS, (2, 2, 2)),
                         {"a": "carry 4", "b": "take 6", "c": "none"})

    def test_the_start_is_a_held_ready_claim_and_two_free_issues(self):
        client = ModelClient(dealing.start())
        self.assertEqual(team.next_for(client, "a", list(dealing.WORKERS)), "take 5", "a's claim needs nothing")
        self.assertEqual(team.next_for(client, "b", list(dealing.WORKERS)), "take 6")
        self.assertEqual(team.next_for(client, "c", list(dealing.WORKERS)), "none")

    def test_main_moving_gives_the_holder_work_again(self):
        model = dealing.start()
        dealing.change(model, "main-moves")
        client = ModelClient(model)
        self.assertEqual(team.next_for(client, "a", list(dealing.WORKERS)), "carry 4")
        self.assertEqual(team.next_for(client, "b", list(dealing.WORKERS)), "take 5")

    def test_an_outsiders_claim_leaves_the_pool(self):
        model = dealing.start()
        dealing.change(model, "outsider-claims")
        client = ModelClient(model)
        self.assertIn("claude/5", client.branch_names())
        self.assertEqual(team.next_for(client, "a", list(dealing.WORKERS)), "take 6", "a is idle, so first")
        self.assertEqual(team.next_for(client, "b", list(dealing.WORKERS)), "none")


class TheClerkAgainstPick(unittest.TestCase):
    """F18: each case's deal and each worker's own answer, held to Pick and
    Planning as the oracle reads them (oracle.deal_problems). The lane over
    every case runs in dealing.main, which the first test of TheLane holds
    to no problem; these are its cases, and the finding as the lane met
    it, by the mutants that undo the fix."""

    def test_the_cases_are_every_mix_but_in_review_under_every_claim_in_three_settings(self):
        mixes = dealing.label_mixes()
        self.assertEqual(len(mixes), 1024)
        self.assertFalse(any("in-review" in names for names in mixes))
        cases = list(dealing.picking_cases())
        self.assertEqual(len(cases), 1024 * len(dealing.CLAIM_STATES) * 5)
        self.assertEqual({case.state for case in cases}, set(dealing.CLAIM_STATES))
        self.assertEqual({case.team for case in cases}, {("a", "b"), ("b", "a"), ("a",)})

    def test_a_claim_is_carried_by_the_worker_its_file_names_whatever_the_labels_say(self):
        case = dealing.Case({1: ("todo", "worker:a")}, 1, "b", ("a", "b"))
        self.assertEqual(dealing.judge_case(case), ({"a": "none", "b": "carry 1"}, []))
        with mutants.switched_on(mutants.MUTANTS["the clerk carrying by labels alone"]):
            self.assertEqual(dealing.judge_case(case), (
                {"a": "carry 1", "b": "none"},
                ["a carries #1, whose claim is b's", "b carries nothing though its claim on #1 is its own"]))

    def test_a_claim_whose_file_names_nobody_is_the_worker_label_s_before_the_older_spellings(self):
        case = dealing.Case({1: ("todo", "agent:a", "worker:b")}, 1, "nobody", ("a", "b"))
        self.assertEqual(dealing.judge_case(case), ({"a": "none", "b": "carry 1"}, []))

    def test_a_task_two_workers_labels_give_goes_to_the_first_of_them_with_nothing_else_to_do(self):
        case = dealing.Case({1: ("todo", "worker:a", "worker:b")}, 1, "none", ("b", "a"))
        self.assertEqual(dealing.judge_case(case), ({"b": "take 1", "a": "none"}, []))
        with mutants.switched_on(mutants.MUTANTS["a shared task dealt to none"]):
            moves, problems = dealing.judge_case(case)
        self.assertEqual(moves, {"b": "none", "a": "none"})
        self.assertIn("#1, given to a, b, goes to none though b has nothing else to do", problems)

    def test_a_task_given_alone_comes_before_one_given_to_several(self):
        case = dealing.Case({1: ("todo", "worker:a", "worker:b"), 2: ("todo", "worker:a")}, 1, "none", ("a", "b"))
        self.assertEqual(dealing.judge_case(case), ({"a": "take 2", "b": "take 1"}, []))
        with mutants.switched_on(mutants.MUTANTS["a shared task before one given alone"]):
            self.assertEqual(dealing.judge_case(case),
                             ({"a": "take 1", "b": "none"}, ["a takes #1 though #2 is given to it alone"]))

    def test_a_blocker_before_the_queue_and_nothing_labelled_blocked(self):
        """The two rules of the clerk F15's fifth review found no catcher of,
        which the lane holds now."""
        blocker = dealing.Case({1: ("todo",), 2: ("blocker",)}, 2, "none", ("a",))
        self.assertEqual(dealing.judge_case(blocker), ({"a": "take 2"}, []))
        with mutants.switched_on(mutants.MUTANTS["the clerk dealing blockers in number order"]):
            self.assertEqual(dealing.judge_case(blocker)[1], ["a takes #1, where Pick has it take from blocker [2]"])
        blocked = dealing.Case({1: ("blocked", "todo")}, 1, "none", ("a", "b"))
        self.assertEqual(dealing.judge_case(blocked), ({"a": "none", "b": "none"}, []))
        with mutants.switched_on(mutants.MUTANTS["the clerk dealing an issue labelled blocked"]):
            self.assertEqual(dealing.judge_case(blocked)[1], ["a takes #1, where Pick has it take from nothing []"])

    def test_a_claim_labelled_blocked_is_not_carried(self):
        case = dealing.Case({1: ("blocked", "todo")}, 1, "a", ("a", "b"))
        self.assertEqual(dealing.judge_case(case), ({"a": "none", "b": "none"}, []))
        with mutants.switched_on(mutants.MUTANTS["a blocked claim carried"]):
            self.assertEqual(dealing.judge_case(case)[1],
                             ["a carries #1, labelled blocked, which waits on a peer or the coordinator"])


class TheClient(unittest.TestCase):
    def test_it_answers_the_clerks_calls_in_githubs_shapes(self):
        client = ModelClient(dealing.start())
        issue = client.issue(4)
        self.assertEqual((issue["number"], issue["state"]), (4, "open"))
        self.assertIn({"name": "worker:a"}, issue["labels"])
        self.assertEqual([i["number"] for i in client.open_issues()], [4, 5, 6])
        pull = client.open_pull("claude/4")
        self.assertEqual((pull["draft"], pull["mergeable_state"], len(pull["head"]["sha"])), (False, "clean", 40))
        self.assertIsNone(client.open_pull("claude/5"))
        self.assertEqual(client.check_runs(pull["head"]["sha"]), [])

    def test_a_snapshot_is_a_look_that_later_changes_leave_alone(self):
        client = ModelClient(dealing.start())
        look = client.snapshot()
        dealing.change(client.model, "outsider-claims")
        self.assertIn("claude/5", client.branch_names())
        self.assertNotIn("claude/5", look.branch_names())

    def test_its_writes_make_issues_the_board_reads(self):
        client = ModelClient(dealing.start())
        made = client.create_issue("a task", "Epic: #4\n", ["todo", "worker:b"])
        self.assertEqual(made["number"], 7)
        self.assertEqual(client.issue(7)["labels"], [{"name": "todo"}, {"name": "worker:b"}])
        client.set_body(7, "changed")
        client.remove_label(7, "todo")
        self.assertEqual((client.issue(7)["body"], client.issue(7)["labels"]), ("changed", [{"name": "worker:b"}]))
        self.assertIsInstance(client, team.Client)


if __name__ == "__main__":
    unittest.main()
