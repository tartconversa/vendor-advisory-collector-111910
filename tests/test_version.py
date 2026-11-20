"""版本区间判断与去重规则的单元测试。"""
import unittest

from collector.version import Version, in_affected_range, is_excluded


class VersionRangeTest(unittest.TestCase):
    def test_boundaries_introduced_inclusive_fixed_exclusive(self):
        self.assertTrue(in_affected_range("1.2.0", "1.2.0", "1.4.0"))
        self.assertFalse(in_affected_range("1.4.0", "1.2.0", "1.4.0"))
        self.assertTrue(in_affected_range("1.3.9", "1.2.0", "1.4.0"))
        self.assertFalse(in_affected_range("1.1.9", "1.2.0", "1.4.0"))

    def test_open_ended_ranges(self):
        self.assertTrue(in_affected_range("99.0.0", "1.0.0", None))
        self.assertFalse(in_affected_range("0.9.0", "1.0.0", None))
        self.assertTrue(in_affected_range("0.1.0", None, "1.0.0"))
        self.assertFalse(in_affected_range("1.0.0", None, "1.0.0"))

    def test_exclusion_is_explicit_version_match_only(self):
        self.assertTrue(is_excluded("1.3.2", ["1.3.2"]))
        self.assertFalse(is_excluded("1.3.3", ["1.3.2"]))
        self.assertFalse(is_excluded("1.3.2", []))
        # 排除优先于区间：先判排除，再判区间
        self.assertTrue(in_affected_range("1.3.2", "1.2.0", "1.4.0"))
        self.assertTrue(is_excluded("1.3.2", ["1.3.2"]))

    def test_numeric_component_ordering(self):
        self.assertLess(Version.parse("1.9.0"), Version.parse("1.10.0"))
        self.assertEqual(Version.parse("2.0.1"), Version.parse("2.0.1"))


if __name__ == "__main__":
    unittest.main()
