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
another branch and never two at once; its plan must list what is due, each
release with its commit, tell the owner, and stop at a tag made by hand
anywhere but where the workflow would have made it, and its job release
must run in the environment release only when something is due, must ask
GitHub for the run's approvals as its first step and tag nothing without
one for release, must run nothing from the repository with a token that
writes contents and reads actions and nothing else, must push nothing,
must make a tag only where the script found one missing, so none is moved,
and must read each tag on GitHub before it releases it, stopping at one
made elsewhere since the plan. The sync must follow a Release run and
never a push, go on only after one that succeeded and that main started,
carry the newest published release and never main's head, and only where
its tag stands, by that commit and never by the tag's name, with the
release's own scripts/vendor.py into the consumers that release names,
never run from another branch and never two at once, must let a leg go on
only with the commit the plan chose, must take as its own only a pull
request from its own repository, must arm auto-merge without ever merging,
and must push only what the branch does not already hold.
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

    def test_keeps_the_template_sections_and_adds_its_own(self):
        # Locks, off by default, is the library's until a template release
        # turns it on and brings the section into the template's page.
        self.assertEqual(headings(self.text), [
            "How work is claimed", "Workers", "Labels", "Pick", "Claim", "Announce", "Work in the open", "Finish",
            "Blockers", "Release", "Overlaps", "Planning", "Locks", "Policy and worlds", "Rules that hold everywhere",
        ])

    def test_names_every_policy_field_and_world_method(self):
        for name in policy_fields() + world_methods():
            self.assertIn(name, self.text)

    def test_the_readme_names_them_too(self):
        readme = (ROOT / "README.md").read_text(encoding="ascii")
        for name in policy_fields() + world_methods():
            self.assertIn(name, readme)


class TheReadme(unittest.TestCase):
    """What README.md tells the people who change the library and the owner
    who approves its releases, where the workflows depend on it."""

    def setUp(self):
        self.text = (ROOT / "README.md").read_text(encoding="ascii")

    def section(self, heading):
        """The section under heading, up to the next heading of any level."""
        start = self.text.index(f"\n{heading}\n") + 1
        end = self.text.find("\n#", start)
        return self.text[start:] if end == -1 else self.text[start:end]

    def test_a_change_a_consumer_must_answer_raises_the_version_before_the_consumer_vendors_it(self):
        """scripts/vendor.py records the last commit that changed the
        package, so a raise made after the consumer vendored the branch
        moves the release past that copy, and the sync then brings the
        consumer the raise in a pull request of its own, where the steps
        promise it up to date. Was: step 3 raised the version at the
        merge, after step 2's copy."""
        steps = self.section("### A change a consumer must answer")
        first = steps[steps.index("\n1. "):steps.index("\n2. ")]
        merge = steps[steps.index("\n3. "):steps.index("\n4. ")]
        self.assertIn("raise the version from the start, before any consumer vendors it", first)
        self.assertNotIn("raising the version", merge)

    def test_the_owners_setup_for_releases_is_where_the_refusal_sends_the_owner(self):
        """release.yml's refusal names this section by its heading and its
        link, and the job's first step holds only with the environment set
        as it says, in GitHub's own words for each control: Configure
        environment, Save protection rules after each rule, and Add rule for
        the branch (github/docs, manage-environments.md)."""
        named = re.findall(r'README\.md, \\"([^\\]+)\\"', workflow("release.yml"))
        self.assertEqual(named, ["The owner's one-time setup for releases"])
        setup = self.section(f"## {named[0]}")
        self.assertIn("https://github.com/OpenSpineConsortium/symphony-protocol/settings/environments", setup)
        for words in ("click Configure environment", "Tick Required reviewers, add yourself and click Save protection "
                      "rules", "Leave Prevent self-review unticked", "Untick Allow administrators to bypass configured "
                      "protection rules and click Save protection rules", "choose Selected branches and tags",
                      "choose Branch in the Ref type menu, enter `main` and click Add rule"):
            with self.subTest(words=words):
                self.assertIn(words, setup)
        self.assertNotIn("after each of these", setup)

    def test_the_approval_is_the_owners_own_act_in_the_browser(self):
        """The review history records only a login, and sessions act under
        the owner's, so the first step cannot tell their approval apart; the
        page says what was measured of a session's reach, not a guess."""
        setup = self.section("## The owner's one-time setup for releases")
        self.assertIn("No session approves or rejects a deployment", setup)
        self.assertIn("so GitHub answers 403", setup)
        self.assertNotIn("is not yet measured", setup)

    def test_says_what_the_approval_does_not_hold(self):
        """A writer can publish a release by hand, a branch's workflow can
        read the App's key, the template's copies take bare tags, and the
        gate's own files run from main once merged: the page says so rather
        than promising more than GitHub gives."""
        releases = self.section("## Releases")
        self.assertIn("What the approval holds, and what it does not.", releases)
        for words in ("A release published by hand", "The App's key is a repository secret",
                      "take the newest tag `v<version>` by their own `protocol.yml`, a release or not",
                      "on the owner's standing permission of 2026-10-13 (CLAUDE.md)"):
            with self.subTest(words=words):
                self.assertIn(words, releases)

    def test_says_the_one_way_a_change_reaches_a_consumer_before_its_release(self):
        """A consumer that must answer a change takes the branch by a pull
        request of its own, before the release exists, so the page may not
        promise that nothing reaches a consumer before the approval."""
        opening = self.section("## How a change reaches the other repositories").split("\n1. ")[0]
        self.assertNotIn("nothing reaches a consumer before that approval", opening)
        self.assertIn("One way runs ahead of a release, on purpose: a change a consumer must answer", opening)


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


def step_ids(job):
    """Each step of a job that has an id, by that id."""
    found = {}
    for step in steps_of(job):
        match = re.search(r"(?m)^ {6}(?:- | {2})id: ([\w-]+)$", step)
        if match:
            found[match.group(1)] = step
    return found


def job_outputs(job):
    """The names of the outputs a job declares."""
    block = re.search(r"(?m)^    outputs:\n((?: {6}.*\n)+)", job)
    return set(re.findall(r"(?m)^ {6}([\w-]+): ", block.group(1))) if block else set()


def keys_that_skip_or_forgive(job):
    """Each step's if: or continue-on-error: in a job, and the job's own
    continue-on-error:, the keys that let a step run after a failure or
    let a failure pass."""
    return re.findall(r"(?m)^(?: {6}(?:- | {2})(?:if|continue-on-error)| {4}continue-on-error):.*$", job)


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

    def test_follows_the_release_workflow_by_the_name_release_yml_gives_itself(self):
        """workflow_run matches a workflow by its name, so renaming Release
        alone would leave the sync to its weekly run, saying nothing."""
        name = re.match(r"name: (.+)\n", workflow("release.yml")).group(1)
        self.assertEqual(re.findall(r"(?m)^    workflows: \[(.*)\]$", self.on), [name])

    def test_runs_by_hand_and_weekly_off_the_hour(self):
        self.assertIn("  workflow_dispatch:\n", self.text)
        minute = re.search(r'- cron: "(\d+) ', self.text).group(1)
        self.assertNotEqual(minute, "0")

    def test_goes_on_only_after_a_release_run_main_started_that_succeeded(self):
        """A pull request can make a run of its own named Release, a fork's
        too, and that run's completion starts this workflow with the App's
        key at hand: scripts/sync.py reads the run from GitHub's event file
        and the plan stops unless main started it and it succeeded. The run
        is compared with this repository, github.repository, never with a
        name the event carries, which a fork's run would make its own."""
        plan = self.jobs["plan"]
        self.assertIn("EVENT: ${{ github.event_name }}", plan)
        self.assertIn("          REPO: ${{ github.repository }}\n", plan)
        self.assertRegex(plan, r'if \[ "\$EVENT" = "workflow_run" \]; then\n'
                               r'\s+why="\$\(python3 scripts/sync.py after-release "\$GITHUB_EVENT_PATH" '
                               r'--repository "\$REPO"\)"\n'
                               r'\s+if \[ -n "\$why" \]; then\n'
                               r'\s+echo "enabled=false" >> "\$GITHUB_OUTPUT"\n'
                               r'\s+printf \'%s\\n\' "\$why" >> "\$GITHUB_STEP_SUMMARY"\n'
                               r'\s+exit 0\n')

    def test_carries_the_newest_published_release_never_mains_head(self):
        """The copy is made by the release's own scripts/vendor.py, in a
        checkout of the release's commit with its whole history, so the
        files, the record and its commit are the release's; nothing in the
        workflow vendors this checkout, main's. Was: the checkout named the
        release's tag; now it names the commit the tag stands at."""
        plan = self.jobs["plan"]
        self.assertIn('gh api --paginate "repos/$REPO/releases?per_page=100"', plan)
        self.assertIn("--jq '.[] | select((.draft or .prerelease) | not) | .tag_name' > \"$tags\"", plan)
        self.assertIn('tag="$(python3 scripts/sync.py newest-release < "$tags")"', plan)
        self.assertIn('echo "tag=$tag" >> "$GITHUB_OUTPUT"', plan)
        self.assertIn('echo "commit=$commit" >> "$GITHUB_OUTPUT"', plan)
        self.assertIn("          ref: ${{ needs.plan.outputs.commit }}\n          path: release\n          fetch-depth: 0\n"
                      "          persist-credentials: false\n", self.jobs["sync"])
        vendoring = [line.strip() for line in code_lines(self.text) if "vendor.py" in line]
        self.assertEqual(vendoring, ['run: python3 release/scripts/vendor.py consumer --consumer "$CONSUMER"'])

    def test_checks_the_release_out_by_its_commit_never_by_a_name(self):
        """actions/checkout resolves a ref that is a bare name to a branch
        of that name before a tag of it, so a branch named like the release
        would take its place; a ref of 40 hex digits it checks out as that
        commit."""
        refs = re.findall(r"(?m)^\s+ref: (.*)$", self.text)
        self.assertEqual(refs, ["${{ needs.plan.outputs.commit }}"])

    def test_the_newest_release_counts_only_where_its_tag_stands(self):
        """Its tag must name the commit that raised main's first-parent line
        to its version, which needs main's whole history and every tag; a
        tag anywhere else fails the run before anything is enabled."""
        plan = self.jobs["plan"]
        self.assertIn("      - uses: actions/checkout@v7\n        with:\n          fetch-depth: 0\n"
                      "          persist-credentials: false\n", plan)
        self.assertIn('commit="$(git rev-parse --verify -q "refs/tags/$tag^{commit}" || true)"', plan)
        self.assertRegex(plan, r'why="\$\(python3 scripts/sync.py misplaced --tag "\$tag" --commit "\$commit"\)"\n'
                               r'\s+if \[ -n "\$why" \]; then\n'
                               r'\s+echo "enabled=false" >> "\$GITHUB_OUTPUT"\n'
                               r'\s+printf \'%s\\n\' "\$why" >> "\$GITHUB_STEP_SUMMARY"\n'
                               r'\s+exit 1\n')
        self.assertLess(plan.index("scripts/sync.py misplaced"), plan.index('echo "enabled=true"'))

    def test_each_leg_goes_on_only_with_the_commit_the_plan_chose(self):
        """An empty commit would leave the release's checkout at this run's
        own commit, main's head; the guard comes before the App's key."""
        steps = steps_of(self.jobs["sync"])
        firsts = [step.splitlines()[0].strip() for step in steps]
        guard = firsts.index("- name: Go on only with the commit of the release the plan chose")
        checkout = next(number for number, step in enumerate(steps) if "path: release" in step)
        token = next(number for number, step in enumerate(steps) if "create-github-app-token" in step)
        self.assertLess(checkout, guard)
        self.assertLess(guard, token)
        self.assertIn("COMMIT: ${{ needs.plan.outputs.commit }}", steps[guard])
        self.assertIn('if ! [[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || [ "$head" != "$COMMIT" ] || '
                      '[ "$tagged" != "$COMMIT" ]; then', steps[guard])

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
        """The plan reads the release's ecosystem.json from the history it
        holds, by the release's commit, and checks nothing of the release
        out: a job of the default branch's own that ran a step beside a
        checkout of another commit is what CodeQL's cache poisoning query
        (actions/cache-poisoning/poisonable-step) reports."""
        plan = self.jobs["plan"]
        self.assertIn("      matrix: ${{ steps.consumers.outputs.matrix }}\n", plan)
        self.assertIn("          COMMIT: ${{ steps.plan.outputs.commit }}\n", plan)
        self.assertIn('git show "$COMMIT:ecosystem.json" > "$RUNNER_TEMP/release/ecosystem.json"', plan)
        self.assertIn('matrix="$(python3 scripts/sync.py --library "$RUNNER_TEMP/release" matrix)"', plan)
        self.assertNotIn("path: release", plan)
        self.assertEqual(plan.count("uses: actions/checkout@"), 1)

    def test_only_the_consumers_checkout_keeps_a_credential(self):
        """Nothing is pushed from this repository's checkouts, so none of
        them keeps a token in its git configuration; the consumer's is the
        one the job pushes to."""
        checkouts = [step for job in self.jobs.values() for step in steps_of(job) if "uses: actions/checkout@" in step]
        self.assertEqual(len(checkouts), 4)
        for step in checkouts:
            with self.subTest(step=step.splitlines()[1:3]):
                if "repository: ${{ matrix.repository }}" in step:
                    self.assertNotIn("persist-credentials", step)
                else:
                    self.assertIn("          persist-credentials: false\n", step)

    def test_nothing_in_a_leg_is_skipped_or_may_fail_quietly(self):
        """The guard before the App's key stops a leg only while no step
        there is told to run anyway or to let a failure pass."""
        self.assertEqual(keys_that_skip_or_forgive(self.jobs["sync"]), [])
        self.assertNotIn("continue-on-error", self.text)

    def test_the_pull_requests_words_read_the_releases_ecosystem_and_history(self):
        self.assertIn('sync="python3 $GITHUB_WORKSPACE/scripts/sync.py --library $GITHUB_WORKSPACE/release"',
                      self.jobs["sync"])

    def test_a_run_off_main_vendors_nothing_and_says_why(self):
        """A run by hand can name any branch, whose own scripts/sync.py
        would then choose what the sync carries and write the consumers'
        pull requests with the App's token. The ref is compared by name: a
        scheduled run's event carries no repository to read the default
        branch from."""
        self.assertIn("REF: ${{ github.ref }}", self.text)
        self.assertRegex(self.text, r'if \[ "\$REF" != "refs/heads/main" \]; then\n'
                                    r'\s+echo "enabled=false" >> "\$GITHUB_OUTPUT"\n'
                                    r'\s+python3 scripts/sync.py off-main --ref "\$REF"')

    def test_one_sync_at_a_time_and_a_newer_one_waits(self):
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

    def test_nothing_in_the_job_that_tags_is_skipped_or_may_fail_quietly(self):
        """A failed approval step stops the job only while no step there is
        told to run anyway or to let a failure pass: continue-on-error on
        the approval step, or an if such as always() on the tagging step,
        would tag with no approval. The job's one if is the one that runs
        it only when something is due."""
        self.assertEqual(keys_that_skip_or_forgive(self.release), [])
        self.assertEqual(re.findall(r"(?m)^    if: .*$", self.release), ["    if: needs.plan.outputs.due != ''"])
        self.assertNotIn("continue-on-error", self.text)

    def test_the_job_that_tags_runs_nothing_from_the_repository(self):
        """It holds the token that writes contents, so it checks nothing out
        and runs no script: the plan's list reaches it as a value."""
        self.assertNotIn("actions/checkout", self.release)
        self.assertNotIn("python3", self.release)
        self.assertNotIn("scripts/", self.release)

    def test_the_plan_lists_what_is_due_tells_the_owner_and_hands_the_list_on(self):
        """Was: each line held the tag and the commit alone; a tag made
        before keeps the word tagged after its commit now, and is checked
        before it is listed (test_a_tag_made_before_gets_its_release_only_
        where_it_stands)."""
        self.assertIn("      due: ${{ steps.plan.outputs.due }}\n", self.plan)
        self.assertRegex(self.plan, r'if gh release view "\$tag" --repo "\$REPO" > /dev/null 2>&1; then\n'
                                    r'\s+echo "\$tag is released already"\n'
                                    r'\s+elif cut -d \' \' -f 1 "\$due" \| grep -q -x -F -e "\$tag"; then\n'
                                    r'\s+echo "\$tag is listed already, at the commit that first carries it"\n'
                                    r'\s+elif \[ -n "\$tagged" \] && [^\n]*\n[^\n]*\n\s+exit 1\n'
                                    r'\s+else\n'
                                    r'\s+echo "\$tag \$commit\$\{tagged:\+ \$tagged\}" >> "\$due"\n')
        self.assertIn('python3 scripts/release.py summary < "$due" >> "$GITHUB_STEP_SUMMARY"', self.plan)
        self.assertIn('echo "due<<END_OF_DUE"\n            cat "$due"\n            echo "END_OF_DUE"\n', self.plan)
        self.assertIn("DUE: ${{ needs.plan.outputs.due }}", self.release)
        self.assertIn('printf \'%s\\n\' "$DUE" > "$list"', self.release)

    def test_pushes_nothing_and_makes_a_tag_only_where_the_script_found_one_missing(self):
        """GitHub makes the tag with the release, at the commit the script
        names; a tag that is already there is released, not made. The
        making of a tag is the condition of an if, so that a refusal says
        what to do (TheTaggingRuns). Was: the job chose between the two by
        the plan's word "tagged"; now by the tag GitHub holds when it gets
        there, and a refusal's advice may name git push, so only a command
        counts here."""
        code = code_lines(self.text)
        self.assertEqual([line for line in code if re.search(r"(^|[;&|]\s*)git (push|tag)\b", line.strip())], [])
        self.assertEqual([line.strip() for line in code if "gh release create" in line], [
            'if ! gh release create "$tag" --repo "$REPO" --target "$commit" --title "$title" --generate-notes; then',
            'gh release create "$tag" --repo "$REPO" --verify-tag --title "$title" --generate-notes',
        ])
        self.assertIn('python3 scripts/release.py missing > "$list"', self.plan)
        self.assertRegex(self.release, r'if \[ -z "\$at" \]; then\n\s+if ! gh release create "\$tag" --repo "\$REPO" '
                                       r'--target "\$commit"')
        self.assertNotIn("gh release create", self.plan)

    def test_a_tag_made_before_gets_its_release_only_where_it_stands(self):
        """Was: the version main carries was listed as "tagged" and released
        wherever its tag pointed, so a tag made by hand at a commit main
        never held would have been released by the approval."""
        self.assertIn('if tagged_at="$(git rev-parse --verify -q "refs/tags/v$version^{commit}")"; then\n'
                      '            echo "v$version $tagged_at tagged" >> "$list"\n', self.plan)
        self.assertRegex(self.plan, r'elif \[ -n "\$tagged" \] && ! why="\$\(python3 scripts/release.py check-tag '
                                    r'"\$tag" "\$commit"\)"; then\n'
                                    r'\s+echo "Nothing was listed for approval: \$why\. [^\n]*'
                                    r'\| tee -a "\$GITHUB_STEP_SUMMARY"\n'
                                    r'\s+exit 1\n')

    def test_reads_each_tag_on_github_before_it_releases_it(self):
        """GitHub makes no tag where one exists and releases whatever that
        one names, so a tag made at another commit while the run waited
        for its approval would be released in place of the one approved."""
        tagging = step_named(self.text, "release", "Tag each version the plan listed, and publish its release")
        self.assertIn('refs="$(gh api "repos/$REPO/git/matching-refs/tags/$1" --jq', tagging)
        self.assertIn('[ "$ref" = "refs/tags/$1" ] || continue', tagging)
        self.assertLess(tagging.index('at="$(tag_commit "$tag")"'), tagging.index("gh release create"))
        self.assertRegex(tagging, r'elif \[ "\$at" = "\$commit" \]; then\n'
                                  r'\s+gh release create "\$tag" --repo "\$REPO" --verify-tag')
        self.assertRegex(tagging, r'echo "Nothing more was released: the tag \$tag now points at \$at, not at '
                                  r'\$commit, [^\n]*\| tee -a "\$GITHUB_STEP_SUMMARY"\n\s+exit 1\n')

    def test_releases_only_a_tag_that_has_no_release(self):
        """Checked again just before it is made, since the approval may come
        long after the plan."""
        view = self.release.index('if gh release view "$tag" --repo "$REPO" > /dev/null 2>&1; then')
        self.assertLess(view, self.release.index("gh release create"))

    def test_a_version_it_cannot_read_stops_the_run(self):
        """An assignment takes its command's exit status, so set -e stops
        the run; inside echo's argument the failure would pass unseen. Was:
        the version went into the list's "tagged" line; now it names the
        tag the plan looks for."""
        self.assertIn('version="$(python3 scripts/release.py version)"', self.plan)
        self.assertLess(self.plan.index('version="$(python3 scripts/release.py version)"'),
                        self.plan.index('"refs/tags/v$version^{commit}"'))

    def test_the_loops_read_the_list_on_its_own_descriptor(self):
        """A command inside a loop that reads its standard input would
        otherwise take the rest of the list in its place. Was: both loops
        read two words a line; the plan's now reads the word tagged too,
        and the job leaves it, deciding by the tag GitHub holds."""
        self.assertIn("while read -r tag commit tagged <&3; do", self.plan)
        self.assertIn("while read -r tag commit _ <&3; do", self.release)
        for job in (self.plan, self.release):
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

    def test_every_output_a_job_reads_is_one_a_step_or_a_job_writes(self):
        """steps.<id>.outputs.<name> must name a step of the same job by its
        id, one that writes that name to GITHUB_OUTPUT unless an action
        writes it, and needs.<job>.outputs.<name> an output that job
        declares. A name that matches nothing reads as an empty string, and
        an empty ref makes actions/checkout take the run's own commit."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            jobs = jobs_of(path.read_text(encoding="ascii"))
            for name, job in jobs.items():
                ids = step_ids(job)
                for step_id, output in re.findall(r"steps\.([\w-]+)\.outputs\.([\w-]+)", job):
                    with self.subTest(workflow=path.name, job=name, step=step_id, output=output):
                        self.assertIn(step_id, ids)
                        if "uses: " not in ids[step_id]:
                            self.assertRegex(ids[step_id], rf'echo "{re.escape(output)}(=|<<)')
                            self.assertIn('"$GITHUB_OUTPUT"', ids[step_id])
                for needed, output in re.findall(r"needs\.([\w-]+)\.outputs\.([\w-]+)", job):
                    with self.subTest(workflow=path.name, job=name, needs=needed, output=output):
                        self.assertIn(needed, jobs)
                        self.assertIn(output, job_outputs(jobs[needed]))

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
# APPROVERS, as the step's own filter would, or fails when API_FAILS is set.
# A listing of tags prints, as the step's filter would, each "<tag>=<type>:
# <sha>" of ON_GITHUB whose name begins with the one asked for, as GitHub
# matches them, or fails when REFS_FAIL is set, and an annotated tag's own
# object comes from ANNOTATED, "<sha>=<type>:<sha>"; a release is made
# unless CREATE_FAILS is set.
STUB_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "$GH_CALLS"
case "$1 $2" in
  "release view")
    for tag in $RELEASED; do [ "$tag" = "$3" ] && exit 0; done
    exit 1 ;;
  "api --paginate")
    for tag in $RELEASE_TAGS; do printf '%s\\n' "$tag"; done ;;
  "api repos/"*"/approvals")
    [ -n "${API_FAILS:-}" ] && exit 1
    printf '%s\\n' "$APPROVERS" ;;
  "api repos/"*"/git/matching-refs/tags/"*)
    [ -n "${REFS_FAIL:-}" ] && exit 1
    asked="${2##*/matching-refs/tags/}"
    for entry in $ON_GITHUB; do
      name="${entry%%=*}" object="${entry#*=}"
      case "$name" in "$asked"*) printf 'refs/tags/%s %s %s\\n' "$name" "${object%%:*}" "${object#*:}" ;; esac
    done ;;
  "api repos/"*"/git/tags/"*)
    asked="${2##*/git/tags/}"
    for entry in $ANNOTATED; do
      object="${entry#*=}"
      [ "${entry%%=*}" = "$asked" ] && printf '%s %s\\n' "${object%%:*}" "${object#*:}"
    done ;;
  "release create")
    [ -n "${CREATE_FAILS:-}" ] && exit 1 ;;
esac
exit 0
"""
SHA = "1111111111111111111111111111111111111111"
OTHER = "3333333333333333333333333333333333333333"
TAG_OBJECT = "4444444444444444444444444444444444444444"


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
                                RELEASE_TAGS="", APPROVERS="", ON_GITHUB="", ANNOTATED="")

    def run_step(self, step, cwd, **environment):
        script = self.scratch / "step.sh"
        script.write_text(shell_script(shell_steps(step)[0][1]), encoding="ascii")
        return subprocess.run(["bash", "-e", str(script)], cwd=cwd, env=dict(self.environment, **environment),
                              capture_output=True, text=True, timeout=60)

    def read(self, file):
        return file.read_text(encoding="ascii")

    def clear(self):
        """Empty the files GitHub hands a step, for a second run in one test."""
        for file in (self.output, self.summary, self.calls):
            file.write_text("", encoding="ascii")


class ALibraryRuns(StepsRun):
    """A step run in a git repository of the test's own, the library as the
    workflow's checkout holds it, with this repository's scripts in it."""

    SCRIPTS = ()

    def setUp(self):
        super().setUp()
        self.library = self.scratch / "library"
        (self.library / "scripts").mkdir(parents=True)
        for name in self.SCRIPTS:
            shutil.copyfile(ROOT / "scripts" / name, self.library / "scripts" / name)
        self.git("init", "-q")

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


class TheReleasePlanRuns(ALibraryRuns):
    """The plan's step in a library of the test's own, with this
    repository's scripts/release.py in it."""

    STEP = "List each version main carries without a release, for the owner to approve"
    SCRIPTS = ("release.py",)

    def setUp(self):
        super().setUp()
        self.first = self.commit("0.1.0")
        self.git("tag", "v0.1.0")
        self.raised = self.commit("0.2.0")
        self.later = self.commit("0.2.0", "more.md")
        self.step = step_named(workflow("release.yml"), "plan", self.STEP)

    def plan(self, ref="refs/heads/main", released="v0.1.0", code=0):
        done = self.run_step(self.step, self.library, REF=ref, RELEASED=released)
        self.assertEqual(done.returncode, code, done.stderr)
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

    def test_a_tag_made_by_hand_gets_its_release_at_the_commit_it_stands_at(self):
        """Was: listed as "v0.2.0 tagged", with no commit, and released
        wherever its tag pointed. Now the line names the tag's commit, the
        one that raised main to its version, for the owner and for the job
        that checks the tag again before it releases it."""
        self.git("tag", "v0.2.0", self.raised)
        output, summary = self.plan()
        self.assertEqual(output, f"due<<END_OF_DUE\nv0.2.0 {self.raised} tagged\nEND_OF_DUE\n")
        self.assertIn(f"- v0.2.0: its tag was made before, at {self.raised}; it gets its release", summary)

    def test_a_tag_made_by_hand_anywhere_else_stops_the_run_and_lists_nothing(self):
        """A tag at a commit main never held, or at a later commit of main,
        would otherwise have been released wherever it pointed once the
        owner approved the run."""
        self.git("checkout", "-q", "-b", "stray", self.first)
        stray = self.commit("0.2.0", "stray.md")
        self.git("checkout", "-q", "main")
        for where in (stray, self.later):
            with self.subTest(where=where):
                self.git("tag", "-f", "v0.2.0", where)
                self.clear()
                output, summary = self.plan(code=1)
                self.assertEqual(output, "")
                self.assertTrue(summary.startswith(
                    f"Nothing was listed for approval: the tag v0.2.0 points at {where[:7]}, but main's first-parent "
                    f"line was raised to 0.2.0 at {self.raised[:7]}."), summary)
                self.assertIn("git push origin :refs/tags/v0.2.0", summary)
                self.assertEqual(summary.count("\n"), 1)

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
        self.assertIn("re-run this run, which GitHub allows for 30 days after it began, or run Release by hand "
                      "from main", summary)
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

    def tag(self, due, **environment):
        return self.run_step(self.step, self.scratch, DUE=due, **environment)

    def calls_made(self):
        """gh's calls, each without its --jq filter."""
        return [call.split(" --jq ")[0] for call in self.read(self.calls).splitlines()]

    def calls_for(self, due, **environment):
        done = self.tag(due, **environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.calls_made()

    def refs(self, tag):
        return f"api repos/octo/library/git/matching-refs/tags/{tag}"

    def test_makes_each_listed_release_once_from_the_plans_list(self):
        """Was: a tag made before was released by --verify-tag on the plan's
        word alone; now each tag is read on GitHub first."""
        calls = self.calls_for(f"v0.2.0 {SHA}\nv0.3.0 {OTHER} tagged", ON_GITHUB=f"v0.3.0=commit:{OTHER}")
        self.assertEqual(calls, [
            "release view v0.2.0 --repo octo/library",
            self.refs("v0.2.0"),
            f"release create v0.2.0 --repo octo/library --target {SHA} --title sdlc-protocol 0.2.0 --generate-notes",
            "release view v0.3.0 --repo octo/library",
            self.refs("v0.3.0"),
            "release create v0.3.0 --repo octo/library --verify-tag --title sdlc-protocol 0.3.0 --generate-notes",
        ])

    def test_a_release_made_while_it_waited_is_left_alone(self):
        self.assertEqual(self.calls_for(f"v0.2.0 {SHA}", RELEASED="v0.2.0"),
                         ["release view v0.2.0 --repo octo/library"])

    def test_a_tag_made_elsewhere_while_it_waited_stops_the_job_and_releases_nothing(self):
        """GitHub makes no tag where one exists and would release the one
        made since the plan, at a commit the owner never saw."""
        done = self.tag(f"v0.2.0 {SHA}\nv0.3.0 {SHA}", ON_GITHUB=f"v0.2.0=commit:{OTHER}")
        self.assertEqual(done.returncode, 1)
        summary = self.read(self.summary)
        self.assertTrue(summary.startswith(f"Nothing more was released: the tag v0.2.0 now points at {OTHER}, not at "
                                           f"{SHA}, the commit the plan listed for this approval."), summary)
        self.assertEqual(summary.count("\n"), 1)
        self.assertEqual([call for call in self.calls_made() if call.startswith("release create")], [])
        self.assertNotIn("v0.3.0", "\n".join(self.calls_made()))

    def test_a_tag_made_at_the_listed_commit_meanwhile_gets_its_release(self):
        calls = self.calls_for(f"v0.2.0 {SHA}", ON_GITHUB=f"v0.2.0=commit:{SHA}")
        self.assertEqual(calls[-1],
                         "release create v0.2.0 --repo octo/library --verify-tag --title sdlc-protocol 0.2.0 --generate-notes")

    def test_an_annotated_tag_is_followed_to_its_commit(self):
        for commit, code in ((SHA, 0), (OTHER, 1)):
            with self.subTest(commit=commit):
                self.clear()
                done = self.tag(f"v0.2.0 {SHA}", ON_GITHUB=f"v0.2.0=tag:{TAG_OBJECT}",
                                ANNOTATED=f"{TAG_OBJECT}=commit:{commit}")
                self.assertEqual(done.returncode, code, done.stderr)
                self.assertIn(f"api repos/octo/library/git/tags/{TAG_OBJECT}", self.calls_made())
                creates = [call for call in self.calls_made() if call.startswith("release create")]
                self.assertEqual(len(creates), 1 - code)

    def test_a_tag_whose_name_only_begins_the_same_is_another_tag(self):
        """GitHub lists every tag whose name begins with the one asked for."""
        calls = self.calls_for(f"v0.2.0 {SHA}", ON_GITHUB=f"v0.2.0-rc1=commit:{OTHER} v0.2.01=commit:{OTHER}")
        self.assertEqual(calls[-1],
                         f"release create v0.2.0 --repo octo/library --target {SHA} --title sdlc-protocol 0.2.0 "
                         "--generate-notes")

    def test_a_tag_it_cannot_read_stops_the_job(self):
        done = self.tag(f"v0.2.0 {SHA}", REFS_FAIL="1")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual([call for call in self.calls_made() if call.startswith("release create")], [])

    def test_a_tag_github_refuses_stops_the_job_and_says_what_to_do(self):
        """GitHub refuses GITHUB_TOKEN a tag at a commit whose workflows
        differ from main's, which a release that waited for its approval
        meets when a change to a workflow merged meanwhile. Was: push the
        tag yourself, which put a tag where the repositories made from the
        template, which take tags, would see it before any release; the
        owner, who approved it already, publishes the release instead."""
        done = self.tag(f"v0.2.0 {SHA}\nv0.3.0 {SHA}", CREATE_FAILS="1")
        self.assertEqual(done.returncode, 1)
        summary = self.read(self.summary)
        self.assertTrue(summary.startswith(f"GitHub did not make v0.2.0 at {SHA}."))
        self.assertIn("publish that release yourself, at that commit (README.md, Releases)", summary)
        self.assertNotIn("v0.3.0", "\n".join(self.calls_made()))


class TheSyncPlanRuns(ALibraryRuns):
    """The sync's plan step in a library of the test's own, with this
    repository's scripts, ecosystem.json and vendoring.py in it, whose main
    raised the version to each of VERSIONS in turn and tagged each where it
    was raised, as release.yml does, and then moved on by one commit."""

    STEP = "Choose the release to carry, and whether the sync may run"
    SCRIPTS = ("sync.py", "release.py", "ecosystem.py")
    VERSIONS = ("0.3.1", "0.4.0", "0.4.1", "0.9.9", "0.10.0")

    def setUp(self):
        super().setUp()
        shutil.copyfile(ROOT / "ecosystem.json", self.library / "ecosystem.json")
        (self.library / "sdlc_protocol").mkdir()
        shutil.copyfile(PACKAGE / "vendoring.py", self.library / "sdlc_protocol" / "vendoring.py")
        self.raised = {}
        for version in self.VERSIONS:
            self.raised[version] = self.commit(version)
            self.git("tag", f"v{version}")
        self.later = self.commit(self.VERSIONS[-1], "later.md")
        self.step = step_named(workflow("sync.yml"), "plan", self.STEP)
        self.event = self.scratch / "event.json"

    def plan(self, event="workflow_run", run=None, app_id="5258276", ref="refs/heads/main", tags="", code=0):
        run = {"conclusion": "success", "event": "push", "head_branch": "main",
               "head_repository": {"full_name": "octo/library"}} if run is None else run
        self.event.write_text(json.dumps({"workflow_run": run} if event == "workflow_run" else {}), encoding="ascii")
        done = self.run_step(self.step, self.library, EVENT=event, APP_ID=app_id, REF=ref, RELEASE_TAGS=tags,
                             GITHUB_EVENT_PATH=str(self.event))
        self.assertEqual(done.returncode, code, done.stderr)
        return self.read(self.output), self.read(self.summary)

    def test_after_main_s_release_run_it_carries_the_highest_release_by_its_commit(self):
        """Was: it handed on the tag alone, which the checkouts then named."""
        output, summary = self.plan(tags="v0.3.1 v0.10.0 v0.9.9 nightly")
        self.assertEqual(output, f"enabled=true\ntag=v0.10.0\ncommit={self.raised['0.10.0']}\n")
        self.assertTrue(summary.startswith("## The sync carries v0.10.0\n"))
        self.assertIn(f"at {self.raised['0.10.0']}, the commit that raised main to its version", summary)
        self.assertIn("api --paginate repos/octo/library/releases?per_page=100 --jq", self.read(self.calls))

    def test_by_hand_or_on_the_schedule_it_carries_the_highest_release_too(self):
        for event in ("workflow_dispatch", "schedule"):
            with self.subTest(event=event):
                self.clear()
                output, _ = self.plan(event=event, tags="v0.4.0 v0.4.1")
                self.assertEqual(output, f"enabled=true\ntag=v0.4.1\ncommit={self.raised['0.4.1']}\n")

    def test_a_newest_release_whose_tag_stands_anywhere_else_stops_the_run_red(self):
        """A release published by hand at another commit, or one whose tag
        was moved after its release, carries nothing, and the run fails so
        the owner sees it."""
        self.git("checkout", "-q", "-b", "side", self.raised["0.10.0"])
        side = self.commit("0.11.0", "side.md")
        self.git("tag", "v0.11.0")
        self.git("checkout", "-q", "main")
        self.git("tag", "-f", "v0.10.0", self.later)
        for tags, words in (("v0.10.0 v0.4.1", f"points at {self.later[:7]}, but main's first-parent line was raised "
                                                f"to 0.10.0 at {self.raised['0.10.0'][:7]}"),
                            ("v0.11.0 v0.10.0", f"points at {side[:7]}, but main's first-parent line never carries "
                                                "0.11.0"),
                            ("v0.12.0", "names no commit this checkout holds")):
            with self.subTest(tags=tags):
                self.clear()
                output, summary = self.plan(event="workflow_dispatch", tags=tags, code=1)
                self.assertEqual(output, "enabled=false\n")
                self.assertTrue(summary.startswith("## The sync did not run\n"))
                self.assertIn(words, summary)

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
                self.clear()
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


class TheSyncGuardRuns(StepsRun):
    """The step that lets a leg of the sync go on, in a folder that holds
    the release's checkout as the job leaves it, under the name release."""

    STEP = "Go on only with the commit of the release the plan chose"

    def setUp(self):
        super().setUp()
        self.release = self.scratch / "release"
        self.release.mkdir()
        self.git("init", "-q")
        self.first = self.commit("first")
        self.second = self.commit("second")
        self.git("tag", "v0.4.1", self.second)
        self.step = step_named(workflow("sync.yml"), "sync", self.STEP)

    def git(self, *args):
        done = subprocess.run(["git", "-c", "user.name=sync test", "-c", "user.email=sync@example.invalid",
                               "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                              cwd=self.release, capture_output=True, text=True, timeout=60, env=self.environment)
        if done.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
        return done.stdout.strip()

    def commit(self, name):
        (self.release / f"{name}.md").write_text(f"{name}\n", encoding="ascii")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def guard(self, tag="v0.4.1", commit=None):
        return self.run_step(self.step, self.scratch, TAG=tag, COMMIT=self.second if commit is None else commit)

    def test_goes_on_when_the_checkout_and_the_tag_are_the_plans_commit(self):
        done = self.guard()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.read(self.summary), "")

    def test_stops_when_the_plan_handed_on_no_commit(self):
        """The checkout is then this run's own commit, main's head, which
        the leg would otherwise vendor into the consumer."""
        for tag in ("v0.4.1", ""):
            with self.subTest(tag=tag):
                self.clear()
                done = self.guard(tag=tag, commit="")
                self.assertEqual(done.returncode, 1)
                self.assertTrue(self.read(self.summary).startswith("Nothing was vendored: the plan chose "))

    def test_stops_when_the_checkout_is_another_commit(self):
        self.git("checkout", "-q", self.first)
        self.assertEqual(self.guard().returncode, 1)

    def test_stops_when_the_tag_was_moved_since_the_plan(self):
        self.git("tag", "-f", "v0.4.1", self.first)
        self.assertEqual(self.guard().returncode, 1)
        self.assertIn(f"with that tag at {self.first}", self.read(self.summary))


class Version(unittest.TestCase):
    def test_is_three_numbers(self):
        self.assertRegex(sdlc_protocol.__version__, r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_the_package_says_what_it_is(self):
        self.assertTrue(sdlc_protocol.__doc__.strip())


if __name__ == "__main__":
    unittest.main()
