#!/usr/bin/env bash
# The library's checks, run by CI (.github/workflows/ci.yml, the job named
# "check") and by hand before a pull request. Every text file comes first:
# the package is vendored into other repositories' tools/, whose own checks
# take only ASCII with LF endings, so a stray byte here breaks every
# consumer's next vendoring. Then the whole test suite, which the template
# and the other consumers trust in place of their own tests of these rules.
set -eu
cd "$(dirname "$0")/.."

python3 - <<'EOF'
"""One line per file that is not plain ASCII with LF endings and a final
newline; exit 1 if there is any."""
import subprocess
import sys

listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                        capture_output=True, check=True).stdout
problems = []
for name in sorted({n.decode() for n in listed.split(b"\0") if n}):
    try:
        data = open(name, "rb").read()
    except FileNotFoundError:
        continue  # removed from the working tree, still in the index
    if any(byte > 127 for byte in data):
        problems.append(f"{name}: a byte outside ASCII")
    if b"\r" in data:
        problems.append(f"{name}: a carriage return; use LF line endings")
    if data and not data.endswith(b"\n"):
        problems.append(f"{name}: no newline at the end")
print("\n".join(problems) or "plain text ok")
sys.exit(1 if problems else 0)
EOF

python3 -m unittest discover -s tests -v

echo "checks ok"
