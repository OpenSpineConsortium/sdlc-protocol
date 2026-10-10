"""Copy the package into a consumer's repository and record what the copy is.

  usage: python3 scripts/vendor.py <target repository> [--consumer OWNER/NAME]

The consumers run the protocol from a copy in their own tree rather than
from an install, so a workflow step or a hook needs nothing but python3.
Which repository takes which modules, and where, is ecosystem.json's to
say: the consumer is the one --consumer names, else the one the target's
remote origin names. The copy replaces whatever <vendor_dir>/sdlc_protocol
held, so a module removed here is removed there too, and Python's caches
stay behind. <vendor_dir>/VENDORED records the version, the commit and the
sha256 of every copied file, which the consumer's own check holds the copy
to with vendoring.py.

The commit is the last one that changed sdlc_protocol/, not HEAD, so a
merge commit on main records the same commit its pull request's branch
did, and a change elsewhere in this repository brings no new copy. It
carries "-dirty" when the package has changes not yet committed, so the
record never names a commit the copy does not match, and is "unknown"
outside a git checkout. Nothing in the target is committed: that is the
target's own pull request, which the sync workflow opens.

A consumer that ships its copy on to users of its own names a version
file in ecosystem.json, as the plugin marketplace names its plugin.json:
Claude Code keeps an installed plugin on its cached copy until the version
in the plugin's manifest changes, so a new guard that landed there without
a new version would never reach a session. Whenever the copy's files
change, this raises that file's patch number in the same run, and so in
the sync's same commit; a run that leaves the files as they were leaves
the version alone, so a second run still changes nothing. A version file
that is missing, or states no MAJOR.MINOR.PATCH version, is refused before
anything is written.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import ecosystem
from ecosystem import EcosystemError
from ecosystem import vendoring  # the package's vendoring.py, loaded by its path alone

LIBRARY = ecosystem.ROOT
VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
GITHUB_REMOTE = re.compile(
    r"(?:https://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?")


def git(where, *args):
    """git's answer in where, stripped, or None when git fails or is not
    there."""
    try:
        done = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def version_of(library):
    """The version the package states, read without importing it."""
    found = VERSION_LINE.search((library / vendoring.PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    if not found:
        raise EcosystemError("sdlc_protocol/__init__.py states no __version__")
    return found.group(1)


def commit_of(library):
    """The commit the package's files are, by the rule above."""
    top = git(library, "rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != library.resolve():
        return "unknown"
    sha = git(library, "log", "-1", "--format=%H", "--", vendoring.PACKAGE)
    if not sha:
        return "unknown"
    if git(library, "status", "--porcelain", "--", vendoring.PACKAGE):
        sha += "-dirty"
    return sha


def repository_of_remote(url):
    """OWNER/NAME from a GitHub remote URL, https or ssh, or None."""
    found = GITHUB_REMOTE.fullmatch(url.strip())
    return found.group(1) if found else None


def choose_consumer(eco, target, named):
    """The consumer --consumer names, else the one the target's origin is."""
    if named:
        return ecosystem.consumer_named(eco, named)
    url = git(target, "remote", "get-url", "origin")
    repository = repository_of_remote(url) if url else None
    if repository is None:
        names = ", ".join(consumer["repository"] for consumer in eco["consumers"])
        raise EcosystemError(f"{target} has no GitHub remote origin to name its consumer; "
                             f"pass --consumer OWNER/NAME, one of {names}")
    return ecosystem.consumer_named(eco, repository)


def load_ecosystem(library):
    """ecosystem.json, refused when anything in it is wrong, a copy whose
    modules import one it leaves out among them: such a copy could not run."""
    eco = ecosystem.load(library / "ecosystem.json")
    found = ecosystem.problems(eco, library / vendoring.PACKAGE)
    if found:
        raise EcosystemError("ecosystem.json cannot be acted on: " + "; ".join(found))
    return eco


def state_of(vendor_dir):
    """What a consumer's copy and record hold now, to tell a change apart
    from a run that found nothing to do."""
    record = vendor_dir / vendoring.RECORD
    copy = vendor_dir / vendoring.PACKAGE
    recorded = record.read_bytes() if record.is_file() else None
    return recorded, (vendoring.digests_of(copy) if copy.is_dir() else None)


def read_version_file(target, consumer):
    """The consumer's version file as (path, its version, its text with the
    next patch version), or None when ecosystem.json names none for it.
    Read before anything is written, so a file that cannot be raised
    refuses the whole run instead of leaving a copy with no release."""
    if "version_file" not in consumer:
        return None
    path = target / consumer["version_file"]
    if not path.is_file():
        raise EcosystemError(f"{path} is not there, and ecosystem.json names it as "
                             f"{consumer['repository']}'s version file")
    text = path.read_bytes().decode("utf-8")
    version = ecosystem.stated_version(text, path)
    return path, version, ecosystem.with_version(text, ecosystem.next_patch(version), path)


def release(version_file, copy_changed):
    """Raise the version file when the copy's files changed, and return the
    line that says what happened to it."""
    if version_file is None:
        return []
    path, version, raised = version_file
    if not copy_changed:
        return [f"vendor: the copy's files are unchanged, so {path} stays at {version}"]
    path.write_bytes(raised.encode("utf-8"))
    return [f"vendor: the copy's files changed, so {path} goes from {version} to "
            f"{ecosystem.stated_version(raised, path)}, the release that takes the new copy to its users"]


def replace_copy(library, files, copy):
    shutil.rmtree(copy, ignore_errors=True)
    for name in files:
        (copy / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(library / vendoring.PACKAGE / name, copy / name)


def vendor(target, named=None, library=LIBRARY):
    """Copy and record, and return the lines that say what was done."""
    if not target.is_dir():
        raise EcosystemError(f"{target} is not a directory")
    if target.resolve() == library.resolve():
        raise EcosystemError(f"{target.resolve()} is this repository; name the repository that takes the copy")
    eco = load_ecosystem(library)
    consumer = choose_consumer(eco, target, named)
    files = ecosystem.files_for(eco, consumer, library / vendoring.PACKAGE)
    version, commit = version_of(library), commit_of(library)
    vendor_dir = target / consumer["vendor_dir"]
    copy, record = vendor_dir / vendoring.PACKAGE, vendor_dir / vendoring.RECORD
    version_file = read_version_file(target, consumer)
    before = state_of(vendor_dir)
    replace_copy(library, files, copy)
    record.write_text(vendoring.record_text(version, commit, vendoring.digests_of(copy)), encoding="ascii",
                      newline="\n")
    after = state_of(vendor_dir)
    released = release(version_file, copy_changed=before[1] != after[1])
    return report(consumer, files, copy, record, version, commit, before == after) + released


def report(consumer, files, copy, record, version, commit, unchanged):
    taken = "all of sdlc_protocol/" if consumer["modules"] == ecosystem.ALL else ", ".join(files)
    lines = [f"vendor: {consumer['repository']} takes {taken} ({len(files)} files) into {copy}",
             f"vendor: wrote {record}: sdlc-protocol {version} {commit}, {len(files)} file hashes"]
    lines.append("vendor: the copy and its record were already this; nothing changed" if unchanged
                 else "vendor: replaced the copy and its record; review the change there and land it by pull request")
    return lines


def arguments(argv):
    parser = argparse.ArgumentParser(
        prog="python3 scripts/vendor.py",
        description="Copy sdlc_protocol into a consumer's repository, as ecosystem.json says, and record it.")
    parser.add_argument("target", help="the consumer's checkout")
    parser.add_argument("--consumer", metavar="OWNER/NAME",
                        help="the consumer in ecosystem.json (default: the one the target's origin names)")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    try:
        lines = vendor(Path(args.target), args.consumer)
    except (EcosystemError, OSError, ValueError) as problem:
        print(f"vendor: {problem}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
