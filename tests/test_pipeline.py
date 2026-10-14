"""Unit tests for the pipeline rules, on small hand-made data.

Each test pins one rule the bulletin depends on. They use tiny frames rather
than the sample export, so a failure points at the rule, not at the data.

Run: python -m unittest discover tests
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bulletin  # noqa: E402
import config as cfg  # noqa: E402


def clinical_rows(rows):
    """Build a clinical frame with every count column, defaulting to zero."""
    base = {c: 0 for c in bulletin.COUNT_COLUMNS}
    return pd.DataFrame([{**base, **r} for r in rows])


class DuplicateRule(unittest.TestCase):
    def setUp(self):
        self.df = clinical_rows([
            {"facility_id": "A", "reporting_month": "2024-01", "total_deliveries": 10},
            {"facility_id": "A", "reporting_month": "2024-01", "total_deliveries": 99},
            {"facility_id": "B", "reporting_month": "2024-01", "total_deliveries": 5},
        ])

    def test_keep_first_keeps_first_and_flags_both(self):
        out = bulletin.clean_clinical(self.df)
        a = out[out.facility_id == "A"]
        self.assertEqual(len(a), 1)
        self.assertEqual(a.total_deliveries.item(), 10)
        self.assertTrue(a.conflicting_export.item())
        self.assertFalse(out[out.facility_id == "B"].conflicting_export.item())

    def test_switching_the_rule_changes_the_kept_row_only(self):
        with mock.patch.object(cfg, "DUPLICATE_RULE", "keep_last"):
            out = bulletin.clean_clinical(self.df)
        self.assertEqual(out[out.facility_id == "A"].total_deliveries.item(), 99)
        self.assertTrue(out[out.facility_id == "A"].conflicting_export.item())

    def test_unknown_rule_stops_with_a_clear_message(self):
        with mock.patch.object(cfg, "DUPLICATE_RULE", "latest_wins"):
            with self.assertRaisesRegex(bulletin.DataError, "Unknown DUPLICATE_RULE"):
                bulletin.clean_clinical(self.df)

    def test_profile_reports_conflicts_without_changing_the_data(self):
        issues = []
        before = self.df.copy()
        bulletin.profile_clinical(self.df, issues)
        pd.testing.assert_frame_equal(before, self.df)
        dup = next(i for i in issues if i["issue"].startswith("Facility-months reported twice"))
        self.assertEqual(dup["count"], 2)
        self.assertEqual(dup["affected_ids"], "A 2024-01")


class Rates(unittest.TestCase):
    def test_zero_denominator_gives_a_gap_not_an_error_or_infinity(self):
        df = clinical_rows([{"facility_id": "A", "reporting_month": "2024-01",
                             "total_deliveries": 0, "live_births": 0}])
        df = bulletin.clean_clinical(df)
        out = bulletin.metric_neonatal_indicators(df)
        self.assertTrue(pd.isna(out.nmr_per_1000.item()))
        self.assertTrue(pd.isna(out.stillbirth_rate_per_1000.item()))

    def test_rates_come_from_summed_counts_not_averaged_rates(self):
        # Facility A: 1 death in 10 births (100 per 1,000). B: 0 in 990.
        # Pooled rate is 1 per 1,000, not the 50 an average of rates would give.
        df = clinical_rows([
            {"facility_id": "A", "reporting_month": "2024-01", "live_births": 10,
             "neonatal_deaths_0_7d": 1, "death_other": 1},
            {"facility_id": "B", "reporting_month": "2024-01", "live_births": 990},
        ])
        out = bulletin.metric_neonatal_indicators(bulletin.clean_clinical(df))
        self.assertAlmostEqual(out.nmr_per_1000.item(), 1.0)


class Coverage(unittest.TestCase):
    def _df(self, months):
        return bulletin.clean_clinical(clinical_rows(
            [{"facility_id": "A", "reporting_month": m} for m in months]))

    def test_quarter_missing_a_month_is_flagged(self):
        cov = bulletin.coverage_by_quarter(self._df(["2024-10", "2024-11"]))
        row = cov[cov.quarter == "2024Q4"].iloc[0]
        self.assertEqual((row.months_present, row.months_expected), (2, 3))
        self.assertTrue(row.low_coverage)

    def test_expected_months_follow_the_calendar(self):
        # A bulletin year that starts in March expects only one month in Q1.
        with mock.patch.object(cfg, "EXPECTED_MONTHS", ["2024-03", "2024-04", "2024-05", "2024-06"]):
            cov = bulletin.coverage_by_quarter(self._df(["2024-03"]))
        row = cov.iloc[0]
        self.assertEqual(row.months_expected, 1)
        self.assertFalse(row.low_coverage)

    def test_only_two_complete_quarters_in_a_row_are_comparable(self):
        df = self._df(["2024-01", "2024-03", "2024-04", "2024-05", "2024-06",
                       "2024-07", "2024-08", "2024-09"])
        cov = bulletin.coverage_by_quarter(df)
        trends = bulletin.metric_trends(bulletin.metric_neonatal_indicators(df), cov)
        self.assertEqual(trends.comparable.tolist(), [False, False, True])


class InputChecks(unittest.TestCase):
    """Structural problems must stop the run with a message that says what to fix."""

    def _write_sample(self, folder, drop_column=None, extra_facility=False):
        for name in bulletin.REQUIRED_COLUMNS:
            df = pd.read_csv(cfg.DATA_DIR / f"{name}.csv")
            if name == "clinical_neonatal" and drop_column:
                df = df.drop(columns=[drop_column])
            if name == "operations" and extra_facility:
                df = pd.concat([df, df.head(1).assign(facility_id="ZZZ999")])
            df.to_csv(Path(folder) / f"{name}.csv", index=False)

    def test_renamed_or_missing_column_stops_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_sample(d, drop_column="live_births")
            with self.assertRaisesRegex(bulletin.DataError, "missing required columns: live_births"):
                bulletin.load_all(Path(d))

    def test_facility_lists_that_disagree_stop_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_sample(d, extra_facility=True)
            with self.assertRaisesRegex(bulletin.DataError, "operations differs"):
                bulletin.load_all(Path(d))

    def test_missing_file_stops_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(bulletin.DataError, "Missing input file"):
                bulletin.load_all(Path(d))

    def test_percent_outside_0_to_100_stops_the_run(self):
        gov = pd.DataFrame({"facility_id": ["A"], **{c: ["50%"] for c in cfg.PERCENT_COLUMNS},
                            "protocol_last_updated": ["2024-01-01"]})
        gov.loc[0, cfg.PERCENT_COLUMNS[0]] = "140%"
        workers = pd.DataFrame({"facility_id": ["A"], "last_neonatal_training_date": ["Never"]})
        with self.assertRaisesRegex(bulletin.DataError, "outside 0 to 100"):
            bulletin.clean_reference(pd.DataFrame(), gov, workers)


class Repeatable(unittest.TestCase):
    def test_two_runs_give_identical_results(self):
        a, b = bulletin.run(verbose=False), bulletin.run(verbose=False)
        for key in ("coverage", "national", "by_province", "perf", "trends", "status"):
            pd.testing.assert_frame_equal(a[key], b[key], obj=key)
        for q in a["top_by_quarter"]:
            pd.testing.assert_frame_equal(a["top_by_quarter"][q], b["top_by_quarter"][q], obj=q)
        self.assertEqual(a["issues"], b["issues"])


if __name__ == "__main__":
    unittest.main()
