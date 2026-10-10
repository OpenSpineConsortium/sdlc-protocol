#!/usr/bin/env bash
# Copy the package into another repository as its tools/sdlc_protocol, and
# record there which version and which commit the copy is.
#
#   usage: scripts/vendor.sh <target repository>
#
# The template and the plugin run the protocol from a copy in their own
# tree rather than from an install, so a workflow step or a hook needs
# nothing but python3. The copy replaces whatever tools/sdlc_protocol held,
# so a module removed here is removed there too, and Python's caches stay
# behind. tools/VENDORED gets one line, "sdlc-protocol <version> <commit>",
# for the target's own test to compare with the copy; the commit carries
# "-dirty" when the package had changes not yet committed, so the line never
# names a commit the copy does not match, and is "unknown" outside a git
# checkout. Nothing in the target is committed: that is the target's own
# pull request.
set -eu

usage="usage: scripts/vendor.sh <target repository>"
if [ "$#" -ne 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "$usage" >&2
    exit 2
fi

here="$(cd "$(dirname "$0")/.." && pwd -P)"
if [ ! -d "$1" ]; then
    echo "vendor: $1 is not a directory; $usage" >&2
    exit 1
fi
target="$(cd "$1" && pwd -P)"
if [ "$target" = "$here" ]; then
    echo "vendor: $target is this repository; name the repository that takes the copy" >&2
    exit 1
fi

# The version as the package states it, read without importing the package.
version="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' "$here/sdlc_protocol/__init__.py")"
if [ -z "$version" ]; then
    echo "vendor: sdlc_protocol/__init__.py states no __version__" >&2
    exit 1
fi

# The commit the copy is, when this directory is a git checkout of its own.
commit_of_package() {
    local top
    top="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null)" || top=""
    if [ "$top" != "$here" ] || ! git -C "$here" rev-parse --verify --quiet HEAD >/dev/null; then
        echo "unknown"
        return
    fi
    local sha
    sha="$(git -C "$here" rev-parse HEAD)"
    if [ -n "$(git -C "$here" status --porcelain -- sdlc_protocol)" ]; then
        sha="$sha-dirty"
    fi
    echo "$sha"
}
commit="$(commit_of_package)"

dest="$target/tools/sdlc_protocol"
mkdir -p "$target/tools"
rm -rf "$dest"
cp -R "$here/sdlc_protocol" "$dest"
find "$dest" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$dest" -name '*.pyc' -type f -exec rm -f {} +
printf 'sdlc-protocol %s %s\n' "$version" "$commit" > "$target/tools/VENDORED"

count="$(find "$dest" -type f | wc -l | tr -d ' ')"
echo "vendor: copied sdlc_protocol/ ($count files) to $dest, replacing what was there"
echo "vendor: wrote $target/tools/VENDORED: sdlc-protocol $version $commit"
