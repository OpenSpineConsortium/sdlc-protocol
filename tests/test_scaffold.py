"""The repository around the package: the spec, the build file and the
workflows.

The spec reaches readers who never see the template, so a {{placeholder}}
left in it is a hole in the protocol, and a policy field or world method
it does not name is one nobody can learn about. pyproject.toml is what an
install reads, so it must parse and agree with the package it builds. The
workflows are what carry a change to the consumers: the check job must
keep the name branch protection requires, the downstream job must run a
leg per consumer and skip the one it cannot read, the sync must run on a
push to main that changes the package, never from another branch and
never two at once, must take as its own only a pull request from its own
repository, must arm auto-merge without ever merging, and must push only
what the branch does not already hold. The release must run on a push to
main that changes the version or what tags it, never from another branch
and never two at once, with a token that writes contents and nothing
else, must push nothing and must make a tag only where the script found
one missing, so none is moved. No workflow may put an expression inside a
shell step, where a value would run as code, and every shell step must at
least parse. The copy itself,
scripts/vendor.py and its record, is tests/test_vendor.py's and
tests/test_vendoring.py's.
"""

import dataclasses
import re
import subprocess
import sys
import textwrap
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sdlc_protocol  # noqa: E402
from sdlc_protocol import gate  # noqa: E402

PACKAGE = ROOT / "sdlc_protocol"
SPEC = ROOT / "spec" / "PROTOCOL.md"
WORKFLOWS = ROOT / ".github" / "workflows"
PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")


def headings(markdown):
    """The document's headings, without the lines of fenced code, where a
    "#" starts an example, not a section."""
    found, fenced = [], False
    for line in markdown.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            found.append(line.lstrip("#").strip())
    return found


def policy_fields():
    return [field.name for field in dataclasses.fields(gate.Policy)]


def world_methods():
    """The methods gate.World asks of every world, in the order it lists
    them."""
    return [name for name, value in vars(gate.World).items()
            if callable(value) and not name.startswith("_")]


class Spec(unittest.TestCase):
    def setUp(self):
        self.text = SPEC.read_text(encoding="ascii")

    def test_names_no_placeholder(self):
        self.assertEqual(PLACEHOLDER.findall(self.text), [])

    def test_keeps_the_template_sections_and_adds_one(self):
        self.assertEqual(headings(self.text), [
            "How work is claimed", "Labels", "Pick", "Claim", "Announce", "Work in the open", "Finish",
            "Blockers", "Release", "Team mode", "Policy and worlds", "Rules that hold everywhere",
        ])

    def test_names_every_policy_field_and_world_method(self):
        for name in policy_fields() + world_methods():
            self.assertIn(name, self.text)

    def test_the_readme_names_them_too(self):
        readme = (ROOT / "README.md").read_text(encoding="ascii")
        for name in policy_fields() + world_methods():
            self.assertIn(name, readme)


class Pyproject(unittest.TestCase):
    def setUp(self):
        self.data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.project = self.data["project"]
        self.setuptools = self.data["tool"]["setuptools"]

    def test_names_the_package_with_its_own_version(self):
        self.assertEqual(self.project["name"], "sdlc-protocol")
        self.assertEqual(self.project["version"], sdlc_protocol.__version__)

    def test_has_no_dependencies(self):
        self.assertEqual(self.project["dependencies"], [])
        self.assertEqual(self.project["requires-python"], ">=3.12")

    def test_lists_every_package_directory(self):
        found = {".".join(path.parent.relative_to(ROOT).parts) for path in PACKAGE.rglob("*.py")
                 if "__pycache__" not in path.parts}
        self.assertEqual(set(self.setuptools["packages"]) - {"sdlc_protocol.spec"}, found)

    def test_ships_the_spec_inside_the_package(self):
        source = self.setuptools["package-dir"]["sdlc_protocol.spec"]
        self.assertIn("sdlc_protocol.spec", self.setuptools["packages"])
        self.assertEqual(ROOT / source / "PROTOCOL.md", SPEC)
        self.assertIn("*.md", self.setuptools["package-data"]["sdlc_protocol.spec"])

    def test_names_a_license_file_that_exists(self):
        for name in self.project["license-files"]:
            self.assertTrue((ROOT / name).is_file(), name)


def workflow(name):
    return (WORKFLOWS / name).read_text(encoding="ascii")


def code_lines(text):
    """The lines that are not comments, YAML's or the shell's."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def token_permissions(text):
    """The permission-<name> inputs a workflow hands the App's token."""
    return dict(re.findall(r"(?m)^\s+permission-([a-z-]+): (\w+)$", text))


def shell_steps(text):
    """The lines of each run: step, the key's own line with the block
    under it, as (first line number, text)."""
    lines, steps = text.splitlines(), []
    for number, line in enumerate(lines):
        key = re.match(r"^(\s*)(?:- )?run:", line)
        if not key:
            continue
        indent, block = len(key.group(1)), [line]
        for follower in lines[number + 1:]:
            if follower.strip() and len(follower) - len(follower.lstrip()) <= indent:
                break
            block.append(follower)
        steps.append((number + 1, "\n".join(block)))
    return steps


def shell_script(step):
    """The script a run: step hands bash: the rest of the key's line, or
    the block under it, dedented."""
    key, *block = step.splitlines()
    inline = key.split("run:", 1)[1].strip()
    return textwrap.dedent("\n".join(block)) if inline.startswith("|") else inline


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = workflow("ci.yml")

    def test_the_job_is_named_check(self):
        self.assertRegex(self.text, r"(?m)^jobs:\n  check:\n")

    def test_runs_the_checks_on_python_3_12(self):
        self.assertIn('python-version: "3.12"', self.text)
        self.assertIn("run: bash scripts/check.sh", self.text)

    def test_the_downstream_job_runs_each_consumers_own_check(self):
        self.assertIn("  downstream:\n    name: downstream (${{ matrix.name }})\n", self.text)
        self.assertIn("matrix: ${{ fromJSON(needs.consumers.outputs.matrix) }}", self.text)
        self.assertIn("fail-fast: false", self.text)
        self.assertIn('run: python3 symphony-protocol/scripts/vendor.py "$NAME" --consumer "$CONSUMER"', self.text)
        self.assertIn('run: bash -c "$CHECK"', self.text)

    def test_a_consumer_that_is_not_public_is_read_with_the_apps_token_unless_from_a_fork(self):
        """A fork's run gets no secrets, though it gets the variables, so
        without the fork guard the App's token step would fail on an empty
        key and turn the leg red instead of skipping it."""
        self.assertIn("FROM_FORK: ${{ github.event_name == 'pull_request' && "
                      "github.event.pull_request.head.repo.full_name != github.repository }}", self.text)
        self.assertIn('if [ "$VISIBILITY" = "public" ]; then', self.text)
        self.assertIn('elif [ -n "$APP_ID" ] && [ "$FROM_FORK" != "true" ]; then', self.text)
        self.assertIn("token: ${{ steps.app-token.outputs.token || github.token }}", self.text)

    def test_the_downstream_token_only_reads(self):
        self.assertEqual(token_permissions(self.text), {"contents": "read"})


class SyncWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = workflow("sync.yml")

    def test_runs_on_a_push_to_main_that_changes_what_the_copies_are_made_of(self):
        self.assertIn("  push:\n    branches: [main]\n    paths:\n", self.text)
        for path in ("sdlc_protocol/**", "ecosystem.json", "scripts/vendor.py", "scripts/sync.py",
                     "scripts/ecosystem.py"):
            self.assertIn(f'      - "{path}"\n', self.text)

    def test_runs_by_hand_and_weekly_off_the_hour(self):
        self.assertIn("  workflow_dispatch:\n", self.text)
        minute = re.search(r'- cron: "(\d+) ', self.text).group(1)
        self.assertNotEqual(minute, "0")

    def test_a_run_off_main_vendors_nothing_and_says_why(self):
        """A run by hand can name any branch, and the sync would vendor
        that branch, unreviewed, into every consumer. The ref is compared
        by name: a scheduled run's event carries no repository to read the
        default branch from."""
        self.assertIn("REF: ${{ github.ref }}", self.text)
        self.assertRegex(self.text, r'if \[ "\$REF" != "refs/heads/main" \]; then\n'
                                    r'\s+echo "enabled=false" >> "\$GITHUB_OUTPUT"\n'
                                    r'\s+python3 scripts/sync.py off-main --ref "\$REF"')

    def test_one_sync_at_a_time_and_a_newer_push_waits(self):
        self.assertIn("concurrency:\n  group: sync\n  cancel-in-progress: false\n", self.text)

    def test_reads_with_its_own_token_and_writes_with_the_apps(self):
        self.assertIn("permissions:\n  contents: read\n", self.text)
        self.assertIn("uses: actions/create-github-app-token@v1", self.text)
        self.assertIn("repositories: ${{ matrix.name }}", self.text)
        self.assertIn("if: needs.plan.outputs.enabled == 'true'", self.text)

    def test_the_token_holds_only_what_the_job_uses(self):
        self.assertEqual(token_permissions(self.text), {"contents": "write", "pull-requests": "write"})

    def test_pushes_only_its_own_branch(self):
        pushes = [line.strip() for line in code_lines(self.text) if "git push" in line]
        self.assertEqual(pushes, ['git push --force origin "HEAD:refs/heads/$BRANCH"'])
        self.assertIn("BRANCH: symphony-protocol/sync", self.text)

    def test_pushes_only_a_copy_the_branch_does_not_already_hold(self):
        self.assertIn("""[ "$(git rev-parse 'FETCH_HEAD^{tree}')" = "$(git write-tree)" ]""", self.text)
        self.assertIn("""[ "$(git rev-parse 'FETCH_HEAD^')" = "$(git rev-parse HEAD)" ]""", self.text)
        guard = self.text.index("if ! already_pushed; then")
        self.assertLess(guard, self.text.index('git push --force origin "HEAD:refs/heads/$BRANCH"'))

    def test_takes_as_its_own_only_a_pull_request_from_its_own_repository(self):
        """gh's --head matches the branch's name in any repository, so a
        fork's pull request from a branch of the same name is listed too."""
        listings = [line for line in code_lines(self.text) if "gh pr list" in line]
        self.assertEqual(len(listings), 1)
        self.assertIn("--json url,isCrossRepository", listings[0])
        self.assertIn("""--jq '[.[] | select(.isCrossRepository | not)][0].url // ""'""", self.text)

    def test_arms_auto_merge_and_never_merges(self):
        """gh pr merge --auto merges at once a pull request GitHub does not
        count as blocked; the mutation only arms auto-merge, by a merge
        commit."""
        self.assertEqual([line for line in code_lines(self.text) if "gh pr merge" in line], [])
        self.assertIn("enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: MERGE})", self.text)
        self.assertIn('if [ "$AUTO_MERGE" = "true" ]; then', self.text)

    def test_a_consumer_up_to_date_is_reported_and_its_stale_pull_request_closed(self):
        start = self.text.index('if [ -z "$(git status --porcelain)" ]; then')
        block = self.text[start:self.text.index("exit 0", start)]
        self.assertIn('gh pr close "$url" --comment "$($sync closing --consumer "$CONSUMER")"', block)
        self.assertIn("report --state closed --url \"$url\"", block)
        self.assertIn('report --state "up to date"', block)
        self.assertLess(self.text.index('url="$(own_pull)"'), start)


class ReleaseWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = workflow("release.yml")

    def test_runs_on_a_push_to_main_that_changes_the_version_or_what_tags_it(self):
        self.assertIn("  push:\n    branches: [main]\n    paths:\n", self.text)
        for path in ("sdlc_protocol/__init__.py", "scripts/release.py", ".github/workflows/release.yml"):
            self.assertIn(f'      - "{path}"\n', self.text)
        self.assertIn("  workflow_dispatch:\n", self.text)

    def test_a_run_off_main_tags_nothing_and_says_why(self):
        """A run by hand can name any branch, whose unmerged version would
        otherwise be released."""
        self.assertIn("REF: ${{ github.ref }}", self.text)
        self.assertRegex(self.text, r'if \[ "\$REF" != "refs/heads/main" \]; then\n'
                                    r'\s+echo "releases are made from main only; \$REF tags nothing"\n'
                                    r'\s+exit 0\n')

    def test_one_release_at_a_time_and_a_newer_push_waits(self):
        self.assertIn("concurrency:\n  group: release\n  cancel-in-progress: false\n", self.text)

    def test_the_token_writes_contents_and_nothing_else(self):
        self.assertIn("permissions:\n  contents: write\n\n", self.text)

    def test_reads_every_commit_and_tag_and_keeps_no_credential(self):
        """The walk back to the newest tagged version needs the whole
        history and the tags; nothing is pushed, so the checkout keeps no
        token in its git configuration."""
        self.assertIn("          fetch-depth: 0\n          persist-credentials: false\n", self.text)

    def test_pushes_nothing_and_makes_a_tag_only_where_the_script_found_one_missing(self):
        """GitHub makes the tag with the release, at the commit the script
        names; the version main carries is released only from a tag that
        is already there."""
        code = code_lines(self.text)
        self.assertEqual([line for line in code if "git push" in line or "git tag" in line], [])
        self.assertEqual([line.strip() for line in code if "gh release create" in line], [
            'gh release create "$tag" --repo "$REPO" --verify-tag --title "$title" --generate-notes',
            'gh release create "$tag" --repo "$REPO" --target "$commit" --title "$title" --generate-notes',
        ])
        self.assertIn('python3 scripts/release.py missing > "$list"', self.text)
        self.assertIn('elif [ "$commit" = "tagged" ]; then', self.text)

    def test_releases_only_a_tag_that_has_no_release(self):
        view = self.text.index('if gh release view "$tag" --repo "$REPO" > /dev/null 2>&1; then')
        self.assertLess(view, self.text.index("gh release create"))

    def test_a_version_it_cannot_read_stops_the_run(self):
        """An assignment takes its command's exit status, so set -e stops
        the run; inside echo's argument the failure would pass unseen."""
        self.assertIn('version="$(python3 scripts/release.py version)"', self.text)
        self.assertIn('echo "v$version tagged" >> "$list"', self.text)

    def test_the_loop_reads_the_list_on_its_own_descriptor(self):
        """A command inside the loop that reads its standard input would
        otherwise take the rest of the list in its place."""
        self.assertIn("while read -r tag commit <&3; do", self.text)
        self.assertIn('done 3< "$list"', self.text)


class EveryWorkflow(unittest.TestCase):
    def test_no_expression_inside_a_shell_step(self):
        """A value reaches a shell only through env:, so nothing it holds
        is pasted into the script GitHub runs."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for number, step in shell_steps(path.read_text(encoding="ascii")):
                with self.subTest(workflow=path.name, line=number):
                    self.assertNotIn("${{", step)

    def test_every_shell_step_parses(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for number, step in shell_steps(path.read_text(encoding="ascii")):
                with self.subTest(workflow=path.name, line=number):
                    done = subprocess.run(["bash", "-n"], input=shell_script(step), capture_output=True,
                                          text=True, timeout=30)
                    self.assertEqual(done.returncode, 0, done.stderr)

    def test_every_action_is_pinned_to_a_major_tag_and_the_same_one_everywhere(self):
        seen = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for action, tag in re.findall(r"uses: ([\w./-]+)@(\S+)", path.read_text(encoding="ascii")):
                with self.subTest(workflow=path.name, action=action):
                    self.assertRegex(tag, r"^v[0-9]+$")
                    self.assertEqual(seen.setdefault(action, tag), tag)


class Version(unittest.TestCase):
    def test_is_three_numbers(self):
        self.assertRegex(sdlc_protocol.__version__, r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_the_package_says_what_it_is(self):
        self.assertTrue(sdlc_protocol.__doc__.strip())


if __name__ == "__main__":
    unittest.main()
