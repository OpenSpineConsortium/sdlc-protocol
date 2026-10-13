"""The dealing lane (validation/dealing.py, DESIGN.md 9.1): three workers
ask the clerk at once over snapshots of one model; from one look no issue
is dealt twice (G14), and from differing looks the known double deal, F4,
shows; and the clerk's client over the model answers as GitHub does."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import team  # noqa: E402
from validation import dealing  # noqa: E402
from validation.clerk import ModelClient  # noqa: E402


class TheLane(unittest.TestCase):
    def test_no_double_deal_from_one_look_and_f4_from_differing_looks(self):
        lane = dealing.run_lane()
        self.assertEqual(lane.deals, 54)
        self.assertEqual(lane.doubled_from_one_look, ())
        self.assertTrue(lane.doubled_from_differing_looks)
        first = lane.doubled_from_differing_looks[0]
        self.assertEqual(first.order, ("main-moves", "outsider-claims"))
        self.assertFalse(first.one_look)
        self.assertTrue(first.doubled)
        self.assertIn("F4", dealing.report(lane))

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
