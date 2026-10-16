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
one whose clone git's or env's -C names by a path this module can tell,
absolute or from a ~, or relative with no cd, pushd or popd before it in
the same shell, and whose repository nothing names: that push is judged
in the clone the -C names alone. That is the clone it runs in, save where
the shell does what this module does not read: a ~ is read from the
hook's own HOME, though quotes, which this module does not keep, leave a
~ as written for the shell, and a HOME the command sets before it sends
the shell elsewhere; and this module does not follow a move or an export
in a command handed to eval or trap or in a file run by source or ., nor
what a function or alias the session's shell has before the command does,
as one named git or cd may, nor a variable that names the repository
given its value by read, printf -v or a for loop: it judges the push as
if none of them ran. A refusal reads as it did before the fix, and ends
with what the hook could not tell and how to have the push judged in its
clone alone (UNMOVED, UNTOLD) only where the reading that refused it is
one the command's words do not name.

The hook is the first fence and branch protection on GitHub the second,
so anything this module cannot read it lets through rather than wedging
the session: an unparseable command, a broken payload or a git that
answers nothing all pass, and GitHub still refuses what matters; a claim
file git cannot show, on either side of a lease, as on a branch whose
history never had one, is a claim the fence cannot judge, and the lease
passes as it did before the fence. Git is found behind what the shell
reads before a command: VAR=value assignments, the reserved words that
open a compound command or negate a pipeline (!, {, if, then, else, elif,
while, until, do, and function with its name), and the wrappers env,
command, nice, time, exec, nohup and timeout, each with its own options,
and git's own options before the verb, each with its value where it takes
one (GIT_VALUE_OPTIONS), and a query after which git runs nothing
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
before it, so a small pass runs first: it turns each unquoted newline into
the separator the shell reads it as, ends a comment at its newline, and
removes a backslash-newline, the shell's line continuation. It tracks
single quotes, double quotes and the backslash, and nothing else of the
shell: the quoting inside a command substitution is read as the quoting
around it. In particular a here-document body is read as commands like any
other line: a body line that starts a push to main is refused, and the
person rephrases it or puts the text in a file; and a cd in a body moves
the shell as this module reads it, so a push after the body is judged
also where the command starts, as if no move ran (push_places). Cutting
bodies out was tried and dropped: telling a here-document from an
arithmetic shift, a quoted marker or an opener inside a substitution
takes more of the shell than this hook can read, each misreading cut real
commands out of sight, and the one shape it bought is rare and answered
by a rephrasing.
"""

import os
import re
import shlex
import subprocess
import unicodedata
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


class Wrapper(NamedTuple):
    """How a command that runs another reads its own words first, as GNU's
    getopt does: its short options that take a value, attached as in -n5 or
    as the next word, its long options that take one, attached with = or as
    the next word, and how many words follow its options before the command
    it runs, as timeout's duration does."""

    short_values: str = ""
    long_values: frozenset = frozenset()
    operands: int = 0


# The commands a person puts before git to run it another way, as GNU's
# env, nice, time, nohup and timeout and bash's command and exec read their
# options (bash's own time takes only -p, a flag).
WRAPPERS = {
    "env": Wrapper("uCS", frozenset({"--unset", "--chdir", "--split-string"})),
    "command": Wrapper(),
    "nice": Wrapper("n", frozenset({"--adjustment"})),
    "time": Wrapper("of", frozenset({"--output", "--format"})),
    "exec": Wrapper("a"),
    "nohup": Wrapper(),
    "timeout": Wrapper("ks", frozenset({"--kill-after", "--signal"}), operands=1),
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
# comment; anywhere else it is part of the word.
WORD_BREAKS = " \t\n;&|()<>"

# The hook's exit codes: 0 lets the tool call run, 2 stops it and sends
# stderr back into the session.
PASS = 0
REFUSE = 2


def plain_shell(command):
    """The command with each unquoted newline made a ; so that shlex, which
    reads a newline as whitespace, ends a command there as the shell does.
    A comment ends at its newline, which then separates; a backslash-newline
    is the line continuation the shell removes; a newline inside quotes
    stays inside its word."""
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
            i, word_start = i + 1, ch in WORD_BREAKS
    return "".join(out)


def shell_parts(command):
    """A shell command as the shell's parts, in order: each simple command
    as the list of its words, and each run of operators between them, such
    as ;, && or ) |, as a string.

    shlex with punctuation_chars keeps the shell's operators as their own
    tokens: ;, &&, | and ( ) end one command, a redirection swallows its
    target and the file descriptor digit before it, and a parenthesis in a
    redirection's run, as in "(...)>log", still closes or opens its
    subshell, after the command it ends. A command shlex cannot read (an
    unclosed quote) yields nothing, and the caller lets it pass. Newlines
    and comments are read by plain_shell first, so shlex's own comment
    handling, which would read a # inside a word as a comment and swallow
    the rest of the text, is off.
    """
    lexer = shlex.shlex(plain_shell(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
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
    """The argument lists of the simple commands inside a shell command
    (shell_parts)."""
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


def past_options(argv, i, wrapper):
    """Where a wrapper's command starts: past its options from argv[i] on,
    each with its value (Wrapper), past a -- that ends them, and past the
    operands the wrapper takes before the command."""
    while i < len(argv) and argv[i].startswith("-"):
        word = argv[i]
        i += 1
        if word == "--":
            break
        if word.startswith("--"):
            if "=" not in word and word in wrapper.long_values:
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
    tell was one it could not tell either). A logical path, the one cd
    keeps, is normalised as the shell normalises it, a .. taking back the
    step before it; a physical one, as git -C and env -C change directory,
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
    return path if physical else os.path.normpath(path)


def env_directory(options):
    """The directory env's option words name for the command it runs, as
    GNU env reads -C DIR, -CDIR, a bundle that ends in C, and --chdir with
    its value attached or as the next word: the last one given, since env
    changes directory once, to the last directory it was given, from where
    it was run (F21's third review: "env -C sub -C ../other" runs in
    ../other); None where none is given."""
    found, env, i = None, WRAPPERS["env"], 0
    while i < len(options):
        word = options[i]
        i += 1
        if word == "--":
            break
        if word.startswith("--"):
            name, attached, value = word.partition("=")
            if not attached and name in env.long_values and i < len(options):
                value, i = options[i], i + 1
            if name == "--chdir":
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


def push_place(argv, here):
    """The directory the git push argv runs in, from here, the directory
    its shell runs it in (placed_commands): with the directory each env
    before it names (env_directory), then each -C git is given, joined on
    in turn as chdir reads them (joined, physical); None where joined
    cannot tell, or where the push names its repository by a path, with
    --git-dir, --bare or a variable set on it (GIT_REPOSITORY_VARIABLES),
    so it goes where this module does not look."""
    found = launch(argv)
    if any(assigns_repository(word) for word in found.assignments):
        return None
    place = here
    for name, options in found.wrappers:
        directory = env_directory(options) if name == "env" else None
        if directory is not None:
            place = joined(place, directory, physical=True)
    i = found.start + 1
    while i < len(argv) and argv[i] != "push":
        word = argv[i]
        if word.split("=", 1)[0] in GIT_REPOSITORY_OPTIONS:
            return None
        if word == "-C":
            place = joined(place, argv[i + 1] if i + 1 < len(argv) else "", physical=True)
        i += 2 if word in GIT_VALUE_OPTIONS else 1
    return place


# --- where the shell runs each command ------------------------------------------

class Shell(NamedTuple):
    """Where a shell stands as it runs a command: its directory and the
    directories pushd stacked, each None where this module cannot tell;
    whether a cd, pushd or popd ran before, made or failed, after which
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
    stack = None if shell.stack is None else shell.stack + (shell.directory,)
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
    return Shell(shell.stack[-1], shell.stack[:-1])


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
    (Shell), from here, where the command starts: a move (moved_to) holds
    for what follows it in the same shell, a subshell's ends at its ), and
    each part of a pipeline, and a list run with & in the background, runs
    in a shell of its own, so its moves change nothing after it. A cd or
    pushd to what is_directory does not take for a directory, by default
    what is not one as the hook runs, leaves the shell where it stood. A
    directory this module cannot tell is None."""
    placed, shell, opened, piped = [], Shell(here), [], False
    list_start = pipe_start = shell
    for part in shell_parts(command):
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
# UNTOLD the session's own clone, where this module cannot tell the clone.
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


def push_places(argv, shell, here):
    """The clones the git push argv may run in, each to judge it in, from
    shell, where the shell stands as this module follows its moves
    (placed_commands), and here, where the command starts: the clone the
    moves take it to, its words alone where this module cannot tell which
    (followed_and_unmoved); then, where it differs, the clone it runs in if
    none of the moves ran, since this module reads a cd in a
    here-document's body, after a test that fails or in a function never
    called as one the shell makes; and here, the session's own clone, as
    every push was judged before F21's fix, where either cannot be told, or
    after a move where a relative -C names the clone (after_a_move). git's
    and env's -C are joined on in each. Each reading after the first is one
    the command's words do not name, and carries what a refusal there adds
    (UNMOVED, UNTOLD)."""
    followed, unmoved = followed_and_unmoved(argv, shell, here)
    found = [Reading(followed)]
    if unmoved is not None and unmoved != followed:
        found.append(Reading(unmoved, UNMOVED))
    if here is not None and here not in (followed, unmoved):
        if followed is None or unmoved is None:
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
    here's; and since its fourth one by a relative -C after a move in
    here's too, and one whose repository a variable names by its words and
    in here's whatever cd or -C comes after. So every push is judged in
    here's clone, as before the fix, save one whose clone git's or env's -C
    names by a path this module can tell (joined), absolute or from a ~, or
    relative with no cd, pushd or popd before it, and whose repository
    nothing names: that push is judged where the -C names alone, which is
    where it runs save where the shell does what this module does not read,
    as the module's docstring says). A push judged by its words alone has
    no current branch and no claim files, the default branch being here's.
    is_directory, by default os.path.isdir, says which directories a cd or
    pushd can reach (placed_commands)."""
    branches_of = branches_of or branches
    reader_of = reader_of or claim_file_reader
    asked = {}

    def shown(place):
        if place not in asked:
            asked[place] = (branches_of(place), reader_of(place))
        return asked[place]

    for argv, shell in placed_commands(command, here, is_directory or os.path.isdir):
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
