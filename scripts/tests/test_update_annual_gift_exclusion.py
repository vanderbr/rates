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
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_annual_gift_exclusion.py"
FIXTURE_PATH = REPO_ROOT / "scripts" / "fixtures" / "annual_gift_exclusion_2026.txt"
SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"


def load_updater_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "update_annual_gift_exclusion", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("updater module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnnualGiftExclusionUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updater = load_updater_module()
        self.fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_parses_annual_exclusion_from_revenue_procedure_text(self) -> None:
        record = self.updater.parse_annual_gift_exclusion_record(
            self.fixture_text, SOURCE_URL
        )

        self.assertEqual(2026, record.year)
        self.assertEqual(19_000, record.annual_exclusion_amount_usd)
        self.assertEqual("Rev. Proc. 2025-32", record.revenue_procedure)
        self.assertEqual(
            "gifts_of_present_interests_made_during_calendar_year",
            record.applies_to,
        )

    def test_update_writes_chronological_json_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "rates.json"

            first_result = self.updater.update_from_source_texts(
                [(SOURCE_URL, self.fixture_text)],
                data_path,
                write=True,
            )
            second_result = self.updater.update_from_source_texts(
                [(SOURCE_URL, self.fixture_text)],
                data_path,
                write=True,
            )

            self.assertEqual((1, 0, 1, True), first_result)
            self.assertEqual((1, 1, 1, False), second_result)
            serialized = data_path.read_text(encoding="utf-8")
            self.assertIn('"period_start_date": "2026-01-01"', serialized)
            self.assertIn('"period_end_date": "2026-12-31"', serialized)
            self.assertIn('"annual_exclusion_amount_usd": 19000', serialized)
            self.assertNotIn("revenue_procedure", serialized)
            self.assertNotIn("source_url", serialized)

    def test_backfill_writes_static_history_without_per_record_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "rates.json"

            result = self.updater.update_from_urls(
                (),
                data_path,
                write=True,
                include_static_history=True,
            )

            self.assertEqual((72, 0, 72, True), result)
            records = self.updater.load_existing_records(data_path)
            self.assertEqual(1955, records[0].year)
            self.assertEqual(3_000, records[0].annual_exclusion_amount_usd)
            self.assertEqual(1981, records[26].year)
            self.assertEqual(3_000, records[26].annual_exclusion_amount_usd)
            self.assertEqual(1982, records[27].year)
            self.assertEqual(10_000, records[27].annual_exclusion_amount_usd)
            self.assertEqual(2026, records[-1].year)
            self.assertEqual(19_000, records[-1].annual_exclusion_amount_usd)
            serialized = data_path.read_text(encoding="utf-8")
            self.assertNotIn("revenue_procedure", serialized)
            self.assertNotIn("source_url", serialized)

    def test_conflicting_existing_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "rates.json"
            data_path.write_text(
                "["
                '{"year":2026,'
                '"annual_exclusion_amount_usd":18000,'
                '"applies_to":"gifts_of_present_interests_made_during_calendar_year",'
                '"revenue_procedure":"Rev. Proc. 2025-32"'
                "}"
                "]",
                encoding="utf-8",
            )

            with self.assertRaises(self.updater.UpdateAnnualGiftExclusionError) as context:
                self.updater.update_from_source_texts(
                    [(SOURCE_URL, self.fixture_text)],
                    data_path,
                    write=True,
                )

            self.assertEqual(
                self.updater.UpdateErrorCode.CONFLICTING_RECORD,
                context.exception.code,
            )

    def test_annual_update_target_year_tracks_publication_window(self) -> None:
        self.assertIsNone(
            self.updater.annual_update_target_year(
                self.updater.dt.date(2026, 10, 31)
            )
        )
        self.assertEqual(
            2027,
            self.updater.annual_update_target_year(
                self.updater.dt.date(2026, 11, 1)
            ),
        )
        self.assertEqual(
            2027,
            self.updater.annual_update_target_year(
                self.updater.dt.date(2027, 1, 31)
            ),
        )
        self.assertIsNone(
            self.updater.annual_update_target_year(
                self.updater.dt.date(2027, 2, 1)
            )
        )

    def test_source_urls_defer_outside_window_and_after_target_exists(self) -> None:
        outside_window_urls = self.updater.source_urls_for_run(
            False,
            self.updater.dt.date(2026, 6, 15),
            existing_records=[],
        )

        existing_target_urls = self.updater.source_urls_for_run(
            False,
            self.updater.dt.date(2026, 11, 15),
            existing_records=[
                self.updater.AnnualGiftExclusionRecord(
                    year=2027,
                    annual_exclusion_amount_usd=20_000,
                    applies_to=self.updater.APPLIES_TO,
                    revenue_procedure=None,
                    source_url=self.updater.HISTORICAL_SOURCE_URL,
                )
            ],
        )

        self.assertEqual((), outside_window_urls)
        self.assertEqual((), existing_target_urls)

    def test_rejects_non_irs_source_url(self) -> None:
        with self.assertRaises(self.updater.UpdateAnnualGiftExclusionError) as context:
            self.updater.parse_annual_gift_exclusion_record(
                self.fixture_text, "https://example.com/pub/irs-drop/rp-25-32.pdf"
            )

        self.assertEqual(self.updater.UpdateErrorCode.BAD_URL, context.exception.code)

    def test_missing_amount_fails_closed(self) -> None:
        with self.assertRaises(self.updater.UpdateAnnualGiftExclusionError) as context:
            self.updater.parse_annual_gift_exclusion_record(
                "Rev. Proc. 2025-32", SOURCE_URL
            )

        self.assertEqual(
            self.updater.UpdateErrorCode.INVALID_AMOUNT, context.exception.code
        )

    def test_input_text_backfill_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "rates.json"

            with redirect_stderr(io.StringIO()):
                exit_code = self.updater.main(
                    [
                        "--input-text",
                        str(FIXTURE_PATH),
                        "--backfill",
                        "--data-path",
                        str(data_path),
                    ]
                )

        self.assertEqual(1, exit_code)

    def test_discovers_revenue_procedure_pdf_links_from_news_html(self) -> None:
        html = (
            '<a href="/pub/irs-drop/rp-25-32.pdf">Revenue Procedure</a>'
            '<a href="https://www.irs.gov/pub/irs-drop/rp-25-32.pdf">Duplicate</a>'
            '<a href="/pub/irs-drop/not-a-revenue-procedure.pdf">Ignore</a>'
        )

        urls = self.updater.discover_pdf_urls_from_news_html(html)

        self.assertEqual(["https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"], urls)


if __name__ == "__main__":
    unittest.main()
