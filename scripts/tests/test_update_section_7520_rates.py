# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_section_7520_rates.py"
FIXTURE_PATH = REPO_ROOT / "scripts" / "fixtures" / "section_7520_current_year.html"
PRIOR_YEARS_FIXTURE_PATH = (
    REPO_ROOT / "scripts" / "fixtures" / "section_7520_prior_years.html"
)
SOURCE_URL = (
    "https://www.irs.gov/businesses/small-businesses-self-employed/"
    "section-7520-interest-rates"
)
PRIOR_YEARS_SOURCE_URL = (
    "https://www.irs.gov/businesses/small-businesses-self-employed/"
    "section-7520-interest-rates-for-prior-years"
)


def load_updater_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_section_7520_rates", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("updater module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Section7520RateUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updater = load_updater_module()
        self.fixture_html = FIXTURE_PATH.read_text(encoding="utf-8")
        self.prior_years_fixture_html = PRIOR_YEARS_FIXTURE_PATH.read_text(
            encoding="utf-8"
        )

    def test_parses_current_year_table_into_integer_basis_points(self) -> None:
        records = self.updater.parse_section_7520_records(self.fixture_html, SOURCE_URL)

        self.assertEqual(3, len(records))
        self.assertEqual("2026-01", records[0].effective_month)
        self.assertEqual(457, records[0].midterm_afr_120_basis_points)
        self.assertEqual(460, records[0].section_7520_rate_basis_points)
        self.assertEqual("Rev. Rul. 2026-2", records[0].revenue_ruling)

    def test_update_writes_chronological_json_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "section-7520-rates.json"

            first_result = self.updater.update_from_html(
                html=self.fixture_html,
                source_url=SOURCE_URL,
                data_path=data_path,
                write=True,
            )
            second_result = self.updater.update_from_html(
                html=self.fixture_html,
                source_url=SOURCE_URL,
                data_path=data_path,
                write=True,
            )

            self.assertEqual((3, 0, 3, True), first_result)
            self.assertEqual((3, 3, 3, False), second_result)
            serialized = data_path.read_text(encoding="utf-8")
            self.assertIn('"effective_month": "2026-01"', serialized)
            self.assertIn('"midterm_afr_120_basis_points": 457', serialized)
            self.assertNotIn("revenue_ruling", serialized)
            self.assertNotIn("source_url", serialized)
            self.assertLess(
                serialized.find('"effective_month": "2026-01"'),
                serialized.find('"effective_month": "2026-03"'),
            )

    def test_parses_prior_years_table_with_leading_decimal_rates(self) -> None:
        records = self.updater.parse_section_7520_records(
            self.prior_years_fixture_html,
            PRIOR_YEARS_SOURCE_URL,
        )

        self.assertEqual(5, len(records))
        self.assertEqual("1999-01", records[0].effective_month)
        self.assertEqual("Rev. Rul. 99-2", records[0].revenue_ruling)
        self.assertEqual("2002-08", records[1].effective_month)
        self.assertEqual(510, records[1].midterm_afr_120_basis_points)
        self.assertEqual("2021-01", records[2].effective_month)
        self.assertEqual(62, records[2].midterm_afr_120_basis_points)
        self.assertEqual(60, records[2].section_7520_rate_basis_points)
        self.assertEqual("2025-02", records[4].effective_month)

    def test_same_published_values_from_different_source_pages_do_not_conflict(self) -> None:
        first_record = self.updater.Section7520RateRecord(
            effective_month="2025-01",
            midterm_afr_120_basis_points=510,
            section_7520_rate_basis_points=520,
            revenue_ruling="Rev. Rul. 2025-1",
            source_url=SOURCE_URL,
        )
        second_record = self.updater.Section7520RateRecord(
            effective_month="2025-01",
            midterm_afr_120_basis_points=510,
            section_7520_rate_basis_points=520,
            revenue_ruling="Rev. Rul. 2025-1",
            source_url=PRIOR_YEARS_SOURCE_URL,
        )

        merged, changed = self.updater.merge_records([first_record], [second_record])

        self.assertFalse(changed)
        self.assertEqual([first_record], merged)

    def test_legacy_long_key_json_migrates_to_compact_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "section-7520-rates.json"
            data_path.write_text(
                "["
                '{"valuation_month":"2026-01",'
                '"applicable_federal_midterm_120_percent_basis_points":457,'
                '"section_7520_rate_basis_points":460,'
                '"revenue_ruling":"Rev. Rul. 2026-2"}'
                "]",
                encoding="utf-8",
            )

            result = self.updater.update_from_html(
                html=self.fixture_html,
                source_url=SOURCE_URL,
                data_path=data_path,
                write=True,
            )

            self.assertEqual((3, 1, 3, True), result)
            serialized = data_path.read_text(encoding="utf-8")
            self.assertIn('"effective_month": "2026-01"', serialized)
            self.assertIn('"midterm_afr_120_basis_points": 457', serialized)
            self.assertNotIn("valuation_month", serialized)
            self.assertNotIn("applicable_federal_midterm_120_percent", serialized)
            self.assertNotIn("revenue_ruling", serialized)

    def test_input_html_backfill_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "section-7520-rates.json"

            with redirect_stderr(io.StringIO()):
                exit_code = self.updater.main(
                    [
                        "--input-html",
                        str(FIXTURE_PATH),
                        "--backfill",
                        "--data-path",
                        str(data_path),
                    ]
                )

        self.assertEqual(1, exit_code)

    def test_conflicting_existing_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "section-7520-rates.json"
            data_path.write_text(
                "["
                '{"valuation_month":"2026-01",'
                '"applicable_federal_midterm_120_percent_basis_points":999,'
                '"section_7520_rate_basis_points":460,'
                '"revenue_ruling":"Rev. Rul. 2026-2"'
                "}"
                "]",
                encoding="utf-8",
            )

            with self.assertRaises(self.updater.UpdateSection7520RatesError) as context:
                self.updater.update_from_html(
                    html=self.fixture_html,
                    source_url=SOURCE_URL,
                    data_path=data_path,
                    write=True,
                )

            self.assertEqual(
                self.updater.UpdateErrorCode.CONFLICTING_RECORD, context.exception.code
            )

    def test_missing_table_fails_closed(self) -> None:
        with self.assertRaises(self.updater.UpdateSection7520RatesError) as context:
            self.updater.parse_section_7520_records("<html><body>No rates</body></html>", SOURCE_URL)

        self.assertEqual(
            self.updater.UpdateErrorCode.HTML_TABLE_NOT_FOUND, context.exception.code
        )

    def test_rejects_non_irs_source_url(self) -> None:
        with self.assertRaises(self.updater.UpdateSection7520RatesError) as context:
            self.updater.parse_section_7520_records(
                self.fixture_html,
                "https://example.com/businesses/section-7520-interest-rates",
            )

        self.assertEqual(self.updater.UpdateErrorCode.BAD_URL, context.exception.code)


if __name__ == "__main__":
    unittest.main()
