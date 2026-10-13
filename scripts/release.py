"""Which versions main carries that have no tag yet, for the release workflow.

A release of the library is the version in sdlc_protocol/__init__.py.
.github/workflows/release.yml tags each version at the first commit of
main's first-parent line that carries it, the merge of the pull request
that raised it, as v0.2.0 was tagged by hand, and publishes a GitHub
release for the tag once the owner approves the run, so nobody has to
remember to and nothing is released without that approval. This script
says what to tag: walking that line from the newest commit back until a
version that has a tag, where the releases already made begin, every
version met on the way, at the oldest commit that carries it. A release a
run missed, in a burst of merges or a failed run, is made by the next one,
and a tag that exists is never moved, wherever it points. It also writes
what the run's summary tells the owner before the approval: the releases
the run would make, or that there is none to make.

Where a tag stands is checked here too, for release.yml's plan and for the
sync (scripts/sync.py): a tag stands only at a commit of main's first-parent
line that raised it to the tag's version, the commit that carries the
version while its first parent does not, which is where missing() always
puts one. A tag anywhere else, made by hand or moved after its release, is
someone else's, and neither workflow releases or carries it.

  missing    one line per tag to make, oldest first: "<tag> <commit>"
  version    the version the checkout carries, as the workflow names it
  check-tag  nothing when a commit is where the tag stands; else why not,
             on one line, and exit 1
  summary    the summary's words for the lines "<tag> <commit>" on stdin,
             each followed by "tagged" for a tag made before the run
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = "sdlc_protocol/__init__.py"
VERSION_LINE = re.compile(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)
TAG_PREFIX = "v"
RELEASE_TAG = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)")
SHA = re.compile(r"[0-9a-f]{40}")
# What the plan writes after the commit of a tag that exists already, which
# the workflow releases without making it.
TAGGED = "tagged"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def version_in(text):
    """The version a version file states, or None when it states none."""
    match = VERSION_LINE.search(text or "")
    return match.group(1) if match else None


def version_at(commit, cwd):
    """The version a commit carries, or None when it has no version file."""
    try:
        return version_in(git("cat-file", "blob", f"{commit}:{VERSION_FILE}", cwd=cwd))
    except subprocess.CalledProcessError:
        return None


def current(cwd):
    """The version the working tree carries, or None."""
    return version_in((Path(cwd) / VERSION_FILE).read_text(encoding="ascii"))


def untagged_line(ref, cwd):
    """(tag, commit) for each commit of ref's first-parent line, newest
    first, down to the first one whose version has a tag or that has no
    version file."""
    made = set(git("tag", "--list", f"{TAG_PREFIX}*", cwd=cwd).split())
    line = []
    for commit in git("rev-list", "--first-parent", ref, cwd=cwd).split():
        version = version_at(commit, cwd)
        tag = f"{TAG_PREFIX}{version}" if version else None
        if tag is None or tag in made:
            break
        line.append((tag, commit))
    return line


def missing(cwd, ref="HEAD"):
    """(tag, commit) for every version on ref's first-parent line that has
    no tag, oldest first, each at the oldest commit that carries it."""
    oldest = {}
    for tag, commit in reversed(untagged_line(ref, cwd)):
        oldest.setdefault(tag, commit)
    return list(oldest.items())


def version_of_tag(tag):
    """The version a release tag names, or None for any other name."""
    match = RELEASE_TAG.fullmatch(tag or "")
    return match.group(1) if match else None


def raised(version, cwd, ref="HEAD"):
    """The commits of ref's first-parent line that raised it to version,
    newest first: each carries the version and its first parent does not.
    missing() always tags one of them, the oldest commit of the stretch it
    walks that carries the version, whose first parent is either in that
    stretch with another version or is where the walk stopped."""
    line = git("rev-list", "--first-parent", ref, cwd=cwd).split()
    versions = [version_at(commit, cwd) for commit in line]
    below = versions[1:] + [None]
    return [commit for commit, here, under in zip(line, versions, below) if here == version and under != version]


def misplaced(tag, commit, cwd, ref="HEAD"):
    """Why commit is not where tag stands, on one line, or None when it is:
    one of the commits that raised ref's first-parent line, main's in both
    workflows, to the tag's version."""
    version = version_of_tag(tag)
    if version is None:
        return f"{tag or 'the tag'} is not a release tag of the form v<major>.<minor>.<patch>"
    if not SHA.fullmatch(commit or ""):
        return f"the tag {tag} names no commit this checkout holds"
    try:
        found = raised(version, cwd, ref)
    except subprocess.CalledProcessError:
        return f"this checkout holds no first-parent line to find where {tag} stands"
    if commit in found:
        return None
    if found:
        where = f"main's first-parent line was raised to {version} at {', '.join(c[:7] for c in found)}"
    else:
        where = f"main's first-parent line never carries {version}"
    return f"the tag {tag} points at {commit[:7]}, but {where}"


def due_from(text):
    """The (tag, commit, tagged) of the lines "<tag> <commit> [tagged]" the
    workflow's plan writes, blank lines left out: tagged is whether the tag
    was made before the run."""
    found = []
    for line in text.splitlines():
        words = line.split()
        if words:
            found.append((words[0], words[1] if len(words) > 1 else "", words[2:] == [TAGGED]))
    return found


def made(commit, tagged):
    """What one release does, as the summary says it: the commit is named
    either way, so the owner sees what the approval releases."""
    if tagged:
        return f"its tag was made before, at {commit}; it gets its release"
    return f"tags {commit} and publishes its release"


def summary(due):
    """What the run's summary tells the owner before the approval: the
    releases the run makes once approved, oldest first, or that there are
    none, when the job that waits for the approval does not run."""
    if not due:
        return ("## Nothing to release\n\n"
                "Every version main carries, back to the newest tag, has its release, so this run asks for "
                "no approval and tags nothing.\n")
    lines = [f"- {tag}: {made(commit, tagged)}" for tag, commit, tagged in due]
    return ("## Waiting for the owner's approval\n\n"
            "Once the owner approves this run in the environment release, with Review deployments at the top "
            "of its page, the job release makes these releases, oldest first, and the sync then carries the "
            "newest release into every consumer:\n\n" + "\n".join(lines) + "\n")


def arguments(argv):
    parser = argparse.ArgumentParser(description="What the release workflow tags.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("missing", help="each tag to make, oldest first")
    sub.add_parser("version", help="the version the checkout carries")
    checking = sub.add_parser("check-tag", help="nothing when the commit is where the tag stands; else why not")
    checking.add_argument("tag")
    checking.add_argument("commit", help="the commit the tag names, or an empty word when it names none")
    sub.add_parser("summary", help="the summary's words for the plan's lines on stdin")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    if args.command == "summary":
        sys.stdout.write(summary(due_from(sys.stdin.read())))
        return 0
    if args.command == "check-tag":
        why = misplaced(args.tag, args.commit, ROOT)
        if why:
            print(why)
            return 1
        return 0
    if args.command == "version":
        version = current(ROOT)
        if version is None:
            print(f"{VERSION_FILE} states no version of three numbers", file=sys.stderr)
            return 1
        print(version)
        return 0
    for tag, commit in missing(ROOT):
        print(tag, commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
