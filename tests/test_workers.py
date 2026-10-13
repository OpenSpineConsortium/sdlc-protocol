"""The registry of workers: what a worker's file may say, how a folder of
them is read and checked, which paths an area takes, and how the "Add a
worker" form's answers become a worker. No network and no git."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdlc_protocol import workers  # noqa: E402


def problems(**data):
    return workers.problems_of(data)


class Settings(unittest.TestCase):
    def test_a_name_and_how_it_runs_are_all_a_worker_needs(self):
        worker = workers.from_dict({"name": "nightly", "runs": "routine"})
        self.assertEqual(worker, workers.Worker("nightly", "routine"))
        self.assertEqual((worker.lands, worker.max_turns, worker.rounds, worker.area), ("owner", 80, 6, ()))
        self.assertEqual((worker.label, worker.file_name), ("worker:nightly", "nightly.json"))

    def test_the_file_writes_every_setting_in_order(self):
        worker = workers.from_dict({"name": "api", "runs": "actions", "area": ["api/"], "lands": "auto-merge",
                                    "model": "claude-sonnet-5-5", "max_turns": 40, "rounds": 2})
        self.assertEqual(worker.to_json(), json.dumps({
            "name": "api", "runs": "actions", "about": "", "area": ["api/"], "lands": "auto-merge",
            "model": "claude-sonnet-5-5", "max_turns": 40, "rounds": 2, "account": ""}, indent=2) + "\n")
        self.assertEqual(workers.parse(worker.to_json()), worker)
        self.assertEqual(workers.KEYS, ("name", "runs", "about", "area", "lands", "model", "max_turns", "rounds",
                                        "account"))

    def test_names_are_lower_case_words_with_inner_dashes(self):
        for name in ("nightly", "alice-api", "w1", "a", "x" * 39):
            self.assertEqual(problems(name=name, runs="session"), [], name)
        for name in ("Nightly", "-a", "a-", "a_b", "a b", "", "x" * 40, 7, None):
            self.assertTrue(problems(name=name, runs="session"), name)

    def test_each_setting_is_held_to_its_values(self):
        self.assertEqual(problems(name="a", runs="cron"), ["runs must be one of actions, routine, session"])
        self.assertEqual(problems(name="a", runs="session", lands="squash"), ["lands must be one of owner, auto-merge"])
        self.assertIn("model must be", problems(name="a", runs="actions", model="opus please")[0])
        self.assertIn("max_turns must be a whole number from 1 to 500",
                      problems(name="a", runs="actions", max_turns=0)[0])
        self.assertIn("rounds must be", problems(name="a", runs="actions", rounds=True)[0])
        self.assertIn("about must be one line", problems(name="a", runs="session", about="x" * 201)[0])
        self.assertIn("account must be a note", problems(name="a", runs="routine", account="caf\u00e9")[0])
        self.assertEqual(problems(name="a", runs="session", area="docs/"), ["area must be a list of paths or globs"])

    def test_an_area_stays_inside_the_repository(self):
        for pattern in ("/etc", "../x", "docs/../../x", "a\\b", "", "   "):
            self.assertTrue(problems(name="a", runs="session", area=[pattern]), pattern)
        self.assertEqual(problems(name="a", runs="session", area=["docs/", "src/**/*.py", "README.md"]), [])

    def test_an_unknown_setting_is_named_first(self):
        found = problems(name="a", runs="session", schedule="hourly")
        self.assertTrue(found[0].startswith("schedule is not a setting a worker has"), found)

    def test_a_number_too_long_for_int_in_a_file_is_named_in_the_checks_words(self):
        # JSON reads an integer through int(), which refuses a run of more
        # than 4,300 digits, so such a file was called "not JSON", though it
        # is JSON; it is a file whose number is no whole number from 1 to 500.
        for key, stop in (("max_turns", 500), ("rounds", 50)):
            for literal in ("1" * 4301, "-" + "1" * 4301, "1" * 10):
                with self.assertRaises(workers.WorkerError) as caught:
                    workers.parse(f'{{"name": "a", "runs": "actions", "{key}": {literal}}}')
                self.assertEqual(str(caught.exception), f"{key} must be a whole number from 1 to {stop}", literal[:20])
        for key in ("name", "about", "model", "account"):
            with self.assertRaises(workers.WorkerError, msg=key):
                workers.parse(f'{{"name": "a", "runs": "actions", "{key}": {"1" * 4301}}}')
        self.assertEqual(workers.parse('{"name": "a", "runs": "actions", "max_turns": 40}').max_turns, 40)

    def test_a_file_that_is_no_json_object_says_so(self):
        with self.assertRaises(workers.WorkerError) as caught:
            workers.parse("[1]")
        self.assertEqual(str(caught.exception), "a worker's file must hold one JSON object")
        with self.assertRaises(workers.WorkerError) as caught:
            workers.parse("{")
        self.assertTrue(str(caught.exception).startswith("not JSON:"))


class Describing(unittest.TestCase):
    def test_one_line_names_where_it_runs_its_area_and_what_it_is_for(self):
        self.assertEqual(workers.describe(workers.Worker("api", "actions", "API fixes", ("api/", "docs/api/"))),
                         "api (actions): area api/, docs/api/; API fixes")
        self.assertEqual(workers.describe(workers.Worker("alice", "session")), "alice (session): area anywhere")


class Registry(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write(self, name, text):
        (self.folder / name).write_text(text, encoding="utf-8")

    def test_load_reads_every_worker_in_name_order(self):
        self.write("web.json", '{"name": "web", "runs": "actions"}')
        self.write("alice.json", '{"name": "alice", "runs": "session"}')
        self.write("README.md", "The registry.\n")
        found = workers.load(self.folder)
        self.assertEqual([w.name for w in found], ["alice", "web"])
        self.assertEqual([w.name for w in workers.in_actions(found)], ["web"])
        self.assertEqual(workers.named(found, "web").runs, "actions")
        self.assertIsNone(workers.named(found, "ghost"))

    def test_a_missing_folder_is_no_workers(self):
        self.assertEqual(workers.load(self.folder / "none"), [])

    def test_every_problem_in_every_file_is_named_at_once(self):
        self.write("a.json", '{"name": "b", "runs": "session"}')
        self.write("c.json", '{"name": "c", "runs": "nowhere"}')
        self.write("d.yml", "name: d\n")
        with self.assertRaises(workers.WorkerError) as caught:
            workers.load(self.folder)
        self.assertEqual(str(caught.exception).splitlines(), [
            "a.json: the file must be named b.json, after the worker",
            "c.json: runs must be one of actions, routine, session",
            "d.yml: a worker's file ends in .json"])


class Areas(unittest.TestCase):
    def test_a_folder_a_glob_and_a_plain_path(self):
        cases = (
            ("docs/", "docs/a.md", True), ("docs/", "docs/x/y.md", True), ("docs/", "docsx/a.md", False),
            ("src/*.py", "src/a.py", True), ("src/*.py", "src/x/a.py", False),
            ("src/**/*.py", "src/a.py", True), ("src/**/*.py", "src/x/y/a.py", True), ("src/**", "src/x", True),
            ("README.md", "README.md", True), ("README.md", "README.md.bak", False),
            ("tools", "tools/a.py", True), ("tools", "toolsmith.py", False), ("a?.md", "ab.md", True),
        )
        for pattern, path, inside in cases:
            self.assertEqual(workers.in_area((pattern,), path), inside, (pattern, path))

    def test_work_fits_an_area_only_wholly_inside_it(self):
        self.assertTrue(workers.fits((), ["anything"]))
        self.assertTrue(workers.fits(("api/",), []))
        self.assertTrue(workers.fits(("api/",), ["api/a.py", "api/b.py"]))
        self.assertFalse(workers.fits(("api/",), ["api/a.py", "web/b.js"]))


class Form(unittest.TestCase):
    BODY = ("### Name\n\n`api-night`\n\n### Where it runs\n\nactions: GitHub Actions, on this repository's Claude "
            "token\n\n### What it is for\n\nThe API's\nsmall fixes.\n\n### Area\n\napi/\n* `docs/api/`\n\n"
            "### How its pull requests land\n\nowner: the owner merges each pull request\n\n### Model\n\n"
            "claude-sonnet-5-5\n\n### Turns per run\n\n30\n\n### Rounds in a row\n\n_No response_\n\n"
            "### Claude account\n\n_No response_\n")

    def test_the_answers_become_the_settings_and_empty_ones_take_the_defaults(self):
        data = workers.from_form(self.BODY)
        self.assertEqual(data, {"name": "api-night", "runs": "actions", "about": "The API's small fixes.",
                                "area": ["api/", "docs/api/"], "lands": "owner", "model": "claude-sonnet-5-5",
                                "max_turns": 30})
        self.assertEqual(workers.from_dict(data).rounds, 6)

    def test_the_form_answers_are_read_by_their_headings(self):
        self.assertEqual(workers.form_answers("### A\r\n\r\nx\r\n### B\n\n_No response_\n"), {"A": "x", "B": ""})
        self.assertEqual(workers.form_answers(None), {})
        self.assertEqual([label for label, _ in workers.FORM][:2], ["Name", "Where it runs"])

    def test_a_number_that_is_no_number_is_left_for_the_check_to_name(self):
        data = workers.from_form("### Name\n\nx\n\n### Where it runs\n\nsession\n\n### Turns per run\n\nmany\n")
        self.assertEqual(data["max_turns"], "many")
        self.assertIn("max_turns must be a whole number", workers.problems_of(data)[0])

    def test_a_number_too_long_for_int_or_in_another_script_is_named_in_the_checks_words(self):
        # int() refuses a run of more than 4,300 digits with a ValueError,
        # which left from_form, so the "Add a worker" workflow ended in a
        # traceback where every other bad answer is one line; and int()
        # reads another script's digits, as in a Turns per run of 30
        # written in Arabic-Indic digits, as a number.
        for answer in ("1" * 4301, "1" * 10, "\u0663\u0660", "-5", "4.5"):
            body = f"### Name\n\nx\n\n### Where it runs\n\nactions\n\n### Turns per run\n\n{answer}\n"
            data = workers.from_form(body)
            self.assertEqual(data["max_turns"], answer, answer[:20])
            self.assertEqual(workers.problems_of(data), ["max_turns must be a whole number from 1 to 500"], answer[:20])
        self.assertEqual(workers.from_form("### Rounds in a row\n\n 007 \n")["rounds"], 7)


if __name__ == "__main__":
    unittest.main()
