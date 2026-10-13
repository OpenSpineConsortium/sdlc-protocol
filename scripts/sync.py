"""The sync's logic, which .github/workflows/sync.yml and ci.yml call.

  python3 scripts/sync.py [--library DIR] matrix [--public-only]
  python3 scripts/sync.py setup
  python3 scripts/sync.py off-main --ref REF
  python3 scripts/sync.py after-release EVENT_FILE --repository OWNER/NAME
  python3 scripts/sync.py newest-release < tags
  python3 scripts/sync.py no-release
  python3 scripts/sync.py misplaced --tag TAG --commit COMMIT
  python3 scripts/sync.py carrying --tag TAG --commit COMMIT
  python3 scripts/sync.py [--library DIR] title   <consumer checkout> --consumer OWNER/NAME
  python3 scripts/sync.py [--library DIR] message <consumer checkout> --consumer OWNER/NAME
  python3 scripts/sync.py [--library DIR] body    <consumer checkout> --consumer OWNER/NAME
  python3 scripts/sync.py [--library DIR] closing --consumer OWNER/NAME
  python3 scripts/sync.py [--library DIR] summary --consumer OWNER/NAME --state STATE [--url URL]
                                                  [--auto-merge on|refused] [--reason TEXT]

Each release of the library, once the owner has approved it, makes
sync.yml vendor it into every consumer the release's ecosystem.json lists
and open, or update, the pull request symphony-protocol/sync there; the
sync carries the newest published release and never main's head, and
--library names the checkout of that release, whose ecosystem.json and
history the commands read (by default this repository's own). ci.yml's
downstream job runs each consumer's own check on a pull request's copy
before it merges here. The workflows only run git, gh and these commands,
so what they decide is here, where it is tested: the matrix of consumers,
what the owner must set up when the sync cannot run, why a run off main
or after a Release run that published nothing vendors nothing, which
release is the newest, whether its tag stands where release.yml makes one
(scripts/release.py decides), the commit message, the title and the body of a
consumer's pull request, built from the consumer's record before and
after the release's scripts/vendor.py ran in its checkout, the comment
that closes a sync pull request main has made stale, and each consumer's
row in the run's summary. The body is written for the person who reviews
the pull request there: which release it brings, what changed in the
library since the commit the copy named before, which files of the copy
moved, what the consumer uses the library for, the release it makes
where the consumer names a version file (the plugin's version, which
scripts/vendor.py raised in the same checkout), and that the consumer's
own check decides.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import ecosystem
import release
from ecosystem import vendoring  # the package's vendoring.py, loaded by its path alone

LIBRARY = ecosystem.ROOT
BRANCH = "symphony-protocol/sync"
SHA = re.compile(r"[0-9a-f]{40}")
APP_REPOSITORY = "OpenSpineConsortium/symphony-app"
# A release's tag, as .github/workflows/release.yml makes it.
RELEASE_TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")
# The events of a Release run the sync follows: main's own, a push or a run
# by hand, which only someone who may write here can start.
RELEASE_EVENTS = ("push", "workflow_dispatch")

# What became of a consumer's pull request in one run, as its row in the
# summary says it: {url} is the pull request's.
PULL_STATES = {
    "up to date": "up to date",
    "closed": "up to date; closed the stale {url}",
    "opened": "opened: {url}",
    "updated": "updated: {url}",
    "unchanged": "open, and already this copy: {url}",
}
AUTO_MERGE_STATES = ("on", "refused")
REASON_LENGTH = 200


# --- what the workflows read ------------------------------------------------------------

def matrix(eco, public_only=False):
    """The consumers as a GitHub Actions matrix, one leg each."""
    legs = [{"repository": consumer["repository"], "name": ecosystem.short_name(consumer["repository"]),
             "visibility": consumer["visibility"], "vendor_dir": consumer["vendor_dir"],
             "check": consumer["check"], "auto_merge": consumer["auto_merge"]}
            for consumer in eco["consumers"] if consumer["visibility"] == "public" or not public_only]
    return {"include": legs}


def setup_text(eco):
    """What the owner sets once before the sync can open pull requests,
    for the run's summary when the App's id is not set."""
    library = eco["library"]
    merging = [c["repository"] for c in eco["consumers"] if c["auto_merge"]]
    allow = "\n".join(f"   gh api -X PATCH repos/{name} -F allow_auto_merge=true" for name in merging)
    return (
        "## The sync is not set up\n\n"
        "The sync opens its pull requests with the token of the GitHub App Symphony, and this "
        "repository has no variable SDLC_APP_ID, so the sync was skipped and the run stays green. "
        "The owner sets it up once (README.md, \"The owner's one-time setup for the sync\"):\n\n"
        f"1. Install the App on {library} and on every consumer: "
        f"{', '.join(c['repository'] for c in eco['consumers'])}.\n"
        "2. On this repository, set the App's id and its private key, as on symphony-template. Run these "
        f"from a clone of {APP_REPOSITORY}, whose .sdlc-app.env scripts/register.py wrote, so the key "
        "goes through a pipe and never onto the screen:\n\n"
        "   ```\n"
        f"   gh variable set SDLC_APP_ID --repo {library} --body <the App's id>\n"
        f"   python3 scripts/register.py --print-key | gh secret set SDLC_APP_PRIVATE_KEY --repo {library}\n"
        "   ```\n\n"
        "3. Allow auto-merge on the consumers ecosystem.json gives auto_merge:\n\n"
        "   ```\n"
        f"{allow}\n"
        "   ```\n\n"
        "4. Require the check named check on each consumer's main with a ruleset, so auto-merge waits "
        "for it: the sync only arms auto-merge, and GitHub refuses to arm it where nothing is left to "
        "wait for.\n")


def off_main_text(ref):
    """Why a run started from a ref other than main vendored nothing, for
    the run's summary."""
    return (
        "## The sync did not run\n\n"
        f"The sync runs only from main, and this run was started from `{ref}`, so it changed nothing in any "
        "consumer. Wherever it runs from, it carries only the newest release, which waits for the owner's "
        "approval: a branch's change reaches the consumers through its pull request, whose downstream jobs "
        "run each consumer's check on it, and then through the release that carries it.\n")


def after_release_text(run, repository):
    """Why the sync does not follow the Release run that started it, for the
    run's summary, or None when it follows it: a run that concluded success
    and that main started in this repository, by a push or by hand. A pull
    request's run, a fork's above all, never starts one that goes on."""
    conclusion = run.get("conclusion")
    if conclusion != "success":
        named = isinstance(conclusion, str) and conclusion.isidentifier()
        ended = f"concluded `{conclusion}` rather than `success`" if named else "ended with no conclusion"
        return (
            "## The sync did not run\n\n"
            f"The Release run that started it {ended}, so it published no release, and the sync changed nothing "
            "in any consumer: a release the owner has not approved reaches no copy. The sync runs again when a "
            "Release run succeeds, every week and by hand, and carries the newest release then.\n")
    head = (run.get("head_repository") or {}).get("full_name") or ""
    if (run.get("event") not in RELEASE_EVENTS or run.get("head_branch") != "main"
            or head.lower() != repository.lower()):
        return (
            "## The sync did not run\n\n"
            "The Release run that started it was not one that main started, by a push or by hand, so the sync "
            "changed nothing in any consumer: only main's own Release runs publish what the owner approved.\n")
    return None


def newest_release(tags):
    """The tag of the newest release among tags: the highest version of the
    form v<major>.<minor>.<patch>, or None when there is none. The highest,
    not the release GitHub marks latest, which a person can move to an older
    one by hand: the sync never takes the consumers back to it."""
    found = [(tuple(int(part) for part in match.groups()), tag)
             for tag in (line.strip() for line in tags) if (match := RELEASE_TAG.fullmatch(tag))]
    return max(found)[1] if found else None


def no_release_text(eco):
    """Why a run found nothing to carry, for the run's summary."""
    return (
        "## The sync did not run\n\n"
        f"{eco['library']} has no published release yet, and the sync carries only a published release, so it "
        "changed nothing in any consumer. The first is made by .github/workflows/release.yml once the owner "
        "approves its run.\n")


def misplaced_text(eco, tag, commit, history=None):
    """Why the sync does not carry the newest release, for the run's
    summary, or None when it carries it: its tag must stand at a commit of
    main's first-parent line that raised it to the tag's version, where
    release.yml makes one, so a release published by hand anywhere else,
    or one whose tag was moved, never reaches a consumer. history is the
    checkout of main the plan runs in, this repository by default."""
    why = release.misplaced(tag, commit, history or release.ROOT)
    if why is None:
        return None
    return (
        "## The sync did not run\n\n"
        f"The newest published release of {eco['library']}, {tag}, does not stand where the release workflow makes "
        f"a release: {why}. The sync carries a release only from the commit that raised main's first-parent line to "
        "its version, so it changed nothing in any consumer, and every run stops here until that release is put "
        "right. If it was published by hand, or its tag was moved, delete it on the repository's Releases page; the "
        "next run then carries the newest release that stands.\n")


def carrying_text(eco, tag, commit):
    """The run's summary's first words when the sync goes on."""
    return (
        f"## The sync carries {tag}\n\n"
        f"The newest published release of {eco['library']}, at {commit}, the commit that raised main to its version, "
        "made into each consumer's copy by that release's own scripts/vendor.py; each consumer's job adds its row.\n")


def title(version, commit):
    return f"Vendor symphony-protocol {version} ({vendoring.short_commit(commit)})"


def release_tag(version):
    """The tag of the release a copy's record names by its version."""
    return f"v{version}"


def message(eco, consumer, version, commit, release=None):
    """The sync's commit message in the consumer: the title, then why."""
    text = (f"{title(version, commit)}\n\n"
            f"Made by {eco['library']}'s sync workflow, which copies the library's\n"
            f"release {release_tag(version)} into {consumer['vendor_dir']}/sdlc_protocol as that release's\n"
            f"ecosystem.json says and records the copy in {consumer['vendor_dir']}/VENDORED.\n"
            "The copy is never edited here; a change to it is made in the library\n"
            "and comes back with the library's next release.\n")
    if is_new_release(release):
        old, new = release
        text += (f"\nIt raises the version in {consumer['version_file']} from {old or 'none'}\n"
                 f"to {new}: the copy's files changed, and that version is how the installs\n"
                 "that run the copy learn there is a new one.\n")
    return text


def is_new_release(release):
    """Whether the version file's version moved: release is (before, after),
    or None for a consumer that names no version file."""
    return release is not None and release[0] != release[1]


# --- the pull request's body ----------------------------------------------------------

def recorded_commit(text):
    """The commit a record's first line names, in the old format or the
    new, or None when there is no record or it cannot be read."""
    first = text.splitlines()[0] if text else ""
    found = vendoring.FIRST_LINE.fullmatch(first)
    return found.group(2) if found else None


def file_changes(porcelain, copy_prefix):
    """The copy's files git status shows added, changed and removed, as
    paths inside the copy, from git status --porcelain --no-renames."""
    added, changed, removed = [], [], []
    for line in porcelain.splitlines():
        code, path = line[:2], line[3:]
        if not path.startswith(copy_prefix):
            continue
        name = path[len(copy_prefix):]
        if code == "??" or "A" in code:
            added.append(name)
        elif "D" in code:
            removed.append(name)
        else:
            changed.append(name)
    return sorted(added), sorted(changed), sorted(removed)


def fenced(lines):
    """Lines as a fenced block, which GitHub neither links nor reads for
    closing keywords: a library commit's "#12" is not the consumer's #12."""
    fence = "````" if any("```" in line for line in lines) else "```"
    return "\n".join([f"{fence}text", *lines, fence])


def history_section(eco, old, log):
    if log is None and not old:
        return ("This repository had no record of the commit its copy was made from, so the library's "
                "log is not listed here; the files below are what changed.")
    if log is None:
        return (f"The copy's record named {old}, which is not a commit of {eco['library']}, so the "
                "library's log is not listed here; the files below are what changed.")
    since = f"Since {vendoring.short_commit(old)}, the commit the copy named before"
    if not log:
        return (f"{since}, no commit changed sdlc_protocol/: what moved is the record, or the set of "
                "files ecosystem.json gives this repository.")
    return f"{since}, these commits changed sdlc_protocol/:\n\n{fenced(log)}"


def files_section(added, changed, removed):
    def listed(names):
        return ", ".join(f"`{name}`" for name in names) or "none"
    return f"- Added: {listed(added)}\n- Changed: {listed(changed)}\n- Removed: {listed(removed)}"


def release_section(consumer, release):
    """What the pull request does to the consumer's version file, for a
    consumer that names one; [] for the others."""
    if release is None:
        return []
    old, new = release
    path = consumer["version_file"]
    if not is_new_release(release):
        text = (f"`{path}` stays at {new}: the copy's files are what they were, so the installs that run "
                "the copy have nothing new to take.")
    else:
        text = (f"`{path}` goes from {old or 'no version'} to {new} in this same pull request, because the "
                "copy's files changed: that version is how the installs that run the copy learn there is a new "
                "one, and they take it at their next update.")
    return ["## The release", "", text, ""]


def landing_sentence(consumer):
    if consumer["auto_merge"]:
        return "Auto-merge, when this repository allows it, merges it once that check is green."
    if consumer["visibility"] == "private":
        return ("Its owner merges it: GitHub Free offers auto-merge only on public repositories, and this "
                "ecosystem relies on no paid plan.")
    return "Its owner merges it, since ecosystem.json gives this repository no auto-merge."


def body(eco, consumer, version, commit, old, log, changes, release=None):
    """The consumer's pull request body. log is the library's one-line log
    from old to commit, or None when old is no commit the library has;
    release is the version file's (before, after), or None."""
    vendor_dir = consumer["vendor_dir"]
    sha = commit.partition("-")[0]
    link = f"https://github.com/{eco['library']}/commit/{sha}" if SHA.fullmatch(sha) else eco["library"]
    tag = release_tag(version)
    return "\n".join([
        f"This pull request brings symphony-protocol {version} at {vendoring.short_commit(commit)} ({link}) "
        f"into `{vendor_dir}/sdlc_protocol`, as {eco['library']}'s sync workflow made it from the library's "
        f"release {tag} (https://github.com/{eco['library']}/releases/tag/{tag}). This repository uses it "
        f"for {consumer['uses']}.",
        "",
        "## What changed in the library",
        "",
        history_section(eco, old, log),
        "",
        "## The copy's files",
        "",
        files_section(*changes),
        "",
        *release_section(consumer, release),
        "## How it lands",
        "",
        "Nothing in the copy is edited here: a change to it is made in the library, and comes back with "
        f"the library's next release. This repository's own check decides, `{consumer['check']}`, which runs "
        f"`vendoring.py verify` to hold the copy to `{vendor_dir}/VENDORED`. {landing_sentence(consumer)}",
        "",
    ])


def closing_comment(eco):
    """Why the sync closes its own pull request: the consumer's main came
    to hold the copy by another way, a consumer's own pull request that
    vendored the library's branch, and nothing is left to land."""
    return (f"Closed by {eco['library']}'s sync workflow: this repository's main already holds the copy "
            "this pull request would bring, so it has nothing left to land. The library's next release "
            "opens a new one.\n")


def one_line(text, limit=REASON_LENGTH):
    """GitHub's words on one line of a table cell: whitespace folded, a
    pipe escaped, and cut at limit."""
    folded = " ".join(text.split()).replace("|", "\\|")
    return folded if len(folded) <= limit else folded[:limit - 3].rstrip() + "..."


def auto_merge_cell(state, auto_merge, reason):
    if state in ("up to date", "closed"):
        return "-"
    if auto_merge == "on":
        return "on: it merges once the required check is green"
    if auto_merge == "refused":
        said = f'GitHub said "{one_line(reason)}"' if reason.strip() else "GitHub gave no reason"
        return (f"not armed: {said}; it needs auto-merge allowed in the repository's settings and a check "
                "its main requires (README.md, the owner's setup, steps 3 and 4)")
    return "off: the owner merges"


def summary(repository, state, url="", auto_merge="", reason=""):
    """One leg's line in the run's summary, under its own header, since
    each leg of the matrix writes a summary of its own."""
    pull = PULL_STATES[state].format(url=url)
    merge = auto_merge_cell(state, auto_merge, reason)
    return f"| Consumer | Pull request | Auto-merge |\n| --- | --- | --- |\n| {repository} | {pull} | {merge} |\n"


# --- what is read from git ---------------------------------------------------------------

def git(where, *args):
    """git's output in where, or None when it fails."""
    done = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, timeout=120)
    return done.stdout if done.returncode == 0 else None


def library_log(library, old, new):
    """The library's one-line log of sdlc_protocol/ from old to new, or
    None when old is not a commit the library's history holds."""
    old = (old or "").partition("-")[0]
    if not SHA.fullmatch(old) or git(library, "cat-file", "-e", f"{old}^{{commit}}") is None:
        return None
    shown = git(library, "log", "--oneline", f"{old}..{new}", "--", vendoring.PACKAGE)
    return None if shown is None else shown.splitlines()


def records(target, consumer):
    """The consumer's record as its last commit has it ("" when none) and
    as scripts/vendor.py left it in the checkout."""
    path = f"{consumer['vendor_dir']}/{vendoring.RECORD}"
    before = git(target, "show", f"HEAD:{path}") or ""
    return before, (Path(target) / path).read_text(encoding="ascii")


def release_of(target, consumer):
    """The version file's version as the consumer's last commit has it
    (None when that commit has no such file) and as scripts/vendor.py left
    it, or None for a consumer that names no version file."""
    if "version_file" not in consumer:
        return None
    path = consumer["version_file"]
    before = git(target, "show", f"HEAD:{path}")
    after = (Path(target) / path).read_text(encoding="utf-8")
    old = None if before is None else ecosystem.stated_version(before, path)
    return old, ecosystem.stated_version(after, path)


def body_for(eco, consumer, target, library=LIBRARY):
    before, after = records(target, consumer)
    version, commit, _ = vendoring.parse_record(after)
    old = recorded_commit(before)
    log = library_log(library, old, commit.partition("-")[0])
    copy_prefix = f"{consumer['vendor_dir']}/{vendoring.PACKAGE}/"
    porcelain = git(target, "status", "--porcelain", "--untracked-files=all", "--no-renames", "--",
                    copy_prefix) or ""
    return body(eco, consumer, version, commit, old, log, file_changes(porcelain, copy_prefix),
                release_of(target, consumer))


def version_and_commit(target, consumer):
    _, after = records(target, consumer)
    version, commit, _ = vendoring.parse_record(after)
    return version, commit


# --- the command line --------------------------------------------------------------------

def arguments(argv):
    parser = argparse.ArgumentParser(prog="python3 scripts/sync.py", description="The sync workflow's logic.")
    parser.add_argument("--library", type=Path, default=LIBRARY, metavar="DIR",
                        help="the checkout of the release the sync carries, whose ecosystem.json and history "
                             "the commands read (default: this repository)")
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("matrix", help="the consumers as a matrix, as JSON on one line")
    planning.add_argument("--public-only", action="store_true")
    sub.add_parser("setup", help="what the owner sets up before the sync can run")
    elsewhere = sub.add_parser("off-main", help="why a run off main vendored nothing")
    elsewhere.add_argument("--ref", required=True)
    following = sub.add_parser("after-release", help="why the sync does not follow the Release run that "
                                                     "started it; nothing when it does")
    following.add_argument("event", type=Path, help="the workflow_run event's payload, GITHUB_EVENT_PATH")
    following.add_argument("--repository", required=True, metavar="OWNER/NAME", help="this repository")
    sub.add_parser("newest-release", help="the newest release's tag among the tags on stdin; nothing when none")
    sub.add_parser("no-release", help="why a run with no published release vendored nothing")
    placing = sub.add_parser("misplaced", help="why the newest release's tag does not stand where release.yml "
                                               "makes one; nothing when it does")
    placing.add_argument("--tag", required=True)
    placing.add_argument("--commit", required=True, help="the commit the tag names here, or an empty word")
    carrying = sub.add_parser("carrying", help="the summary's first words when the sync goes on")
    carrying.add_argument("--tag", required=True)
    carrying.add_argument("--commit", required=True)
    for name in ("title", "message", "body"):
        pull = sub.add_parser(name, help=f"the consumer's pull request {name}")
        pull.add_argument("target", help="the consumer's checkout, after the release's scripts/vendor.py ran in it")
        pull.add_argument("--consumer", required=True, metavar="OWNER/NAME")
    closing = sub.add_parser("closing", help="the comment that closes a stale sync pull request")
    closing.add_argument("--consumer", required=True, metavar="OWNER/NAME")
    row = sub.add_parser("summary", help="one consumer's row in the run's summary")
    row.add_argument("--consumer", required=True, metavar="OWNER/NAME")
    row.add_argument("--state", required=True, choices=list(PULL_STATES))
    row.add_argument("--url", default="")
    row.add_argument("--auto-merge", default="", choices=["", *AUTO_MERGE_STATES])
    row.add_argument("--reason", default="", help="what GitHub said when it refused to arm auto-merge")
    return parser.parse_args(argv)


def answer(args, eco):
    if args.command == "matrix":
        return json.dumps(matrix(eco, args.public_only)) + "\n"
    if args.command == "setup":
        return setup_text(eco)
    if args.command == "off-main":
        return off_main_text(args.ref)
    if args.command == "after-release":
        run = json.loads(args.event.read_text(encoding="utf-8")).get("workflow_run") or {}
        return after_release_text(run, args.repository) or ""
    if args.command == "newest-release":
        tag = newest_release(sys.stdin.read().splitlines())
        return f"{tag}\n" if tag else ""
    if args.command == "no-release":
        return no_release_text(eco)
    if args.command == "misplaced":
        return misplaced_text(eco, args.tag, args.commit) or ""
    if args.command == "carrying":
        return carrying_text(eco, args.tag, args.commit)
    consumer = ecosystem.consumer_named(eco, args.consumer)
    if args.command == "closing":
        return closing_comment(eco)
    if args.command == "summary":
        return summary(consumer["repository"], args.state, args.url, args.auto_merge, args.reason)
    if args.command == "body":
        return body_for(eco, consumer, args.target, args.library)
    version, commit = version_and_commit(args.target, consumer)
    if args.command == "title":
        return title(version, commit) + "\n"
    return message(eco, consumer, version, commit, release_of(args.target, consumer))


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.write(answer(args, ecosystem.load(args.library / "ecosystem.json")))
    except (ecosystem.EcosystemError, OSError, ValueError) as problem:
        print(f"sync: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
