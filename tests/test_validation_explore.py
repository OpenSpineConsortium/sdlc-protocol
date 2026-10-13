"""The harness's first search (validation/explore.py): every interleaving of
two workers' verbs on one issue to depth 4 breaks no guarantee on the
library as it stands; a mutant is found on a shortest trace; and the
search goes on past a violation instead of pruning there."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims  # noqa: E402
from validation import cycle, explore, model as M, verbs  # noqa: E402


def start():
    model = M.seeded()
    cycle.board_run(model)
    return model


def at_exactly_24_hours(last, now, idle_after=claims.IDLE_AFTER):
    return now - claims.parse_time(last) >= idle_after


class TheSearch(unittest.TestCase):
    def test_two_workers_on_one_issue_to_depth_4_break_nothing(self):
        found = explore.explore(start(), verbs.Scope(), 4)
        self.assertGreater(found.states, 1000)
        self.assertEqual([(f.guarantee, f.detail, f.trace) for f in found.found], [])

    def test_a_mutant_is_found_on_a_shortest_trace(self):
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            found = explore.explore(start(), verbs.Scope(), 3)
        shortest = min(found.found, key=lambda f: len(f.trace))
        self.assertEqual(shortest.trace, ("claim a 1", "wait clock 24", "board board schedule "))

    def test_it_goes_on_past_a_violation(self):
        """The prototype stopped at every violating state; here the states
        after one are explored, so the same violation is met again deeper,
        on the paths that go through it."""
        scope = verbs.Scope(workers=("a",))
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            mutated = explore.explore(start(), scope, 4)
        plain = explore.explore(start(), scope, 4)
        self.assertEqual(mutated.depth, plain.depth)
        self.assertGreater(sum(f.count for f in mutated.found), len(mutated.found))
        self.assertTrue(any(len(f.trace) == 4 for f in mutated.found))


if __name__ == "__main__":
    unittest.main()
