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
        for lane in ("scenarios", "deal", "properties", "guard"):
            with self.subTest(lane=lane):
                out = io.StringIO()
                self.assertEqual(validate.main([lane], out), 0)
                self.assertIn(f"{lane}: ok in", out.getvalue())


if __name__ == "__main__":
    unittest.main()
