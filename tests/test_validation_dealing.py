"""The dealing lane (validation/dealing.py, DESIGN.md 9.1): three workers'
legs at any moments of one model, dealt as the workers' workflow deals,
once for the run from one look; no issue is dealt twice (G14), where each
leg's own look dealt one twice until F4's fix; and the clerk's client over
the model answers as GitHub does."""

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
