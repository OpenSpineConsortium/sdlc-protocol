"""The push guard refuses exactly what the board rules forbid and lets
everything else through, including what it cannot parse: the hook is the
first fence, branch protection the second, so it fails open."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import claims, guard  # noqa: E402

FORCE = ("--force overwrites history another account may hold; the one forced push the protocol allows is "
         "--force-with-lease on your own claude/ branch after a rebase (.claude/rules/board.md)")
PLUS = ("a + before a refspec forces that ref as --force does, and git lets it override --force-with-lease; the "
        "one forced push the protocol allows is --force-with-lease on your own claude/ branch after a rebase, with "
        "no + (.claude/rules/board.md)")
MAIN = "a push to main is refused: the default branch takes only pull requests (.claude/rules/board.md)"
BOARD = "a push to board is refused: only the board workflow writes that branch (.claude/rules/board.md)"


def refusal(args, current="claude/7", default="main", claim_file_at=None):
    return guard.refusal(args, current, default, claim_file_at)


def files_at(table):
    """A claim-file reader over a table of (ref, number) to text, as
    guard.claim_file_reader answers from a clone."""
    return lambda ref, number: table.get((ref, number))


CLAIM_A = "# Claim: #7\n\n- Worker: a\n- Branch: claude/7\n"
CLAIM_B = "# Claim: #7\n\n- Worker: b (a second session)\n"
ORIGIN_7 = "refs/remotes/origin/claude/7"


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

    def test_a_refspec_written_with_a_plus_is_a_force(self):
        # A leading + forces that one ref as --force forces them all, and
        # git 2.43 lets it override a lease: a push of +claude/7 with
        # --force-with-lease overwrote a peer's newer commit that the lease
        # alone refused. The refusal names the +, which is what to drop.
        for args in (["origin", "+claude/7"], ["origin", "+HEAD:claude/7"], ["origin", "+claude/7:claude/8"],
                     ["--force-with-lease", "origin", "+claude/7"], ["-u", "origin", "claude/7", "+feature"],
                     ["origin", "+"]):
            self.assertEqual(refusal(args), PLUS, args)
        self.assertEqual(refusal(["--force", "origin", "+claude/7"]), FORCE, "--force is named where it is given")
        self.assertIsNone(refusal(["+origin", "claude/7"]), "the first word is the remote, not a refspec")
        # Under --delete git reads the + as part of the name to delete,
        # "unable to delete '+claude/7'", so that is no force.
        self.assertIsNone(refusal(["--delete", "origin", "+claude/7"]))

    def test_short_options_bundle_as_git_reads_them(self):
        # git 2.43 force-updated a branch on "git push -fu origin HEAD:x"
        # and on -uf; -o takes the rest of its word as its value.
        for args in (["-fu", "origin", "claude/7"], ["-uf", "origin", "claude/7"], ["-vqf", "origin", "x"],
                     ["-df", "origin", "x"]):
            self.assertEqual(refusal(args), FORCE, args)
        for args in (["-of", "origin", "claude/7"], ["-uo", "f", "origin", "claude/7"], ["-un", "origin", "claude/7"]):
            self.assertIsNone(refusal(args), args)
        self.assertIsNone(refusal(["-du", "origin", "+claude/7"]), "-d in a bundle deletes, so the + is a name")

    def test_a_long_option_is_read_by_any_start_that_names_it_alone(self):
        # git 2.43 took --force-w for --force-with-lease, and --mi for
        # --mirror; --forc and --d name more than one option, and git
        # refuses them and pushes nothing.
        lease = refusal(["--force-with-lease", "origin", "feature"], current="feature")
        self.assertEqual(refusal(["--force-w", "origin", "feature"], current="feature"), lease)
        self.assertEqual(refusal(["--force-with-l=feature", "origin", "feature"], current="feature"), lease)
        self.assertIsNone(refusal(["--force-w", "origin", "claude/7"]))
        self.assertEqual(refusal(["--mi", "origin"]), refusal(["--mirror", "origin"]))
        self.assertEqual(refusal(["--al", "origin"]), refusal(["--all", "origin"]))
        self.assertEqual(refusal(["--br", "origin"]), refusal(["--branches", "origin"]))
        self.assertIsNone(refusal(["--de", "origin", "+claude/7"]), "--de is --delete")
        # git 2.43 deleted claude/2 on "--delet origin claude/2", as it does
        # on --delete, which the guard lets through for one's own claim
        # branch after its merge, and refuses for main.
        self.assertIsNone(refusal(["--delet", "origin", "claude/2"]))
        self.assertEqual(refusal(["--delet", "origin", "main"]), MAIN)
        # git 2.43 answered "git push --forc origin claude/1" with "ambiguous
        # option: forc (could be --force-with-lease or --force-if-includes)",
        # exit 129, and pushed nothing.
        for ambiguous in ("--forc", "--d", "--a", "--force-"):
            self.assertIsNone(refusal([ambiguous, "origin", "claude/7"]), ambiguous)

    def test_an_option_taken_back_with_no_is_not_in_force(self):
        self.assertIsNone(refusal(["--force", "--no-force", "origin", "claude/7"]))
        self.assertIsNone(refusal(["--force-with-lease", "--no-force-with", "origin", "feature"], current="feature"))
        self.assertIsNone(refusal(["--all", "--no-all", "origin", "claude/7"]))
        self.assertEqual(refusal(["--no-force", "-f", "origin", "claude/7"]), FORCE)

    def test_a_value_is_no_refspec_and_the_words_after_two_dashes_are_refspecs(self):
        self.assertEqual(refusal(["--recurse-submodules", "check", "origin", "main"]), MAIN)
        self.assertIsNone(refusal(["--exec", "git-receive-pack", "origin", "claude/7"]))
        self.assertIsNone(refusal(["--receive-pack=git-receive-pack", "-o", "ci.skip", "origin", "claude/7"]))
        self.assertEqual(refusal(["--", "origin", "main"]), MAIN)

    def test_a_refspec_that_moves_many_branches_is_wholesale(self):
        # The matching refspec : updates every branch both sides have, the
        # default one among them, and a pattern with a * pushes every
        # branch it matches.
        for spec in (":", "+:", "refs/heads/*:refs/heads/*", "claude/*"):
            self.assertEqual(refusal(["origin", spec]),
                             f"a push with the refspec {spec} moves branches wholesale; push your own claim "
                             "branch alone (.claude/rules/board.md)", spec)

    def test_a_name_that_reads_as_a_claim_and_claims_nothing_is_refused(self):
        # GitHub's lock is per name, so claude/03 beside claude/3 would be a
        # second branch on #3 that claims nothing; the guard names the
        # issue's own claim branch.
        self.assertEqual(refusal(["origin", "claude/03"]), (
            "a push to claude/03 is refused: it reads as a claim branch and claims nothing, since a claim branch "
            "is claude/ and the issue's number in one to nine ASCII digits with no leading zero, so the claim "
            "branch of #3 is claude/3 (docs/agents/PROTOCOL.md, Claim)"))
        self.assertIn("so the claim branch of #3 is claude/3", refusal(["-u", "origin", "HEAD"], current="claude/\u0663"))
        self.assertIn("so the claim branch of #0 is claude/0", refusal([], current="claude/00"))
        self.assertIn("with no leading zero (docs/agents/PROTOCOL.md", refusal(["origin", "HEAD:claude/1234567890"]))
        for args in (["origin", "claude/3"], ["origin", "claude/0"], ["origin", "claude/x"],
                     ["origin", "claude/upbeat-sagan-6th2hu"], ["origin", "claude/03-notes"],
                     ["origin", "claude/03:claude/3"], ["--delete", "origin", "claude/03"], ["origin", ":claude/03"],
                     ["-d", "origin", "claude/\u00b2"]):
            self.assertIsNone(refusal(args), args)

    def test_a_branch_written_as_git_resolves_it_is_that_branch(self):
        # git reads heads/main as refs/heads/main on both sides of a
        # refspec, as it reads any name with refs/ left off, and @ alone as
        # HEAD. Against a bare remote, git 2.43 updated main on "git push
        # origin heads/main", "HEAD:heads/main", "heads/claude/7:heads/main"
        # and, from main, "git push origin @"; it deleted board on "git push
        # origin :heads/board" and "git push --delete origin heads/board";
        # and the guard let each of them through.
        for args, current in ((["origin", "heads/main"], "claude/7"), (["origin", "HEAD:heads/main"], "claude/7"),
                              (["origin", "heads/claude/7:heads/main"], "claude/7"),
                              (["origin", ":heads/main"], "claude/7"),
                              (["--delete", "origin", "heads/main"], "claude/7"),
                              (["origin", "@"], "main"), (["-u", "origin", "@"], "main")):
            self.assertEqual(refusal(args, current=current), MAIN, args)
        for args in (["origin", "heads/board"], ["origin", "HEAD:heads/board"], ["origin", ":heads/board"],
                     ["--delete", "origin", "heads/board"], ["--delet", "origin", "heads/board"]):
            self.assertEqual(refusal(args), BOARD, args)
        # refs/heads/ is taken off once: git 2.43 made a branch named
        # heads/main on "git push origin HEAD:refs/heads/heads/main".
        self.assertIsNone(refusal(["origin", "HEAD:refs/heads/heads/main"]))
        self.assertIsNone(refusal(["origin", "refs/tags/main"]), "a tag is no branch")
        self.assertIsNone(refusal(["origin", "@"]), "@ on a claim branch pushes that branch")
        # git pushed claude/7 on "--force-with-lease origin heads/claude/7",
        # one's own claim branch, which the guard refused as another one.
        self.assertIsNone(refusal(["--force-with-lease", "origin", "heads/claude/7"]))

    def test_the_claim_number_is_the_rule_claims_states(self):
        # The guard keeps its own copy, since the marketplace takes guard.py
        # alone; it must say what claims.NUMBER says.
        self.assertEqual(guard.CLAIM_NUMBER.pattern, claims.NUMBER.pattern)

    def test_lease_allowed_on_claim_branch(self):
        self.assertIsNone(refusal(["--force-with-lease", "origin", "claude/7"]))
        self.assertIsNone(refusal(["-u", "--force-with-lease=claude/7", "origin", "claude/7"]))

    def test_the_worker_line_is_read_as_claims_reads_it(self):
        # The guard keeps its own copies, as it does of the claim number.
        self.assertEqual(guard.WORKER_LINE.pattern, claims.WORKER_LINE.pattern)
        self.assertEqual(guard.TRAILING_NOTE.pattern, claims.TRAILING_NOTE.pattern)
        self.assertEqual(guard.worker_named(CLAIM_A), "a")
        self.assertEqual(guard.worker_named(CLAIM_B), "b")
        self.assertEqual(guard.worker_named("- Account: **old** (note)\n"), "old")
        self.assertIsNone(guard.worker_named("# Claim: #7\n\n## Plan\n"))
        self.assertIsNone(guard.worker_named(None))

    def test_a_lease_s_values_are_kept_as_git_reads_them(self):
        options, _ = guard.push_words(["--force-with-lease", "origin", "claude/7"])
        self.assertEqual(options["force-with-lease"], [""])
        options, _ = guard.push_words(["--force-with-lease=claude/7:abc", "--force-with-lease=claude/8", "origin"])
        self.assertEqual(options["force-with-lease"], ["claude/7:abc", "claude/8"])
        options, _ = guard.push_words(["--force-with-lease=claude/7", "--no-force-with-lease", "origin"])
        self.assertNotIn("force-with-lease", options)
        self.assertIsNone(guard.lease_holds([], "claude/7"))
        self.assertEqual(guard.lease_holds([""], "claude/7"), "")
        self.assertEqual(guard.lease_holds(["claude/7:abc"], "claude/7"), "abc")
        self.assertEqual(guard.lease_holds(["refs/heads/claude/7"], "claude/7"), "")
        self.assertIsNone(guard.lease_holds(["claude/8:abc"], "claude/7"), "a lease on another ref holds nothing here")
        self.assertEqual(guard.lease_holds(["claude/7:abc", ""], "claude/7"), "abc", "a ref named is held as named")
        self.assertEqual(guard.lease_holds(["", "claude/7:abc"], "claude/7"), "abc")
        self.assertEqual(guard.lease_holds(["claude/7:abc", "claude/7:def"], "claude/7"), "abc", "the first naming")

    def test_a_lease_onto_another_worker_s_claim_is_refused_by_the_claim_file_on_origin(self):
        reader = files_at({(ORIGIN_7, "7"): CLAIM_A, ("claude/7", "7"): CLAIM_B, ("HEAD", "7"): CLAIM_B})
        reason = refusal(["--force-with-lease", "origin", "claude/7"], claim_file_at=reader)
        self.assertEqual(reason, "a push with --force-with-lease to claude/7 is refused: claude/7 on origin is a's "
                                 "claim (its claim file docs/agents/claims/7.md says so) and the branch you push "
                                 "claims #7 for b; never push to another worker's branch (.claude/rules/board.md)")
        self.assertEqual(refusal(["--force-with-lease", "origin"], claim_file_at=reader), reason, "HEAD is claude/7")
        self.assertEqual(refusal(["--force-with-lease=claude/7", "origin", "claude/7"], claim_file_at=reader), reason)
        self.assertEqual(refusal(["--force-with-lease", "origin", "HEAD:claude/7"], claim_file_at=reader), reason)
        self.assertIsNone(refusal(["origin", "claude/7"], claim_file_at=reader), "a plain push is git's to refuse")
        self.assertIsNone(refusal(["--force-with-lease", "origin", "claude/7"]), "no clone to read, no fence")
        self.assertIsNone(refusal(["--force-with-lease", "origin", ":claude/7"], claim_file_at=reader),
                          "a deletion carries no claim file")

    def test_a_lease_by_the_claim_s_own_worker_passes_and_an_unreadable_claim_is_not_judged(self):
        self.assertIsNone(refusal(["--force-with-lease", "origin", "claude/7"],
                                  claim_file_at=files_at({(ORIGIN_7, "7"): CLAIM_A, ("claude/7", "7"): CLAIM_A})))
        self.assertIsNone(refusal(["--force-with-lease", "origin", "claude/7"],
                                  claim_file_at=files_at({("claude/7", "7"): CLAIM_B})),
                          "origin shows no claim file: a claim the fence cannot judge")
        self.assertIsNone(refusal(["--force-with-lease", "origin", "claude/7"],
                                  claim_file_at=files_at({(ORIGIN_7, "7"): "# no worker line\n",
                                                          ("claude/7", "7"): CLAIM_B})))
        reader = files_at({(ORIGIN_7, "7"): CLAIM_A})
        self.assertEqual(refusal(["--force-with-lease", "origin", "claude/7"], claim_file_at=reader),
                         "a push with --force-with-lease to claude/7 is refused: claude/7 on origin is a's claim "
                         "(its claim file docs/agents/claims/7.md says so) and the branch you push carries no claim "
                         "file for #7; never push to another worker's branch (.claude/rules/board.md)")

    def test_a_lease_that_names_a_commit_is_judged_at_that_commit(self):
        reader = files_at({("abc", "7"): CLAIM_A, (ORIGIN_7, "7"): CLAIM_B, ("claude/7", "7"): CLAIM_B})
        self.assertIsNotNone(refusal(["--force-with-lease=claude/7:abc", "origin", "claude/7"], claim_file_at=reader))
        self.assertIsNone(refusal(["--force-with-lease=claude/8:abc", "origin", "claude/7"], claim_file_at=reader),
                          "a lease that holds only claude/8 holds nothing on claude/7")

    def test_a_commit_git_cannot_show_is_judged_at_the_remote_tracking_ref(self):
        """The claim skill's lease names the tip last pushed, which a session
        writes as a shell variable the guard sees unexpanded: the push lands
        only when origin stands at that commit, so origin's ref as fetched
        judges it."""
        reader = files_at({(ORIGIN_7, "7"): CLAIM_A, ("claude/7", "7"): CLAIM_B})
        self.assertIsNotNone(refusal(["--force-with-lease=claude/7:$TIP", "origin", "claude/7"], claim_file_at=reader))
        self.assertIsNotNone(refusal(["--force-with-lease=claude/7:abc", "origin", "claude/7"], claim_file_at=reader))
        reader = files_at({(ORIGIN_7, "7"): CLAIM_B, ("claude/7", "7"): CLAIM_B})
        self.assertIsNone(refusal(["--force-with-lease=claude/7:$TIP", "origin", "claude/7"], claim_file_at=reader))

    def test_the_remote_pushed_to_names_the_remote_tracking_ref(self):
        reader = files_at({("refs/remotes/upstream/claude/7", "7"): CLAIM_A, ("claude/7", "7"): CLAIM_B})
        self.assertIsNotNone(refusal(["--force-with-lease", "upstream", "claude/7"], claim_file_at=reader))
        self.assertIsNone(refusal(["--force-with-lease", "origin", "claude/7"], claim_file_at=reader),
                          "origin's ref is not there to read")
        self.assertEqual(guard.remote_ref("upstream", "claude/7"), "refs/remotes/upstream/claude/7")
        self.assertEqual(guard.remote_ref("git@github.com:o/r.git", "claude/7"), ORIGIN_7, "a URL has no tracking ref")
        self.assertEqual(guard.remote_ref("../other", "claude/7"), ORIGIN_7)
        self.assertEqual(guard.remote_ref("", "claude/7"), ORIGIN_7)

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

    def test_git_is_found_behind_assignments_and_the_four_wrappers(self):
        # A person may put the environment, env, command, nice or time
        # before git, each with its own options; the push behind them is
        # still a push.
        for command in ("FOO=1 git push origin main", "GIT_TRACE=1 A='b c' git push origin main",
                        "env git push origin main", "/usr/bin/env -i FOO=1 git push origin main",
                        "env -u HOME --chdir=. -C .. git push origin main", "env -- git push origin main",
                        "command git push origin main", "command -p git push origin main",
                        "nice git push origin main", "nice -n 5 git push origin main", "nice -10 git push origin main",
                        "nice --adjustment=3 git push origin main", "time git push origin main",
                        "time -p git push origin main", "time -o out.txt -f %e -a git push origin main",
                        "FOO=1 env BAR=2 nice -n 2 time -p command /usr/bin/git -C . push origin main"):
            pushes = [guard.push_arguments(argv) for argv in guard.simple_commands(command)]
            self.assertEqual(pushes, [["origin", "main"]], command)

    def test_a_wrapper_that_runs_no_git_push_is_no_push(self):
        # command -v and -V only say what would run; the rest run something
        # that is not git, or nothing at all.
        for command in ("command -v git", "command -V git push origin main", "command -pv git push origin main",
                        "env", "nice", "time", "FOO=1", "FOO=1 echo git push origin main", "env cat git push",
                        "nice -n 5", "time -o out.txt"):
            pushes = [guard.push_arguments(argv) for argv in guard.simple_commands(command)]
            self.assertEqual(pushes, [None] * len(pushes), command)

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

    def test_a_push_behind_a_prefix_is_judged_as_the_push_itself(self):
        refused = self.run_guard("git push origin main")
        self.assertEqual(refused[0], 2)
        for prefix in ("FOO=1 ", "env ", "command ", "nice ", "time ", "env FOO=1 nice -n 5 "):
            self.assertEqual(self.run_guard(prefix + "git push origin main"), refused, prefix)
            self.assertEqual(self.run_guard(prefix + "git push -u origin claude/7"), (0, ""), prefix)
        self.assertEqual(self.run_guard("time git push origin +claude/7"), (2, PLUS))

    def test_a_push_inside_a_compound_command_is_judged_as_the_push_itself(self):
        # The reserved words run nothing of their own, so the push after
        # one runs; bash 5.2 ran each of these.
        for command in ("! git push origin main", "if git push origin main; then echo ok; fi",
                        "if ! git push origin main; then echo failed; fi",
                        "while ! git push origin main; do sleep 1; done", "until git push origin main; do sleep 5; done",
                        "for i in 1 2; do git push origin main; done", "if false; then :; else git push origin main; fi",
                        "if false; then :; elif git push origin main; then :; fi", "{ git push origin main; }",
                        "false || { git push origin main; }", "f() { git push origin main; }; f",
                        "function f { git push origin main; }; f"):
            self.assertEqual(self.run_guard(command), (2, MAIN), command)
        for command in ("if git push -u origin claude/7; then echo ok; fi",
                        "for i in 1 2 3; do git push origin claude/7 && break; sleep 2; done",
                        "while read -r b; do git push origin \"$b\"; done", "do_not=1 echo git push origin main"):
            self.assertEqual(self.run_guard(command), (0, ""), command)

    def test_a_push_behind_exec_nohup_or_timeout_is_judged_as_the_push_itself(self):
        for command in ("exec git push origin main", "exec -a pusher git push origin main",
                        "exec -cla pusher git push origin main", "nohup git push origin main",
                        "timeout 60 git push origin main", "timeout -s KILL -k 5 1m git push origin main",
                        "timeout --signal=TERM --kill-after 5 10 git push origin main", "timeout -- 60 git push origin main",
                        "FOO+=1 git push origin main", "env -iu HOME git push origin main"):
            self.assertEqual(self.run_guard(command), (2, MAIN), command)
        self.assertEqual(self.run_guard("timeout 60 git push -u origin claude/7"), (0, ""))

    def test_a_branch_written_as_git_resolves_it_is_judged_as_that_branch(self):
        for command in ("git push origin heads/main", "git push origin HEAD:heads/main",
                        "git push --delete origin heads/main"):
            self.assertEqual(self.run_guard(command), (2, MAIN), command)
        self.assertEqual(self.run_guard("git push origin :heads/board"), (2, BOARD))
        self.assertEqual(self.run_guard("git push -u origin heads/claude/7"), (0, ""))

    def test_a_command_handed_to_another_shell_as_text_is_out_of_reach(self):
        """The trade the docstring states: what bash -c, eval or env -S runs
        is text to this module, and a wrapper it does not name is no git, so
        each passes, and branch protection stands behind it."""
        for command in ("bash -c 'git push origin main'", "eval 'git push origin main'",
                        "env -S 'git push origin main'", "sudo git push origin main"):
            self.assertEqual(self.run_guard(command), (0, ""), command)

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

    def test_a_payload_of_the_wrong_shape_passes_without_git(self):
        """The old main() crashed on these, which Claude Code reads as a
        hook error and lets the call run; run() says so without raising."""
        shapes = ({"tool_input": "git push origin main"},
                  {"tool_input": ["git push origin main"]},
                  {"tool_input": {"command": ["git", "push", "origin", "main"]}},
                  {"tool_input": {"command": 7}})
        with mock.patch.object(guard, "branches", side_effect=AssertionError("git was asked")):
            for payload in shapes:
                self.assertEqual(guard.run(payload), (0, ""), repr(payload))

    def test_a_payload_cwd_that_is_not_text_is_passed_over(self):
        asked = []

        def remember(cwd):
            asked.append(cwd)
            return "claude/7", "main"
        with mock.patch.object(guard, "branches", side_effect=remember):
            for where in (7, "", None, ["/x"]):
                guard.run({"tool_input": {"command": "git push"}, "cwd": where}, "/hook")
        self.assertEqual(asked, ["/hook"] * 4)


GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C",
           "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_COUNT": "5", "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
           "GIT_CONFIG_KEY_1": "gc.autoDetach", "GIT_CONFIG_VALUE_1": "false",
           "GIT_CONFIG_KEY_2": "maintenance.auto", "GIT_CONFIG_VALUE_2": "false",
           "GIT_CONFIG_KEY_3": "maintenance.autoDetach", "GIT_CONFIG_VALUE_3": "false",
           "GIT_CONFIG_KEY_4": "receive.autogc", "GIT_CONFIG_VALUE_4": "false"}


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def scratch_clone(where):
    """A clone as b's session holds one: on origin claude/7 is a's claim,
    finished (the remote-tracking ref's tip dropped the claim file a's
    first commit added); the local claude/7 is b's own claim of #7 from
    main, finished the same way."""
    git(where, "init", "-q", "-b", "main")
    for key in ("gc.auto", "gc.autoDetach", "maintenance.auto", "maintenance.autoDetach", "receive.autogc"):
        git(where, "config", key, "0" if key == "gc.auto" else "false")
    claim = Path(where, "docs", "agents", "claims")
    Path(where, "README.md").write_text("base\n")
    git(where, "add", "."); git(where, "commit", "-q", "-m", "base")
    for worker, text, base in (("a", CLAIM_A, "main"), ("b", CLAIM_B, "main"), ("b", CLAIM_B, "a-done")):
        git(where, "checkout", "-q", "-B", "claude/7", base)
        claim.mkdir(parents=True, exist_ok=True)
        (claim / "7.md").write_text(text)
        git(where, "add", "."); git(where, "commit", "-q", "-m", f"{worker} claims 7")
        git(where, "update-ref", f"refs/tags/{worker}-claim", git(where, "rev-parse", "HEAD"))
        git(where, "rm", "-q", "docs/agents/claims/7.md"); git(where, "commit", "-q", "-m", f"{worker} finishes")
        git(where, "update-ref", f"refs/tags/{worker}-done", git(where, "rev-parse", "HEAD"))
        if worker == "a":
            git(where, "update-ref", ORIGIN_7, git(where, "rev-parse", "HEAD"))
    # The last pass left claude/7 as b's claim made again over a's merged
    # history, so a's add of the file is in its history under b's.


class ClaimFileReaderTest(unittest.TestCase):
    """The reader over a real clone: a claim file at a ref's tip, else where
    its history first added it, else None."""

    def setUp(self):
        self.where = tempfile.TemporaryDirectory()
        scratch_clone(self.where.name)
        self.read = guard.claim_file_reader(self.where.name)

    def tearDown(self):
        self.where.cleanup()

    def test_the_file_at_the_tip_then_where_the_history_first_added_it(self):
        self.assertEqual(self.read("a-claim", "7"), CLAIM_A.strip(), "at the tip")
        self.assertEqual(self.read(ORIGIN_7, "7"), CLAIM_A.strip(), "the tip dropped it; where it was first added")
        self.assertEqual(self.read("b-done", "7"), CLAIM_B.strip())
        self.assertEqual(self.read("claude/7", "7"), CLAIM_B.strip(),
                         "the issue claimed again after a's claim merged: the last add, b's, not the first, a's")
        self.assertEqual(self.read("HEAD", "7"), CLAIM_B.strip())
        self.assertEqual(self.read(git(self.where.name, "rev-parse", "b-claim"), "7"), CLAIM_B.strip())

    def test_a_ref_that_starts_with_a_dash_is_a_ref_to_git_and_not_an_option(self):
        self.assertIsNone(self.read("--output=/tmp/guard-test-never-written", "7"))
        self.assertIsNone(self.read("-q", "7"))
        self.assertFalse(Path("/tmp/guard-test-never-written").exists())

    def test_none_for_a_ref_the_clone_lacks_or_a_file_no_commit_added(self):
        self.assertIsNone(self.read("refs/remotes/origin/claude/8", "8"))
        self.assertIsNone(self.read("claude/7", "8"))
        self.assertIsNone(self.read("main", "7"))

    def test_the_hook_refuses_the_lease_and_passes_the_holder_s_own(self):
        payload = {"tool_input": {"command": "git push --force-with-lease origin claude/7"}, "cwd": self.where.name}
        with mock.patch.object(guard, "branches", return_value=("claude/7", "main")):
            code, reason = guard.run(payload)
            self.assertEqual(code, guard.REFUSE)
            self.assertIn("claude/7 on origin is a's claim", reason)
            self.assertIn("the branch you push claims #7 for b", reason)
            variable = {"tool_input": {"command": "git push --force-with-lease=claude/7:$TIP origin claude/7"},
                        "cwd": self.where.name}
            self.assertEqual(guard.run(variable)[0], guard.REFUSE, "a lease on a commit the shell expands")
            git(self.where.name, "update-ref", ORIGIN_7, "b-claim")
            self.assertEqual(guard.run(payload), (guard.PASS, ""), "origin's claim file names b too")
            self.assertEqual(guard.run(variable), (guard.PASS, ""))
            plain = {"tool_input": {"command": "git push origin claude/7"}, "cwd": self.where.name}
            self.assertEqual(guard.run(plain), (guard.PASS, ""), "a plain push reads no claim file")


if __name__ == "__main__":
    unittest.main()
