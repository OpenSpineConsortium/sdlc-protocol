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
refused (other_holder). The file is read with git in the session's own
clone, so the fence costs a few git calls on a lease push and nothing
on any other command.

The hook is the first fence and branch protection on GitHub the second,
so anything this module cannot read it lets through rather than wedging
the session: an unparseable command, a broken payload or a git that
answers nothing all pass, and GitHub still refuses what matters; a claim
file git cannot show, on either side of a lease, as on a branch whose
history never had one, is a claim the fence cannot judge, and the lease
passes as it did before the fence. Git is
found behind what the shell reads before a command: VAR=value
assignments, the reserved words that open a compound command or negate a
pipeline (!, {, if, then, else, elif, while, until, do, and function with
its name), and the wrappers env, command, nice, time, exec, nohup and
timeout, each with its own options. A push is read as git reads its
arguments: short options in a bundle, as in -fu; a long one by any start
of its name that names it alone, as --force-w, or taken back with --no-;
a value that is no refspec; the refspecs that write many branches, the
matching refspec : and a pattern with a *; and each branch by the name
git resolves, refs/heads/main and heads/main both being main, and @
alone being HEAD. git reads heads/X after the colon as X only where the
remote has a branch X, and makes a branch named heads/X where it has
none. This module cannot see the remote's branches and reads heads/X as X
wherever it stands: that is exact for the default branch and board, which
are always there, and a name the remote lacks becomes heads/X, a branch
no rule names, so the module at worst refuses a push git would have let
make one, as HEAD:heads/claude/03. What a command hands to another shell
or command as text, as bash -c 'git push ...', eval and env -S do, is one
word to this module and passes, as do a wrapper not named here (sudo,
xargs), a git alias for push, and a push git's configuration sends where
its words do not say. With push.default=upstream, or its old name
tracking, git 2.43 sends a branch named alone, as in "git push origin
claude/7" and "git push -u origin claude/7", and the current branch on a
bare "git push", to the branch it tracks: a claim branch made with "git
switch -c claude/7 origin/main", which tracks main, went to main on each
of the three, where with --no-track the first push made claude/7. A
remote's push refspecs (remote.<name>.push) send a push elsewhere too.
This module reads no configuration.

shlex reads a newline as whitespace, which would glue a line onto the one
before it, so a small pass runs first: it turns each unquoted newline into
the separator the shell reads it as, ends a comment at its newline, and
removes a backslash-newline, the shell's line continuation. It tracks
single quotes, double quotes and the backslash, and nothing else of the
shell: the quoting inside a command substitution is read as the quoting
around it. In particular a here-document body is read as commands like any
other line: a body line that starts a push to main is refused, and the
person rephrases it or puts the text in a file. Cutting bodies out was
tried and dropped: telling a here-document from an arithmetic shift, a
quoted marker or an opener inside a substitution takes more of the shell
than this hook can read, each misreading cut real commands out of sight,
and the one shape it bought is rare and answered by a rephrasing.
"""

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
# and the verb, as in "git -C clone push".
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


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


def simple_commands(command):
    """The argument lists of the simple commands inside a shell command.

    shlex with punctuation_chars keeps the shell's operators as their own
    tokens: ;, &&, | and ( ) end one command, a redirection swallows its
    target and the file descriptor digit before it. A command shlex cannot
    read (an unclosed quote) yields nothing, and the caller lets it pass.
    Newlines and comments are read by plain_shell first, so shlex's own
    comment handling, which would read a # inside a word as a comment and
    swallow the rest of the text, is off.
    """
    lexer = shlex.shlex(plain_shell(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    commands, current, eat_next = [], [], False
    for token in tokens:
        operator = all(ch in "();<>|&" for ch in token)
        if operator and (">" in token or "<" in token):
            if current and current[-1].isdigit():
                current.pop()
            eat_next = True
        elif operator:
            if current:
                commands.append(current)
            current = []
            eat_next = False
        elif eat_next:
            eat_next = False
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


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


def command_start(argv):
    """Where the command itself starts in argv: past leading assignments,
    the RESERVED words, function and its name, and the WRAPPERS with their
    options; None when nothing is run, as after a lone assignment or
    command -v."""
    i = 0
    while i < len(argv):
        word = argv[i]
        name = program(word)
        if word in RESERVED or ASSIGNMENT.match(word):
            i += 1
        elif word == "function":
            i += 2
        elif name in WRAPPERS:
            end = past_options(argv, i + 1, WRAPPERS[name])
            if name == "command" and describes_only(argv[i + 1:end]):
                return None
            i = end
        else:
            return i
    return None


def push_arguments(argv):
    """The arguments after "push" when argv is a git push, else None; git
    may stand behind assignments and wrappers (command_start)."""
    start = command_start(argv)
    if start is None or program(argv[start]) != "git":
        return None
    i = start + 1
    while i < len(argv):
        word = argv[i]
        if word in GIT_VALUE_OPTIONS:
            i += 2
        elif word.startswith("-"):
            i += 1
        elif word == "push":
            return argv[i + 1:]
        else:
            return None
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
    """One line from git, or None when git cannot answer."""
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
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
    not JSON pass, as this lets one that is not an object pass. Git is
    asked in the payload's own cwd, else in cwd, else here, and only once
    the command holds a push, so a session that never pushes never waits
    on it; and for the claim files a lease is judged by, only on a lease
    push (other_holder)."""
    command = command_of(payload)
    if "push" not in command:
        return PASS, ""
    pushes = [args for argv in simple_commands(command)
              if (args := push_arguments(argv)) is not None]
    if not pushes:
        return PASS, ""
    where = where_to_ask(payload, cwd)
    current, default = branches(where)
    for args in pushes:
        reason = refusal(args, current, default, claim_file_reader(where))
        if reason:
            return REFUSE, reason
    return PASS, ""
