"""The git world (validation/git_store.py): the scenarios and the regression
traces play over a real bare repository with the results known.json
records for the memory world, and git itself takes or refuses each push
in its own words: a second claim is "fetch first", and the lease after
a fetch is taken, which is F7, so the guard's fence, reading the claim
files in the clone, stands before git and refuses it. The required check plays a few; the
deep lane plays them all (scripts/validate.py deep)."""

import sys
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import deep, git_store, known, model as M, replay, run, scenarios, verbs  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

# A guard against a hung git, not against a slow runner: the lane's own
# timing is what scripts/validate.py measures.
BUDGET_SECONDS = 300


def results(record):
    return {(at, v.guarantee) for at, v in record.violations}


@contextmanager
def attached(workers=("a", "b")):
    """A seeded model attached to a store with a clone per worker, closed
    at the end."""
    with git_store.GitStore() as store:
        model = M.seeded()
        store.attach(model, workers)
        yield store, model


class ScenariosOverGit(unittest.TestCase):
    def test_s01_s01c_and_s05a_play_over_git_as_known_json_records(self):
        began = time.perf_counter()
        for name in ("S01", "S01c", "S05a"):
            with self.subTest(scenario=name):
                record = run.run_scenario(scenarios.SCENARIOS[name], world=run.GIT)
                self.assertEqual(record.mismatches, [], "\n".join(record.timeline))
                self.assertEqual(known.differences("scenario", name, results(record)), [], "\n".join(record.timeline))
        self.assertLess(time.perf_counter() - began, BUDGET_SECONDS)

    def test_the_second_claim_is_refused_by_git_with_fetch_first(self):
        record = run.run_scenario(scenarios.SCENARIOS["S01"], world=run.GIT)
        self.assertTrue(any("claim b 1: fetch first" in line for line in record.timeline), "\n".join(record.timeline))

    def test_the_traces_of_f7_and_f12_play_over_git_as_known_json_records(self):
        for finding in ("F7", "F12"):
            with self.subTest(trace=finding):
                trace = replay.traces()[finding]
                record = replay.replay(trace, world=run.GIT)
                self.assertEqual(record.mismatches, [], "\n".join(record.timeline))
                self.assertEqual(known.differences("trace", finding, results(record)), [], "\n".join(record.timeline))


class TheStore(unittest.TestCase):
    def test_a_claim_is_one_commit_on_main_in_the_bare_repository(self):
        with attached() as (store, model):
            outcome = verbs.apply(model, Verb("claim", "a", (1,)), Scope())
            self.assertTrue(outcome.taken, outcome.note)
            self.assertIn("claude/1", store.refs())
            self.assertEqual(model.refs["claude/1"], store.refs()["claude/1"])
            self.assertIn("docs/agents/claims/1.md", model.tree(model.refs["claude/1"]))
            self.assertEqual(model.commits[model.refs["claude/1"]].parents, (model.refs["main"],))

    def test_the_guard_refuses_a_lease_onto_anothers_claim_that_git_would_take_after_a_fetch(self):
        # F7: git takes a lease after a fetch, so the guard's fence stands
        # before git, reading the claim files in the worker's clone with
        # the library's own reader, as the template's hook hands it.
        with attached() as (store, model):
            verbs.apply(model, Verb("claim", "a", (1,)), Scope())
            second = verbs.apply(model, Verb("claim", "b", (1,)), Scope())
            self.assertFalse(second.taken)
            self.assertIn("fetch first", second.note)
            lease = verbs.apply(model, Verb("lease-onto", "b", (1,)), Scope(careless=True))
            self.assertFalse(lease.taken, lease.note)
            self.assertIn("claude/1 on origin is a's claim", lease.refused_by_guard)
            self.assertFalse(lease.allowed_shape)
            self.assertIn("- Worker: a", model.tree(model.refs["claude/1"])["docs/agents/claims/1.md"])
            with mock.patch.object(git_store, "guard_says", lambda *args: None):
                taken = verbs.apply(model, Verb("lease-onto", "b", (1,)), Scope(careless=True))
            self.assertTrue(taken.taken, taken.note)
            self.assertEqual(model.creators["claude/1"], "a")
            self.assertIn("- Worker: b", model.tree(model.refs["claude/1"])["docs/agents/claims/1.md"])

    def test_a_copy_of_an_attached_model_answers_from_its_mirror_and_writes_no_git(self):
        with attached() as (store, model):
            verbs.apply(model, Verb("claim", "a", (1,)), Scope())
            copied = model.clone()
            self.assertIsNone(copied.store)
            verbs.apply(copied, Verb("commit", "a", (1,)), Scope())
            self.assertEqual(store.refs()["claude/1"], model.refs["claude/1"])
            self.assertNotEqual(copied.refs["claude/1"], model.refs["claude/1"])

    def test_a_rebase_after_a_merge_of_main_leaves_the_same_trees_in_both_worlds(self):
        """git's rebase drops the merge of main, and the memory rebase now
        drops it too, so the two worlds agree on a path with no conflict
        (the review found the trees differing at the rebase)."""
        path = (Verb("claim", "a", (1,)), Verb("move-main", M.OWNER), Verb("merge-main", "a", (1,)),
                Verb("move-main", M.OWNER), Verb("rebase-main", "a", (1,)))
        self.assertEqual(deep.compare(deep.SPACES["E-deep"], path), [])

    def test_standing_and_files_changed_come_from_git(self):
        with attached() as (store, model):
            verbs.apply(model, Verb("claim", "a", (1,)), Scope())
            verbs.apply(model, Verb("commit", "a", (1,)), Scope())
            self.assertEqual(model.standing("claude/1"), M.CLEAN)
            verbs.apply(model, Verb("move-main", M.OWNER), Scope())
            self.assertEqual(model.standing("claude/1"), M.BEHIND)
            self.assertIn("docs/agents/claims/1.md", model.files_changed("claude/1"))


if __name__ == "__main__":
    unittest.main()
