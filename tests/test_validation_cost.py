"""What a board run costs in calls to GitHub, on the fake (validation/cost.py),
held to what README.md says of it."""

import itertools
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import cost, cycle, verbs  # noqa: E402
from validation.model import HOUR  # noqa: E402

README = ROOT / "README.md"


def pages(items):
    """The calls a list takes, a hundred items a page and one for none."""
    return max(1, -(-items // 100))


def nothing_due(case):
    """The README's rule for a run with nothing due, on the fake: the four
    lists, a page each a hundred items (GitHub lists the open pull
    requests with the open issues); a claim's file, its issue where it is
    closed, and its head's date unless it is finished or its issue closed;
    three calls a pull request; and one permission read for the stranger
    the strangers' pull requests share."""
    open_issues = case.claims - case.closed + case.queued
    pulls = case.drafts + case.strangers
    lists = pages(open_issues + pulls) + pages(pulls) + pages(case.claims + 1) + 1
    dated = case.claims - case.closed - case.ready
    return lists + case.claims + case.closed + dated + 3 * pulls + (1 if case.strangers else 0)


def small_cases(most):
    """Every repository of up to most claims the rule speaks of: drafts,
    ready ones among them, strangers' pull requests and closed issues
    among the claims that have no draft."""
    for claims in range(most + 1):
        for drafts, strangers in itertools.product(range(claims + 1), repeat=2):
            for ready, closed in itertools.product(range(drafts + 1), range(claims + 1)):
                if drafts + strangers + closed <= claims:
                    yield cost.Case("", claims=claims, drafts=drafts, ready=ready, strangers=strangers,
                                    closed=closed)


class TheCost(unittest.TestCase):
    def test_the_readme_s_table_is_what_the_fake_measures(self):
        self.assertIn(cost.table(), README.read_text(encoding="utf-8"),
                      "README.md, What a board run costs: paste python3 scripts/validate.py cost")

    def test_a_run_with_nothing_due_costs_what_the_readme_s_rule_says(self):
        """The rule a reader takes past the table's rows, at every small
        size and past a page of a hundred, both runs alike."""
        for case in itertools.chain(small_cases(4), (cost.Case("", queued=150), cost.Case("", claims=101),
                                                     cost.Case("", claims=12, drafts=12, queued=95))):
            with self.subTest(case=case):
                first, then = cost.measure(case)
                self.assertEqual(first, then)
                self.assertEqual(first, cost.Cost(nothing_due(case), nothing_due(case), 0))

    def test_a_release_costs_nine_calls_more_five_of_them_writes_and_ten_with_a_draft(self):
        for drafts, more in ((0, 9), (1, 10)):
            with self.subTest(drafts=drafts):
                busy, _ = cost.measure(cost.Case("", claims=1, drafts=drafts))
                released, after = cost.measure(cost.Case("", claims=1, drafts=drafts, idle=True))
                self.assertEqual((released.calls - busy.calls, released.writes), (more, 5))
                self.assertEqual(after, cost.Cost(4, 4, 0),
                                 "the released claim's branch is renamed, so it costs nothing after")

    def test_each_move_of_main_costs_a_comment_on_each_pull_request_behind_and_a_read_a_run(self):
        """The comment names main's head (board.MAIN_MOVED_MARKER), so main
        moving again under the same drafts owes each of them one more."""
        busy, _ = cost.measure(cost.Case("", claims=10, drafts=10))
        case = cost.Case("", claims=10, drafts=10, moves=1)
        moved, after = cost.measure(case)
        self.assertEqual((moved.writes, after.calls - busy.calls, after.writes), (10, 10, 0))
        model = cost.seeded(case)
        cycle.board_run(model)
        cycle.board_run(model)
        verbs.move_main(model)
        self.assertEqual(cost.cost_of(cycle.board_run(model)), moved)

    def runs(self, case, hours):
        """The cost of a board run after each wait of hours, on one model."""
        model = cost.seeded(case)
        found = []
        for wait in hours:
            model.now += wait * HOUR
            found.append(cost.cost_of(cycle.board_run(model)))
        return found

    def test_a_pull_request_nobody_touched_for_a_day_costs_a_read_of_its_comments_a_run(self):
        """The board asks it once whether it is parked (board.comments_due),
        its question putting the next read off by a day, and reads its
        comments in every run after: a ready pull request waiting for
        review costs one call a run more than the rule's from a day after
        the board asked."""
        case = cost.Case("", claims=1, drafts=1, ready=1)
        rule = nothing_due(case)
        self.assertEqual(self.runs(case, (0, 25, 0, 25, 0)),
                         [cost.Cost(rule, rule, 0), cost.Cost(rule + 2, rule + 1, 1), cost.Cost(rule, rule, 0),
                          cost.Cost(rule + 1, rule + 1, 0), cost.Cost(rule + 1, rule + 1, 0)])

    def test_a_draft_said_finished_costs_a_read_of_its_comments_a_run_once_a_day_passes(self):
        """Its claim is dated as any draft's, and once its branch has gone a
        day without a commit the coordinator reads its comments in every
        run for the word that keeps it from a release, beside the idle
        read every pull request untouched for a day costs."""
        case = cost.Case("", claims=1, drafts=1, finished=1)
        rule = nothing_due(case)
        self.assertEqual(self.runs(case, (0, 24, 0, 25, 0)),
                         [cost.Cost(rule, rule, 0), cost.Cost(rule + 3, rule + 2, 1),
                          cost.Cost(rule + 1, rule + 1, 0), cost.Cost(rule + 2, rule + 2, 0),
                          cost.Cost(rule + 2, rule + 2, 0)])


if __name__ == "__main__":
    unittest.main()
