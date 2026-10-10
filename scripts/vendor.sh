#!/usr/bin/env bash
# Copy the package into a consumer's repository and record there which
# version, which commit and which file hashes the copy is.
#
#   usage: scripts/vendor.sh <target repository> [--consumer OWNER/NAME]
#
# A wrapper over scripts/vendor.py, which does the work by ecosystem.json
# and says what it did; this name stays because the consumers' documents
# and people's hands run it. Nothing in the target is committed: that is
# the target's own pull request, which the sync workflow opens.
set -eu

usage="usage: scripts/vendor.sh <target repository> [--consumer OWNER/NAME]"
if [ "$#" -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "$usage" >&2
    exit 2
fi

exec python3 "$(dirname "$0")/vendor.py" "$@"
