"""The push guard against git's recorded answers (validation/guard_grammar.py,
DESIGN.md section 8): the file holds an answer for every argument list the
grammar generates; the guard refuses every forbidden push it can read and
passes every shape the rules allow; what it lets through is only what it
cannot read (G11b) or cannot know; and the rules read off git's offers say
what the prototype measured."""

import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import guard_grammar as g  # noqa: E402

BUDGET_SECONDS = 15


class TheGrammar(unittest.TestCase):
    def test_it_generates_the_parts_the_design_names(self):
        texts = [c.text for c in g.commands()]
        for shape in ("git push origin +claude/7", "git push -fu origin claude/7", "git push --al origin",
                      "git push --mirr origin", "git push origin heads/main", "FOO=1 git push origin main",
                      "bash -c 'git push origin main'", "git -c push.default=current push origin",
                      "git push --force-with-lease origin claude/7", "git push origin HEAD:claude/07",
                      "git -C . push origin main", "git --config-env user.name=HOME push origin +claude/7",
                      "git --shallow-file x push origin main", "env --uns FOO git push origin main",
                      "env --def git push origin main", "nice --adj 5 git push origin +claude/7",
                      "timeout --sig KILL 60 git push origin HEAD:board",
                      "git commit -F - <<'EOF'\nDon't stop\nEOF\ngit push origin main\ncat <<'EOF'\nIt's done\nEOF"):
            with self.subTest(shape=shape):
                self.assertIn(shape, texts)
        self.assertEqual(len(set(texts)), len(texts))
        self.assertEqual(len(g.argument_lists()), 1398, "a prefix changes what the guard reads, never git's words")
        self.assertEqual(len(texts), 51260, "since F25's fix a sixth wrapper, the session's here-documents")
        self.assertFalse(any(c.prefix in g.ABBREVIATED and (c.wrapper != "{}" or c.argv.startswith("-"))
                             for c in g.commands()), "an abbreviated wrapper is taken with the command alone")


class GitsAnswers(unittest.TestCase):
    def test_the_file_is_ascii_and_keyed_by_gits_version(self):
        text = g.ANSWERS.read_text(encoding="ascii")
        data = json.loads(text)
        self.assertTrue(data["git"].startswith("git version "))
        self.assertEqual(sorted(data["answers"]), g.argument_lists())

    def test_git_offers_what_the_prototype_measured(self):
        answers = g.read_answers()

        def fresh(argv):
            return sorted(ref.removeprefix("refs/heads/") + " " + kind for ref, kind in answers[argv]["fresh"])

        self.assertEqual(fresh("push -f origin claude/7"), ["claude/7 forced"])
        self.assertEqual(fresh("push -fu origin claude/7"), ["claude/7 forced"])
        self.assertEqual(fresh("push origin +claude/7"), ["claude/7 forced"])
        self.assertEqual(fresh("push --force-with-lease origin claude/7"), ["claude/7 forced"])
        self.assertEqual(answers["push --force-with-lease origin claude/7"]["stale"], [], "stale info, refused by git")
        self.assertEqual(fresh("push --al origin"), ["claude/8 fast-forward", "main fast-forward"],
                         "claude/7 and feature are no fast-forward, so git does not offer them")
        self.assertEqual(fresh("push --force-with-lease origin feature"), ["feature forced"], "a lease outside claude/")
        self.assertEqual(answers["push --force-with-lease origin feature"]["stale"], [])
        forced_outside = {ref for a in answers.values() for ref, kind in a["fresh"]
                          if kind == "forced" and not ref.startswith("refs/heads/claude/")}
        self.assertEqual(forced_outside, {"refs/heads/feature"}, "the lease-outside rule has an offer to fire on")
        self.assertGreater(len(fresh("push --mirr origin")), 2)
        self.assertEqual(fresh("push origin heads/main"), ["main fast-forward"])
        self.assertEqual(fresh("push origin HEAD:main"), ["main fast-forward"])
        self.assertEqual(fresh("push origin HEAD:claude/07"), ["claude/07 create"])
        self.assertEqual(fresh("push origin claude/7"), [], "not a fast-forward: git refuses it itself")

    def test_the_rules_read_off_the_offers(self):
        forced_7 = {("refs/heads/claude/7", "forced")}
        self.assertEqual(g.rules_verdict(forced_7, set()), None, "a lease on one's own claim")
        self.assertIn("plain forced", g.rules_verdict(forced_7, forced_7))
        self.assertIn("default or the board", g.rules_verdict({("refs/heads/main", "fast-forward")}, set()))
        self.assertIn("default or the board", g.rules_verdict(set(), {("refs/heads/board", "delete")}))
        self.assertIn("lease outside", g.rules_verdict({("refs/heads/feature", "forced")}, set()))
        self.assertIn("more than one", g.rules_verdict({("refs/heads/a", "create"), ("refs/heads/b", "create")}, set()))
        self.assertIn("claims nothing", g.rules_verdict({("refs/heads/claude/07", "create")}, set()))
        self.assertEqual(g.rules_verdict({("refs/heads/claude/8", "delete")}, set()), None, "whose, only GitHub knows")
        self.assertEqual(g.rules_verdict(set(), set()), None)


class TheGuardJudged(unittest.TestCase):
    def test_it_agrees_with_git_on_every_readable_command_in_the_budget(self):
        began = time.perf_counter()
        judgement = g.judge()
        self.assertLess(time.perf_counter() - began, BUDGET_SECONDS)
        self.assertEqual(judgement.missing, ())
        self.assertEqual(judgement.failures, {}, g.report(judgement))
        self.assertEqual(judgement.commands, 51260)
        let_through = {d.why for d in judgement.classes if d.kind == "let through"}
        self.assertTrue(let_through)
        self.assertTrue(all("G11b" in why for why in let_through), let_through)

    def test_a_missing_answer_fails_the_lane(self):
        answers = g.read_answers()
        del answers["push -f origin claude/7"]
        judgement = g.judge(answers)
        self.assertEqual(judgement.missing, ("push -f origin claude/7",))

    def test_the_lane_bites_on_a_guard_that_passes_a_lease_outside_claude(self):
        from unittest import mock

        from sdlc_protocol import guard
        original = guard.refusal

        def lenient(args, current, default, claim_file_at=None):
            reason = original(args, current, default, claim_file_at)
            return None if reason and reason.startswith("--force-with-lease to") else reason

        with mock.patch.object(guard, "refusal", lenient):
            judgement = g.judge(commands=[c for c in g.commands() if c.prefix == "" and c.wrapper == "{}"])
        self.assertTrue(any("lease outside" in d.why for d in judgement.failures), g.report(judgement))

    def test_the_lane_bites_on_a_guard_that_passes_a_plain_force(self):
        from unittest import mock

        from sdlc_protocol import guard
        original = guard.refusal

        def lenient(args, current, default, claim_file_at=None):
            reason = original(args, current, default, claim_file_at)
            return None if reason and reason.startswith("--force overwrites") else reason

        with mock.patch.object(guard, "refusal", lenient):
            judgement = g.judge(commands=[c for c in g.commands() if c.prefix == "" and c.wrapper == "{}"])
        self.assertTrue(judgement.failures)
        self.assertTrue(any("plain forced" in d.why for d in judgement.failures))

    def test_the_lane_bites_on_a_guard_that_reads_a_wrapper_s_long_option_only_in_full(self):
        """F26: read by its whole name alone, --uns, --adj and --sig take no
        value, so the word after each is read as the command and every
        forbidden push behind it passes, as on the guard before the fix;
        --def, which names no option in full either, is read as a flag,
        and the push after it is judged."""
        from unittest import mock

        from sdlc_protocol import guard

        def in_full(word, wrapper):
            name = word[2:].partition("=")[0]
            return name if name in wrapper.long_options else None

        let_through = {}
        for prefix in g.ABBREVIATED:
            commands = [c for c in g.commands() if c.prefix == prefix]
            self.assertEqual(g.judge(commands=commands).failures, {}, prefix)
            with mock.patch.object(guard, "long_option_of", in_full):
                let_through[prefix] = sum(g.judge(commands=commands).failures.values())
        self.assertEqual(let_through, {"env --uns FOO ": 157, "env --def ": 0, "nice --adj 5 ": 157,
                                       "timeout --sig KILL 60 ": 157})


    def test_the_lane_bites_on_a_guard_that_reads_a_command_whole_or_not_at_all(self):
        """F25: read whole and strictly, with no body told, the session's
        here-documents pair their bodies' apostrophes around the push, so
        every forbidden push in that wrapper passes, as on the guard before
        the fix; with the fix the wrapper is judged as the bare command."""
        from unittest import mock

        from sdlc_protocol import guard

        commands = [c for c in g.commands() if c.wrapper == g.HERE_DOCUMENTS]
        self.assertEqual(g.judge(commands=commands).failures, {})
        bare = {(c.prefix, c.argv): g.guard_refuses(c.text) for c in g.commands() if c.wrapper == "{}"}
        self.assertTrue(all(g.guard_refuses(c.text) == bare[c.prefix, c.argv] for c in commands[::7]))
        strict = guard.plain_text
        with mock.patch.object(guard, "plain_text", lambda command, lenient=False: strict(command)), \
                mock.patch.object(guard, "here_document_bodies", lambda command: []):
            self.assertEqual(sum(g.judge(commands=commands).failures.values()), 5670)


if __name__ == "__main__":
    unittest.main()
