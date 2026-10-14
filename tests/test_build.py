"""Integration checks on the real sample: the figures the site publishes agree
with the pipeline and with each other.

These guard against formatting bugs (a quarter dropped, a NaN reaching the
browser, a total that no longer adds up) rather than against wrong rules,
which test_pipeline.py covers on small hand-made data.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bulletin  # noqa: E402
import build_site  # noqa: E402


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = bulletin.run(verbose=False)
        cls.d = build_site.dashboard_data(cls.r)

    def test_quarters_line_up(self):
        q = self.d["meta"]["quarters"]
        self.assertEqual(q, [c["quarter"] for c in self.d["coverage"]])
        self.assertEqual(q, [n["quarter"] for n in self.d["national"]])
        self.assertEqual(set(q), set(self.d["top"]))

    def test_provinces_add_up_to_national(self):
        for n in self.d["national"]:
            prov = [p for p in self.d["by_province"] if p["quarter"] == n["quarter"]]
            self.assertEqual(sum(p["deliveries"] for p in prov), n["deliveries"])

    def test_coverage_flags(self):
        for c in self.d["coverage"]:
            self.assertEqual(c["low_coverage"], c["months_present"] < 3)
        self.assertTrue(self.d["coverage"][-1]["low_coverage"])  # Q4 has two months

    def test_rates_in_range(self):
        for n in self.d["national"]:
            self.assertTrue(0 < n["stillbirth_rate_per_1000"] < 1000)
            self.assertTrue(0 < n["nmr_per_1000"] < 1000)
            causes = sum(n[k] for k in n if k.startswith("cause_"))
            self.assertAlmostEqual(causes, 100, delta=0.6)

    def test_top_ten_sorted(self):
        for rows in self.d["top"].values():
            self.assertLessEqual(len(rows), 10)
            vals = [r["deliveries"] for r in rows]
            self.assertEqual(vals, sorted(vals, reverse=True))

    def test_status_covers_every_facility(self):
        self.assertEqual(len(self.d["status"]["rows"]), self.d["meta"]["facilities"])
        self.assertEqual(sum(self.d["status"]["counts"].values()), self.d["meta"]["facilities"])

    def test_no_nan_in_json(self):
        text = json.dumps(self.d, allow_nan=False)  # raises if a NaN slipped through
        self.assertIn("2024Q4", text)

    def test_pdf_link_matches_file_name(self):
        self.assertTrue(self.d["meta"]["pdf"].endswith(build_site.pdf_name(self.r)))


if __name__ == "__main__":
    unittest.main()
