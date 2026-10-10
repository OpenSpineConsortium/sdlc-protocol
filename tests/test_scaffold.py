"""The repository around the package: the vendoring script, the spec, the
build file and the workflow.

The template and the plugin take the package only through
scripts/vendor.sh, so the copy it makes and the line it writes in
tools/VENDORED are what those repositories trust; a vendoring that left a
removed module behind, or named a commit the copy is not, would fail
there, far from here. The spec reaches readers who never see the
template, so a {{placeholder}} left in it is a hole in the protocol, and a
policy field or world method it does not name is one nobody can learn
about. pyproject.toml is what an install reads, so it must parse and agree
with the package it builds. And the workflow's job must keep the name
branch protection requires.
"""

import dataclasses
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sdlc_protocol  # noqa: E402
from sdlc_protocol import gate  # noqa: E402

PACKAGE = ROOT / "sdlc_protocol"
VENDOR = ROOT / "scripts" / "vendor.sh"
SPEC = ROOT / "spec" / "PROTOCOL.md"
MARKER = re.compile(r"sdlc-protocol (\S+) ([0-9a-f]{40}(?:-dirty)?|unknown)\n")
PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")


def package_files(root):
    """The package's files below root, as relative paths, without the
    caches Python writes beside them."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")


def caches(root):
    return [path for path in root.rglob("*") if path.name == "__pycache__" or path.suffix == ".pyc"]


def run_vendor(script, *args):
    return subprocess.run(["bash", str(script), *map(str, args)], capture_output=True, text=True, timeout=60)


def head_commit():
    """This checkout's commit, or None outside a git checkout."""
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


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


class TemporaryTarget(unittest.TestCase):
    """A fresh directory to vendor into, removed after each test."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.target = self.scratch / "target"
        self.target.mkdir()
        self.dest = self.target / "tools" / "sdlc_protocol"
        self.marker = self.target / "tools" / "VENDORED"


class VendorFromThisCheckout(TemporaryTarget):
    """scripts/vendor.sh run as a consumer runs it, from this checkout."""

    def vendor(self):
        done = run_vendor(VENDOR, self.target)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def test_copies_every_file_of_the_package_byte_for_byte(self):
        self.vendor()
        self.assertEqual(package_files(self.dest), package_files(PACKAGE))
        for name in package_files(PACKAGE):
            self.assertEqual((self.dest / name).read_bytes(), (PACKAGE / name).read_bytes(), name)

    def test_the_copy_holds_no_python_cache(self):
        self.vendor()
        self.assertEqual(caches(self.dest), [])

    def test_replaces_the_old_copy_and_leaves_the_rest_of_tools_alone(self):
        removed = self.dest / "removed_module.py"
        removed.parent.mkdir(parents=True)
        removed.write_text("a module a later version dropped\n")
        neighbour = self.target / "tools" / "check_docs.py"
        neighbour.write_text("the target's own tool\n")
        self.vendor()
        self.assertFalse(removed.exists())
        self.assertEqual(neighbour.read_text(), "the target's own tool\n")

    def test_writes_the_version_and_the_commit(self):
        self.vendor()
        found = MARKER.fullmatch(self.marker.read_text())
        self.assertIsNotNone(found, self.marker.read_text())
        self.assertEqual(found.group(1), sdlc_protocol.__version__)
        head = head_commit()
        if head:
            self.assertEqual(found.group(2).removesuffix("-dirty"), head)

    def test_says_what_it_did(self):
        done = self.vendor()
        self.assertIn(str(self.dest), done.stdout)
        self.assertIn(f"{self.marker}: {self.marker.read_text().strip()}", done.stdout)

    def test_vendoring_twice_gives_the_same_copy(self):
        self.vendor()
        first = {name: (self.dest / name).read_bytes() for name in package_files(self.dest)}
        self.vendor()
        again = {name: (self.dest / name).read_bytes() for name in package_files(self.dest)}
        self.assertEqual(again, first)


class VendorFromACopy(TemporaryTarget):
    """vendor.sh run from a copy of this repository's package and scripts
    outside any git checkout, where a test may plant caches without
    touching the real tree."""

    def setUp(self):
        super().setUp()
        self.source = self.scratch / "source"
        shutil.copytree(PACKAGE, self.source / "sdlc_protocol", ignore=shutil.ignore_patterns("__pycache__"))
        (self.source / "scripts").mkdir()
        self.script = self.source / "scripts" / "vendor.sh"
        shutil.copy2(VENDOR, self.script)

    def test_strips_the_caches_it_finds(self):
        planted = self.source / "sdlc_protocol" / "__pycache__"
        planted.mkdir()
        (planted / "gate.cpython-312.pyc").write_bytes(b"\0")
        (self.source / "sdlc_protocol" / "stray.pyc").write_bytes(b"\0")
        done = run_vendor(self.script, self.target)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(caches(self.dest), [])
        self.assertEqual(package_files(self.dest), package_files(PACKAGE))

    def test_names_the_commit_unknown_outside_git(self):
        # A scratch directory inside a checkout would see that checkout's
        # commit; the script must not claim it.
        done = run_vendor(self.script, self.target)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.marker.read_text(), f"sdlc-protocol {sdlc_protocol.__version__} unknown\n")

    def test_refuses_to_vendor_into_its_own_repository(self):
        done = run_vendor(self.script, self.source)
        self.assertEqual(done.returncode, 1)
        self.assertIn("this repository", done.stderr)
        self.assertFalse((self.source / "tools").exists())


class VendorRefusals(unittest.TestCase):
    def test_no_target_is_a_usage_error(self):
        done = run_vendor(VENDOR)
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage: scripts/vendor.sh <target repository>", done.stderr)

    def test_a_target_that_is_not_a_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            missing = Path(scratch) / "nowhere"
            done = run_vendor(VENDOR, missing)
            self.assertEqual(done.returncode, 1)
            self.assertIn("is not a directory", done.stderr)
            self.assertFalse(missing.exists())


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


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="ascii")

    def test_the_job_is_named_check(self):
        self.assertRegex(self.text, r"(?m)^jobs:\n  check:\n")

    def test_runs_the_checks_on_python_3_12(self):
        self.assertIn('python-version: "3.12"', self.text)
        self.assertIn("run: bash scripts/check.sh", self.text)


class Version(unittest.TestCase):
    def test_is_three_numbers(self):
        self.assertRegex(sdlc_protocol.__version__, r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_the_package_says_what_it_is(self):
        self.assertTrue(sdlc_protocol.__doc__.strip())


if __name__ == "__main__":
    unittest.main()
