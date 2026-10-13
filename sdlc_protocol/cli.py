"""The command line: python3 -m sdlc_protocol <board|check|team|workers|guard|verify-vendored>.

Each subcommand is a thin shell over the modules and takes exactly the
arguments the template's tool for the same job took, so a workflow line
changes only from "python3 tools/board.py ..." to "python3 -m
sdlc_protocol board ...":

  board  release the claims gone a day without a commit and tell open
         work that meets other work in a file (the coordinator), render
         STATE.md from GitHub (--default-branch, --sha, --out,
         --render-only), rename the branches of released claims and
         comment on the pull requests that wait, or render a JSON
         snapshot with --dry-run FIXTURE and no network
  check  hold the pull request in GITHUB_EVENT_PATH to the claim protocol
  team   [--repo OWNER/NAME] seed, route, next or schema, the clerk; seed,
         next and schema read the workers from the registry with
         --registry DIR
  workers check, list, describe, get or from-form: the registry of
         workers, .github/symphony/workers/<name>.json, read offline
  guard  the PreToolUse hook: the tool call's JSON on stdin, and exit 2
         with the reason on stderr to refuse a push
  verify-vendored VENDOR_DIR
         hold a vendored copy, VENDOR_DIR/sdlc_protocol, to the record
         VENDOR_DIR/VENDORED beside it (vendoring.py, which a copy also
         runs as a script of its own)

The exit codes are the old tools' too: 0 when done, 1 for a failure told
in one line on stderr (or, for check, the problems on stdout), 2 for a
push the guard refuses or arguments that cannot be read.

Only board and team import the GitHub adapter, and only when they run:
the guard answers before every Bash call of a session, and the start-up
time of urllib is no use to it.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import board, claims, guard, protocol_check, team, vendoring, workers

PROG = "python3 -m sdlc_protocol"
USAGE = (f"usage: {PROG} {{board,check,team,workers,guard,verify-vendored}} [arguments]; "
         f"{PROG} <command> -h says more")


class CommandError(Exception):
    """What a subcommand cannot start without, in one line, never a token."""


def github_adapter():
    """The GitHub adapter, imported when a subcommand first calls GitHub."""
    from .adapters import github
    return github


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def environment(given):
    """The environment a subcommand reads: the one a test hands in, else
    the process's own."""
    return os.environ if given is None else given


# --- board --------------------------------------------------------------------

def board_arguments(argv):
    parser = argparse.ArgumentParser(
        prog=f"{PROG} board",
        description="Release idle claims and tell overlapping work (the coordinator), render the board "
                    "(STATE.md), rename released claims' branches and comment on waiting pull requests.")
    parser.add_argument("--dry-run", metavar="FIXTURE", help="render this JSON snapshot instead of asking GitHub")
    parser.add_argument("--render-only", action="store_true",
                        help="write the board, and release no claim, rename no branch and post no comment")
    parser.add_argument("--out", help=f"where to write the board (default: {board.STATE_PATH} in the current "
                                      "directory; stdout with --dry-run)")
    parser.add_argument("--default-branch", default="main", help="the default branch's name (default: main)")
    parser.add_argument("--sha", help="the default branch's commit the board describes (default: GITHUB_SHA)")
    return parser.parse_args(argv)


def board_dry_run(args):
    """The fixture's board, as it stands: the fixture names its own default
    branch, as the template's dry run read it."""
    text = board.render(load_json(args.dry_run))
    if args.out:
        board.write_if_changed(Path(args.out), text)
    else:
        sys.stdout.write(text)
    return 0


def board_live_run(args, environ):
    token, repo = environ.get("GITHUB_TOKEN"), environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise CommandError("GITHUB_TOKEN and GITHUB_REPOSITORY must be set")
    github = github_adapter()
    api = github.GitHub(token, repo)
    now = datetime.now(timezone.utc)
    snap = github.snapshot(api, args.sha or environ.get("GITHUB_SHA", ""), args.default_branch, now)
    for line in github.unread_claim_lines(snap):
        print(line)
    if not args.render_only:
        for line in github.coordinate(api, snap, now):
            print(line)
    out = Path(args.out) if args.out else board.STATE_PATH
    changed = board.write_if_changed(out, board.render(snap))
    print(f"{out.as_posix()}: {'rewritten' if changed else 'unchanged'}")
    if not args.render_only:
        for line in github.release_claims(api, snap):
            print(line)
        due = board.comments_due(snap, environ.get("GITHUB_EVENT_NAME", ""), environ.get("GITHUB_REF_NAME", ""), now)
        for line in github.post_comments(api, due):
            print(line)
    return 0


def run_board(argv, environ=None):
    args = board_arguments(argv)
    try:
        return board_dry_run(args) if args.dry_run else board_live_run(args, environment(environ))
    except (CommandError, team.ClientError) as problem:
        print(f"board: {problem}", file=sys.stderr)
        return 1


# --- check --------------------------------------------------------------------

def read_event(environ):
    """The event GitHub hands the run, and what to say when there is none
    to check: no event, or one that is not a JSON object."""
    where = environ.get("GITHUB_EVENT_PATH")
    if not where or not Path(where).is_file():
        return None, "no pull request event to check"
    try:
        event = load_json(where)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        return None, "the event is not readable; nothing checked"
    return event, ""


def run_check(argv, environ=None, root="."):
    """The protocol check: a run outside a pull request event passes, so
    the check never breaks a push build; the claim file is looked for in
    the checkout, root."""
    argparse.ArgumentParser(
        prog=f"{PROG} check",
        description="Hold the claude/<issue> pull request in GITHUB_EVENT_PATH to the claim protocol.",
    ).parse_args(argv)
    event, why_not = read_event(environment(environ))
    if event is None:
        print(why_not)
        return 0
    problems = protocol_check.problems_for(
        event.get("pull_request") or {}, lambda number: (Path(root) / claims.claim_path(number)).is_file())
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("protocol ok")
    return 0


# --- team ---------------------------------------------------------------------

def team_arguments(argv, environ=None):
    environ = environment(environ)
    parser = argparse.ArgumentParser(prog=f"{PROG} team", description="The clerk's steps (docs/agents/WORKERS.md).")
    parser.add_argument("--repo", default=environ.get("GITHUB_REPOSITORY"))
    sub = parser.add_subparsers(dest="command", required=True)
    seeding = sub.add_parser("seed")
    seeding.add_argument("--epic", type=int, required=True)
    seeding.add_argument("--plan", required=True, help="the coordinator's plan as a JSON file")
    seeding.add_argument("--team", default=environ.get("CLAUDE_TEAM", ""))
    seeding.add_argument("--registry", help="give the tasks to the workers of this registry folder, of any kind")
    routing = sub.add_parser("route")
    routing.add_argument("--event", required=True)
    routing.add_argument("--issue", type=int, required=True)
    routing.add_argument("--label", default=None)
    nexting = sub.add_parser("next")
    nexting.add_argument("--agent", required=True)
    nexting.add_argument("--team", default=environ.get("CLAUDE_TEAM", ""))
    nexting.add_argument("--registry", help="deal the shared work among this registry's workers in GitHub "
                                            "Actions, each by its area")
    nexting.add_argument("--label", default=None,
                         help="the worker's label, made before a take so the session can add it")
    schema = sub.add_parser("schema", help="the JSON schema of the coordinator's plan, on one line")
    schema.add_argument("--registry", help="the registry whose workers a task may go to")
    return parser.parse_args(argv)


def team_answer(client, args):
    """The one line the workflow reads, from the clerk's rules. With a
    registry, seed gives tasks to every registered worker, and next deals
    the shared work among the workers in Actions, which ask at the same
    moment, each by its area."""
    registered = workers.load(args.registry) if getattr(args, "registry", None) else None
    if args.command == "seed":
        names = [w.name for w in registered] if registered is not None else team.team_members(args.team)
        return team.seed(client, args.epic, load_json(args.plan), names)
    if args.command == "route":
        return team.route(client, args.event, args.issue, args.label)
    if registered is None:
        return team.next_move(client, args.agent, team.team_members(args.team), args.label)
    in_actions = workers.in_actions(registered)
    areas = {w.name: w.area for w in registered}
    return team.next_move(client, args.agent, [w.name for w in in_actions], args.label, areas)


def team_schema(args):
    """The plan's schema, which needs no GitHub: the registry's names, or
    none, for the owners."""
    names = [w.name for w in workers.load(args.registry)] if args.registry else []
    print(json.dumps(team.plan_schema(names), separators=(",", ":")))
    return 0


def run_team(argv, environ=None):
    environ = environment(environ)
    args = team_arguments(argv, environ)
    if args.command == "schema":
        try:
            return team_schema(args)
        except workers.WorkerError as problem:
            print(f"team: {problem}", file=sys.stderr)
            return 1
    token = environ.get("GITHUB_TOKEN") or environ.get("GH_TOKEN")
    if not token or not args.repo:
        print("GITHUB_TOKEN and --repo (or GITHUB_REPOSITORY) are needed", file=sys.stderr)
        return 1
    try:
        print(team_answer(github_adapter().GitHub(token, args.repo), args))
    except (team.ClientError, team.TeamError, workers.WorkerError, ValueError, KeyError) as problem:
        print(f"team: {problem}", file=sys.stderr)
        return 1
    return 0


# --- workers ------------------------------------------------------------------

def workers_arguments(argv):
    parser = argparse.ArgumentParser(
        prog=f"{PROG} workers",
        description="The registry of workers, one JSON file per worker (docs/agents/WORKERS.md); no network.")
    parser.add_argument("--dir", default=workers.REGISTRY, help=f"the registry's folder (default: {workers.REGISTRY})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="hold every worker's file to the registry's rules; exit 1 on any problem")
    listing = sub.add_parser("list", help="the workers' names as a JSON list, as a workflow's matrix reads it")
    listing.add_argument("--runs", choices=workers.RUNS, help="only the workers that run this way")
    sub.add_parser("describe", help="one line per worker: its name, where it runs, its area, what it is for")
    getting = sub.add_parser("get", help="one setting of one worker")
    getting.add_argument("name")
    getting.add_argument("setting", choices=workers.KEYS + ("label",))
    forming = sub.add_parser("from-form", help="write the worker an \"Add a worker\" issue's answers describe")
    forming.add_argument("--body", help="a file holding the issue's body (default: stdin)")
    return parser.parse_args(argv)


def workers_check(args):
    found = workers.load(args.dir)
    listed = ", ".join(f"{w.name} ({w.runs})" for w in found) or "none"
    print(f"{len(found)} worker{'s' if len(found) != 1 else ''} in {args.dir}: {listed}")
    return 0


def workers_list(args):
    found = workers.load(args.dir)
    print(json.dumps([w.name for w in found if not args.runs or w.runs == args.runs]))
    return 0


def workers_describe(args):
    found = workers.load(args.dir)
    print("\n".join(workers.describe(w) for w in found) if found else "(no worker is registered)")
    return 0


def workers_get(args):
    worker = workers.named(workers.load(args.dir), args.name)
    if worker is None:
        raise CommandError(f"no worker named {args.name} in {args.dir}")
    value = worker.label if args.setting == "label" else worker.to_dict()[args.setting]
    print(value if isinstance(value, str) else json.dumps(value))
    return 0


def workers_from_form(args, stdin):
    """Write the worker the form's answers describe into the registry and
    print its name and how it runs, for the workflow that opens the pull
    request; a name already registered is refused, since a worker's file is
    changed by a pull request of its own, never by a second form."""
    body = Path(args.body).read_text(encoding="utf-8") if args.body else (stdin or sys.stdin).read()
    worker = workers.from_dict(workers.from_form(body))
    if workers.named(workers.load(args.dir), worker.name) is not None:
        raise CommandError(f"a worker named {worker.name} exists already; change {args.dir}/{worker.file_name} "
                           f"by pull request, or pick another name")
    folder = Path(args.dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / worker.file_name).write_text(worker.to_json(), encoding="ascii")
    print(f"{worker.name} {worker.runs}")
    return 0


def run_workers(argv, stdin=None):
    args = workers_arguments(argv)
    commands = {"check": workers_check, "list": workers_list, "describe": workers_describe, "get": workers_get,
                "from-form": lambda given: workers_from_form(given, stdin)}
    try:
        return commands[args.command](args)
    except workers.WorkerError as problem:
        for line in str(problem).splitlines():
            print(f"workers: {line}", file=sys.stderr)
        return 1
    except (CommandError, OSError, UnicodeError) as problem:
        print(f"workers: {problem}", file=sys.stderr)
        return 1


# --- guard --------------------------------------------------------------------

GUARD_HELP = (f"usage: {PROG} guard < payload.json\n\n"
              "The PreToolUse hook on Bash: reads the tool call's JSON on stdin and exits 2, "
              "with the reason on stderr, to refuse a push the board rules forbid.")


def run_guard(argv, stdin=None):
    """The push guard as Claude Code's hook runs it. Its arguments are not
    parsed: exit 2 stops the tool call, so an argument error would refuse
    every Bash call of the session, and the guard fails open instead, as
    it does for a payload that is not JSON. Help is the one argument it
    answers, and it lets the call pass too."""
    if "-h" in argv or "--help" in argv:
        print(GUARD_HELP)
        return guard.PASS
    try:
        payload = json.load(sys.stdin if stdin is None else stdin)
    except ValueError:
        return guard.PASS
    code, reason = guard.run(payload)
    if reason:
        print(reason, file=sys.stderr)
    return code


# --- verify-vendored ----------------------------------------------------------

def run_verify_vendored(argv):
    """vendoring.py's check under the package's own command line, for a
    consumer that would rather name the module than the file."""
    parser = argparse.ArgumentParser(
        prog=f"{PROG} verify-vendored",
        description="Hold VENDOR_DIR/sdlc_protocol to the record VENDOR_DIR/VENDORED: exit 1 on any difference.")
    parser.add_argument("vendor_dir", metavar="VENDOR_DIR", help="the directory that holds sdlc_protocol/ and VENDORED")
    return vendoring.verify(parser.parse_args(argv).vendor_dir)


COMMANDS = {"board": run_board, "check": run_check, "team": run_team, "workers": run_workers, "guard": run_guard,
            "verify-vendored": run_verify_vendored}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if not argv or argv[0] not in COMMANDS:
        print(USAGE, file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](argv[1:])
