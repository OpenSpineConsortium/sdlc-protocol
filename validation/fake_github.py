"""A fake of GitHub's REST API that answers from a RepoModel (DESIGN.md, 3.2).

FakeGitHub is an opener: the library's GitHub adapter takes it through the
opener argument it already has (adapters/github.py, GitHub.__init__), and
every call the adapter makes is answered from the model, a write changing
the model as GitHub would. It serves exactly the endpoints the adapter's
board run calls (the snapshot, the coordinator, the renames and the
comments); the clerk's and the gate's come with the steps that drive them
(DESIGN.md, 14). A call to any other endpoint raises
Unserved, which no part of the adapter catches, so a call the harness
does not model stops the run loudly rather than reading as a 404.

It is faithful to the transport under the adapter, where the prototype's
fake was not:
- The request line is ASCII. Before it answers, the fake encodes the
  method, the selector and the protocol as http.client does, which raises
  UnicodeEncodeError for any other character before anything is sent.
- List endpoints answer 100 items a page, with a Link header naming the
  next page.
- A pull request's mergeable is null on the first read of its detail in a
  run, and computed on the next, as GitHub computes it late.

Fault makes chosen calls fail: an HTTP status, GitHub's message and, for a
secondary rate limit, a retry-after header; with applied, the write is
made and its answer lost, as a 502 after GitHub wrote the comment.

What the fake assumes of GitHub, each settled by the documentation or by a
canary check (DESIGN.md, 3.2 and 11): the issues list holds the pull
requests; a draft's mergeable_state is "draft" (F9, canary C0); a rename
closes the pull request whose head the branch is, and so does a delete
(the web UI's page says so; canary C6 settles the REST delete); a rename onto a name
that stands answers 422; the board posts as github-actions[bot] with the
author_association NONE; a pull request's updated_at moves with pushes and
comments.
"""

import base64
import fnmatch
import io
import json
import urllib.error
import urllib.parse
from typing import NamedTuple

from .model import BOT, MAIN, REPO, Comment, iso

PER_PAGE = 100
# GitHub's contents API gives the text of a file of up to 1 MB and answers
# a bigger one with an empty content and the encoding "none".
CONTENTS_LIMIT = 1024 * 1024
BASE = f"/repos/{REPO}"


class Unserved(Exception):
    """A call the fake does not model."""


class Fault(NamedTuple):
    """Make the next times calls whose "METHOD path" (the path after
    /repos/OWNER/NAME, unquoted) matches "METHOD path", a glob in which *
    stands for any run of characters, fail with status. applied makes the
    write and loses its answer."""

    method: str
    path: str
    status: int = 500
    times: int = 1
    message: str = "injected"
    retry_after: str | None = None
    applied: bool = False


class Call(NamedTuple):
    """One call as the fake saw it: what was asked, the body, the status it
    answered, and whether a write it made took effect."""

    method: str
    path: str
    query: tuple
    body: object
    status: int
    applied: bool
    # Whether a Fault made the answer, as against a refusal the model gives.
    injected: bool = False


class Answer:
    """What urllib's opener returns: read(), headers, a context manager."""

    def __init__(self, body, headers=None):
        self.raw = b"" if body is None else json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def refusal(url, status, message="", headers=None):
    body = json.dumps({"message": message}).encode("utf-8") if message else b""
    return urllib.error.HTTPError(url, status, message or "refused", headers or {}, io.BytesIO(body))


class Refused(Exception):
    """A status the fake answers in place of a body."""

    def __init__(self, status):
        super().__init__(status)
        self.status = status


class FakeGitHub:
    def __init__(self, model, faults=()):
        self.model = model
        self.faults = [[fault, fault.times] for fault in faults]
        self.calls = []
        self.computed = set()

    # --- the opener ---------------------------------------------------------

    def open(self, request, timeout=None):
        method = request.get_method()
        f"{method} {request.selector} HTTP/1.1".encode("ascii")
        parts = urllib.parse.urlsplit(request.full_url)
        if not parts.path.startswith(BASE):
            raise Unserved(f"{method} {parts.path}")
        segments = [urllib.parse.unquote(s) for s in parts.path[len(BASE):].strip("/").split("/")]
        query = dict(urllib.parse.parse_qsl(parts.query))
        body = json.loads(request.data) if request.data else None
        path = "/" + "/".join(segments)
        fault = self.fault_for(f"{method} {path}")
        if fault is not None and not fault.applied:
            self.calls.append(Call(method, path, tuple(sorted(query.items())), body, fault.status, False, True))
            raise self.fault_answer(request.full_url, fault)
        try:
            found, headers = self.answer(method, segments, query, body, request.full_url)
        except Refused as refused:
            self.calls.append(Call(method, path, tuple(sorted(query.items())), body, refused.status, False))
            raise refusal(request.full_url, refused.status) from None
        self.calls.append(Call(method, path, tuple(sorted(query.items())), body,
                              fault.status if fault else 200, method != "GET", fault is not None))
        if fault is not None:
            raise self.fault_answer(request.full_url, fault)
        return Answer(found, headers)

    def fault_for(self, call):
        for entry in self.faults:
            fault, left = entry
            if left > 0 and fnmatch.fnmatchcase(call, f"{fault.method} {fault.path}"):
                entry[1] = left - 1
                return fault
        return None

    @staticmethod
    def fault_answer(url, fault):
        headers = {"retry-after": fault.retry_after} if fault.retry_after else {}
        return refusal(url, fault.status, fault.message, headers)

    # --- dispatch -----------------------------------------------------------

    def answer(self, method, s, query, body, url):
        """The body and the headers GitHub would answer, or Refused."""
        route = (method, s[0] if s else "")
        if route == ("GET", "issues") and len(s) == 1:
            return self.page(self.open_issue_list(), query, url)
        if s[:1] == ["issues"] and len(s) >= 2:
            return self.issue_route(method, s, query, body, url)
        if s[:1] == ["pulls"]:
            return self.pull_route(method, s, query, url)
        if route == ("GET", "branches") and len(s) == 1:
            listed = [{"name": name, "commit": {"sha": sha}} for name, sha in sorted(self.model.refs.items())]
            return self.page(listed, query, url)
        if route == ("POST", "branches") and s[-1] == "rename":
            return self.rename("/".join(s[1:-1]), body["new_name"]), {}
        if route == ("GET", "contents"):
            return self.contents("/".join(s[1:]), query.get("ref", MAIN)), {}
        if s[:1] == ["git"]:
            return self.git_route(method, s), {}
        if route == ("GET", "collaborators") and s[-1] == "permission":
            word = "write" if s[1] in self.model.writers else "read"
            return {"permission": word, "role_name": word}, {}
        if route == ("GET", "compare"):
            base, _, sha = s[1].partition("...")
            return self.compare(base, sha), {}
        if route == ("GET", "rules"):
            return self.page([], query, url)
        if route == ("GET", "rulesets"):
            raise Refused(404)
        if route == ("GET", "commits") and s[-1] == "check-runs":
            return {"total_count": 0, "check_runs": []}, {}
        raise Unserved(f"{method} /{'/'.join(s)}")

    def page(self, items, query, url):
        """One page of a list endpoint, with a Link header to the next."""
        number = int(query.get("page", "1"))
        size = int(query.get("per_page", PER_PAGE))
        chunk = items[(number - 1) * size:number * size]
        headers = {}
        if number * size < len(items):
            parts = urllib.parse.urlsplit(url)
            asked = dict(urllib.parse.parse_qsl(parts.query))
            asked["page"] = str(number + 1)
            headers["Link"] = f'<{parts._replace(query=urllib.parse.urlencode(asked)).geturl()}>; rel="next"'
        return chunk, headers

    # --- issues and comments ------------------------------------------------

    def open_issue_list(self):
        found = [self.issue_json(i) for i in self.model.issues.values() if i.state == "open"]
        found += [self.pull_as_issue(p) for p in self.model.pulls.values() if p.state == "open"]
        return sorted(found, key=lambda item: -item["number"])

    def issue_route(self, method, s, query, body, url):
        number = int(s[1]) if s[1].isdigit() and s[1].isascii() else None
        target = self.model.target(number) if number is not None else None
        if target is None:
            raise Refused(404)
        if len(s) == 2 and method == "GET":
            return (self.issue_json(target) if number in self.model.issues else self.pull_as_issue(target)), {}
        if len(s) == 2:
            raise Unserved(f"{method} /{'/'.join(s)}")
        if s[2] == "comments" and len(s) == 3:
            if method == "GET":
                return self.page([self.comment_json(number, i, c) for i, c in enumerate(target.comments)], query, url)
            if method == "POST":
                self.model.add_comment(number, Comment(BOT, "NONE", self.model.now, body["body"]))
                at = len(self.model.comments_on(number)) - 1
                return self.comment_json(number, at, self.model.comments_on(number)[at]), {}
        if s[2] == "labels" and number in self.model.issues:
            if method == "POST" and len(s) == 3:
                for name in body["labels"]:
                    self.model.add_label(number, BOT, name)
                return [{"name": n} for n in sorted(self.model.issues[number].labels)], {}
            if method == "DELETE" and len(s) == 4:
                if not self.model.remove_label(number, BOT, s[3]):
                    raise Refused(404)
                return [{"name": n} for n in sorted(self.model.issues[number].labels)], {}
        raise Unserved(f"{method} /{'/'.join(s)}")

    def issue_json(self, issue):
        return {"number": issue.number, "title": issue.title, "state": issue.state, "body": issue.body,
                "labels": [{"name": n} for n in sorted(issue.labels)], "updated_at": iso(0)}

    def pull_as_issue(self, pull):
        return {"number": pull.number, "title": pull.title, "state": "open" if pull.state == "open" else "closed",
                "body": pull.body, "labels": [], "pull_request": {"url": f"{BASE}/pulls/{pull.number}"},
                "updated_at": iso(pull.updated_at)}

    @staticmethod
    def comment_json(number, index, comment):
        return {"id": number * 1000 + index, "body": comment.body, "created_at": iso(comment.at),
                "user": {"login": comment.author}, "author_association": comment.association}

    # --- pull requests ------------------------------------------------------

    def pull_route(self, method, s, query, url):
        if method != "GET":
            raise Unserved(f"{method} /{'/'.join(s)}")
        pulls = self.model.pulls
        if len(s) == 1:
            state = query.get("state", "open")
            chosen = [p for p in pulls.values() if (p.state == "open") == (state == "open")]
            if "head" in query:
                chosen = [p for p in chosen if f"{REPO.split('/')[0]}:{p.head}" == query["head"]]
            if state == "closed":
                chosen.sort(key=lambda p: (-p.updated_at, -p.number))
                return self.page([self.closed_json(p) for p in chosen], query, url)
            chosen.sort(key=lambda p: -p.number)
            return self.page([{"number": p.number, "head": {"ref": p.head}} for p in chosen], query, url)
        number = int(s[1]) if s[1].isdigit() and s[1].isascii() else None
        pull = pulls.get(number)
        if pull is None:
            raise Refused(404)
        if len(s) == 2:
            return self.pull_json(pull), {}
        if s[2:] == ["files"]:
            return self.page([{"filename": f} for f in self.model.files_changed(pull.head)], query, url)
        raise Unserved(f"{method} /{'/'.join(s)}")

    def closed_json(self, pull):
        return {"number": pull.number, "title": pull.title, "head": {"ref": pull.head},
                "merged_at": iso(pull.merged_at) if pull.merged_at is not None else None,
                "updated_at": iso(pull.updated_at)}

    def pull_json(self, pull):
        """The detail of one pull request; mergeable is null on its first
        read in a run for each head and main it stands on."""
        model = self.model
        sha = model.refs.get(pull.head, "") if pull.state == "open" else ""
        standing = model.standing(pull.head) if sha else "clean"
        seen = (pull.number, sha, model.refs[MAIN])
        if seen in self.computed:
            mergeable, state = standing != "dirty", "draft" if pull.draft else standing
        else:
            self.computed.add(seen)
            mergeable, state = None, "unknown"
        repo = {"full_name": REPO}
        return {"number": pull.number, "title": pull.title, "body": pull.body, "draft": pull.draft,
                "state": "open" if pull.state == "open" else "closed", "user": {"login": pull.author},
                "head": {"ref": pull.head, "sha": sha, "repo": repo}, "base": {"ref": MAIN, "repo": repo},
                "mergeable": mergeable, "mergeable_state": state, "updated_at": iso(pull.updated_at),
                "merged_at": iso(pull.merged_at) if pull.merged_at is not None else None}

    # --- branches, contents and commits -------------------------------------

    def rename(self, old, new):
        if old not in self.model.refs:
            raise Refused(404)
        if new in self.model.refs:
            raise Refused(422)
        self.model.rename_ref(old, new)
        return {"name": new}

    def contents(self, path, ref):
        sha = self.model.refs.get(ref)
        text = self.model.tree(sha).get(path) if sha else None
        if text is None:
            raise Refused(404)
        data = text.encode("utf-8")
        if len(data) > CONTENTS_LIMIT:
            return {"type": "file", "path": path, "encoding": "none", "content": ""}
        return {"type": "file", "path": path, "encoding": "base64", "content": base64.b64encode(data).decode("ascii")}

    def git_route(self, method, s):
        model = self.model
        if method == "GET" and s[1] == "commits":
            commit = model.commits.get(s[2])
            if commit is None:
                raise Refused(404)
            return {"sha": s[2], "committer": {"date": iso(commit.committed_at)}}
        if method == "GET" and s[1:3] == ["ref", "heads"]:
            name = "/".join(s[3:])
            if name not in model.refs:
                raise Refused(404)
            return {"ref": f"refs/heads/{name}", "object": {"sha": model.refs[name]}}
        if method == "DELETE" and s[1:3] == ["refs", "heads"]:
            name = "/".join(s[3:])
            if name not in model.refs:
                raise Refused(422)
            model.delete_ref(name)
            return None
        raise Unserved(f"{method} /{'/'.join(s)}")

    def compare(self, base, sha):
        model = self.model
        base_sha = model.refs.get(base)
        if base_sha is None or sha not in model.commits:
            raise Refused(404)
        ahead = model.ancestors(sha) - model.ancestors(base_sha)
        order = model.in_order(ahead)
        return {"ahead_by": len(ahead), "commits": [{"sha": c, "commit": {"message": model.commits[c].message}}
                                                   for c in order],
                "files": [{"filename": f} for f in model.changed(model.merge_base(sha, base_sha), sha)]}


def pull_detail(model, pull):
    """A pull request's settled detail, as the protocol check reads one."""
    fake = FakeGitHub(model)
    fake.pull_json(pull)
    return fake.pull_json(pull)

