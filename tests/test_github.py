"""The GitHub adapter makes the same REST calls the template's board and
clerk made, and reads them into the board's Snapshot, the clerk's Client
and the gate's World. No network: a fake opener stands in for urllib,
writes each request line as http.client does, answers by method, path and
query, and remembers every request."""

import base64
import http.client
import io
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import board, claims, cli, coordinator, gate, team  # noqa: E402
from sdlc_protocol.adapters import github  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


class FakeAnswer:
    def __init__(self, body, headers=None):
        self.body = json.dumps(body).encode()
        self.headers = headers or {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class CutAnswer(FakeAnswer):
    """An answer whose connection closed before its body was whole, which
    http.client tells by raising IncompleteRead when it is read."""

    def read(self):
        raise http.client.IncompleteRead(b'{"id"', 96)


class RawAnswer(FakeAnswer):
    """An answer whose body is these bytes, JSON or not."""

    def __init__(self, raw, headers=None):
        super().__init__(None, headers)
        self.body = raw


class Refusal:
    """An HTTP error GitHub would answer, with its JSON body."""

    def __init__(self, code, body=None):
        self.code, self.body = code, body

    def raised(self, url):
        raw = json.dumps(self.body).encode() if self.body is not None else b""
        return urllib.error.HTTPError(url, self.code, "refused", {}, io.BytesIO(raw))


class CutBody(io.BytesIO):
    """A refusal's body whose connection closed before it was whole."""

    def read(self, *_):
        raise http.client.IncompleteRead(b'{"message"', 90)


class CutRefusal(Refusal):
    """An HTTP error whose body was cut short, which http.client tells by
    raising IncompleteRead when the body is read for GitHub's words."""

    def raised(self, url):
        return urllib.error.HTTPError(url, self.code, "refused", {}, CutBody())


def send_line(call):
    """Write a request's line and headers as http.client writes them before
    it connects, which refuses a path it cannot send as ASCII
    (UnicodeEncodeError), or one holding a space or a control character
    (InvalidURL), and a header value outside Latin-1 (UnicodeEncodeError)
    or holding a bare line break (ValueError), so a fake fails where a live
    run fails."""
    connection = http.client.HTTPConnection("api.github.com")
    connection.putrequest(call.get_method(), call.selector)
    for name, value in call.header_items():
        connection.putheader(name, value)


class FakeOpener:
    """Answers by path and query for GET and by "METHOD path" otherwise, 404
    for anything it was not given, and remembers every request. It sends
    what a live run's urllib sends: the request line as http.client writes
    it (send_line), answered by its path and query, the selector, which
    leaves out whatever follows a # in the URL, so a path built from a name
    a person writes and left unquoted fails here as it fails a live run."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def open(self, call, timeout=None):
        send_line(call)
        self.calls.append(call)
        path = call.selector
        method = call.get_method()
        answer = self.answers.get(path if method == "GET" else f"{method} {path}")
        if answer is None:
            raise urllib.error.HTTPError(call.full_url, 404, "Not Found", {}, io.BytesIO(b""))
        if isinstance(answer, Refusal):
            raise answer.raised(call.full_url)
        return answer

    def sent(self, method):
        """The decoded bodies of the requests made with this method."""
        return [json.loads(c.data) for c in self.calls if c.get_method() == method]


class Turns(list):
    """The answers to one path, given in turn, the last for every ask after
    it, as GitHub answers a pull request's detail before and after it has
    computed the pull request's mergeability."""

    def __init__(self, *answers):
        super().__init__(answers)


class InTurn(FakeOpener):
    """A FakeOpener that answers a path given Turns with them in turn, keyed
    as FakeOpener keys its answers: by path and query for GET and by
    "METHOD path" otherwise."""

    def open(self, call, timeout=None):
        method = call.get_method()
        key = call.selector if method == "GET" else f"{method} {call.selector}"
        turns = self.answers.get(key)
        if not isinstance(turns, Turns):
            return super().open(call, timeout)
        self.answers = dict(self.answers, **{key: turns[0]})
        try:
            return super().open(call, timeout)
        finally:
            self.answers[key] = Turns(*turns[1:]) if len(turns) > 1 else turns


def too_large_to_give(path, ref):
    """What GitHub answers through the contents API's default media type
    for a file it gives no text of, as it answered on 2026-10-13 for a
    file of 1,361,824 bytes (application/vnd.github+json, API version
    2022-11-28): 200, the file's metadata, an empty content and the
    encoding "none". Its REST description documents that shape for a file
    between 1 and 100 MB under the object media type, and GitHub takes no
    push of a file over 100 MiB."""
    name = path.rsplit("/", 1)[-1]
    return FakeAnswer({"name": name, "path": path, "sha": "04e7f66b309c6c2bf217a521399b7e7ed6a22ab0",
                       "size": 1361824, "url": f"{github.API}/repos/o/r/contents/{path}?ref={ref}",
                       "type": "file", "content": "", "encoding": "none"})


def client(answers, waits=None):
    opener = FakeOpener(answers)
    sleep = waits.append if waits is not None else (lambda seconds: None)
    return github.GitHub("t", "o/r", opener=opener, sleep=sleep), opener


def closed_pull(number, updated_at, merged_at):
    return {"number": number, "title": f"PR {number}", "head": {"ref": f"claude/{number}"}, "updated_at": updated_at, "merged_at": merged_at}


CLOSED_FIRST = "/repos/o/r/pulls?state=closed&sort=updated&direction=desc&per_page=100"
CLOSED_SECOND = CLOSED_FIRST + "&page=2"


class Client(unittest.TestCase):
    """The template's client tests, against the adapter."""

    def test_pages_by_the_link_header_and_sends_the_headers(self):
        first = "/repos/o/r/issues?state=open&per_page=100"
        second = "/repos/o/r/issues?state=open&per_page=100&page=2"
        opener = FakeOpener({
            first: FakeAnswer([{"number": 1}], {"Link": f'<{github.API}{second}>; rel="next"'}),
            second: FakeAnswer([{"number": 2}]),
        })
        api = github.GitHub("secret-token", "o/r", opener=opener, sleep=lambda s: None)
        items = api.get_all("/repos/o/r/issues", {"state": "open"})
        self.assertEqual([i["number"] for i in items], [1, 2])
        self.assertEqual(len(opener.calls), 2)
        sent = opener.calls[0]
        self.assertEqual(sent.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(sent.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(sent.get_header("X-github-api-version"), "2022-11-28")
        self.assertEqual(sent.get_header("User-agent"), "sdlc-board")

    def test_a_missing_claim_file_is_none_and_an_error_never_names_the_token(self):
        api = github.GitHub("secret-token", "o/r", opener=FakeOpener({}), sleep=lambda s: None)
        self.assertIsNone(github.claim_text(api, "claude/12"))
        with self.assertRaises(github.GitHubError) as caught:
            api.get("/repos/o/r/pulls/1")
        self.assertEqual(str(caught.exception), "GET /repos/o/r/pulls/1: HTTP 404")
        self.assertNotIn("secret-token", str(caught.exception))

    def test_mergeable_is_asked_once_more_then_unknown(self):
        detail = "/repos/o/r/pulls/7"
        waits = []
        api, _ = client({
            detail: FakeAnswer({"number": 7, "mergeable": None, "mergeable_state": "unknown", "head": {"ref": "claude/7"}}),
            detail + "/files?per_page=100": FakeAnswer([{"filename": "a.md"}]),
        }, waits)
        pull = github.pull_with_detail(api, 7)
        self.assertEqual(waits, [github.MERGEABLE_RETRY_SECONDS])
        self.assertEqual(pull["mergeable_state"], "unknown")
        self.assertEqual(pull["files"], ["a.md"])

    def test_comment_once_posts_only_without_the_marker(self):
        comments = "/repos/o/r/issues/40/comments?per_page=100"
        api, opener = client({comments: FakeAnswer([{"body": board.IDLE_MARKER + "\nidle"}])})
        self.assertFalse(github.comment_once(api, 40, board.IDLE_MARKER, board.IDLE_TEXT))
        self.assertEqual(len(opener.calls), 1)

    def test_recent_merged_takes_the_five_merged_last_from_the_first_page(self):
        page = [closed_pull(n, "2026-09-22T10:00:00Z", f"2026-09-{n:02d}T10:00:00Z") for n in range(1, 9)]
        page.append(closed_pull(9, "2026-09-23T10:00:00Z", None))
        api, opener = client({
            CLOSED_FIRST: FakeAnswer(page, {"Link": f'<{github.API}{CLOSED_SECOND}>; rel="next"'}),
        })
        merged = github.recent_merged(api)
        self.assertEqual([p["number"] for p in merged], [8, 7, 6, 5, 4])
        self.assertEqual(len(opener.calls), 1)

    def test_recent_merged_is_the_same_list_each_time_it_is_asked(self):
        # A merge fires two runs; the list depends on the repository alone, so
        # the second run reports the same merge the first did.
        page = [closed_pull(50, "2026-09-22T10:00:00Z", "2026-09-22T10:00:00Z")]
        api, _ = client({CLOSED_FIRST: FakeAnswer(page)})
        self.assertEqual(github.recent_merged(api), github.recent_merged(api))
        self.assertEqual([p["number"] for p in github.recent_merged(api)], [50])

    def test_recent_merged_with_no_closed_pull_requests_is_empty(self):
        api, _ = client({CLOSED_FIRST: FakeAnswer([])})
        self.assertEqual(github.recent_merged(api), [])

    def test_claim_issues_reads_only_the_issues_the_open_list_lacks(self):
        api, opener = client({
            "/repos/o/r/issues/3": FakeAnswer({"number": 3, "state": "closed", "labels": [{"name": "account:main"}, {"name": "todo"}]}),
        })
        open_issues = [{"number": 14, "state": "open", "labels": [{"name": "todo"}]}]
        found = github.claim_issues(api, ["main", "claude/3", "claude/14", "claude/x"], open_issues)
        self.assertEqual(found, {
            "3": {"state": "closed", "labels": ["account:main", "todo"]},
            "14": {"state": "open", "labels": ["todo"]},
        })
        self.assertEqual(len(opener.calls), 1)

    def test_a_branch_whose_key_is_no_ascii_number_costs_no_issue_call_and_stops_no_run(self):
        # http.client refuses a path it cannot send as ASCII, and the client
        # reads that as an answer that was not JSON: one branch named with
        # a superscript stopped every board run, the coordinator's with it,
        # before STATE.md was written.
        snap, answers = fixture_answers()
        branches = snap["branches"] + ["claude/\u00b2", "claude/\u27800"]
        answers["/repos/o/r/branches?per_page=100"] = FakeAnswer([{"name": b} for b in branches])
        opener = FakeOpener(answers)
        api = github.GitHub("t", "o/r", opener=opener, sleep=lambda seconds: None)
        page = board.render(github.snapshot(api, snap["sha"], snap["default_branch"]))
        self.assertIn("| #12 | claude/12 | main | #40 |", page)
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertIn("/repos/o/r/contents/docs/agents/claims/%C2%B2.md?ref=claude%2F%C2%B2", asked)
        self.assertFalse(any(path.startswith("/repos/o/r/issues/%") for path in asked), asked)

    def test_a_claim_key_with_a_hash_asks_for_its_own_file_and_not_the_folder(self):
        # Unquoted, the # began the URL's fragment, so the request asked for
        # the claims folder on the default branch, GitHub answered with its
        # listing, and reading that list as a file raised in every run.
        snap, answers = fixture_answers()
        answers["/repos/o/r/branches?per_page=100"] = FakeAnswer([{"name": b} for b in snap["branches"] + ["claude/#3"]])
        answers["/repos/o/r/contents/docs/agents/claims/"] = FakeAnswer([{"name": "12.md", "type": "file"}])
        opener = FakeOpener(answers)
        api = github.GitHub("t", "o/r", opener=opener, sleep=lambda seconds: None)
        self.assertIsNone(github.collect(api, snap["sha"], snap["default_branch"])["claims"]["#3"])
        asked = [c.selector for c in opener.calls]
        self.assertIn("/repos/o/r/contents/docs/agents/claims/%233.md?ref=claude%2F%233", asked)

    def test_a_folder_a_submodule_or_a_link_at_the_claim_path_is_no_claim_file_and_stops_no_run(self):
        # GitHub answers a folder with a list, which raised AttributeError
        # in every board run while the branch stood; git pushes a branch
        # whose docs/agents/claims/13.md is a folder.
        snap, answers = fixture_answers()
        path = "/repos/o/r/contents/docs/agents/claims/13.md?ref=claude%2F13"
        for found in ([{"name": "notes.txt", "path": "docs/agents/claims/13.md/notes.txt", "type": "file"}],
                      {"type": "submodule", "name": "13.md"}, {"type": "symlink", "target": "nowhere"}):
            answers[path] = FakeAnswer(found)
            api = github.GitHub("t", "o/r", opener=FakeOpener(answers), sleep=lambda seconds: None)
            collected = github.collect(api, snap["sha"], snap["default_branch"])
            self.assertIsNone(collected["claims"]["13"], found)
            self.assertIn("| #13 | claude/13 | no claim file yet |", board.render(board.Snapshot.from_dict(collected)))

    def test_a_claim_file_github_gives_no_text_of_is_read_as_not_read_and_said_so(self):
        # It was read as an empty claim file, so the board said "no claim
        # file yet" of a file that is there, and a release would have said
        # that the claim states no plan.
        snap, answers = fixture_answers()
        answers["/repos/o/r/contents/docs/agents/claims/13.md?ref=claude%2F13"] = too_large_to_give(
            "docs/agents/claims/13.md", "claude/13")
        api, _ = client(answers)
        collected = github.collect(api, snap["sha"], snap["default_branch"])
        self.assertIs(collected["claims"]["13"], board.UNREAD_CLAIM)
        live = board.Snapshot.from_dict(collected)
        self.assertIn("| #13 | claude/13 | claim file over 1 MB, not read | #41 |", board.render(live))
        self.assertEqual(github.unread_claim_lines(live), [
            "claude/13: GitHub gave no text for docs/agents/claims/13.md, a file over 1 MB, so the board reads "
            "no worker and no files from it, and a release quotes no plan"])
        self.assertEqual(github.unread_claim_lines(board.Snapshot.from_dict(snap)), [])

    def test_the_log_says_what_the_board_shows_of_a_claim_file_github_gave_no_text_of_under_a_worker_label(self):
        # The Announce step puts the worker's label on the issue, and with
        # it the row names that worker and lists no files, as for a claim
        # file that lists none: the line said the board shows the claim
        # file as not read, which nothing on the page did.
        snap = board.Snapshot.from_dict({
            "sha": "0123abc", "default_branch": "main", "branches": ["claude/13"],
            "issues": [{"number": 13, "title": "Add the remember skill", "labels": [{"name": "claimed"}]}],
            "claims": {"13": board.UNREAD_CLAIM},
            "claim_issues": {"13": {"state": "open", "labels": ["claimed", "worker:nightly"]}},
        })
        page = board.render(snap)
        self.assertIn("| #13 | claude/13 | nightly | (no pull request yet) | - |", page)
        self.assertNotIn("not read", page)
        self.assertEqual(github.unread_claim_lines(snap), [
            "claude/13: GitHub gave no text for docs/agents/claims/13.md, a file over 1 MB, so the board reads "
            "no worker and no files from it, and a release quotes no plan"])
        snap.releases["13"] = board.Release("2026-09-20T09:00:00Z", HEAD_12, "2026-09-18T12:00:00Z")
        self.assertIn("The claim file is over 1 MB, more than GitHub gives the text of, so its plan is not quoted",
                      coordinator.release_text(snap, "13", coordinator.Holding()))

    def test_a_board_run_says_which_claim_file_github_gave_no_text_of(self):
        snap, answers = fixture_answers()
        answers["/repos/o/r/contents/docs/agents/claims/13.md?ref=claude%2F13"] = too_large_to_give(
            "docs/agents/claims/13.md", "claude/13")
        api, _ = client(answers)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "STATE.md"
            printed = io.StringIO()
            with mock.patch.object(github, "GitHub", lambda token, repo: api), redirect_stdout(printed):
                code = cli.run_board(["--render-only", "--sha", snap["sha"], "--out", str(out)],
                                     {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"})
            self.assertEqual(code, 0)
            self.assertIn("| #13 | claude/13 | claim file over 1 MB, not read |", out.read_text(encoding="ascii"))
            self.assertEqual(printed.getvalue().splitlines(), [
                "claude/13: GitHub gave no text for docs/agents/claims/13.md, a file over 1 MB, so the board reads "
                "no worker and no files from it, and a release quotes no plan", f"{out.as_posix()}: rewritten"])

    def test_a_claim_branch_whose_issue_was_deleted_has_no_issue_and_stops_no_run(self):
        # GitHub answers 410 for an issue someone deleted, and the run
        # exited 1 before STATE.md was written.
        snap, answers = fixture_answers()
        answers["/repos/o/r/branches?per_page=100"] = FakeAnswer([{"name": b} for b in snap["branches"] + ["claude/77"]])
        answers["/repos/o/r/issues/77"] = Refusal(410, {"message": "This issue was deleted"})
        api = github.GitHub("t", "o/r", opener=FakeOpener(answers), sleep=lambda seconds: None)
        collected = github.collect(api, snap["sha"], snap["default_branch"])
        self.assertNotIn("77", collected["claim_issues"])
        self.assertIn("| #77 | claude/77 |", board.render(board.Snapshot.from_dict(collected)))
        answers["/repos/o/r/issues/77"] = Refusal(502)
        with self.assertRaises(github.GitHubError):
            github.collect(api, snap["sha"], snap["default_branch"])

    def test_next_link(self):
        headers = {"Link": '<https://api.github.com/repos/o/r/issues?page=2>; rel="next", <https://api.github.com/repos/o/r/issues?page=3>; rel="last"'}
        self.assertEqual(github.next_link(headers), "https://api.github.com/repos/o/r/issues?page=2")
        self.assertIsNone(github.next_link({"Link": '<https://x/>; rel="last"'}))
        self.assertIsNone(github.next_link({}))
        self.assertIsNone(github.next_link(None))


class Token(unittest.TestCase):
    def test_the_token_comes_from_the_argument_else_github_token(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "from-env"}):
            self.assertEqual(github.GitHub(None, "o/r").token, "from-env")
            self.assertEqual(github.GitHub("given", "o/r").token, "given")

    def test_no_token_is_no_client(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(github.GitHubError):
                github.GitHub(None, "o/r")

    def test_a_refusal_keeps_githubs_words_apart_from_the_message(self):
        api, _ = client({"/repos/o/r": Refusal(401, {"message": "Bad credentials"})})
        with self.assertRaises(github.GitHubError) as caught:
            api.get("/repos/o/r")
        self.assertEqual(str(caught.exception), "GET /repos/o/r: HTTP 401")
        self.assertEqual((caught.exception.status, caught.exception.detail), (401, "Bad credentials"))

    def test_an_answer_http_client_cannot_read_is_a_github_error(self):
        # Neither is an OSError: an answer cut short, and one that is no
        # HTTP at all, which urllib passes on as http.client raised them.
        for raised, said in ((http.client.IncompleteRead(b"{", 9), "IncompleteRead(1 bytes read, 9 more expected)"),
                             (http.client.BadStatusLine("NOT HTTP"), "BadStatusLine('NOT HTTP')")):
            opener = mock.Mock()
            opener.open.side_effect = raised
            api = github.GitHub("t", "o/r", opener=opener, sleep=lambda seconds: None)
            with self.assertRaises(github.GitHubError) as caught:
                api.get("/repos/o/r/issues")
            self.assertEqual(str(caught.exception), f"GET /repos/o/r/issues: {said}")

    def test_a_refusal_whose_body_was_cut_short_is_a_github_error_without_githubs_words(self):
        # The body is read for GitHub's words inside the handler of the
        # refusal, where the handler of http.client's exceptions beside it
        # never sees what is raised, so the IncompleteRead left request as
        # it was, past every caller that turns a refusal into a line.
        for code in (403, 422, 500):
            api, _ = client({"POST /repos/o/r/issues/40/comments": CutRefusal(code)})
            with self.assertRaises(github.GitHubError) as caught:
                api.post("/repos/o/r/issues/40/comments", {"body": "x"})
            self.assertEqual(str(caught.exception), f"POST /repos/o/r/issues/40/comments: HTTP {code}")
            self.assertEqual((caught.exception.status, caught.exception.detail), (code, ""))
        self.assertEqual(github.error_detail(CutRefusal(500).raised(github.API + "/repos/o/r")), "")

    def test_a_path_http_client_cannot_send_is_refused_by_the_fake_as_by_http_client(self):
        # http.client writes the request line in ASCII and refuses any
        # other path before it connects; the fake every test here talks to
        # must refuse it the same way, so that a path built from a name a
        # person writes, unquoted, fails a test as it fails a live run.
        line = "/repos/o/r/git/ref/heads/caf\u00e9"
        with self.assertRaises(UnicodeEncodeError):
            http.client.HTTPConnection("api.github.com").putrequest("GET", line)
        with self.assertRaises(UnicodeEncodeError):
            FakeOpener({line: FakeAnswer({})}).open(urllib.request.Request(github.API + line))
        api, _ = client({line: FakeAnswer({})})
        with self.assertRaises(github.GitHubError) as caught:
            api.get(line)
        self.assertEqual(str(caught.exception),
                         "GET /repos/o/r/git/ref/heads/caf\u00e9: the path holds a character outside ASCII, "
                         "which http.client does not send")

    def test_a_token_http_client_cannot_send_is_said_as_the_token_and_never_shown(self):
        # http.client writes a header in Latin-1 and refuses one with a bare
        # line break, as it refuses a path outside ASCII, before it connects.
        # A token pasted with curly quotes was said as a path outside ASCII
        # of a path that was plain ASCII, and one ending in a newline as an
        # answer that was not JSON, of a call that was never sent; the fake
        # refuses both as http.client does, and the message holds neither.
        for token, raised in (("ghs_\u201cpasted\u201d", UnicodeEncodeError), ("ghs_token\n", ValueError)):
            connection = http.client.HTTPConnection("api.github.com")
            connection.putrequest("GET", "/repos/o/r")
            with self.assertRaises(raised):
                connection.putheader("Authorization", f"Bearer {token}")
            api = github.GitHub(token, "o/r", opener=FakeOpener({"/repos/o/r": FakeAnswer({})}),
                                sleep=lambda seconds: None)
            with self.assertRaises(github.GitHubError) as caught:
                api.get("/repos/o/r")
            self.assertEqual(str(caught.exception),
                             "GET /repos/o/r: the token holds a character http.client does not send in a "
                             "header, such as a curly quote or a line break")
            self.assertNotIn("ghs_", str(caught.exception))

    def test_a_call_urllib_will_not_send_for_another_reason_is_said_so_and_quotes_nothing(self):
        # urllib raises ValueError for a proxy setting it cannot read, and
        # its words quote the setting, which may hold a password.
        opener = mock.Mock()
        opener.open.side_effect = ValueError("proxy URL with no authority: 'http:user:secret'")
        api = github.GitHub("t", "o/r", opener=opener, sleep=lambda seconds: None)
        with self.assertRaises(github.GitHubError) as caught:
            api.get("/repos/o/r")
        self.assertEqual(str(caught.exception), "GET /repos/o/r: urllib would not send the call")

    def test_an_answer_that_is_not_json_is_said_so(self):
        api, _ = client({"/repos/o/r": RawAnswer(b"<html>busy</html>")})
        with self.assertRaises(github.GitHubError) as caught:
            api.get("/repos/o/r")
        self.assertEqual(str(caught.exception), "GET /repos/o/r: the answer was not JSON")
        api, _ = client({"/repos/o/r": RawAnswer(b"")})
        self.assertIsNone(api.get("/repos/o/r"))

    def test_an_error_is_a_client_error_the_clerk_can_tolerate(self):
        self.assertTrue(issubclass(github.GitHubError, team.ClientError))


def fixture_answers(name="board.json"):
    """The REST answers that describe the repository a board fixture holds."""
    snap = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    issues = snap["issues"] + [{"number": 99, "pull_request": {"url": "x"}}]
    answers = {
        "/repos/o/r/issues?state=open&per_page=100": FakeAnswer(issues),
        "/repos/o/r/pulls?state=open&per_page=100": FakeAnswer([{"number": p["number"]} for p in snap["pulls"]]),
        "/repos/o/r/branches?per_page=100": FakeAnswer([{"name": b} for b in snap["branches"]]),
        CLOSED_FIRST: FakeAnswer(snap["merged"]),
    }
    for pull in snap["pulls"]:
        detail = {k: v for k, v in pull.items() if k != "files"}
        answers[f"/repos/o/r/pulls/{pull['number']}"] = FakeAnswer(detail)
        answers[f"/repos/o/r/pulls/{pull['number']}/files?per_page=100"] = FakeAnswer([{"filename": f} for f in pull["files"]])
    for key, text in snap["claims"].items():
        if text is not None:
            content = base64.b64encode(text.encode()).decode()
            answers[f"/repos/o/r/contents/docs/agents/claims/{key}.md?ref=claude%2F{key}"] = FakeAnswer({"content": content})
    return snap, answers


def activity_path(branch):
    """The one activity call branch_made_at makes for a branch."""
    return "/repos/o/r/activity?" + urllib.parse.urlencode(
        {"ref": f"refs/heads/{branch}", "activity_type": "branch_creation", "direction": "desc", "per_page": 1})


# The one call newest_creations makes: the whole repository's newest branch
# creations.
NEWEST_CREATIONS = "/repos/o/r/activity?" + urllib.parse.urlencode(
    {"activity_type": "branch_creation", "direction": "desc", "per_page": 100})


def made(branch, timestamp, kind="branch_creation"):
    """One entry of the repository's activity, in GitHub's shape."""
    return {"id": 1, "ref": f"refs/heads/{branch}", "timestamp": timestamp, "activity_type": kind,
            "actor": {"login": "a"}, "before": "0" * 40, "after": "f" * 40}


def wide_answers(forks=()):
    """The wide fixture's answers, its branches by name as GitHub lists
    them, each pull request's head in this repository as GitHub's detail
    names it, but those whose numbers forks holds, whose heads stand in a
    fork."""
    snap, answers = fixture_answers("board-wide.json")
    answers["/repos/o/r/branches?per_page=100"] = FakeAnswer([{"name": board.branch_name(b)} for b in snap["branches"]])
    for pull in snap["pulls"]:
        detail = {k: v for k, v in pull.items() if k != "files"}
        detail["head"] = dict(detail["head"], repo={"full_name": "x/fork" if pull["number"] in forks else "o/r"})
        detail["base"] = {"ref": "main", "repo": {"full_name": "o/r"}}
        answers[f"/repos/o/r/pulls/{pull['number']}"] = FakeAnswer(detail)
    return snap, answers


def activity_asked(opener):
    return sorted(c.selector for c in opener.calls if c.selector.startswith("/repos/o/r/activity"))


class WhenWorkBegan(unittest.TestCase):
    """F13: the oldest work is the work that began first, by when GitHub's
    activity says the push that made its branch came. The wide fixture's
    overlaps: a.md by #61 (claude/22) and #66 (claude/34), both ready, and
    docs/b.md by #63 (claude/31) and claude/5's claim with no pull
    request."""

    def test_the_newest_creation_of_the_branch_is_read_in_one_call(self):
        api, opener = client({activity_path("claude/7"): FakeAnswer([made("claude/7", "2026-10-01T09:00:00Z")])})
        self.assertEqual(github.branch_made_at(api, "claude/7"), "2026-10-01T09:00:00Z")
        self.assertEqual([c.selector for c in opener.calls], [activity_path("claude/7")])

    def test_nothing_on_record_says_nothing(self):
        for answer in ([], [made("claude/8", "2026-10-01T09:00:00Z")],
                       [made("claude/7", "2026-10-01T09:00:00Z", "push")], {}):
            with self.subTest(answer=answer):
                api, _ = client({activity_path("claude/7"): FakeAnswer(answer)})
                self.assertEqual(github.branch_made_at(api, "claude/7"), "")
        api, _ = client({})
        self.assertEqual(github.branch_made_at(api, "claude/7"), "")

    def test_a_refusal_other_than_a_404_raises_from_the_one_call(self):
        api, _ = client({activity_path("claude/7"): Refusal(500)})
        with self.assertRaises(github.GitHubError):
            github.branch_made_at(api, "claude/7")

    def test_one_read_dates_every_branch_the_newest_creations_name(self):
        page = [made("claude/8", "2026-10-01T10:00:00Z"), made("claude/7", "2026-10-01T09:00:00Z"),
                made("claude/8", "2026-10-01T08:00:00Z"), made("claude/9", "2026-10-01T07:00:00Z", "push"),
                made("claude/10", ""), dict(made("claude/9", "2026-10-01T06:00:00Z"), ref=["refs/heads/claude/9"]),
                "an entry that is no object"]
        api, opener = client({NEWEST_CREATIONS: FakeAnswer(page)})
        self.assertEqual(github.newest_creations(api, ["claude/7", "claude/8", "claude/9", "claude/10"]),
                         {"claude/8": "2026-10-01T10:00:00Z", "claude/7": "2026-10-01T09:00:00Z"})
        self.assertEqual([c.selector for c in opener.calls], [NEWEST_CREATIONS])
        for answer in ([], {}, None):
            with self.subTest(answer=answer):
                api, _ = client({NEWEST_CREATIONS: FakeAnswer(answer)})
                self.assertEqual(github.newest_creations(api, ["claude/7"]), {})
        api, _ = client({NEWEST_CREATIONS: Refusal(502)})
        with self.assertRaises(github.GitHubError):
            github.newest_creations(api, ["claude/7"])

    def test_collect_reads_the_newest_creations_once_then_each_branch_they_miss_and_never_a_fork(self):
        snap, answers = wide_answers(forks=(66,))
        answers[NEWEST_CREATIONS] = FakeAnswer([made("claude/22", "2026-09-20T08:00:00Z"),
                                                made("claude/34", "2026-09-21T08:00:00Z")])
        answers[activity_path("claude/5")] = FakeAnswer([made("claude/5", "2026-09-19T08:00:00Z")])
        api, opener = client(answers)
        found = github.collect(api, snap["sha"], snap["default_branch"])
        self.assertEqual(found["began"], {"claude/22": "2026-09-20T08:00:00Z", "claude/5": "2026-09-19T08:00:00Z"})
        self.assertNotIn("began_refused", found)
        self.assertEqual(activity_asked(opener), sorted([NEWEST_CREATIONS] + [activity_path(b) for b in ("claude/31",
                                                                                                         "claude/5")]))

    def test_nothing_is_read_where_nothing_overlaps(self):
        snap, answers = fixture_answers()
        api, opener = client(answers)
        self.assertEqual(github.collect(api, snap["sha"], snap["default_branch"])["began"], {})
        self.assertEqual(activity_asked(opener), [])

    def test_the_overlaps_land_in_the_order_the_work_began(self):
        snap, answers = wide_answers()
        answers[NEWEST_CREATIONS] = FakeAnswer([made("claude/22", "2026-09-20T08:00:00Z"),
                                                made("claude/34", "2026-09-18T08:00:00Z")])
        api, _ = client(answers)
        self.assertIn("| a.md | #66, #61 |", board.render(github.snapshot(api, snap["sha"], snap["default_branch"])))

    def test_a_refused_read_of_the_newest_creations_asks_each_branch_on_its_own(self):
        snap, answers = wide_answers()
        answers[NEWEST_CREATIONS] = Refusal(502)
        answers[activity_path("claude/22")] = FakeAnswer([made("claude/22", "2026-09-20T08:00:00Z")])
        answers[activity_path("claude/34")] = FakeAnswer([made("claude/34", "2026-09-18T08:00:00Z")])
        api, opener = client(answers)
        found = github.collect(api, snap["sha"], snap["default_branch"])
        self.assertEqual(found["began"], {"claude/22": "2026-09-20T08:00:00Z", "claude/34": "2026-09-18T08:00:00Z"})
        self.assertNotIn("began_refused", found)
        self.assertEqual(activity_asked(opener), sorted([NEWEST_CREATIONS] + [
            activity_path(b) for b in ("claude/22", "claude/34", "claude/31", "claude/5")]))

    def test_a_refused_read_leaves_that_branch_out_says_so_and_fails_no_run(self):
        snap, answers = wide_answers()
        answers[NEWEST_CREATIONS] = FakeAnswer([made("claude/22", "2026-09-20T08:00:00Z")])
        for code in (403, 422, 500, 502, 503):
            answers[activity_path("claude/31")] = Refusal(code, {"message": "no"})
            with self.subTest(code=code):
                api, _ = client(answers)
                found = github.collect(api, snap["sha"], snap["default_branch"])
                self.assertEqual(found["began"], {"claude/22": "2026-09-20T08:00:00Z"})
                self.assertEqual(list(found["began_refused"]), ["claude/31"])
                self.assertIn(f"HTTP {code}", found["began_refused"]["claude/31"])
                live = board.Snapshot.from_dict(found)
                self.assertIn("| docs/b.md | #63, #5 |", board.render(live))
                self.assertEqual(github.began_refused_lines(live), [
                    f"claude/31: GitHub refused to say when the branch was made "
                    f"({found['began_refused']['claude/31']}), so the Overlaps date its pull request by when it "
                    f"was opened"])
        self.assertEqual(github.began_refused_lines(board.Snapshot.from_dict(snap)), [])

    def test_the_log_says_a_refused_claim_lands_last(self):
        """F13's second review: a claim with no pull request yet is the work
        nothing dates once GitHub refuses its branch, and lands last, where
        a pull request's work is dated by its opening."""
        snap, answers = wide_answers()
        answers[NEWEST_CREATIONS] = FakeAnswer([made("claude/31", "2026-09-20T08:00:00Z")])
        answers[activity_path("claude/5")] = Refusal(502)
        api, _ = client(answers)
        live = github.snapshot(api, snap["sha"], snap["default_branch"])
        self.assertIn("| docs/b.md | #63, #5 |", board.render(live))
        self.assertEqual(github.began_refused_lines(live), [
            f"claude/5: GitHub refused to say when the branch was made ({live.began_refused['claude/5']}), "
            f"so the Overlaps put its claim last, as work nothing dates"])

    def test_the_log_says_a_stranger_s_pull_request_is_dated_and_the_claim_put_last(self):
        """F19's review: a stranger's pull request from a claim's branch is
        a piece of its own, dated by its opening once GitHub refuses the
        branch, while the claim, which has no pull request of its own, is
        the work nothing dates; the line says both, where it said the
        branch's pull request alone."""
        snap, answers = wide_answers()
        stranger = {"number": 90, "title": "Describe b", "draft": False, "created_at": "2026-09-20T10:00:00Z",
                    "user": {"login": "sam"}, "author_association": "NONE",
                    "head": {"ref": "claude/5", "sha": "s90", "repo": {"full_name": "o/r"}},
                    "base": {"ref": "main", "repo": {"full_name": "o/r"}}}
        answers["/repos/o/r/pulls?state=open&per_page=100"] = FakeAnswer(
            [{"number": p["number"]} for p in snap["pulls"]] + [{"number": 90}])
        answers["/repos/o/r/pulls/90"] = FakeAnswer(stranger)
        answers["/repos/o/r/pulls/90/files?per_page=100"] = FakeAnswer([{"filename": "docs/b.md"}])
        answers["/repos/o/r/collaborators/sam/permission"] = FakeAnswer({"permission": "read", "role_name": "read"})
        answers[NEWEST_CREATIONS] = FakeAnswer([made("claude/31", "2026-09-20T08:00:00Z")])
        answers[activity_path("claude/5")] = Refusal(502)
        api, _ = client(answers)
        live = github.snapshot(api, snap["sha"], snap["default_branch"])
        self.assertTrue(next(p for p in live.pulls if p.number == 90).stranger)
        self.assertIn("| docs/b.md | #63, #90, #5 |", board.render(live))
        self.assertEqual(github.began_refused_lines(live), [
            f"claude/5: GitHub refused to say when the branch was made ({live.began_refused['claude/5']}), "
            f"so the Overlaps date a stranger's pull request from it by when it was opened and put its claim "
            f"last, as work nothing dates"])

    def test_a_branch_two_pull_requests_share_is_asked_about_once(self):
        """F13's second review: two open pull requests from one head, as to
        the default branch and to a release branch, are two pieces of work
        on one branch, whose push GitHub is asked about once."""
        snap, _ = wide_answers()
        twin = dict(next(p for p in snap["pulls"] if p["number"] == 63), number=70)
        snap["pulls"].append(twin)
        api, opener = client({NEWEST_CREATIONS: FakeAnswer([])})
        self.assertEqual([w.branch for w in board.overlaps(snap)["docs/b.md"]].count("claude/31"), 2)
        self.assertEqual(github.began_times(api, snap), ({}, {}))
        self.assertEqual(activity_asked(opener), sorted([NEWEST_CREATIONS] + [
            activity_path(b) for b in ("claude/22", "claude/31", "claude/34", "claude/5")]))


class Collect(unittest.TestCase):
    def test_a_live_snapshot_renders_the_page_the_fixture_does(self):
        snap, answers = fixture_answers()
        api, _ = client(answers)
        live = github.snapshot(api, snap["sha"], snap["default_branch"])
        self.assertIsInstance(live, board.Snapshot)
        self.assertEqual(board.render(live).encode("ascii"), (FIXTURES / "board-STATE.md").read_bytes())

    def test_collect_leaves_pull_requests_out_of_the_issues_and_keeps_only_claim_branches(self):
        snap, answers = fixture_answers()
        api, _ = client(answers)
        found = github.collect(api, "abc", "main")
        self.assertEqual([i["number"] for i in found["issues"]], [14, 15])
        self.assertEqual(found["branches"], ["claude/12", "claude/13"])
        self.assertEqual(found["claims"]["12"], snap["claims"]["12"])
        self.assertIsNone(found["claims"]["13"])
        self.assertEqual((found["sha"], found["default_branch"]), ("abc", "main"))

    def test_post_comments_posts_each_due_comment_once_and_says_so(self):
        api, opener = client({
            "/repos/o/r/issues/40/comments?per_page=100": FakeAnswer([]),
            "/repos/o/r/issues/41/comments?per_page=100": FakeAnswer([{"body": board.IDLE_MARKER}]),
            "POST /repos/o/r/issues/40/comments": FakeAnswer({}),
        })
        due = [board.DueComment(40, "<!-- sdlc-board: main-moved 0123abc -->", "main moved: x"),
               (41, board.IDLE_MARKER, board.IDLE_TEXT)]
        self.assertEqual(github.post_comments(api, due), ["PR #40: main-moved comment posted"])
        self.assertEqual(opener.sent("POST"), [{"body": "<!-- sdlc-board: main-moved 0123abc -->\nmain moved: x"}])

    def test_a_comment_github_refuses_is_a_line_and_the_next_comment_is_still_tried(self):
        # A refused comment, a secondary rate limit or a server error, ended
        # the board's run with exit 1 after STATE.md was written, and the
        # workflow writes the board to its branch only after a run that
        # succeeded; a comment GitHub refuses for good froze the branch.
        api, opener = client({
            "/repos/o/r/issues/40/comments?per_page=100": FakeAnswer([]),
            "POST /repos/o/r/issues/40/comments": Refusal(403, {"message": "You have exceeded a secondary rate limit"}),
            "/repos/o/r/issues/41/comments?per_page=100": Refusal(502),
            "/repos/o/r/issues/42/comments?per_page=100": FakeAnswer([]),
            "POST /repos/o/r/issues/42/comments": FakeAnswer({}),
        })
        due = [board.DueComment(40, "<!-- sdlc-board: main-moved 0123abc -->", "main moved: x"),
               board.DueComment(41, board.IDLE_MARKER, board.IDLE_TEXT),
               board.DueComment(42, board.IDLE_MARKER, board.IDLE_TEXT)]
        self.assertEqual(github.post_comments(api, due), [
            "PR #40: main-moved comment failed: POST /repos/o/r/issues/40/comments: HTTP 403: "
            "You have exceeded a secondary rate limit",
            "PR #41: idle comment failed: GET /repos/o/r/issues/41/comments: HTTP 502",
            "PR #42: idle comment posted"])
        # 41's comments could not be read, so whether it was told is not
        # known, and nothing is posted to it: the next run tells it once.
        self.assertEqual([c.full_url.replace(github.API, "") for c in opener.calls if c.get_method() == "POST"],
                         ["/repos/o/r/issues/40/comments", "/repos/o/r/issues/42/comments"])

    def test_a_comment_whose_answer_was_cut_short_is_a_line_and_the_next_comment_is_still_tried(self):
        # http.client raises IncompleteRead, which is no OSError, for an
        # answer that ends before its length, and it left the client as it
        # was, past every except GitHubError of the adapter, so it ended
        # the run after STATE.md was written. GitHub may have written the
        # comment; the next run reads its marker and posts nothing twice.
        api, _ = client({
            "/repos/o/r/issues/40/comments?per_page=100": FakeAnswer([]),
            "POST /repos/o/r/issues/40/comments": CutAnswer({}),
            "/repos/o/r/issues/41/comments?per_page=100": FakeAnswer([]),
            "POST /repos/o/r/issues/41/comments": FakeAnswer({}),
        })
        due = [board.DueComment(40, board.IDLE_MARKER, board.IDLE_TEXT),
               board.DueComment(41, board.IDLE_MARKER, board.IDLE_TEXT)]
        self.assertEqual(github.post_comments(api, due), [
            "PR #40: idle comment failed: POST /repos/o/r/issues/40/comments: "
            "IncompleteRead(5 bytes read, 96 more expected)",
            "PR #41: idle comment posted"])

    def test_a_board_run_whose_comment_github_refuses_writes_the_board_and_exits_0(self):
        snap, answers = fixture_answers()
        answers["/repos/o/r/issues/40/comments?per_page=100"] = FakeAnswer([])
        answers["POST /repos/o/r/issues/40/comments"] = Refusal(500)
        answers["/repos/o/r/issues/41/comments?per_page=100"] = FakeAnswer([])
        answers["POST /repos/o/r/issues/41/comments"] = FakeAnswer({})
        api, _ = client(answers)
        environ = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "GITHUB_EVENT_NAME": "push",
                   "GITHUB_REF_NAME": "main"}
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "STATE.md"
            printed, said = io.StringIO(), io.StringIO()
            with mock.patch.object(github, "GitHub", lambda token, repo: api), \
                 redirect_stdout(printed), redirect_stderr(said):
                code = cli.run_board(["--sha", snap["sha"], "--out", str(out)], environ)
            self.assertEqual((code, said.getvalue()), (0, ""))
            self.assertEqual(out.read_bytes(), (FIXTURES / "board-STATE.md").read_bytes())
            self.assertEqual(printed.getvalue().splitlines(), [
                f"{out.as_posix()}: rewritten",
                "PR #40: main-moved comment failed: POST /repos/o/r/issues/40/comments: HTTP 500",
                "PR #40: idle comment failed: POST /repos/o/r/issues/40/comments: HTTP 500",
                "PR #41: idle comment posted"])

    def test_a_board_run_whose_refused_comment_came_with_a_cut_body_writes_the_board_and_exits_0(self):
        # The refusal's body, read for GitHub's words, raised IncompleteRead,
        # which ended the run after STATE.md was written, so the workflow
        # wrote no board to its branch and #41's comment was never tried.
        snap, answers = fixture_answers()
        answers["/repos/o/r/issues/40/comments?per_page=100"] = FakeAnswer([])
        answers["POST /repos/o/r/issues/40/comments"] = CutRefusal(500)
        answers["/repos/o/r/issues/41/comments?per_page=100"] = FakeAnswer([])
        answers["POST /repos/o/r/issues/41/comments"] = FakeAnswer({})
        api, _ = client(answers)
        environ = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "GITHUB_EVENT_NAME": "push",
                   "GITHUB_REF_NAME": "main"}
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "STATE.md"
            printed, said = io.StringIO(), io.StringIO()
            with mock.patch.object(github, "GitHub", lambda token, repo: api), \
                 redirect_stdout(printed), redirect_stderr(said):
                code = cli.run_board(["--sha", snap["sha"], "--out", str(out)], environ)
            self.assertEqual((code, said.getvalue()), (0, ""))
            self.assertEqual(out.read_bytes(), (FIXTURES / "board-STATE.md").read_bytes())
            self.assertEqual(printed.getvalue().splitlines(), [
                f"{out.as_posix()}: rewritten",
                "PR #40: main-moved comment failed: POST /repos/o/r/issues/40/comments: HTTP 500",
                "PR #40: idle comment failed: POST /repos/o/r/issues/40/comments: HTTP 500",
                "PR #41: idle comment posted"])


HEAD_12 = "aaaaaaa1111111"
RENAME_12 = "POST /repos/o/r/branches/claude%2F12/rename"
COMMENTS_12 = "/repos/o/r/issues/12/comments?per_page=100"
RULES_12 = "/repos/o/r/rules/branches/claude%2F12?per_page=100"
RULES_RELEASED_12 = "/repos/o/r/rules/branches/released%2F12-aaaaaaa?per_page=100"
REFUSED_12 = "<!-- sdlc-board: rename-refused released/12-aaaaaaa -->"
DRAFT_COMMENTS_40 = "/repos/o/r/issues/40/comments?per_page=100"
RELEASE_COMMENT = {"body": claims.RELEASE_MARKER + "\nReleased: no commit on claude/12 for two days.",
                   "author_association": "OWNER", "created_at": "2026-09-20T09:00:00Z"}
# What the draft's author says on it when the work is done and only a
# maintainer can mark it ready.
FINISHED = {"user": {"login": "alice"}, "body": "finished; ready to mark", "created_at": "2026-09-18T13:00:00Z"}


def released_answers():
    """The fixture's repository once the coordinator released claude/12: the
    branch list with each head, issue 12 open with only todo left, its
    comments ending with the release, and the head commit; issue 13 still
    labelled claimed, whose comments must cost no call."""
    snap, answers = fixture_answers()
    answers["/repos/o/r/branches?per_page=100"] = FakeAnswer([
        {"name": "main", "commit": {"sha": "0123abc"}},
        {"name": "claude/12", "commit": {"sha": HEAD_12}},
        {"name": "claude/13", "commit": {"sha": "bbbbbbb2222222"}},
    ])
    answers["/repos/o/r/issues?state=open&per_page=100"] = FakeAnswer(snap["issues"] + [
        {"number": 12, "title": "Describe the board", "state": "open", "labels": [{"name": "todo"}]},
        {"number": 13, "title": "Add the remember skill", "state": "open", "labels": [{"name": "claimed"}]},
    ])
    answers[COMMENTS_12] = FakeAnswer([
        {"body": "Taking this one.", "author_association": "OWNER", "created_at": "2026-09-18T11:00:00Z"},
        RELEASE_COMMENT,
    ])
    answers[f"/repos/o/r/git/commits/{HEAD_12}"] = FakeAnswer({"sha": HEAD_12,
                                                               "committer": {"date": "2026-09-18T12:00:00Z"}})
    draft = {k: v for k, v in snap["pulls"][0].items() if k != "files"}
    answers["/repos/o/r/pulls/40"] = FakeAnswer(dict(draft, draft=True, user={"login": "alice"},
                                                     author_association="COLLABORATOR"))
    answers[DRAFT_COMMENTS_40] = FakeAnswer([])
    return snap, answers


def released(head=HEAD_12):
    """A snapshot in which the coordinator released claude/12 at that head."""
    return board.Snapshot.from_dict({
        "branches": ["claude/12"],
        "claim_issues": {"12": {"state": "open", "labels": ["todo"]}},
        "releases": {"12": {"released_at": "2026-09-20T09:00:00Z", "sha": head,
                            "committed_at": "2026-09-18T12:00:00Z"}},
    })


class Releases(unittest.TestCase):
    """The board reads a release where one can have happened, and frees the
    released claim's name by renaming its branch."""

    def test_collect_reads_a_release_only_where_the_label_claimed_is_off(self):
        _, answers = released_answers()
        api, opener = client(answers)
        found = github.collect(api, "abc", "main")
        self.assertEqual(found["releases"], {"12": {"released_at": "2026-09-20T09:00:00Z", "sha": HEAD_12,
                                                    "committed_at": "2026-09-18T12:00:00Z"}})
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertIn(COMMENTS_12, asked)
        self.assertNotIn("/repos/o/r/issues/13/comments?per_page=100", asked)

    def test_a_live_snapshot_puts_the_released_issue_back_in_the_queue(self):
        _, answers = released_answers()
        api, _ = client(answers)
        page = board.render(github.snapshot(api, "abc", "main"))
        self.assertNotIn("| #12 | claude/12 |", page)
        self.assertIn("| #12 | Describe the board | todo |", page.split(board.QUEUE_HEADING)[1])

    def test_an_untrusted_release_comment_leaves_the_claim_due_but_unreleased(self):
        _, answers = released_answers()
        answers[COMMENTS_12] = FakeAnswer([dict(RELEASE_COMMENT, author_association="NONE")])
        api, opener = client(answers)
        self.assertEqual(github.collect(api, "abc", "main")["releases"],
                         {"12": {"released_at": "", "sha": HEAD_12, "committed_at": "2026-09-18T12:00:00Z"}})

    def test_a_claim_committed_to_within_a_day_costs_its_commit_and_no_comment_call(self):
        _, answers = released_answers()
        api, opener = client(answers)
        found = github.collect(api, "abc", "main", now=datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(found["releases"], {})
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertIn(f"/repos/o/r/git/commits/{HEAD_12}", asked)
        self.assertNotIn(COMMENTS_12, asked)

    def test_a_claim_still_labelled_claimed_is_read_too_since_the_coordinator_may_release_it(self):
        _, answers = released_answers()
        answers["/repos/o/r/issues?state=open&per_page=100"] = FakeAnswer([
            {"number": 12, "title": "Describe the board", "state": "open",
             "labels": [{"name": "claimed"}, {"name": "todo"}, {"name": "worker:main"}]}])
        answers[COMMENTS_12] = FakeAnswer([])
        api, _ = client(answers)
        self.assertEqual(github.collect(api, "abc", "main")["releases"],
                         {"12": {"released_at": "", "sha": HEAD_12, "committed_at": "2026-09-18T12:00:00Z"}})

    def test_an_author_github_does_not_vouch_for_counts_with_write_permission(self):
        # GitHub may show a private member of the organization to the
        # workflow's token as a contributor; their permission decides.
        private = dict(RELEASE_COMMENT, author_association="CONTRIBUTOR", user={"login": "private-member"})
        asking = "/repos/o/r/collaborators/private-member/permission"
        cases = ((FakeAnswer({"permission": "admin", "role_name": "admin"}), True),
                 (FakeAnswer({"permission": "write", "role_name": "maintain"}), True),
                 (FakeAnswer({"permission": "read", "role_name": "triage"}), False),
                 (Refusal(403, {"message": "Must have push access"}), False))
        for answer, counts in cases:
            _, answers = released_answers()
            answers[COMMENTS_12] = FakeAnswer([private, private])
            answers[asking] = answer
            api, opener = client(answers)
            released_at = github.collect(api, "abc", "main")["releases"]["12"]["released_at"]
            self.assertEqual(bool(released_at), counts, answer)
            self.assertEqual(sum(c.full_url.endswith(asking) for c in opener.calls), 1, "asked once per login")

    def test_a_draft_its_author_said_is_finished_is_never_released_by_the_coordinator(self):
        # A cloud session whose gh pr calls are refused cannot mark its pull
        # request ready, and says so on the draft for a maintainer instead.
        # While its issue carries claimed no release can count, so its
        # issue's comments cost no call.
        later = {"user": {"login": "bob"}, "body": "Thanks!", "created_at": "2026-09-18T14:00:00Z"}
        _, answers = released_answers()
        answers["/repos/o/r/issues?state=open&per_page=100"] = FakeAnswer([
            {"number": 12, "title": "Describe the board", "state": "open",
             "labels": [{"name": "claimed"}, {"name": "todo"}, {"name": "worker:alice"}]}])
        answers[DRAFT_COMMENTS_40] = FakeAnswer([FINISHED, later])
        api, opener = client(answers)
        found = github.collect(api, "abc", "main")
        self.assertEqual(found["releases"], {})
        self.assertNotIn(COMMENTS_12, [c.full_url.replace(github.API, "") for c in opener.calls])
        self.assertEqual(coordinator.releases_due(board.Snapshot.from_dict(found), NOW), [])
        taken_back = {"user": {"login": "alice"}, "body": "One more change first.", "created_at": "2026-09-18T15:00:00Z"}
        answers[DRAFT_COMMENTS_40] = FakeAnswer([FINISHED, later, taken_back])
        api, _ = client(answers)
        self.assertIn("12", github.collect(api, "abc", "main")["releases"])

    def test_a_finished_draft_a_person_released_by_hand_is_released_and_the_coordinator_releases_none(self):
        # The coordinator never releases a finished claim, but a person may,
        # by the steps of any release (spec, Release): the comment, then
        # the labels. The board left out every finished draft before it
        # read any release comment, so such a release never took effect:
        # the claim stayed held and its issue out of the queue for good.
        _, answers = released_answers()
        answers[DRAFT_COMMENTS_40] = FakeAnswer([FINISHED])
        api, _ = client(answers)
        snap = github.snapshot(api, "abc", "main")
        self.assertEqual(snap.releases["12"].released_at, RELEASE_COMMENT["created_at"])
        self.assertEqual(board.released_keys(snap), {"12"})
        page = board.render(snap)
        self.assertNotIn("| #12 | claude/12 |", page)
        self.assertIn("| #12 | Describe the board | todo |", page.split(board.QUEUE_HEADING)[1])
        self.assertEqual(coordinator.releases_due(snap, NOW), [])

    def test_a_finished_draft_with_no_release_that_counts_is_left_to_its_review(self):
        # With claimed off and no release comment, or one made before the
        # claim had gone a day without a commit, the coordinator must not
        # take the finished claim for one it may release.
        too_soon = dict(RELEASE_COMMENT, created_at="2026-09-19T09:00:00Z")
        for comments in ([], [too_soon]):
            _, answers = released_answers()
            answers[DRAFT_COMMENTS_40] = FakeAnswer([FINISHED])
            answers[COMMENTS_12] = FakeAnswer(comments)
            api, _ = client(answers)
            snap = github.snapshot(api, "abc", "main")
            self.assertEqual(snap.releases, {}, comments)
            self.assertEqual(coordinator.releases_due(snap, NOW), [], comments)
            self.assertEqual(board.released_keys(snap), set(), comments)

    def test_a_finished_draft_whose_labels_alone_came_off_is_held_until_a_person_posts_the_release(self):
        # The spec's Release section: a finished draft is released by hand
        # only with the release comment, since no coordinator's comment
        # follows the labels coming off, as it does for any other claim.
        _, answers = released_answers()
        answers[DRAFT_COMMENTS_40] = FakeAnswer([FINISHED])
        answers[COMMENTS_12] = FakeAnswer([])
        api, _ = client(answers)
        snap = github.snapshot(api, "abc", "main")
        page = board.render(snap)
        self.assertIn("| #12 | claude/12 |", page)
        self.assertNotIn("| #12 |", page.split(board.QUEUE_HEADING)[1])
        self.assertEqual(coordinator.releases_due(snap, NOW), [])
        answers[COMMENTS_12] = FakeAnswer([RELEASE_COMMENT])
        api, _ = client(answers)
        snap = github.snapshot(api, "abc", "main")
        self.assertEqual(board.released_keys(snap), {"12"})
        self.assertIn("| #12 | Describe the board | todo |", board.render(snap).split(board.QUEUE_HEADING)[1])
        self.assertEqual(coordinator.releases_due(snap, NOW), [])

    def test_a_pull_request_from_a_fork_named_like_the_claim_neither_finishes_nor_holds_it(self):
        """F17: sam's pull request from a fork's claude/12 is no claim's.
        Marked ready, it leaves the claim to be read for its release; as a
        draft its author said is finished, it holds nothing, and its comments
        cost no call; and the board lists it beside the released claim."""
        fork = {"number": 50, "title": "Fix the board", "mergeable_state": "clean", "updated_at": "2026-09-19T12:00:00Z",
                "user": {"login": "sam"}, "head": {"ref": "claude/12", "sha": "ddd", "repo": {"full_name": "sam/r"}},
                "base": {"ref": "main", "repo": {"full_name": "o/r"}}}
        self.assertEqual(github.ready_branches([dict(fork, draft=False), {"head": {"ref": "claude/13"}}]), {"claude/13"})
        for ready in (True, False):
            _, answers = released_answers()
            answers["/repos/o/r/pulls?state=open&per_page=100"] = FakeAnswer([{"number": n} for n in (40, 41, 50)])
            answers["/repos/o/r/pulls/50"] = FakeAnswer(dict(fork, draft=not ready))
            answers["/repos/o/r/pulls/50/files?per_page=100"] = FakeAnswer([{"filename": "docs/agents/README.md"}])
            answers["/repos/o/r/issues/50/comments?per_page=100"] = FakeAnswer([dict(FINISHED, user={"login": "sam"})])
            api, opener = client(answers)
            snap = github.snapshot(api, "abc", "main")
            with self.subTest(ready=ready):
                self.assertEqual(snap.releases["12"].released_at, RELEASE_COMMENT["created_at"])
                self.assertEqual(board.released_keys(snap), {"12"})
                self.assertNotIn("/repos/o/r/issues/50/comments?per_page=100",
                                 [c.full_url.replace(github.API, "") for c in opener.calls])
                self.assertIn("| #50 | Fix the board | claude/12 |", board.render(snap))
        # While the claim is still held, the claim's own draft, which nobody
        # said is finished, decides, so the claim is read for its release;
        # until the fix the fork's draft, listed after it, took its place
        # and sam's "finished; ready to mark" held the claim (F17's review).
        _, answers = released_answers()
        answers["/repos/o/r/issues?state=open&per_page=100"] = FakeAnswer([
            {"number": 12, "title": "Describe the board", "state": "open",
             "labels": [{"name": "claimed"}, {"name": "todo"}, {"name": "worker:alice"}]}])
        answers["/repos/o/r/pulls?state=open&per_page=100"] = FakeAnswer([{"number": n} for n in (40, 41, 50)])
        answers["/repos/o/r/pulls/50"] = FakeAnswer(dict(fork, draft=True))
        answers["/repos/o/r/pulls/50/files?per_page=100"] = FakeAnswer([{"filename": "docs/agents/README.md"}])
        answers["/repos/o/r/issues/50/comments?per_page=100"] = FakeAnswer([dict(FINISHED, user={"login": "sam"})])
        api, opener = client(answers)
        self.assertIn("12", github.collect(api, "abc", "main")["releases"])
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertIn(DRAFT_COMMENTS_40, asked)
        self.assertNotIn("/repos/o/r/issues/50/comments?per_page=100", asked)

    def test_a_pull_request_a_stranger_opened_from_the_claim_s_branch_neither_finishes_nor_holds_it(self):
        """F19: sam, whom GitHub shows with no association and whose
        permission reads read, opened #50 from claude/12 itself, to the
        branch board, beside alice's own #40 to main. Marked ready, it
        leaves the claim to be read for its release; as a draft its author
        said is finished, it holds nothing, and its comments cost no call;
        sam's permission costs one call a run; and the release's rename
        closes it, so the board leaves it off the page, where it lists a
        fork's."""
        stranger = {"number": 50, "title": "Fix the board", "mergeable_state": "clean",
                    "updated_at": "2026-09-19T12:00:00Z", "user": {"login": "sam"}, "author_association": "NONE",
                    "head": {"ref": "claude/12", "sha": "ddd", "repo": {"full_name": "o/r"}},
                    "base": {"ref": "board", "repo": {"full_name": "o/r"}}}
        permission = "/repos/o/r/collaborators/sam/permission"
        for ready in (True, False):
            _, answers = released_answers()
            answers["/repos/o/r/pulls?state=open&per_page=100"] = FakeAnswer([{"number": n} for n in (40, 41, 50)])
            answers["/repos/o/r/pulls/50"] = FakeAnswer(dict(stranger, draft=not ready))
            answers["/repos/o/r/pulls/50/files?per_page=100"] = FakeAnswer([{"filename": "docs/agents/README.md"}])
            answers["/repos/o/r/issues/50/comments?per_page=100"] = FakeAnswer([dict(FINISHED, user={"login": "sam"})])
            answers[permission] = FakeAnswer({"permission": "read", "role_name": "read"})
            api, opener = client(answers)
            found = github.collect(api, "abc", "main")
            snap = board.Snapshot.from_dict(found)
            with self.subTest(ready=ready):
                self.assertTrue(next(p for p in found["pulls"] if p["number"] == 50)["stranger"])
                self.assertEqual(snap.releases["12"].released_at, RELEASE_COMMENT["created_at"])
                self.assertEqual(board.released_keys(snap), {"12"})
                asked = [c.full_url.replace(github.API, "") for c in opener.calls]
                self.assertNotIn("/repos/o/r/issues/50/comments?per_page=100", asked)
                self.assertEqual(asked.count(permission), 1, "asked once a run")
                self.assertNotIn("| #50 |", board.render(snap))
        # While the claim is still held, the claim's own draft, which nobody
        # said is finished, decides, so the claim is read for its release;
        # sam's "finished; ready to mark" on #50 holds nothing.
        _, answers = released_answers()
        answers["/repos/o/r/issues?state=open&per_page=100"] = FakeAnswer([
            {"number": 12, "title": "Describe the board", "state": "open",
             "labels": [{"name": "claimed"}, {"name": "todo"}, {"name": "worker:alice"}]}])
        answers["/repos/o/r/pulls?state=open&per_page=100"] = FakeAnswer([{"number": n} for n in (40, 41, 50)])
        answers["/repos/o/r/pulls/50"] = FakeAnswer(dict(stranger, draft=True))
        answers["/repos/o/r/pulls/50/files?per_page=100"] = FakeAnswer([{"filename": "docs/agents/README.md"}])
        answers["/repos/o/r/issues/50/comments?per_page=100"] = FakeAnswer([dict(FINISHED, user={"login": "sam"})])
        answers[permission] = FakeAnswer({"permission": "read", "role_name": "read"})
        api, opener = client(answers)
        self.assertIn("12", github.collect(api, "abc", "main")["releases"])
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertIn(DRAFT_COMMENTS_40, asked)
        self.assertNotIn("/repos/o/r/issues/50/comments?per_page=100", asked)

    def test_the_marking_and_the_release_rule_share_one_answer_about_an_author(self):
        """F19: sam, whom GitHub shows with no association and whose
        permission reads write, opened draft #50 from claude/12 to the
        branch board and posted issue 12's release comment; one collect
        asks about sam once, through the one cache it hands both readers,
        marks no stranger and reads the release."""
        permission = "/repos/o/r/collaborators/sam/permission"
        _, answers = released_answers()
        answers[COMMENTS_12] = FakeAnswer([dict(RELEASE_COMMENT, author_association="NONE", user={"login": "sam"})])
        answers["/repos/o/r/pulls?state=open&per_page=100"] = FakeAnswer([{"number": n} for n in (40, 41, 50)])
        answers["/repos/o/r/pulls/50"] = FakeAnswer({
            "number": 50, "title": "Fix the board", "draft": True, "mergeable_state": "clean",
            "updated_at": "2026-09-19T12:00:00Z", "user": {"login": "sam"}, "author_association": "NONE",
            "head": {"ref": "claude/12", "sha": "ddd", "repo": {"full_name": "o/r"}},
            "base": {"ref": "board", "repo": {"full_name": "o/r"}}})
        answers["/repos/o/r/pulls/50/files?per_page=100"] = FakeAnswer([{"filename": "docs/agents/README.md"}])
        answers["/repos/o/r/issues/50/comments?per_page=100"] = FakeAnswer([])
        answers[permission] = FakeAnswer({"permission": "write", "role_name": "write"})
        api, opener = client(answers)
        found = github.collect(api, "abc", "main")
        self.assertNotIn("stranger", next(p for p in found["pulls"] if p["number"] == 50))
        self.assertEqual(found["releases"]["12"]["released_at"], RELEASE_COMMENT["created_at"])
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertEqual(asked.count(permission), 1)

    def test_a_refused_permission_call_keeps_a_pull_request_the_claim_s_and_a_404_makes_it_a_stranger_s(self):
        """A lost answer never takes a claim's own pull request for a
        stranger's, which would free a finished claim; it costs the runs in
        which GitHub refuses the call, each releasing no claim such a pull
        request holds. GitHub knowing no such login makes it a stranger's
        (F19)."""
        stranger = {"number": 50, "title": "Fix the board", "mergeable_state": "clean",
                    "updated_at": "2026-09-19T12:00:00Z", "user": {"login": "sam"}, "author_association": "NONE",
                    "head": {"ref": "claude/12", "sha": "ddd", "repo": {"full_name": "o/r"}},
                    "base": {"ref": "board", "repo": {"full_name": "o/r"}}}
        permission = "/repos/o/r/collaborators/sam/permission"
        for answer, kept in ((Refusal(403, {"message": "Must have push access"}), True), (None, False)):
            _, answers = released_answers()
            answers["/repos/o/r/pulls?state=open&per_page=100"] = FakeAnswer([{"number": n} for n in (40, 41, 50)])
            answers["/repos/o/r/pulls/50"] = FakeAnswer(dict(stranger, draft=False))
            answers["/repos/o/r/pulls/50/files?per_page=100"] = FakeAnswer([{"filename": "docs/agents/README.md"}])
            if answer is not None:
                answers[permission] = answer
            api, opener = client(answers)
            found = github.collect(api, "abc", "main")
            with self.subTest(kept=kept):
                self.assertEqual("stranger" in next(p for p in found["pulls"] if p["number"] == 50), not kept)
                self.assertEqual("12" in found["releases"], not kept)
                self.assertEqual(board.released_keys(board.Snapshot.from_dict(found)), set() if kept else {"12"})
                self.assertIn(permission, [c.full_url.replace(github.API, "") for c in opener.calls])

    def test_a_finished_claim_costs_no_call(self):
        ready, answers = released_answers()
        answers["/repos/o/r/pulls/40"] = FakeAnswer({k: v for k, v in ready["pulls"][0].items() if k != "files"})
        in_review, review_answers = released_answers()
        review_answers["/repos/o/r/issues?state=open&per_page=100"] = FakeAnswer(in_review["issues"] + [
            {"number": 12, "title": "Describe the board", "state": "open",
             "labels": [{"name": "in-review"}, {"name": "todo"}]}])
        for name, given in (("marked ready", answers), ("labelled in-review", review_answers)):
            api, opener = client(given)
            self.assertEqual(github.collect(api, "abc", "main")["releases"], {}, name)
            self.assertNotIn(COMMENTS_12, [c.full_url.replace(github.API, "") for c in opener.calls], name)

    def test_the_released_branch_is_renamed_and_the_issue_told_once(self):
        api, opener = client({
            REF + "claude/12": FakeAnswer({"object": {"sha": HEAD_12}}),
            RENAME_12: FakeAnswer({"name": "released/12-aaaaaaa"}),
            COMMENTS_12: FakeAnswer([RELEASE_COMMENT]),
            "POST /repos/o/r/issues/12/comments": FakeAnswer({}),
        })
        self.assertEqual(github.release_claims(api, released()),
                         ["#12: claim released; claude/12 renamed to released/12-aaaaaaa"])
        self.assertEqual(opener.sent("POST"), [
            {"new_name": "released/12-aaaaaaa"},
            {"body": "<!-- sdlc-board: released released/12-aaaaaaa -->\n"
                     + board.released_text("claude/12", "released/12-aaaaaaa")},
        ])

    def test_a_branch_that_moved_since_the_snapshot_keeps_its_name(self):
        api, opener = client({REF + "claude/12": FakeAnswer({"object": {"sha": "ddddddd4444444"}})})
        self.assertEqual(github.release_claims(api, released()),
                         ["claude/12: no longer at aaaaaaa, so it keeps its name"])
        api, opener = client({})
        self.assertEqual(github.release_claims(api, released()),
                         ["claude/12: no longer at aaaaaaa, so it keeps its name"])
        self.assertEqual(opener.sent("POST"), [])

    def test_a_refused_rename_is_said_not_raised(self):
        api, opener = client({
            REF + "claude/12": FakeAnswer({"object": {"sha": HEAD_12}}),
            RENAME_12: Refusal(422, {"message": "Reference already exists"}),
            REF + "released/12-aaaaaaa": FakeAnswer({"object": {"sha": "eeeeeee5555555"}}),
            RULES_12: FakeAnswer([]),
            RULES_RELEASED_12: FakeAnswer([]),
            COMMENTS_12: FakeAnswer([RELEASE_COMMENT]),
            "POST /repos/o/r/issues/12/comments": FakeAnswer({}),
        })
        self.assertEqual(github.release_claims(api, released()), [
            "claude/12: not renamed to released/12-aaaaaaa: POST /repos/o/r/branches/claude%2F12/rename: "
            "HTTP 422: Reference already exists; said so on #12"])
        self.assertEqual(opener.sent("POST")[1:], [{"body": REFUSED_12 + "\n" + board.rename_refused_text(
            "claude/12", "released/12-aaaaaaa", [], "HTTP 422: Reference already exists")}])

    def test_a_rename_rulesets_refuse_names_them_on_the_issue_once(self):
        # A ruleset over claude/** makes the rename an administrator's, and
        # GitHub's own refusal does not say which one; the rules endpoint
        # does, for a branch name whether or not the branch exists.
        rule = {"ruleset_source_type": "Repository", "ruleset_source": "o/r", "ruleset_id": 7}
        answers = {
            REF + "claude/12": FakeAnswer({"object": {"sha": HEAD_12}}),
            RENAME_12: Refusal(422, {"message": "Repository rule violations found\n\nCannot update this ref."}),
            REF + "released/12-aaaaaaa": FakeAnswer(None),
            RULES_12: FakeAnswer([dict(rule, type="update"), dict(rule, type="deletion")]),
            RULES_RELEASED_12: FakeAnswer([{"ruleset_source_type": "Organization", "ruleset_source": "o",
                                            "ruleset_id": 9, "type": "creation"}]),
            "/repos/o/r/rulesets/7": FakeAnswer({"id": 7, "name": "claims"}),
            COMMENTS_12: FakeAnswer([RELEASE_COMMENT]),
            "POST /repos/o/r/issues/12/comments": FakeAnswer({}),
        }
        api, opener = client(answers)
        line, = github.release_claims(api, released())
        self.assertTrue(line.endswith("Cannot update this ref.; said so on #12"), line)
        body = opener.sent("POST")[1]["body"]
        self.assertTrue(body.startswith(REFUSED_12 + "\nrename refused: "))
        self.assertIn("(HTTP 422: Repository rule violations found Cannot update this ref.)", body)
        self.assertIn("- [claims](https://github.com/o/r/settings/rules/7): deletion, update\n", body)
        self.assertIn("- [ruleset 9](https://github.com/organizations/o/settings/rules/9): creation\n", body)
        self.assertIn("Take `claude/**` and `released/**` out of their targets", body)
        answers[COMMENTS_12] = FakeAnswer([RELEASE_COMMENT, {"body": REFUSED_12 + "\nrename refused: earlier"}])
        api, opener = client(answers)
        line, = github.release_claims(api, released())
        self.assertTrue(line.endswith("Cannot update this ref."), line)
        self.assertEqual(len(opener.sent("POST")), 1, "only the rename is tried again")
        self.assertFalse(any("/rules" in c.full_url for c in opener.calls), "the rules are read once")

    def test_rules_github_will_not_show_are_said_to_be_unread(self):
        api, opener = client({
            REF + "claude/12": FakeAnswer({"object": {"sha": HEAD_12}}),
            RENAME_12: Refusal(403, {"message": "Resource not accessible by integration"}),
            RULES_12: Refusal(403, {"message": "Resource not accessible by integration"}),
            COMMENTS_12: FakeAnswer([]),
            "POST /repos/o/r/issues/12/comments": FakeAnswer({}),
        })
        line, = github.release_claims(api, released())
        self.assertTrue(line.endswith("; said so on #12"), line)
        self.assertIn("The board could not read which rulesets apply to the two names.",
                      opener.sent("POST")[1]["body"])

    def test_a_rename_github_could_not_answer_is_left_for_the_next_run_unsaid(self):
        # A server error is for the moment, and a 404 can mean only that the
        # branch went away since it was read.
        for status in (502, 404):
            api, opener = client({
                REF + "claude/12": FakeAnswer({"object": {"sha": HEAD_12}}),
                RENAME_12: Refusal(status),
            })
            self.assertEqual(github.release_claims(api, released()), [
                "claude/12: not renamed to released/12-aaaaaaa: POST /repos/o/r/branches/claude%2F12/rename: "
                f"HTTP {status}"])
            asked = [c.full_url.replace(github.API, "") for c in opener.calls]
            self.assertFalse(any("/rules/" in path or "/comments" in path for path in asked), asked)

    def test_a_comment_github_refuses_after_a_refused_rename_is_a_line(self):
        api, _ = client({
            REF + "claude/12": FakeAnswer({"object": {"sha": HEAD_12}}),
            RENAME_12: Refusal(422, {"message": "Repository rule violations found"}),
            REF + "released/12-aaaaaaa": FakeAnswer(None),
            RULES_12: FakeAnswer([]),
            RULES_RELEASED_12: FakeAnswer([]),
            COMMENTS_12: Refusal(403, {"message": "Resource not accessible by integration"}),
        })
        line, = github.release_claims(api, released())
        self.assertTrue(line.endswith("; saying so on #12 failed: GET /repos/o/r/issues/12/comments: HTTP 403: "
                                      "Resource not accessible by integration"), line)

    def test_no_call_github_refuses_stops_the_run(self):
        # A server error while the branch is read again, and a comment
        # refused after the rename: each is a line, and the next claim is
        # still released.
        snap = board.Snapshot.from_dict({
            "branches": ["claude/12", "claude/13"],
            "claim_issues": {k: {"state": "open", "labels": ["todo"]} for k in ("12", "13")},
            "releases": {"12": {"released_at": "2026-09-20T09:00:00Z", "sha": HEAD_12,
                                "committed_at": "2026-09-18T12:00:00Z"},
                         "13": {"released_at": "2026-09-20T09:00:00Z", "sha": "bbbbbbb2222222",
                                "committed_at": "2026-09-18T12:00:00Z"}},
        })
        api, opener = client({
            REF + "claude/12": Refusal(502),
            REF + "claude/13": FakeAnswer({"object": {"sha": "bbbbbbb2222222"}}),
            "POST /repos/o/r/branches/claude%2F13/rename": FakeAnswer({"name": "released/13-bbbbbbb"}),
            "/repos/o/r/issues/13/comments?per_page=100": Refusal(403, {"message": "Resource not accessible"}),
        })
        self.assertEqual(github.release_claims(api, snap), [
            "claude/12: not renamed to released/12-aaaaaaa: GET /repos/o/r/git/ref/heads/claude/12: HTTP 502",
            "#13: claim released; claude/13 renamed to released/13-bbbbbbb, but the comment on the issue failed: "
            "GET /repos/o/r/issues/13/comments: HTTP 403: Resource not accessible"])

    def test_a_claim_pushed_again_at_the_released_head_is_deleted(self):
        # released/12-aaaaaaa already keeps that commit, so GitHub refuses
        # the rename for good; deleting claude/12 frees the name and loses
        # nothing, and the issue was told at the first release.
        api, opener = client({
            REF + "claude/12": FakeAnswer({"object": {"sha": HEAD_12}}),
            RENAME_12: Refusal(422, {"message": "Reference already exists"}),
            REF + "released/12-aaaaaaa": FakeAnswer({"object": {"sha": HEAD_12}}),
            "DELETE /repos/o/r/git/refs/heads/claude/12": FakeAnswer(None),
            COMMENTS_12: FakeAnswer([{"body": board.RELEASED_MARKER.format(branch="released/12-aaaaaaa") + "\nx"}]),
        })
        self.assertEqual(github.release_claims(api, released()), [
            "#12: claim released; released/12-aaaaaaa already keeps aaaaaaa, so claude/12 was deleted"])
        self.assertEqual([c.get_method() for c in opener.calls if c.get_method() != "GET"], ["POST", "DELETE"])

    def test_nothing_released_asks_nothing(self):
        snap, answers = fixture_answers()
        api, opener = client(answers)
        self.assertEqual(github.release_claims(api, snap), [])
        self.assertEqual(opener.calls, [])


class MarkStrangers(unittest.TestCase):
    """github.mark_strangers writes the board's key stranger on a pull
    request from a claim branch here that someone who may not write opened
    (F19); the adapter's readings then leave it out (is_claims)."""

    def pull(self, number, branch="claude/12", login="sam", association="NONE", repo="o/r"):
        return {"number": number, "user": {"login": login}, "author_association": association,
                "head": {"ref": branch, "sha": "ddd", "repo": {"full_name": repo}},
                "base": {"ref": "main", "repo": {"full_name": "o/r"}}}

    def marked(self, pulls, answers):
        asked = []

        def ask(login):
            asked.append(login)
            return answers.get(login)

        self.assertIs(github.mark_strangers(pulls, ask), pulls)
        return [p.get("stranger") for p in pulls], asked

    def test_a_pull_request_from_a_claim_branch_is_marked_when_its_author_may_not_write(self):
        pulls = [self.pull(50), self.pull(51, login="private-member", association="CONTRIBUTOR")]
        self.assertEqual(self.marked(pulls, {"sam": False, "private-member": True}),
                         ([True, None], ["sam", "private-member"]))

    def test_an_author_the_association_or_an_app_s_login_vouches_for_costs_no_call(self):
        pulls = [self.pull(50, association="COLLABORATOR"), self.pull(51, association="MEMBER"),
                 self.pull(52, association="OWNER"), self.pull(53, login="some-app[bot]")]
        self.assertEqual(self.marked(pulls, {}), ([None] * 4, []))

    def test_only_a_pull_request_from_a_claim_branch_here_is_marked(self):
        pulls = [self.pull(50, branch="feature/x"), self.pull(51, repo="sam/r"), self.pull(52)]
        self.assertEqual(self.marked(pulls, {"sam": False}), ([None, None, True], ["sam"]))

    def test_an_answer_github_refused_marks_nothing(self):
        self.assertEqual(self.marked([self.pull(50)], {"sam": None}), ([None], ["sam"]))

    def test_the_ready_branches_and_the_drafts_the_releases_read_leave_a_marked_one_out(self):
        theirs, own = dict(self.pull(50), draft=False), {"head": {"ref": "claude/13"}}
        self.assertEqual(github.ready_branches([theirs, own]), {"claude/12", "claude/13"})
        self.assertTrue(github.is_claims(theirs))
        github.mark_strangers([theirs], lambda login: False)
        self.assertEqual(github.ready_branches([theirs, own]), {"claude/13"})
        self.assertFalse(github.is_claims(theirs))


class Permissions(unittest.TestCase):
    """github.permissions, the client's one cache of who may write, which
    may_write reads for a release comment's author and may_open for a pull
    request's (F19): a refused call is no to the one, so a release by an
    author nobody vouches for frees nothing, and yes to the other, so a lost
    answer never takes a claim's own pull request for a stranger's."""

    def answers(self, login, answer):
        return {f"/repos/o/r/collaborators/{login}/permission": answer}

    def test_a_write_permission_or_role_counts_and_read_or_triage_does_not(self):
        cases = (({"permission": "admin", "role_name": "admin"}, True),
                 ({"permission": "write", "role_name": "write"}, True),
                 ({"permission": "read", "role_name": "maintain"}, True),
                 ({"permission": "read", "role_name": "read"}, False),
                 ({"permission": "read", "role_name": "triage"}, False))
        for answer, may in cases:
            api, _ = client(self.answers("sam", FakeAnswer(answer)))
            with self.subTest(answer=answer):
                asked = github.permissions(api)
                self.assertEqual(asked("sam"), may)
                self.assertEqual(github.may_write(api, asked)("sam"), may)
                self.assertEqual(github.may_open(asked)("sam"), may)

    def test_a_login_github_does_not_know_may_not_and_a_refusal_is_no_answer(self):
        api, _ = client({})
        asked = github.permissions(api)
        self.assertEqual((asked("nobody"), github.may_write(api, asked)("nobody"), github.may_open(asked)("nobody")),
                         (False, False, False))
        api, _ = client(self.answers("sam", Refusal(403, {"message": "Must have push access"})))
        asked = github.permissions(api)
        self.assertEqual((asked("sam"), github.may_write(api, asked)("sam"), github.may_open(asked)("sam")),
                         (None, False, True))

    def test_each_login_is_asked_once_and_the_client_keeps_one_cache(self):
        api, opener = client(self.answers("sam", FakeAnswer({"permission": "read", "role_name": "read"})))
        asked = github.permissions(api)
        self.assertEqual([asked("sam") for _ in range(3)], [False, False, False])
        self.assertEqual(sum(c.full_url.endswith("/collaborators/sam/permission") for c in opener.calls), 1)
        self.assertEqual([api.asked("sam"), api.asked("sam"), github.may_write(api)("sam")], [False, False, False])
        self.assertEqual(sum(c.full_url.endswith("/collaborators/sam/permission") for c in opener.calls), 2,
                         "the client's own cache asks once more, and may_write given no cache takes the client's, so "
                         "every reader of a run, collect, releases, claim_released, landed_release and tell_overlaps, "
                         "asks about an author once (F19's review)")
        bare = types.SimpleNamespace(repo="o/r", get=api.get)
        self.assertEqual([github.may_write(bare)("sam"), github.may_write(bare)("sam")], [False, False])
        self.assertEqual(sum(c.full_url.endswith("/collaborators/sam/permission") for c in opener.calls), 4,
                         "a client with no cache pays for one of its own at each call")


IDLE_CLAIM = ("# Claim: #12\n\n- Worker: nightly\n- Branch: claude/12\n\n## Plan\n\nDescribe the board.\n\n"
              "## Files to touch\n\n- docs/agents/README.md\n")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def idle_snapshot():
    """Issue 12, claimed by nightly, whose branch has had no commit for two
    days, with a draft pull request #40."""
    return board.Snapshot.from_dict({
        "default_branch": "main",
        "issues": [{"number": 12, "title": "Describe the board", "labels": ["claimed", "todo", "worker:nightly"]}],
        "pulls": [{"number": 40, "title": "Describe the board", "head": {"ref": "claude/12", "sha": HEAD_12},
                   "mergeable_state": "clean", "updated_at": "2026-09-18T12:00:00Z", "draft": True,
                   "files": ["docs/agents/README.md"]}],
        "branches": ["claude/12"],
        "claims": {"12": IDLE_CLAIM},
        "claim_issues": {"12": {"state": "open", "labels": ["claimed", "todo", "worker:nightly"]}},
        "releases": {"12": {"released_at": "", "sha": HEAD_12, "committed_at": "2026-09-18T12:00:00Z"}},
    })


COMPARE_12 = f"/repos/o/r/compare/main...{HEAD_12}"
LABEL_CLAIMED_12 = "DELETE /repos/o/r/issues/12/labels/claimed"
LABEL_WORKER_12 = "DELETE /repos/o/r/issues/12/labels/worker%3Anightly"
LABELS_12 = "POST /repos/o/r/issues/12/labels"


def release_answers():
    return {
        COMPARE_12: FakeAnswer({"ahead_by": 2, "commits": [{"commit": {"message": "Claim #12"}},
                                                           {"commit": {"message": "Describe the tables\n\nWhy."}}],
                                "files": [{"filename": "docs/agents/README.md"},
                                          {"filename": "docs/agents/claims/12.md"}]}),
        "POST /repos/o/r/issues/12/comments": FakeAnswer({"id": 1, "created_at": "2026-09-20T12:00:05Z"}),
        LABEL_CLAIMED_12: FakeAnswer(None),
        LABEL_WORKER_12: FakeAnswer(None),
    }


class Coordinate(unittest.TestCase):
    """The coordinator's calls in the board's run: a release, then the
    overlaps."""

    def test_a_due_claim_is_released_labels_first_and_the_board_sees_it_in_the_same_run(self):
        snap = idle_snapshot()
        api, opener = client(release_answers())
        self.assertEqual(github.coordinate(api, snap, NOW), ["#12: claim released, idle since 2026-09-18T12:00:00Z"])
        self.assertEqual([c.get_method() for c in opener.calls], ["DELETE", "DELETE", "GET", "POST"])
        body = opener.sent("POST")[0]["body"]
        self.assertTrue(body.startswith(claims.RELEASE_MARKER + "\nreleased: the claim on this issue, held by nightly,"))
        self.assertIn('2 commits ahead of main, the newest "Describe the tables"; it changes `docs/agents/README.md`.',
                      body)
        self.assertEqual(board.released_keys(snap), {"12"})
        self.assertEqual(snap.releases["12"].released_at, "2026-09-20T12:00:05Z")
        self.assertIn("| #12 | Describe the board | todo |", board.render(snap).split(board.QUEUE_HEADING)[1])

    def test_a_comment_github_refuses_after_the_labels_puts_them_back(self):
        # With no release comment and the labels back, the issue is as the
        # run found it and the next run starts the release over
        # (ReleaseAcrossRuns); the comments are read first, in case GitHub
        # wrote the comment and lost its answer.
        answers = release_answers()
        answers["POST /repos/o/r/issues/12/comments"] = Refusal(403, {"message": "Resource not accessible"})
        answers[COMMENTS_12] = FakeAnswer([])
        answers[LABELS_12] = FakeAnswer([])
        snap = idle_snapshot()
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, snap, NOW), [
            "#12: not released, the comment failed: POST /repos/o/r/issues/12/comments: HTTP 403: "
            "Resource not accessible; claimed, worker:nightly put back"])
        self.assertEqual([c.get_method() for c in opener.calls], ["DELETE", "DELETE", "GET", "POST", "GET", "POST"])
        self.assertEqual(opener.sent("POST")[1], {"labels": ["claimed", "worker:nightly"]})
        self.assertEqual(board.released_keys(snap), set())

    def test_a_second_label_github_refuses_puts_the_first_back(self):
        answers = release_answers()
        answers[LABEL_WORKER_12] = Refusal(502)
        answers[LABELS_12] = FakeAnswer([])
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, idle_snapshot(), NOW), [
            "#12: not released, taking the labels off failed: DELETE /repos/o/r/issues/12/labels/worker%3Anightly: "
            "HTTP 502; claimed put back"])
        self.assertEqual(opener.sent("POST"), [{"labels": ["claimed"]}])

    def test_a_label_already_gone_is_not_put_back(self):
        # GitHub's 404 says the issue no longer carries the label, so a
        # release that fails later puts back only what it took off.
        answers = release_answers()
        answers[LABEL_WORKER_12] = Refusal(404, {"message": "Label does not exist"})
        answers["POST /repos/o/r/issues/12/comments"] = Refusal(502)
        answers[COMMENTS_12] = FakeAnswer([])
        answers[LABELS_12] = FakeAnswer([])
        api, opener = client(answers)
        line, = github.coordinate(api, idle_snapshot(), NOW)
        self.assertTrue(line.endswith("HTTP 502; claimed put back"), line)
        self.assertEqual(opener.sent("POST")[1], {"labels": ["claimed"]})

    def test_labels_github_will_not_put_back_are_said(self):
        answers = release_answers()
        answers["POST /repos/o/r/issues/12/comments"] = Refusal(502)
        answers[COMMENTS_12] = Refusal(502)
        answers[LABELS_12] = Refusal(403, {"message": "Resource not accessible"})
        api, _ = client(answers)
        self.assertEqual(github.coordinate(api, idle_snapshot(), NOW), [
            "#12: not released, the comment failed: POST /repos/o/r/issues/12/comments: HTTP 502; putting claimed, "
            "worker:nightly back failed too, so they may still be off: POST /repos/o/r/issues/12/labels: HTTP 403: "
            "Resource not accessible"])

    def given_snapshot(self, names=("claimed", "worker:nightly")):
        """idle_snapshot with issue 12 given to nightly by its label alone,
        as a person gives one by hand, so it carries no todo (the
        coordinator's own tasks carry todo too, team.open_task)."""
        snap = idle_snapshot()
        snap.claim_issues["12"].labels = list(names)
        snap.issues[0].labels = list(names)
        return snap

    def test_a_release_puts_todo_on_an_issue_given_by_a_label_alone_so_the_queue_lists_it_again(self):
        """F15: the Queue lists only an issue labelled todo, so until the fix
        such an issue was shown nowhere once its claim was released."""
        answers = release_answers()
        answers[LABELS_12] = FakeAnswer([])
        snap = self.given_snapshot()
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, snap, NOW), ["#12: claim released, idle since 2026-09-18T12:00:00Z"])
        self.assertEqual([c.get_method() for c in opener.calls], ["DELETE", "DELETE", "POST", "GET", "POST"])
        self.assertEqual(opener.sent("POST")[0], {"labels": ["todo"]})
        self.assertEqual((snap.claim_issues["12"].labels, snap.issues[0].labels), (["todo"], ["todo"]))
        self.assertEqual(board.released_keys(snap), {"12"})
        self.assertIn("| #12 | Describe the board | todo |", board.render(snap).split(board.QUEUE_HEADING)[1])

    def test_an_issue_with_todo_an_epic_or_a_blocker_gets_no_label_put_on(self):
        """A blocker is listed under Blockers and dealt first without todo,
        so todo would only list it twice."""
        for names in (("claimed", "todo", "worker:nightly"), ("claimed", "epic", "worker:nightly"),
                      ("blocker", "claimed", "worker:nightly")):
            with self.subTest(labels=names):
                api, opener = client(release_answers())
                github.coordinate(api, self.given_snapshot(names), NOW)
                self.assertEqual([c.get_method() for c in opener.calls], ["DELETE", "DELETE", "GET", "POST"])

    def test_a_comment_github_refuses_takes_off_the_todo_the_release_put_on(self):
        answers = release_answers()
        answers["POST /repos/o/r/issues/12/comments"] = Refusal(502)
        answers[COMMENTS_12] = FakeAnswer([])
        answers[LABELS_12] = FakeAnswer([])
        answers["DELETE /repos/o/r/issues/12/labels/todo"] = FakeAnswer(None)
        snap = self.given_snapshot()
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, snap, NOW), [
            "#12: not released, the comment failed: POST /repos/o/r/issues/12/comments: HTTP 502; claimed, "
            "worker:nightly put back; todo taken off again"])
        self.assertEqual([f"{c.get_method()} {c.selector}" for c in opener.calls][-2:], [
            "POST /repos/o/r/issues/12/labels", "DELETE /repos/o/r/issues/12/labels/todo"])
        self.assertEqual(opener.sent("POST")[2], {"labels": ["claimed", "worker:nightly"]})
        self.assertEqual(board.released_keys(snap), set())
        self.assertEqual(snap.claim_issues["12"].labels, ["claimed", "worker:nightly"])

    def test_todo_github_refuses_puts_the_labels_back_and_takes_off_a_todo_it_may_have_written(self):
        """GitHub may write a label and lose its answer, so the run takes off
        the todo whose putting on it was told failed; a 404 says it was
        never written, which counts as taken off."""
        for todo_there, said in ((True, "; todo taken off again"), (False, "")):
            with self.subTest(todo_there=todo_there):
                answers = release_answers()
                answers[LABELS_12] = Turns(Refusal(502), FakeAnswer([]))
                if todo_there:
                    answers["DELETE /repos/o/r/issues/12/labels/todo"] = FakeAnswer(None)
                api = github.GitHub("t", "o/r", opener=InTurn(answers), sleep=lambda seconds: None)
                snap = self.given_snapshot()
                self.assertEqual(github.coordinate(api, snap, NOW), [
                    "#12: not released, putting todo on failed: POST /repos/o/r/issues/12/labels: HTTP 502; "
                    f"claimed, worker:nightly put back{said}"])
                self.assertEqual(api.opener.sent("POST"), [{"labels": ["todo"]}, {"labels": ["claimed", "worker:nightly"]}])
                self.assertEqual(board.released_keys(snap), set())

    def test_a_todo_github_will_not_take_off_again_is_said(self):
        answers = release_answers()
        answers["POST /repos/o/r/issues/12/comments"] = Refusal(502)
        answers[COMMENTS_12] = FakeAnswer([])
        answers[LABELS_12] = FakeAnswer([])
        answers["DELETE /repos/o/r/issues/12/labels/todo"] = Refusal(403, {"message": "Resource not accessible"})
        api, _ = client(answers)
        line, = github.coordinate(api, self.given_snapshot(), NOW)
        self.assertTrue(line.endswith("; claimed, worker:nightly put back; taking todo off again failed too, so it "
                                      "may still be on: DELETE /repos/o/r/issues/12/labels/todo: HTTP 403: "
                                      "Resource not accessible"), line)

    def test_a_label_github_refuses_is_a_line_and_posts_no_comment(self):
        answers = release_answers()
        answers[LABEL_CLAIMED_12] = Refusal(502)
        snap = idle_snapshot()
        api, opener = client(answers)
        line, = github.coordinate(api, snap, NOW)
        self.assertEqual(line, "#12: not released, taking the labels off failed: "
                               "DELETE /repos/o/r/issues/12/labels/claimed: HTTP 502")
        self.assertEqual(opener.sent("POST"), [])
        self.assertEqual(board.released_keys(snap), set())

    def test_a_comparison_github_refuses_still_releases(self):
        answers = release_answers()
        answers[COMPARE_12] = Refusal(500)
        api, opener = client(answers)
        self.assertEqual(len(github.coordinate(api, idle_snapshot(), NOW)), 1)
        self.assertIn("GitHub would not compare the branch", opener.sent("POST")[0]["body"])

    def overlapping(self):
        snap = idle_snapshot()
        snap.releases = {}
        snap.pulls.append(board.Pull(41, "Reword", "claude/13", "bbbb", "clean", "2026-09-20T09:00:00Z",
                                     ["docs/agents/README.md"], False))
        return snap

    def test_overlapping_work_is_told_once_and_again_only_of_new_work(self):
        answers = {
            "/repos/o/r/issues/40/comments?per_page=100": FakeAnswer([]),
            "/repos/o/r/issues/41/comments?per_page=100": FakeAnswer(
                [{"body": "<!-- sdlc-coordinator: overlap 40 -->\noverlap: told before",
                  "author_association": "NONE", "user": {"login": "github-actions[bot]"}}]),
            "POST /repos/o/r/issues/40/comments": FakeAnswer({}),
        }
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, self.overlapping(), NOW), ["#40: overlap comment posted"])
        body = opener.sent("POST")[0]["body"]
        self.assertTrue(body.startswith("<!-- sdlc-coordinator: overlap 41 -->\noverlap: other open work"), body)
        self.assertIn("- #41, a pull request marked ready: `docs/agents/README.md`", body)

    def test_a_strangers_marker_neither_silences_the_work_nor_stops_the_run(self):
        # The permission GitHub gives for the stranger is asked once, and
        # says no; a marker of 4,301 digits raised in int() before.
        answers = {
            "/repos/o/r/issues/40/comments?per_page=100": FakeAnswer([
                {"body": "<!-- sdlc-coordinator: overlap 41 -->", "author_association": "NONE",
                 "user": {"login": "stranger"}},
                {"body": "<!-- sdlc-coordinator: overlap " + "4" * 4301 + " -->", "author_association": "NONE",
                 "user": {"login": "stranger"}}]),
            "/repos/o/r/issues/41/comments?per_page=100": FakeAnswer([]),
            "/repos/o/r/collaborators/stranger/permission": FakeAnswer({"permission": "read"}),
            "POST /repos/o/r/issues/40/comments": FakeAnswer({}),
            "POST /repos/o/r/issues/41/comments": FakeAnswer({}),
        }
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, self.overlapping(), NOW),
                         ["#40: overlap comment posted", "#41: overlap comment posted"])
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertEqual(asked.count("/repos/o/r/collaborators/stranger/permission"), 1)

    def test_work_whose_comments_github_will_not_give_is_told_nothing(self):
        answers = {
            "/repos/o/r/issues/40/comments?per_page=100": Refusal(502),
            "/repos/o/r/issues/41/comments?per_page=100": FakeAnswer([]),
            "POST /repos/o/r/issues/41/comments": Refusal(403, {"message": "Locked"}),
        }
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, self.overlapping(), NOW), [
            "#40: overlaps not told, its comments were not read: GET /repos/o/r/issues/40/comments: HTTP 502",
            "#41: overlap comment failed: POST /repos/o/r/issues/41/comments: HTTP 403: Locked"])
        self.assertEqual(len(opener.sent("POST")), 1)

    def test_nothing_due_asks_nothing(self):
        snap, answers = fixture_answers()
        api, opener = client(answers)
        self.assertEqual(github.coordinate(api, board.Snapshot.from_dict(snap), NOW), [])
        self.assertEqual(opener.calls, [])

    def test_one_client_asks_about_an_author_once_through_a_lost_release_comment_and_the_overlaps_too(self):
        """F19's review: landed_release, which reads an issue's comments
        again after a refused release comment, and tell_overlaps, which
        reads the overlap markers, each built a permission cache of their
        own, so a board run asked GitHub twice about a login collect had
        asked about; may_write takes the client's cache when its caller
        passes none, so one run asks once."""
        permission = "/repos/o/r/collaborators/sam/permission"
        answers = release_answers()
        answers["POST /repos/o/r/issues/12/comments"] = Refusal(502)
        answers[COMMENTS_12] = FakeAnswer([dict(RELEASE_COMMENT, author_association="NONE", user={"login": "sam"})])
        answers[permission] = FakeAnswer({"permission": "write", "role_name": "write"})
        snap = idle_snapshot()
        api, opener = client(answers)
        self.assertTrue(api.asked("sam"))
        line, = github.coordinate(api, snap, NOW)
        self.assertIn("released", line)
        self.assertEqual(board.released_keys(snap), {"12"}, "the comment GitHub wrote and lost its answer to counts")
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertEqual(asked.count(permission), 1)
        answers = {
            "/repos/o/r/issues/40/comments?per_page=100": FakeAnswer([
                {"body": "<!-- sdlc-coordinator: overlap 41 -->", "author_association": "NONE",
                 "user": {"login": "sam"}}]),
            "/repos/o/r/issues/41/comments?per_page=100": FakeAnswer([]),
            permission: FakeAnswer({"permission": "write", "role_name": "write"}),
            "POST /repos/o/r/issues/41/comments": FakeAnswer({}),
        }
        api, opener = client(answers)
        self.assertTrue(api.asked("sam"))
        self.assertEqual(github.coordinate(api, self.overlapping(), NOW), ["#41: overlap comment posted"])
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertEqual(asked.count(permission), 1)


class Repository(FakeOpener):
    """GitHub as board runs see it, kept from one run to the next: issue 12
    with its labels and comments, claimed by nightly, whose branch claude/12
    has had no commit since two days before NOW and no pull request. The
    calls that change it change it: a comment is posted at the clock's
    time, a label taken off (404 for one that is gone, as GitHub answers)
    or put on, a branch renamed. A call named in refuse gets its refusal
    once and then works, as a failure that passes does; one named in lose
    is made and then answered with the refusal, once, as when GitHub writes
    a comment and its answer is lost."""

    def __init__(self, refuse=None, lose=None, claim=IDLE_CLAIM):
        super().__init__({
            "/repos/o/r/pulls?state=open&per_page=100": FakeAnswer([]),
            CLOSED_FIRST: FakeAnswer([]),
            f"/repos/o/r/git/commits/{HEAD_12}": FakeAnswer({"committer": {"date": "2026-09-18T12:00:00Z"}}),
            "/repos/o/r/contents/docs/agents/claims/12.md?ref=claude%2F12": FakeAnswer(
                {"content": base64.b64encode(claim.encode()).decode()}),
            COMPARE_12: release_answers()[COMPARE_12],
        })
        self.labels = ["claimed", "todo", "worker:nightly"]
        self.comments = []
        self.branches = {"main": "0123abc", "claude/12": HEAD_12}
        self.refuse, self.lose = dict(refuse or {}), dict(lose or {})
        self.clock = NOW

    def open(self, call, timeout=None):
        send_line(call)
        where = call.selector
        asked = where if call.get_method() == "GET" else f"{call.get_method()} {where}"
        if asked in self.refuse:
            self.calls.append(call)
            raise self.refuse.pop(asked).raised(call.full_url)
        answer = self.answer(asked, call)
        if asked in self.lose:
            self.calls.append(call)
            raise self.lose.pop(asked).raised(call.full_url)
        if answer is None:
            return super().open(call, timeout)
        self.calls.append(call)
        if isinstance(answer, Refusal):
            raise answer.raised(call.full_url)
        return answer

    def answer(self, asked, call):
        """The answer that reads or changes the state, or None for the fixed
        ones the FakeOpener holds."""
        if asked == "/repos/o/r/issues?state=open&per_page=100":
            return FakeAnswer([{"number": 12, "title": "Describe the board", "state": "open",
                                "labels": [{"name": name} for name in self.labels]}])
        if asked == "/repos/o/r/branches?per_page=100":
            return FakeAnswer([{"name": name, "commit": {"sha": sha}} for name, sha in self.branches.items()])
        if asked == COMMENTS_12:
            return FakeAnswer(self.comments)
        if asked == "POST /repos/o/r/issues/12/comments":
            body = json.loads(call.data)["body"]
            if len(body) > 65536:
                return Refusal(422, {"message": "Body is too long (maximum is 65536 characters)"})
            posted = {"body": body, "created_at": self.clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "author_association": "NONE", "user": {"login": "github-actions[bot]"}}
            self.comments.append(posted)
            return FakeAnswer(posted)
        if asked == LABELS_12:
            self.labels += [name for name in json.loads(call.data)["labels"] if name not in self.labels]
            return FakeAnswer([{"name": name} for name in self.labels])
        if asked.startswith("DELETE /repos/o/r/issues/12/labels/"):
            name = urllib.parse.unquote(asked.rsplit("/", 1)[1])
            if name not in self.labels:
                return Refusal(404, {"message": "Label does not exist"})
            self.labels.remove(name)
            return FakeAnswer([])
        if asked.startswith(REF):
            sha = self.branches.get(urllib.parse.unquote(asked[len(REF):]))
            return FakeAnswer({"object": {"sha": sha}}) if sha else Refusal(404)
        if asked == RENAME_12:
            new = json.loads(call.data)["new_name"]
            self.branches[new] = self.branches.pop("claude/12")
            return FakeAnswer({"name": new})
        return None

    def release_comments(self):
        return [c for c in self.comments if claims.is_release_comment(c)]


def board_run(api, repository, now):
    """One board run as cli.board_live_run makes it: the snapshot, the
    coordinator, the page, then the renames; the snapshot, the page and
    the lines."""
    repository.clock = now
    snap = github.snapshot(api, "abc", "main", now)
    lines = github.coordinate(api, snap, now)
    page = board.render(snap)
    return snap, page, lines + github.release_claims(api, snap)


class ReleaseAcrossRuns(unittest.TestCase):
    """A release is two acts, the labels and the comment; a run that fails
    between them puts back what it took off, so the next run with no
    failure releases the claim whole, and a holder who comes back first
    keeps it as it was; a release a person undid stays undone."""

    def runs(self, refuse=None):
        repository = Repository(refuse)
        api = github.GitHub("t", "o/r", opener=repository, sleep=lambda seconds: None)
        _, _, first = board_run(api, repository, NOW)
        snap, page, second = board_run(api, repository, NOW + timedelta(hours=1))
        return repository, snap, page, first, second

    def assert_released(self, repository, snap, page):
        self.assertEqual(repository.labels, ["todo"])
        self.assertEqual(len(repository.release_comments()), 1)
        self.assertEqual(board.released_keys(snap), {"12"})
        self.assertIn("| #12 | Describe the board | todo |", page.split(board.QUEUE_HEADING)[1])
        self.assertIn("released/12-aaaaaaa", repository.branches)
        self.assertNotIn("claude/12", repository.branches)

    def test_a_release_whose_label_did_not_come_off_is_finished_by_the_next_run(self):
        repository, snap, page, first, second = self.runs({LABEL_CLAIMED_12: Refusal(502)})
        self.assertIn("HTTP 502", first[0])
        self.assert_released(repository, snap, page)

    def test_a_release_whose_second_label_did_not_come_off_is_finished_by_the_next_run(self):
        # With claimed off and worker:nightly left, the issue would sit
        # under Given to workers for a holder who has gone.
        repository, snap, page, first, second = self.runs({LABEL_WORKER_12: Refusal(502)})
        self.assertIn("HTTP 502", first[0])
        self.assert_released(repository, snap, page)

    def test_a_release_whose_comment_failed_is_finished_by_the_next_run(self):
        repository, snap, page, first, second = self.runs(
            {"POST /repos/o/r/issues/12/comments": Refusal(502)})
        self.assertIn("HTTP 502", first[0])
        self.assert_released(repository, snap, page)

    def test_a_release_whose_labels_did_not_go_back_on_is_finished_by_the_next_run(self):
        repository, snap, page, first, second = self.runs(
            {"POST /repos/o/r/issues/12/comments": Refusal(502), LABELS_12: Refusal(502)})
        self.assertIn("back failed too, so they may still be off", first[0])
        self.assert_released(repository, snap, page)

    def test_a_holder_who_comes_back_after_a_failed_comment_keeps_the_claim_and_its_labels(self):
        # Had the labels stayed off, the claim would be held with none, and
        # the clerk, which finds a worker's claim by its label, would not
        # send nightly to it again.
        repository = Repository({"POST /repos/o/r/issues/12/comments": Refusal(502)})
        repository.answers["/repos/o/r/git/commits/bbbbbbb2222222"] = FakeAnswer(
            {"committer": {"date": "2026-09-20T12:30:00Z"}})
        repository.answers["/repos/o/r/pulls?state=open&head=o%3Aclaude%2F12"] = FakeAnswer([])
        api = github.GitHub("t", "o/r", opener=repository, sleep=lambda seconds: None)
        _, _, first = board_run(api, repository, NOW)
        self.assertEqual(first, ["#12: not released, the comment failed: POST /repos/o/r/issues/12/comments: "
                                 "HTTP 502; claimed, worker:nightly put back"])
        repository.branches["claude/12"] = "bbbbbbb2222222"
        snap, page, second = board_run(api, repository, NOW + timedelta(hours=1))
        self.assertEqual(second, [])
        self.assertEqual(sorted(repository.labels), ["claimed", "todo", "worker:nightly"])
        self.assertEqual(repository.release_comments(), [])
        self.assertIn("| #12 | claude/12 | nightly |", page)
        self.assertEqual(team.next_for(api, "nightly"), "carry 12")

    def test_a_comment_github_wrote_and_lost_the_answer_to_is_a_whole_release(self):
        repository = Repository(lose={"POST /repos/o/r/issues/12/comments": Refusal(502)})
        api = github.GitHub("t", "o/r", opener=repository, sleep=lambda seconds: None)
        snap, page, lines = board_run(api, repository, NOW)
        self.assertEqual(lines, [
            "#12: claim released, idle since 2026-09-18T12:00:00Z (GitHub's answer to the comment was lost, "
            "POST /repos/o/r/issues/12/comments: HTTP 502, but the comment is there)",
            "#12: claim released; claude/12 renamed to released/12-aaaaaaa"])
        self.assert_released(repository, snap, page)

    def test_a_plan_longer_than_github_takes_in_a_comment_is_cut_and_the_claim_released(self):
        # Quoted whole, the plan made a comment GitHub refuses with 422, so
        # the claim was never released.
        plan = IDLE_CLAIM.replace("Describe the board.", "Describe the board. " + "word " * 14000)
        repository = Repository(claim=plan)
        api = github.GitHub("t", "o/r", opener=repository, sleep=lambda seconds: None)
        snap, page, lines = board_run(api, repository, NOW)
        self.assertEqual(lines[0], "#12: claim released, idle since 2026-09-18T12:00:00Z")
        self.assert_released(repository, snap, page)
        comment, = repository.release_comments()
        self.assertIn("The claim's plan, cut short (docs/agents/claims/12.md on released/12-aaaaaaa has it whole): "
                      "Describe the board. word word", comment["body"])
        self.assertLess(len(comment["body"]), 8000)

    def test_a_release_by_hand_comment_first_is_the_one_release_comment(self):
        # Taking a label off starts a board run; the comment made first
        # keeps that run's coordinator from releasing the claim again.
        repository = Repository()
        repository.comments.append({"body": claims.RELEASE_MARKER + "\nreleased by hand", "created_at":
                                    "2026-09-20T11:59:00Z", "author_association": "MEMBER", "user": {"login": "owner"}})
        repository.labels = ["todo"]
        api = github.GitHub("t", "o/r", opener=repository, sleep=lambda seconds: None)
        snap, page, lines = board_run(api, repository, NOW)
        self.assertEqual(lines, ["#12: claim released; claude/12 renamed to released/12-aaaaaaa"])
        self.assertEqual([c["user"]["login"] for c in repository.release_comments()], ["owner"])

    def test_a_label_already_gone_counts_as_taken_off(self):
        # A person took worker:nightly off between the snapshot and the
        # release: GitHub answers 404, and the release goes on.
        repository = Repository()
        api = github.GitHub("t", "o/r", opener=repository, sleep=lambda seconds: None)
        snap = github.snapshot(api, "abc", "main", NOW)
        repository.labels.remove("worker:nightly")
        self.assertEqual(github.coordinate(api, snap, NOW), ["#12: claim released, idle since 2026-09-18T12:00:00Z"])
        self.assertEqual((repository.labels, len(repository.release_comments())), (["todo"], 1))
        self.assertEqual(board.released_keys(snap), {"12"})

    def test_putting_claimed_back_undoes_a_release_for_good(self):
        repository, snap, page, first, second = self.runs()
        self.assertEqual(first[0], "#12: claim released, idle since 2026-09-18T12:00:00Z")
        repository.labels = ["claimed", "todo", "worker:nightly"]
        repository.branches["claude/12"] = repository.branches.pop("released/12-aaaaaaa")
        api = github.GitHub("t", "o/r", opener=repository, sleep=lambda seconds: None)
        snap, page, third = board_run(api, repository, NOW + timedelta(days=3))
        self.assertEqual(third, [])
        self.assertEqual(repository.labels, ["claimed", "todo", "worker:nightly"])
        self.assertEqual(len(repository.release_comments()), 1)
        self.assertEqual(board.released_keys(snap), set())
        self.assertIn("| #12 | claude/12 | nightly |", page)


PULL_LIST = "/repos/o/r/pulls?state=open&head=o%3Aclaude%2F7"


def pull_answers(state="clean", mergeable=True, sha="sha7", draft=False):
    """The open pull request #507 from claude/7, as GitHub details it."""
    return {
        PULL_LIST: FakeAnswer([{"number": 507}]),
        "/repos/o/r/pulls/507": FakeAnswer({"number": 507, "draft": draft, "mergeable": mergeable,
                                            "mergeable_state": state, "head": {"ref": "claude/7", "sha": sha},
                                            "base": {"ref": "main"}}),
    }


class TeamClient(unittest.TestCase):
    """The methods team.Client lists, as REST calls."""

    def test_the_adapter_is_a_client(self):
        self.assertIsInstance(client({})[0], team.Client)

    def test_open_pull_asks_by_head_and_returns_the_detail(self):
        api, _ = client(pull_answers(state="behind"))
        self.assertEqual(api.open_pull("claude/7")["mergeable_state"], "behind")
        api, _ = client({PULL_LIST: FakeAnswer([])})
        self.assertIsNone(api.open_pull("claude/7"))

    def test_open_pull_leaves_out_a_pull_request_from_a_fork_the_owner_s_filter_matches(self):
        """A fork in the same organization shares the owner the list's head
        filter names, so the list may give its pull request from a branch
        named like the claim's; the detail asked for is the repository's own
        (F17's review)."""
        fork = {"number": 506, "head": {"ref": "claude/7", "repo": {"full_name": "o/r-fork"}},
                "base": {"ref": "main", "repo": {"full_name": "o/r"}}}
        own = {"number": 507, "head": {"ref": "claude/7", "repo": {"full_name": "o/r"}},
               "base": {"ref": "main", "repo": {"full_name": "o/r"}}}
        answers = pull_answers(state="behind")
        answers[PULL_LIST] = FakeAnswer([fork, own])
        api, opener = client(answers)
        self.assertEqual(api.open_pull("claude/7")["number"], 507)
        self.assertNotIn("/repos/o/r/pulls/506", [c.full_url.replace(github.API, "") for c in opener.calls])
        answers[PULL_LIST] = FakeAnswer([fork])
        api, _ = client(answers)
        self.assertIsNone(api.open_pull("claude/7"))

    def test_open_pull_leaves_out_a_stranger_s_pull_request_from_the_claim_s_branch(self):
        """The clerk's read of a claim's pull request (team.Snapshot.released,
        F18) agrees with the board: one that someone who may not write
        opened from the branch here is no claim's (F19), so it neither
        finishes the claim nor stands in for its own, at one permission
        call a client; a call GitHub refuses keeps it the claim's, as the
        board keeps it."""
        theirs = {"number": 506, "head": {"ref": "claude/7", "repo": {"full_name": "o/r"}},
                  "base": {"ref": "main", "repo": {"full_name": "o/r"}}, "user": {"login": "sam"},
                  "author_association": "NONE"}
        own = {"number": 507, "head": {"ref": "claude/7", "repo": {"full_name": "o/r"}},
               "base": {"ref": "main", "repo": {"full_name": "o/r"}}, "user": {"login": "alice"},
               "author_association": "COLLABORATOR"}
        permission = "/repos/o/r/collaborators/sam/permission"
        answers = pull_answers(state="behind")
        answers[PULL_LIST] = FakeAnswer([theirs, own])
        answers[permission] = FakeAnswer({"permission": "read", "role_name": "read"})
        api, opener = client(answers)
        self.assertEqual(api.open_pull("claude/7")["number"], 507)
        self.assertEqual(api.open_pull("claude/7")["number"], 507)
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertNotIn("/repos/o/r/pulls/506", asked)
        self.assertEqual(asked.count(permission), 1)
        answers[PULL_LIST] = FakeAnswer([theirs])
        api, _ = client(answers)
        self.assertIsNone(api.open_pull("claude/7"))
        answers[permission] = Refusal(403, {"message": "Must have push access"})
        answers["/repos/o/r/pulls/506"] = FakeAnswer({"number": 506, "draft": False, "mergeable": True,
                                                      "mergeable_state": "clean",
                                                      "head": {"ref": "claude/7", "sha": "sha6"}, "base": {"ref": "main"}})
        api, _ = client(answers)
        self.assertEqual(api.open_pull("claude/7")["number"], 506)

    def test_open_pull_from_a_branch_that_is_no_claim_s_is_whoever_s_at_no_call(self):
        """The stranger filter is for claim branches alone, as
        mark_strangers's is (F19's review): another branch's pull request
        is no claim's whoever opened it, so the first open one is the
        branch's, and nobody's permission is asked."""
        theirs = {"number": 508, "head": {"ref": "feature/x", "repo": {"full_name": "o/r"}},
                  "base": {"ref": "main", "repo": {"full_name": "o/r"}}, "user": {"login": "sam"},
                  "author_association": "NONE"}
        api, opener = client({"/repos/o/r/pulls?state=open&head=o%3Afeature%2Fx": FakeAnswer([theirs]),
                              "/repos/o/r/pulls/508": FakeAnswer(dict(theirs, draft=True, mergeable_state="clean"))})
        self.assertEqual(api.open_pull("feature/x")["number"], 508)
        self.assertFalse(any("/collaborators/" in c.full_url for c in opener.calls))

    def test_check_runs_reads_every_page(self):
        first = "/repos/o/r/commits/sha7/check-runs?per_page=100"
        api, _ = client({
            first: FakeAnswer({"check_runs": [{"name": "lint"}]}, {"Link": f'<{github.API}{first}&page=2>; rel="next"'}),
            first + "&page=2": FakeAnswer({"check_runs": [{"name": "check"}]}),
        })
        self.assertEqual([r["name"] for r in api.check_runs("sha7")], ["lint", "check"])

    def test_create_label_lets_an_existing_label_be(self):
        api, opener = client({"POST /repos/o/r/labels": Refusal(422, {"message": "already_exists"})})
        api.create_label("agent:w1", "c5def5")
        self.assertEqual(opener.sent("POST"), [{"name": "agent:w1", "color": "c5def5"}])
        api, _ = client({"POST /repos/o/r/labels": Refusal(403)})
        with self.assertRaises(github.GitHubError):
            api.create_label("agent:w1", "c5def5")

    def test_the_writes_go_where_the_clerk_wrote_them(self):
        api, opener = client({
            "POST /repos/o/r/issues": FakeAnswer({"number": 101, "id": 9101}),
            "POST /repos/o/r/issues/1/sub_issues": FakeAnswer({}),
            "PATCH /repos/o/r/issues/1": FakeAnswer({}),
        })
        self.assertEqual(api.create_issue("Parser", "Epic: #1", ["todo", "agent:w1"])["number"], 101)
        api.add_sub_issue(1, 9101)
        api.set_body(1, "new body")
        api.remove_label(1, "todo")
        self.assertEqual([(c.get_method(), c.full_url.replace(github.API, "")) for c in opener.calls], [
            ("POST", "/repos/o/r/issues"), ("POST", "/repos/o/r/issues/1/sub_issues"),
            ("PATCH", "/repos/o/r/issues/1"), ("DELETE", "/repos/o/r/issues/1/labels/todo"),
        ])
        self.assertEqual(opener.sent("POST")[0], {"title": "Parser", "body": "Epic: #1", "labels": ["todo", "agent:w1"]})
        self.assertEqual(opener.sent("POST")[1], {"sub_issue_id": 9101})
        self.assertEqual(opener.sent("PATCH"), [{"body": "new body"}])

    def test_the_clerk_answers_over_rest(self):
        api, _ = client({
            "/repos/o/r/issues?state=open&per_page=100": FakeAnswer([
                {"number": 4, "state": "open", "labels": [{"name": "todo"}]},
                {"number": 5, "state": "open", "labels": [{"name": "todo"}], "pull_request": {}},
            ]),
            "/repos/o/r/branches?per_page=100": FakeAnswer([{"name": "main"}]),
        })
        self.assertEqual(team.next_for(api, "w1"), "take 4")

    def test_the_clerk_reads_whose_a_claim_is_from_its_claim_file_over_rest(self):
        """F18: #7 still carries a's label, but the claim file b pushed names
        b, so b carries the claim and a is told nothing of it; the file is
        read once for the look, through the contents API, as the board
        reads it."""
        claim = "# Claim: #7\n\n- Worker: b\n- Branch: claude/7\n"
        path = "/repos/o/r/contents/docs/agents/claims/7.md?ref=claude%2F7"
        api, opener = client({
            "/repos/o/r/issues?state=open&per_page=100": FakeAnswer([
                {"number": 7, "state": "open", "labels": [{"name": "todo"}, {"name": "worker:a"}]}]),
            "/repos/o/r/branches?per_page=100": FakeAnswer([{"name": "main"}, {"name": "claude/7"}]),
            path: FakeAnswer({"type": "file", "content": base64.b64encode(claim.encode()).decode()}),
            PULL_LIST: FakeAnswer([]),
        })
        self.assertEqual(api.claim_text("claude/7"), claim)
        self.assertEqual(team.deals(api, ["a", "b"]), {"a": "none", "b": "carry 7"})
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertEqual(asked.count(path), 2)

    def test_a_claim_file_github_refuses_stops_the_look_and_one_it_has_not_got_leaves_the_label_to_name(self):
        """F18's second review: a read GitHub refuses stops the look, as
        every other read of the look does, rather than guess whose a claim
        is from the labels and send a worker to another's, the finding over
        again; a 404, no file on the branch yet, is no refusal, and the
        issue's label then names the holder."""
        path = "/repos/o/r/contents/docs/agents/claims/7.md?ref=claude%2F7"
        answers = {
            "/repos/o/r/issues?state=open&per_page=100": FakeAnswer([
                {"number": 7, "state": "open", "labels": [{"name": "todo"}, {"name": "worker:a"}]}]),
            "/repos/o/r/branches?per_page=100": FakeAnswer([{"name": "main"}, {"name": "claude/7"}]),
            PULL_LIST: FakeAnswer([]),
        }
        for code in (403, 500):
            api, opener = client({**answers, path: Refusal(code)})
            with self.assertRaises(github.GitHubError):
                team.deals(api, ["a", "b"])
            asked = [c.full_url.replace(github.API, "") for c in opener.calls]
            self.assertEqual(asked[-1], path, f"nothing is read after the refusal ({code})")
        api, _ = client({**answers, path: Refusal(404)})
        self.assertEqual(team.deals(api, ["a", "b"]), {"a": "carry 7", "b": "none"})


class ClaimReleased(unittest.TestCase):
    """github.claim_released, what the clerk asks of a claim whose labels and
    pull request leave a release possible (team.Snapshot.released, F18's
    review): the branch's head and its commit, and only once that commit is
    more than a day old the issue's comments, with a permission asked per
    author no association vouches for, as the board reads a release."""

    REF_12 = "/repos/o/r/git/ref/heads/claude/12"
    COMMIT_12 = f"/repos/o/r/git/commits/{HEAD_12}"
    NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    def answers(self, comments, committed="2026-09-18T12:00:00Z"):
        return {
            self.REF_12: FakeAnswer({"object": {"sha": HEAD_12}}),
            self.COMMIT_12: FakeAnswer({"sha": HEAD_12, "committer": {"date": committed}}),
            COMMENTS_12: FakeAnswer(comments),
        }

    def asked(self, opener):
        return [c.full_url.replace(github.API, "") for c in opener.calls]

    def test_a_trusted_release_comment_after_a_day_idle_counts_and_costs_three_calls(self):
        api, opener = client(self.answers([RELEASE_COMMENT]))
        self.assertTrue(github.claim_released(api, "claude/12", self.NOW))
        self.assertEqual(self.asked(opener), [self.REF_12, self.COMMIT_12, COMMENTS_12])
        # The Client's method, on the clock: the commit is long past.
        self.assertTrue(client(self.answers([RELEASE_COMMENT]))[0].released("claude/12"))

    def test_a_branch_github_does_not_know_is_no_released_claim_and_costs_one_call(self):
        api, opener = client({})
        self.assertFalse(github.claim_released(api, "claude/12", self.NOW))
        self.assertEqual(self.asked(opener), [self.REF_12])

    def test_a_commit_within_the_day_asks_no_comments(self):
        api, opener = client(self.answers([RELEASE_COMMENT], committed="2026-09-20T01:00:00Z"))
        self.assertFalse(github.claim_released(api, "claude/12", self.NOW))
        self.assertEqual(self.asked(opener), [self.REF_12, self.COMMIT_12])

    def test_a_comment_sooner_than_a_day_after_the_commit_or_before_it_releases_nothing(self):
        for created in ("2026-09-19T11:00:00Z", "2026-09-18T11:00:00Z"):
            api, _ = client(self.answers([dict(RELEASE_COMMENT, created_at=created)]))
            self.assertFalse(github.claim_released(api, "claude/12", self.NOW), created)

    def test_an_author_no_association_vouches_for_is_asked_once_and_a_refusal_counts_as_no(self):
        stranger = dict(RELEASE_COMMENT, author_association="NONE", user={"login": "sam"})
        permission = "/repos/o/r/collaborators/sam/permission"
        answers = self.answers([stranger, dict(stranger, created_at="2026-09-20T10:00:00Z")])
        answers[permission] = FakeAnswer({"permission": "write", "role_name": "write"})
        api, opener = client(answers)
        self.assertTrue(github.claim_released(api, "claude/12", self.NOW))
        self.assertEqual(self.asked(opener).count(permission), 1)
        api, opener = client(self.answers([stranger]))
        self.assertFalse(github.claim_released(api, "claude/12", self.NOW))
        self.assertIn(permission, self.asked(opener))

    def test_one_client_asks_about_an_author_once_however_many_claims_it_reads(self):
        """The clerk reads each candidate claim's release with one client
        (team.Snapshot.released), whose cache of who may write
        (GitHub.asked) the board's marking of strangers shares (F19), so an
        author is asked about once a look."""
        stranger = dict(RELEASE_COMMENT, author_association="NONE", user={"login": "sam"})
        permission = "/repos/o/r/collaborators/sam/permission"
        answers = self.answers([stranger])
        answers[permission] = FakeAnswer({"permission": "write", "role_name": "write"})
        api, opener = client(answers)
        self.assertEqual([github.claim_released(api, "claude/12", self.NOW) for _ in range(2)], [True, True])
        self.assertEqual(self.asked(opener).count(permission), 1)


def world(answers, who_merges=gate.GATE, waits=None):
    api, opener = client(answers, waits)
    clock = lambda: datetime(2026, 10, 10, tzinfo=timezone.utc)  # noqa: E731
    return github.GitHubWorld(api, gate.Policy(who_merges=who_merges), clock=clock), opener


REF = "/repos/o/r/git/ref/heads/"
COMPARE = "/repos/o/r/compare/main...sha7"


class World(unittest.TestCase):
    def test_the_adapter_is_a_world(self):
        self.assertIsInstance(world({})[0], gate.World)

    def test_shas_come_from_the_refs_and_a_missing_branch_is_empty(self):
        place, _ = world({REF + "main": FakeAnswer({"object": {"sha": "m0"}}),
                          REF + "claude/7": FakeAnswer({"object": {"sha": "sha7"}})})
        self.assertEqual((place.main_sha(), place.branch_sha("claude/7"), place.branch_sha("claude/8")),
                         ("m0", "sha7", ""))

    def test_the_default_branch_is_the_policys_unless_named(self):
        api, _ = client({})
        self.assertEqual(github.GitHubWorld(api, gate.Policy(default_branch="trunk")).default_branch, "trunk")
        self.assertEqual(github.GitHubWorld(api, gate.Policy(), "dev").default_branch, "dev")

    def test_standing_reads_mergeable_state_as_the_board_does(self):
        for state, word in (("clean", "clean"), ("has_hooks", "clean"), ("unstable", "clean"),
                            ("behind", "behind"), ("dirty", "conflicting"), ("unknown", "unknown")):
            place, _ = world(pull_answers(state=state))
            self.assertEqual(place.standing("claude/7"), word, state)

    def test_its_standing_never_vouches_a_clean_branch_is_level(self):
        """GitHub calls a branch clean that a rule not asking for an
        up-to-date branch lets merge while behind (F16), so the standing the
        gate reads, which it prefers to the word, keeps GitHub's word and
        says the branch may not be level."""
        for state, word in (("clean", "clean"), ("behind", "behind"), ("dirty", "conflicting")):
            place, _ = world(pull_answers(state=state))
            self.assertEqual(gate.standing_of(place, "claude/7"), gate.Standing(word, level=False), state)

    def test_a_blocked_or_draft_pull_is_read_from_the_comparison(self):
        for state in ("blocked", "draft"):
            for behind_by, word in ((0, "clean"), (2, "behind")):
                place, _ = world({**pull_answers(state=state), COMPARE: FakeAnswer({"behind_by": behind_by})})
                self.assertEqual(place.standing("claude/7"), word, (state, behind_by))
            place, opener = world(pull_answers(state=state, mergeable=False))
            self.assertEqual(place.standing("claude/7"), "conflicting")
            self.assertNotIn(COMPARE, [c.full_url.replace(github.API, "") for c in opener.calls])

    def test_standing_without_a_pull_or_a_settled_mergeable_is_unknown(self):
        place, _ = world({PULL_LIST: FakeAnswer([])})
        self.assertEqual(place.standing("claude/7"), "unknown")
        waits = []
        place, _ = world(pull_answers(state="blocked", mergeable=None), waits=waits)
        self.assertEqual(place.standing("claude/7"), "unknown")
        self.assertEqual(waits, [github.MERGEABLE_RETRY_SECONDS])

    def test_behind_by_is_the_modules_through_the_same_compare(self):
        """The world's behind_by is the module's, so the gate and the board
        read one comparison the same way; the world's default branch stands
        in where the pull request names no base, and a pull request with no
        head has nothing to compare, so neither asks GitHub for it."""
        dev = "/repos/o/r/compare/dev...sha7"
        api, opener = client({COMPARE: FakeAnswer({"behind_by": 3}), dev: FakeAnswer({"behind_by": 4})})
        place = github.GitHubWorld(api, gate.Policy(), "dev")
        with_base, without = {"base": {"ref": "main"}, "head": {"sha": "sha7"}}, {"head": {"sha": "sha7"}}
        self.assertEqual((place.behind_by(with_base), github.behind_by(api, with_base, "dev")), (3, 3))
        self.assertEqual((place.behind_by(without), github.behind_by(api, without, "dev")), (4, 4))
        self.assertIsNone(place.behind_by({"base": {"ref": "main"}}))
        self.assertEqual(compares_asked(opener), [COMPARE, COMPARE, dev, dev])

    def test_check_reads_the_required_check_on_the_head(self):
        runs = "/repos/o/r/commits/sha7/check-runs?per_page=100"
        cases = (
            ([], "none"),
            ([{"name": "lint", "status": "completed", "conclusion": "failure"}], "none"),
            ([{"name": "check", "status": "in_progress", "conclusion": None}], "pending"),
            ([{"name": "check", "status": "completed", "conclusion": "success"}], "green"),
            ([{"name": "check", "status": "completed", "conclusion": "skipped"}], "green"),
            ([{"name": "check", "status": "completed", "conclusion": "cancelled"}], "red"),
            ([{"name": "check", "status": "queued", "conclusion": None},
              {"name": "check", "status": "completed", "conclusion": "timed_out"}], "red"),
        )
        for found, word in cases:
            place, _ = world({REF + "claude/7": FakeAnswer({"object": {"sha": "sha7"}}),
                              runs: FakeAnswer({"total_count": len(found), "check_runs": found})})
            self.assertEqual(place.check("claude/7"), word, found)
        place, _ = world({})
        self.assertEqual(place.check("claude/7"), "none")

    def test_merge_goes_through_the_merge_api_with_the_head_it_saw(self):
        place, opener = world({**pull_answers(), "PUT /repos/o/r/pulls/507/merge": FakeAnswer({"sha": "m1", "merged": True})})
        self.assertEqual(place.merge("claude/7"), gate.MergeResult("m1"))
        self.assertEqual(opener.sent("PUT"), [{"sha": "sha7", "merge_method": "merge"}])

    def test_merge_is_not_the_worlds_where_the_owner_or_auto_merge_merges(self):
        for who in (gate.OWNER, gate.AUTO_MERGE):
            place, opener = world(pull_answers(), who_merges=who)
            with self.assertRaises(gate.NotAllowed):
                place.merge("claude/7")
            self.assertEqual(opener.calls, [])

    def test_a_refused_merge_is_main_moved_when_the_branch_no_longer_fits(self):
        put = "PUT /repos/o/r/pulls/507/merge"
        place, _ = world({**pull_answers(state="behind"), put: Refusal(405, {"message": "Base branch was modified"})})
        with self.assertRaises(gate.MainMoved) as caught:
            place.merge("claude/7")
        self.assertIn("Base branch was modified", caught.exception.reason)
        place, _ = world({**pull_answers(state="clean"), put: Refusal(405, {"message": "Review required"})})
        with self.assertRaises(gate.MergeRefused) as refused:
            place.merge("claude/7")
        self.assertIn("Review required", refused.exception.reason)
        place, _ = world({**pull_answers(), put: Refusal(500)})
        with self.assertRaises(gate.GateFailed):
            place.merge("claude/7")
        place, _ = world({PULL_LIST: FakeAnswer([])})
        with self.assertRaises(gate.MergeRefused):
            place.merge("claude/7")

    def test_a_stranger_s_pull_request_from_a_claim_s_branch_has_no_standing_and_is_never_merged(self):
        """The gate's world reads a branch's pull request through
        pull_detail, so a pull request someone who may not write opened
        from a claim's branch is no claim's here either (F19's review): a
        branch with only such a pull request has no standing and nothing to
        merge, and beside the claim's own, the own one is merged."""
        theirs = {"number": 506, "head": {"ref": "claude/7", "repo": {"full_name": "o/r"}},
                  "base": {"ref": "main", "repo": {"full_name": "o/r"}}, "user": {"login": "sam"},
                  "author_association": "NONE"}
        own = {"number": 507, "head": {"ref": "claude/7", "repo": {"full_name": "o/r"}},
               "base": {"ref": "main", "repo": {"full_name": "o/r"}}, "user": {"login": "alice"},
               "author_association": "COLLABORATOR"}
        permission = "/repos/o/r/collaborators/sam/permission"
        put = "PUT /repos/o/r/pulls/507/merge"
        answers = {**pull_answers(), permission: FakeAnswer({"permission": "read", "role_name": "read"}),
                   put: FakeAnswer({"sha": "m1", "merged": True}), PULL_LIST: FakeAnswer([theirs])}
        place, opener = world(answers)
        self.assertEqual(place.standing("claude/7"), gate.UNKNOWN)
        with self.assertRaises(gate.MergeRefused):
            place.merge("claude/7")
        asked = [c.full_url.replace(github.API, "") for c in opener.calls]
        self.assertNotIn("/repos/o/r/pulls/506", asked)
        self.assertEqual(asked.count(permission), 1, "asked once a client")
        place, opener = world({**answers, PULL_LIST: FakeAnswer([theirs, own])})
        self.assertEqual(place.merge("claude/7"), gate.MergeResult("m1"))
        self.assertEqual(opener.sent("PUT"), [{"sha": "sha7", "merge_method": "merge"}])

    def test_now_is_the_worlds_clock(self):
        self.assertEqual(world({})[0].now(), datetime(2026, 10, 10, tzinfo=timezone.utc))

    def test_the_gate_lands_a_ready_pull_request_over_github(self):
        place, opener = world({
            **pull_answers(),
            REF + "main": FakeAnswer({"object": {"sha": "m0"}}),
            REF + "claude/7": FakeAnswer({"object": {"sha": "sha7"}}),
            "/repos/o/r/commits/sha7/check-runs?per_page=100": FakeAnswer(
                {"check_runs": [{"name": "check", "status": "completed", "conclusion": "success"}]}),
            "PUT /repos/o/r/pulls/507/merge": FakeAnswer({"sha": "m1", "merged": True}),
        })
        policy, queue = place.policy, gate.Queue({7: "w1"})
        gate.claim(policy, queue, 7, "w1")
        gate.open_pull(policy, place, queue, 7, "w1")
        outcome = gate.mark_ready(policy, place, queue, 7, "w1")
        self.assertEqual((outcome.result, outcome.main, outcome.exit_code), (gate.MERGED, "m1", gate.EXIT_DONE))
        self.assertEqual(len(opener.sent("PUT")), 1)


DETAIL = "/repos/o/r/pulls/7"


def detail_answers(state, mergeable=True, base="main", behind_by=None):
    """Pull request #7 from claude/7 at sha7 as GitHub details it, with one
    file, and GitHub's comparison of its base to its head where behind_by is
    given; base None leaves the base out, which a detail from GitHub never
    does, for a caller that hands the adapter a pull request read elsewhere."""
    pull = {"number": 7, "mergeable": mergeable, "mergeable_state": state, "head": {"ref": "claude/7", "sha": "sha7"}}
    if base is not None:
        pull["base"] = {"ref": base}
    answers = {DETAIL: FakeAnswer(pull), DETAIL + "/files?per_page=100": FakeAnswer([{"filename": "a.md"}])}
    if behind_by is not None:
        answers[f"/repos/o/r/compare/{base}...sha7"] = FakeAnswer({"behind_by": behind_by})
    return answers


def compares_asked(opener):
    """The comparison paths the fake was asked, in the order it was asked."""
    return [c.full_url.replace(github.API, "") for c in opener.calls if "/compare/" in c.full_url]


class Standing(unittest.TestCase):
    """What pull_with_detail reads for a pull request whose mergeable_state
    says only that something else stops the merge (F9): a rule, a review or
    the draft itself, each of which GitHub answers as blocked, or draft in
    older answers, without saying whether the branch is behind."""

    def test_an_opaque_state_is_resolved_through_one_compare_into_a_standing(self):
        """Until this the board read blocked as blocked and draft as unknown
        and told neither "main moved"; one comparison tells behind from
        clean, and GitHub's own word stays on the detail, since a fixture
        is a detail as GitHub gave it."""
        for state in ("blocked", "draft"):
            for behind_by, word in ((0, "clean"), (2, "behind")):
                api, opener = client(detail_answers(state, behind_by=behind_by))
                pull = github.pull_with_detail(api, 7)
                self.assertEqual((pull["standing"], pull["mergeable_state"], pull["files"]),
                                 (word, state, ["a.md"]), (state, behind_by))
                self.assertEqual(compares_asked(opener), [COMPARE], (state, behind_by))

    def test_a_comparison_github_cannot_make_leaves_the_standing_unknown(self):
        """A comparison GitHub has no answer to (404, missing_ok) or answers
        without behind_by says nothing about the branch, so the standing is
        unknown rather than a guess at clean, as standing_word reads None."""
        api, opener = client(detail_answers("draft"))
        self.assertEqual(github.pull_with_detail(api, 7)["standing"], "unknown")
        self.assertEqual(compares_asked(opener), [COMPARE])
        api, _ = client({**detail_answers("blocked"), COMPARE: FakeAnswer({})})
        self.assertEqual(github.pull_with_detail(api, 7)["standing"], "unknown")

    def test_mergeable_false_under_an_opaque_state_is_conflicting_with_no_compare(self):
        """GitHub's mergeable flag already tells a conflict apart, so the
        comparison, one more call on every board run for each pull request
        GitHub calls blocked or draft, is made only where it can change the
        answer."""
        for state in ("blocked", "draft"):
            api, opener = client(detail_answers(state, mergeable=False))
            self.assertEqual(github.pull_with_detail(api, 7)["standing"], "conflicting", state)
            self.assertEqual(compares_asked(opener), [], state)

    def test_a_state_the_board_reads_as_it_is_gets_no_standing_and_no_compare(self):
        """behind and dirty already say how the branch stands and unknown is
        GitHub still computing, so a standing beside them would be a second
        word for the same thing on a dict no fixture ever held. clean,
        unstable and has_hooks say the repository's rules do not hold the
        pull request for being behind, which they say of a branch behind
        main only under a rule that does not require an up-to-date branch,
        and the spec owes such a pull request nothing however far main has
        moved (F16), so a comparison there could change no answer."""
        for state, mergeable in (("clean", True), ("unstable", True), ("has_hooks", True), ("behind", True),
                                 ("dirty", False), ("unknown", None)):
            waits = []
            api, opener = client(detail_answers(state, mergeable=mergeable), waits)
            pull = github.pull_with_detail(api, 7)
            self.assertNotIn("standing", pull, state)
            self.assertEqual((pull["mergeable_state"], compares_asked(opener)), (state, []), state)
            self.assertEqual(waits, [github.MERGEABLE_RETRY_SECONDS] if mergeable is None else [], state)
            self.assertIsNone(github.standing_of(api, {"mergeable_state": state, "mergeable": mergeable}))

    def test_standing_of_compares_against_the_base_and_leaves_a_stacked_pull_request_alone(self):
        """A detail from GitHub names its base. On the default branch the
        caller names, or with none named, the branch is compared against
        that base; stacked on another branch it gets no standing and keeps
        GitHub's word, since "main moved" speaks of the default branch, and
        a branch behind its own base would hear it on every commit of main
        with nothing to do about it. A pull request handed in without a base
        is compared against the default branch given, and with neither there
        is no branch to compare against, so the standing is unknown with no
        call."""
        trunk = "/repos/o/r/compare/trunk...sha7"
        api, opener = client({**detail_answers("blocked", base=None), trunk: FakeAnswer({"behind_by": 1})})
        self.assertEqual(github.pull_with_detail(api, 7, "trunk")["standing"], "behind")
        self.assertEqual(compares_asked(opener), [trunk])
        api, opener = client({COMPARE: FakeAnswer({"behind_by": 0})})
        named = {"mergeable": True, "mergeable_state": "draft", "base": {"ref": "main"}, "head": {"sha": "sha7"}}
        self.assertEqual(github.standing_of(api, named, "main"), "clean")
        self.assertEqual(github.standing_of(api, named), "clean")
        self.assertEqual(compares_asked(opener), [COMPARE, COMPARE])
        api, opener = client({})
        for mergeable in (True, False):
            self.assertIsNone(github.standing_of(api, dict(named, mergeable=mergeable), "trunk"), mergeable)
        unnamed = {"mergeable": True, "mergeable_state": "draft", "head": {"sha": "sha7"}}
        self.assertEqual(github.standing_of(api, unnamed), "unknown")
        self.assertEqual(opener.calls, [])

    def test_collect_names_the_default_branch_so_a_stacked_pull_request_keeps_github_s_word(self):
        """collect hands standing_of the default branch, so a held pull
        request stacked on another branch is compared with nothing and shows
        GitHub's word; one on the default branch is compared."""
        snap, answers = fixture_answers()
        held = {"number": 40, "mergeable": True, "mergeable_state": "blocked",
                "head": {"ref": "claude/12", "sha": "aaaaaaa1111111"}}
        compare = "/repos/o/r/compare/main...aaaaaaa1111111"
        for base, standing, asked in (("feature", None, []), ("main", "behind", [compare])):
            api, opener = client({**answers, "/repos/o/r/pulls/40": FakeAnswer(dict(held, base={"ref": base})),
                                  compare: FakeAnswer({"behind_by": 2})})
            pull = next(p for p in github.collect(api, "abc", "main")["pulls"] if p["number"] == 40)
            self.assertEqual((pull.get("standing"), compares_asked(opener)), (standing, asked), base)

    def test_a_comparison_github_refuses_reads_as_unknown_and_keeps_github_s_word(self):
        """A 5xx, a 422 or a secondary rate limit's 403 on one pull
        request's comparison leaves its standing unknown, so the board shows
        GitHub's own word for it, as the coordinator's own comparison does,
        rather than failing the whole run: let through, the error stopped the
        board and every release on every run while that pull request stayed
        open and held."""
        for status in (403, 422, 500, 502, 503):
            api, _ = client({**detail_answers("blocked"), COMPARE: Refusal(status)})
            pull = github.pull_with_detail(api, 7)
            self.assertEqual((pull["standing"], board.Pull.from_dict(pull).state), ("unknown", "blocked"), status)

    def test_open_pull_carries_the_standing_the_clerk_reads_and_the_gate_compares_once(self):
        """The clerk deals a claim back when its pull request is behind, and
        where GitHub's word is opaque only the standing says so; the gate's
        world reads the pull request without one and compares the branch
        itself, once."""
        listed = "/repos/o/r/pulls?state=open&head=o%3Aclaude%2F7"
        answers = {**detail_answers("blocked", behind_by=3), listed: FakeAnswer([{"number": 7}])}
        api, opener = client(answers)
        self.assertEqual(api.open_pull("claude/7")["standing"], "behind")
        self.assertEqual(compares_asked(opener), [COMPARE])
        api, opener = client(answers)
        world = github.GitHubWorld(api, gate.Policy(), "main")
        self.assertEqual((world.standing("claude/7"), compares_asked(opener)), (gate.BEHIND, [COMPARE]))
        api, opener = client({**detail_answers("clean"), listed: FakeAnswer([{"number": 7}])})
        self.assertNotIn("standing", api.open_pull("claude/7"))
        self.assertEqual(compares_asked(opener), [])

    def test_neither_a_draft_for_the_clerk_nor_the_merge_pays_for_a_comparison(self):
        """The clerk carries a draft whatever its standing, and the merge
        asks GitHub to merge the head it read, so neither compares the
        branch: a comparison is made only where it can change the answer."""
        behind = {COMPARE: FakeAnswer({"behind_by": 2})}
        api, opener = client({**pull_answers(state="blocked", draft=True), **behind})
        self.assertNotIn("standing", api.open_pull("claude/7"))
        self.assertTrue(team.claim_needs_work(api, 7))
        self.assertEqual(compares_asked(opener), [])
        place, opener = world({**pull_answers(state="blocked"), **behind,
                               "PUT /repos/o/r/pulls/507/merge": FakeAnswer({"sha": "m1", "merged": True})})
        self.assertEqual(place.merge("claude/7"), gate.MergeResult("m1"))
        self.assertEqual(compares_asked(opener), [])

    def test_a_refused_comparison_reads_unknown_however_late_github_computed_mergeability(self):
        """The gate's world compares the branch itself, so a comparison
        GitHub refuses reads as unknown whether the pull request's
        mergeability was computed at the first read or only at the read
        after the pause, and a refused merge is then GitHub's own refusal,
        never the gate failing."""
        blocked = {"number": 507, "draft": False, "mergeable": True, "mergeable_state": "blocked",
                   "head": {"ref": "claude/7", "sha": "sha7"}, "base": {"ref": "main"}}
        computing = dict(blocked, mergeable=None, mergeable_state="unknown")
        put = "PUT /repos/o/r/pulls/507/merge"
        for first in (blocked, computing):
            with self.subTest(first=first["mergeable_state"]):
                answers = {PULL_LIST: FakeAnswer([{"number": 507}]),
                           "/repos/o/r/pulls/507": Turns(FakeAnswer(first), FakeAnswer(blocked)),
                           COMPARE: Refusal(502), put: Refusal(405, {"message": "Required status check is expected"})}
                api = github.GitHub("t", "o/r", opener=InTurn(answers), sleep=lambda seconds: None)
                place = github.GitHubWorld(api, gate.Policy(who_merges=gate.GATE))
                self.assertEqual(place.standing("claude/7"), gate.UNKNOWN)
                with self.assertRaises(gate.MergeRefused):
                    place.merge("claude/7")


# Names a person writes, with characters outside ASCII and the characters
# a URL reads as its own (#, %, &, +, =, ;) where git allows them: a branch,
# a default branch, a label and a login (GitHub keeps logins to ASCII
# letters, digits and dashes, and the path quotes one anyway).
BRANCH = "feat/caf\u00e9#1%&+=;"
DEFAULT = "d\u00e9veloppement"
LABEL = "worker:caf\u00e9 #1/2%"
LOGIN = "caf\u00e9"


def quote_all(name):
    return urllib.parse.quote(name, safe="")


def quote_path(name):
    return urllib.parse.quote(name, safe="/")


class PathsFromNames(unittest.TestCase):
    """Every path the adapter builds from a name a person writes goes out as
    ASCII and names that thing exactly, through the fake that writes the
    request line as http.client does. The other paths hold numbers and
    commits GitHub itself gave: an issue's or a pull request's number from
    its answers, or a claim key claims.number_in read as ASCII digits; a
    commit's sha from a branch list, a ref or a pull request; a ruleset's
    id; the repository's own name; and the next page's URL from GitHub's
    Link header."""

    def assert_ascii(self, opener):
        self.assertTrue(opener.calls)
        for call in opener.calls:
            self.assertTrue(call.selector.isascii(), call.selector)

    def test_a_branch_a_default_branch_a_label_and_a_login_are_quoted_wherever_a_path_holds_one(self):
        ref, compare = REF + quote_path(BRANCH), f"/repos/o/r/compare/{quote_path(DEFAULT)}...s1"
        api, opener = client({
            ref: FakeAnswer({"object": {"sha": "s1"}}),
            f"POST /repos/o/r/branches/{quote_all(BRANCH)}/rename": FakeAnswer({}),
            compare: FakeAnswer({"ahead_by": 1, "behind_by": 2, "commits": [{"commit": {"message": "x"}}],
                                 "files": [{"filename": "a.md"}]}),
            f"/repos/o/r/rules/branches/{quote_all(BRANCH)}?per_page=100": FakeAnswer([]),
            f"DELETE /repos/o/r/issues/12/labels/{quote_all(LABEL)}": FakeAnswer(None),
            f"/repos/o/r/collaborators/{quote_all(LOGIN)}/permission": FakeAnswer({"permission": "write"}),
            "/repos/o/r/pulls?" + urllib.parse.urlencode({"state": "open", "head": f"o:{BRANCH}"}):
                FakeAnswer([{"number": 5}]),
            "/repos/o/r/pulls/5": FakeAnswer({"number": 5, "mergeable": True, "mergeable_state": "clean"}),
            "POST /repos/o/r/labels": FakeAnswer({}),
            "POST /repos/o/r/issues/12/labels": FakeAnswer([]),
        })
        self.assertEqual(github.branch_sha(api, BRANCH), "s1")
        self.assertEqual(github.free_name(api, BRANCH, "released/9-s1", "s1"),
                         f"claim released; {BRANCH} renamed to released/9-s1")
        self.assertEqual(github.holding(api, DEFAULT, "s1"), coordinator.Holding(1, "x", ("a.md",)))
        self.assertEqual(github.GitHubWorld(api, gate.Policy()).behind_by({"base": {"ref": DEFAULT},
                                                                           "head": {"sha": "s1"}}), 2)
        self.assertEqual(github.rulesets_on(api, (BRANCH,)), [])
        self.assertTrue(github.take_label_off(api, "12", LABEL))
        api.remove_label(12, LABEL)
        self.assertTrue(github.may_write(api)(LOGIN))
        self.assertEqual(api.open_pull(BRANCH)["number"], 5)
        api.create_label(LABEL, "ededed")
        self.assertEqual(github.put_back(api, "12", [LABEL]), f"; {LABEL} put back")
        self.assert_ascii(opener)
        self.assertEqual(opener.sent("POST")[-1], {"labels": [LABEL]})

    def test_a_rename_refused_for_a_kept_head_deletes_the_branch_by_its_quoted_name(self):
        api, opener = client({
            REF + quote_path(BRANCH): FakeAnswer({"object": {"sha": "s1"}}),
            f"POST /repos/o/r/branches/{quote_all(BRANCH)}/rename": Refusal(422),
            REF + "released/9-s1": FakeAnswer({"object": {"sha": "s1"}}),
            f"DELETE /repos/o/r/git/refs/heads/{quote_path(BRANCH)}": FakeAnswer(None),
        })
        self.assertEqual(github.free_name(api, BRANCH, "released/9-s1", "s1"),
                         f"claim released; released/9-s1 already keeps s1, so {BRANCH} was deleted")
        self.assert_ascii(opener)

    def test_a_branch_list_of_names_that_claim_nothing_is_read_with_every_request_line_in_ascii(self):
        # Each branch here has a head, so the release rule reads it too;
        # none of them names an issue, so none costs an issue's call.
        snap, answers = fixture_answers()
        names = ["main", "claude/12", "claude/13", "claude/caf\u00e9#1", "claude/\u00b2", "claude/03", "claude/%41"]
        answers["/repos/o/r/branches?per_page=100"] = FakeAnswer(
            [{"name": name, "commit": {"sha": f"s{at}"}} for at, name in enumerate(names)])
        api, opener = client(answers)
        found = github.collect(api, snap["sha"], snap["default_branch"])
        self.assertEqual(sorted(found["claims"]), sorted(claims.key_of(name) for name in names[1:]))
        self.assert_ascii(opener)
        asked = [c.selector for c in opener.calls]
        self.assertIn("/repos/o/r/contents/docs/agents/claims/caf%C3%A9%231.md?ref=claude%2Fcaf%C3%A9%231", asked)
        self.assertIn("/repos/o/r/contents/docs/agents/claims/%2541.md?ref=claude%2F%2541", asked)
        self.assertFalse([path for path in asked if path.startswith("/repos/o/r/issues/") and "%" in path], asked)


if __name__ == "__main__":
    unittest.main()
