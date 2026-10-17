"""The push guard refuses exactly what the board rules forbid and lets
everything else through, including a push it cannot read, as one inside
quoting it misreads: the hook is the first fence, branch protection the
second, so it fails open."""

import os
import random
import re
import shlex
import shutil
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
        # F21: --config-env and --attr-source take their value as the next
        # word, as -C and -c do, so the verb is the word after it.
        for options in (["--config-env", "user.name=HOME"], ["--attr-source", "HEAD"], ["--namespace", "n"],
                        ["-c", "push.default=current"], ["--git-dir=.git"], ["--work-tree", "."], ["--no-pager"]):
            with self.subTest(options=options):
                self.assertEqual(guard.push_arguments(["git", *options, "push", "origin", "main"]),
                                 ["origin", "main"])

    def test_a_query_of_git_s_own_runs_no_push(self):
        """git prints its exec path, or one of its documentation paths, or a
        list of its commands, and exits: the words after it run nothing.
        --exec-path with a value sets the path and runs the command."""
        for query in ("--exec-path", "--html-path", "--man-path", "--info-path", "--list-cmds=main"):
            with self.subTest(query=query):
                self.assertIsNone(guard.push_arguments(["git", query, "push", "origin", "main"]))
        self.assertEqual(guard.push_arguments(["git", "--exec-path=/usr/lib/git-core", "push", "origin", "main"]),
                         ["origin", "main"])

    def test_a_nul_byte_in_a_directory_or_a_ref_is_a_place_git_cannot_answer_in(self):
        """subprocess refuses a NUL byte with ValueError, which git_line
        takes as no answer, so the hook judges the push by its words and
        never raises (F21's second review); and expanduser refuses a ~
        before a NUL byte, or before a lone surrogate, which JSON's \\ud800
        carries and no file name can hold, which joined takes as a place it
        cannot tell (F21's third review: until then the hook raised)."""
        for command in ("git -C a\x00b push origin main", "cd a\x00b && git push origin main"):
            with self.subTest(command=command):
                self.assertEqual(guard.run({"tool_input": {"command": command}, "cwd": str(ROOT)}),
                                 (guard.REFUSE, MAIN))
        self.assertEqual(guard.git_line(["status"], "a\x00b"), None)
        with mock.patch.object(guard, "branches", return_value=("claude/7", "main")):
            for place in ("~a\x00b", "~\ud800", "~/\ud800"):
                for shape in ("cd {}; git push origin HEAD", "git -C {} push origin HEAD", "env -C {} git push",
                              "pushd {}; git push", "cd {} && git -C {} push"):
                    command = shape.format(place, place)
                    with self.subTest(command=command):
                        self.assertEqual(guard.run({"tool_input": {"command": command}, "cwd": str(ROOT)}),
                                         (guard.PASS, ""))
                        self.assertEqual(guard.run({"tool_input": {"command": command + " origin main"},
                                                    "cwd": str(ROOT)})[0], guard.REFUSE)
            self.assertIsNone(guard.joined("/w", "~a\x00b"))
            self.assertIsNone(guard.joined("/w", "~\ud800"))
        lease = {"tool_input": {"command": "git push --force-with-lease=claude/7:a\x00b origin claude/7"},
                 "cwd": str(ROOT)}
        with mock.patch.object(guard, "branches", return_value=("claude/7", "main")):
            self.assertEqual(guard.run(lease)[0], guard.PASS)

    def test_an_empty_word_is_a_word(self):
        """'' and "" are words to the shell, and git -C takes the empty one
        as no change of directory, so the push is read whole (F21: an
        empty word was read as an operator and ended the command)."""
        for command in ("git -C '' push origin main", 'git -C "" push origin main'):
            with self.subTest(command=command):
                self.assertEqual(guard.simple_commands(command), [["git", "-C", "", "push", "origin", "main"]])

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
        """simple_commands reads a command whole or not at all, as the
        template's shim, which the template's own tests pin, reads it; the
        hook reads it leniently and more than one way (ReadingTest)."""
        self.assertEqual(guard.simple_commands("git push 'unclosed"), [])
        self.assertEqual(guard.simple_commands("echo 'unclosed\ngit push origin main"), [])
        self.assertEqual(guard.simple_commands("git push --force origin main\necho \"unclosed"), [])


def plain_shell_before_f25(command):
    """guard.plain_shell as it was until F25's fix, word for word, which the
    pass must still give: the template's shim and its profile read it."""
    out, quote, word_start, i = [], None, True, 0
    while i < len(command):
        ch = command[i]
        if ch == "\\" and quote != "'":
            if command[i + 1:i + 2] != "\n":
                out.append(command[i:i + 2])
            i, word_start = i + 2, False
        elif quote:
            if ch == quote:
                quote = None
            out.append(ch)
            i, word_start = i + 1, False
        elif ch == "#" and word_start:
            end = command.find("\n", i)
            i = len(command) if end == -1 else end
        else:
            if ch in "'\"":
                quote = ch
            out.append(" ; " if ch == "\n" else ch)
            i, word_start = i + 1, ch in guard.WORD_BREAKS
    return "".join(out)


def lexed(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def body_texts(command):
    """The text of each here-document body the guard tells in command."""
    return [command[body.start:body.end] for body in guard.here_document_bodies(command)]


# Pieces of shell to make commands of at random: quotes, backslashes,
# newlines, comments, substitutions, arithmetic and here-documents.
PIECES = ("'", '"', "\\", "\n", "#", " ", "$(", ")", "((", "))", "<<", "<<-", "<<<", "EOF", "\t", "x", ";", "|",
          "git push origin main", "It's", '"$(cat <<\'EOF\'', "\nEOF\n", "`", "$'")


class ReadingTest(unittest.TestCase):
    """How the hook reads a command (F25): bash runs each complete command
    before a line it cannot read and reads a here-document's body as text,
    so a quote that never closes is read as a character of its word and
    the command is read whole and with its bodies cut out, every push any
    reading finds judged; until the fix a quote that never closed hid
    every push in the command, and an apostrophe in a body opened a quote a
    later one could close around a push. bash 5.2 ran each push these
    tests say it runs."""

    def run_guard(self, command):
        payload = {"tool_input": {"command": command}, "cwd": str(ROOT)}
        with mock.patch.object(guard, "branches", return_value=("claude/7", "main")):
            return guard.run(payload, str(ROOT))

    def run_as_before_f25(self, command):
        """The hook as built until F25's fix: the command read whole or not
        at all, and no body told."""
        strict = guard.plain_text
        with mock.patch.object(guard, "plain_text", lambda command, lenient=False: strict(command)), \
                mock.patch.object(guard, "here_document_bodies", lambda command: []):
            return self.run_guard(command)

    def test_a_push_beside_a_quote_that_never_closes_is_read(self):
        """bash runs the push before the line it cannot read; it runs
        nothing of a line it cannot read whole, as the one-line form, which
        the hook refuses too, reading too much of such a line and never too
        little."""
        for command in ("git push origin main\necho \"unclosed", "git push origin main\necho 'unclosed",
                        "git push origin main\necho a \\", "git push origin main; echo \"unclosed",
                        "echo \"unclosed\ngit push origin main"):
            with self.subTest(command=command):
                self.assertEqual(self.run_guard(command), (2, MAIN))
        self.assertEqual(self.run_guard("git push -u origin claude/7\necho \"unclosed"), (0, ""))
        self.assertEqual(self.run_as_before_f25("git push origin main\necho \"unclosed"), (0, ""),
                         "as built until F25's fix: an unclosed quote hid every push in the command")

    def test_a_quote_that_never_closes_is_a_character_of_its_word_when_read_leniently(self):
        self.assertEqual(guard.plain_text("git push 'unclosed", lenient=True), "git push \\'unclosed")
        self.assertEqual(guard.plain_text('echo "a\ngit push origin main', lenient=True),
                         'echo \\"a ; git push origin main')
        self.assertEqual(guard.plain_text("echo a \\", lenient=True), "echo a \\\\")
        self.assertEqual(lexed(guard.plain_text("echo \"it's\" 'a\"b' \"c", lenient=True)),
                         ["echo", "it's", 'a"b', '"c'])
        self.assertEqual(guard.plain_text("git push 'unclosed"), "git push 'unclosed", "strict: left for shlex")

    def test_plain_shell_is_the_pass_it_was(self):
        """The strict pass gives what it gave before the fix, for every
        command, so the template's shim and profile read as they did; read
        leniently, every command shlex reads whole reads the same, and every
        other one reads too."""
        draw = random.Random(25)
        for _ in range(3000):
            command = "".join(draw.choice(PIECES) for _ in range(draw.randint(1, 12)))
            with self.subTest(command=command):
                self.assertEqual(guard.plain_shell(command), plain_shell_before_f25(command))
                lenient = guard.plain_text(command, lenient=True)
                lexed(lenient)
                try:
                    lexed(guard.plain_shell(command))
                except ValueError:
                    continue
                self.assertEqual(lenient, guard.plain_shell(command))

    def test_here_document_bodies_are_told_as_bash_reads_them(self):
        for command, bodies in (
                ("cat <<'EOF' > /dev/null\nIt's done\nEOF\ngit push origin main", ["It's done\nEOF\n"]),
                ("cat <<EOF\nx\nEOF", ["x\nEOF"]),
                ("cat << EOF | sort\nx\nEOF\nls", ["x\nEOF\n"]),
                ("cat <<-EOF\n\tx\n\tEOF\nls", ["\tx\n\tEOF\n"]),
                ("cat <<A <<'B'\na\nA\nb\nB\nls", ["a\nA\n", "b\nB\n"]),
                ("cat <<'EOF'\nIt's\ngit push origin main", ["It's\ngit push origin main"]),
                ("cat <<'EOF' \"multi\nline\"\nbody\nEOF\nls", ["body\nEOF\n"]),
                ("git commit -m \"$(cat <<'EOF'\nSay \"hi\nEOF\n)\"\nls", ["Say \"hi\nEOF\n"]),
                ("x=$(cat <<'EOF'\nhi\nEOF)\nls", ["hi\nEOF"]),
                ("x=\"$(cat <<-EOF\n\thi\n\tEOF ) \"\nls", ["\thi\n\tEOF"]),
                ("cat <<EOF; y=$(\necho inner)\nbody\nEOF\nls", ["body\nEOF\n"]),
                ("cat <<EOF; (\necho sub)\nbody\nEOF", ["echo sub)\nbody\nEOF"]),
                ("cat <<\"E\"OF\nx\nEOF", ["x\nEOF"]), ("cat <<\\EOF\nx\nEOF", ["x\nEOF"]),
                ("cat <<E'O'F\nx\nEOF", ["x\nEOF"]), ("cat <<''\nx\n\nls", ["x\n\n"]),
                ("echo a<<b\nx\nb", ["x\nb"]), ("cat <<EOF # it's\nx\nEOF\nls", ["x\nEOF\n"]),
                ("cat <<EOF \\\n> out\nx\nEOF", ["x\nEOF"]),
                ("cat <<'EOF'\nEOFX\nEOF)\nEOF\nls", ["EOFX\nEOF)\nEOF\n"])):
            with self.subTest(command=command):
                self.assertEqual(body_texts(command), bodies)
        for command in ("cat <<<'EOF'\nIt's", "echo $((1<<2))\nIt's", "(( x <<= 2 ))\nIt's", "# cat <<EOF\nIt's",
                        "echo '<<EOF'\nIt's", "echo \"<<EOF\"\nIt's", "cat <<\nIt's", "cat << ;\nIt's",
                        "cat \\<<EOF\nIt's", "echo \"$((1<<2))\"\nIt's", "ls"):
            with self.subTest(command=command):
                self.assertEqual(guard.here_document_bodies(command), [])

    def test_an_expanded_body_runs_its_substitutions_and_nothing_else(self):
        """A body whose word nothing quotes is expanded, so the shell runs
        its command substitutions, and those alone are read where the body
        is cut out; a quoted word's body runs nothing."""
        runs = [body.runs for body in guard.here_document_bodies(
            "cat <<EOF\nIt's $(git push origin main) done, isn't it, $(echo \"a\" ')') \\$(no) $((1+2))\nEOF")]
        self.assertEqual(runs, [("$(git push origin main)", "$(echo \"a\" ')')")])
        self.assertEqual([body.runs for body in guard.here_document_bodies("cat <<'EOF'\n$(git push)\nEOF")], [()])
        self.assertEqual(guard.readings("cat <<EOF\nIt's $(git push origin main) done\nEOF\nls")[1],
                         "cat <<EOF ; $(git push origin main) ; ls")

    def test_an_expanded_body_joins_a_line_that_ends_in_a_backslash(self):
        """bash takes a backslash-newline out of a body it expands before it
        compares a line with the delimiter, an odd run of backslashes
        continuing the line and an even one not, and takes a <<-'s tabs
        off the joined line's start alone; a quoted body joins nothing."""
        for command, bodies in (
                ("cat <<EOF\nE\\\nOF\nls", ["E\\\nOF\n"]),
                ("cat <<EOF\nEOF\\\n\nls", ["EOF\\\n\n"]),
                ("cat <<EOF\nE\\\nO\\\nF\nls", ["E\\\nO\\\nF\n"]),
                ("cat <<EOF\na\\\nEOF\nEOF\nls", ["a\\\nEOF\nEOF\n"]),
                ("cat <<EOF\na\\\\\nEOF\nls", ["a\\\\\nEOF\n"]),
                ("cat <<EOF\na\\\\\\\nEOF\nEOF\nls", ["a\\\\\\\nEOF\nEOF\n"]),
                ("cat <<-EOF\n\tE\\\nOF\nls", ["\tE\\\nOF\n"]),
                ("cat <<-EOF\nE\\\n\tOF\nEOF\nls", ["E\\\n\tOF\nEOF\n"]),
                ("cat <<'EOF'\nE\\\nOF\nEOF\nls", ["E\\\nOF\nEOF\n"]),
                ("x=\"$(cat <<EOF\nE\\\nOF)\"\nls", ["E\\\nOF"])):
            with self.subTest(command=command):
                self.assertEqual(body_texts(command), bodies)
        self.assertTrue(guard.continues("a\\") and guard.continues("a\\\\\\"))
        self.assertFalse(guard.continues("a") or guard.continues("a\\\\"))

    def test_a_double_parenthesis_is_arithmetic_only_where_bash_reads_it_so(self):
        """bash reads (( as arithmetic where the ) that closes its second (
        is followed at once by another, and else as a subshell in a
        subshell, whose here-documents are read as any other; so is $((."""
        for command in ("((1 << 2))\nIt's", "(( (1+2) << 3 ))\nIt's", "echo $(( (1) << 2 ))\nIt's",
                        "echo \"$((1<<2))\"\nIt's"):
            with self.subTest(command=command):
                self.assertEqual(guard.here_document_bodies(command), [])
        for command, bodies in (("((cd /tmp); cat <<'EOF'\nIt's\nEOF\n)", ["It's\nEOF\n"]),
                                ("((cd /tmp) && cat <<'EOF'\nIt's\nEOF\n)", ["It's\nEOF\n"]),
                                ("x=$((cd /tmp); cat <<'EOF'\nIt's\nEOF\n)", ["It's\nEOF\n"]),
                                ("(( 1 ) ); cat <<'EOF'\nIt's\nEOF", ["It's\nEOF"]),
                                ("((cd /tmp; cat <<'EOF'\nIt's\nEOF", ["It's\nEOF"])):
            with self.subTest(command=command):
                self.assertEqual(body_texts(command), bodies)
        self.assertEqual([body.runs for body in guard.here_document_bodies(
            "cat <<EOF\n$((git push origin main); true) $((1+2))\nEOF")], [("$((git push origin main); true)",)])
        self.assertEqual(guard.arithmetic_end("((1))", 2), 5)
        self.assertIsNone(guard.arithmetic_end("((a) )", 2))
        self.assertIsNone(guard.arithmetic_end("((a", 2))

    def test_the_command_is_read_whole_and_with_its_bodies_cut_out(self):
        self.assertEqual(guard.readings("git push origin main"), ["git push origin main"])
        self.assertEqual(guard.readings("cat <<'EOF'\nIt's\nEOF\ngit push origin main"),
                         ["cat <<'EOF' ; It\\'s ; EOF ; git push origin main", "cat <<'EOF' ; git push origin main"])

    def test_a_push_beside_here_documents_is_read_whatever_their_bodies_hold(self):
        """Each of these is a shape bash ran the push of, the third the one a
        session writes most: a commit message by here-document, the push,
        then text by another."""
        for command in (
                "cat <<'EOF' > /dev/null\nIt's done\nEOF\ngit push origin main",
                "git commit -F - <<'EOF'\nDon't stop\nEOF\ngit push origin main\ncat <<'EOF'\nIt's done\nEOF",
                "git commit -q -m \"$(cat <<'EOF'\nSay \"hi\nEOF\n)\"\ngit push origin main\n"
                "gh pr create --body \"$(cat <<'EOF'\nQuote\" here\nEOF\n)\"",
                "msg=$(cat <<'EOF'\nIt's\nEOF\n)\ngit push origin main\necho 'x'",
                "cat <<-'EOF'\n\tIt's\n\tEOF\ngit push origin main\necho 'x'",
                "cat <<A <<'B'\nIt's\nA\nfine\nB\ngit push origin main\necho 'x'",
                "x=\"$(cat <<'EOF'\nSay \"hi\nEOF)\"\ngit push origin main\ngh pr create --body \"x\"",
                "cat <<EOF\nIt's $(git push origin main) done, isn't it\nEOF",
                "echo $((1<<2)); cat <<'EOF'\nIt's\nEOF\ngit push origin main\necho 'done'",
                "cat <<EOF\nDon't\nE\\\nOF\ngit push origin main\ncat <<'EOF'\nIt's\nEOF",
                "cat <<EOF\nDon't\nEOF\\\n\ngit push origin main\nEOF\ncat <<'EOF'\nIt's\nEOF",
                "((cd /tmp); cat <<'EOF'\nDon't\nEOF\n)\ngit push origin main\n((cd /tmp); cat <<'EOF'\nIt's\nEOF\n)"):
            with self.subTest(command=command):
                self.assertEqual(self.run_guard(command), (2, MAIN))
                self.assertEqual(self.run_as_before_f25(command), (0, ""), "as built until F25's fix")
        self.assertEqual(self.run_guard("git commit -F - <<'EOF'\nDon't stop\nEOF\ngit push -u origin claude/7\n"
                                        "cat <<'EOF'\nIt's done\nEOF"), (0, ""))

    def test_a_body_misread_hides_nothing_the_whole_reading_shows(self):
        """The second reading only adds pushes: were a body told where there
        is none, every push of the whole command would still be judged."""
        def everything_a_body(command):
            return [guard.Body(0, len(command))]

        with mock.patch.object(guard, "here_document_bodies", everything_a_body):
            self.assertEqual(guard.readings("git push origin main")[1], "")
            self.assertEqual(self.run_guard("git push origin main"), (2, MAIN))

    def test_every_command_reads_and_its_bodies_lie_inside_it_in_order(self):
        draw = random.Random(2025)
        for _ in range(3000):
            command = "".join(draw.choice(PIECES) for _ in range(draw.randint(1, 14)))
            with self.subTest(command=command):
                at = 0
                for body in guard.here_document_bodies(command):
                    self.assertTrue(at <= body.start <= body.end <= len(command))
                    at = body.end
                texts = guard.readings(command)
                self.assertIn(len(texts), (1, 2))
                for text in texts:
                    lexed(text)


# Each GNU coreutils wrapper's words before a command, as a person may
# write them: a long option by a start of its name, its value attached or
# the next word, a value it may take attached or left off, a flag, nice's
# --5, which nice reads as an adjustment before its options, and a start
# of more than one option's name or of none, which the wrapper refuses
# (F26). env's --split-string is left out: what it splits is one word to
# the guard (RunTest).
WRAPPER_WORDS = (
    ["env", "--uns", "HOME"], ["env", "--u", "HOME"], ["env", "--unset=HOME"], ["env", "--ch", "/"], ["env", "--c=/"],
    ["env", "--chdir", "/", "--uns", "HOME"], ["env", "--def"], ["env", "--def=TERM"],
    ["env", "--default-signal", "TERM"], ["env", "--ignore-e"], ["env", "--deb"], ["env", "--l"], ["env", "--b=PIPE"],
    ["env", "--d"], ["env", "--i"], ["env", "--x"], ["nice", "--adj", "5"], ["nice", "--a", "5"],
    ["nice", "--adjustment=5"], ["nice", "-n", "5"], ["nice", "-5"], ["nice", "--5"], ["nice", "--x"],
    ["timeout", "--sig", "KILL", "60"], ["timeout", "--s", "TERM", "--k", "5", "60"], ["timeout", "--kill=5", "60"],
    ["timeout", "--f", "60"], ["timeout", "--p", "60"], ["timeout", "--verb", "60"], ["timeout", "--v", "60"],
    ["nohup", "--"], ["nohup", "--x"], ["env", "-P", "/usr/bin"], ["env", "-L", "root"], ["env", "-U", "root"],
)
# What a GNU coreutils program exits with when it refuses its own words
# and runs nothing.
CANCELED = 125
# An option line of a GNU program's --help: "-u, --unset=NAME", "--block-
# signal[=SIG]" or "--null", the short letter, the long name and what the
# long one takes.
HELP_OPTION = re.compile(r"^\s+(?:-(\S), )?--([a-z][a-z0-9-]*)(\[=|=)?", re.M)
TAKES = {"=": guard.VALUE, "[=": guard.OPTIONAL, None: guard.FLAG}


def named_in_full(word, wrapper):
    """guard.long_option_of as the guard read a wrapper's long option until
    F26's fix: by its whole name alone."""
    name = word[2:].partition("=")[0]
    return name if name in wrapper.long_options else None


def gnu_coreutils(name):
    """Whether name runs GNU coreutils' program of that name here."""
    try:
        shown = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "GNU coreutils" in shown.stdout


class WrapperTest(unittest.TestCase):
    """F26: a wrapper's long option is read as GNU's getopt_long reads it,
    by its full name or a start of it that names no other option, with a
    value as the option takes one: the guard compared it with the names
    that take a value, in full, so the value after "env --uns" or "nice
    --adj" or "timeout --sig" was read as the command, and the push
    behind it passed."""

    def test_a_long_option_is_read_by_any_start_of_its_name_that_names_it_alone(self):
        for command in ("env --uns HOME git push origin main", "env --u HOME git push origin main",
                        "env --unset=HOME git push origin main", "env --ch .. git push origin main",
                        "env --sp -i --uns HOME git push origin main", "env --a pusher git push origin main",
                        "env -a pusher git push origin main", "env --env0 file git push origin main",
                        "env --q literal git push origin main", "nice --adj 5 git push origin main",
                        "nice --a 5 git push origin main", "timeout --sig KILL 60 git push origin main",
                        "timeout --s TERM --k 5 60 git push origin main",
                        "timeout --kill=5 --si=TERM 60 git push origin main",
                        "time --output-file x git push origin main", "time --output x git push origin main",
                        "time --o x --fo %e --a git push origin main", "time --f %e --verb git push origin main"):
            with self.subTest(command=command):
                self.assertEqual([guard.push_arguments(argv) for argv in guard.simple_commands(command)],
                                 [["origin", "main"]])

    def test_bsd_env_s_short_options_that_take_a_value_are_read_with_it(self):
        """The env of FreeBSD and macOS takes -P altpath, and FreeBSD's
        -L and -U a login class and a user, each with a value, attached or
        the next word, and runs the command after it, so the push behind
        each is judged; GNU's env refuses all three and runs nothing."""
        for command in ("env -P /usr/bin git push origin main", "env -P/usr/bin git push origin main",
                        "env -L root git push origin main", "env -U root git push origin main",
                        "env -i -P /usr/bin -u HOME git push origin main", "env -Lroot/ git push origin main"):
            with self.subTest(command=command):
                self.assertEqual([guard.push_arguments(argv) for argv in guard.simple_commands(command)],
                                 [["origin", "main"]])

    def test_an_exact_name_wins_and_a_start_of_more_than_one_or_of_none_names_nothing(self):
        env, timeout = guard.WRAPPERS["env"], guard.WRAPPERS["timeout"]
        for word, wrapper, option in (("--unset", env, "unset"), ("--uns", env, "unset"), ("--ch=..", env, "chdir"),
                                      ("--def", env, "default-signal"), ("--deb", env, "debug"),
                                      ("--d", env, None), ("--i", env, None), ("--ignore-", env, None),
                                      ("--x", env, None), ("--=x", env, None), ("--verb", timeout, "verbose"),
                                      ("--vers", timeout, "version"), ("--ver", timeout, None),
                                      ("--5", guard.WRAPPERS["nice"], None),
                                      ("--help", guard.WRAPPERS["command"], None)):
            with self.subTest(word=word):
                self.assertEqual(guard.long_option_of(word, wrapper), option)
        shared = guard.Wrapper("", {"out": guard.VALUE, "output": guard.FLAG})
        self.assertEqual(guard.long_option_of("--out", shared), "out", "an exact name wins over a longer one")
        self.assertEqual(guard.long_option_of("--outp", shared), "output")
        self.assertEqual(guard.long_option_of("--ou", shared), None)

    def test_a_value_is_the_next_word_only_where_the_option_needs_one(self):
        """A value the option may take is taken only attached, so env runs
        the word after --default-signal, here TERM, and no push; a flag
        takes none; and a word the wrapper refuses, which runs nothing, is
        read as taking none, so the push after it is judged as though the
        wrapper ran it."""
        for command, pushes in (("env --default-signal TERM git push origin main", [None]),
                                ("env --default-signal git push origin main", [["origin", "main"]]),
                                ("env --def=TERM git push origin main", [["origin", "main"]]),
                                ("env --ignore-e git push origin main", [["origin", "main"]]),
                                ("timeout --f 60 git push origin main", [["origin", "main"]]),
                                ("timeout --p --verb 60 git push origin main", [["origin", "main"]]),
                                ("env --d git push origin main", [["origin", "main"]]),
                                ("env --i git push origin main", [["origin", "main"]]),
                                ("timeout --v 60 git push origin main", [["origin", "main"]]),
                                ("nohup --x git push origin main", [["origin", "main"]]),
                                ("command --help git push origin main", [["origin", "main"]])):
            with self.subTest(command=command):
                self.assertEqual([guard.push_arguments(argv) for argv in guard.simple_commands(command)], pushes)

    def test_each_installed_gnu_wrapper_s_options_are_in_its_table_as_its_help_lists_them(self):
        """The table holds each option coreutils' env, nice, nohup and
        timeout list in their --help where they are installed, each long
        one resolving to an option that takes what the help says, and each
        short one that takes a value among the short values. GNU time,
        seldom installed, is read as its src/time.c reads its options
        (WRAPPERS), which the first test holds."""
        for name in ("env", "nice", "nohup", "timeout"):
            if not gnu_coreutils(name):
                continue
            wrapper = guard.WRAPPERS[name]
            listed = HELP_OPTION.findall(subprocess.run([name, "--help"], capture_output=True, text=True).stdout)
            self.assertIn("help", [long for _, long, _ in listed], name)
            for short, long, takes in listed:
                with self.subTest(wrapper=name, option=long):
                    self.assertEqual(wrapper.long_options.get(guard.long_option_of(f"--{long}", wrapper)),
                                     TAKES[takes or None])
                    if short:
                        self.assertEqual(short in wrapper.short_values, TAKES[takes or None] == guard.VALUE)

    def test_the_guard_reads_the_command_each_installed_gnu_wrapper_runs(self):
        """Run with printf after its words, each wrapper runs printf where
        the guard reads printf as the command, and something else where the
        guard reads something else; where the wrapper refuses its own words
        and runs nothing, the guard reads on to printf."""
        for words in WRAPPER_WORDS:
            if not gnu_coreutils(words[0]):
                continue
            argv = words + ["printf", "%s", "ran"]
            with self.subTest(argv=argv):
                done = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
                start = guard.command_start(argv)
                read = start is not None and argv[start] == "printf"
                if done.returncode == CANCELED:
                    self.assertTrue(read, "a word the wrapper refuses is read as taking no value")
                else:
                    self.assertEqual(read, done.stdout == "ran", done.stderr)


HOME = os.path.expanduser("~")


def push_places(command, here="/w/s", is_directory=lambda place: True):
    """The directory each push of a shell command runs in, in order, as the
    guard follows the shell from here (guard.placed_commands,
    guard.followed_and_unmoved), every directory a cd names being one
    unless is_directory says otherwise."""
    return [guard.followed_and_unmoved(argv, shell, here)[0]
            for argv, shell in guard.placed_commands(command, here, is_directory)
            if guard.push_arguments(argv) is not None]


class PlaceTest(unittest.TestCase):
    """Where each push runs (F21): the directories git's -C and env's -C
    name, joined as git and env read them, the shell's own moves, which hold
    for what follows them in the same shell, and None where this module
    cannot tell, which the guard then judges by the push's words alone."""

    def test_git_and_env_name_the_directory_a_push_runs_in(self):
        """Joined as chdir reads them, left for the system to resolve, so a
        .. after a symbolic link climbs from where the link leads, as git's
        and env's own chdir does (OtherCloneTest has it over git)."""
        for command, place in (("git -C ../other push origin x", "/w/s/../other"),
                               ("git -C .. -C other push", "/w/s/../other"),
                               ("git -C /elsewhere push", "/elsewhere"),
                               ("git -C '' push", "/w/s"),
                               ("git -C ~ push", HOME),
                               ("git -c user.name=x -C ../other push", "/w/s/../other"),
                               ("git --config-env user.name=HOME -C ../other push", "/w/s/../other"),
                               ("git --work-tree=../other push", "/w/s"),
                               ("env -C ../other git push", "/w/s/../other"),
                               ("env -C../other git push", "/w/s/../other"),
                               ("env -iC ../other git push", "/w/s/../other"),
                               ("env --chdir=../other git push", "/w/s/../other"),
                               ("env --chdir ../other git -C x push", "/w/s/../other/x"),
                               ("env -u HOME git push", "/w/s"),
                               ("env -C sub -C ../other git push", "/w/s/../other"),
                               ("env -C sub --chdir=../other git push", "/w/s/../other"),
                               ("env -C a env -C b git push", "/w/s/a/b"),
                               ("git --shallow-file x -C ../other push", "/w/s/../other")):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), [place])

    def test_env_changes_directory_once_to_the_last_it_is_given(self):
        """GNU env reads its options first and changes directory after, to the
        last directory -C or --chdir gave it, from where it was run; git's
        own -C, by contrast, are joined on in turn (F21's third review)."""
        if shutil.which("env") is None or subprocess.run(["env", "-C", "/", "true"]).returncode != 0:
            self.skipTest("no env that takes -C")
        with tempfile.TemporaryDirectory() as scratch:
            Path(scratch, "sub").mkdir()
            Path(scratch, "other").mkdir()
            ran = subprocess.run(["env", "-C", "sub", "-C", "other", "pwd"], cwd=Path(scratch, "sub").parent,
                                 capture_output=True, text=True)
            self.assertEqual(Path(ran.stdout.strip()).resolve(), Path(scratch, "other").resolve())
        self.assertEqual(guard.env_directory(["-C", "sub", "-C", "../other"]), "../other")
        self.assertEqual(guard.env_directory(["-u", "HOME"]), None)

    def test_env_s_chdir_is_read_by_any_start_of_its_name_that_names_it_alone(self):
        """GNU env reads --ch, --c=DIR and --chd as --chdir, each start of it
        that names no other option, and a value the option before it needs,
        attached or the next word, as that option's, so "env --uns --ch"
        unsets a variable named --ch and changes no directory (F26: a push
        after --c=DIR was judged where env was run, and one after --ch DIR
        not at all, DIR read as the command). Each place is where env 9.4
        runs pwd."""
        for command, place in (("env --ch ../other git push", "/w/s/../other"),
                               ("env --c=../other git push", "/w/s/../other"),
                               ("env --chd ../other git -C x push", "/w/s/../other/x"),
                               ("env --uns HOME --ch ../other git push", "/w/s/../other"),
                               ("env --def --ch ../other git push", "/w/s/../other"),
                               ("env --ch sub -C ../other git push", "/w/s/../other"),
                               ("env -C sub --c ../other git push", "/w/s/../other"),
                               ("env --ch=../other --chdir sub git push", "/w/s/sub"),
                               ("env --uns --ch git push", "/w/s"),
                               ("env --u=--ch git push", "/w/s")):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), [place])
        self.assertEqual(guard.env_directory(["--ch", "a", "--c=b"]), "b")
        self.assertEqual(guard.env_directory(["--uns", "--ch"]), None)
        self.assertEqual(guard.env_directory(["--debug", "--d"]), None)

    def test_a_push_that_names_its_repository_or_a_path_the_shell_expands_runs_where_this_cannot_tell(self):
        for command in ("git -C $DIR push", "git -C \"$DIR\" push", "git -C ../o* push", "git -C `pwd` push",
                        "git --git-dir=../other/.git push", "git --git-dir ../other/.git push", "git --bare push",
                        "GIT_DIR=../other/.git git push", "env GIT_DIR=../other/.git git push",
                        "GIT_REFERENCE_BACKEND=files://../other/.git git push",
                        "env GIT_REFERENCE_BACKEND=files:///w/other/.git git -C /w/s push"):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), [None])

    def test_a_variable_that_names_the_repository_holds_whatever_cd_or_minus_c_comes_after(self):
        """A move changes the directory and not the variable, so a push after
        GIT_DIR, or GIT_REFERENCE_BACKEND, set alone or exported, runs where
        this module cannot tell, through an absolute cd or -C too, until a
        subshell's ) or the end of a pipeline's part (F21's fourth review:
        an absolute -C after the export was taken for where the push runs).
        GIT_COMMON_DIR moves no ref (OtherCloneTest shows it over git)."""
        for command, places in (("export GIT_DIR=/w/s/.git; git -C /w/other push", [None]),
                                ("export GIT_DIR=/w/s/.git; env -C /w/other git push", [None]),
                                ("export GIT_DIR=/w/s/.git; cd /w/other && git push", [None]),
                                ("GIT_DIR=/w/s/.git; pushd /w/other && popd && git push", [None]),
                                ("declare -x GIT_REFERENCE_BACKEND=files:///w/s/.git; git -C /w/other push", [None]),
                                ("export GIT_REFERENCE_BACKEND; cd /w/other; git push", [None]),
                                ("(export GIT_DIR=/w/s/.git); git -C /w/other push", ["/w/other"]),
                                ("export GIT_DIR=/w/s/.git | cat; git -C /w/other push", ["/w/other"]),
                                ("export GIT_COMMON_DIR=/w/s/.git; git -C /w/other push", ["/w/other"])):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), places)

    def test_the_shell_s_moves_hold_for_what_follows_them_in_the_same_shell(self):
        for command, places in (("cd ../other && git push", ["/w/other"]),
                                ("cd ../other; git push", ["/w/other"]),
                                ("cd ../other\ngit push", ["/w/other"]),
                                ("cd ../other || exit 1; git push", ["/w/other"]),
                                ("cd /elsewhere && git -C sub push", ["/elsewhere/sub"]),
                                ("cd && git push", [HOME]),
                                ("cd -L -- ../other && git push", ["/w/other"]),
                                ("builtin cd ../other && git push", ["/w/other"]),
                                ("command cd ../other && git push", ["/w/other"]),
                                ("if cd ../other; then git push; fi", ["/w/other"]),
                                ("cd ../a && cd ../b && git push", ["/w/b"]),
                                ("{ cd ../other; git push; }; git push", ["/w/other", "/w/other"]),
                                ("pushd ../other && git push && popd && git push", ["/w/other", "/w/s"]),
                                ("pushd -n ../other; git push", ["/w/s"]),
                                ("env cd ../other; git push", ["/w/s"]),
                                ("time cd ../other; git push", ["/w/other"]),
                                ("time -p cd ../other; git push", ["/w/other"]),
                                ("nice cd ../other; git push", ["/w/s"]),
                                ("export FOO=1; git push", ["/w/s"]),
                                ("git status; git push", ["/w/s"])):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), places)

    def test_an_absolute_path_is_a_place_wherever_the_shell_stood(self):
        """After a move this module cannot follow, an absolute path still
        names its directory (F21's third review: until then it stayed
        unknown, so a lease through it went unjudged)."""
        for command, places in (("cd $X && git -C /w/other push", ["/w/other"]),
                                ("cd $X && cd /w/other && git push", ["/w/other"]),
                                ("cd $X && cd ~ && git push", [HOME]),
                                ("cd $X && git -C ../other push", [None])):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), places)

    def test_a_move_this_cannot_follow_leaves_the_directory_unknown(self):
        for command in ("cd - && git push", "cd -P ../other && git push", "cd -LP ../other && git push",
                        "cd $X && git push", "cd ~/$X && git push", "pushd && git push",
                        "pushd +1 && git push", "popd && git push", "export GIT_DIR=../o/.git; git push",
                        "GIT_DIR=../o/.git; git push", "declare -x GIT_DIR=../o/.git; git push"):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), [None])

    def test_a_cd_or_pushd_to_what_is_not_a_directory_leaves_the_shell_where_it_stood(self):
        """The shell runs what follows a failed cd from where it stood, so
        the push is judged there; until F21's second review it was judged
        in a directory that holds nothing, so a lease from the clone the
        shell never left went unjudged."""
        def there(place):
            return place != "/w/nowhere"

        for command, places in (("cd ../nowhere; git push", ["/w/s"]),
                                ("cd ../nowhere\ngit push", ["/w/s"]),
                                ("cd ../nowhere || true; git push", ["/w/s"]),
                                ("pushd ../nowhere; git push", ["/w/s"]),
                                ("cd ../nowhere; cd ../other; git push", ["/w/other"]),
                                ("cd ../other; cd ../nowhere; git push", ["/w/other"]),
                                ("git -C ../nowhere push", ["/w/s/../nowhere"])):
            with self.subTest(command=command):
                self.assertEqual(push_places(command, is_directory=there), places)

    def test_a_subshell_a_pipeline_and_a_background_list_keep_their_moves_to_themselves(self):
        for command, places in (("(cd ../other && git push); git push", ["/w/other", "/w/s"]),
                                ("(cd ../other; git push) && git push", ["/w/other", "/w/s"]),
                                ("(cd ../other)>log; git push", ["/w/s"]),
                                ("(cd ../a; (cd ../b; git push); git push); git push", ["/w/b", "/w/a", "/w/s"]),
                                ("cd ../other | cat; git push", ["/w/s"]),
                                ("cd ../other |& cat; git push", ["/w/s"]),
                                ("git push | cd ../x; git push", ["/w/s", "/w/s"]),
                                ("cd ../other & git push", ["/w/s"]),
                                ("cd ../other && git push & git push", ["/w/other", "/w/s"])):
            with self.subTest(command=command):
                self.assertEqual(push_places(command), places)

    def test_a_push_after_a_body_is_placed_as_each_reading_places_it(self):
        """The whole command moves the shell by a cd in a body, as this module
        reads it; with the body cut out it runs no cd, as bash runs none, and
        that reading adds no push the whole placed in the same shell (F25)."""
        self.assertEqual(push_places("cat <<'EOF'\ncd ../x\nEOF\ngit push"), ["/w/x", "/w/s"])
        self.assertEqual(push_places("cat <<'EOF'\nIt's\nEOF\ngit push; git push"), ["/w/s", "/w/s"])


class CloneTest(unittest.TestCase):
    """Each push is judged in each clone it may run in (guard.refused_push,
    F21): its current branch and its claim files are that clone's, each
    clone asked once; a push after a move is judged also where the command
    starts, as if no move ran, and, by a relative -C, in the session's own
    clone too, and a push whose clone this module cannot tell, or whose
    repository a variable names, by its words and in the session's own
    clone, and one by a -C from a ~ in the session's own clone too. So
    every push is judged in the session's own clone, as before the fix,
    save one whose clone git's or env's -C names by an absolute path, or a
    relative one with no cd, pushd or popd before it, holding nothing the
    shell expands, and whose repository nothing names, which is judged
    where the -C names alone (F21's third and fourth reviews)."""

    BRANCHES = {"/w/s": ("claude/7", "main"), "/w/other": ("main", "main")}

    def setUp(self):
        self.asked = []

    def branches_of(self, place):
        self.asked.append(place)
        return self.BRANCHES.get(os.path.normpath(place), (None, "main"))

    def reader_of(self, place):
        tables = {"/w/s": {}, "/w/other": {(ORIGIN_7, "7"): CLAIM_A, ("claude/7", "7"): CLAIM_B}}
        return files_at(tables.get(os.path.normpath(place), {}))

    def refused(self, command, here="/w/s", is_directory=lambda place: True):
        return guard.refused_push(command, here, self.branches_of, self.reader_of, is_directory)

    def test_a_push_of_another_clone_s_current_branch_is_judged_by_that_branch(self):
        for command in ("git -C ../other push origin HEAD", "git -C ../other push", "cd ../other && git push",
                        "env -C ../other git push -u origin HEAD"):
            with self.subTest(command=command):
                self.assertEqual(self.refused(command), MAIN)
        for command in ("git push origin HEAD", "(cd ../other); git push", "cd ../other | cat; git push"):
            with self.subTest(command=command):
                self.assertIsNone(self.refused(command))

    def test_a_lease_is_judged_by_the_claim_files_of_the_clone_it_runs_in(self):
        """The session's own clone has not fetched a's claim, the other one
        has: until F21's fix the lease below was judged in the first, which
        cannot judge it, and git took it in the second."""
        lease = "git -C ../other push --force-with-lease origin claude/7"
        reason = self.refused(lease)
        self.assertIn("claude/7 on origin is a's claim", reason)
        self.assertIn("the branch you push claims #7 for b", reason)
        self.assertIsNone(self.refused("git push --force-with-lease origin claude/7"),
                          "the session's own clone holds no claim file to judge by")

    def test_each_place_is_asked_once(self):
        """A place as the push names it: git -C's, joined as chdir reads it,
        and the shell's own logical one may be one clone or two, and are
        asked apart."""
        self.refused("git push origin claude/7; git -C ../other push origin x; git -C ../other push origin y; "
                     "cd ../other; git push origin z; git push origin w")
        self.assertEqual(self.asked, ["/w/s", "/w/s/../other", "/w/other"])

    def test_a_push_whose_clone_this_cannot_tell_is_judged_by_its_words_and_in_the_session_s_own_clone(self):
        self.assertEqual(self.refused("cd $X && git push origin main"), MAIN, "the session's own default branch")
        self.assertIsNone(self.refused("cd $X && git push"), "the session's own claim branch, as before the fix")
        self.assertEqual(self.asked, ["/w/s", "/w/s"], "once a command, for its words and as the push's clone")
        self.assertEqual(self.refused("cd $X && git push --force origin claude/7"), FORCE)
        self.assertEqual(self.refused("cd $X && git push origin HEAD", "/w/other"), MAIN + guard.UNMOVED,
                         "the session's own clone is on main, as the push may be")
        self.assertEqual(self.refused('git -C "$(git rev-parse --show-toplevel)" push origin HEAD', "/w/other"),
                         MAIN + guard.UNTOLD)
        self.assertEqual(self.refused("GIT_DIR=../s/.git git push --force-with-lease origin claude/7", "/w/other"),
                         self.refused("git push --force-with-lease origin claude/7", "/w/other") + guard.UNTOLD)

    def test_a_push_after_a_move_is_judged_where_the_command_starts_too(self):
        """A cd this module reads and the shell does not run, in a
        here-document's body, after a test that fails, in a function never
        called, sent the push to a clone that holds no claim file, which
        passed a lease that git takes from the session's own (F21's third
        review: a regression against 0.5.0, which judged every push there)."""
        lease = "git push --force-with-lease origin claude/7"
        held = self.refused(lease, "/w/other")
        self.assertIn("claude/7 on origin is a's claim", held)
        for moves in ("cat > /dev/null <<'EOF'\ncd ../s\nEOF\n", '[ -n "" ] && cd ../s; ', "f() { cd ../s; }; ",
                      "true || cd ../s; ", "{ cd ../s; } | cat; ", "pushd ../s; popd; popd; ",
                      "if false; then cd ../s; fi; ", "(case x in x) cd ../s;; esac); ", "cd ../s extra; ",
                      "cd ../s/missing/..; ", 'cd "~"; '):
            with self.subTest(moves=moves):
                self.assertEqual(self.refused(moves + lease, "/w/other"), held + guard.UNMOVED)
        self.assertEqual(self.refused("cd ../other && " + lease), held, "judged where the shell's cd takes it")
        self.assertEqual(self.refused("cd ../s && git push origin HEAD", "/w/other"), MAIN + guard.UNMOVED,
                         "what it costs: a cd the shell makes, refused for what the session's own clone shows")
        self.assertIsNone(self.refused("git -C ../s push origin HEAD", "/w/other"), "git -C is judged where it runs")
        with mock.patch.object(guard, "push_places", lambda argv, shell, here: [guard.Reading(
                guard.push_place(argv, shell.directory))]):
            self.assertEqual(self.refused("cat > /dev/null <<'EOF'\ncd ../s\nEOF\n" + lease, "/w/other"), held,
                             "since F25's fix the reading with the body cut out judges it where it starts")
            with mock.patch.object(guard, "here_document_bodies", lambda command: []):
                self.assertIsNone(self.refused("cat > /dev/null <<'EOF'\ncd ../s\nEOF\n" + lease, "/w/other"),
                                  "as built until F21's third review: judged in s alone, which shows no claim file")

    def test_a_relative_minus_c_after_a_move_is_judged_in_the_session_s_own_clone_too(self):
        """A cd into a directory the command makes first reads as failed,
        and one behind a test that fails or in a here-document's body as
        made, so after any cd, pushd or popd a relative -C may name another
        clone than the one it runs in, the session's own among them; the
        push is judged there too, where an absolute -C names its clone
        wherever the shell stands (F21's fourth review: each of these
        passed, and bash pushed main, or took a's claim, from the session's
        own clone)."""
        def there(place):
            return not os.path.normpath(place).startswith(("/w/other/tmpdir", "/w/other/build"))

        held = self.refused("git push --force-with-lease origin claude/7", "/w/other")
        held_head = self.refused("git push --force-with-lease origin HEAD:claude/7", "/w/other")
        self.assertIn("claude/7 on origin is a's claim", held)
        self.assertIn("claude/7 on origin is a's claim", held_head)
        for command, reason in (
                ("mkdir -p tmpdir && cd tmpdir && git -C .. push origin HEAD", MAIN),
                ("mkdir -p build && cd build && env -C .. git push origin HEAD", MAIN),
                ("mkdir -p build && cd build && cmake .. >/dev/null 2>&1; git -C .. push", MAIN),
                ("mkdir -p build/out && cd build/out && git -C ../.. push --force-with-lease origin claude/7", held),
                ("cd sub; false && cd /tmp; git -C .. push --force-with-lease origin HEAD:claude/7", held_head),
                ("cd sub\ncat > /dev/null <<'EOF'\ncd /tmp\nEOF\n"
                 "git -C .. push --force-with-lease origin HEAD:claude/7", held_head)):
            with self.subTest(command=command):
                self.assertEqual(self.refused(command, "/w/other", there), reason + guard.UNMOVED)
        for command in ("cd /tmp && git -C /w/s push origin HEAD", "cd /tmp && env -C /w/s git push origin HEAD",
                        "pushd /tmp && git -C /w/s/.. -C s push origin HEAD"):
            with self.subTest(command=command):
                self.assertIsNone(self.refused(command, "/w/other"),
                                  "an absolute -C is judged where it names alone, whatever cd came before")
        self.assertEqual(self.refused("cd /tmp && git -C ../w/s push origin HEAD", "/w/other"), MAIN + guard.UNMOVED,
                         "what it costs: a relative -C after a cd the shell makes, refused for what the session's "
                         "own clone shows")
        with mock.patch.object(guard, "after_a_move", lambda argv, shell: False):
            self.assertIsNone(self.refused("mkdir -p tmpdir && cd tmpdir && git -C .. push origin HEAD", "/w/other",
                                           there), "as built until F21's fourth review: judged in /w alone")

    def test_a_variable_that_names_the_repository_has_the_push_judged_in_the_session_s_own_clone(self):
        """GIT_DIR, or GIT_REFERENCE_BACKEND, set alone or exported names the
        repository whatever cd or -C comes after, so the push is judged by
        its words and in the session's own clone, as one with GIT_DIR on it
        is, with UNTOLD's advice and never UNMOVED's, a cd before it or not
        (F21's fourth review: after the export an absolute -C was judged
        where it names alone, and passed, though git took the refs GIT_DIR
        named)."""
        lease = "push --force-with-lease origin claude/7"
        held = self.refused("git " + lease, "/w/other")
        for command, reason in (("export GIT_DIR=/w/other/.git; git -C /w/s " + lease, held),
                                ('export GIT_DIR="$PWD/.git"; git -C /w/s push origin HEAD', MAIN),
                                ('export GIT_DIR="$PWD/.git"; env -C /w/s git push origin HEAD', MAIN),
                                ("GIT_DIR=/w/other/.git; git -C /w/s " + lease, held),
                                ("export GIT_DIR=/w/other/.git; cd /w/s && git " + lease, held),
                                ("cd /w/s; export GIT_DIR=/w/other/.git; git push origin HEAD", MAIN),
                                ("declare -x GIT_REFERENCE_BACKEND=files:///w/other/.git; git -C /w/s " + lease, held),
                                ("GIT_REFERENCE_BACKEND=files:///w/other/.git git -C /w/s " + lease, held),
                                ("export GIT_DIR=.git; git push origin HEAD", MAIN)):
            with self.subTest(command=command):
                self.assertEqual(self.refused(command, "/w/other"), reason + guard.UNTOLD)
        self.assertIsNone(self.refused("(export GIT_DIR=/w/other/.git); git -C /w/s " + lease, "/w/other"),
                          "the subshell's export ends at its ), and the push runs in s")
        self.assertIsNone(self.refused("export GIT_COMMON_DIR=/w/other/.git; git -C /w/s " + lease, "/w/other"),
                          "GIT_COMMON_DIR moves no ref")
        with mock.patch.object(guard, "followed_and_unmoved", lambda argv, shell, here: (
                guard.push_place(argv, shell.directory), guard.push_place(argv, here))):
            self.assertIsNone(self.refused("export GIT_DIR=/w/other/.git; git -C /w/s " + lease, "/w/other"),
                              "as built until F21's fourth review: judged in s alone, which shows no claim file")

    def test_bash_s_tildes_for_the_shell_s_own_directories_are_places_this_cannot_tell(self):
        """~+ is where the shell stands, ~- where it stood before, and ~N,
        ~+N and ~-N are entries of its directory stack, none of which this
        module keeps, where os.path.expanduser left them directories named ~+
        and ~-: a push through one is judged by its words and in the
        session's own clone, by git's or env's -C with UNTOLD's advice and
        after a cd or pushd with UNMOVED's (F21's fourth review). ~ and ~user
        stay home directories, and ~+x, which bash leaves as written, is a
        directory of that name."""
        for path in ("~+", "~-", "~+/sub", "~-/..", "~1", "~+2", "~-0/sub"):
            with self.subTest(path=path):
                self.assertIsNone(guard.joined("/w/s", path))
                self.assertIsNone(guard.joined("/w/s", path, physical=True))
                for command in (f"git -C {path} push", f"env -C {path} git push", f"cd {path} && git push",
                                f"pushd {path} && git push", f"cd /w/other && git -C {path} push"):
                    self.assertEqual(push_places(command), [None], command)
                self.assertEqual(self.refused(f"git -C {path} push origin HEAD", "/w/other"), MAIN + guard.UNTOLD)
                self.assertEqual(self.refused(f"env -C {path} git push origin HEAD", "/w/other"), MAIN + guard.UNTOLD)
                self.assertEqual(self.refused(f"cd {path} && git push origin HEAD", "/w/other"), MAIN + guard.UNMOVED)
        self.assertEqual(guard.joined("/w/s", "~"), HOME)
        self.assertEqual(guard.joined("/w/s", "~+x", physical=True), "/w/s/~+x")
        self.assertEqual(guard.joined("/w/s", "a/~-"), "/w/s/a/~-")

    def test_a_minus_c_from_a_tilde_is_judged_in_the_session_s_own_clone_too(self):
        """This module reads a ~ from the hook's own HOME, while a HOME the
        command sets before it, assigned or exported, sends the shell
        elsewhere, and quotes, which this module does not keep, leave a ~ as
        written, so a push by a -C from a ~ is judged where this module
        reads it and in the session's own clone too, with UNTOLD's advice
        (F21's fourth review: with HOME set in the command, a lease git
        takes from the session's own clone was judged in the hook's ~/other
        alone, and passed). What it costs, a push by git -C ~/x from a
        session on main refused as a push to main, is what 0.5.0's guard
        did. An absolute -C after the ~ names the clone alone, and a cd to
        a ~ is judged where the command starts too, as after any move."""
        lease = "push --force-with-lease origin claude/7"
        held = self.refused("git " + lease, "/w/other")
        for command, reason in (("HOME=/w; git -C ~/other " + lease, held),
                                ("export HOME=/w; git -C ~/other " + lease, held),
                                ("HOME=/w; env -C ~/other git " + lease, held),
                                ('git -C "~/other" ' + lease, held),
                                ("git -C ~/x push origin HEAD", MAIN),
                                ("git -C ~ push origin HEAD", MAIN),
                                ("git -C ~root/x push origin HEAD", MAIN),
                                ("env --chdir=~/x git push origin HEAD", MAIN),
                                ("git -C /w/s -C ~/x push origin HEAD", MAIN),
                                ("git -C ~/x -C sub push origin HEAD", MAIN),
                                ("cd /tmp && git -C ~/x push origin HEAD", MAIN)):
            with self.subTest(command=command):
                self.assertEqual(self.refused(command, "/w/other"), reason + guard.UNTOLD)
        for command in ("git -C ~/x -C /w/s push origin HEAD", "env -C ~/x git -C /w/s push origin HEAD",
                        "git -C /w/s push origin HEAD", "git -C ../s push origin HEAD"):
            with self.subTest(command=command):
                self.assertIsNone(self.refused(command, "/w/other"),
                                  "an absolute -C, or a relative one with no move before it, names its clone alone")
        self.assertEqual(self.refused("cd ~/x && git push origin HEAD", "/w/other"), MAIN + guard.UNMOVED,
                         "a cd to a ~ is judged where the command starts too, as after any move")
        with mock.patch.object(guard, "from_a_tilde", lambda argv: False):
            for command in ("HOME=/w; git -C ~/other " + lease, "export HOME=/w; git -C ~/other " + lease):
                with self.subTest(command=command):
                    self.assertIsNone(self.refused(command, "/w/other"),
                                      "as built at a71a391: judged in the hook's ~/other alone, which is no clone")


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
        """The trade the docstring states: the hook reads the command whole,
        bodies and all, beside its reading with the bodies cut out (F25), so
        a body line that starts a push to main is refused and the person
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


def git_version():
    """The version of the git on PATH, as (major, minor), or (0, 0) where it
    says none."""
    said = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    found = re.search(r"(\d+)\.(\d+)", said)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def dry_run(command, where):
    """What bash prints for command with its push made a dry run, from
    where: the refs git would send, and whether it would force them."""
    dry = command.replace(" push ", " push --dry-run --porcelain ")
    return subprocess.run(["bash", "-c", dry], cwd=where, env=GIT_ENV, capture_output=True, text=True).stdout


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


def settle(where):
    """No automatic maintenance in a scratch repository, set in its own
    configuration, since git does not hand the environment's to the
    receive-pack it starts for a local remote."""
    for key in ("gc.auto", "gc.autoDetach", "maintenance.auto", "maintenance.autoDetach", "receive.autogc"):
        git(where, "config", key, "0" if key == "gc.auto" else "false")


class OtherCloneTest(unittest.TestCase):
    """F21 over real clones: b's session works in its clone s, on its own
    claim claude/8, made before a claimed #7, so s has never seen a's
    claim; b's second clone of the same repository, other, beside it, was
    made after a's claim and stands on main. A push run in other through
    git -C is judged in other."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        root = Path(self.scratch.name)
        origin, seed, self.session, self.other = root / "origin.git", root / "seed", root / "s", root / "other"
        git(root, "init", "-q", "--bare", "-b", "main", str(origin))
        settle(origin)
        git(root, "clone", "-q", str(origin), str(seed))
        settle(seed)
        Path(seed, "README.md").write_text("base\n")
        git(seed, "add", "."); git(seed, "commit", "-q", "-m", "base"); git(seed, "push", "-q", "origin", "HEAD:main")
        git(root, "clone", "-q", str(origin), str(self.session))
        settle(self.session)
        git(self.session, "switch", "-q", "-c", "claude/8")
        git(seed, "switch", "-q", "-c", "claude/7")
        Path(seed, "docs", "agents", "claims").mkdir(parents=True)
        Path(seed, "docs", "agents", "claims", "7.md").write_text(CLAIM_A)
        git(seed, "add", "."); git(seed, "commit", "-q", "-m", "a claims 7"); git(seed, "push", "-q", "origin", "claude/7")
        git(root, "clone", "-q", str(origin), str(self.other))
        settle(self.other)

    def tearDown(self):
        self.scratch.cleanup()

    def hook(self, command, where=None):
        return guard.run({"tool_input": {"command": command}, "cwd": str(where or self.session)})

    def claim_of_b_in_other(self):
        """b's claim of #7 made in other, which fetched a's: git would take
        a lease of it there."""
        git(self.other, "switch", "-q", "-c", "claude/7", "main")
        Path(self.other, "docs", "agents", "claims").mkdir(parents=True)
        Path(self.other, "docs", "agents", "claims", "7.md").write_text(CLAIM_B)
        git(self.other, "add", "."); git(self.other, "commit", "-q", "-m", "b claims 7")

    def test_a_push_of_the_other_clone_s_main_is_refused(self):
        for command in ("git -C ../other push origin HEAD", "cd ../other && git push origin HEAD"):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command), (guard.REFUSE, MAIN))
        self.assertEqual(self.hook("git push -u origin HEAD"), (guard.PASS, ""), "the session's own claim")
        with mock.patch.object(guard, "push_place", lambda argv, here: here):
            self.assertEqual(self.hook("git -C ../other push origin HEAD"), (guard.PASS, ""),
                             "as built until F21's fix: HEAD read as the session's claude/8")

    def test_a_lease_from_the_clone_that_fetched_another_s_claim_is_refused(self):
        self.claim_of_b_in_other()
        dry = subprocess.run(["git", "push", "--dry-run", "--porcelain", "--force-with-lease", "origin", "claude/7"],
                             cwd=self.other, env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0, "git itself would take a's claim: the lease holds in other")
        self.assertIn("(forced update)", dry.stdout)
        lease = "git -C ../other push --force-with-lease origin claude/7"
        code, reason = self.hook(lease)
        self.assertEqual(code, guard.REFUSE)
        self.assertIn("claude/7 on origin is a's claim", reason)
        with mock.patch.object(guard, "push_place", lambda argv, here: here):
            self.assertEqual(self.hook(lease), (guard.PASS, ""),
                             "as built until F21's fix: judged in s, which holds no claim file of #7")

    def test_a_push_through_env_s_abbreviated_options_is_judged_in_the_clone_env_runs_it_in(self):
        """env reads --ch as --chdir and --uns as --unset, each with its
        value, so from s these run git in other, which pushes its main and,
        once b has claimed #7 there, takes a's claim; until F26's fix the
        hook read --ch and --uns as flags and the word after each as the
        command, and passed them, and read --c=../other as no move, judging
        the push in s, on its own claim claude/8."""
        main = "env --uns HOME --c=../other git push origin HEAD"
        self.assertIn("refs/heads/main", dry_run(main, self.session), "env runs git in other")
        self.assertEqual(self.hook(main), (guard.REFUSE, MAIN))
        self.claim_of_b_in_other()
        lease = "env --ch ../other git push --force-with-lease origin claude/7"
        self.assertIn("(forced update)", dry_run(lease, self.session), "git would take a's claim from other")
        code, reason = self.hook(lease)
        self.assertEqual(code, guard.REFUSE)
        self.assertIn("claude/7 on origin is a's claim", reason)
        with mock.patch.object(guard, "long_option_of", named_in_full):
            for command in (main, lease):
                with self.subTest(command=command):
                    self.assertEqual(self.hook(command), (guard.PASS, ""),
                                     "as built until F26's fix: the word after --uns or --ch read as the command")
        with mock.patch.object(guard, "names_chdir", lambda word: word.partition("=")[0] == "--chdir"):
            for command in (main, lease):
                with self.subTest(command=command):
                    self.assertEqual(self.hook(command), (guard.PASS, ""),
                                     "--c= and --ch read as no move: judged in s, on its own claim")

    def test_a_lease_after_a_cd_that_fails_is_judged_where_the_shell_stood(self):
        """From s, the shell's cd into other is made and the cd into nowhere
        fails, so the push runs from other; judged in nowhere, which shows
        nothing, and where the command starts, in s, which never fetched
        a's claim, it passed."""
        self.claim_of_b_in_other()
        lease = "cd ../other; cd ../nowhere; git push --force-with-lease origin claude/7"
        ran = subprocess.run(["bash", "-c", "cd ../other; cd ../nowhere; git rev-parse --abbrev-ref HEAD"],
                             cwd=self.session, env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(ran.stdout.strip(), "claude/7", "bash runs what follows the failed cd from other")
        code, reason = self.hook(lease)
        self.assertEqual(code, guard.REFUSE)
        self.assertIn("claude/7 on origin is a's claim", reason)
        self.assertEqual(self.hook("cd ../nowhere; git push --force-with-lease origin claude/7", self.other)[0],
                         guard.REFUSE)
        with mock.patch.object(guard, "reached", lambda shell, target, stack, is_directory: guard.Shell(target, stack)):
            self.assertEqual(self.hook(lease), (guard.PASS, ""),
                             "as built until F21's second review: judged in ../nowhere, which shows nothing")

    def test_a_lease_after_a_cd_in_a_here_document_s_body_is_judged_where_the_shell_stands(self):
        """bash reads the body as text, so the push runs from other, whose
        lease git would take; this module reads the body's cd as one the
        shell makes, into s, and judges the push in other too (F21's
        third review: a regression against 0.5.0)."""
        self.claim_of_b_in_other()
        lease = "cat > /dev/null <<'EOF'\ncd ../s\nEOF\ngit push --force-with-lease origin claude/7"
        ran = subprocess.run(["bash", "-c", lease.replace("git push --force-with-lease origin claude/7",
                                                          "git rev-parse --show-toplevel")],
                             cwd=self.other, env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(Path(ran.stdout.strip()).resolve(), self.other.resolve())
        code, reason = self.hook(lease, self.other)
        self.assertEqual(code, guard.REFUSE)
        self.assertTrue(reason.endswith(guard.UNMOVED), reason)
        code, reason = self.hook('git -C "$(git rev-parse --show-toplevel)" push --force-with-lease origin claude/7',
                                 self.other)
        self.assertEqual(code, guard.REFUSE)
        self.assertTrue(reason.endswith(guard.UNTOLD), reason)
        self.assertEqual(self.hook("time cd ../other; git push --force-with-lease origin claude/7")[0], guard.REFUSE,
                         "bash's time runs cd in the shell itself")
        self.assertEqual(self.hook(f"cd $X; git -C {self.other} push --force-with-lease origin claude/7")[0],
                         guard.REFUSE, "an absolute path after a place this module cannot tell")

    def test_a_lease_beside_a_quote_that_never_closes_or_quoted_bodies_is_refused(self):
        """bash runs each of these leases from other, where git takes a's
        claim: it runs the push before a line it cannot read, and reads the
        bodies as text; until F25's fix the hook found no push in them."""
        self.claim_of_b_in_other()
        lease = "git push --force-with-lease origin claude/7"
        for command in (f"{lease}\necho \"unclosed",
                        f"cat > /dev/null <<'EOF'\nDon't stop\nEOF\n{lease}\ncat > /dev/null <<'EOF'\nIt's done\nEOF",
                        f"msg=\"$(cat <<'EOF'\nSay \"hi\nEOF\n)\"\n{lease}\nbody=\"$(cat <<'EOF'\nQuote\" here\nEOF\n)\""):
            with self.subTest(command=command):
                self.assertIn("(forced update)", dry_run(command, self.other), "bash runs the lease, and git takes it")
                code, reason = self.hook(command, self.other)
                self.assertEqual(code, guard.REFUSE)
                self.assertIn("claude/7 on origin is a's claim", reason)
                strict = guard.plain_text
                with mock.patch.object(guard, "plain_text", lambda command, lenient=False: strict(command)), \
                        mock.patch.object(guard, "here_document_bodies", lambda command: []):
                    self.assertEqual(self.hook(command, self.other), (guard.PASS, ""), "as built until F25's fix")

    def test_a_relative_minus_c_after_a_cd_the_hook_misreads_is_judged_where_the_command_starts_too(self):
        """bash makes tmpdir and build/out before its cd into them, which the
        hook, run first, reads as failed, and skips the cd behind a test that
        fails, which the hook reads as made: either way the -C after it names
        another directory to the hook than the one git runs in, other, from
        which git would push main or take a's claim; the hook judges the push
        in other too (F21's fourth review: until then each passed)."""
        main = "mkdir -p tmpdir && cd tmpdir && git -C .. push origin HEAD"
        for command in (main, "mkdir -p build && cd build && env -C .. git push origin HEAD",
                        "mkdir -p build && cd build && cmake .. >/dev/null 2>&1; git -C .. push"):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command, self.other), (guard.REFUSE, MAIN + guard.UNMOVED))
        self.assertIn("refs/heads/main", dry_run(main, self.other), "git -C .. pushes other's main")
        self.claim_of_b_in_other()
        Path(self.other, "sub").mkdir()
        for lease in ("mkdir -p build/out && cd build/out && git -C ../.. push --force-with-lease origin claude/7",
                      "cd sub; false && cd /tmp; git -C .. push --force-with-lease origin HEAD:claude/7",
                      "cd sub\ncat > /dev/null <<'EOF'\ncd /tmp\nEOF\n"
                      "git -C .. push --force-with-lease origin HEAD:claude/7"):
            with self.subTest(lease=lease):
                code, reason = self.hook(lease, self.other)
                self.assertEqual(code, guard.REFUSE)
                self.assertIn("claude/7 on origin is a's claim", reason)
                self.assertTrue(reason.endswith(guard.UNMOVED), reason)
                self.assertIn("(forced update)", dry_run(lease, self.other), "git would take a's claim from other")
        self.assertEqual(self.hook(f"cd /tmp && git -C {self.session} push origin HEAD", self.other), (guard.PASS, ""),
                         "an absolute -C after a cd: judged in s alone, on its own claim claude/8")

    def test_an_exported_git_dir_holds_through_an_absolute_minus_c(self):
        """git pushes from the repository an exported GIT_DIR names, wherever
        -C points, so from other, on main, git pushes main, and once b has
        claimed #7 there, takes a's claim by other's refs though -C names s,
        which never fetched it; the hook judges each push by its words and
        in other, where the command starts, with UNTOLD's advice, a cd
        before it or not (F21's fourth review: judged in s alone, each
        passed)."""
        main = f'export GIT_DIR="$PWD/.git"; git -C {self.session} push origin HEAD'
        for command in (main, f'export GIT_DIR="$PWD/.git"; env -C {self.session} git push origin HEAD',
                        "export GIT_DIR=.git; git push origin HEAD"):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command, self.other), (guard.REFUSE, MAIN + guard.UNTOLD))
        self.assertIn("refs/heads/main", dry_run(main, self.other))
        self.claim_of_b_in_other()
        lease = f"export GIT_DIR={self.other}/.git; git -C {self.session} push --force-with-lease origin claude/7"
        self.assertIn("(forced update)", dry_run(lease, self.other), "git takes the lease by other's refs")
        code, reason = self.hook(lease, self.other)
        self.assertEqual(code, guard.REFUSE)
        self.assertIn("claude/7 on origin is a's claim", reason)
        self.assertTrue(reason.endswith(guard.UNTOLD), reason)

    def test_a_reference_backend_moves_the_refs_and_a_common_dir_does_not(self):
        """git 2.54 and later keep the refs where GIT_REFERENCE_BACKEND says,
        so with other's, and other's objects to send, git takes a's claim
        though -C names s; the hook judges such a push in other too, on any
        git. GIT_COMMON_DIR moves no ref: git judges that lease by s's own,
        which have not fetched a's claim, as the hook does (F21's fourth
        review)."""
        self.claim_of_b_in_other()
        lease = (f"export GIT_REFERENCE_BACKEND=files://{self.other}/.git; "
                 f"git -C {self.session} push --force-with-lease origin claude/7")
        code, reason = self.hook(lease, self.other)
        self.assertEqual(code, guard.REFUSE)
        self.assertIn("claude/7 on origin is a's claim", reason)
        self.assertTrue(reason.endswith(guard.UNTOLD), reason)
        if git_version() >= (2, 54):
            objects = f"export GIT_ALTERNATE_OBJECT_DIRECTORIES={self.other}/.git/objects; "
            self.assertIn("(forced update)", dry_run(objects + lease, self.other))
        git(self.session, "branch", "claude/7")
        common = (f"export GIT_COMMON_DIR={self.other}/.git; "
                  f"git -C {self.session} push --force-with-lease origin claude/7")
        self.assertIn("[rejected] (stale info)", dry_run(common, self.other), "s's refs, which lack a's claim")
        self.assertEqual(self.hook(common, self.other), (guard.PASS, ""), "judged in s alone, as git judges it")

    def test_a_minus_c_from_a_tilde_is_judged_where_the_command_starts_too(self):
        """bash reads a ~ from the HOME the command sets, assigned or
        exported, and leaves a quoted one as written: git -C ~/other then
        runs in other, though the hook reads the ~ from its own HOME, and
        git -C "~" in other's own directory named ~, here a link back to
        other. From other, on main, git pushes main, and once b has claimed
        #7 there, takes a's claim; the hook judges each push in other too,
        where the command starts, with UNTOLD's advice (F21's fourth review:
        until then each was judged in the hook's ~/other or ~ alone, and
        passed)."""
        root = self.scratch.name
        Path(root, "home").mkdir()
        Path(self.other, ".git", "info").mkdir(exist_ok=True)
        with open(Path(self.other, ".git", "info", "exclude"), "a") as exclude:
            exclude.write("/~\n")
        Path(self.other, "~").symlink_to(".")
        pushes = (f"HOME={root}; git -C ~/other push origin HEAD",
                  f"export HOME={root}; git -C ~/other push origin HEAD", 'git -C "~" push origin HEAD')
        with mock.patch.dict(os.environ, {"HOME": str(Path(root, "home"))}):
            for command in pushes:
                with self.subTest(command=command):
                    self.assertIn("refs/heads/main", dry_run(command, self.other), "git pushes other's main")
                    self.assertEqual(self.hook(command, self.other), (guard.REFUSE, MAIN + guard.UNTOLD))
            self.claim_of_b_in_other()
            for command in pushes:
                lease = command.replace(" push origin HEAD", " push --force-with-lease origin claude/7")
                with self.subTest(lease=lease):
                    self.assertIn("(forced update)", dry_run(lease, self.other), "git would take a's claim")
                    code, reason = self.hook(lease, self.other)
                    self.assertEqual(code, guard.REFUSE)
                    self.assertIn("claude/7 on origin is a's claim", reason)
                    self.assertTrue(reason.endswith(guard.UNTOLD), reason)
                    with mock.patch.object(guard, "from_a_tilde", lambda argv: False):
                        self.assertEqual(self.hook(lease, self.other), (guard.PASS, ""),
                                         "as built at a71a391: judged where the hook reads the ~ alone")

    def test_git_s_minus_c_climbs_from_where_a_link_leads_and_cd_does_not(self):
        """s/peek leads into other: git -C peek/.. changes directory as the
        system resolves it, into other, while bash's cd peek/.. takes the
        step back along the path it keeps, into s."""
        Path(self.other, "inner").mkdir()
        Path(self.session, "peek").symlink_to(Path("..", "other", "inner"))
        for command, branch in (("git -C peek/.. rev-parse --abbrev-ref HEAD", "main"),
                                ("cd peek/.. && git rev-parse --abbrev-ref HEAD", "claude/8")):
            ran = subprocess.run(["bash", "-c", command], cwd=self.session, env=GIT_ENV, capture_output=True, text=True)
            self.assertEqual(ran.stdout.strip(), branch, command)
        self.assertEqual(self.hook("git -C peek/.. push origin HEAD"), (guard.REFUSE, MAIN))
        self.assertEqual(self.hook("cd peek/.. && git push origin HEAD"), (guard.PASS, ""))


if __name__ == "__main__":
    unittest.main()
