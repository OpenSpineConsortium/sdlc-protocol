"""The claim: a branch named for an issue and the file that describes it.

A claim is the first push of the branch claude/<issue number>. GitHub
refuses the second push of the same name, so the branch is the lock, and
its first commit adds docs/agents/claims/<issue number>.md, which names
the account, the plan and the files the work expects to touch. The board
reads that file until the pull request has files of its own, and the
coordinator releases a claim whose branch has had no commit for a day.
A release is a comment on the issue that starts with RELEASE_MARKER;
once the board sees one, it renames the branch to
released/<issue>-<short sha>, which keeps the work and frees the name, so
the next claim is again a first push of claude/<issue number>.
This module holds those conventions and the parsing, and nothing that
talks to git or to GitHub.
"""

import re
from datetime import datetime, timedelta, timezone

PREFIX = "claude/"
CLAIM_DIR = "docs/agents/claims"
# A claim with no commit for this long is released by the coordinator,
# and the board asks a pull request idle this long whether it is parked.
IDLE_AFTER = timedelta(hours=24)
# The first line of every release comment the coordinator posts, so the
# board and the field metrics tell a release from what people write
# without reading what the comment says.
RELEASE_MARKER = "<!-- sdlc-coordinator: claim-released -->"
# Whose release comments count by GitHub's author_association word alone:
# the repository's owner, the members of its organization and its
# collaborators. Anyone else's count only when an adapter finds that they
# may write to the repository (is_release_comment).
RELEASERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
# Where a released claim's branch goes: out of the claude/ names, so the
# issue can be claimed again, and under a name of its own, so the work it
# holds stays for whoever carries it on.
RELEASED_PREFIX = "released/"

ACCOUNT_LINE = re.compile(r"^\W*account\W*:\s*(.+?)\s*$", re.IGNORECASE)
# A note in parentheses after the account, like the claim template's
# "(label account:main)", is for the reader, not the board.
TRAILING_NOTE = re.compile(r"\s*\(.*\)\s*$")
FILES_HEADING = re.compile(r"^#+\s*files to touch\s*$", re.IGNORECASE)
LIST_ITEM = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
UNNAMED = "unnamed"


def branch_of(number, prefix=PREFIX):
    """The claim branch for an issue: claude/<number>. The prefix is an
    argument so a gate policy that names another one uses the same rule."""
    return f"{prefix}{number}"


def is_claim_branch(branch, prefix=PREFIX):
    return branch.startswith(prefix)


def key_of(branch, prefix=PREFIX):
    """What follows claude/ in a claim branch: the issue number by protocol,
    kept as text because a hand-made branch may put something else there
    and the board still shows it. None for a branch that is no claim."""
    return branch[len(prefix):] if is_claim_branch(branch, prefix) else None


def number_of(branch, prefix=PREFIX):
    """The issue number a claim branch names, or None when the branch is no
    claim or what follows claude/ is not a number. Decimal digits only, as
    the template's check matched them with \\d: a superscript passes
    isdigit() but not int(), and a branch name must never raise."""
    key = key_of(branch, prefix)
    return int(key) if key is not None and key.isdecimal() else None


def claim_path(number):
    """The claim file's path inside the repository, as text with slashes."""
    return f"{CLAIM_DIR}/{number}.md"


def by_number(value):
    """A sort key that puts numbers in order and anything else after them,
    so claude/9 comes before claude/100 and claude/x after both."""
    text = str(value)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def claim_account(text):
    """The account a claim file names, from a line like "Account: main" in
    any of the Markdown spellings a hand might give it, without a note in
    parentheses after it; "unnamed" when no line does."""
    for line in text.splitlines():
        match = ACCOUNT_LINE.match(line)
        if match:
            return TRAILING_NOTE.sub("", match.group(1)).strip("`*_ ")
    return UNNAMED


def claim_files(text):
    """The paths a claim file lists under its "Files to touch" heading, one
    list line each, up to the next heading, sorted: what the board shows
    for a claim whose pull request does not exist yet."""
    files = []
    inside = False
    for line in text.splitlines():
        if FILES_HEADING.match(line):
            inside = True
            continue
        if inside and line.startswith("#"):
            break
        match = LIST_ITEM.match(line) if inside else None
        if match:
            files.append(match.group(1).strip("`"))
    return sorted(files)


def parse_time(value):
    """An ISO 8601 time as GitHub or git writes it, as an aware datetime; a
    datetime given is returned as it is. A time without a zone is UTC."""
    if isinstance(value, datetime):
        moment = value
    else:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def is_idle(last_change, now, idle_after=IDLE_AFTER):
    """Whether more than idle_after has passed since the last change: the
    one rule behind the board's idle comment, the coordinator's release
    and the board's reading of a release (was_released)."""
    return now - parse_time(last_change) > idle_after


def release_due(claims, now, idle_after=IDLE_AFTER, released=None):
    """The claims the coordinator releases: those whose branch has had no
    commit for more than idle_after. The claims are a mapping, or pairs,
    from the claim's key (its branch, or the issue number) to the time of
    the branch's last commit, as a datetime or as GitHub writes it; the
    answer is the keys due, in issue order, so the comments the coordinator
    posts come out the same for the same state. released maps the same
    keys to the time of the latest release comment on each claim's issue;
    a claim that comment already released (was_released) is not due
    again, so a coordinator that runs every hour releases it once."""
    pairs = claims.items() if hasattr(claims, "items") else claims
    released = released or {}
    due = [key for key, last_commit in pairs
           if is_idle(last_commit, now, idle_after) and not was_released(released.get(key, ""), last_commit, idle_after)]
    return sorted(due, key=issue_order)


def issue_order(key):
    """A sort key for a claim given as its branch or as its issue number."""
    text = str(key)
    return by_number(key_of(text) if is_claim_branch(text) else text)


def starts_with_release_marker(comment):
    """Whether an issue comment, as the API gives it, has RELEASE_MARKER as
    its first line. A comment that quotes the marker further down, a reply
    for instance, talks about a release and is none."""
    lines = ((comment or {}).get("body") or "").strip().splitlines()
    return bool(lines) and lines[0].strip() == RELEASE_MARKER


def is_release_comment(comment, trusted=None):
    """Whether an issue comment releases a claim: its first line is the
    marker and the repository trusts its author, by GitHub's
    author_association (RELEASERS) or, for an author that word does not
    vouch for, by trusted, a callable given the author's login that an
    adapter answers from the author's permission on the repository: GitHub
    may show a private member of the organization to a workflow's token as
    a mere contributor. A comment by anyone else cannot take a claim away
    from its holder."""
    if not starts_with_release_marker(comment):
        return False
    if comment.get("author_association") in RELEASERS:
        return True
    login = (comment.get("user") or {}).get("login")
    return bool(trusted and login and trusted(login))


def latest_release(comments, trusted=None):
    """When the latest release comment among an issue's comments was made,
    as GitHub wrote the time, or "" when none of them releases a claim."""
    times = [c["created_at"] for c in comments if c.get("created_at") and is_release_comment(c, trusted)]
    return max(times, key=parse_time) if times else ""


def was_released(released_at, last_commit, idle_after=IDLE_AFTER):
    """Whether a release comment made at released_at released the claim
    whose branch was last committed to at last_commit: the claim had gone
    without a commit for more than idle_after when the comment came, as a
    release is due only then, and nothing has been committed since, which
    would mean that its holder came back or another account claimed the
    issue anew. A comment made sooner than that releases nothing, and with
    either time unknown no release can be told."""
    if not released_at or not last_commit:
        return False
    return is_idle(last_commit, parse_time(released_at), idle_after)


def released_branch(key, sha):
    """The name a released claim's branch takes: released/<issue>-<the
    first seven characters of its head commit>, so an issue released twice
    keeps both branches apart."""
    return f"{RELEASED_PREFIX}{key}-{sha[:7]}"
