"""scripts/validate.py runs each lane by hand and says what it found."""

import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402


class TheScript(unittest.TestCase):
    def test_it_names_its_lanes(self):
        out = io.StringIO()
        self.assertEqual(validate.main([], out), 2)
        self.assertIn("scripts/validate.py fast", out.getvalue())
        self.assertEqual(validate.main(["nothing"], out), 2)

    def test_the_cheap_lanes_run_and_say_ok(self):
        for lane in ("scenarios", "traces", "deal", "properties", "guard"):
            with self.subTest(lane=lane):
                out = io.StringIO()
                self.assertEqual(validate.main([lane], out), 0)
                self.assertIn(f"{lane}: ok in", out.getvalue())

    def test_replay_takes_one_trace_file_and_prints_its_timeline(self):
        out = io.StringIO()
        self.assertEqual(validate.main(["replay", str(ROOT / "validation" / "traces" / "F4.json")], out), 0)
        self.assertIn("red: step 9 G14", out.getvalue())
        self.assertIn("deal", out.getvalue())
        self.assertEqual(validate.main(["replay", str(ROOT / "validation" / "traces" / "F7.json")], out), 0)
        self.assertIn("red: none", out.getvalue())
        self.assertIn("lease-onto b 1: refused by the guard", out.getvalue())
        self.assertEqual(validate.main(["replay"], out), 2)

    def test_replay_plays_a_trace_over_git_too(self):
        out = io.StringIO()
        self.assertEqual(validate.main(["replay", str(ROOT / "validation" / "traces" / "F7.json"), "--world", "git"], out), 0)
        self.assertIn("red: none", out.getvalue())
        self.assertIn("lease-onto b 1: refused by the guard", out.getvalue())
        with self.assertRaises(SystemExit):
            validate.main(["replay", str(ROOT / "validation" / "traces" / "F7.json"), "--world", "paper"], out)

    def test_deep_runs_quick_into_a_folder_of_its_own(self):
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            self.assertEqual(validate.main(["deep", "--quick", "--only", "search", "--jobs", "2", "--out", folder], out), 0)
            self.assertTrue((Path(folder) / "deep.json").exists())
        self.assertIn("deep lane: ok", out.getvalue())

    def test_an_option_with_no_value_is_refused(self):
        """--jobs with nothing after it would be read as True, which is 1."""
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(SystemExit) as refused:
            validate.main(["deep", "--quick", "--only", "search", "--out", folder, "--jobs"], io.StringIO())
        self.assertIn("--jobs takes a value", str(refused.exception))

    def test_deep_refuses_a_part_only_does_not_name(self):
        """A misspelt part would run nothing and say ok."""
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(SystemExit) as refused:
            validate.main(["deep", "--quick", "--only", "searches", "--out", folder], io.StringIO())
        self.assertIn("--only takes search, soak, gate, git, guard, conformance, not searches", str(refused.exception))
        self.assertIn("python3 scripts/validate.py deep", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
