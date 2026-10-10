"""ecosystem.json, the one record of who takes what, and scripts/ecosystem.py,
which every script that acts on it reads it through.

The file decides what four repositories receive on every sync, so it is
held to its shape here before any workflow acts on it: every consumer
names a repository, the visibility GitHub gives it, a place for its copy
inside its tree, modules the package has, the check it runs, no
auto-merge on a private repository, where GitHub Free has none, and, for
the plugin marketplace, the manifest whose version the sync raises with
each new copy, without which no installed plugin would ever take one.
Above all, a consumer that takes only some modules must get
every module those import: a copy of protocol_check.py without claims.py
would pass the sync and fail in the App at its first pull request. The
imports are read with ast from the package's source, so the rule holds for
whatever the modules import next."""

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ecosystem  # noqa: E402

PACKAGE = ROOT / "sdlc_protocol"
ECO = ecosystem.load()


def changed(edit):
    """A copy of ecosystem.json with one edit made to it."""
    eco = copy.deepcopy(ECO)
    edit(eco)
    return eco


def consumer_of(eco, name):
    return ecosystem.consumer_named(eco, f"OpenSpineConsortium/{name}")


class TheFile(unittest.TestCase):
    def test_has_no_problem(self):
        self.assertEqual(ecosystem.problems(ECO), [])

    def test_names_this_library_and_its_package(self):
        self.assertEqual((ECO["library"], ECO["package"]), ("OpenSpineConsortium/sdlc-protocol", "sdlc_protocol"))

    def test_every_copy_takes_the_version_and_its_own_check(self):
        self.assertEqual(ECO["always_copied"], ["__init__.py", "vendoring.py"])

    def test_the_private_app_merges_by_hand_and_the_others_by_auto_merge(self):
        for consumer in ECO["consumers"]:
            with self.subTest(consumer=consumer["repository"]):
                self.assertEqual(consumer["auto_merge"], consumer["visibility"] != "private")

    def test_the_template_is_internal_as_github_has_it(self):
        """GitHub has the template internal, so another repository's run
        reads it only with the App's token, as the downstream job does for
        every consumer that is not public."""
        self.assertEqual(consumer_of(ECO, "sdlc-template")["visibility"], "internal")

    def test_the_marketplace_names_its_plugin_manifest_as_its_version_file(self):
        """Claude Code keeps an installed plugin at the version its manifest
        names, so the marketplace's copy reaches no session without it."""
        named = {c["repository"]: c["version_file"] for c in ECO["consumers"] if "version_file" in c}
        self.assertEqual(named, {"OpenSpineConsortium/sdlc-marketplace": "plugins/sdlc/.claude-plugin/plugin.json"})

    def test_every_named_module_exists(self):
        for consumer in ECO["consumers"]:
            if consumer["modules"] != ecosystem.ALL:
                for name in consumer["modules"]:
                    with self.subTest(consumer=consumer["repository"], module=name):
                        self.assertTrue((PACKAGE / name).is_file())


class TheImportClosure(unittest.TestCase):
    """Every module a consumer's copy holds finds what it imports there."""

    def test_holds_for_every_consumer(self):
        for consumer in ECO["consumers"]:
            with self.subTest(consumer=consumer["repository"]):
                files = ecosystem.files_for(ECO, consumer)
                self.assertEqual(ecosystem.missing_imports(files), [])

    def test_protocol_check_needs_claims_and_the_guard_needs_nothing(self):
        self.assertEqual(ecosystem.imports_of("protocol_check.py"), ["claims.py"])
        self.assertEqual(ecosystem.imports_of("guard.py"), [])
        self.assertEqual(ecosystem.imports_of("vendoring.py"), [])

    def test_relative_imports_from_a_subpackage_climb_to_the_package(self):
        self.assertEqual(ecosystem.imports_of("adapters/github.py"),
                         ["board.py", "claims.py", "gate.py", "labels.py", "team.py"])
        self.assertEqual(ecosystem.imports_of("adapters/bare_git.py"), ["gate.py"])

    def test_an_import_inside_a_function_counts(self):
        self.assertIn("adapters/github.py", ecosystem.imports_of("cli.py"))

    def test_an_absolute_import_of_the_package_counts(self):
        self.assertEqual(ecosystem.imports_of("__main__.py"), ["cli.py"])

    def test_a_module_in_a_subpackage_needs_the_packages_around_it(self):
        self.assertEqual(ecosystem.missing_imports(["adapters/bare_git.py", "gate.py", "claims.py"]),
                         [("adapters/bare_git.py", "__init__.py"), ("adapters/bare_git.py", "adapters/__init__.py"),
                          ("claims.py", "__init__.py"), ("gate.py", "__init__.py")])

    def test_a_copy_that_leaves_out_an_import_is_a_problem(self):
        eco = changed(lambda eco: consumer_of(eco, "sdlc-app").update(modules=["protocol_check.py"]))
        self.assertEqual(ecosystem.problems(eco),
                         ["OpenSpineConsortium/sdlc-app: protocol_check.py imports claims.py, which its copy "
                          "leaves out"])

    def test_every_module_imports_only_the_standard_library(self):
        for name in ecosystem.module_files():
            with self.subTest(module=name):
                self.assertLessEqual(set(ecosystem.outside_imports(name)), set(sys.stdlib_module_names))

    def test_an_import_of_a_module_that_is_not_there_is_named(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch)
            (package / "__init__.py").write_text("", encoding="ascii")
            (package / "rule.py").write_text("from .gone import x\nfrom . import also_gone\n", encoding="ascii")
            self.assertEqual(ecosystem.imports_of("rule.py", package), ["__init__.py", "gone.py"])
            self.assertEqual(ecosystem.missing_imports(["__init__.py", "rule.py"], package), [("rule.py", "gone.py")])


class TheShape(unittest.TestCase):
    """Each kind of mistake the file can hold is named."""

    def assertProblem(self, edit, words):
        found = ecosystem.problems(changed(edit))
        self.assertTrue(any(words in problem for problem in found), found)

    def test_a_missing_or_unknown_key(self):
        self.assertProblem(lambda eco: consumer_of(eco, "coordbench").pop("check"), "coordbench lacks check")
        self.assertProblem(lambda eco: consumer_of(eco, "coordbench").update(branch="x"),
                           "has branch, which nothing reads")
        self.assertProblem(lambda eco: eco.pop("always_copied"), "ecosystem.json lacks always_copied")

    def test_a_repository_that_is_not_owner_and_name(self):
        self.assertProblem(lambda eco: eco["consumers"][0].update(repository="sdlc-template"),
                           "repository is not OWNER/NAME")

    def test_a_visibility_github_does_not_have(self):
        self.assertProblem(lambda eco: consumer_of(eco, "coordbench").update(visibility="secret"),
                           "visibility is not one of public, internal, private")

    def test_auto_merge_on_a_private_repository(self):
        self.assertProblem(lambda eco: consumer_of(eco, "sdlc-app").update(auto_merge=True),
                           "GitHub Free offers no auto-merge")

    def test_auto_merge_on_an_internal_repository_is_no_problem(self):
        """Only GitHub Enterprise Cloud has internal repositories, and it has
        auto-merge on them."""
        eco = changed(lambda eco: consumer_of(eco, "coordbench").update(visibility="internal"))
        self.assertEqual(ecosystem.problems(eco), [])

    def test_a_vendor_dir_outside_the_repository(self):
        for vendor_dir in ("../tools", "/tools", ""):
            with self.subTest(vendor_dir=vendor_dir):
                self.assertProblem(lambda eco: consumer_of(eco, "sdlc-template").update(vendor_dir=vendor_dir),
                                   "vendor_dir is not a relative path inside the repository")

    def test_a_module_the_package_does_not_have(self):
        self.assertProblem(lambda eco: consumer_of(eco, "sdlc-marketplace").update(modules=["guards.py"]),
                           "names guards.py, which sdlc_protocol/ does not have")
        self.assertProblem(lambda eco: eco.update(always_copied=["__init__.py", "vendor.py"]),
                           "always_copied names vendor.py")

    def test_modules_that_are_neither_all_nor_a_list(self):
        self.assertProblem(lambda eco: consumer_of(eco, "sdlc-marketplace").update(modules="guard.py"),
                           "modules is not a list of the package's files")

    def test_a_consumer_listed_twice(self):
        self.assertProblem(lambda eco: eco["consumers"].append(copy.deepcopy(eco["consumers"][0])),
                           "is listed more than once")

    def test_a_version_file_is_optional_and_read(self):
        self.assertNotIn("version_file", consumer_of(ECO, "sdlc-template"))
        self.assertFalse(any("version_file" in problem for problem in ecosystem.problems(ECO)))

    def test_a_version_file_outside_the_repository_or_not_json(self):
        for path in ("../plugin.json", "/plugin.json", "plugin.toml", 3):
            with self.subTest(path=path):
                self.assertProblem(lambda eco: consumer_of(eco, "sdlc-marketplace").update(version_file=path),
                                   "version_file is not a relative path to a .json file inside the repository")

    def test_a_version_file_inside_the_copy(self):
        self.assertProblem(lambda eco: consumer_of(eco, "sdlc-marketplace").update(
            version_file="plugins/sdlc/vendor/sdlc_protocol/plugin.json"), "version_file lies inside the copy")

    def test_an_empty_sentence(self):
        """A blank check above all: the downstream job would run bash -c "",
        which passes without checking anything."""
        for key in ("about", "check", "uses"):
            for blank in (" ", ""):
                with self.subTest(key=key, value=blank):
                    self.assertProblem(lambda eco: consumer_of(eco, "coordbench").update({key: blank}),
                                       f"coordbench: {key} is not a sentence")


class TheVersionFile(unittest.TestCase):
    """Reading and raising the version a consumer's version file states."""

    def test_reads_the_version(self):
        self.assertEqual(ecosystem.stated_version('{"name": "sdlc", "version": "0.1.0"}', "plugin.json"), "0.1.0")

    def test_refuses_what_is_no_version_it_can_raise(self):
        for text in ("not json", "[]", "{}", '{"version": 1}', '{"version": "1.0"}', '{"version": "v1.0.0"}',
                     '{"version": "1.0.0-rc.1"}'):
            with self.subTest(text=text):
                with self.assertRaises(ecosystem.EcosystemError) as caught:
                    ecosystem.stated_version(text, "plugin.json")
                self.assertIn("plugin.json", str(caught.exception))

    def test_the_next_patch_version(self):
        self.assertEqual([ecosystem.next_patch(v) for v in ("0.1.0", "0.1.9", "2.0.41")], ["0.1.1", "0.1.10", "2.0.42"])

    def test_raising_changes_the_version_and_no_other_byte(self):
        text = '{\n  "name": "sdlc",\n  "version":   "0.1.0",\r\n  "keywords": ["0.1.0"]\n}'
        self.assertEqual(ecosystem.with_version(text, "0.1.1", "plugin.json"),
                         '{\n  "name": "sdlc",\n  "version":   "0.1.1",\r\n  "keywords": ["0.1.0"]\n}')

    def test_a_version_it_cannot_tell_apart_is_refused(self):
        with self.assertRaises(ecosystem.EcosystemError) as caught:
            ecosystem.with_version('{"version": "0.1.0", "engine": {"version": "0.1.0"}}', "0.1.1", "plugin.json")
        self.assertIn("raise it by hand", str(caught.exception))


class FindingAndCopying(unittest.TestCase):
    def test_a_consumer_is_found_whatever_the_case(self):
        self.assertEqual(ecosystem.consumer_named(ECO, "openspineconsortium/SDLC-APP")["vendor_dir"], "app/vendor")

    def test_an_unknown_consumer_lists_the_consumers(self):
        with self.assertRaises(ecosystem.EcosystemError) as caught:
            ecosystem.consumer_named(ECO, "octo/widgets")
        for consumer in ECO["consumers"]:
            self.assertIn(consumer["repository"], str(caught.exception))

    def test_all_is_every_file_of_the_package(self):
        files = ecosystem.files_for(ECO, consumer_of(ECO, "sdlc-template"))
        self.assertEqual(files, ecosystem.vendoring.copied_files(PACKAGE))
        self.assertIn("adapters/github.py", files)

    def test_a_list_is_its_modules_and_what_every_copy_takes(self):
        self.assertEqual(ecosystem.files_for(ECO, consumer_of(ECO, "sdlc-app")),
                         ["__init__.py", "claims.py", "protocol_check.py", "vendoring.py"])

    def test_module_names_read_as_a_reader_names_them(self):
        self.assertEqual([ecosystem.module_name(name) for name in
                          ("guard.py", "adapters/github.py", "adapters/__init__.py", "__init__.py")],
                         ["guard", "adapters.github", "adapters", "__init__"])


if __name__ == "__main__":
    unittest.main()
