"""The sync's logic, which .github/workflows/sync.yml and ci.yml call.

  python3 scripts/sync.py matrix [--public-only]
  python3 scripts/sync.py setup
  python3 scripts/sync.py title   <consumer checkout> --consumer OWNER/NAME
  python3 scripts/sync.py message <consumer checkout> --consumer OWNER/NAME
  python3 scripts/sync.py body    <consumer checkout> --consumer OWNER/NAME
  python3 scripts/sync.py summary --consumer OWNER/NAME --state STATE [--url URL] [--auto-merge on|unavailable]

A push to main that changes the package makes sync.yml vendor it into
every consumer ecosystem.json lists and open, or update, the pull request
sdlc-protocol/sync there; ci.yml's downstream job runs each consumer's
own check on a pull request's copy before it merges here. The workflows
only run git, gh and these commands, so what they decide is here, where
it is tested: the matrix of consumers, what the owner must set up when
the sync cannot run, and the commit message, the title and the body of a
consumer's pull request, built from the consumer's record before and after
scripts/vendor.py ran in its checkout. The body is written for the person
who reviews the pull request there: what changed in the library since the
commit the copy named before, which files of the copy moved, what the
consumer uses the library for, the release it makes where the consumer
names a version file (the plugin's version, which scripts/vendor.py raised
in the same checkout), and that the consumer's own check decides.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import ecosystem
from ecosystem import vendoring  # the package's vendoring.py, loaded by its path alone

LIBRARY = ecosystem.ROOT
BRANCH = "sdlc-protocol/sync"
SHA = re.compile(r"[0-9a-f]{40}")
AUTO_MERGE_STATES = {
    "on": "on: it merges once the required check is green",
    "unavailable": "not enabled here: allow auto-merge in the repository's settings",
}


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
    public = [c["repository"] for c in eco["consumers"] if c["auto_merge"]]
    allow = "\n".join(f"   gh api -X PATCH repos/{name} -F allow_auto_merge=true" for name in public)
    return (
        "## The sync is not set up\n\n"
        "The sync opens its pull requests with the token of the GitHub App OpenSpine SDLC, and this "
        "repository has no variable SDLC_APP_ID, so the sync was skipped and the run stays green. "
        "The owner sets it up once (README.md, \"The owner's one-time setup for the sync\"):\n\n"
        f"1. Install the App on {library} and on every consumer: "
        f"{', '.join(c['repository'] for c in eco['consumers'])}.\n"
        "2. On this repository, set the App's id and its private key, as on sdlc-template:\n\n"
        "   ```\n"
        f"   gh variable set SDLC_APP_ID --repo {library} --body <the App's id>\n"
        f"   gh secret set SDLC_APP_PRIVATE_KEY --repo {library} < <the App's private key file>\n"
        "   ```\n\n"
        "3. Allow auto-merge on the public consumers:\n\n"
        "   ```\n"
        f"{allow}\n"
        "   ```\n\n"
        "4. Require the check named check on each consumer's main, so auto-merge waits for it.\n")


def title(version, commit):
    return f"Vendor sdlc-protocol {version} ({vendoring.short_commit(commit)})"


def message(eco, consumer, version, commit, release=None):
    """The sync's commit message in the consumer: the title, then why."""
    text = (f"{title(version, commit)}\n\n"
            f"Made by {eco['library']}'s sync workflow, which copies the library into\n"
            f"{consumer['vendor_dir']}/sdlc_protocol as ecosystem.json says and records the copy in\n"
            f"{consumer['vendor_dir']}/VENDORED. The copy is never edited here; a change to it\n"
            "is made in the library and comes back by the next sync.\n")
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
    return "Its owner merges it: GitHub Free offers auto-merge only on public repositories."


def body(eco, consumer, version, commit, old, log, changes, release=None):
    """The consumer's pull request body. log is the library's one-line log
    from old to commit, or None when old is no commit the library has;
    release is the version file's (before, after), or None."""
    vendor_dir = consumer["vendor_dir"]
    sha = commit.partition("-")[0]
    link = f"https://github.com/{eco['library']}/commit/{sha}" if SHA.fullmatch(sha) else eco["library"]
    return "\n".join([
        f"This pull request brings sdlc-protocol {version} at {vendoring.short_commit(commit)} ({link}) "
        f"into `{vendor_dir}/sdlc_protocol`, as {eco['library']}'s sync workflow made it from the library's "
        f"main. This repository uses it for {consumer['uses']}.",
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
        "Nothing in the copy is edited here: a change to it is made in the library, and the next sync "
        f"brings it. This repository's own check decides, `{consumer['check']}`, which runs "
        f"`vendoring.py verify` to hold the copy to `{vendor_dir}/VENDORED`. {landing_sentence(consumer)}",
        "",
    ])


def summary(repository, state, url="", auto_merge=""):
    """One leg's line in the run's summary, under its own header, since
    each leg of the matrix writes a summary of its own."""
    pull = f"{state}: {url}" if url else state
    merge = AUTO_MERGE_STATES.get(auto_merge, "off: the owner merges" if url else "-")
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
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("matrix", help="the consumers as a matrix, as JSON on one line")
    planning.add_argument("--public-only", action="store_true")
    sub.add_parser("setup", help="what the owner sets up before the sync can run")
    for name in ("title", "message", "body"):
        pull = sub.add_parser(name, help=f"the consumer's pull request {name}")
        pull.add_argument("target", help="the consumer's checkout, after scripts/vendor.py ran in it")
        pull.add_argument("--consumer", required=True, metavar="OWNER/NAME")
    row = sub.add_parser("summary", help="one consumer's row in the run's summary")
    row.add_argument("--consumer", required=True, metavar="OWNER/NAME")
    row.add_argument("--state", required=True, help="up to date, opened or updated")
    row.add_argument("--url", default="")
    row.add_argument("--auto-merge", default="", choices=["", *AUTO_MERGE_STATES])
    return parser.parse_args(argv)


def answer(args, eco):
    if args.command == "matrix":
        return json.dumps(matrix(eco, args.public_only)) + "\n"
    if args.command == "setup":
        return setup_text(eco)
    consumer = ecosystem.consumer_named(eco, args.consumer)
    if args.command == "summary":
        return summary(consumer["repository"], args.state, args.url, args.auto_merge)
    if args.command == "body":
        return body_for(eco, consumer, args.target)
    version, commit = version_and_commit(args.target, consumer)
    if args.command == "title":
        return title(version, commit) + "\n"
    return message(eco, consumer, version, commit, release_of(args.target, consumer))


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.write(answer(args, ecosystem.load(LIBRARY / "ecosystem.json")))
    except (ecosystem.EcosystemError, OSError, ValueError) as problem:
        print(f"sync: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
