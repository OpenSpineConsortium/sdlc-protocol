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

from validation import deep, fake_github, git_store, invariants, known, model as M, mutants, replay, run, scenarios, verbs  # noqa: E402
from validation.verbs import Scope, Verb  # noqa: E402

# A guard against a hung git, not against a slow runner: the lane's own
# timing is what scripts/validate.py measures.
BUDGET_SECONDS = 300

# Three pieces of work change one file, where git and the model's rule
# part: git merges claude/3 into main at line level, so it stands behind,
# where the model, judging by file, calls it dirty. c opens #102 last.
SHARED = ("src/shared.py",)
THREE = Scope(workers=("a", "b", "c"), issues=(1, 2, 3), files=((1, SHARED), (2, SHARED), (3, SHARED)))
THREE_PIECES = (Verb("claim", "a", (1,)), Verb("claim", "b", (2,)), Verb("commit", "a", (1,)), Verb("commit", "b", (2,)),
                Verb("open-draft", "a", (1,)), Verb("mark-ready", "a", (100,)), Verb("merge", M.OWNER, (100,)),
                Verb("claim", "c", (3,)), Verb("commit", "c", (3,)), Verb("merge-main", "b", (2,)),
                Verb("open-draft", "b", (2,)), Verb("mark-ready", "b", (101,)), Verb("merge", M.OWNER, (101,)),
                Verb("open-draft", "c", (3,)))


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

    def test_the_traces_of_f7_f12_and_f22_play_over_git_as_known_json_records(self):
        for finding in ("F7", "F12", "F22"):
            with self.subTest(trace=finding):
                trace = replay.traces()[finding]
                record = replay.replay(trace, world=run.GIT)
                self.assertEqual(record.mismatches, [], "\n".join(record.timeline))
                self.assertEqual(known.differences("trace", finding, results(record)), [], "\n".join(record.timeline))

    def test_f22_s_trace_is_red_over_git_where_it_is_in_memory_under_each_of_its_mutants(self):
        """DESIGN.md says F22's trace plays the same in both worlds, green
        on the library and red under each mutant that undoes a part of the
        fix at the same step and for the same guarantees; a's claim at hour
        27 deletes its claim file before its merge, so a's next claim has a
        file to add and git a commit to make (F22's third review)."""
        trace = replay.traces()["F22"]
        for name, mutant in mutants.MUTANTS.items():
            if "F22" not in mutant.why:
                continue
            with self.subTest(mutant=name), mutants.switched_on(mutant):
                in_memory = results(replay.replay(trace))
                over_git = results(replay.replay(trace, world=run.GIT))
                self.assertTrue(in_memory)
                self.assertEqual(over_git, in_memory)


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

    def test_a_rebase_drops_a_commit_whose_patch_main_holds_already_in_both_worlds(self):
        """git's rebase drops a commit whose patch-id is upstream; the
        memory rebase replayed it and wrote the commit's text over main's
        newer one (the review found the trees differing at the rebase).
        One change made on two claims at one hour is such a patch: a's
        first commit on each claim writes the same line to the shared
        file, #2 lands with one more line on it, and a's rebase of #1 must
        leave the branch at main's text in both worlds."""
        path = (Verb("claim", "a", (1,)), Verb("claim", "a", (2,)), Verb("commit", "a", (1,)), Verb("commit", "a", (2,)),
                Verb("wait", verbs.CLOCK, (24,)), Verb("commit", "a", (2,)), Verb("drop-claim-file", "a", (2,)),
                Verb("open-draft", "a", (2,)), Verb("mark-ready", "a", (100,)), Verb("merge", M.OWNER, (100,)),
                Verb("rebase-main", "a", (1,)))
        self.assertEqual(deep.compare(deep.SPACES["E-two"], path), [])
        model = deep.SPACES["E-two"].start()
        for verb in path:
            verbs.apply(model, verb, deep.SPACES["E-two"].scope)
        self.assertEqual(model.tree(model.refs["claude/1"])["src/shared.py"], model.tree(model.refs[M.MAIN])["src/shared.py"])
        self.assertIn("a 1440", model.tree(model.refs["claude/1"])["src/shared.py"])

    def test_standing_and_files_changed_come_from_git(self):
        """git answers a branch's standing at line level and the memory
        model at file level, and the two part only where three pieces of
        work change one file, which the lanes never reach; on a path that
        reaches it the attached model answers as git does, in its pull
        request's detail first of all, which the merge of F17 nearly had
        the fake read by the model's rule, and its detached copy answers
        with the word git gave (model.standings), where the model's own
        rule says dirty. The path this test took before had both rules
        agree, so it passed with git's answers bypassed. The files a
        branch changes are the store's too."""
        with git_store.GitStore() as store:
            model = M.seeded(issues=(1, 2, 3), writers=("a", "b", "c"))
            store.attach(model, ("a", "b", "c"))
            for verb in THREE_PIECES:
                verbs.apply(model, verb, THREE)
            self.assertEqual(fake_github.pull_detail(model, model.pulls[102])["mergeable_state"], "behind")
            self.assertEqual(model.pull_standing(model.pulls[102]), M.BEHIND)
            self.assertEqual(model.standing("claude/3"), M.BEHIND)
            copied = model.clone()
            self.assertEqual(copied.standing("claude/3"), M.BEHIND)
            self.assertEqual(fake_github.pull_detail(copied, copied.pulls[102])["mergeable_state"], "behind")
            copied.standings = None
            self.assertEqual(copied.standing("claude/3"), M.DIRTY)
            self.assertEqual(model.files_changed("claude/3"), ["docs/agents/claims/3.md", "src/shared.py"])
            with mock.patch.object(store, "files_changed", return_value=["what/git/said.py"]):
                self.assertEqual(model.files_changed("claude/3"), ["what/git/said.py"])

    def test_under_lenient_rules_a_branch_git_calls_behind_is_owed_nothing_over_git_too(self):
        """Under a rule that does not require an up-to-date branch (F16) a
        branch behind main is owed nothing, one in conflict "main moved".
        The oracle and G9's quiet second run read a copy of the model,
        which answered by the model's rule, so over git, on a branch git
        calls behind and the rule dirty, they owed the notice and then
        posted it, two violations the library did not commit (the review
        of F16's merge); a copy shares the word git gave. A run in two
        halves is judged from the state it started from, which kept a
        copy of the words git had given before its first half asked about
        #102, so the oracle owed the notice there too (the second review);
        that state shares them now."""
        whole = (THREE, (Verb("board", "board", ("schedule", "")),))
        halves = (THREE._replace(split_board=True),
                  (Verb("board-start", "board", ("schedule", "")), Verb("board-finish", "board")))
        for name, (scope, board_run) in (("whole", whole), ("in two halves", halves)):
            with self.subTest(run=name), git_store.GitStore() as store:
                model = M.seeded(issues=(1, 2, 3), writers=("a", "b", "c"))
                model.lenient_rules = True
                store.attach(model, ("a", "b", "c"))
                red = []
                for verb in THREE_PIECES + board_run:
                    before = model.clone()
                    outcome = verbs.apply(model, verb, scope)
                    red += [(str(verb), v.guarantee, v.detail)
                            for v in invariants.step_checks(before, model, verb, outcome)]
                self.assertEqual(red, [])
                self.assertEqual(model.standing("claude/3"), M.BEHIND)
                self.assertEqual([c.body for c in model.comments_on(102)], [])

    def test_every_copy_shares_the_words_git_gave_at_every_level(self):
        """The states a run in two halves keeps, and those of a copy of a
        model with such a run, share the attached model's one record of
        git's words, so a word git gives after the copy is heard there
        too."""
        with git_store.GitStore() as store:
            model = M.seeded()
            store.attach(model, ("a",))
            verbs.apply(model, Verb("board-start", "board", ("schedule", "")), Scope(split_board=True))
            copied = model.clone()
            for state in (model.running.started_from, model.running.planned, copied,
                          copied.running.started_from, copied.running.planned):
                self.assertIs(state.standings, model.standings)


if __name__ == "__main__":
    unittest.main()
