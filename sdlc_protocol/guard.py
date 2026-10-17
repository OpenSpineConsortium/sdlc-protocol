"""Refuse a git push the board rules forbid, before it runs.

Claude Code runs this as a PreToolUse hook on the Bash tool, through
cli.py's "guard" subcommand: the tool call arrives as JSON on stdin, exit
code 2 stops it, and stderr goes back into the session as the reason.
The rules are the board rules of the repository that vendors this
(its .claude/rules/board.md): the default branch takes only pull
requests, the board branch takes only the board workflow's writes, and
the one forced push the protocol allows is --force-with-lease on one's
own claude/ branch after a rebase; and the protocol's Claim rule, that the
claim branch of issue N is claude/N exactly, so a branch of other digits
after claude/, which reads as a claim and claims nothing, is not pushed.
A refusal is the rule working; the answer to one is a pull request, a
rebase or another branch, never a way around the hook.

The lease is also the one push GitHub itself would let take another
worker's claim: a worker that has fetched holds a remote-tracking ref
that matches the remote, so its --force-with-lease onto claude/N is
taken whoever claimed N, and a ruleset against forced pushes on claude/**
would refuse the prescribed lease too (spec/PROTOCOL.md, Work in the
open). So before a lease onto claude/N the guard reads whose the claim
is, from the claim file docs/agents/claims/N.md on the branch the lease
holds against (the commit a --force-with-lease=claude/N:<commit> names,
else, or when git cannot show that commit, as a lease written with a
shell variable names one, the remote-tracking ref refs/remotes/<remote>/
claude/N as the fetch left it), and whose claim the branch pushed makes
it, from the same file on that branch; on either side the file is read
at the tip, or, once the branch has deleted it as a finished branch has,
where its history last added it, the last so that an issue claimed again
after a merge is judged by its new claim and not by the one main's
history keeps. A lease whose two workers differ, or that would put a
branch with no claim file where another worker's claim stands, is
refused (other_holder). The file is read with git in the clone the push
runs in, so the fence costs a few git calls on a lease push and nothing
on any other command.

Each push is judged in each clone it may run in (refused_push,
push_places), since a session's shell is not always where its push goes:
git -C and env -C name another directory, and cd, pushd and popd move the
shell before a later command, so "cd ../other && git push origin HEAD"
pushes the other clone's branch, and its current and default branch, and
its claim files, are that clone's. The -C of git and env are joined on as
chdir reads them (push_place). The shell's moves are followed within one
command (placed_commands), each part of a pipeline and each list run with
& in a shell of its own, a cd or pushd to what is not a directory leaving
the shell where it stood, as the shell does when it fails there. But this
module cannot tell which of the moves it reads the shell makes: one in a
here-document's body, after a test that fails or in a function never
called counts as made, and one into a directory the command makes first,
as after mkdir, as failed. So a push after a cd, pushd or popd is judged
also where the command starts, as if no move ran, and, where a -C
relative to where the shell stands names its clone, in the session's own
clone too. Where this module cannot tell the directory, after cd - or
cd -P, with a path that holds what the shell expands, quoted or not,
since this module keeps no quotes, or that starts with bash's ~+, ~- or
~N (joined), or with the repository named by --git-dir, --bare, GIT_DIR
or GIT_REFERENCE_BACKEND, on the push or set before it, the push is
judged by what its words name and in the session's own clone. A push is
refused when any of these judgements refuses it. So a push is judged in
the session's own clone, as every push was judged before F21's fix, save
one whose clone git's or env's -C names by an absolute path, or a
relative one with no cd, pushd or popd before it in the same shell,
holding nothing the shell expands, and whose repository nothing names:
that push is judged in the clone the -C names alone. That is the clone it
runs in, save where the shell does what this module does not read, as
where the command first points a symbolic link on that path elsewhere
(ln -sfn), makes a repository there (git init, git worktree add) or
writes a .git file there, since the hook reads the path before the
command runs. This module does not trust a ~ to name a clone: it reads
one from the hook's own HOME, while a HOME the command sets before it
sends the shell elsewhere, and quotes, which this module does not keep,
leave a ~ as written, so a push by a -C from a ~ is judged where this
module reads it and in the session's own clone too (from_a_tilde). And it
does not follow a move or an export in a command handed to eval or trap
or in a file run by source or ., nor what a function or alias the
session's shell has before the command does, as one named git or cd may,
nor a variable that names the repository given its value by read,
printf -v or a for loop: it judges the push as if none of them ran. A
refusal reads as it did before the fix, and ends with what the hook could
not tell and how to have the push judged in its clone alone (UNMOVED,
UNTOLD) only where the reading that refused it is one the command's words
do not name.

The hook is the first fence and branch protection on GitHub the second,
so anything this module cannot read it lets through rather than wedging
the session: a push in quoting it misreads, a broken payload or a git that
answers nothing all pass, and GitHub still refuses what matters; a claim
file git cannot show, on either side of a lease, as on a branch whose
history never had one, is a claim the fence cannot judge, and the lease
passes as it did before the fence. Git is found behind what the shell
reads before a command: VAR=value assignments, the reserved words that
open a compound command or negate a pipeline (!, {, if, then, else, elif,
while, until, do, and function with its name), and the wrappers env,
command, nice, time, exec, nohup and timeout, each with its own options as
it reads them, a long one by any start of its name that names it alone, as
env --uns for env --unset, since GNU's getopt_long reads them so (WRAPPERS,
F26), and git's own options before the verb, each with its value where it
takes one (GIT_VALUE_OPTIONS), and a query after which git runs nothing
(GIT_QUERIES). A push is read as git reads its arguments: short options
in a bundle, as in -fu; a long one by any start of its name that names it
alone, as --force-w, or taken back with --no-; a value that is no
refspec; the refspecs that write many branches, the matching refspec :
and a pattern with a *; and each branch by the name git resolves,
refs/heads/main and heads/main both being main, and @ alone being HEAD.
git reads heads/X after the colon as X only where the remote has a branch
X, and makes a branch named heads/X where it has none. This module cannot
see the remote's branches and reads heads/X as X wherever it stands: that
is exact for the default branch and board, which are always there, and a
name the remote lacks becomes heads/X, a branch no rule names, so the
module at worst refuses a push git would have let make one, as
HEAD:heads/claude/03. What a command hands to another shell or command as
text, as bash -c 'git push ...', eval and env -S do, is one word to this
module and passes, as do a wrapper not named here (sudo, xargs), a git
alias for push, and a push git's configuration sends where its words do
not say. With push.default=upstream, or its old name tracking, git 2.43
sends a branch named alone, as in "git push origin claude/7" and "git
push -u origin claude/7", and the current branch on a bare "git push", to
the branch it tracks: a claim branch made with "git switch -c claude/7
origin/main", which tracks main, went to main on each of the three, where
with --no-track the first push made claude/7. A remote's push refspecs
(remote.<name>.push) send a push elsewhere too. This module reads no
configuration.

shlex reads a newline as whitespace, which would glue a line onto the one
before it, so a small pass runs first (plain_text): it turns each unquoted
newline into the separator the shell reads it as, ends a comment at its
newline, and removes a backslash-newline, the shell's line continuation.
It tracks single quotes, double quotes and the backslash, and nothing else
of the shell: the quoting inside a command substitution is read as the
quoting around it, and bash's $'...' as a $ and a quoted span. bash runs
a command text one complete command at a time, so a quote that never
closes stops the line it opens on and what follows, and the commands
before it run; and it reads a here-document's body as text, whose quotes
it does not read. Until F25's fix this module read a command whole or not
at all: a quote that never closed hid every push in the command, those
bash ran before it among them, and an apostrophe in a body opened a quote
bash never opens, which one in a later body could close around a push, as
in the shape a session writes most, a commit message by here-document, a
push, then a pull request's body by another. So the hook reads a command
more than one way and judges every push any reading finds (readings,
placed_commands): whole, as before, with a quote that never closes read as
a character of its word, so the text on either side of it is read as
commands (plain_text, lenient), which reads too much of a line bash
refuses whole and never too little; and, where the command holds
here-documents this module tells (here_document_bodies), with their bodies
cut out, but for the command substitutions of a body the shell expands,
which it runs. The whole reading still reads each body as commands: a body
line that starts a push to main is refused, and the person rephrases it or
puts the text in a file, and a cd in a body moves the shell as this module
reads it, so a push after the body is judged also where the command
starts, as if no move ran (push_places), where the second reading judges
it too. Cutting bodies out in place of reading them was tried once and
dropped: telling a here-document from an arithmetic shift, a quoted marker
or an opener inside a substitution takes more of the shell than this hook
can read, and each misreading cut real commands out of sight. As a second
reading beside the whole, a body misread hides no command the whole
reading shows, and refuses too much at worst. The bodies told are those of
<< and <<- outside quotes and in a substitution, in double quotes or not,
as in "$(cat <<'EOF' ... )", each starting at the first newline at its
operator's level, or at the level around it where the substitution or
the double quotes it stands in close first, as in x=$(cat <<'EOF') (leave),
and ending past the line that is its word, a line of a body the shell
expands joined to the next where it ends in a backslash (continues),
where bash 5.2 ends one in a substitution (closes_substitution), or at
the end of the text; a <<< here-string, arithmetic, a comment and a
quoted span open none, a (( is arithmetic only where bash reads it so,
its parentheses counted past quotes (arithmetic_end), ((cd x); ...) being
a subshell in a subshell, and a case pattern's ) closes no substitution
(case_step), nor does one inside a parameter expansion, ${y:-)}, or a
backquoted span, which bash reads whole and whose here-documents lie
inside it (expansion_step, backquoted). What the shell runs of a body it
expands, its substitutions, is read as bash reads it, the bodies of its
own here-documents cut out in turn (substitutions_in), down to
MAX_NESTING, past which the command is refused unread (TOO_DEEP). Claude
Code reads a hook that runs out of time as no objection and runs the
command, so the reading is built to take a time in proportion to the
text: the scan matches each ( once (matching_parenthesis, scan), a cd
joins no path longer than the system takes (PATH_MAX) and a pushd stacks
in the same time at any depth (stacked); shlex takes a time in the square
of a word's length, so a command longer than MAX_COMMAND is refused
unread (TOO_LONG); and a reading that still runs past READING_SECONDS,
for a shape no test has timed, is refused unread too (TOO_SLOW), never
passed.
A third reading, of each line alone, was weighed
and left out: with a quote that never closes read leniently it finds no
push in F25's shapes that the two miss, and what it alone would read is
text inside quotes that close, as a commit message given by -m "...",
which it would refuse wherever a line starts with a push; what hides a
push inside quotes that close is the quoting of a command substitution or
$'...', which want readings of their own. So a push inside $'...',
backquotes or a substitution in double quotes still passes, as does one
after a $'...' that holds a backslash before an apostrophe, which this
module reads as a quote closed early, and branch protection stands behind
it.
"""

import contextvars
import os
import re
import shlex
import subprocess
import time
import unicodedata
import weakref
from typing import NamedTuple

# git push's own options, as its parse-options reads them (git 2.43's
# git push -h): each long one by what it takes, VALUE a value attached with
# = or given as the next word, which is then no refspec, OPTIONAL a value
# only when attached with =, FLAG none. All but NOT_NEGATED also read as
# --no-<name>, which takes the option back, and git takes any start of a
# long name that names one option alone for the whole of it, --force-w for
# --force-with-lease and --mi for --mirror. The short ones bundle, -fu
# being -f and -u, and -o takes the rest of its word, or the next word.
FLAG, VALUE, OPTIONAL = "flag", "value", "optional"
PUSH_OPTIONS = {
    "verbose": FLAG, "quiet": FLAG, "repo": VALUE, "all": FLAG, "branches": FLAG, "mirror": FLAG,
    "delete": FLAG, "tags": FLAG, "dry-run": FLAG, "porcelain": FLAG, "force": FLAG,
    "force-with-lease": OPTIONAL, "force-if-includes": FLAG, "recurse-submodules": VALUE, "thin": FLAG,
    "receive-pack": VALUE, "exec": VALUE, "set-upstream": FLAG, "progress": FLAG, "prune": FLAG,
    "no-verify": FLAG, "verify": FLAG, "follow-tags": FLAG, "signed": OPTIONAL, "atomic": FLAG,
    "push-option": VALUE, "ipv4": FLAG, "ipv6": FLAG,
}
NOT_NEGATED = {"no-verify", "verify", "ipv4", "ipv6"}
PUSH_LETTERS = {"v": "verbose", "q": "quiet", "d": "delete", "n": "dry-run", "f": "force",
                "u": "set-upstream", "o": "push-option", "4": "ipv4", "6": "ipv6"}
# The options that push branches wholesale rather than the one named.
WHOLESALE = ("all", "branches", "mirror")

# Global options of git itself that take a separate value, between "git"
# and the verb, as in "git -C clone push": git 2.43's (git.c,
# handle_options), --config-env, --attr-source and --shallow-file among
# them, whose value was read as the verb until F21's fix and its review,
# so "git --config-env user.name=HOME push origin main" read as no push.
GIT_VALUE_OPTIONS = {"-C", "-c", "--config-env", "--git-dir", "--work-tree", "--namespace", "--attr-source",
                     "--shallow-file"}
# Global options after which git prints what it is asked and exits, running
# no command: "git --exec-path push origin main" pushes nothing. So does
# --list-cmds=<group>.
GIT_QUERIES = {"--exec-path", "--html-path", "--man-path", "--info-path"}
# How a push names its repository by a path, an option of git's own,
# attached or not, or a variable, set on the push, before it alone or
# exported earlier: the push then goes where this module does not look
# (push_place, Shell.named_repository). --work-tree names a work tree and
# leaves the repository to the directory. GIT_REFERENCE_BACKEND, which git
# reads since 2.54, keeps the refs where its files://<path> says, so a
# lease holds against that path's refs wherever -C points (F21's fourth
# review: git 2.55 took a lease by git -C into a clone that had not fetched
# the claim, against the refs of one that had); GIT_COMMON_DIR moves no
# ref, since git finds its ref store without reading it (git 2.43 and 2.55
# both refused that lease as stale), and is not counted.
GIT_REPOSITORY_OPTIONS = {"--git-dir", "--bare"}
GIT_REPOSITORY_VARIABLES = ("GIT_DIR", "GIT_REFERENCE_BACKEND")
# What the shell expands in a path before cd or git sees it: a variable, a
# command's output, a glob; such a path names a directory this module does
# not know (joined).
EXPANDED = set("$`*?[")
# bash's tilde prefixes that name the shell's own directories, followed by
# a / or nothing: ~+ the directory it stands in, ~- the one it stood in
# before, and ~N, ~+N and ~-N an entry of its directory stack. A path that
# starts with one names a directory this module does not know (joined),
# where os.path.expanduser left it a directory named ~+ or ~- (F21's
# fourth review).
SHELL_TILDE = re.compile(r"~(?:[+-][0-9]*|[0-9]+)(?:/|$)")
# The longest path the system takes in one call, in bytes with its NUL
# (Linux's PATH_MAX): a longer one it refuses (ENAMETOOLONG), where the
# shell's cd still reaches it a step at a time, so this module cannot tell
# the directory such a path names (joined). The bound also keeps a long
# run of cd from joining ever longer paths, a time in the square of the
# run (F25's third review: 43,000 of "cd a;" took 16 seconds).
PATH_MAX = 4096


class Wrapper(NamedTuple):
    """How a command that runs another reads its own words first, as GNU's
    getopt_long does: its short options that take a value, attached as in
    -n5 or as the next word; every long option it has, each by what it
    takes, as git push's are read (FLAG, VALUE, OPTIONAL), the flags among
    them, since a word names an option by any start of its name that names
    no other (long_option_of); and how many words follow its options before
    the command it runs, as timeout's duration does."""

    short_values: str
    long_options: dict
    operands: int = 0


# The two long options every GNU program reads.
GNU_STANDARD = {"help": FLAG, "version": FLAG}
# The commands a person puts before git to run it another way, with every
# option each reads before the command (F26: the guard compared a long one
# with the names that take a value, in full, so "env --uns HOME", "nice
# --adj 5" and "timeout --sig KILL" were read as running HOME, 5 and the
# duration). GNU coreutils' env, nice, nohup and timeout as 9.4's --help
# lists them, with env's -a and --argv0 of 9.5, its --env0-from of 9.12
# and its --quoting-style, which coreutils' NEWS lists for the release
# after 9.12, since a version that lacks an option refuses the word and
# runs nothing, and one that has it runs the command after it; GNU time as
# its src/time.c reads them in 1.9 and 1.10, whose manual's --output is a
# start of --output-file; and bash's command and exec, which take no long
# option (bash's own time takes only -p, a flag). env's short options that
# take a value hold the BSD ones too: -P altpath, the directories the env
# of FreeBSD and macOS searches for the command, and FreeBSD's -L and -U,
# a login class and a user, since those makes run the command after the
# value; GNU's env refuses all three and runs nothing (exit 125), so
# reading the next word as their value costs only the refusal of a push
# GNU would not have run. Any other option another make's wrapper reads
# and GNU's does not is read as a flag, and the push behind it passes, as
# before F26's fix.
WRAPPERS = {
    "env": Wrapper("uCSaPLU", {"argv0": VALUE, "ignore-environment": FLAG, "env0-from": VALUE, "null": FLAG,
                            "unset": VALUE, "chdir": VALUE, "default-signal": OPTIONAL, "ignore-signal": OPTIONAL,
                            "block-signal": OPTIONAL, "list-signal-handling": FLAG, "debug": FLAG,
                            "quoting-style": VALUE, "split-string": VALUE, **GNU_STANDARD}),
    "command": Wrapper("", {}),
    "nice": Wrapper("n", {"adjustment": VALUE, **GNU_STANDARD}),
    "time": Wrapper("of", {"append": FLAG, "format": VALUE, "output-file": VALUE, "portability": FLAG, "quiet": FLAG,
                           "verbose": FLAG, **GNU_STANDARD}),
    "exec": Wrapper("a", {}),
    "nohup": Wrapper("", GNU_STANDARD),
    "timeout": Wrapper("ks", {"foreground": FLAG, "kill-after": VALUE, "preserve-status": FLAG, "signal": VALUE,
                              "verbose": FLAG, **GNU_STANDARD}, operands=1),
}
# command -v and -V say what would run and run nothing.
DESCRIBES_ONLY = set("vV")
# A word the shell reads as an assignment to the command's environment,
# VAR=value or VAR+=value.
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
# The reserved words the shell reads before a command without running
# anything of their own: those that open or go on with a compound command,
# and ! that negates a pipeline. The command after one is a command of its
# own, so "if git push ...", "while ! git push ...", "do git push ..." and
# "{ git push ...; }" each run the push. function takes the next word as
# the name it defines.
RESERVED = {"!", "{", "if", "then", "else", "elif", "while", "until", "do"}

# A claim branch is claude/ and the issue's number as GitHub writes it,
# one to nine ASCII digits with no leading zero, the rule claims.NUMBER
# states (this module imports nothing of the package, since the plugin
# marketplace takes it alone). Any other digits after claude/, as in
# claude/03, read as a claim and claim nothing, since GitHub's lock, the
# refusal of a second push, works per name.
CLAIM_NUMBER = re.compile(r"0|[1-9][0-9]{0,8}")
# The claim file the protocol's Claim rule names, docs/agents/claims/N.md,
# and the line in it that names the claim's worker, "- Worker: name" (or
# the older "- Account: name"), with a note in parentheses after the name
# left to the reader, as claims.WORKER_LINE and claims.TRAILING_NOTE read
# it; tests/test_guard.py holds the two to each other.
CLAIM_DIR = "docs/agents/claims"
WORKER_LINE = re.compile(r"^\W*(?:worker|account)\W*:\s*(.+?)\s*$", re.IGNORECASE)
TRAILING_NOTE = re.compile(r"\s*\(.*\)\s*$")
# Where a fetch leaves a remote's branches in the clone, which a lease
# that names no commit holds against; origin unless the push names a
# remote (remote_ref).
REMOTE_REFS = "refs/remotes/"

# What ends a word for the shell: a # right after one of these starts a
# comment; anywhere else it is part of the word. A here-document's
# delimiter word ends at one too.
WORD_BREAKS = " \t\n;&|()<>"
# A double-quoted span as the shell reads it, up to the first " that no
# backslash escapes, a backslash and the character after it being one pair
# wherever they stand; and such a pair.
DOUBLE_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
BACKSLASH_PAIR = re.compile(r"\\(.)", re.DOTALL)
# The pairs whose backslash quote removal takes off inside double quotes,
# as bash reads a here-document's delimiter written there; a backslash
# before anything else is kept.
DOUBLE_QUOTED_ESCAPE = re.compile(r'\\([$`"\\\n])')

# The longest command the hook reads, in characters: shlex builds each
# word a character at a time, in a time that grows with the square of the
# word's length (a word of 800,000 characters took 9 seconds), so a longer
# command is refused unread rather than run the hook out of time, which
# would pass it.
MAX_COMMAND = 262144
TOO_LONG = (f"a command of more than {MAX_COMMAND} characters is refused unread: the guard would take longer to "
            "read it than a hook may run; write the text to a file first (.claude/rules/board.md)")
# How deep the here-documents of a command may nest, each in a command
# substitution of a body the shell expands, before the hook refuses the
# command unread: bash reads each such body again inside the one around
# it, and so would this module, the time it takes growing with the depth.
MAX_NESTING = 16
# The refusal of a command whose here-documents nest deeper than that,
# which the hook refuses unread rather than run out of time and pass it.
TOO_DEEP = (f"a command whose here-documents nest in command substitutions more than {MAX_NESTING} deep is "
            "refused: the guard cannot read it to tell which of its lines run; write the text to a file "
            "first (.claude/rules/board.md)")

# How long the reading of one command may take, in seconds of the
# monotonic clock, before the hook refuses it unread: Claude Code reads a
# hook that runs out of time as no objection and runs the command, so a
# reading that runs long must end in a refusal, never in the hook's
# timeout. Each part of the reading is meant to take a time in proportion
# to the text, and a command at MAX_COMMAND reads in about a second; the
# deadline stands behind that for a shape no test has timed (F25's third
# review found two that took minutes).
READING_SECONDS = 10
TOO_SLOW = (f"a command the guard cannot read in {READING_SECONDS} seconds is refused unread: a hook that runs out "
            "of time lets the command run; write the text to a file first (.claude/rules/board.md)")

# The hook's exit codes: 0 lets the tool call run, 2 stops it and sends
# stderr back into the session.
PASS = 0
REFUSE = 2


def quote_end(command, i):
    """Where the quoted span that opens at command[i], a ' or a ", ends,
    past its closing quote: a single quote's at the next ', a double
    quote's at the first " that no backslash escapes; None when it never
    closes."""
    if command[i] == "'":
        end = command.find("'", i + 1)
        return None if end == -1 else end + 1
    match = DOUBLE_QUOTED.match(command, i)
    return match.end() if match else None


def as_quoted(span):
    """A quoted span as shlex is handed it: a double-quoted one without its
    backslash-newlines, the line continuation the shell removes there too,
    every other pair kept; a single-quoted one as written."""
    if not span.startswith('"'):
        return span
    return BACKSLASH_PAIR.sub(lambda pair: "" if pair.group(1) == "\n" else pair.group(0), span)


def plain_text(command, lenient=False):
    """The command with each unquoted newline made a ; so that shlex, which
    reads a newline as whitespace, ends a command there as the shell does.
    A comment ends at its newline, which then separates; a backslash-newline
    is the line continuation the shell removes; a newline inside quotes
    stays inside its word. A quote that never closes leaves the rest of the
    text as written, and a backslash that ends it stays alone, so shlex
    refuses the whole (plain_shell); read leniently, each is a character of
    its word, as if a backslash escaped it, and the text after the quote is
    read as text no quote opened, so the commands on either side of it are
    read (F25: bash runs each complete command before the line it cannot
    read, and that line's own as it reads them)."""
    out, word_start, i = [], True, 0
    while i < len(command):
        ch = command[i]
        if ch in "'\"":
            end = quote_end(command, i)
            if end is None and not lenient:
                out.append(as_quoted(command[i:]))
                break
            out.append("\\" + ch if end is None else as_quoted(command[i:end]))
            i, word_start = end or i + 1, False
        elif ch == "\\":
            pair = command[i:i + 2]
            out.append("" if pair == "\\\n" else "\\\\" if pair == "\\" and lenient else pair)
            i, word_start = i + 2, False
        elif ch == "#" and word_start:
            end = command.find("\n", i)
            i = len(command) if end == -1 else end
        else:
            out.append(" ; " if ch == "\n" else ch)
            i, word_start = i + 1, ch in WORD_BREAKS
    return "".join(out)


def plain_shell(command):
    """The command made plain for shlex (plain_text), a quote that never
    closes left for shlex to refuse whole, as shell_parts reads it."""
    return plain_text(command)


# --- here-documents ------------------------------------------------------------

class HereDocument(NamedTuple):
    """A here-document's operator as the shell reads it: the word whose line
    ends its body, its quotes taken off; whether the body's lines lose their
    leading tabs, as after <<-; whether the shell expands the body, as it
    does where no part of the word is quoted; and the level the operator
    stands at (Context), where the first newline the shell reads after it
    starts its body, None at the top."""

    delimiter: str
    strip_tabs: bool
    expands: bool
    within: object


class Body(NamedTuple):
    """A here-document's body, as the span of the command from start to
    end, and what of it the shell runs: the command substitutions of a body
    it expands (substitutions_in)."""

    start: int
    end: int
    runs: tuple = ()


class TooDeep(Exception):
    """A command whose here-documents nest in the substitutions of bodies
    the shell expands deeper than this module reads (MAX_NESTING)."""


class TooSlow(Exception):
    """A command whose reading ran past its deadline (READING_SECONDS)."""


# The monotonic clock the deadline is read by, and the deadline of the
# reading under way, None where none is (refused_push sets it).
clock = time.monotonic
DEADLINE = contextvars.ContextVar("deadline", default=None)


def in_time():
    """Raise TooSlow where the reading under way has run past its deadline;
    called at the head of each loop of the reading (scan, parts_of,
    placed_parts), so no shape of command holds the hook past it."""
    deadline = DEADLINE.get()
    if deadline is not None and clock() > deadline:
        raise TooSlow()


# What the scan of a command's here-documents tells levels by: double
# quotes, a command substitution and a parameter expansion, ${ }, inside
# which a newline starts no body, since bash reads the expansion whole as
# part of a word before it reads the newline after it.
LEVELS = ('"', "$(", "${")
# A backquoted span as bash's parse_matched_pair reads it, up to the first
# ` that no backslash escapes, with nothing nested inside (F25's third
# review: a case pattern's ) inside one closed the substitution around it).
BACKQUOTED = re.compile(r"`(?:[^`\\]|\\.)*`", re.DOTALL)
# What matching_parenthesis keeps on its stack for a ${ it stands in.
BRACE = "{"
# The reserved words after which a word is still a command's first, so a
# case there is a case command.
BEFORE_A_COMMAND = ("then", "do", "else", "elif", "if", "while", "until", "!", "{", "time")


class Context:
    """One context the scan of a command's here-documents stands in, inside
    the one around it (outer): double quotes ("), a command substitution
    ($(), a parenthesis inside one ((), which the scan counts to tell the
    ) that closes the substitution, or a case command inside one, before
    its in (case), among its patterns (in) or in a pattern's commands
    (case)), since bash reads a pattern's ) as closing no substitution
    (F25), or a parameter expansion (${), inside which a ( or ) is text
    (F25's third review). Its level is the innermost double quotes,
    substitution or parameter expansion at or around it, where a
    here-document's body starts at a newline, and whether that stands in a
    substitution. A context is one object, told from another by identity,
    so opening or closing one takes the same time however deep the scan
    stands."""

    __slots__ = ("kind", "outer", "level", "in_substitution")

    def __init__(self, kind, outer=None):
        self.kind, self.outer = kind, outer
        self.level = self if kind in LEVELS else level(outer)
        self.in_substitution = kind == "$(" or bool(outer and outer.in_substitution)

    def __iter__(self):
        """The kinds of this context and of each around it, outward."""
        context = self
        while context is not None:
            yield context.kind
            context = context.outer


def level(contexts):
    """The double quotes or substitution the scan stands in at contexts
    (Context), None at the top: a parenthesis or a case inside a
    substitution opens no level of its own, as a subshell's does not at the
    top."""
    return None if contexts is None else contexts.level


def takes_here_document(contexts):
    """Whether a << that the scan of here_document_bodies meets in contexts,
    the double quotes, substitutions and parentheses around it, opens a
    here-document: outside quotes, and in a substitution inside double
    quotes, as in "$(cat <<'EOF' ...)", the form a session writes a commit
    message or a pull request's body in; in double quotes themselves it is
    text, and so it is in a parameter expansion, ${x:-<<EOF}, which bash
    reads whole as part of a word."""
    return contexts is None or contexts.kind not in ('"', "${")


def backquote_end(command, i):
    """Where the backquoted span that opens at command[i] ends, past the `
    that closes it (BACKQUOTED), or the end of the text where none does."""
    match = BACKQUOTED.match(command, i)
    return match.end() if match else len(command)


def backquoted(command, i, depth):
    """Where the scan goes on past the backquoted span at command[i], and
    the bodies of the here-documents inside it, or None where command[i]
    opens none. bash reads the span whole, to the first ` no backslash
    escapes, so a ) or a newline inside it closes and starts nothing
    outside (F25's third review: the ) of `case a in a) :;; esac` closed
    the substitution around it), and then reads its text as a command of
    its own, whose here-documents' bodies lie inside the span, as in
    x=`cat <<EOF ... EOF`; so the text is scanned as one, a level deeper
    (scan)."""
    if command[i] != "`":
        return None
    match = BACKQUOTED.match(command, i)
    end, inner = (match.end(), command[i + 1:match.end() - 1]) if match else (len(command), command[i + 1:])
    found = scan(inner, depth=depth + 1)[0] if "<<" in inner else []
    return end, [Body(body.start + i + 1, body.end + i + 1, body.runs) for body in found]


def matching_parenthesis(command, open_at, memo):
    """Where the ) that closes the ( at command[open_at] stands, counting the
    parentheses after it and stepping over a backslash pair, a quoted span
    and a backquoted one, and reading a ${ to the } that closes it, inside
    which a ( or ) is text but for the ( of a $(, as bash's
    parse_matched_pair does (F25's third review: the ) of ${y:-)} was
    counted); None where none closes it. A # opens no comment here, since
    parse_matched_pair counts the parentheses after one as it tells
    arithmetic from a subshell: bash reads ((echo a #(, a newline and )))
    as arithmetic. Every ( the count meets outside quotes is matched in the
    same pass and kept in memo, the match the count from it would find, so
    a text of many (( that never close is read once and not once for each
    (F25)."""
    if open_at in memo:
        return memo[open_at]
    opened, i, after_dollar = [open_at], open_at + 1, False
    while i < len(command) and opened:
        ch, dollar, after_dollar = command[i], after_dollar, False
        in_braces = opened[-1] == BRACE
        if ch == "\\":
            i += 2
        elif ch in "'\"":
            i = quote_end(command, i) or len(command)
        elif ch == "`":
            i = backquote_end(command, i)
        elif ch == "{" and dollar:
            opened.append(BRACE)
            i += 1
        elif ch == "}" and in_braces:
            opened.pop()
            i += 1
        elif ch == "(" and (dollar or not in_braces):
            if i in memo:
                if memo[i] is None:
                    break
                i = memo[i] + 1
            else:
                opened.append(i)
                i += 1
        elif ch == ")" and not in_braces:
            memo[opened.pop()] = i
            i += 1
        else:
            after_dollar = ch == "$"
            i += 1
    for unmatched in opened:
        if unmatched != BRACE:
            memo[unmatched] = None
    return memo[open_at]


def arithmetic_end(command, i, memo=None):
    """Where the arithmetic whose (( ends just before i ends, past the ))
    that closes it, counting the parentheses inside and stepping over
    quotes and backslash pairs (matching_parenthesis); None where the (( is
    no arithmetic. bash reads (( as arithmetic only where the ) that closes
    its second ( is followed at once by another, and else as a subshell in
    a subshell, as in ((cd x); cat <<EOF ...) or ((echo "))"); ...), whose
    here-documents it reads as any other (F25), and so does this module,
    and where no ) closes it, which bash cannot read, the text after it is
    read as commands. memo keeps the matches across the calls of one scan."""
    close = matching_parenthesis(command, i - 1, {} if memo is None else memo)
    return close + 2 if close is not None and command.startswith(")", close + 1) else None


def delimiter_word(command, i):
    """The word at command[i] that ends a here-document's body, its quotes
    and backslashes taken off as bash takes them off, whether any part of it
    was quoted, and where it ends: at the first character outside quotes
    that ends a word (WORD_BREAKS). None for no word at all, an empty one
    being a word only where quoted."""
    word, quoted = [], False
    while i < len(command) and command[i] not in WORD_BREAKS:
        ch = command[i]
        if ch == "\\":
            escaped = command[i + 1:i + 2]
            word.append("" if escaped == "\n" else escaped)
            i, quoted = i + 2, quoted or escaped != "\n"
        elif ch in "'\"":
            end = quote_end(command, i)
            inner = command[i + 1:end - 1] if end else command[i + 1:]
            word.append(inner if ch == "'" else DOUBLE_QUOTED_ESCAPE.sub(
                lambda pair: "" if pair.group(1) == "\n" else pair.group(1), inner))
            i, quoted = end or len(command), True
        else:
            word.append(ch)
            i += 1
    text = "".join(word)
    return (text if text or quoted else None), quoted, i


def here_operator(command, i, contexts):
    """Where the scan goes on past the << at command[i], and the
    here-document it opens: past a <<< here-string, which opens none, past
    a << the shell reads as text (takes_here_document), and past the word
    after a << or <<- and the blanks before it, which is the
    here-document's delimiter; None where it opens none, as where no word
    follows, which is a syntax error."""
    if command.startswith("<<<", i):
        return i + 3, None
    if not takes_here_document(contexts):
        return i + 2, None
    strip_tabs = command.startswith("<<-", i)
    start = i + 2 + strip_tabs
    while start < len(command) and command[start] in " \t":
        start += 1
    word, quoted, end = delimiter_word(command, start)
    return end, None if word is None else HereDocument(word, strip_tabs, not quoted, level(contexts))


class Step(NamedTuple):
    """Where the scan goes on after one step, the contexts it then stands
    in, whether a word starts there (a # there opens a comment) and whether
    that word is a command's first (a case there is a case command)."""

    at: int
    contexts: object
    word_start: bool
    command_start: bool


def expansion_step(command, i, contexts, memo):
    """The step of the scan from command[i] where an expansion starts there,
    in double quotes, a parameter expansion or neither, else None: an
    arithmetic expansion jumped over, or a parameter expansion or a
    substitution opened (F25's third review: a ) inside ${y:-)} closed the
    substitution around it). The scan reads a backquoted span before any
    step (backquoted)."""
    end = arithmetic_end(command, i + 3, memo) if command.startswith("$((", i) else None
    if end:
        return Step(end, contexts, False, False)
    if command.startswith("${", i):
        return Step(i + 2, Context("${", contexts), False, False)
    if command.startswith("$(", i):
        return Step(i + 2, Context("$(", contexts), True, True)
    return None


def double_quoted_step(command, i, contexts, memo):
    """One step of the scan inside double quotes, from command[i]: a
    backslash pair, the closing quote, an expansion (expansion_step), or
    one character."""
    if command[i] == "\\":
        return Step(i + 2, contexts, False, False)
    if command[i] == '"':
        return Step(i + 1, contexts.outer, False, False)
    return expansion_step(command, i, contexts, memo) or Step(i + 1, contexts, False, False)


def braced_step(command, i, contexts, memo):
    """One step of the scan inside a parameter expansion, ${ }, from
    command[i]: a backslash pair, a single-quoted span, which bash reads as
    one there inside double quotes too, a double quote opened, an expansion
    (expansion_step), the } that closes it, or one character, a ( or )
    among them, as bash's parse_matched_pair reads it."""
    ch = command[i]
    if ch == "\\":
        return Step(i + 2, contexts, False, False)
    if ch == "'":
        return Step(quote_end(command, i) or len(command), contexts, False, False)
    if ch == '"':
        return Step(i + 1, Context('"', contexts), False, False)
    if ch == "}":
        return Step(i + 1, contexts.outer, False, False)
    return expansion_step(command, i, contexts, memo) or Step(i + 1, contexts, False, False)


def is_word(command, i, word):
    """Whether the word at command[i] is word, whole."""
    end = i + len(word)
    return command.startswith(word, i) and (end == len(command) or command[end] in WORD_BREAKS)


def case_step(command, i, contexts, word_start, command_start):
    """One step of the scan through a case command inside a substitution,
    or None where the step is any other: bash reads a pattern's ) as
    closing no substitution (F25: x="$(case a in a) cat <<'EOF' ...)" told
    the body at the wrong level). A case among a command's first words
    opens one; its in starts the patterns, where a ( before a pattern opens
    nothing, a ) ends the pattern and esac closes the case; and in a
    pattern's commands ;;, ;& or ;;& starts the next pattern and an esac
    among a command's first words closes it. A ) there, which bash
    refuses, closes the case and what holds it."""
    kind, ch = contexts.kind, command[i]
    if kind == "case" and word_start and is_word(command, i, "in"):
        contexts.kind = "in"
        return Step(i + 2, contexts, False, False)
    if kind == "in":
        if word_start and is_word(command, i, "esac"):
            return Step(i + 4, contexts.outer, False, False)
        if ch in "()":
            contexts.kind = "case)" if ch == ")" else kind
            return Step(i + 1, contexts, True, ch == ")")
        return None
    if kind == "case)":
        if command.startswith(";;", i) or command.startswith(";&", i):
            contexts.kind = "in"
            return Step(i + (3 if command.startswith(";;&", i) else 2), contexts, True, False)
        if word_start and command_start and is_word(command, i, "esac"):
            return Step(i + 4, contexts.outer, False, False)
        if ch == ")":
            return Step(i + 1, contexts.outer.outer, True, False)
    if word_start and command_start and is_word(command, i, "case"):
        return Step(i + 4, Context("case", contexts), False, False)
    return None


def unquoted_step(command, i, contexts, word_start, command_start, memo):
    """One step of the scan outside double quotes, at the top or inside a
    substitution, from command[i] (Step). A backslash pair, a single-quoted
    span, a comment and arithmetic, (( )) or $(( )), are jumped over, a
    comment to its newline; a double quote, a parameter expansion and a
    substitution open a context (expansion_step), a parenthesis inside a
    substitution one the ) after it closes, and a case inside one is read
    as bash reads it (case_step)."""
    ch = command[i]
    if ch == "\\":
        return Step(i + 2, contexts, False, False)
    if ch == "'":
        return Step(quote_end(command, i) or len(command), contexts, False, False)
    if ch == "#" and word_start:
        end = command.find("\n", i)
        return Step(len(command) if end == -1 else end, contexts, False, command_start)
    end = arithmetic_end(command, i + 2, memo) if command.startswith("((", i) else None
    if end:
        return Step(end, contexts, False, False)
    if ch == '"':
        return Step(i + 1, Context('"', contexts), False, False)
    step = expansion_step(command, i, contexts, memo)
    if step:
        return step
    step = case_step(command, i, contexts, word_start, command_start) if contexts is not None else None
    if step:
        return step
    if ch == "(" and contexts is not None:
        return Step(i + 1, Context("(", contexts), True, True)
    if ch == ")" and contexts is not None:
        return Step(i + 1, contexts.outer, True, False)
    if word_start and command_start:
        word = next((word for word in BEFORE_A_COMMAND if is_word(command, i, word)), None)
        if word:
            return Step(i + len(word), contexts, False, True)
    return Step(i + 1, contexts, ch in WORD_BREAKS, ch in ";&|(\n" or (command_start and ch in " \t"))


def scan_step(command, i, contexts, word_start, command_start, memo):
    """One step of the scan from command[i], inside double quotes, a
    parameter expansion or neither (double_quoted_step, braced_step,
    unquoted_step)."""
    if contexts is not None and contexts.kind == '"':
        return double_quoted_step(command, i, contexts, memo)
    if contexts is not None and contexts.kind == "${":
        return braced_step(command, i, contexts, memo)
    return unquoted_step(command, i, contexts, word_start, command_start, memo)


def leave(pending, before, after):
    """The documents still pending at each level the scan left in going from
    the contexts before to those after, moved to the level after stands at,
    after those already pending there: bash reads the body of a
    here-document whose substitution or double quotes close on its
    operator's own line from the next newline it reads outside them (F25:
    x=$(cat <<'EOF') read its body from the line after the ), and the scan
    waited for a newline inside the substitution, which never came)."""
    if after is not None and after.outer is before:
        return
    context, target = before, level(after)
    while context is not None and context is not after:
        if context.level is context and context in pending:
            moved = [document._replace(within=target) for document in pending.pop(context)]
            pending.setdefault(target, []).extend(moved)
        context = context.outer


def scan(command, i=0, contexts=None, depth=0, memo=None):
    """The bodies of the here-documents the scan meets in command from i,
    standing in contexts, in order, and where it stops: past the ) that
    closes the substitution it starts in, or at the end of the text. A body
    starts at the first newline after its operator that the shell reads at
    the operator's own level (level), past a quote that spans lines, or at
    the level it is moved to where that level closes first (leave), and
    ends past the line that is its delimiter (body_end). A << is an
    operator outside quotes and in a substitution, double-quoted or not
    (takes_here_document), and none in a <<< here-string, in arithmetic, in
    a comment or in a quoted span, which the scan steps over as the shell
    reads them (scan_step), and a backquoted span is read whole, its own
    here-documents told inside it (backquoted). Each step takes the same
    time however deep the scan stands, and each ( is matched once
    (matching_parenthesis) through
    memo, kept for the text across every scan of it, so the scan takes a
    time in proportion to the text, times the depth its bodies nest at
    (depth, MAX_NESTING). A memo kept per scan, as until F25's third
    review, matched a ( again in the scan of each substitution of an
    expanded body, a time in the square of the body's length. The reading
    stops where it runs past its deadline (in_time)."""
    if depth > MAX_NESTING:
        raise TooDeep()
    closes = contexts is not None
    bodies, pending, word_start, command_start = [], {}, True, True
    memo = {} if memo is None else memo
    while i < len(command):
        in_time()
        if command[i] == "\n" and level(contexts) in pending:
            found = bodies_from(command, i + 1, pending.pop(level(contexts)), depth)
            bodies += found
            i, word_start, command_start = found[-1].end, True, True
        elif command.startswith("<<", i):
            i, document = here_operator(command, i, contexts)
            if document:
                pending.setdefault(document.within, []).append(document)
            word_start = command_start = False
        elif (span := backquoted(command, i, depth)) is not None:
            i, inner = span
            bodies += inner
            word_start = command_start = False
        else:
            before = contexts
            i, contexts, word_start, command_start = scan_step(command, i, contexts, word_start, command_start, memo)
            if contexts is not before:
                leave(pending, before, contexts)
                if closes and contexts is None:
                    return bodies, i
    return bodies, len(command)


def substitutions_in(command, start, end, depth=0):
    """The command substitutions, each $( ) whole, in the text of a body the
    shell expands, from start to end: the shell runs each when it expands
    the body, and none of the body's other text, whose quotes are text too
    (F25: "It's $(git push ...) done" hid the push between two
    apostrophes). bash reads the body's text alone, so each substitution is
    read inside it: it ends where the scan of it closes it (scan), and what
    is kept of it is its text with its own here-documents' bodies cut out
    in turn (cut_out), since their quotes are text as well (F25: a body in
    a substitution in a body, whose apostrophe paired with one after the
    push). A backslash keeps the $ after it, and $(( )) is arithmetic. One
    memo serves every scan of the text, so each ( in it is matched once
    (scan)."""
    text, found, memo, i = command[start:end], [], {}, 0
    while i < len(text):
        in_time()
        arithmetic = arithmetic_end(text, i + 3, memo) if text.startswith("$((", i) else None
        if text[i] == "\\":
            i += 2
        elif arithmetic:
            i = arithmetic
        elif text.startswith("$(", i):
            bodies, close = scan(text, i + 2, Context("$("), depth + 1, memo)
            found.append(cut_out(text, bodies, i, close))
            i = close
        else:
            i += 1
    return tuple(found)


def closes_substitution(line, delimiter):
    """Whether a line of a body inside a substitution ends the body though
    it is not the delimiter: one that starts with the delimiter and holds a
    ) after it, as "EOF)" does, where bash 5.2 ends the body after the
    delimiter, with a warning, and reads the rest of the line as the
    substitution's own text."""
    return line.startswith(delimiter) and ")" in line[len(delimiter):]


def continues(text):
    """Whether a line of a body the shell expands goes on into the next: it
    ends in a backslash that no backslash before it escapes, an odd run of
    them, which bash takes out with the newline after it before it compares
    the line with the delimiter (F25: a line of E and a backslash, then one
    of OF, ends a body whose delimiter is EOF)."""
    return (len(text) - len(text.rstrip("\\"))) % 2 == 1


def body_line(command, i, joins):
    """The line of a body that starts at command[i] as bash compares it with
    the delimiter, the spans of command it is made of, and where the
    newline that ends it stands: in a body the shell expands (joins), a
    line goes on into the next while it continues (continues), and in a
    quoted one each line stands alone."""
    pieces, spans = [], []
    while True:
        newline = command.find("\n", i)
        line_end = len(command) if newline == -1 else newline
        if not (joins and newline != -1 and continues(command[i:line_end])):
            pieces.append(command[i:line_end])
            spans.append((i, line_end))
            return "".join(pieces), spans, line_end
        pieces.append(command[i:line_end - 1])
        spans.append((i, line_end - 1))
        i = line_end + 1


def place_in(spans, k):
    """Where in the command the character k of a line made of spans
    (body_line) stands."""
    for start, end in spans:
        if k < end - start:
            return start + k
        k -= end - start
    return spans[-1][1]


def body_end(command, start, document):
    """Where a here-document's body that starts at start ends: past the
    first line that is its delimiter (body_line), that line's leading tabs
    taken off after <<-, or, inside a substitution, right after the
    delimiter on a line that ends the body there (closes_substitution); and
    at the end of the text, where bash ends a body no line ends."""
    i, inside = start, document.within is not None and document.within.in_substitution
    while i < len(command):
        line, spans, line_end = body_line(command, i, document.expands)
        indent = len(line) - len(line.lstrip("\t")) if document.strip_tabs else 0
        if line[indent:] == document.delimiter:
            return min(line_end + 1, len(command))
        if inside and closes_substitution(line[indent:], document.delimiter):
            return place_in(spans, indent + len(document.delimiter))
        i = line_end + 1
    return len(command)


def bodies_from(command, start, documents, depth=0):
    """The bodies of documents, here-documents whose operators stand on one
    line, read in order from start, the character after the newline that
    ends that line: each starts where the one before it ends (body_end), and
    what of it the shell runs is read where it expands the body
    (substitutions_in)."""
    bodies = []
    for document in documents:
        end = body_end(command, start, document)
        runs = substitutions_in(command, start, end, depth) if document.expands else ()
        bodies.append(Body(start, end, runs))
        start = end
    return bodies


def here_document_bodies(command):
    """The bodies of a shell command's here-documents (Body), in order,
    which the shell reads as text and not as commands (F25), as the scan
    from the top tells them (scan); TooDeep where they nest deeper than
    MAX_NESTING."""
    return scan(command)[0] if "<<" in command else []


def cut_out(command, bodies, start=0, end=None):
    """The command from start to end, by default the whole, with each body,
    in order and apart, taken out, and in its place what of it the shell
    runs, each on a line of its own."""
    kept, at = [], start
    for body in bodies:
        kept += [command[at:body.start], *(run + "\n" for run in body.runs)]
        at = body.end
    return "".join(kept) + command[at:len(command) if end is None else end]


def readings(command):
    """The texts this module reads a shell command as, each made plain with
    a quote that never closes read leniently (plain_text): the command
    whole, its here-documents' bodies read as commands, as before F25's
    fix; and, where it holds bodies this module tells
    (here_document_bodies), the command with them cut out, but for what
    the shell runs of them, since a body's quotes are none the shell reads.
    Every push either finds is judged, so a body misread hides no command
    the whole reading shows. TooDeep where the bodies nest deeper than this
    module reads."""
    texts = [plain_text(command, lenient=True)]
    bodies = here_document_bodies(command)
    if bodies:
        texts.append(plain_text(cut_out(command, bodies), lenient=True))
    return texts


def shell_parts(command):
    """A shell command as the shell's parts (parts_of), read whole and
    strictly: a command shlex cannot read whole, as one with a quote that
    never closes, yields nothing, which simple_commands, the template's
    shim's reading, keeps. The hook's own reading reads such a command
    leniently and more than one way (placed_commands, readings)."""
    return parts_of(plain_shell(command))


def parts_of(text):
    """A text made plain (plain_text) as the shell's parts, in order: each
    simple command as the list of its words, and each run of operators
    between them, such as ;, && or ) |, as a string; nothing where shlex
    cannot read it.

    shlex with punctuation_chars keeps the shell's operators as their own
    tokens: ;, &&, | and ( ) end one command, a redirection swallows its
    target and the file descriptor digit before it, and a parenthesis in a
    redirection's run, as in "(...)>log", still closes or opens its
    subshell, after the command it ends. Newlines and comments are read by
    plain_text first, so shlex's own comment handling, which would read a #
    inside a word as a comment and swallow the rest of the text, is off.
    """
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = []
    try:
        for token in lexer:
            in_time()
            tokens.append(token)
    except ValueError:
        return []
    parts, current, eat_next, held = [], [], False, ""
    for token in tokens:
        # An empty word, '' or "", is a word and no operator: read as one, it
        # ended the command, so "git -C '' push origin main" read as no push
        # until F21's fix.
        operator = bool(token) and all(ch in "();<>|&" for ch in token)
        if operator and (">" in token or "<" in token):
            if current and current[-1].isdigit():
                current.pop()
            eat_next = True
            held += "".join(ch for ch in token if ch in "()")
        elif operator:
            if current:
                parts.append(current)
            parts.append(held + token)
            current, eat_next, held = [], False, ""
        elif eat_next:
            eat_next = False
        else:
            current.append(token)
    if current:
        parts.append(current)
    if held:
        parts.append(held)
    return parts


def simple_commands(command):
    """The argument lists of the simple commands inside a shell command,
    read whole and strictly (shell_parts)."""
    return [part for part in shell_parts(command) if isinstance(part, list)]


def program(word):
    """A command's name without its directory: /usr/bin/git is git."""
    return word.rsplit("/", 1)[-1]


def value_is_next(bundle, value_letters):
    """Whether a bundle of short options ends in one that takes a value, so
    that the value is the next word, as in -n alone or -iu; a value
    attached, as in -n5 or -uHOME, is the rest of the bundle."""
    for at, letter in enumerate(bundle):
        if letter in value_letters:
            return at == len(bundle) - 1
    return False


def long_option_of(word, wrapper):
    """The wrapper's long option a word that starts with -- names, as GNU's
    getopt_long resolves it, the word read up to any =: the option of
    exactly that name, else the one option whose name it starts, as --uns
    names --unset; None where it starts the names of more than one option,
    as env's --d does --default-signal's and --debug's, or of none, either
    of which the wrapper refuses, running nothing. glibc also takes a start
    shared by two names of one option, which no wrapper here has."""
    name = word[2:].partition("=")[0]
    if name in wrapper.long_options:
        return name
    found = [option for option in wrapper.long_options if option.startswith(name)]
    return found[0] if len(found) == 1 else None


def long_value_is_next(word, wrapper):
    """Whether a wrapper's long option word takes the next word as its
    value: one that names an option that needs a value (VALUE), with none
    attached by =. One whose value is OPTIONAL takes it only attached, as
    env's --default-signal does, and a FLAG none. A word long_option_of
    cannot resolve is read as taking none, as every long option the guard
    did not know was before F26's fix, and the words after it as the
    wrapper reads them: the wrapper runs nothing then, so a push after it
    refused costs a rephrasing of a command that runs nothing, and a
    wrapper of another version, which may read the word as one option, as
    an env from before --ignore-signal reads --i as --ignore-environment,
    has its push judged. No start that options of one wrapper share here
    is one of an option that takes the next word, so the word is read as
    each of the options it starts would read it."""
    return "=" not in word and wrapper.long_options.get(long_option_of(word, wrapper)) == VALUE


def past_options(argv, i, wrapper):
    """Where a wrapper's command starts: past its options from argv[i] on,
    each with its value (Wrapper, long_value_is_next), past a -- that ends
    them, and past the operands the wrapper takes before the command."""
    while i < len(argv) and argv[i].startswith("-"):
        word = argv[i]
        i += 1
        if word == "--":
            break
        if word.startswith("--"):
            if long_value_is_next(word, wrapper):
                i += 1
        elif value_is_next(word[1:], wrapper.short_values):
            i += 1
    return i + wrapper.operands


def describes_only(options):
    """Whether command's options say it runs nothing (DESCRIBES_ONLY), one
    of them alone or in a bundle such as -pv."""
    return any(DESCRIBES_ONLY & set(word[1:]) for word in options if not word.startswith("--"))


class Launch(NamedTuple):
    """What stands before the command itself in a simple command: the
    assignments to its environment, each wrapper that runs it, by name with
    its option words, and where the command starts, None when nothing is
    run, as after a lone assignment or command -v."""

    assignments: list
    wrappers: list
    start: object


def launch(argv):
    """argv read as the shell and the wrappers read it, past leading
    assignments, the RESERVED words, function and its name, and the
    WRAPPERS with their options (Launch)."""
    assignments, wrappers, i = [], [], 0
    while i < len(argv):
        word = argv[i]
        name = program(word)
        if word in RESERVED:
            i += 1
        elif ASSIGNMENT.match(word):
            assignments.append(word)
            i += 1
        elif word == "function":
            i += 2
        elif name in WRAPPERS:
            end = past_options(argv, i + 1, WRAPPERS[name])
            if name == "command" and describes_only(argv[i + 1:end]):
                return Launch(assignments, wrappers, None)
            wrappers.append((name, argv[i + 1:end]))
            i = end
        else:
            return Launch(assignments, wrappers, i)
    return Launch(assignments, wrappers, None)


def command_start(argv):
    """Where the command itself starts in argv, or None when nothing is run
    (launch)."""
    return launch(argv).start


def push_arguments(argv):
    """The arguments after "push" when argv is a git push, else None; git
    may stand behind assignments and wrappers (command_start), and a query
    of git's own, as --exec-path alone, runs no command at all."""
    start = command_start(argv)
    if start is None or program(argv[start]) != "git":
        return None
    i = start + 1
    while i < len(argv):
        word = argv[i]
        if word in GIT_QUERIES or word.startswith("--list-cmds"):
            return None
        if word in GIT_VALUE_OPTIONS:
            i += 2
        elif word.startswith("-"):
            i += 1
        elif word == "push":
            return argv[i + 1:]
        else:
            return None
    return None


def assigns_repository(word):
    """Whether an assignment, or a name export hands on, is to a variable
    that names the repository (GIT_REPOSITORY_VARIABLES)."""
    return word.split("=", 1)[0].rstrip("+") in GIT_REPOSITORY_VARIABLES


def joined(place, path, physical=False):
    """The directory a path names from place: an absolute path in place of
    it, whatever place is, a relative one joined on, ~ the home directory,
    and an empty path, which git -C and bash's cd both take as no change,
    place itself; None when the path holds what the shell expands first
    (EXPANDED) or starts with a tilde of bash's that names one of the
    shell's own directories (SHELL_TILDE), when it is relative and place is
    None, and when the system cannot read it, as a ~ before a NUL byte or a
    character no file name can hold, which expanduser refuses with
    ValueError (F21's third review: until then such a path raised, and the
    hook with it, and an absolute path after a place this module could not
    tell was one it could not tell either), and when the path it names is
    too long for the system to take in one call (PATH_MAX). A logical
    path, the one cd keeps, is normalised as the shell normalises it, a ..
    taking back the step before it; a physical one, as git -C and env -C change directory,
    is left as written for the system to resolve, since there a .. after a
    symbolic link climbs from where the link leads."""
    if EXPANDED & set(path) or SHELL_TILDE.match(path):
        return None
    if not path:
        return place
    try:
        path = os.path.expanduser(path)
    except ValueError:
        return None
    if not os.path.isabs(path):
        if place is None:
            return None
        path = os.path.join(place, path)
    path = path if physical else os.path.normpath(path)
    return None if len(path) >= PATH_MAX or len(path.encode("utf-8", "surrogatepass")) >= PATH_MAX else path


def names_chdir(word):
    """Whether one of env's long option words names --chdir, in full or by
    any start of it that names no other option of env's, as --ch does
    (long_option_of)."""
    return long_option_of(word, WRAPPERS["env"]) == "chdir"


def env_directory(options):
    """The directory env's option words name for the command it runs, as
    GNU env reads -C DIR, -CDIR, a bundle that ends in C, and --chdir or a
    start of it (names_chdir, F26: "env --ch=../other" was judged where env
    was run), with its value attached or as the next word: the last one
    given, since env changes directory once, to the last directory it was
    given, from where it was run (F21's third review: "env -C sub -C
    ../other" runs in ../other); None where none is given."""
    found, env, i = None, WRAPPERS["env"], 0
    while i < len(options):
        word = options[i]
        i += 1
        if word == "--":
            break
        if word.startswith("--"):
            value = word.partition("=")[2]
            if long_value_is_next(word, env) and i < len(options):
                value, i = options[i], i + 1
            if names_chdir(word):
                found = value
            continue
        for at, letter in enumerate(word[1:], start=1):
            if letter in env.short_values:
                value = word[at + 1:]
                if not value and i < len(options):
                    value, i = options[i], i + 1
                if letter == "C":
                    found = value
                break
    return found


def chdir_paths(argv):
    """The paths the git push argv changes directory by before it pushes,
    in the order chdir reads them: the directory each env before it names
    (env_directory), then each -C git is given; None where the push names
    its repository by a path, with --git-dir, --bare or a variable set on
    it (GIT_REPOSITORY_VARIABLES), so it goes where this module does not
    look."""
    found = launch(argv)
    if any(assigns_repository(word) for word in found.assignments):
        return None
    named = (env_directory(options) for name, options in found.wrappers if name == "env")
    paths = [directory for directory in named if directory is not None]
    i = found.start + 1
    while i < len(argv) and argv[i] != "push":
        word = argv[i]
        if word.split("=", 1)[0] in GIT_REPOSITORY_OPTIONS:
            return None
        if word == "-C":
            paths.append(argv[i + 1] if i + 1 < len(argv) else "")
        i += 2 if word in GIT_VALUE_OPTIONS else 1
    return paths


def push_place(argv, here):
    """The directory the git push argv runs in, from here, the directory
    its shell runs it in (placed_commands): the paths it changes directory
    by (chdir_paths), joined on in turn as chdir reads them (joined,
    physical); None where joined cannot tell, or where the push names its
    repository by a path."""
    paths = chdir_paths(argv)
    if paths is None:
        return None
    place = here
    for path in paths:
        place = joined(place, path, physical=True)
    return place


# --- where the shell runs each command ------------------------------------------

class Stacked:
    """The directory stack after a pushd: the directory it stacked (top) on
    the stack before it (below), () being the empty stack. A stack is
    made by stacked alone, which makes one object of every stack of the
    same entries, so a pushd, a popd and comparing two stacks each take the
    same time however deep the stack is; a stack kept as a tuple took a
    time in its depth for each, and a run of pushd one in the square of its
    length (F25's third review: 29,000 of "pushd a;" took 68 seconds)."""

    __slots__ = ("top", "below", "__weakref__")

    def __init__(self, top, below):
        self.top, self.below = top, below


# Every stack made and still held, by its top and the stack below it.
STACKS = weakref.WeakValueDictionary()


def stacked(below, top):
    """The stack of top on below (Stacked), one object for each."""
    key = (top, below)
    found = STACKS.get(key)
    if found is None:
        found = STACKS[key] = Stacked(top, below)
    return found


class Shell(NamedTuple):
    """Where a shell stands as it runs a command: its directory and the
    directories pushd stacked (Stacked), each None where this module cannot
    tell; whether a cd, pushd or popd ran before, made or failed, after which
    this module cannot tell that the shell stands where it reads it
    (after_a_move); and whether a variable set alone or exported names the
    repository (GIT_REPOSITORY_VARIABLES), which holds wherever the shell
    moves after, since a move changes the directory and not the variable
    (followed_and_unmoved)."""

    directory: object
    stack: object = ()
    moved: bool = False
    named_repository: bool = False


def operators(run):
    """The shell's operators in a run of them, ;; and && and the like as
    one each: ")", "|" for ")|"."""
    found, i = [], 0
    while i < len(run):
        size = 2 if run[i:i + 2] in ("&&", "||", "|&", ";;", ";&") else 1
        found.append(run[i:i + size])
        i += size
    return found


def operands_of(words):
    """A builtin's operands, past its options and a -- that ends them; a
    lone - is an operand."""
    i = 0
    while i < len(words) and words[i].startswith("-") and words[i] != "-":
        i += 1
        if words[i - 1] == "--":
            break
    return words[i:]


def rotates(word):
    """Whether pushd's or popd's operand is +N or -N, which turns the stack."""
    return word[:1] in "+-" and word[1:].isascii() and word[1:].isdigit()


def reached(shell, target, stack, is_directory):
    """The shell after a move to target: there, with stack, when target is
    a directory as the hook runs (is_directory), or one this module cannot
    tell; where it stood when target is not, since cd and pushd fail there
    and the shell runs what follows a ; or a newline from where it stood,
    and before F21's second review such a push was judged in a directory
    that holds nothing, so a lease from the clone the shell never left
    went unjudged."""
    if target is not None and not is_directory(target):
        return shell
    return Shell(target, stack)


def cd(shell, operands, is_directory):
    """Where cd takes the shell: to its operand, the home directory with
    none, along the logical path the shell keeps (joined), and where
    reached says for a target that is not a directory; where this module
    cannot tell for -, the directory before, which it does not track, and
    with -P, which takes the shell along the physical path, so that a
    later .. climbs from where a symbolic link led. -L, -e and -@ change
    nothing here."""
    words = operands_of(operands)
    options = operands[:len(operands) - len(words)]
    target = words[0] if words else "~"
    if target == "-" or any("P" in word[1:] for word in options):
        return Shell(None, shell.stack)
    return reached(shell, joined(shell.directory, target), shell.stack, is_directory)


def pushd(shell, operands, is_directory):
    """pushd DIR moves the shell to DIR and stacks where it was, where
    reached says for a DIR that is not a directory; with -n it stacks DIR
    and moves nothing, and with no operand, -, +N or -N it turns the
    stack, which this module does not follow."""
    words, moves = list(operands), True
    while words and words[0] == "-n":
        words, moves = words[1:], False
    if words and words[0] == "--":
        words = words[1:]
    if not words or words[0] == "-" or rotates(words[0]):
        return Shell(None, None)
    if not moves:
        return Shell(shell.directory, None)
    stack = None if shell.stack is None else stacked(shell.stack, shell.directory)
    return reached(shell, joined(shell.directory, words[0]), stack, is_directory)


def popd(shell, operands):
    """popd moves the shell back to where pushd last stacked it; with -n,
    +N or -N it takes another entry off, or one without the move, and from
    a stack this command did not fill it moves where this module cannot
    tell."""
    if operands == ["-n"]:
        return Shell(shell.directory, None)
    if operands or not shell.stack:
        return Shell(None, None)
    return Shell(shell.stack.top, shell.stack.below)


# The words before a command that leave it run by the shell itself, so a
# cd behind them moves the shell: command, and time, which bash reads as a
# keyword that times what follows in the same shell.
IN_THE_SHELL = ("command", "time")


def after_move(before, after):
    """The shell after a cd, pushd or popd, made or failed, from the shell
    before it: one that has moved, and whose repository a variable names
    where it named it before, since a move changes the directory and not
    the variable."""
    return after._replace(moved=True, named_repository=before.named_repository)


def naming_repository(shell):
    """The shell once a variable that names the repository is set alone or
    exported (GIT_REPOSITORY_VARIABLES): git goes to a repository this
    module does not follow, which puts the directory out of its reading,
    and the variable names it after any later move or -C (Shell)."""
    return shell._replace(directory=None, named_repository=True)


def moved_to(argv, shell, is_directory):
    """Where the shell stands after the simple command argv: cd, pushd and
    popd, run by the shell itself, behind assignments, reserved words,
    command, time or builtin (IN_THE_SHELL), move it, cd and pushd only to
    a directory (reached), and leave it moved whether they move it or not
    (after_move); GIT_DIR, or another variable that names the repository,
    set alone or exported, names a repository this module does not follow
    (naming_repository); anything else, a cd another program runs among
    them, as env cd does, leaves it where it was."""
    found = launch(argv)
    if found.start is None:
        named = any(assigns_repository(word) for word in found.assignments)
        return naming_repository(shell) if named else shell
    if any(name not in IN_THE_SHELL for name, _ in found.wrappers):
        return shell
    name, operands = argv[found.start], argv[found.start + 1:]
    if name == "builtin" and operands:
        name, operands = operands[0], operands[1:]
    if name in ("export", "declare", "typeset", "readonly", "local"):
        return naming_repository(shell) if any(assigns_repository(word) for word in operands) else shell
    if name == "popd":
        return after_move(shell, popd(shell, operands))
    moves = {"cd": cd, "pushd": pushd}.get(name)
    return after_move(shell, moves(shell, operands, is_directory)) if moves else shell


def placed_commands(command, here, is_directory=os.path.isdir):
    """Each simple command in a shell command, with the shell that runs it
    (Shell), from here, where the command starts: those of the whole
    command, then those of each other reading of it (readings) that no
    reading before placed in the same shell (F25). A move (moved_to) holds
    for what follows it in the same shell and the same reading, a
    subshell's ends at its ), and each part of a pipeline, and a list run
    with & in the background, runs in a shell of its own, so its moves
    change nothing after it. A cd or pushd to what is_directory does not
    take for a directory, by default what is not one as the hook runs,
    leaves the shell where it stood. A directory this module cannot tell
    is None."""
    placed = []
    for text in readings(command):
        known = {(tuple(argv), shell) for argv, shell in placed}
        placed += [(argv, shell) for argv, shell in placed_parts(parts_of(text), here, is_directory)
                   if (tuple(argv), shell) not in known]
    return placed


def placed_parts(parts, here, is_directory):
    """Each simple command among the parts of one reading of a command
    (parts_of), with the shell that runs it, from here (placed_commands)."""
    placed, shell, opened, piped = [], Shell(here), [], False
    list_start = pipe_start = shell
    for part in parts:
        in_time()
        if isinstance(part, list):
            placed.append((part, shell))
            shell = moved_to(part, shell, is_directory)
            continue
        for operator in operators(part):
            if operator in ("|", "|&"):
                shell, piped = pipe_start, True
                continue
            if piped:
                shell, piped = pipe_start, False
            if operator == "(":
                opened.append(shell)
            elif operator == ")" and opened:
                shell = opened.pop()
            elif operator == "&":
                shell = list_start
            if operator not in ("&&", "||"):
                list_start = shell
            pipe_start = shell
    return placed


# What a refusal adds where the reading that refused the push is one the
# command's words do not name, which says why it was judged there and how
# to have it judged in its clone alone (push_places); the refusal itself
# reads as it read before F21's fix. UNMOVED is the reading from where the
# command starts, after a cd, pushd or popd, as if none ran, and, where a
# relative -C names the clone, in the session's own clone (after_a_move);
# UNTOLD the session's own clone, where this module cannot tell the clone
# or reads it from a ~ (from_a_tilde).
UNMOVED = ("; the push was judged from where the command starts too, since the hook cannot tell where its cd, pushd "
           "or popd leave the shell: name the clone with git -C and an absolute path that holds nothing the "
           "shell expands, or put no cd, pushd or popd before the push, to have it judged there alone")
UNTOLD = ("; the push was judged in the session's own clone, since the hook cannot tell which clone it runs "
          "in: name the clone with git -C and an absolute path that holds nothing the shell expands, with "
          "nothing naming its repository as GIT_DIR, --git-dir and --bare do, to have the push judged there "
          "alone")


class Reading(NamedTuple):
    """One clone a push may run in, None for its words alone, and what a
    refusal there adds."""

    place: object
    added: str = ""


def followed_and_unmoved(argv, shell, here):
    """Where the git push argv runs as this module follows the shell's
    moves (shell), and where it would run had none of them run, from here
    (push_place); both None, its words alone, while a variable set alone or
    exported before it names its repository (Shell.named_repository),
    which it names whatever cd or -C comes after (F21's fourth review:
    after an export of GIT_DIR an absolute -C was taken for the clone the
    push runs in, alone, though git took the refs GIT_DIR named, so a lease
    onto another worker's claim that 0.5.0's guard refused passed)."""
    if shell.named_repository:
        return None, None
    return push_place(argv, shell.directory), push_place(argv, here)


def after_a_move(argv, shell):
    """Whether the git push argv may run in the session's own clone though
    this module reads it elsewhere: after any cd, pushd or popd, made or
    failed (Shell.moved), where the clone it runs in hangs on where the
    shell stands, as a relative -C does, which is read from where this
    module puts the shell, a cd into a directory the command makes first
    counting as failed and one behind a test that fails as made; an
    absolute -C names its clone wherever the shell stands (F21's fourth
    review: "mkdir -p tmp && cd tmp && git -C .. push origin HEAD" from a
    clone on main was judged in its parent alone, and passed)."""
    return shell.moved and push_place(argv, None) is None


def from_a_tilde(argv):
    """Whether the git push argv names its clone from a ~: of the paths it
    changes directory by (chdir_paths), the last that names a place
    wherever the shell stands, an absolute one or one that starts with a
    ~, starts with a ~, and any after it are joined on what the ~ names.
    This module reads a ~ from the hook's own HOME, while the shell reads
    it from a HOME the command may set before it, and leaves one in
    quotes, which this module does not keep, as written, so such a push
    may run elsewhere than this module reads it, in the session's own
    clone among other places (F21's fourth review: from b-own, which had
    fetched a's claim, "HOME=<the clones>; git -C ~/b-own push
    --force-with-lease origin HEAD:claude/7" was judged in the hook's own
    ~/b-own alone and passed, where 0.5.0's guard refused it)."""
    anchors = [path for path in chdir_paths(argv) or () if path.startswith("~") or os.path.isabs(path)]
    return bool(anchors) and anchors[-1].startswith("~")


def push_places(argv, shell, here):
    """The clones the git push argv may run in, each to judge it in, from
    shell, where the shell stands as this module follows its moves
    (placed_commands), and here, where the command starts: the clone the
    moves take it to, its words alone where this module cannot tell which
    (followed_and_unmoved); then, where it differs, the clone it runs in if
    none of the moves ran, since this module reads a cd in a
    here-document's body, after a test that fails or in a function never
    called as one the shell makes; and here, the session's own clone, as
    every push was judged before F21's fix, where either cannot be told or
    a ~ names the clone (from_a_tilde), or after a move where a relative -C
    names it (after_a_move). git's and env's -C are joined on in each. Each
    reading after the first is one the command's words do not name, and
    carries what a refusal there adds (UNMOVED, UNTOLD)."""
    followed, unmoved = followed_and_unmoved(argv, shell, here)
    found = [Reading(followed)]
    if unmoved is not None and unmoved != followed:
        found.append(Reading(unmoved, UNMOVED))
    if here is not None and here not in (followed, unmoved):
        if followed is None or unmoved is None or from_a_tilde(argv):
            found.append(Reading(here, UNTOLD))
        elif after_a_move(argv, shell):
            found.append(Reading(here, UNMOVED))
    return found


def refused_push(command, here, branches_of=None, reader_of=None, is_directory=None):
    """Why the first push of a shell command that this module refuses is
    refused, or None when it refuses none. Each push is judged in each
    clone it may run in, from here, the directory the command starts in
    (placed_commands, push_places), its current and default branch read
    there (branches_of, by default branches) and, for a lease, its claim
    files (reader_of, by default claim_file_reader), each place asked once,
    and refused when any judgement refuses it, with what that reading adds
    (F21: until the fix every push was judged in here's clone, so "git -C
    ../clone push" from a claim branch sent ../clone's main, and a lease
    from a clone that had fetched another's claim was judged in one that
    had not; since its third review a push after a move is judged where
    the command starts too, and one whose clone this module cannot tell in
    here's; and since its fourth one by a relative -C after a move, or by a
    -C from a ~, in here's too, and one whose repository a variable names
    by its words and in here's whatever cd or -C comes after. So every push
    is judged in here's clone, as before the fix, save one whose clone
    git's or env's -C names by an absolute path, or a relative one with no
    cd, pushd or popd before it, holding nothing the shell expands, and
    whose repository nothing names: that push is judged where the -C names
    alone, which is where it runs save where the shell does what this
    module does not read, as the module's docstring says). A push judged by
    its words alone has no current branch and no claim files, the default
    branch being here's. is_directory, by default os.path.isdir, says which
    directories a cd or pushd can reach (placed_commands). The pushes are
    those any reading of the command finds, the whole command's first
    (readings; F25: until its fix a quote that never closed hid every
    push, and a quote in a here-document's body could hide one after it),
    so a command this module read before that fix is refused as it was; a
    command whose bodies nest deeper than MAX_NESTING, or one longer than
    MAX_COMMAND, is refused unread (TOO_DEEP, TOO_LONG), since a hook that
    ran out of time would pass it, and so is one whose reading runs past
    READING_SECONDS (TOO_SLOW). The deadline holds the reading alone: the
    git calls that judge a push have timeouts of their own (git_line)."""
    branches_of = branches_of or branches
    reader_of = reader_of or claim_file_reader
    asked = {}

    def shown(place):
        if place not in asked:
            asked[place] = (branches_of(place), reader_of(place))
        return asked[place]

    if len(command) > MAX_COMMAND:
        return TOO_LONG
    deadline = DEADLINE.set(clock() + READING_SECONDS)
    try:
        placed = placed_commands(command, here, is_directory or os.path.isdir)
    except TooDeep:
        return TOO_DEEP
    except TooSlow:
        return TOO_SLOW
    finally:
        DEADLINE.reset(deadline)
    for argv, shell in placed:
        args = push_arguments(argv)
        if args is None:
            continue
        for place, added in push_places(argv, shell, here):
            (current, default), reader = shown(place) if place is not None else ((None, shown(here)[0][1]), None)
            reason = refusal(args, current, default, reader)
            if reason:
                return reason + added
    return None


def long_option(word):
    """The git push option a word that starts with -- names, as (name,
    negated), or None when git would refuse the word, as an option it does
    not know or a start of more than one, and push nothing."""
    name = word[2:].split("=", 1)[0]
    if name in PUSH_OPTIONS:
        return name, False
    if name.startswith("no-") and name[3:] in PUSH_OPTIONS and name[3:] not in NOT_NEGATED:
        return name[3:], True
    found = {(option, False) for option in PUSH_OPTIONS if option.startswith(name)}
    found |= {(option, True) for option in PUSH_OPTIONS
              if option not in NOT_NEGATED and f"no-{option}".startswith(name)}
    return found.pop() if len(found) == 1 else None


def short_options(bundle, options):
    """Put in options what a bundle of short ones names, -fu being -f and
    -u; 1 when the last one takes the next word as its value, as -o does,
    else 0. A letter git does not know makes it refuse the push."""
    for at, letter in enumerate(bundle):
        name = PUSH_LETTERS.get(letter)
        if name is None:
            return 0
        options.pop(name, None)
        options[name] = True
        if PUSH_OPTIONS[name] == VALUE:
            return 1 if at == len(bundle) - 1 else 0
    return 0


def push_words(args):
    """git push's arguments read as git reads them: the options in force,
    by name, in the order they were last given, and the positional words,
    the remote and then the refspecs. --force-with-lease is in force as
    the list of what each giving of it named, "" for one given bare,
    which holds every ref pushed to its remote-tracking ref, and
    "ref" or "ref:commit" for one that names a ref (git push's --help);
    --no-force-with-lease takes them all back, as git does."""
    options, positionals, i = {}, [], 0
    while i < len(args):
        word = args[i]
        i += 1
        if word == "--":
            positionals += args[i:]
            break
        if word.startswith("--"):
            found = long_option(word)
            if found is None:
                continue
            name, negated = found
            named = options.pop(name, None)
            if negated:
                continue
            if PUSH_OPTIONS[name] == OPTIONAL:
                options[name] = (named if isinstance(named, list) else []) + [word.partition("=")[2]]
            else:
                options[name] = True
                if PUSH_OPTIONS[name] == VALUE and "=" not in word:
                    i += 1
        elif word.startswith("-") and word != "-":
            i += short_options(word[1:], options)
        else:
            positionals.append(word)
    return options, positionals


def moves_many(spec):
    """Whether a refspec pushes many branches: the matching refspec :,
    which updates every branch both sides have, or a pattern with a *."""
    return spec.lstrip("+") == ":" or "*" in spec


def branch_named(name):
    """The branch one side of a refspec names, as git resolves it: a name
    with refs/ left off is read as one under refs/, so heads/main is
    refs/heads/main, which is main, on either side of the colon (git 2.43
    pushed main on "git push origin heads/main" and on "HEAD:heads/main"),
    after the colon where the remote has the branch, as it always has the
    default branch and board, git making heads/X of a name it lacks.
    refs/heads/ comes off once, so refs/heads/heads/main is the branch
    heads/main; any other name under refs/, a tag's, is kept whole and is
    no branch the rules name."""
    if name.startswith("refs/"):
        return name.removeprefix("refs/heads/")
    return name.removeprefix("heads/")


def refspec_pairs(refspecs, current_branch, deleting):
    """What a push writes, as (source, branch, deleted) for each refspec: the
    branch is the refspec's destination, after the colon when it has one,
    as git resolves the name (branch_named), the current branch for HEAD,
    for @ alone, which git reads as HEAD, or for no refspec at all; the
    source is what stands before the colon, else the refspec itself, HEAD
    for @ alone and for no refspec, as git names what it would push; and
    a deletion is one under --delete or a refspec with nothing before its
    colon."""
    pairs = []
    for spec in refspecs:
        name, deleted = spec.lstrip("+"), deleting
        source = name
        if ":" in name:
            source, name = name.split(":", 1)
            deleted = deleted or not source
        elif name == "@":
            source = name = "HEAD"
        name = branch_named(name)
        if name in ("", "HEAD"):
            name = current_branch
        if name:
            pairs.append((source, name, deleted))
    if not refspecs and current_branch:
        pairs.append(("HEAD", current_branch, deleting))
    return pairs


def targets_of(refspecs, current_branch, deleting):
    """The branches a push writes, each with whether it deletes it
    (refspec_pairs)."""
    return [(name, deleted) for _, name, deleted in refspec_pairs(refspecs, current_branch, deleting)]


def misnamed_claim(name):
    """Why a push that writes this branch is refused for a name that reads
    as a claim and claims nothing (CLAIM_NUMBER), naming the issue's own
    claim branch where the digits spell one; None for any other name."""
    key = name[len("claude/"):] if name.startswith("claude/") else ""
    if not key.isdigit() or CLAIM_NUMBER.fullmatch(key):
        return None
    meant = "".join(str(unicodedata.digit(digit)) for digit in key).lstrip("0") or "0"
    own = f", so the claim branch of #{meant} is claude/{meant}" if len(meant) <= 9 else ""
    return (f"a push to {name} is refused: it reads as a claim branch and claims nothing, since a claim "
            f"branch is claude/ and the issue's number in one to nine ASCII digits with no leading "
            f"zero{own} (docs/agents/PROTOCOL.md, Claim)")


def claim_number(name):
    """The issue a branch claims, as text, or None for any other name: the
    protocol's Claim rule, CLAIM_NUMBER after claude/."""
    key = name[len("claude/"):] if name.startswith("claude/") else ""
    return key if CLAIM_NUMBER.fullmatch(key) else None


def claim_path(number):
    return f"{CLAIM_DIR}/{number}.md"


def worker_named(text):
    """The worker a claim file names (WORKER_LINE), or None when no line of
    it does, or there is no file."""
    for line in (text or "").splitlines():
        match = WORKER_LINE.match(line)
        if match:
            return TRAILING_NOTE.sub("", match.group(1)).strip("`*_ ")
    return None


def lease_holds(values, name):
    """How a --force-with-lease given as values (push_words) holds the
    branch name: None when it does not, so git pushes that ref as a plain
    push would; else the commit the lease expects there, "" for the
    remote-tracking ref, as a bare --force-with-lease, which holds every
    ref not named, or one that names the ref alone expects it. A giving
    that names the ref holds it whatever a bare one says, the first such
    giving winning, as git applies the first entry that matches the ref."""
    for value in values:
        ref, _, commit = value.partition(":")
        if ref and branch_named(ref) == name:
            return commit
    return "" if "" in values else None


def remote_ref(remote, name):
    """The remote-tracking ref of a branch for the remote a push names,
    a name with no slash or colon; a URL or a path has none, and origin's
    stands in, as the clone's best knowledge of the branch."""
    named = remote if remote and "/" not in remote and ":" not in remote else "origin"
    return f"{REMOTE_REFS}{named}/{name}"


def other_holder(name, source, expected, claim_file_at, remote="origin"):
    """Why a lease onto the claim branch name is refused as another
    worker's: the claim file on the branch the lease holds against
    names a worker, and the claim file on the branch pushed, source,
    names another or is not there. The branch the lease holds against is
    the commit expected, when the lease names one git can show, else the
    remote-tracking ref (remote_ref): a lease written with a shell
    variable names a commit git cannot show, and the push lands only when
    the remote stands at the commit meant, of which the remote-tracking
    ref is the clone's best knowledge. claim_file_at gives a claim file's
    text by a ref and an issue number, or None (claim_file_reader). None
    when the lease is allowed, or when the fence cannot judge it."""
    number = claim_number(name)
    if number is None or expected is None:
        return None
    held = worker_named(claim_file_at(expected, number)) if expected else None
    if held is None:
        held = worker_named(claim_file_at(remote_ref(remote, name), number))
    if held is None:
        return None
    pushed = worker_named(claim_file_at(source, number))
    if pushed == held:
        # The holder's own rebase, or a branch built on the holder's own
        # claim commit, which keeps the holder's work and name: the fence
        # judges whose claim a branch carries, not who pushes, since the
        # session's own name is nowhere in the command (G11b).
        return None
    theirs = f"{name} on origin is {held}'s claim (its claim file {claim_path(number)} says so)"
    yours = f"claims #{number} for {pushed}" if pushed else f"carries no claim file for #{number}"
    return (f"a push with --force-with-lease to {name} is refused: {theirs} and the branch you push {yours}; "
            f"never push to another worker's branch (.claude/rules/board.md)")


def refusal(args, current_branch, default_branch, claim_file_at=None):
    """Why this push is refused, or None when it is allowed. claim_file_at
    reads a claim file by a ref and an issue number, for the fence on a
    lease onto another worker's claim (other_holder); None, as a caller
    with no clone to read gives, leaves that fence out."""
    options, positionals = push_words(args)
    remote, refspecs, deleting = (positionals or [""])[0], positionals[1:], "delete" in options
    wholesale = [name for name in options if name in WHOLESALE]
    if wholesale:
        return (f"a push with --{wholesale[-1]} moves branches wholesale; "
                "push your own claim branch alone (.claude/rules/board.md)")
    many = [spec for spec in refspecs if moves_many(spec)]
    if many:
        return (f"a push with the refspec {many[0]} moves branches wholesale; "
                "push your own claim branch alone (.claude/rules/board.md)")
    if "force" in options:
        return ("--force overwrites history another account may hold; the one forced push "
                "the protocol allows is --force-with-lease on your own claude/ branch "
                "after a rebase (.claude/rules/board.md)")
    # A refspec's leading + forces that one ref, as --force forces them all,
    # and git lets it override --force-with-lease; the first word is the
    # remote, never a refspec, and under --delete each refspec is a name to
    # delete, the + in it part of the name.
    if not deleting and any(spec.startswith("+") for spec in refspecs):
        return ("a + before a refspec forces that ref as --force does, and git lets it override "
                "--force-with-lease; the one forced push the protocol allows is --force-with-lease "
                "on your own claude/ branch after a rebase, with no + (.claude/rules/board.md)")
    targets = targets_of(refspecs, current_branch, deleting)
    for name, deleted in targets:
        if name == default_branch:
            return (f"a push to {name} is refused: the default branch takes only "
                    "pull requests (.claude/rules/board.md)")
        if name == "board":
            return ("a push to board is refused: only the board workflow writes that "
                    "branch (.claude/rules/board.md)")
        misnamed = None if deleted else misnamed_claim(name)
        if misnamed:
            return misnamed
    if "force-with-lease" in options:
        strays = [name for name, _ in targets if not name.startswith("claude/")]
        if strays:
            return (f"--force-with-lease to {', '.join(strays)} is refused: a forced push "
                    "is for your own claude/ branch only (.claude/rules/board.md)")
        for source, name, deleted in refspec_pairs(refspecs, current_branch, deleting):
            if deleted or claim_file_at is None:
                continue
            reason = other_holder(name, source, lease_holds(options["force-with-lease"], name), claim_file_at,
                                  remote)
            if reason:
                return reason
    return None


def git_line(args, cwd):
    """One line from git, or None when git cannot answer, as in a directory
    or with an argument that holds a NUL byte or a character the system
    cannot encode, which subprocess refuses with ValueError: the directory
    comes from the command since F21's fix, and the ref of a lease came
    from it before."""
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace").strip() or None


def claim_file_reader(cwd):
    """A reader of claim files in the clone at cwd, for the fence on a
    lease (other_holder): the text of docs/agents/claims/N.md at a ref,
    at its tip, else at the commit where the ref's history last added it,
    since a finished branch has deleted the file; None when git shows
    none, for a ref the clone lacks or a branch whose history never had
    the file among other things, which the fence then does not judge.
    The ref comes from the command, so it is handed to git after
    --end-of-options, where one that starts with a dash is a ref and not
    an option."""
    def read(ref, number):
        path = claim_path(number)
        text = git_line(["show", "--end-of-options", f"{ref}:{path}"], cwd)
        if text is None:
            added = git_line(["log", "--format=%H", "--diff-filter=A", "-n", "1", "--end-of-options", ref, "--", path],
                             cwd)
            text = git_line(["show", "--end-of-options", f"{added}:{path}"], cwd) if added else None
        return text
    return read


def branches(cwd):
    """The current branch (None when detached) and the default branch."""
    current = git_line(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if current == "HEAD":
        current = None
    head = git_line(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd)
    default = head.removeprefix("origin/") if head else "main"
    return current, default


def command_of(payload):
    """The shell command a hook payload carries, or "" when the payload is
    not the shape Claude Code sends (an object whose tool_input is an
    object with a string command): a payload the guard cannot read passes,
    since branch protection still stands behind it."""
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else ""


def where_to_ask(payload, cwd):
    """The directory git is asked in: the session's own, from the payload,
    else the caller's, else here. A payload cwd that is not text is passed
    over, since git cannot be run in it."""
    session = payload.get("cwd") if isinstance(payload, dict) else None
    return (session if isinstance(session, str) and session else None) or cwd or "."


def run(payload, cwd=None):
    """The hook's answer to one tool call: the exit code and the reason,
    which is empty when the call passes. The payload is the hook's JSON
    already decoded; cli.py reads it from stdin and lets a payload that is
    not JSON pass, as this lets one that is not an object pass. The
    command starts in the payload's own cwd, else in cwd, else here, and
    each push is judged in each clone it may run in from there
    (refused_push), git being asked only once the command holds a push, so
    a session that never pushes never waits on it; and for the claim files
    a lease is judged by, only on a lease push (other_holder)."""
    command = command_of(payload)
    if "push" not in command:
        return PASS, ""
    reason = refused_push(command, where_to_ask(payload, cwd))
    return (REFUSE, reason) if reason else (PASS, "")
