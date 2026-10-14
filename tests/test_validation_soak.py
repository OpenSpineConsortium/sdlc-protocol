"""The soak (validation/soak.py): seeded random walks of five workers and
three people over six issues with every fault kind, each worker under a
policy of its own, that break no guarantee and reach the board's work:
claims, releases, renames and merges. The deep lane walks a thousand
seeds for two hundred steps; here a few seeds walk a few steps."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims  # noqa: E402
from validation import deep, invariants, lanes, model as M, oracle, replay, shrink, soak  # noqa: E402

LEADS = ROOT / "validation" / "runs" / "2026-10-14-partial" / "traces"


def at_exactly_24_hours(last, now, idle_after=claims.IDLE_AFTER):
    return now - claims.parse_time(last) >= idle_after


class TheSoak(unittest.TestCase):
    def test_a_violation_that_stays_is_kept_once_at_the_step_it_first_shows(self):
        """A wrong cell or an open pull request that stays wrong is reported
        by every board run after it; the walk keeps the fault once, as the
        search does, so the lane shortens and writes it once."""
        stays = invariants.Violation("G1", "the board names b for claude/1, whose holder is a")
        checks = invariants.step_checks

        def with_stays(before, after, verb, outcome, known):
            return checks(before, after, verb, outcome, known) + [stays] if verb.name != "claim" else [stays, stays]

        with mock.patch.object(invariants, "step_checks", with_stays):
            walk = soak.walk(0, 12)
        self.assertEqual(walk.steps, 12)
        self.assertEqual(walk.violations, ((stays.guarantee, stays.detail, 0),))
        self.assertEqual(soak.summary([walk])["violations"], 1)

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
        """The step a walk names is the one the violation first shows at:
        the walk's verbs to it, replayed from the start, show the detail
        as named, and without its last verb show nothing of it."""
        with mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            walks = [soak.walk(seed, 80) for seed in range(6)]
            met = [(walk, violation) for walk in walks for violation in walk.violations]
            self.assertTrue(met, "no walk of six met the mutant")
            for walk, (guarantee, detail, at) in met:
                with self.subTest(seed=walk.seed, step=at):
                    trace = deep.symbolic_trace(soak.SPACE, walk.verbs[:at + 1])
                    self.assertEqual(shrink.shown(soak.SPACE.start, soak.SCOPE, trace, (guarantee, detail)), detail)
                    self.assertIsNone(shrink.shown(soak.SPACE.start, soak.SCOPE, trace[:-1], (guarantee, detail)))

    def test_a_file_names_the_detail_its_own_steps_show(self):
        """The lane shortens a walk's violation before it writes it, and
        taking a wait out moves the clock a released branch's name is made
        from; each file names the detail a replay of it meets, sha and
        all, not the one the walk met."""
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(claims, "is_idle", at_exactly_24_hours):
            result = lanes.run_soak([3], 12, folder)
            self.assertTrue(any("released/" in v["detail"] for v in result["violations"]), result["violations"])
            for v in result["violations"]:
                with self.subTest(file=Path(v["file"]).name):
                    violated = json.loads(Path(v["file"]).read_text(encoding="ascii"))["violated"]
                    self.assertEqual(violated, f"{v['guarantee']} {v['detail']}")
                    record = replay.replay(replay.load(v["file"]))
                    self.assertIn((v["guarantee"], v["detail"]), [(x.guarantee, x.detail) for _, x in record.violations])


def delete_ref_keeping_pulls(self, ref):
    """model.delete_ref as it was before the G9 lead: the open pull
    request whose head is deleted stayed open."""
    if self.store is not None:
        self.store.delete(ref)
    self.refs.pop(ref, None)
    self.creators.pop(ref, None)
    self.created.pop(ref, None)


def holder_whatever_the_file(model):
    """invariants.one_name_one_holder as it was before the G1 leads: the
    cell had to name the branch's holder with the claim file gone too."""
    problems = []
    for ref in sorted(model.refs):
        number = oracle.claim_number(ref)
        if number is None or number not in model.issues or oracle.released(model, number):
            continue
        shown = invariants.shown_worker(model, ref, number)
        if shown not in {model.creators.get(ref), board.NO_CLAIM_FILE, board.CLAIM_FILE_NOT_READ}:
            problems.append(invariants.Violation("G1", f"the board names {shown} for {ref}, whose holder is "
                                                       f"{model.creators.get(ref)}"))
    return problems


class TheFirstRunsLeads(unittest.TestCase):
    def test_the_three_leads_of_the_first_deep_run_replay_green(self):
        """The soak's three violations of 2026-10-14, shortened, each a
        fault of the harness's model that is fixed (the run's README):
        the Worker cell once the claim file is gone (G1, seeds 585 and
        959) and a deleted branch's open pull request (G9, seed 427).
        With the two fixes taken back, each lead shows the violation its
        file names, so the leads reach their faults; with them, none."""
        files = sorted(LEADS.glob("*.json"))
        self.assertEqual([f.stem for f in files], ["G1-soak-585-161", "G1-soak-959-184", "G9-soak-427-159"])
        for file in files:
            with self.subTest(lead=file.stem):
                guarantee, detail = json.loads(file.read_text(encoding="ascii"))["violated"].split(" ", 1)
                self.assertEqual(guarantee, file.stem.split("-")[0])
                trace = replay.load(file)
                with mock.patch.object(M.RepoModel, "delete_ref", delete_ref_keeping_pulls), \
                        mock.patch.object(invariants, "one_name_one_holder", holder_whatever_the_file):
                    before = replay.replay(trace)
                self.assertIn((guarantee, detail), [(v.guarantee, v.detail) for _, v in before.violations],
                              "\n".join(before.timeline))
                record = replay.replay(trace)
                self.assertEqual(record.violations, [], "\n".join(record.timeline))
                self.assertEqual(record.mismatches, [])


if __name__ == "__main__":
    unittest.main()
