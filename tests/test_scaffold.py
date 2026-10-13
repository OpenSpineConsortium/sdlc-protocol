"""The repository around the package: the spec, the build file and the
workflows.

The spec reaches readers who never see the template, so a {{placeholder}}
left in it is a hole in the protocol, and a policy field or world method
it does not name is one nobody can learn about. pyproject.toml is what an
install reads, so it must parse and agree with the package it builds. The
workflows are what carry a change to the consumers: the check job must
keep the name branch protection requires, the downstream job must run a
leg per consumer and skip the one it cannot read. The release must run on
a push to main that changes the version or what tags it, never from
another branch and never two at once; its plan must list what is due and
tell the owner, and its job release must run in the environment release
only when something is due, must ask GitHub for the run's approvals as its
first step and tag nothing without one for release, must run nothing from
the repository with a token that writes contents and reads actions and
nothing else, must push nothing and must make a tag only where the script
found one missing, so none is moved. The sync must follow a Release run
and never a push, go on only after one that succeeded and that main
started, carry the newest published release and never main's head, with
the release's own scripts/vendor.py into the consumers that release names,
never run from another branch and never two at once, must take as its own
only a pull request from its own repository, must arm auto-merge without
ever merging, and must push only what the branch does not already hold.
No workflow may put an expression inside a shell step, where a value would
run as code, and every shell step must at least parse. The copy itself,
scripts/vendor.py and its record, is tests/test_vendor.py's and
tests/test_vendoring.py's.
"""

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
            "How work is claimed", "Workers", "Labels", "Pick", "Claim", "Announce", "Work in the open", "Finish",
            "Blockers", "Release", "Overlaps", "Planning", "Policy and worlds", "Rules that hold everywhere",
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


def jobs_of(text):
    """Each job's lines by the job's name, from its key under jobs: to the
    next job's key."""
    jobs, name = {}, None
    for line in text.split("\njobs:\n", 1)[1].splitlines():
        key = re.match(r"^  ([A-Za-z_][\w-]*):$", line)
        if key:
            name = key.group(1)
            jobs[name] = []
        elif name is not None:
            jobs[name].append(line)
    return {name: "\n".join(lines) + "\n" for name, lines in jobs.items()}


def steps_of(job):
    """A job's steps in order, each as its text; a comment between two
    steps belongs to neither."""
    steps, current = [], None
    for line in job.splitlines():
        if line.startswith("      - "):
            current = [line]
            steps.append(current)
        elif line.startswith("      #") or (line.strip() and not line.startswith("       ")):
            current = None
        elif current is not None:
            current.append(line)
    return ["\n".join(step) + "\n" for step in steps]


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
        self.on = self.text[self.text.index("\non:\n"):self.text.index("\npermissions:")]
        self.jobs = jobs_of(self.text)

    def test_follows_a_release_run_and_never_runs_on_a_push(self):
        """Was: it ran on a push to main that changed the package, and so
        carried main's head into every consumer with nobody's approval. A
        release published with GITHUB_TOKEN starts no workflow, so the
        sync follows the Release run that published it."""
        self.assertIn("  workflow_run:\n    workflows: [Release]\n    types: [completed]\n    branches: [main]\n",
                      self.on)
        self.assertNotIn("push:", self.on)
        self.assertNotIn("paths:", self.on)

    def test_runs_by_hand_and_weekly_off_the_hour(self):
        self.assertIn("  workflow_dispatch:\n", self.text)
        minute = re.search(r'- cron: "(\d+) ', self.text).group(1)
        self.assertNotEqual(minute, "0")

    def test_goes_on_only_after_a_release_run_main_started_that_succeeded(self):
        """A pull request can make a run of its own named Release, a fork's
        too, and that run's completion starts this workflow with the App's
        key at hand: scripts/sync.py reads the run from GitHub's event file
        and the plan stops unless main started it and it succeeded."""
        plan = self.jobs["plan"]
        self.assertIn("EVENT: ${{ github.event_name }}", plan)
        self.assertRegex(plan, r'if \[ "\$EVENT" = "workflow_run" \]; then\n'
                               r'\s+why="\$\(python3 scripts/sync.py after-release "\$GITHUB_EVENT_PATH" '
                               r'--repository "\$REPO"\)"\n'
                               r'\s+if \[ -n "\$why" \]; then\n'
                               r'\s+echo "enabled=false" >> "\$GITHUB_OUTPUT"\n'
                               r'\s+printf \'%s\\n\' "\$why" >> "\$GITHUB_STEP_SUMMARY"\n'
                               r'\s+exit 0\n')

    def test_carries_the_newest_published_release_never_mains_head(self):
        """The copy is made by the release's own scripts/vendor.py, in a
        checkout of the release's tag with its whole history, so the files,
        the record and its commit are the release's; nothing in the
        workflow vendors this checkout, main's."""
        plan = self.jobs["plan"]
        self.assertIn('gh api --paginate "repos/$REPO/releases?per_page=100"', plan)
        self.assertIn("--jq '.[] | select((.draft or .prerelease) | not) | .tag_name' > \"$tags\"", plan)
        self.assertIn('tag="$(python3 scripts/sync.py newest-release < "$tags")"', plan)
        self.assertIn('echo "tag=$tag" >> "$GITHUB_OUTPUT"', plan)
        self.assertIn("          ref: ${{ needs.plan.outputs.tag }}\n          path: release\n          fetch-depth: 0\n",
                      self.jobs["sync"])
        vendoring = [line.strip() for line in code_lines(self.text) if "vendor.py" in line]
        self.assertEqual(vendoring, ['run: python3 release/scripts/vendor.py consumer --consumer "$CONSUMER"'])

    def test_a_run_by_hand_or_on_the_schedule_carries_the_newest_release_too(self):
        """Only the step after a Release run looks at the event, and only to
        stop; every run that goes on carries the newest release, so neither
        a run by hand nor the schedule is a way around the approval."""
        plan = self.jobs["plan"]
        start = plan.index('if [ "$EVENT" = "workflow_run" ]; then')
        block = plan[start:plan.index("\n          fi\n", start)]
        self.assertNotIn("newest-release", block)
        self.assertNotIn("enabled=true", block)
        self.assertEqual(plan.count('echo "enabled=true" >> "$GITHUB_OUTPUT"'), 1)
        self.assertLess(plan.index("newest-release"), plan.index('echo "enabled=true" >> "$GITHUB_OUTPUT"'))

    def test_with_no_release_it_stops_and_says_so(self):
        self.assertRegex(self.jobs["plan"], r'if \[ -z "\$tag" \]; then\n'
                                            r'\s+echo "enabled=false" >> "\$GITHUB_OUTPUT"\n'
                                            r'\s+python3 scripts/sync.py no-release >> "\$GITHUB_STEP_SUMMARY"\n'
                                            r'\s+exit 0\n')

    def test_the_consumers_are_the_ones_the_release_names(self):
        plan = self.jobs["plan"]
        self.assertIn("      matrix: ${{ steps.consumers.outputs.matrix }}\n", plan)
        self.assertIn('matrix="$(python3 scripts/sync.py --library release matrix)"', plan)
        self.assertIn("          ref: ${{ steps.plan.outputs.tag }}\n          path: release\n", plan)

    def test_the_pull_requests_words_read_the_releases_ecosystem_and_history(self):
        self.assertIn('sync="python3 $GITHUB_WORKSPACE/scripts/sync.py --library $GITHUB_WORKSPACE/release"',
                      self.jobs["sync"])

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
        self.jobs = jobs_of(self.text)
        self.plan, self.release = self.jobs["plan"], self.jobs["release"]

    def test_runs_on_a_push_to_main_that_changes_the_version_or_what_tags_it(self):
        self.assertIn("  push:\n    branches: [main]\n    paths:\n", self.text)
        for path in ("sdlc_protocol/__init__.py", "scripts/release.py", ".github/workflows/release.yml"):
            self.assertIn(f'      - "{path}"\n', self.text)
        self.assertIn("  workflow_dispatch:\n", self.text)

    def test_a_run_off_main_tags_nothing_and_says_why(self):
        """A run by hand can name any branch, whose unmerged version would
        otherwise be released. The line now goes into the run's summary as
        well as its log, where the plan's other words go."""
        self.assertIn("REF: ${{ github.ref }}", self.plan)
        self.assertRegex(self.plan, r'if \[ "\$REF" != "refs/heads/main" \]; then\n'
                                    r'\s+echo "releases are made from main only; \$REF tags nothing" '
                                    r'\| tee -a "\$GITHUB_STEP_SUMMARY"\n'
                                    r'\s+exit 0\n')

    def test_one_release_at_a_time_and_a_newer_push_waits(self):
        self.assertIn("concurrency:\n  group: release\n  cancel-in-progress: false\n", self.text)

    def test_only_the_job_that_tags_writes_and_it_reads_the_runs_approvals(self):
        """Was: one token for the whole run that wrote contents and nothing
        else. The plan only reads now; the job release writes contents and
        reads actions, the permission of a run's review history."""
        self.assertIn("permissions:\n  contents: read\n\n", self.text)
        self.assertNotIn("permissions:", self.plan)
        self.assertIn("    permissions:\n      contents: write\n      actions: read\n    steps:\n", self.release)

    def test_reads_every_commit_and_tag_and_keeps_no_credential(self):
        """The walk back to the newest tagged version needs the whole
        history and the tags; nothing is pushed, so the checkout keeps no
        token in its git configuration."""
        self.assertIn("          fetch-depth: 0\n          persist-credentials: false\n", self.plan)

    def test_the_job_that_tags_runs_in_the_environment_release_and_only_when_something_is_due(self):
        """The environment's required reviewer is the owner, so GitHub starts
        the job only on the owner's approval; with nothing due, nobody is
        asked."""
        self.assertIn("    needs: plan\n    if: needs.plan.outputs.due != ''\n", self.release)
        self.assertIn("    environment: release\n", self.release)

    def test_its_first_step_tags_nothing_unless_this_run_was_approved_in_release(self):
        """GitHub creates an environment a job names that does not exist,
        with no protection rule, and runs the job at once; so before
        anything else the job reads this run's review history, finds an
        approval for release whatever the case of its name, and otherwise
        fails the run with the one line that says what to set up."""
        first = steps_of(self.release)[0]
        self.assertIn("- name: Go on only if the owner approved this run in the environment release", first)
        self.assertIn("RUN_ID: ${{ github.run_id }}", first)
        self.assertIn("set -eu", first)
        self.assertIn('approvers="$(gh api "repos/$REPO/actions/runs/$RUN_ID/approvals" --jq \'', first)
        self.assertIn('[.[] | select(.state == "approved")', first)
        self.assertIn('| select(any((.environments // [])[]; (.name // "" | ascii_downcase) == "release"))', first)
        self.assertIn('| .user.login // "a reviewer"] | unique | join(", ")\')"', first)
        refusal = first[first.index('if [ -z "$approvers" ]; then'):]
        self.assertIn("https://github.com/$REPO/settings/environments", refusal)
        self.assertIn('| tee -a "$GITHUB_STEP_SUMMARY"\n            exit 1\n          fi\n', refusal)
        self.assertNotIn("gh release", first)

    def test_the_job_that_tags_runs_nothing_from_the_repository(self):
        """It holds the token that writes contents, so it checks nothing out
        and runs no script: the plan's list reaches it as a value."""
        self.assertNotIn("actions/checkout", self.release)
        self.assertNotIn("python3", self.release)
        self.assertNotIn("scripts/", self.release)

    def test_the_plan_lists_what_is_due_tells_the_owner_and_hands_the_list_on(self):
        self.assertIn("      due: ${{ steps.plan.outputs.due }}\n", self.plan)
        self.assertRegex(self.plan, r'if gh release view "\$tag" --repo "\$REPO" > /dev/null 2>&1; then\n'
                                    r'\s+echo "\$tag is released already"\n'
                                    r'\s+elif cut -d \' \' -f 1 "\$due" \| grep -q -x -F -e "\$tag"; then\n'
                                    r'\s+echo "\$tag is listed already, at the commit that first carries it"\n'
                                    r'\s+else\n'
                                    r'\s+echo "\$tag \$commit" >> "\$due"\n')
        self.assertIn('python3 scripts/release.py summary < "$due" >> "$GITHUB_STEP_SUMMARY"', self.plan)
        self.assertIn('echo "due<<END_OF_DUE"\n            cat "$due"\n            echo "END_OF_DUE"\n', self.plan)
        self.assertIn("DUE: ${{ needs.plan.outputs.due }}", self.release)
        self.assertIn('printf \'%s\\n\' "$DUE" > "$list"', self.release)

    def test_pushes_nothing_and_makes_a_tag_only_where_the_script_found_one_missing(self):
        """GitHub makes the tag with the release, at the commit the script
        names; the version main carries is released only from a tag that
        is already there. The making of a tag is now the condition of an
        if, so that a refusal says what to do (TheTaggingRuns)."""
        code = code_lines(self.text)
        self.assertEqual([line for line in code if "git push" in line or "git tag" in line], [])
        self.assertEqual([line.strip() for line in code if "gh release create" in line], [
            'gh release create "$tag" --repo "$REPO" --verify-tag --title "$title" --generate-notes',
            'if ! gh release create "$tag" --repo "$REPO" --target "$commit" --title "$title" --generate-notes; then',
        ])
        self.assertIn('python3 scripts/release.py missing > "$list"', self.plan)
        self.assertIn('elif [ "$commit" = "tagged" ]; then', self.release)
        self.assertNotIn("gh release create", self.plan)

    def test_releases_only_a_tag_that_has_no_release(self):
        """Checked again just before it is made, since the approval may come
        long after the plan."""
        view = self.release.index('if gh release view "$tag" --repo "$REPO" > /dev/null 2>&1; then')
        self.assertLess(view, self.release.index("gh release create"))

    def test_a_version_it_cannot_read_stops_the_run(self):
        """An assignment takes its command's exit status, so set -e stops
        the run; inside echo's argument the failure would pass unseen."""
        self.assertIn('version="$(python3 scripts/release.py version)"', self.plan)
        self.assertIn('echo "v$version tagged" >> "$list"', self.plan)

    def test_the_loops_read_the_list_on_its_own_descriptor(self):
        """A command inside a loop that reads its standard input would
        otherwise take the rest of the list in its place."""
        for job in (self.plan, self.release):
            self.assertIn("while read -r tag commit <&3; do", job)
            self.assertIn('done 3< "$list"', job)


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


# --- the steps that decide, run --------------------------------------------------------

# gh as these tests need it: every call is logged, and the answers come from
# the environment each test sets. A view answers for a tag RELEASED names, a
# paginated listing prints RELEASE_TAGS, and the review history prints
# APPROVERS, as the step's own filter would, or fails when API_FAILS is set;
# a release is made unless CREATE_FAILS is set.
STUB_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "$GH_CALLS"
case "$1 $2" in
  "release view")
    for tag in $RELEASED; do [ "$tag" = "$3" ] && exit 0; done
    exit 1 ;;
  "api --paginate")
    for tag in $RELEASE_TAGS; do printf '%s\\n' "$tag"; done ;;
  "api repos/"*)
    [ -n "${API_FAILS:-}" ] && exit 1
    printf '%s\\n' "$APPROVERS" ;;
  "release create")
    [ -n "${CREATE_FAILS:-}" ] && exit 1 ;;
esac
exit 0
"""
SHA = "1111111111111111111111111111111111111111"


def step_named(text, job, name):
    """The one step of a workflow's job that has this name."""
    found = [step for step in steps_of(jobs_of(text)[job]) if step.splitlines()[0].strip() == f"- name: {name}"]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} steps named {name!r} in the job {job}")
    return found[0]


class StepsRun(unittest.TestCase):
    """A step's script run by bash -e, as GitHub runs a step that names no
    shell, with that gh first on the PATH and the files GitHub hands a step
    in a folder of the test's own, removed afterwards."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        stubs = self.scratch / "bin"
        stubs.mkdir()
        (stubs / "gh").write_text(STUB_GH, encoding="ascii")
        (stubs / "gh").chmod(0o755)
        (self.scratch / "runner").mkdir()
        self.output, self.summary, self.calls = (self.scratch / name for name in ("output", "summary", "calls"))
        for file in (self.output, self.summary, self.calls):
            file.write_text("", encoding="ascii")
        quiet = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GH_", "GITHUB_"))}
        self.environment = dict(quiet, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                                RUNNER_TEMP=str(self.scratch / "runner"), GITHUB_OUTPUT=str(self.output),
                                GITHUB_STEP_SUMMARY=str(self.summary), GH_CALLS=str(self.calls),
                                GH_TOKEN="a token of the test's", REPO="octo/library", RELEASED="",
                                RELEASE_TAGS="", APPROVERS="")

    def run_step(self, step, cwd, **environment):
        script = self.scratch / "step.sh"
        script.write_text(shell_script(shell_steps(step)[0][1]), encoding="ascii")
        return subprocess.run(["bash", "-e", str(script)], cwd=cwd, env=dict(self.environment, **environment),
                              capture_output=True, text=True, timeout=60)

    def read(self, file):
        return file.read_text(encoding="ascii")


class TheReleasePlanRuns(StepsRun):
    """The plan's step in a library of the test's own, with this
    repository's scripts/release.py in it."""

    STEP = "List each version main carries without a release, for the owner to approve"

    def setUp(self):
        super().setUp()
        self.library = self.scratch / "library"
        (self.library / "scripts").mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts" / "release.py", self.library / "scripts" / "release.py")
        self.git("init", "-q")
        self.first = self.commit("0.1.0")
        self.git("tag", "v0.1.0")
        self.raised = self.commit("0.2.0")
        self.later = self.commit("0.2.0", "more.md")
        self.step = step_named(workflow("release.yml"), "plan", self.STEP)

    def git(self, *args):
        done = subprocess.run(["git", "-c", "user.name=release test", "-c", "user.email=release@example.invalid",
                               "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                               "-c", "init.defaultBranch=main", *args],
                              cwd=self.library, capture_output=True, text=True, timeout=60, env=self.environment)
        if done.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
        return done.stdout.strip()

    def commit(self, version, extra=None):
        package = self.library / "sdlc_protocol"
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="ascii")
        if extra:
            (self.library / extra).write_text("more\n", encoding="ascii")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"at {version}")
        return self.git("rev-parse", "HEAD")

    def plan(self, ref="refs/heads/main", released="v0.1.0"):
        done = self.run_step(self.step, self.library, REF=ref, RELEASED=released)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.read(self.output), self.read(self.summary)

    def test_a_new_version_is_listed_once_at_the_commit_that_first_carries_it(self):
        """release.py's list names the new version at its first commit, and
        the line for the version main carries names it again; the owner is
        shown, and the job release given, the one release."""
        output, summary = self.plan()
        self.assertEqual(output, f"due<<END_OF_DUE\nv0.2.0 {self.raised}\nEND_OF_DUE\n")
        self.assertIn(f"- v0.2.0: tags {self.raised} and publishes its release\n", summary)
        self.assertNotIn("tag was made before", summary)
        self.assertEqual(summary.count("- v0.2.0"), 1)

    def test_nothing_is_due_when_the_version_main_carries_has_its_release(self):
        self.git("tag", "v0.2.0", self.raised)
        output, summary = self.plan(released="v0.1.0 v0.2.0")
        self.assertEqual(output, "due<<END_OF_DUE\nEND_OF_DUE\n")
        self.assertIn("## Nothing to release", summary)

    def test_a_tag_pushed_by_hand_gets_its_release(self):
        self.git("tag", "v0.2.0", self.raised)
        output, summary = self.plan()
        self.assertEqual(output, "due<<END_OF_DUE\nv0.2.0 tagged\nEND_OF_DUE\n")
        self.assertIn("- v0.2.0: its tag was made before; it gets its release", summary)

    def test_off_main_nothing_is_listed_and_the_summary_says_why(self):
        output, summary = self.plan(ref="refs/heads/claude/7")
        self.assertEqual(output, "")
        self.assertEqual(summary, "releases are made from main only; refs/heads/claude/7 tags nothing\n")


class TheApprovalRuns(StepsRun):
    STEP = "Go on only if the owner approved this run in the environment release"

    def setUp(self):
        super().setUp()
        self.step = step_named(workflow("release.yml"), "release", self.STEP)

    def gate(self, **environment):
        return self.run_step(self.step, self.scratch, RUN_ID="77", **environment)

    def test_with_no_approval_it_fails_and_says_what_to_set_up(self):
        done = self.gate(APPROVERS="")
        self.assertEqual(done.returncode, 1)
        summary = self.read(self.summary)
        self.assertTrue(summary.startswith("Nothing was released: this run has no approval in the environment release."))
        self.assertIn("https://github.com/octo/library/settings/environments", summary)
        self.assertEqual(summary.count("\n"), 1)

    def test_it_asks_for_this_runs_review_history(self):
        self.gate(APPROVERS="")
        self.assertTrue(self.read(self.calls).startswith("api repos/octo/library/actions/runs/77/approvals --jq"))

    def test_with_an_approval_it_goes_on_and_names_who_gave_it(self):
        done = self.gate(APPROVERS="owner")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.read(self.summary), "Approved in the environment release by owner.\n")

    def test_a_history_it_cannot_read_stops_the_job(self):
        done = self.gate(APPROVERS="owner", API_FAILS="1")
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("Approved", self.read(self.summary))


class TheTaggingRuns(StepsRun):
    STEP = "Tag each version the plan listed, and publish its release"

    def setUp(self):
        super().setUp()
        self.step = step_named(workflow("release.yml"), "release", self.STEP)

    def calls_for(self, due, released=""):
        done = self.run_step(self.step, self.scratch, DUE=due, RELEASED=released)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.read(self.calls).splitlines()

    def test_makes_each_listed_release_once_from_the_plans_list(self):
        calls = self.calls_for(f"v0.2.0 {SHA}\nv0.3.0 tagged")
        self.assertEqual(calls, [
            "release view v0.2.0 --repo octo/library",
            f"release create v0.2.0 --repo octo/library --target {SHA} --title sdlc-protocol 0.2.0 --generate-notes",
            "release view v0.3.0 --repo octo/library",
            "release create v0.3.0 --repo octo/library --verify-tag --title sdlc-protocol 0.3.0 --generate-notes",
        ])

    def test_a_release_made_while_it_waited_is_left_alone(self):
        self.assertEqual(self.calls_for(f"v0.2.0 {SHA}", released="v0.2.0"),
                         ["release view v0.2.0 --repo octo/library"])

    def test_a_tag_github_refuses_stops_the_job_and_says_what_to_do(self):
        """GitHub refuses GITHUB_TOKEN a tag at a commit whose workflows
        differ from main's, which a release that waited for its approval
        meets when a change to a workflow merged meanwhile."""
        done = self.run_step(self.step, self.scratch, DUE=f"v0.2.0 {SHA}\nv0.3.0 {SHA}", CREATE_FAILS="1")
        self.assertEqual(done.returncode, 1)
        summary = self.read(self.summary)
        self.assertTrue(summary.startswith(f"GitHub did not make v0.2.0 at {SHA}."))
        self.assertIn("push the tag yourself and run Release again from main (README.md, Releases)", summary)
        self.assertNotIn("v0.3.0", self.read(self.calls))


class TheSyncPlanRuns(StepsRun):
    """The sync's plan step in this checkout, whose scripts/sync.py it runs."""

    STEP = "Choose the release to carry, and whether the sync may run"

    def setUp(self):
        super().setUp()
        self.step = step_named(workflow("sync.yml"), "plan", self.STEP)
        self.event = self.scratch / "event.json"

    def plan(self, event="workflow_run", run=None, app_id="5258276", ref="refs/heads/main", tags=""):
        run = {"conclusion": "success", "event": "push", "head_branch": "main",
               "head_repository": {"full_name": "octo/library"}} if run is None else run
        self.event.write_text(json.dumps({"workflow_run": run} if event == "workflow_run" else {}), encoding="ascii")
        done = self.run_step(self.step, ROOT, EVENT=event, APP_ID=app_id, REF=ref, RELEASE_TAGS=tags,
                             GITHUB_EVENT_PATH=str(self.event))
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.read(self.output), self.read(self.summary)

    def test_after_main_s_release_run_it_carries_the_highest_release(self):
        output, summary = self.plan(tags="v0.3.1 v0.10.0 v0.9.9 nightly")
        self.assertEqual(output, "enabled=true\ntag=v0.10.0\n")
        self.assertTrue(summary.startswith("## The sync carries v0.10.0\n"))
        self.assertIn("api --paginate repos/octo/library/releases?per_page=100 --jq", self.read(self.calls))

    def test_by_hand_or_on_the_schedule_it_carries_the_highest_release_too(self):
        for event in ("workflow_dispatch", "schedule"):
            with self.subTest(event=event):
                self.output.write_text("", encoding="ascii")
                output, _ = self.plan(event=event, tags="v0.4.0 v0.4.1")
                self.assertEqual(output, "enabled=true\ntag=v0.4.1\n")

    def test_after_a_release_run_that_failed_it_stops_and_asks_nothing(self):
        output, summary = self.plan(run={"conclusion": "failure", "event": "push", "head_branch": "main",
                                         "head_repository": {"full_name": "octo/library"}}, tags="v0.4.1")
        self.assertEqual(output, "enabled=false\n")
        self.assertIn("concluded `failure` rather than `success`", summary)
        self.assertEqual(self.read(self.calls), "")

    def test_after_a_release_run_main_did_not_start_it_stops(self):
        for run in ({"conclusion": "success", "event": "pull_request", "head_branch": "main",
                     "head_repository": {"full_name": "a-fork/library"}},
                    {"conclusion": "success", "event": "workflow_dispatch", "head_branch": "claude/7",
                     "head_repository": {"full_name": "octo/library"}}):
            with self.subTest(run=run):
                self.output.write_text("", encoding="ascii")
                output, _ = self.plan(run=run, tags="v0.4.1")
                self.assertEqual(output, "enabled=false\n")

    def test_off_main_it_stops_and_says_why(self):
        output, summary = self.plan(event="workflow_dispatch", ref="refs/heads/claude/7", tags="v0.4.1")
        self.assertEqual(output, "enabled=false\n")
        self.assertIn("The sync runs only from main", summary)

    def test_without_the_app_it_stops_and_says_what_to_set_up(self):
        output, summary = self.plan(event="schedule", app_id="", tags="v0.4.1")
        self.assertEqual(output, "enabled=false\n")
        self.assertIn("## The sync is not set up", summary)

    def test_with_no_release_it_stops_and_says_so(self):
        output, summary = self.plan(event="workflow_dispatch", tags="")
        self.assertEqual(output, "enabled=false\n")
        self.assertIn("has no published release yet", summary)


class Version(unittest.TestCase):
    def test_is_three_numbers(self):
        self.assertRegex(sdlc_protocol.__version__, r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_the_package_says_what_it_is(self):
        self.assertTrue(sdlc_protocol.__doc__.strip())


if __name__ == "__main__":
    unittest.main()
