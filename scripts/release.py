"""Which versions main carries that have no tag yet, for the release workflow.

A release of the library is the version in sdlc_protocol/__init__.py.
.github/workflows/release.yml tags each version at the first commit of
main's first-parent line that carries it, the merge of the pull request
that raised it, as v0.2.0 was tagged by hand, and publishes a GitHub
release for the tag, so nobody has to remember to. This script says what
to tag: walking that line from the newest commit back until a version that
has a tag, where the releases already made begin, every version met on the
way, at the oldest commit that carries it. A release a run missed, in a
burst of merges or a failed run, is made by the next one, and a tag that
exists is never moved, wherever it points.

  missing   one line per tag to make, oldest first: "<tag> <commit>"
  version   the version the checkout carries, as the workflow names it
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="What the release workflow tags.")
    parser.add_argument("command", choices=("missing", "version"))
    args = parser.parse_args(argv)
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
