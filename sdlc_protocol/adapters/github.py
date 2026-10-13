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
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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
# GitHub computes mergeability after the first ask; one more ask after a
# pause is enough, and "unknown" is the honest word when it still has not.
MERGEABLE_RETRY_SECONDS = 3
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
        """The open pull request from a branch of this repository, in the
        detail that holds mergeable_state and the head's sha, or None."""
        owner = self.repo.split("/")[0]
        found = self.get(f"/repos/{self.repo}/pulls", {"state": "open", "head": f"{owner}:{branch}"}) or []
        if not found:
            return None
        return self.get(f"/repos/{self.repo}/pulls/{found[0]['number']}")

    def check_runs(self, sha):
        """Every check run on one commit, page by page: the endpoint wraps
        each page's runs in an object, and a busy commit has more than one
        page of them."""
        runs = []
        for page in self.pages(f"/repos/{self.repo}/commits/{sha}/check-runs"):
            if isinstance(page, dict):
                runs.extend(page.get("check_runs") or [])
        return runs

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


def pull_with_detail(api, number):
    """One pull request with its mergeable_state settled and its file paths."""
    path = f"/repos/{api.repo}/pulls/{number}"
    detail = settled(api, api.get(path))
    detail["files"] = [f["filename"] for f in api.get_all(path + "/files")]
    return detail


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
    return {branch["name"]: (branch.get("commit") or {}).get("sha") or ""
            for branch in listed if isinstance(branch, dict) and claims.is_claim_branch(branch.get("name") or "")}


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
    ready, as the board counts it."""
    return {(p.get("head") or {}).get("ref") for p in pulls if not p.get("draft")}


def may_write(api):
    """A callable saying whether a login may write to the repository, from
    GitHub's collaborator permission, asked once per login. It stands in
    for author_association where that word does not vouch for an author:
    GitHub may show a private member of the organization to a workflow's
    token as a contributor. A call GitHub refuses counts as no, so a
    release by an author nobody can vouch for frees nothing."""
    answers = {}

    def ask(login):
        if login not in answers:
            try:
                found = api.get(f"/repos/{api.repo}/collaborators/{urllib.parse.quote(login, safe='')}/permission",
                                missing_ok=True) or {}
            except GitHubError:
                found = {}
            answers[login] = found.get("permission") in WRITE_PERMISSIONS or found.get("role_name") in WRITE_ROLES
        return answers[login]
    return ask


def committed_at(api, sha):
    """When the commit was committed, as GitHub writes it, or "" when GitHub
    does not know the commit."""
    commit = api.get(f"/repos/{api.repo}/git/commits/{sha}", missing_ok=True) or {}
    return (commit.get("committer") or {}).get("date") or ""


def finished_draft(api, pull):
    """Whether a draft's author said it is finished (claims.said_finished),
    from the pull request's own comments; None, a pull request, costs no
    call."""
    if not pull:
        return False
    author = (pull.get("user") or {}).get("login")
    return claims.said_finished(api.get_all(f"/repos/{api.repo}/issues/{pull['number']}/comments"), author)


def releases(api, heads, found_issues, pulls, now=None):
    """What the release rule reads about each claim that has gone more than
    a day without a commit, by key: the branch's head commit with the time
    it was committed, and the time of the latest release comment on its
    issue, "" when there is none. The coordinator releases such a claim
    unless a comment already did (coordinator.releases_due), and the board
    counts it released once the label claimed is off too
    (board.released_keys). A finished claim, its issue labelled in-review
    or its pull request marked ready, is left out, since the board counts
    no release of it. So is a draft whose author said it is finished
    (finished_draft), which the coordinator never releases, unless a person
    has released it, as the protocol lets anyone who may write release any
    claim: it is read only once its issue no longer carries claimed, and
    kept only when its release comment counts (claims.was_released), so
    the board counts the release and the coordinator, which finds it done,
    adds none. A claim whose issue is labelled in-review or is not open,
    or whose pull request is marked ready, costs no call; any other costs
    one for its head commit, and, when that commit is more than a day old,
    one for its draft's comments, then, unless the draft is finished and
    its issue still carries claimed, one for its issue's comments and one
    per author whose permission must be asked, so the run pays only where
    a release can be due or can have happened."""
    now = now or datetime.now(timezone.utc)
    ready, trusted = ready_branches(pulls), may_write(api)
    drafts = {(p.get("head") or {}).get("ref"): p for p in pulls if p.get("draft")}
    found = {}
    for branch, sha in sorted(heads.items()):
        key = claims.key_of(branch)
        if not sha or branch in ready or not may_be_due(found_issues.get(key)):
            continue
        last_commit = committed_at(api, sha)
        if not last_commit or not claims.is_idle(last_commit, now):
            continue
        finished = finished_draft(api, drafts.get(branch))
        if finished and labels.CLAIMED in found_issues[key]["labels"]:
            continue
        released_at = claims.latest_release(api.get_all(f"/repos/{api.repo}/issues/{key}/comments"), trusted)
        if finished and not claims.was_released(released_at, last_commit):
            continue
        found[key] = {"released_at": released_at, "sha": sha, "committed_at": last_commit}
    return found


def collect(api, sha, default_branch, now=None):
    """Everything the board renders, in the shape a --dry-run fixture has,
    so a live run's answers can be saved and rendered again offline; now is
    the moment a claim's idleness is measured from, the wall clock unless
    given."""
    repo = api.repo
    issues = [i for i in api.get_all(f"/repos/{repo}/issues", {"state": "open"}) if "pull_request" not in i]
    pulls = [pull_with_detail(api, p["number"]) for p in api.get_all(f"/repos/{repo}/pulls", {"state": "open"})]
    listed = api.get_all(f"/repos/{repo}/branches")
    branches = board.claim_branches(listed)
    found_issues = claim_issues(api, branches, issues)
    return {
        "sha": sha,
        "default_branch": default_branch,
        "issues": issues,
        "pulls": pulls,
        "branches": branches,
        "claims": {claims.key_of(b): claim_text(api, b) for b in branches},
        "claim_issues": found_issues,
        "merged": recent_merged(api),
        "releases": releases(api, claim_heads(listed), found_issues, pulls, now),
    }


def snapshot(api, sha, default_branch, now=None):
    """The board's Snapshot of the repository as GitHub knows it now."""
    return board.Snapshot.from_dict(collect(api, sha, default_branch, now))


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
    gives for good would keep it off on every run. A refused idle comment
    is due again, and a later run, which finds no marker yet, posts it; a
    "main moved" comment is due only in the run of the push that moved the
    default branch, so a refused one is never posted, and the pull request
    hears that main moved only when the default branch moves again."""
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
    """Release one claim: the labels first, then the comment, in the
    protocol's order, and the board counts a release only once both are
    done. A run that fails between them puts back the labels it took off,
    so the issue is as the run found it and the next run starts the
    release over, unless the comment turns out to be there after all, one
    GitHub wrote and lost its answer to, which makes the release whole.
    Only when GitHub refuses the putting back too are the labels left off
    with no comment, until a run releases the claim or a person puts them
    back. What was done, in a line."""
    release, names, taken_off = snap.releases[key], coordinator.labels_released(snap, key), []
    try:
        for name in names:
            if take_label_off(api, key, name):
                taken_off.append(name)
    except GitHubError as problem:
        return (f"#{key}: not released, taking the labels off failed: {problem}{github_words(problem)}"
                + put_back(api, key, taken_off))
    text = coordinator.release_text(snap, key, holding(api, snap.default_branch, release.sha))
    lost = ""
    try:
        released_at = (api.post(f"/repos/{api.repo}/issues/{key}/comments", {"body": text}) or {}).get("created_at")
    except GitHubError as problem:
        released_at = landed_release(api, key, release)
        if not released_at:
            return (f"#{key}: not released, the comment failed: {problem}{github_words(problem)}"
                    + put_back(api, key, taken_off))
        lost = f" (GitHub's answer to the comment was lost, {problem}, but the comment is there)"
    coordinator.after_release(snap, key, released_at or now.isoformat(), names)
    return f"#{key}: claim released, idle since {release.committed_at}{lost}"


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


def put_back(api, key, names):
    """Put the labels a release took off back on its issue once a later step
    failed, so the claim stays its holder's until a run releases it whole;
    what was done, as the end of the release's line."""
    if not names:
        return ""
    listed = ", ".join(names)
    try:
        api.post(f"/repos/{api.repo}/issues/{key}/labels", {"labels": names})
    except GitHubError as problem:
        return f"; putting {listed} back failed too, so they stay off: {problem}{github_words(problem)}"
    return f"; {listed} put back"


def landed_release(api, key, release):
    """When the comment that releases this claim was made, by the rule the
    next run reads it with (claims.was_released), or "" when none stands:
    a call that GitHub answered with an error, a 502 or a timeout, may
    still have written the comment, and once it stands the release is done.
    A read GitHub refuses finds none."""
    try:
        comments = api.get_all(f"/repos/{api.repo}/issues/{key}/comments")
    except GitHubError:
        return ""
    released_at = claims.latest_release(comments, may_write(api))
    return released_at if claims.was_released(released_at, release.committed_at) else ""


def tell_overlaps(api, snap):
    """Post each overlap comment that is due, reading only the comments of
    the work that meets other work, and counting only the markers of
    authors the repository trusts (coordinator.told_numbers); work whose
    comments GitHub would not give is told nothing this run, so it is never
    told twice. The lines say what was posted."""
    lines, told, trusted = [], {}, may_write(api)
    for number in coordinator.meeting(snap):
        try:
            comments = api.get_all(f"/repos/{api.repo}/issues/{number}/comments")
            told[number] = coordinator.told_numbers(comments, trusted)
        except GitHubError as problem:
            lines.append(f"#{number}: overlaps not told, its comments were not read: {problem}{github_words(problem)}")
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

def standing_word(pull, behind_by=None):
    """How a pull request's branch stands to its base, in the gate's four
    words, from the pull's mergeable_state as the board reads it. Where
    GitHub's word says only that something else blocks the merge (a rule,
    a review, a draft), mergeable tells a conflict apart, and behind_by,
    the commits of the base the head lacks, tells behind from clean; with
    neither known the answer is unknown."""
    if pull.get("mergeable_state") not in OPAQUE_STATES:
        return board.pr_state(pull)
    if pull.get("mergeable") is False:
        return gate.CONFLICTING
    if pull.get("mergeable") is None or behind_by is None:
        return gate.UNKNOWN
    return gate.BEHIND if behind_by > 0 else gate.CLEAN


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
        found = self.api.open_pull(branch)
        return settled(self.api, found) if found else None

    def behind_by(self, pull):
        """How many commits of the pull request's base its head lacks, from
        GitHub's comparison of the two; None when GitHub cannot say."""
        base = (pull.get("base") or {}).get("ref") or self.default_branch
        head = (pull.get("head") or {}).get("sha")
        if not head:
            return None
        found = self.api.get(f"/repos/{self.api.repo}/compare/{quoted(base)}...{head}", missing_ok=True)
        return (found or {}).get("behind_by")

    def standing(self, branch):
        """Unknown for a branch with no open pull request: GitHub computes
        mergeability only for one, and the gate asks only about those."""
        pull = self.pull_of(branch)
        if pull is None:
            return gate.UNKNOWN
        if pull.get("mergeable_state") in OPAQUE_STATES and pull.get("mergeable"):
            return standing_word(pull, self.behind_by(pull))
        return standing_word(pull)

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
        pull = self.api.open_pull(branch)
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
        rule, a push to look at). Any other answer is the gate failing."""
        said = f": {problem.detail}" if problem.detail else ""
        if problem.status not in (405, 409):
            return GateFailed(f"GitHub did not merge #{pull['number']}: {problem}{said}")
        if self.standing(branch) in (gate.BEHIND, gate.CONFLICTING):
            return MainMoved(f"{branch} no longer merges cleanly into {self.default_branch}{said}")
        return MergeRefused(f"GitHub would not merge #{pull['number']}{said or ' as it stands'}")

    def now(self):
        return self.clock()
