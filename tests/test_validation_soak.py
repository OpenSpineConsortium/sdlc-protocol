"""The soak (validation/soak.py): seeded random walks of five workers and
three people over six issues with every fault kind, each worker under a
policy of its own, that break no guarantee and reach the board's work:
claims, releases, renames and merges. The deep lane walks a thousand
seeds for two hundred steps; here a few seeds walk a few steps."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims  # noqa: E402
from validation import soak  # noqa: E402


def at_exactly_24_hours(last, now, idle_after=claims.IDLE_AFTER):
    return now - claims.parse_time(last) >= idle_after


class TheSoak(unittest.TestCase):
    def test_a_few_walks_break_nothing_and_reach_the_board_s_work(self):
        walks = [soak.walk(seed, 60) for seed in range(3)]
        for walk in walks:
            self.assertEqual(walk.violations, ())
            self.assertEqual(walk.steps, 60)
        counts = soak.summary(walks)["counts"]
        self.assertGreater(counts.get("claim", 0), 0)
        self.assertGreater(counts.get("board", 0), 0)
        self.assertTrue(any(kind in counts for kind in ("rename", "merge", "comment:claim-released")), counts)

    def test_the_same_seed_walks_the_same_path(self):
        self.assertEqual(soak.walk(7, 30).verbs, soak.walk(7, 30).verbs)
        self.assertNotEqual(soak.walk(7, 30).verbs, soak.walk(8, 30).verbs)

    def test_a_mutant_is_met_on_a_walk_and_the_step_is_named(self):
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            walks = [soak.walk(seed, 80) for seed in range(6)]
        met = [v for w in walks for v in w.violations]
        self.assertTrue(met, "no walk of six met the mutant")
        self.assertTrue(all(0 <= at < 80 for _, _, at in met))


if __name__ == "__main__":
    unittest.main()
