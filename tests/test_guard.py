"""The push guard refuses exactly what the board rules forbid and lets
everything else through, including what it cannot parse: the hook is the
first fence, branch protection the second, so it fails open."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import guard  # noqa: E402


def refusal(args, current="claude/7", default="main"):
    return guard.refusal(args, current, default)


class RefusalTest(unittest.TestCase):
    def test_push_to_default_branch(self):
        self.assertIsNotNone(refusal(["origin", "main"]))

    def test_push_head_to_default_branch(self):
        self.assertIsNotNone(refusal(["origin", "HEAD:main"]))

    def test_delete_board_branch(self):
        self.assertIsNotNone(refusal(["origin", ":board"]))
        self.assertIsNotNone(refusal(["--delete", "origin", "board"]))

    def test_plain_push_on_default_branch(self):
        self.assertIsNotNone(refusal([], current="main"))

    def test_force_refused_even_on_own_claim(self):
        self.assertIsNotNone(refusal(["--force", "origin", "claude/7"]))
        self.assertIsNotNone(refusal(["-f", "origin", "claude/7"]))

    def test_lease_allowed_on_claim_branch(self):
        self.assertIsNone(refusal(["--force-with-lease", "origin", "claude/7"]))
        self.assertIsNone(refusal(["-u", "--force-with-lease=claude/7", "origin", "claude/7"]))

    def test_lease_refused_elsewhere(self):
        self.assertIsNotNone(refusal(["--force-with-lease", "origin", "feature"], current="feature"))

    def test_wholesale_pushes_refused(self):
        self.assertIsNotNone(refusal(["--mirror", "origin"]))
        self.assertIsNotNone(refusal(["--all", "origin"]))

    def test_ordinary_claim_pushes_allowed(self):
        self.assertIsNone(refusal(["origin", "claude/7"]))
        self.assertIsNone(refusal(["-u", "origin", "HEAD"], current="claude/7"))
        self.assertIsNone(refusal([], current="claude/7"))

    def test_full_ref_to_default_refused(self):
        self.assertIsNotNone(refusal(["origin", "refs/heads/main"]))

    def test_detached_head_plain_push_passes(self):
        self.assertIsNone(refusal([], current=None))


class ParsingTest(unittest.TestCase):
    def test_compound_command_finds_the_push(self):
        commands = guard.simple_commands("git fetch origin && git push origin main")
        pushes = [guard.push_arguments(argv) for argv in commands]
        self.assertIn(["origin", "main"], pushes)

    def test_quoted_text_is_not_a_push(self):
        commands = guard.simple_commands("echo 'git push origin main'")
        self.assertTrue(all(guard.push_arguments(argv) is None for argv in commands))

    def test_redirection_target_is_not_a_refspec(self):
        commands = guard.simple_commands("git push origin claude/7 2>&1")
        self.assertEqual(guard.push_arguments(commands[0]), ["origin", "claude/7"])

    def test_git_global_options_are_stepped_over(self):
        self.assertEqual(guard.push_arguments(["git", "-C", "clone", "push", "origin", "x"]),
                         ["origin", "x"])

    def test_other_git_verbs_are_not_pushes(self):
        self.assertIsNone(guard.push_arguments(["git", "pull", "origin", "main"]))

    def test_a_newline_ends_a_command(self):
        commands = guard.simple_commands("git add -A\ngit commit -m x\ngit push origin main")
        self.assertEqual(commands, [["git", "add", "-A"], ["git", "commit", "-m", "x"],
                                    ["git", "push", "origin", "main"]])

    def test_a_comment_ends_at_its_newline(self):
        for command in ("git status # look first\ngit push origin main",
                        "# look first\ngit push origin main",
                        "true; # a note ending in a backslash \\\ngit push origin main"):
            self.assertEqual(guard.simple_commands(command)[-1],
                             ["git", "push", "origin", "main"], command)

    def test_a_hash_inside_a_word_or_quotes_is_not_a_comment(self):
        for command in ("git commit -m 'Claim #7' && git push origin main",
                        "echo a#b; git push origin main"):
            self.assertEqual(guard.simple_commands(command)[-1],
                             ["git", "push", "origin", "main"], command)

    def test_a_backslash_newline_joins_two_lines(self):
        self.assertEqual(guard.simple_commands("git push \\\n  origin main"),
                         [["git", "push", "origin", "main"]])

    def test_a_newline_inside_quotes_stays_inside_the_word(self):
        for command in ("git commit -m 'one\ngit push origin main' && git push -u origin claude/7",
                        'git commit -m "one\ngit push origin main" && git push -u origin claude/7'):
            commands = guard.simple_commands(command)
            self.assertEqual(commands[0], ["git", "commit", "-m", "one\ngit push origin main"], command)
            self.assertEqual(commands[1], ["git", "push", "-u", "origin", "claude/7"], command)

    def test_unparseable_command_yields_nothing(self):
        self.assertEqual(guard.simple_commands("git push 'unclosed"), [])
        self.assertEqual(guard.simple_commands("echo 'unclosed\ngit push origin main"), [])


class RunTest(unittest.TestCase):
    """What the hook answers for one tool call, with git's two answers
    about the branches held still: run() is what cli.py's guard subcommand
    calls after decoding stdin."""

    def run_guard(self, command):
        payload = {"tool_input": {"command": command}, "cwd": str(ROOT)}
        with mock.patch.object(guard, "branches", return_value=("claude/7", "main")):
            return guard.run(payload, str(ROOT))

    def test_forbidden_push_is_refused_with_a_reason(self):
        code, message = self.run_guard("git push origin main")
        self.assertEqual(code, 2)
        self.assertIn("pull requests", message)

    def test_allowed_push_passes(self):
        code, message = self.run_guard("git push -u origin claude/7")
        self.assertEqual(code, 0)
        self.assertEqual(message, "")

    def test_push_to_main_on_a_later_line_is_refused(self):
        code, message = self.run_guard("git add -A\ngit commit -m x\ngit push origin main")
        self.assertEqual(code, 2)
        self.assertIn("pull requests", message)

    def test_claim_push_then_a_line_naming_main_passes(self):
        for command in ("git push -u origin claude/7\ngit fetch origin main",
                        "git push -u origin claude/7\n# then open the pull request against main",
                        "git push -u origin claude/7 # not main"):
            self.assertEqual(self.run_guard(command), (0, ""), command)

    def test_a_here_document_body_is_read_as_commands(self):
        """The trade the docstring states: the hook reads no here-documents,
        so a body line that starts a push to main is refused and the person
        rephrases it, while a body that names no push passes as before."""
        refused, message = self.run_guard("git commit -F - <<'EOF'\nClaim #7\n\ngit push origin main\nEOF\n"
                                          "git push -u origin claude/7")
        self.assertEqual(refused, 2)
        self.assertIn("pull requests", message)
        self.assertEqual(self.run_guard("git commit -F - <<'EOF'\nClaim #7\n\nThe board first.\nEOF\n"
                                        "git push -u origin claude/7"), (0, ""))

    def test_command_without_push_passes_without_git(self):
        with mock.patch.object(guard, "branches", side_effect=AssertionError("git was asked")):
            self.assertEqual(guard.run({"tool_input": {"command": "git status"}}), (0, ""))

    def test_git_is_asked_in_the_payloads_cwd_before_the_callers(self):
        asked = []

        def remember(cwd):
            asked.append(cwd)
            return "claude/7", "main"
        with mock.patch.object(guard, "branches", side_effect=remember):
            guard.run({"tool_input": {"command": "git push"}, "cwd": "/session"}, "/hook")
            guard.run({"tool_input": {"command": "git push"}}, "/hook")
            guard.run({"tool_input": {"command": "git push"}})
        self.assertEqual(asked, ["/session", "/hook", "."])

    def test_broken_payload_passes(self):
        for payload in (None, "not json", [], {}, {"tool_input": None}, {"tool_input": {}}):
            self.assertEqual(guard.run(payload), (0, ""), repr(payload))


if __name__ == "__main__":
    unittest.main()
