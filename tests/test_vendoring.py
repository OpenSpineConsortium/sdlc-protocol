"""The record a vendored copy carries, and the check a consumer runs on it.

A consumer's check runs sdlc_protocol/vendoring.py as a script inside its
copy, which may hold only a few of the package's files, so the module must
run with nothing of the package around it, and its answers are what stops
a hand edit of the copy on the consumer's pull request: each kind of
difference must be named, the old one-line record must ask for a
re-vendor, Python's caches must never count, and the exit codes must be
the ones a check script reads (0 matches, 1 problems, 2 usage)."""

import ast
import hashlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sdlc_protocol  # noqa: E402
from sdlc_protocol import vendoring  # noqa: E402

PACKAGE = ROOT / "sdlc_protocol"
SHA = "0123456789abcdef0123456789abcdef01234567"
ONE = "1" * 64
TWO = "2" * 64


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Scratch(unittest.TestCase):
    """A vendor directory of its own for each test."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.vendor = self.scratch / "vendor"
        self.copy = self.vendor / "sdlc_protocol"
        self.record = self.vendor / "VENDORED"

    def make_copy(self, names=("__init__.py", "guard.py", "vendoring.py"), commit=SHA):
        """A partial copy of the real package, as the plugin's is, with its
        record."""
        self.copy.mkdir(parents=True)
        for name in names:
            shutil.copyfile(PACKAGE / name, self.copy / name)
        text = vendoring.record_text(sdlc_protocol.__version__, commit, vendoring.digests_of(self.copy))
        self.record.write_text(text, encoding="ascii")


class TheRecord(unittest.TestCase):
    def test_round_trip(self):
        digests = {"guard.py": ONE, "adapters/github.py": TWO}
        text = vendoring.record_text("0.2.0", SHA, digests)
        self.assertEqual(vendoring.parse_record(text), ("0.2.0", SHA, digests))

    def test_the_layout_is_sha256sums_sorted_by_path_under_the_old_first_line(self):
        text = vendoring.record_text("0.2.0", SHA, {"guard.py": ONE, "__init__.py": TWO, "adapters/x.py": ONE})
        self.assertEqual(text, f"sdlc-protocol 0.2.0 {SHA}\n{TWO}  __init__.py\n{ONE}  adapters/x.py\n"
                               f"{ONE}  guard.py\n")

    def test_a_dirty_or_unknown_commit_is_recorded_as_it_is(self):
        for commit in (f"{SHA}-dirty", "unknown"):
            with self.subTest(commit=commit):
                text = vendoring.record_text("0.2.0", commit, {"guard.py": ONE})
                self.assertEqual(vendoring.parse_record(text)[1], commit)

    def test_a_version_or_commit_that_would_not_read_back_is_refused(self):
        for version, commit in (("0.2 0", SHA), ("0.2.0", "abc"), ("0.2.0", SHA.upper())):
            with self.subTest(version=version, commit=commit), self.assertRaises(ValueError):
                vendoring.record_text(version, commit, {"guard.py": ONE})

    def test_the_old_record_of_one_line_asks_for_a_re_vendor(self):
        with self.assertRaises(ValueError) as caught:
            vendoring.parse_record(f"sdlc-protocol 0.1.0 {SHA}\n")
        self.assertIn("old record of one line", str(caught.exception))
        self.assertIn("re-vendor", str(caught.exception))

    def test_malformed_records_are_refused(self):
        cases = {
            "empty": "",
            "another first line": f"protocol 0.1.0 {SHA}\n{ONE}  guard.py\n",
            "a short hash": f"sdlc-protocol 0.1.0 {SHA}\nabc  guard.py\n",
            "one space": f"sdlc-protocol 0.1.0 {SHA}\n{ONE} guard.py\n",
            "a path that climbs out": f"sdlc-protocol 0.1.0 {SHA}\n{ONE}  ../guard.py\n",
            "an absolute path": f"sdlc-protocol 0.1.0 {SHA}\n{ONE}  /etc/guard.py\n",
            "a blank line": f"sdlc-protocol 0.1.0 {SHA}\n\n{ONE}  guard.py\n",
            "a file twice": f"sdlc-protocol 0.1.0 {SHA}\n{ONE}  guard.py\n{TWO}  guard.py\n",
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                vendoring.parse_record(text)


class TheCopysFiles(Scratch):
    def test_digests_are_sha256_by_path_inside_the_copy(self):
        (self.copy / "adapters").mkdir(parents=True)
        (self.copy / "guard.py").write_bytes(b"guard\n")
        (self.copy / "adapters" / "github.py").write_bytes(b"github\n")
        self.assertEqual(vendoring.digests_of(self.copy),
                         {"adapters/github.py": sha256(b"github\n"), "guard.py": sha256(b"guard\n")})

    def test_caches_are_never_counted(self):
        (self.copy / "__pycache__").mkdir(parents=True)
        (self.copy / "__pycache__" / "guard.cpython-312.pyc").write_bytes(b"\0")
        (self.copy / "stray.pyc").write_bytes(b"\0")
        (self.copy / "guard.py").write_bytes(b"guard\n")
        self.assertEqual(vendoring.copied_files(self.copy), ["guard.py"])


class TheCheck(Scratch):
    def test_a_copy_that_matches_its_record_has_no_problem(self):
        self.make_copy()
        self.assertEqual(vendoring.problems(self.vendor), [])

    def test_the_caches_running_the_copy_writes_are_no_problem(self):
        self.make_copy()
        (self.copy / "__pycache__").mkdir()
        (self.copy / "__pycache__" / "guard.cpython-312.pyc").write_bytes(b"\0")
        self.assertEqual(vendoring.problems(self.vendor), [])

    def test_no_record(self):
        self.make_copy()
        self.record.unlink()
        [problem] = vendoring.problems(self.vendor)
        self.assertIn("VENDORED is missing", problem)

    def test_the_old_record(self):
        self.make_copy()
        self.record.write_text(f"sdlc-protocol 0.1.0 {SHA}\n", encoding="ascii")
        [problem] = vendoring.problems(self.vendor)
        self.assertIn("cannot be read", problem)
        self.assertIn("re-vendor", problem)

    def test_a_record_that_is_not_ascii(self):
        self.make_copy()
        self.record.write_bytes(b"sdlc-protocol 0.1.0 \xc3\xa9\n")
        [problem] = vendoring.problems(self.vendor)
        self.assertIn("cannot be read", problem)

    def test_no_copy(self):
        self.make_copy()
        shutil.rmtree(self.copy)
        [problem] = vendoring.problems(self.vendor)
        self.assertIn("sdlc_protocol is missing", problem)

    def test_a_recorded_file_lost(self):
        self.make_copy()
        (self.copy / "guard.py").unlink()
        [problem] = vendoring.problems(self.vendor)
        self.assertIn("guard.py is recorded in VENDORED but missing", problem)

    def test_a_file_edited_by_hand(self):
        self.make_copy()
        with (self.copy / "guard.py").open("a", encoding="ascii") as guard:
            guard.write("# a local fix\n")
        [problem] = vendoring.problems(self.vendor)
        self.assertIn("guard.py was edited by hand: change it in sdlc-protocol and let the sync bring it",
                      problem)

    def test_a_file_added_beside_the_copy(self):
        self.make_copy()
        (self.copy / "local.py").write_text("x = 1\n", encoding="ascii")
        [problem] = vendoring.problems(self.vendor)
        self.assertIn("local.py is not recorded in VENDORED", problem)

    def test_every_problem_is_named_in_path_order(self):
        self.make_copy()
        (self.copy / "__init__.py").unlink()
        (self.copy / "guard.py").write_text("edited\n", encoding="ascii")
        (self.copy / "local.py").write_text("x = 1\n", encoding="ascii")
        found = vendoring.problems(self.vendor)
        self.assertEqual([Path(problem.split(" ")[0]).name for problem in found],
                         ["__init__.py", "guard.py", "local.py"])

    def test_verify_says_how_many_files_match(self):
        self.make_copy()
        out = io.StringIO()
        self.assertEqual(vendoring.verify(self.vendor, out), 0)
        self.assertEqual(out.getvalue(),
                         f"vendored sdlc-protocol {sdlc_protocol.__version__} at {SHA[:7]}: 3 files match VENDORED\n")


class AsAScript(Scratch):
    """The form a consumer's check runs: the file inside a partial copy,
    by its path, with nothing else of the package beside it."""

    def run_script(self, *args):
        return subprocess.run([sys.executable, "-B", str(self.copy / "vendoring.py"), *map(str, args)],
                              capture_output=True, text=True, timeout=60, cwd=self.scratch)

    def test_a_matching_copy_exits_0_with_one_line(self):
        self.make_copy()
        done = self.run_script("verify", self.vendor)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"vendored sdlc-protocol {sdlc_protocol.__version__} at {SHA[:7]}: "
                                      "3 files match VENDORED\n")

    def test_a_relative_vendor_dir_names_the_files_as_the_check_sees_them(self):
        self.make_copy()
        (self.copy / "guard.py").write_text("edited\n", encoding="ascii")
        done = self.run_script("verify", "vendor")
        self.assertEqual(done.returncode, 1)
        self.assertIn("vendor/sdlc_protocol/guard.py was edited by hand", done.stdout)

    def test_usage_errors_exit_2(self):
        self.make_copy()
        for args in ((), ("check", self.vendor), ("verify",), ("verify", self.vendor, "again")):
            with self.subTest(args=args):
                done = self.run_script(*args)
                self.assertEqual(done.returncode, 2)
                self.assertIn("usage:", done.stderr)

    def test_help_exits_0(self):
        self.make_copy()
        done = self.run_script("--help")
        self.assertEqual((done.returncode, done.stdout), (0, vendoring.USAGE + "\n"))


class StandsAlone(unittest.TestCase):
    """What lets the file run as a script inside any copy."""

    def imports(self):
        tree = ast.parse((PACKAGE / "vendoring.py").read_text(encoding="utf-8"))
        return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]

    def test_imports_nothing_from_the_package(self):
        for node in self.imports():
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, ast.dump(node))
                self.assertNotEqual(node.module.split(".")[0], "sdlc_protocol")

    def test_imports_only_the_standard_library(self):
        names = {alias.name.split(".")[0] for node in self.imports() if isinstance(node, ast.Import)
                 for alias in node.names}
        names |= {node.module.split(".")[0] for node in self.imports() if isinstance(node, ast.ImportFrom)}
        self.assertLessEqual(names, set(sys.stdlib_module_names))

    def test_no_module_of_the_package_shadows_the_standard_library(self):
        """The script runs with the package's directory first on sys.path,
        where a module named like one of the standard library's would be
        imported in its place."""
        for path in PACKAGE.iterdir():
            with self.subTest(name=path.name):
                self.assertNotIn(path.stem, sys.stdlib_module_names)


class ShortCommit(unittest.TestCase):
    def test_seven_digits_keeping_dirty(self):
        self.assertEqual(vendoring.short_commit(SHA), SHA[:7])
        self.assertEqual(vendoring.short_commit(f"{SHA}-dirty"), f"{SHA[:7]}-dirty")
        self.assertEqual(vendoring.short_commit("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
