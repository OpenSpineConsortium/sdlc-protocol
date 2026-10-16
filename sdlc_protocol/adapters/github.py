"""The protocol over GitHub's REST API, through urllib.

Four parts of the package read GitHub, and they read it the same way, so
the calls live once, here. The board collects a Snapshot, posts the
comments board.comments_due names and renames the branch of each claim
board.released_keys counts as released; the coordinator, in the same run,
releases the claims coordinator.releases_due names and tells the open work
that meets other work in a file; the team clerk calls the few methods
team.Client lists; and the merge gate asks GitHubWorld, gate.World over one
repository, how a branch stands, whether its required check passed, and
for the merge itself, which goes through GitHub's merge API and only when
the policy says the gate merges.

The token is an argument or GITHUB_TOKEN: a workflow's own token and an
App installation token are both sent as a bearer token. It travels once
per call, in the Authorization header, and never in a message: an error
names the method, the path and the status, and keeps GitHub's own words
apart, on the exception, for a caller that wants to say why.
"""

import base64
import dataclasses
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from .. import board, claims, coordinator, gate, labels, team
from ..gate import GateFailed, MainMoved, MergeRefused, MergeResult, NotAllowed

API = "https://api.github.com"
# Where an owner changes a ruleset, for the board's comment on a rename a
# ruleset refused.
WEB = "https://github.com"
# The answers GitHub gives a rename it refuses for good: a rule on the
# branch, or a token without the right. A 404 can mean only that the branch
# went away since it was read, which the next run sees for itself.
REFUSED_FOR_GOOD = {403, 422}
# What GitHub answers for an issue someone deleted.
GONE = 410
API_VERSION = "2022-11-28"
USER_AGENT = "sdlc-board"
TIMEOUT = 60
PER_PAGE = 100
# The branch creations one read of the repository's activity lists, the
# most GitHub gives on a page (began_times).
ACTIVITY_PAGE = 100
# What the Overlaps do with the work of a branch GitHub refused to date
# (began_refused_lines).
UNDATED_PULL = "date its pull request by when it was opened"
UNDATED_CLAIM = "put its claim last, as work nothing dates"
UNDATED_STRANGER = ("date a stranger's pull request from it by when it was opened and put its claim last, as work "
                    "nothing dates")
# GitHub computes mergeability after the first ask; one more ask after a
# pause is enough, and "unknown" is the honest word when it still has not.
MERGEABLE_RETRY_SECONDS = 3
# The repository's activity may not list a push the moment GitHub takes it:
# one more read after a pause before a claim is dated by its committer
# date (head_since, F8).
PUSH_RETRY_SECONDS = 3
NO_PUSH_LISTED = "the repository's activity lists no push that set the branch's head"
# In the run a branch's push started, an entry that set its head more than
# PUSH_SINCE_SLACK before the run began dates nothing: an older push of
# that head, the run's own not listed yet, or the run's own where the run
# began that much after it, queued behind other runs; either way the run's
# start dates the head, and the line says only what is so of both
# (head_since).
OLDER_PUSH_LISTED = "the newest push the repository's activity lists of the branch's head came before this run began"
# An entry of a branch's activity that names another head dates the claim
# only when it came after the run began, a push after the run read the
# branch; this much earlier still counts, for a runner's clock ahead of
# GitHub's. Dating by it can only hold a claim, never release one. In the
# run a branch's push started, an entry that set the head counts as that
# push only when it came as late as this too (OLDER_PUSH_LISTED).
PUSH_SINCE_SLACK = timedelta(minutes=5)
RECENT_MERGED = board.RECENT_MERGED

# GitHub's mergeable_state words that say only that something besides the
# branch stops the merge, a rule, a review or the draft itself: how the
# branch stands to its base is then read from mergeable and a comparison.
OPAQUE_STATES = {"blocked", "draft"}
# The permissions whose holders' release comments count when GitHub's
# author_association does not vouch for them: write and above, in the old
# permission word and in the role name.
WRITE_PERMISSIONS = {"admin", "write"}
WRITE_ROLES = {"admin", "maintain", "write"}
# A required check run that finished with one of these let the merge go;
# any other finish (failure, timed_out, cancelled, action_required, stale)
# did not.
PASSING = {"success", "neutral", "skipped"}


class GitHubError(team.ClientError):
    """A call GitHub refused or did not answer. The message names the
    method, the path and the status, never the token; status is the HTTP
    status (None when there was no answer) and detail GitHub's own message
    from the answer's body, "" when it gave none."""

    def __init__(self, message, status=None, detail=""):
        super().__init__(message)
        self.status = status
        self.detail = detail


def next_link(headers):
    """The URL of the next page named by a Link header, or None."""
    link = (headers.get("Link") if headers is not None else None) or ""
    for part in link.split(","):
        pieces = part.split(";")
        if len(pieces) >= 2 and pieces[1].strip() == 'rel="next"':
            return pieces[0].strip().strip("<>")
    return None


def error_detail(problem):
    """GitHub's message from a refused call's JSON body, or "" when the
    body is empty, unreadable or not GitHub's shape. A body cut short
    raises http.client's IncompleteRead, which is no OSError, when it is
    read, and GitHub.request reads it inside its handler of the refusal,
    whose neighbours never catch what a handler raises: only this except
    keeps it from ending the run."""
    try:
        body = json.loads(problem.read() or b"null")
    except (AttributeError, OSError, TypeError, ValueError, http.client.HTTPException):
        return ""
    return str(body.get("message") or "") if isinstance(body, dict) else ""


def quoted(name):
    """A branch or label name made safe for a URL path, slashes kept, since
    GitHub reads a ref's slashes as part of its name."""
    return urllib.parse.quote(name, safe="/")


def unsent(call, token):
    """Why a call was refused before it was sent, in words that quote no
    header, since one holds the token. http.client writes the request line
    in ASCII and refuses any other path, and every path here quotes what a
    person names (quoted), so such a path is a call built wrong; it writes a
    header in Latin-1 with no bare line break, and the token is the one
    header whose value comes from outside the adapter, so a token pasted
    with a curly quote or a newline is said as the token. Anything else
    urllib refuses to send, a proxy setting it cannot read for one, is said
    in no more words than that, since urllib's own may quote a password."""
    if not call.selector.isascii():
        return "the path holds a character outside ASCII, which http.client does not send"
    if not (token.isascii() and token.isprintable()):
        return ("the token holds a character http.client does not send in a header, such as a curly quote or a "
                "line break")
    return "urllib would not send the call"


class GitHub:
    """The REST calls the package makes, through urllib, and the methods
    team.Client lists.

    The opener and the sleep are arguments so a test can hand in fakes; the
    token is the argument, else GITHUB_TOKEN, and with neither there is no
    client, since every call would be refused.
    """

    def __init__(self, token, repo, opener=None, sleep=time.sleep):
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise GitHubError("no token: pass one, or set GITHUB_TOKEN")
        self.repo = repo
        self.opener = opener or urllib.request.build_opener()
        self.sleep = sleep
        self.asked = permissions(self)

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }

    def url(self, path, params=None):
        return API + path + ("?" + urllib.parse.urlencode(params) if params else "")

    def request(self, method, url, body=None, missing_ok=False):
        """One call; the decoded JSON and the answer's headers. A 404 is
        (None, headers) when missing_ok says absence is an answer. Every
        failure of the call, whether GitHub, the connection or http.client
        refused it, is a GitHubError, whichever part of urllib raised it;
        the URL is always the adapter's own or the next page a Link header
        names, which has the scheme urllib asks of it before trying
        anything. An answer cut short (IncompleteRead) and one that is no
        HTTP (BadStatusLine) are http.client's own exceptions, no OSError,
        which urllib passes on as they are, past every caller that turns a
        refusal into a line. A call http.client will not send is said by
        what holds the character it refuses (unsent), and the answer is read
        as JSON only once the call is done, so a call never sent is never
        said to have had an answer that was not JSON."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = self.headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        call = urllib.request.Request(url, data=data, method=method, headers=headers)
        where = f"{method} {urllib.parse.urlsplit(url).path}"
        try:
            with self.opener.open(call, timeout=TIMEOUT) as answer:
                raw, answered = answer.read(), answer.headers
        except urllib.error.HTTPError as problem:
            if problem.code == 404 and missing_ok:
                return None, problem.headers
            raise GitHubError(f"{where}: HTTP {problem.code}", problem.code, error_detail(problem)) from None
        except OSError as problem:
            raise GitHubError(f"{where}: {getattr(problem, 'reason', problem)}") from None
        except http.client.HTTPException as problem:
            raise GitHubError(f"{where}: {problem!r}") from None
        except ValueError:
            raise GitHubError(f"{where}: {unsent(call, self.token)}") from None
        try:
            return (json.loads(raw) if raw else None), answered
        except ValueError:
            raise GitHubError(f"{where}: the answer was not JSON") from None

    def get(self, path, params=None, missing_ok=False):
        data, _ = self.request("GET", self.url(path, params), missing_ok=missing_ok)
        return data

    def pages(self, path, params=None):
        """A list endpoint page by page, following the Link header, so a
        caller that has seen enough can stop asking."""
        params = dict(params or {})
        params["per_page"] = PER_PAGE
        url = self.url(path, params)
        while url:
            data, headers = self.request("GET", url)
            yield data or []
            url = next_link(headers)

    def get_all(self, path, params=None):
        """Every item of a list endpoint."""
        return [item for page in self.pages(path, params) for item in page]

    def post(self, path, body):
        data, _ = self.request("POST", self.url(path), body=body)
        return data

    def put(self, path, body):
        data, _ = self.request("PUT", self.url(path), body=body)
        return data

    def patch(self, path, body):
        data, _ = self.request("PATCH", self.url(path), body=body)
        return data

    def delete(self, path, missing_ok=False):
        data, _ = self.request("DELETE", self.url(path), missing_ok=missing_ok)
        return data

    # --- team.Client ---------------------------------------------------------

    def issue(self, number):
        return self.get(f"/repos/{self.repo}/issues/{number}")

    def open_issues(self):
        """Every open issue, with the pull requests GitHub mixes into the
        list left in, as the API gives them; the clerk and the board each
        leave them out."""
        return self.get_all(f"/repos/{self.repo}/issues", {"state": "open"})

    def branch_names(self):
        return [branch["name"] for branch in self.get_all(f"/repos/{self.repo}/branches")]

    def open_pull(self, branch):
        """The open pull request from a branch of this repository, as
        pull_detail gives it, with its standing where GitHub's word says
        only that something else stops the merge (standing_of), so the
        clerk deals a claim back for a branch behind its base, as the board
        tells one behind the default branch "main moved"; or None. A draft
        is not compared, since the clerk carries a draft whatever its
        standing. No default branch is named, so a pull request stacked on
        another branch is compared with that base, which the clerk reads as
        a base it has fallen behind and the board leaves to GitHub's word;
        the template's rulesets hold only the default branch, so GitHub
        never calls such a pull request blocked there."""
        detail = self.pull_detail(branch)
        if detail and not detail.get("draft"):
            standing = standing_of(self, detail)
            if standing is not None:
                detail["standing"] = standing
        return detail

    def pull_detail(self, branch):
        """The open pull request from a branch of this repository, in the
        detail that holds mergeable_state and the head's sha, as GitHub
        gives it, or None. The list's head filter names the branch by its
        owner, which a fork in the same organization shares (github/docs,
        content/pull-requests/reference/forks.md:113), so a pull request
        from such a fork is left out by the head and base the list gives
        (board.from_fork; F17's review); and so, from a claim's branch, is
        one that someone who may not write opened from the branch here,
        which is no claim's either (claims.opened_by_writer, may_open;
        F19), a filter for claim branches alone, as mark_strangers's is,
        since another branch's pull request is no claim's whoever opened
        it. The gate's world reads a branch's pull request through this too
        (GitHubWorld.pull_of, merge), so a stranger's from a claim's branch
        has no standing and is never merged."""
        owner = self.repo.split("/")[0]
        found = self.get(f"/repos/{self.repo}/pulls", {"state": "open", "head": f"{owner}:{branch}"}) or []
        own = [pull for pull in found
               if not board.from_fork(pull)
               and (not claims.is_claim_branch(branch) or claims.opened_by_writer(pull, may_open(self.asked)))]
        return self.get(f"/repos/{self.repo}/pulls/{own[0]['number']}") if own else None

    def check_runs(self, sha):
        """Every check run on one commit, page by page: the endpoint wraps
        each page's runs in an object, and a busy commit has more than one
        page of them."""
        runs = []
        for page in self.pages(f"/repos/{self.repo}/commits/{sha}/check-runs"):
            if isinstance(page, dict):
                runs.extend(page.get("check_runs") or [])
        return runs

    def claim_text(self, branch):
        """The claim file on a claude/<issue> branch, as the board reads it
        (claim_text below): the clerk reads whose a claim is from it (F18)."""
        return claim_text(self, branch)

    def released(self, branch):
        """Whether a release comment counts for the claim on a claude/<issue>
        branch (claim_released below): the clerk carries a released claim
        for nobody (F18's review)."""
        return claim_released(self, branch)

    def create_label(self, name, colour):
        """Make the label; GitHub answers 422 for one that exists, which is
        the label being there, not a failure."""
        try:
            self.post(f"/repos/{self.repo}/labels", {"name": name, "color": colour})
        except GitHubError as problem:
            if problem.status != 422:
                raise

    def create_issue(self, title, body, labels):
        return self.post(f"/repos/{self.repo}/issues", {"title": title, "body": body, "labels": labels})

    def add_sub_issue(self, parent, child_id):
        self.post(f"/repos/{self.repo}/issues/{parent}/sub_issues", {"sub_issue_id": child_id})

    def set_body(self, number, body):
        self.patch(f"/repos/{self.repo}/issues/{number}", {"body": body})

    def remove_label(self, number, name):
        """Take a label off an issue; GitHub's 404 for one the issue no
        longer carries is the label being off, not a failure."""
        self.delete(f"/repos/{self.repo}/issues/{number}/labels/{urllib.parse.quote(name, safe='')}",
                    missing_ok=True)


# --- the board ----------------------------------------------------------------

def settled(api, detail):
    """A pull request's detail with mergeable_state settled: asked once
    more after a pause when GitHub had not computed it, and "unknown" when
    it still has not."""
    if detail.get("mergeable") is None:
        api.sleep(MERGEABLE_RETRY_SECONDS)
        detail = api.get(f"/repos/{api.repo}/pulls/{detail['number']}")
    if detail.get("mergeable") is None:
        detail["mergeable_state"] = "unknown"
    return detail


def pull_with_detail(api, number, default_branch=None):
    """One pull request with its mergeable_state settled, its standing read
    where that word is opaque, and its file paths."""
    path = f"/repos/{api.repo}/pulls/{number}"
    detail = settled(api, api.get(path))
    standing = standing_of(api, detail, default_branch)
    if standing is not None:
        detail["standing"] = standing
    detail["files"] = [f["filename"] for f in api.get_all(path + "/files")]
    return detail


def standing_of(api, detail, default_branch=None):
    """How the pull request's branch stands to its base, in the board's
    words, where GitHub's mergeable_state says only that something else
    stops the merge (a rule, a review, the draft itself): mergeable tells
    a conflict apart, and the comparison tells behind from clean, as the
    gate's world reads such a pull request (F9: until this the board read
    blocked as blocked and draft as unknown, and neither heard "main
    moved"). None where GitHub's word is one the board reads as it is:
    behind and dirty say how the branch stands, unknown is GitHub still
    computing, and clean, unstable and has_hooks say the repository's
    rules do not hold the pull request for being behind, a draft's as any
    other's, since GitHub gives a draft the word its branch has (C0), which
    they say of a branch that is behind only under a rule that does not
    require an up-to-date branch, or under none, so the spec owes it
    nothing however far the default branch has moved (F16). None too for a pull request
    stacked on a branch other than the default branch the caller names,
    since "main moved" speaks of the default branch. A
    comparison GitHub refuses, a 5xx, a 422 or a rate limit's 403, reads
    as unknown, which keeps GitHub's own word, as the coordinator's own
    comparison does (holding): one pull request GitHub will not compare
    must not fail the board's whole run."""
    if detail.get("mergeable_state") not in OPAQUE_STATES:
        return None
    base = (detail.get("base") or {}).get("ref")
    if default_branch and base and base != default_branch:
        return None
    if not detail.get("mergeable"):
        return standing_word(detail)
    try:
        behind = behind_by(api, detail, default_branch)
    except GitHubError:
        behind = None
    return standing_word(detail, behind)


def behind_by(api, pull, default_branch=None):
    """How many commits of the pull request's base its head lacks, from
    GitHub's comparison of the two; None when there is nothing to compare
    or GitHub has no such comparison (404). Any other refusal raises."""
    base = (pull.get("base") or {}).get("ref") or default_branch
    head = (pull.get("head") or {}).get("sha")
    if not head or not base:
        return None
    found = api.get(f"/repos/{api.repo}/compare/{quoted(base)}...{head}", missing_ok=True)
    return (found or {}).get("behind_by")


def claim_text(api, branch):
    """The claim file on a claude/<issue> branch, or None when there is none
    yet, or when what stands at its path is no file: GitHub answers a folder
    with the list of what it holds, and a submodule, or a link to anything
    but a file, with an object of that type, and reading a list as a file
    raised in every run while the branch stood. A file GitHub gives no text
    of, one over 1 MB, which it answers with an empty content and the
    encoding "none", is board.UNREAD_CLAIM, never an empty claim file. The
    key is quoted, as a hand-made branch may put anything after claude/:
    http.client refuses a path it cannot send as ASCII, and a # would begin
    the URL's fragment, which asks for the claims folder."""
    name = board.branch_name(branch)
    found = api.get(
        f"/repos/{api.repo}/contents/{claims.CLAIM_DIR}/{quoted(claims.key_of(name))}.md",
        {"ref": name}, missing_ok=True,
    )
    if not isinstance(found, dict) or found.get("type", "file") != "file":
        return None
    if found.get("encoding") == "none":
        return board.UNREAD_CLAIM
    return base64.b64decode(found.get("content") or "").decode("utf-8", "replace")


def unread_claim_lines(snapshot):
    """A line for each claim file GitHub gave no text of, for the workflow's
    log, since the page says so only where no label names the worker: where
    one does, as the protocol's Announce step makes it, the row shows that
    worker and no files, as for a claim file that lists none."""
    snap = board.as_snapshot(snapshot)
    return [f"{claims.branch_of(key)}: GitHub gave no text for {claims.claim_path(key)}, a file over 1 MB, so the "
            f"board reads no worker and no files from it, and a release quotes no plan"
            for key, text in sorted(snap.claims.items(), key=lambda item: claims.by_number(item[0]))
            if text is board.UNREAD_CLAIM]


def recent_merged(api):
    """The pull requests merged last, newest first, at most RECENT_MERGED,
    from the first page of closed pull requests by last update (a hundred).
    The list depends on the repository alone, so two runs in a row render
    the same one: a merge fires two runs (the pull request closing and the
    push to the default branch), and a list measured from the previous
    board's commit lost, in the second, the merge the first had reported."""
    for page in api.pages(f"/repos/{api.repo}/pulls", {"state": "closed", "sort": "updated", "direction": "desc"}):
        merged = [p for p in page if p.get("merged_at")]
        merged.sort(key=lambda p: p["merged_at"], reverse=True)
        return merged[:RECENT_MERGED]
    return []


def claim_issues(api, branches, open_issues):
    """The issue behind each claim branch, by its key: the state and the
    label names. An open issue comes from the list already read; any other
    takes one call, so the board can say that a branch outlived its issue.
    A branch whose key is no number (claims.number_in) has none and costs
    no call: a key such as a superscript would put a path in the URL that
    http.client refuses, and that refusal stopped the whole run."""
    by_number = {str(i["number"]): i for i in open_issues}
    found = {}
    for branch in board.claim_branches(branches):
        key = claims.key_of(branch)
        if claims.number_of(branch) is None:
            continue
        issue = by_number.get(key) or issue_or_none(api, key)
        if issue is not None:
            found[key] = {"state": issue.get("state", ""), "labels": labels.label_names(issue)}
    return found


def issue_or_none(api, number):
    """The issue with this number, or None when GitHub has none by it: 404
    for a number no issue was given, and 410 for an issue someone deleted,
    whose branch the template's tidy workflow, which runs when an issue
    closes, leaves standing; the 410 raised, and stopped every run while
    the branch stood."""
    try:
        return api.get(f"/repos/{api.repo}/issues/{number}", missing_ok=True)
    except GitHubError as problem:
        if problem.status == GONE:
            return None
        raise


def claim_heads(listed):
    """The head commit of each claim branch, by branch, from the branch
    list as the API gives it; a branch given by name alone has none."""
    return {name: sha for name, sha in branch_heads(listed).items() if claims.is_claim_branch(name)}


def branch_heads(listed):
    """The head commit of every branch, by name, from the branch list as
    the API gives it; a branch given by name alone has none."""
    return {branch["name"]: (branch.get("commit") or {}).get("sha") or ""
            for branch in listed if isinstance(branch, dict) and branch.get("name")}


def may_be_due(issue):
    """Whether a release can be due on a claim on this issue, or can have
    happened: the issue is open and not labelled in-review, which a
    finished claim waiting for review is, whether or not the label claimed
    is still on it."""
    names = (issue or {}).get("labels", [])
    return issue is not None and issue.get("state") == "open" and labels.IN_REVIEW not in names


def ready_branches(pulls):
    """The head branches of the open pull requests marked ready, as the API
    details them; a pull request whose draft state is not given counts as
    ready, as the board counts it. One from a fork, or one a stranger
    opened (mark_strangers), is left out, whatever its branch is called,
    since it is no claim's (board.claim_of)."""
    return {(p.get("head") or {}).get("ref") for p in pulls if not p.get("draft") and is_claims(p)}


def is_claims(pull):
    """Whether a pull request, as the API details it, may be a claim's own:
    not from a fork, and not one mark_strangers found a stranger opened
    (board.claim_of)."""
    return not board.from_fork(pull) and not board.from_stranger(pull)


def permissions(api):
    """A callable saying whether a login may write to the repository, from
    GitHub's collaborator permission, asked once per login: True where it
    may, False where GitHub says it may not or knows no such login, and
    None where GitHub refused the call, which may_write and may_open each
    read their own way."""
    answers = {}

    def ask(login):
        if login not in answers:
            try:
                found = api.get(f"/repos/{api.repo}/collaborators/{urllib.parse.quote(login, safe='')}/permission",
                                missing_ok=True) or {}
            except GitHubError:
                answers[login] = None
            else:
                answers[login] = (found.get("permission") in WRITE_PERMISSIONS
                                  or found.get("role_name") in WRITE_ROLES)
        return answers[login]
    return ask


def may_write(api, asked=None):
    """A callable saying whether a login may write to the repository, from
    GitHub's collaborator permission (permissions), asked once per login:
    through asked where the caller passes it, else the client's own cache
    (GitHub.asked), so a board run and a clerk's look ask about an author
    once however many readers name them, and only a client with no cache
    pays for one of its own (F19's review). It stands in for
    author_association where that word does not vouch for an author:
    GitHub may show a private member of the organization to a workflow's
    token as a contributor. A call GitHub refuses counts as no, so a
    release by an author nobody can vouch for frees nothing."""
    asked = asked or getattr(api, "asked", None) or permissions(api)
    return lambda login: asked(login) is True


def may_open(asked):
    """A callable saying whether a login may have opened a claim's pull
    request (claims.opened_by_writer), from the answers permissions gives:
    unless GitHub says it may not write. A call GitHub refuses counts as
    yes, so a lost answer never takes a claim's own pull request for a
    stranger's, which would free a finished claim; it costs the runs in
    which GitHub refuses the call, each releasing no claim such a pull
    request holds."""
    return lambda login: asked(login) is not False


def mark_strangers(pulls, asked):
    """Write the board's key stranger (board.from_stranger) on each pull
    request, as the API details it, from a branch of this repository named
    as a claim's that someone who may not write opened
    (claims.opened_by_writer, may_open): it is no claim's, whatever its
    branch is called (board.claim_of; F19). An author author_association
    or an App's login vouches for costs no call; any other costs one, once
    a run, through asked. The pull requests are returned."""
    for pull in pulls:
        branch = (pull.get("head") or {}).get("ref") or ""
        if (claims.is_claim_branch(branch) and not board.from_fork(pull)
                and not claims.opened_by_writer(pull, may_open(asked))):
            pull["stranger"] = True
    return pulls


def committed_at(api, sha):
    """When the commit was committed, as GitHub writes it, or "" when GitHub
    does not know the commit."""
    commit = api.get(f"/repos/{api.repo}/git/commits/{sha}", missing_ok=True) or {}
    return (commit.get("committer") or {}).get("date") or ""


def head_since(api, branch, sha, refused=None, now=None, started_by=""):
    """When the branch's last commit came, as (pushed_at, committed_at),
    the one read and the other "" (board.Release.last_commit): when GitHub
    received the push that set the head, from the repository's activity
    (read_push, pushed_by), so no worker's clock decides how long a claim has
    been idle (F8: until its fix the head's committer date did, which the
    pusher sets, so a commit dated ahead held a claim past its day and one
    dated back had it released at once). now is when the run began, the
    wall clock unless given, taken before the run read the branch. A first
    read that lists no push that set the head, or that GitHub gives no
    answer to that holds (a 5xx, a 429, a rate limit's 403, or none at
    all), is made once more after a pause, as settled asks again for
    mergeable: the push a run was started by is the one the activity is
    likeliest not to list yet, and the committer date in its place would
    let a commit dated back have its claim released in the run its own
    push started. Where the second read dates no push either, or GitHub
    refuses the read for good, the head's committer date stands in, the
    rule before the fix, so a token that may not read the activity still
    sees claims released and no claim is held for good; but never earlier
    than the newest entry either read listed for the branch, since the
    push that set the head came no sooner than that, so an activity that
    lags still dates a claim no earlier than the push it lists last
    (latest). In the run that a push to this branch started, started_by,
    which the workflow's event names (cli.pushed_branch), a head no read
    dates is dated at the run's start instead, for that run alone, since
    that push came just before the run, whatever the two reads list: a
    fresh branch's activity that lags both reads is an empty list, with no
    floor, so the committer date would have a claim commit dated back
    released in the run its own push started (F8's third review). So is a
    head a read dates by an entry that came more than PUSH_SINCE_SLACK
    before the run began: an older push of the same head that the activity
    lists while it does not list the run's own yet, as a head pushed back
    to one the branch had before gives, which dated by that entry would be
    released in the run its own push started where the entry is more than
    a day old (OLDER_PUSH_LISTED; F8's follow-up's second review); or the
    run's own push, where the run began that much after it, queued behind
    other runs, which the run's start then dates a little late, holding
    the claim no longer than the queue held the run (its review). The next run
    reads the activity again, so no claim is held for good. refused
    records why, by branch, for the run's log (pushed_refused_lines),
    except for a repository GitHub keeps no activity of (a 404), whose
    committer dates were always the rule outside the run its push
    started, which dates it at its start with no line either, and the
    second read's 404 after a first that listed nothing, which says the
    same. It costs one call where the first read dates the push; two
    where it does not and the read is not made again, a refusal for good
    or a 404, the second the commit; and up to three with a pause where it
    is made again; in the run the branch's push started, the commit is
    not read."""
    now = now or datetime.now(timezone.utc)
    read = first = read_push(api, branch, sha, now)
    if first.again:
        api.sleep(PUSH_RETRY_SECONDS)
        read = read_push(api, branch, sha, now)
    if read.pushed and (branch != started_by or came_since(read.pushed, now)):
        return read.pushed, ""
    floor, why = latest(read.floor, first.floor), read.why or (first.why if first.floor else "")
    if branch == started_by:
        began = github_time(now)
        why = OLDER_PUSH_LISTED if read.pushed else why
        if why and refused is not None:
            refused[branch] = f"{why}, so its idleness is read from the start of this run, {began}, which its push started"
        return began, ""
    committed = committed_at(api, sha)
    if floor and latest(committed, floor) != committed:
        if refused is not None:
            refused[branch] = (f"{why}, so its idleness is read from its newest entry's time, {floor}, "
                               + ("later than its committer date" if committed else "GitHub not showing its commit"))
        return floor, ""
    if why and refused is not None:
        refused[branch] = f"{why}, so its idleness is read from its committer date"
    return "", committed


def github_time(moment):
    """A moment as GitHub writes a time, ISO 8601 in UTC to the second, so
    a run's start can stand where the activity's times do."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PushRead(NamedTuple):
    """One read of when the push that set a branch's head came: GitHub's
    time of that push where the activity dates it; else why the committer
    date would stand in, in words for the run's log ("" for a repository
    GitHub keeps no activity of, a 404), whether a second read may tell
    more, and the time of the newest entry listed for the branch, which
    the push of the head came no sooner than ("" where none was listed)."""

    pushed: str = ""
    why: str = ""
    again: bool = False
    floor: str = ""


def read_push(api, branch, sha, now=None):
    """One read of when the push that set the branch's head came
    (PushRead), now being when the run began: dated where the newest entry
    dates the head (pushed_by); else owed a second read where GitHub's
    answer may change (may_answer_later) or the list names no push of the
    head yet."""
    try:
        entry = newest_entry(api, branch)
    except GitHubError as problem:
        return PushRead(why=f"GitHub refused to say when the push that set the branch's head came "
                            f"({problem}{github_words(problem)})", again=may_answer_later(problem))
    if entry is None:
        return PushRead()
    pushed = pushed_by(entry, sha, now)
    return PushRead(pushed=pushed) if pushed else PushRead(why=NO_PUSH_LISTED, again=True, floor=entry_time(entry))


def may_answer_later(problem):
    """Whether GitHub may answer the same call after a pause: it gave no
    answer, a 5xx, a 429, or a 403 that says a rate limit was reached,
    which GitHub sends for its secondary limits; any other refusal, a 401,
    a 403 for a token that may not read, a 422, is its answer for good."""
    status = problem.status
    if status is None or status >= 500 or status == 429:
        return True
    return status == 403 and "rate limit" in (problem.detail or str(problem)).lower()


def head_pushed_at(api, branch, sha, now=None):
    """When GitHub received the push that set the branch's head, as the
    repository's activity records it (pushed_by, on the newest entry of the
    branch's activity); "" when that entry does not date it, or there is
    no entry at all; None when GitHub keeps no activity of the repository
    (a 404). Any other refusal raises."""
    entry = newest_entry(api, branch)
    return None if entry is None else pushed_by(entry, sha, now)


def newest_entry(api, branch):
    """The newest entry of the branch's activity, of any kind; {} where the
    list is empty or its first item is no entry of this branch; None where
    GitHub keeps no activity of the repository (a 404). Any other refusal
    raises."""
    ref = f"refs/heads/{branch}"
    listed = api.get(f"/repos/{api.repo}/activity", {"ref": ref, "direction": "desc", "per_page": 1},
                     missing_ok=True)
    if listed is None:
        return None
    newest = listed[0] if isinstance(listed, list) and listed else None
    return newest if isinstance(newest, dict) and newest.get("ref") == ref else {}


def pushed_by(entry, sha, now):
    """The time the branch's newest entry dates its head by: its own, when
    it set the head the run read, a push, a forced push, the branch's
    creation or a merge into it; or when it came no earlier than
    PUSH_SINCE_SLACK before now, when the run began, whatever heads it
    names, a push after the run read the branch, which says the claim had
    a push then. An older entry that names another head is stale, since a
    later push the activity does not list yet set the head the run read,
    and dating by it would read the claim as idle while its holder pushes;
    with now None only the first rule dates. "" where neither holds, or
    the entry's time is none GitHub writes."""
    at = entry_time(entry)
    if at and (entry.get("after") == sha or came_since(at, now)):
        return at
    return ""


def entry_time(entry):
    """The entry's time as GitHub writes it, ISO 8601; "" for one missing
    or none such, which dates nothing rather than failing the run."""
    at = entry.get("timestamp") if isinstance(entry, dict) else None
    if not isinstance(at, str) or not at:
        return ""
    try:
        claims.parse_time(at)
    except ValueError:
        return ""
    return at


def came_since(timestamp, now):
    """Whether GitHub's time of an entry is no earlier than PUSH_SINCE_SLACK
    before now; a time that is missing or not one is not."""
    if not timestamp or now is None or not isinstance(timestamp, str):
        return False
    try:
        return claims.parse_time(timestamp) >= now - PUSH_SINCE_SLACK
    except ValueError:
        return False


def latest(first, second):
    """The later of two times as GitHub writes them, the first where they
    are the same instant, and either one where the other is "" or none
    GitHub writes."""
    try:
        later = claims.parse_time(first) >= claims.parse_time(second)
    except (ValueError, AttributeError):
        return first if entry_time({"timestamp": first}) else second
    return first if later else second


def pushed_refused_lines(snapshot):
    """A line for each claim branch whose head the run could not date by
    the push that set it, saying why and what it read in its place, the
    committer date, the newest entry listed or the start of the run its
    push started, for the workflow's log, since the page says nothing of
    it: a token that may not read the repository's activity would
    otherwise have every claim's idleness read from its committer date on
    every run unseen."""
    snap = board.as_snapshot(snapshot)
    return [f"{branch}: {why}" for branch, why in sorted(snap.pushed_refused.items())]


def claim_released(api, branch, now=None):
    """Whether a release comment counts for the claim on a claude/<issue>
    branch, by the part of the release rule its issue's comments and the
    branch's head decide, read as releases reads them for the board: the
    latest release comment by an author the repository trusts came after
    the branch had gone more than a day without a commit, as GitHub dates
    the push of its head (head_since), and nothing was committed since, or
    the head is one a release already kept (board.release_counts). The
    labels and the pull request decide the rest (board.is_released), which
    the clerk reads from its own look (team.Snapshot.released). A branch
    with no head GitHub knows is no released claim, as the board counts
    none. It costs a call for the branch's head, one for the released
    branch that would keep it, what head_since costs and, once the branch
    has gone more than a day without a commit or its head is kept, the
    issue's comments and a call for each author whose permission must be
    asked, once a client (GitHub.asked), so the clerk's look asks about an
    author once however many claims it reads."""
    now = now or datetime.now(timezone.utc)
    name = board.branch_name(branch)
    sha = branch_sha(api, name)
    if not sha:
        return False
    key = claims.key_of(name)
    kept = branch_sha(api, claims.released_branch(key, sha)) == sha
    pushed, committed = head_since(api, name, sha, now=now)
    release = board.Release(sha=sha, committed_at=committed, pushed_at=pushed, kept=kept)
    if not kept and (not release.last_commit or not claims.is_idle(release.last_commit, now)):
        return False
    comments = api.get_all(f"/repos/{api.repo}/issues/{quoted(key)}/comments")
    release.released_at = claims.latest_release(comments, may_write(api))
    return board.release_counts(release)


def finished_draft(api, pull):
    """Whether a draft's author said it is finished (claims.said_finished),
    from the pull request's own comments; None, a pull request, costs no
    call."""
    if not pull:
        return False
    author = (pull.get("user") or {}).get("login")
    return claims.said_finished(api.get_all(f"/repos/{api.repo}/issues/{pull['number']}/comments"), author)


def releases(api, heads, found_issues, pulls, now=None, asked=None, every_head=None, refused=None, started_by=""):
    """What the release rule reads about each claim that has gone more than
    a day without a commit, or whose head a release kept, by key, as a
    board.Release's JSON shape: the branch's head commit with the time its
    last commit came, and the time of the latest release comment on its
    issue, "" when there is none. The coordinator releases such a claim
    unless a comment already did (coordinator.releases_due), and the board
    counts it released once the label claimed is off too
    (board.released_keys). A finished claim, its issue labelled in-review
    or its pull request marked ready, is left out, since the board counts
    no release of it. So is a draft whose author said it is finished
    (finished_draft), which the coordinator never releases, unless a person
    has released it, as the protocol lets anyone who may write release any
    claim: it is read only once its issue no longer carries claimed, and
    kept only when its release comment counts (board.release_counts), so
    the board counts the release and the coordinator, which finds it done,
    adds none. A claim whose issue is labelled in-review or is not open,
    or whose pull request is marked ready, costs no call; any other costs
    what dating its head costs (head_since: one call where the activity
    dates the push at once, up to three where it does not), and, when its
    last commit is more than a day old or its head is kept, one for its
    draft's comments, then, unless the draft is finished and its issue
    still carries claimed, one for its issue's comments and one per
    author whose permission must be asked, so the run pays only where
    a release can be due or can have happened. A draft from a fork, or one
    a stranger opened, is no claim's (is_claims), so what its author says
    holds no claim. asked is the run's answers to who may write
    (permissions), shared with mark_strangers. When the last commit came is
    when GitHub received the push of the head (head_since, F8), and
    every_head, each branch's head by name, says which heads a release
    already kept, a branch released/<issue>-<short sha> standing at the
    same commit (board.Release.kept), which is read whatever its time, so
    the board frees the name of a branch pushed again at such a head;
    refused gathers, for the run's log, why each claim whose head no read
    dated by its push was dated by its committer date, by the newest
    entry listed or by the run's start: GitHub's refusal, or an activity
    that lists no push of the head, or, in the run the branch's push
    started, only one from before the run began. now is when the run
    began, taken before it read the branches, so a push after that read is
    told from a stale entry (pushed_by); started_by is the branch whose
    push started the run, "" for none, whose head is dated at the run's
    start where no read dates its push, or a read dates it by an entry from
    before the run began (head_since)."""
    now = now or datetime.now(timezone.utc)
    ready, trusted = ready_branches(pulls), may_write(api, asked)
    drafts = {(p.get("head") or {}).get("ref"): p for p in pulls if p.get("draft") and is_claims(p)}
    every_head = every_head or {}
    found = {}
    for branch, sha in sorted(heads.items()):
        key = claims.key_of(branch)
        if not sha or branch in ready or not may_be_due(found_issues.get(key)):
            continue
        kept = every_head.get(claims.released_branch(key, sha)) == sha
        pushed, committed = head_since(api, branch, sha, refused, now, started_by)
        release = board.Release(sha=sha, committed_at=committed, pushed_at=pushed, kept=kept)
        if not kept and (not release.last_commit or not claims.is_idle(release.last_commit, now)):
            continue
        finished = finished_draft(api, drafts.get(branch))
        if finished and labels.CLAIMED in found_issues[key]["labels"]:
            continue
        release.released_at = claims.latest_release(api.get_all(f"/repos/{api.repo}/issues/{key}/comments"), trusted)
        if finished and not board.release_counts(release):
            continue
        found[key] = release.to_dict()
    return found


def collect(api, sha, default_branch, now=None, started_by=""):
    """Everything the board renders, in the shape a --dry-run fixture has,
    so a live run's answers can be saved and rendered again offline; now is
    the moment a claim's idleness is measured from, the wall clock unless
    given, taken before the branches are read, so a push after that read
    dates its claim (pushed_by); started_by is the branch whose push
    started the run, "" for none (releases)."""
    now = now or datetime.now(timezone.utc)
    repo = api.repo
    asked = getattr(api, "asked", None) or permissions(api)
    issues = [i for i in api.get_all(f"/repos/{repo}/issues", {"state": "open"}) if "pull_request" not in i]
    pulls = mark_strangers([pull_with_detail(api, p["number"], default_branch)
                            for p in api.get_all(f"/repos/{repo}/pulls", {"state": "open"})], asked)
    listed = api.get_all(f"/repos/{repo}/branches")
    branches = board.claim_branches(listed)
    found_issues = claim_issues(api, branches, issues)
    undated = {}
    found = {
        "sha": sha,
        "default_branch": default_branch,
        "issues": issues,
        "pulls": pulls,
        "branches": branches,
        "claims": {claims.key_of(b): claim_text(api, b) for b in branches},
        "claim_issues": found_issues,
        "merged": recent_merged(api),
        "releases": releases(api, claim_heads(listed), found_issues, pulls, now, asked, branch_heads(listed),
                             undated, started_by),
    }
    if undated:
        found["pushed_refused"] = undated
    found["began"], refused = began_times(api, found)
    if refused:
        found["began_refused"] = refused
    return found


def began_times(api, found):
    """When each piece of open work that meets other work in a file began,
    by its branch: the time GitHub writes for the newest push that made a
    branch of that name, read from the repository's activity, which GitHub
    records as the pushes reach it, so no clock of a worker's own comes in
    (F13); and GitHub's answer for each branch it refused to date. Only the
    work in the Overlaps is asked about, since the landing order is all the
    time is for, and a pull request from a fork never, since its branch is
    none of this repository's (board.open_work). One call reads the newest
    branch creations of the whole repository (newest_creations), and each
    branch it does not name is asked about on its own (branch_made_at), so
    a run pays one call where the work is new, as it is while it overlaps,
    and a refused first call costs only the reads it would have saved.
    When GitHub has no such push to give, or no activity at all (a 404),
    or refuses a branch's own read, whatever it answers, that branch is
    left out and the board orders its work as work whose beginning nothing
    says (board.landing_key), a pull request by when it was opened: the
    order is the one thing the time is for, and one branch GitHub will not
    answer for must not fail the board's whole run, as a comparison GitHub
    refuses does not (standing_of)."""
    wanted = list(dict.fromkeys(work.branch for works in board.overlaps(found).values()
                                for work in works if work.branch))
    if not wanted:
        return {}, {}
    try:
        times = newest_creations(api, wanted)
    except GitHubError:
        times = {}
    refused = {}
    for branch in wanted:
        if branch in times:
            continue
        try:
            began = branch_made_at(api, branch)
        except GitHubError as problem:
            refused[branch] = f"{problem}{github_words(problem)}"
            continue
        if began:
            times[branch] = began
    return times, refused


def newest_creations(api, branches):
    """When the newest push that made each of these branches came, for those
    the repository's newest ACTIVITY_PAGE branch creations name: one call,
    the activity filtered to branch creations and listed newest first, so
    the first entry for a branch is its newest creation. A refusal other
    than a 404 raises."""
    wanted = {f"refs/heads/{branch}": branch for branch in branches}
    listed = api.get(f"/repos/{api.repo}/activity", {"activity_type": "branch_creation", "direction": "desc",
                                                     "per_page": ACTIVITY_PAGE}, missing_ok=True) or []
    times = {}
    for activity in listed if isinstance(listed, list) else []:
        made = made_at(activity)
        branch = wanted.get(activity.get("ref")) if made and isinstance(activity.get("ref"), str) else None
        if branch and branch not in times:
            times[branch] = made
    return times


def branch_made_at(api, branch):
    """When the newest push that made the branch came, as GitHub writes the
    time, or "" when GitHub records none: the activity list, filtered to the
    branch and to branch creations and listed newest first. A refusal other
    than a 404 raises."""
    listed = api.get(f"/repos/{api.repo}/activity", {"ref": f"refs/heads/{branch}", "activity_type": "branch_creation",
                                                     "direction": "desc", "per_page": 1}, missing_ok=True) or []
    for activity in listed if isinstance(listed, list) else []:
        if made_at(activity) and activity.get("ref") == f"refs/heads/{branch}":
            return made_at(activity)
    return ""


def made_at(activity):
    """The time of an entry of the activity that records a push making a
    branch, "" for any other entry."""
    if not isinstance(activity, dict) or activity.get("activity_type") != "branch_creation":
        return ""
    return activity.get("timestamp") or ""


def began_refused_lines(snapshot):
    """A line for each branch of overlapping work GitHub refused to date in
    this run, for the workflow's log, since the page says nothing of why
    that work stands where it does: a token that may not read the
    repository's activity would otherwise leave every claim undated on
    every run unseen. A pull request from such a branch is then dated by
    its opening, and a claim with none yet is work nothing dates, which
    lands last (board.pull_began, board.UNDATED)."""
    snap = board.as_snapshot(snapshot)
    opened = {pull.branch for pull in snap.pulls if not pull.fork}
    own = {pull.branch for pull in snap.pulls if board.claim_of(pull) is not None}
    return [f"{branch}: GitHub refused to say when the branch was made ({answer}), so the Overlaps "
            f"{undated_words(branch, opened, own)}"
            for branch, answer in sorted(snap.began_refused.items())]


def undated_words(branch, opened, own):
    """What the Overlaps do with the work of a branch GitHub refused to
    date: date its pull request by its opening where the branch has one of
    its own, or is no claim's; and for a claim branch with none of its own,
    put the claim last, and date a stranger's pull request from it, where
    there is one, by its opening, since that is a piece of its own (F19;
    the fix's review)."""
    if branch in own or (branch in opened and not claims.is_claim_branch(branch)):
        return UNDATED_PULL
    return UNDATED_STRANGER if branch in opened else UNDATED_CLAIM


def snapshot(api, sha, default_branch, now=None, started_by=""):
    """The board's Snapshot of the repository as GitHub knows it now;
    started_by is the branch whose push started the run, "" for none
    (collect)."""
    return board.Snapshot.from_dict(collect(api, sha, default_branch, now, started_by))


def has_marker(api, number, marker):
    """Whether a comment on the issue or pull request carries the marker."""
    comments = api.get_all(f"/repos/{api.repo}/issues/{number}/comments")
    return any(marker in (c.get("body") or "") for c in comments)


def comment_once(api, number, marker, text):
    """Post the comment unless one on the pull request carries the marker."""
    if has_marker(api, number, marker):
        return False
    api.post(f"/repos/{api.repo}/issues/{number}/comments", {"body": marker + "\n" + text})
    return True


def post_comments(api, due):
    """Post each comment board.comments_due names, once each; the lines say
    which were posted and which failed, one each, for the workflow's log.
    Nothing here raises: a comment GitHub refuses, or whose pull request's
    comments it would not give, is a line, and the next comment is still
    tried. The board is written before the comments, and the workflow
    writes it to its branch only after a run that succeeded, so a refusal
    that escaped here would keep the board off its branch, and one GitHub
    gives for good would keep it off on every run. A refused comment is
    due again, idle or "main moved" alike, since both depend on the state
    and not on the run's event (board.comments_due), and a later run,
    which finds no marker yet, posts it."""
    lines = []
    for number, marker, text in due:
        kind = "idle" if marker == board.IDLE_MARKER else "main-moved"
        try:
            posted = comment_once(api, number, marker, text)
        except GitHubError as problem:
            lines.append(f"PR #{number}: {kind} comment failed: {problem}{github_words(problem)}")
            continue
        if posted:
            lines.append(f"PR #{number}: {kind} comment posted")
    return lines


# --- the coordinator ----------------------------------------------------------

def coordinate(api, snapshot, now):
    """What the coordinator does in the board's run, before the board is
    rendered: release every claim coordinator.releases_due names, then tell
    each piece of open work about other work it meets that it has not been
    told of. The snapshot is brought up to date with each release, so the
    board this run renders shows it and the branch is renamed in the same
    run. Nothing here raises: a call GitHub refuses is a line, and the next
    claim is still released. The lines say what was done, one each, for the
    workflow's log."""
    snap = board.as_snapshot(snapshot)
    lines = [release_one(api, snap, key, now) for key in coordinator.releases_due(snap, now)]
    return lines + tell_overlaps(api, snap)


def holding(api, default_branch, sha):
    """What a branch holds beside the default branch, as coordinator.Holding,
    from GitHub's comparison of the two; ahead is None when GitHub would not
    compare them."""
    try:
        found = api.get(f"/repos/{api.repo}/compare/{quoted(default_branch)}...{sha}", missing_ok=True)
    except GitHubError:
        found = None
    if not found:
        return coordinator.Holding()
    newest = (found.get("commits") or [{}])[-1]
    message = (newest.get("commit") or {}).get("message") or ""
    files = tuple(sorted(f["filename"] for f in found.get("files") or [] if f.get("filename")))
    return coordinator.Holding(found.get("ahead_by"), (message.splitlines() or [""])[0], files)


def release_one(api, snap, key, now):
    """Release one claim: the labels first, claimed and every worker's off
    and todo on where the issue carries none of todo, epic and blocker
    (coordinator.labels_put_on), then the comment, in the protocol's
    order, and the board counts a release only once both are done. A run
    that fails between them puts back the labels it took off and takes off
    the todo it put on, so the issue is as the run found it and the next
    run starts the release over, unless the comment turns out to be there
    after all, one GitHub wrote and lost its answer to, which makes the
    release whole. A run that cannot read the issue's comments to see
    whether it is there leaves the labels as the release left them and
    counts no release of its own (unsettled; F20), since labels put back
    beside a comment that stands would hold the claim for good: the
    comment tells every later coordinator the claim is released, and the
    label claimed tells the board it is held; the next run, reading the
    comments, settles it as it settles any claim whose labels are off,
    finding the release whole or releasing the claim again unless its
    holder pushed or a person put the label claimed back in the meantime,
    while on a head a release already kept, where a release comment that
    counts stands, whenever it came (board.release_counts), that earlier
    release frees the claim in the same run once the labels are off.
    Where GitHub refuses the putting back too, the labels are left off
    with no comment, as an unsettled release leaves them where GitHub
    wrote none, until a run releases the claim or a person puts them
    back. What was done, in a line."""
    release = snap.releases[key]
    names, put_on = coordinator.labels_released(snap, key), coordinator.labels_put_on(snap, key)
    taken_off = []
    try:
        for name in names:
            if take_label_off(api, key, name):
                taken_off.append(name)
    except GitHubError as problem:
        return (f"#{key}: not released, taking the labels off failed: {problem}{github_words(problem)}"
                + put_back(api, key, taken_off))
    refused = put_labels_on(api, key, put_on, taken_off)
    if refused:
        return refused
    text = coordinator.release_text(snap, key, holding(api, snap.default_branch, release.sha))
    lost = ""
    try:
        released_at = (api.post(f"/repos/{api.repo}/issues/{key}/comments", {"body": text}) or {}).get("created_at")
    except GitHubError as problem:
        try:
            released_at = landed_release(api, key, release)
        except GitHubError as unread:
            return unsettled(snap, key, names, put_on, problem, unread)
        if not released_at:
            return (f"#{key}: not released, the comment failed: {problem}{github_words(problem)}"
                    + put_back(api, key, taken_off, put_on))
        lost = f" (GitHub's answer to the comment was lost, {problem}, but the comment is there)"
    coordinator.after_release(snap, key, released_at or now.isoformat(), names, put_on)
    return f"#{key}: claim released, idle since {release.last_commit}{lost}"


def take_label_off(api, key, name):
    """Take one label off a claim's issue: True when it came off, False when
    GitHub answered 404, for an issue that no longer carried it, which is
    the label being off too, so a release goes on and puts back, if a later
    step fails, only what it took off."""
    try:
        api.delete(f"/repos/{api.repo}/issues/{key}/labels/{urllib.parse.quote(name, safe='')}")
    except GitHubError as problem:
        if problem.status == 404:
            return False
        raise
    return True


def put_labels_on(api, key, put_on, taken_off):
    """Put on a claim's issue the labels its release puts on
    (coordinator.labels_put_on): nothing when they went on, else the
    release's line saying it failed there, with the labels taken off put
    back and those put on taken off again, since GitHub may have written
    them and lost its answer."""
    if not put_on:
        return ""
    try:
        api.post(f"/repos/{api.repo}/issues/{key}/labels", {"labels": put_on})
    except GitHubError as problem:
        return (f"#{key}: not released, putting {', '.join(put_on)} on failed: {problem}{github_words(problem)}"
                + put_back(api, key, taken_off, put_on))
    return ""


def put_back(api, key, names, put_on=()):
    """Put the labels a release took off back on its issue once a later step
    failed, so the claim stays its holder's until a run releases it whole,
    then take off the labels it put on, so the issue is as the run found
    it; what was done, as the end of the release's line. A label put on
    whose answer was an error is taken off too, since GitHub may have
    written it and lost the answer; one that is not there counts as taken
    off, as in the release itself."""
    said = ""
    if names:
        listed = ", ".join(names)
        try:
            api.post(f"/repos/{api.repo}/issues/{key}/labels", {"labels": names})
            said += f"; {listed} put back"
        except GitHubError as problem:
            said += f"; putting {listed} back failed too, so they may still be off: {problem}{github_words(problem)}"
    for name in put_on:
        try:
            if take_label_off(api, key, name):
                said += f"; {name} taken off again"
        except GitHubError as problem:
            said += f"; taking {name} off again failed too, so it may still be on: {problem}{github_words(problem)}"
    return said


def unsettled(snap, key, names, put_on, problem, unread):
    """The line of a release whose comment failed and whose issue's
    comments could not be read to see whether GitHub wrote it anyway: the
    labels stay as the release left them, and the snapshot shows them so,
    with no release of this run's, since the run cannot tell whether one
    stands, so the page lists the claim as held and nothing is renamed
    this run; the next run, reading the comments, finds the comment and
    the release is whole, or finds none and releases the claim again,
    unless its holder pushed since or a person put the label claimed back
    beside the comment (F20; until its fix the labels went back beside a
    comment that might stand, which held the claim for good). A head a
    release already kept, where a release comment that counts stands, is
    the exception: that comment counts whenever it came
    (board.release_counts), so with the labels off the snapshot counts
    that earlier release, and the same run lists the claim as released
    and deletes the branch, whose work is kept, with no word of this
    release on the issue where GitHub wrote none (F20's review). So the
    line says what was done and nothing of what comes next."""
    coordinator.after_labels(snap, key, names, put_on)
    return (f"#{key}: release unsettled, the comment failed: {problem}{github_words(problem)}; its issue's "
            f"comments could not be read to see whether GitHub wrote it anyway: {unread}{github_words(unread)}; "
            f"the labels stay as the release left them")


def landed_release(api, key, release):
    """When the comment that releases this claim was made, by the rule the
    next run reads it with (claims.was_released), or "" when none stands:
    a call that GitHub answered with an error, a 502 or a timeout, may
    still have written the comment, and once it stands the release is done;
    only a comment newer than the latest the run held is this run's
    (newer). A read GitHub refuses raises GitHubError, since the run then
    cannot tell whether the comment stands (release_one)."""
    comments = api.get_all(f"/repos/{api.repo}/issues/{key}/comments")
    released_at = claims.latest_release(comments, may_write(api))
    if not released_at or not newer(released_at, release.released_at):
        return ""
    return released_at if board.release_counts(dataclasses.replace(release, released_at=released_at)) else ""


def newer(found, held):
    """Whether a release comment found after a failed post is newer than
    the latest the run held before it: one no newer is an earlier
    release's, which for a kept head counts whenever it came
    (board.release_counts), so it would pass for this run's comment while
    none was written."""
    return not held or claims.parse_time(found) > claims.parse_time(held)


def tell_overlaps(api, snap):
    """Post each overlap comment that is due, on the piece's identity, the
    issue for a claim's work and the pull request itself for one that is
    no claim's (board.identity), for the work that meets other work,
    counting only the markers of authors the repository trusts
    (coordinator.told_numbers) and only those that came no earlier than
    the claims they speak of (coordinator.heard_of). The markers on the
    identity are read first; a claim's work that they leave something to
    tell is read on its own open pull requests too
    (coordinator.told_places), where the coordinator told it before F2's
    fix, so the upgrade tells nothing again; the extra read is paid only
    where the issue's markers alone would have a comment go out, which for
    a claim told on its pull request before the fix, and never on its
    issue since, is every run until its overlap ends. Work whose comments
    GitHub would not give is told nothing this run, so it is never told
    twice. The lines say what was posted."""
    lines, told, trusted = [], {}, may_write(api)
    for number in coordinator.meeting(snap):
        markers = markers_on(api, number, trusted, lines, f"#{number}: overlaps not told, its comments were not read")
        if markers is not None:
            told[number] = markers
    for number, _, _ in coordinator.overlap_comments_due(snap, told):
        for place in coordinator.told_places(snap, number) if number in told else ():
            failed = f"#{number}: overlaps not told, the comments of its pull request #{place} were not read"
            markers = markers_on(api, place, trusted, lines, failed)
            if markers is None:
                told.pop(number)
                break
            told[number] = coordinator.merged_times(told[number], markers)
    for number, marker, text in coordinator.overlap_comments_due(snap, told):
        if number not in told:
            continue
        try:
            api.post(f"/repos/{api.repo}/issues/{number}/comments", {"body": marker + "\n" + text})
        except GitHubError as problem:
            lines.append(f"#{number}: overlap comment failed: {problem}{github_words(problem)}")
            continue
        lines.append(f"#{number}: overlap comment posted")
    return lines


def markers_on(api, number, trusted, lines, failed):
    """When the latest trusted overlap marker on an issue or a pull request
    named each number (coordinator.told_times), or None, with a line
    saying why, when GitHub would not give its comments."""
    try:
        return coordinator.told_times(api.get_all(f"/repos/{api.repo}/issues/{number}/comments"), trusted)
    except GitHubError as problem:
        lines.append(f"{failed}: {problem}{github_words(problem)}")
        return None


def branch_sha(api, branch):
    """The branch's commit, or "" when the repository has no such branch."""
    found = api.get(f"/repos/{api.repo}/git/ref/heads/{quoted(branch)}", missing_ok=True)
    return ((found or {}).get("object") or {}).get("sha") or ""


def release_claims(api, snapshot):
    """Rename the branch of every claim board.released_keys counts as
    released, so its name is free for the next claim, and say so once on
    its issue; the lines say what was done, one per claim, for the
    workflow's log. GitHub closes a pull request whose branch is renamed,
    and keeps the commits under the new name."""
    snap = board.as_snapshot(snapshot)
    return [release_claim(api, key, snap.releases[key])
            for key in sorted(board.released_keys(snap), key=claims.by_number)]


def release_claim(api, key, release):
    """Free one released claim's name and say so once on its issue. Nothing
    here raises: a call GitHub refuses (a rule on the branch, a server
    error) is said in the line and left for the next run, since the board
    is written before this runs, and a failure that stopped the run would
    stop every board after it, and the comments it still owes. A rename
    GitHub refused for good is also said once on the issue, with the
    rulesets in its way, since nobody reads the workflow's log."""
    old, new = claims.branch_of(key), claims.released_branch(key, release.sha)
    try:
        done = free_name(api, old, new, release.sha)
    except GitHubError as problem:
        return f"{old}: not renamed to {new}: {problem}{github_words(problem)}" + \
            explain_refusal(api, key, old, new, problem)
    if done is None:
        return f"{old}: no longer at {release.sha[:7]}, so it keeps its name"
    try:
        comment_once(api, key, board.RELEASED_MARKER.format(branch=new), board.released_text(old, new))
    except GitHubError as problem:
        return f"#{key}: {done}, but the comment on the issue failed: {problem}{github_words(problem)}"
    return f"#{key}: {done}"


def free_name(api, old, new, sha):
    """Rename the claim branch old to new, reading it again first: None,
    and nothing done, when it moved since the snapshot, since a commit
    after the release means its holder came back. When new already keeps
    the same commit, old was pushed again at the released head, and the
    rename is refused for good; deleting old then frees the name and loses
    nothing. What was done, in words."""
    if branch_sha(api, old) != sha:
        return None
    try:
        api.post(f"/repos/{api.repo}/branches/{urllib.parse.quote(old, safe='')}/rename", {"new_name": new})
        return f"claim released; {old} renamed to {new}"
    except GitHubError as problem:
        if problem.status != 422 or branch_sha(api, new) != sha:
            raise
    api.delete(f"/repos/{api.repo}/git/refs/heads/{quoted(old)}")
    return f"claim released; {new} already keeps {sha[:7]}, so {old} was deleted"


def explain_refusal(api, key, old, new, problem):
    """Say once on the issue why its released claim's name is not free,
    when GitHub refused the rename for good: a ruleset over the claim
    branches, or a token without the right. Any other answer, or none, is
    for the moment, and the next run tries again without a word. The
    rulesets are read only for the first saying, so a rename refused run
    after run costs one more call each time. What was said, for the log
    line, or ""."""
    if problem.status not in REFUSED_FOR_GOOD:
        return ""
    marker = board.RENAME_REFUSED_MARKER.format(branch=new)
    words = f"HTTP {problem.status}{github_words(problem)}"
    try:
        if has_marker(api, key, marker):
            return ""
        text = board.rename_refused_text(old, new, rulesets_on(api, (old, new)), words)
        api.post(f"/repos/{api.repo}/issues/{key}/comments", {"body": marker + "\n" + text})
    except GitHubError as failure:
        return f"; saying so on #{key} failed: {failure}{github_words(failure)}"
    return f"; said so on #{key}"


def rulesets_on(api, branches):
    """The rulesets whose rules apply to any of the branches, each once,
    with the types of the rules it applies, as board.Ruleset holds them;
    None when GitHub would not say, which the comment then says instead."""
    applied = {}
    try:
        for branch in branches:
            for rule in api.get_all(f"/repos/{api.repo}/rules/branches/{urllib.parse.quote(branch, safe='')}"):
                source = (rule.get("ruleset_source_type") or "", rule.get("ruleset_source") or "",
                          rule.get("ruleset_id"))
                applied.setdefault(source, set()).add(rule.get("type") or "unnamed")
    except GitHubError:
        return None
    order = sorted(applied.items(), key=lambda item: tuple(str(part) for part in item[0]))
    return [ruleset(api, source, types) for source, types in order]


def ruleset(api, source, types):
    """One ruleset as the comment names it: its own name where the token
    may read the ruleset, else its number, and the page that changes it."""
    source_type, owner, number = source
    try:
        found = api.get(f"/repos/{api.repo}/rulesets/{number}", missing_ok=True) or {}
    except GitHubError:
        found = {}
    return board.Ruleset(found.get("name") or f"ruleset {number}", ruleset_link(source_type, owner, number),
                         tuple(sorted(types)))


def ruleset_link(source_type, owner, number):
    """The settings page of a repository's or an organization's ruleset,
    or "" for a source the board cannot link, such as an enterprise's."""
    if source_type == "Repository" and owner:
        return f"{WEB}/{owner}/settings/rules/{number}"
    if source_type == "Organization" and owner:
        return f"{WEB}/organizations/{owner}/settings/rules/{number}"
    return ""


def github_words(problem):
    """GitHub's own words on a refused call, after a colon, or nothing."""
    return f": {problem.detail}" if problem.detail else ""


# --- the gate's world ---------------------------------------------------------

def standing_word(pull, behind=None):
    """How a pull request's branch stands to its base, in the gate's four
    words, from the pull's mergeable_state as the board reads it. Where
    GitHub's word says only that something else blocks the merge (a rule,
    a review, a draft), mergeable tells a conflict apart, and behind, the
    commits of the base the head lacks, tells behind from clean; with
    neither known the answer is unknown."""
    if pull.get("mergeable_state") not in OPAQUE_STATES:
        return board.pr_state(pull)
    if pull.get("mergeable") is False:
        return gate.CONFLICTING
    if pull.get("mergeable") is None or behind is None:
        return gate.UNKNOWN
    return gate.BEHIND if behind > 0 else gate.CLEAN


def check_word(runs, name):
    """The required check's state on a commit, in the gate's words: none
    when no run has its name, red when a run of it finished without
    passing, pending while one is still running, green when every run of
    it passed. Red wins over pending, since a failed run already means
    the branch needs work."""
    mine = [run for run in runs if run.get("name") == name]
    if not mine:
        return gate.NO_CHECK
    if any(run.get("status") == "completed" and run.get("conclusion") not in PASSING for run in mine):
        return gate.RED
    if any(run.get("status") != "completed" for run in mine):
        return gate.PENDING
    return gate.GREEN


class GitHubWorld:
    """gate.World over one repository on GitHub.

    The policy says which check is required, who merges and which branch
    is the default one, so the world reads the same branch the gate's
    answers name; default_branch overrides it for a caller that knows
    better. The clock is an argument so a test can fix it.
    """

    def __init__(self, api, policy, default_branch=None, clock=None):
        self.api = api
        self.policy = policy
        self.default_branch = default_branch or policy.default_branch
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def main_sha(self):
        return self.branch_sha(self.default_branch)

    def branch_sha(self, branch):
        """The branch's commit, or "" when the repository has no such branch."""
        return branch_sha(self.api, branch)

    def pull_of(self, branch):
        """The branch's open pull request, settled, or None."""
        found = self.api.pull_detail(branch)
        return settled(self.api, found) if found else None

    def behind_by(self, pull):
        """How many commits of the pull request's base its head lacks, from
        GitHub's comparison of the two; None when there is nothing to
        compare or GitHub has no such comparison (a 404). Any other refusal
        raises."""
        return behind_by(self.api, pull, self.default_branch)

    def standing(self, branch):
        """Unknown for a branch with no open pull request: GitHub computes
        mergeability only for one, and the gate asks only about those.
        Where GitHub's word is opaque the branch is read through mergeable
        and one comparison (standing_of), a comparison GitHub refuses
        reading as unknown, the same whether GitHub had computed the pull
        request's mergeability at the first read or only after the pause."""
        pull = self.pull_of(branch)
        if pull is None:
            return gate.UNKNOWN
        found = standing_of(self.api, pull)
        return found if found is not None else standing_word(pull)

    def standing_detail(self, branch):
        """The branch's gate.Standing, which never says a clean branch is
        level with the default branch: GitHub's word clean says only that
        it merges as it stands, behind or not under a rule that does not
        ask for an up-to-date branch (F16)."""
        return gate.Standing(self.standing(branch), level=False)

    def check(self, branch):
        sha = self.branch_sha(branch)
        return check_word(self.api.check_runs(sha), self.policy.required_check) if sha else gate.NO_CHECK

    def merge(self, branch):
        """Merge the branch's pull request through GitHub's merge API, with
        the head the gate looked at, so a push that lands meanwhile is
        refused rather than merged unseen. Only where the policy says the
        gate merges: elsewhere the owner or auto-merge does, and a merge
        here would go behind their back."""
        if self.policy.who_merges != gate.GATE:
            raise NotAllowed(f"{gate.MOVERS[self.policy.who_merges]} merges here, not the gate; "
                             f"{branch} waits for it")
        pull = self.api.pull_detail(branch)
        if pull is None:
            raise MergeRefused(f"{branch} has no open pull request to merge; open one first")
        try:
            done = self.api.put(f"/repos/{self.api.repo}/pulls/{pull['number']}/merge",
                                {"sha": pull["head"]["sha"], "merge_method": "merge"})
        except GitHubError as problem:
            raise self.refusal(branch, pull, problem) from None
        return MergeResult(done["sha"])

    def refusal(self, branch, pull, problem):
        """What a refused merge means for the gate. GitHub answers 405 for
        a pull request it will not merge and 409 when the head moved: main
        moved when the branch, read again, no longer fits the default
        branch, and otherwise the author has something to fix (a review, a
        rule, a push to look at), which is also the answer when GitHub will
        not compare the branch, since the standing is then unknown. Any
        other answer is the gate failing."""
        said = f": {problem.detail}" if problem.detail else ""
        if problem.status not in (405, 409):
            return GateFailed(f"GitHub did not merge #{pull['number']}: {problem}{said}")
        if self.standing(branch) in (gate.BEHIND, gate.CONFLICTING):
            return MainMoved(f"{branch} no longer merges cleanly into {self.default_branch}{said}")
        return MergeRefused(f"GitHub would not merge #{pull['number']}{said or ' as it stands'}")

    def now(self):
        return self.clock()
