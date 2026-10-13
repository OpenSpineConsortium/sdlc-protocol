"""The standard-library property lane (validation/properties.py, DESIGN.md
9.2 and 9.3): every property holds over the enumerated domain, the
preconditions leave inputs to check, the Unicode classes are what the
design counted, the unproven list names only what is in the package, and
the whole lane runs in about a second."""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation import domains, properties  # noqa: E402

BUDGET_SECONDS = 15


class TheDomain(unittest.TestCase):
    def test_the_unicode_classes_are_enumerated_whole(self):
        digits, decimals = domains.unicode_classes()
        self.assertGreaterEqual(len(digits), 128)
        self.assertGreaterEqual(len(decimals), 670)
        self.assertTrue(all(c.isdigit() and not c.isdecimal() for c in digits))
        self.assertTrue(all(c.isdecimal() and not c.isascii() for c in decimals))
        self.assertIn("\u00b2", digits)
        self.assertIn("\u0663", decimals)

    def test_the_names_hold_every_short_ascii_string_and_the_classes(self):
        names = domains.names()
        self.assertIn("", names)
        self.assertIn("00", names)
        self.assertIn("+\x00", names)
        self.assertIn("\u00b20", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(set(domains.short_names()) <= set(names))

    def test_the_label_sets_carry_the_three_spellings(self):
        sets = list(domains.label_sets())
        self.assertEqual(len(sets), 1024)
        self.assertIn(["todo", "worker:a", "account:a", "agent:a"], sets)


class TheLane(unittest.TestCase):
    def test_every_property_holds_and_checks_something_in_the_budget(self):
        began = time.perf_counter()
        outcomes = properties.run_lane()
        self.assertLess(time.perf_counter() - began, BUDGET_SECONDS)
        self.assertGreaterEqual(len(outcomes), 20)
        for out in outcomes:
            with self.subTest(property=out.name):
                self.assertEqual(out.failures, [], out.failures[:3])
                self.assertGreater(out.checked, 0, "the preconditions left nothing to check")
        self.assertEqual(sorted(o.name for o in outcomes), sorted(f.__name__ for f in properties.properties()))

    def test_a_precondition_failure_is_filtered_and_a_property_failure_is_not(self):
        def needs_a_short_key(key: str) -> None:
            assert len(key) <= 1
            found = key.upper()
            assert found != "X"

        out = properties.check(needs_a_short_key, [("",), ("ab",), ("x",)])
        self.assertEqual((out.checked, out.filtered, len(out.failures)), (1, 1, 1))
        self.assertEqual(out.failures[0][0], ("x",))

    def test_a_property_written_as_asserts_alone_is_refused(self):
        def all_preconditions(key: str) -> None:
            assert len(key) <= 1
            assert key != "x"

        with self.assertRaisesRegex(ValueError, "all preconditions"):
            properties.check(all_preconditions, [("",)])

    def test_a_failure_on_the_property_line_is_reported_not_filtered(self):
        from unittest import mock

        from sdlc_protocol import board
        with mock.patch.object(board, "state_word", lambda state: "bogus" if state == "x" else "clean"):
            out = properties.check(properties.state_word_is_one_of_five, [(s,) for s in properties.STATES])
        self.assertEqual((out.filtered, len(out.failures)), (0, 1))

    def test_a_property_that_raises_has_failed(self):
        def raises(key: str) -> None:
            assert len(key) <= 1
            int(key)

        out = properties.check(raises, [("1",), ("a",)])
        self.assertEqual((out.checked, len(out.failures)), (1, 1))

    def test_the_unproven_list_names_what_is_in_the_package_and_nothing_else(self):
        entries = properties.unproven()
        self.assertEqual([e["name"] for e in entries],
                         ["adapters/", "cli.py", "guard.branches", "guard.git_line", "board.write_if_changed"])
        self.assertTrue(all(e["reason"] for e in entries))
        self.assertEqual(properties.stale_unproven(), [])
        self.assertEqual(properties.stale_unproven([{"name": "guard.no_such", "reason": "x"},
                                                    {"name": "gone/", "reason": "x"}]), ["guard.no_such", "gone/"])

    def test_the_lane_bites_on_a_rule_changed(self):
        from unittest import mock

        from sdlc_protocol import claims
        with mock.patch.object(claims, "number_in", lambda key: int(key) if str(key).isdigit() else None):
            out = properties.check(properties.a_claim_branch_names_one_issue, [("05",), ("\u0663",)])
        self.assertEqual(len(out.failures), 2)


if __name__ == "__main__":
    unittest.main()
