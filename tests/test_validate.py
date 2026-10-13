"""scripts/validate.py runs each lane by hand and says what it found."""

import io
import sys
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
        self.assertEqual(validate.main(["replay", str(ROOT / "validation" / "traces" / "F7.json")], out), 0)
        self.assertIn("red: step 1 G1, step 2 G1", out.getvalue())
        self.assertIn("lease-onto b 1", out.getvalue())
        self.assertEqual(validate.main(["replay"], out), 2)


if __name__ == "__main__":
    unittest.main()
