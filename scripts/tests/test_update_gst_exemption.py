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
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_gst_exemption.py"
FIXTURE_PATH = REPO_ROOT / "scripts" / "fixtures" / "gst_exemption_2026.txt"
SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"


def load_updater_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_gst_exemption", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("updater module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GstExemptionUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updater = load_updater_module()
        self.fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_parses_gst_exemption_from_revenue_procedure_text(self) -> None:
        record = self.updater.parse_gst_exemption_record(
            self.fixture_text, SOURCE_URL
        )

        self.assertEqual("2026-01-01", record.period_start_date)
        self.assertEqual("2026-12-31", record.period_end_date)
        self.assertEqual(15_000_000, record.exemption_amount_usd)
        self.assertEqual("Rev. Proc. 2025-32", record.revenue_procedure)
        self.assertEqual(
            "generation_skipping_transfers_during_calendar_year",
            record.applies_to,
        )

    def test_static_form_709_history_expands_grouped_years(self) -> None:
        records = self.updater.static_form_709_records()
        by_year = {record.period_start_date[:4]: record for record in records}

        self.assertEqual(1_010_000, by_year["1999"].exemption_amount_usd)
        self.assertEqual(1_500_000, by_year["2004"].exemption_amount_usd)
        self.assertEqual(1_500_000, by_year["2005"].exemption_amount_usd)
        self.assertEqual(13_990_000, by_year["2025"].exemption_amount_usd)

    def test_update_writes_chronological_json_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "gst-exemption.json"

            first_result = self.updater.update_from_source_texts(
                [(SOURCE_URL, self.fixture_text)],
                data_path,
                write=True,
                include_static_history=True,
            )
            second_result = self.updater.update_from_source_texts(
                [(SOURCE_URL, self.fixture_text)],
                data_path,
                write=True,
                include_static_history=True,
            )

            self.assertEqual((28, 0, 28, True), first_result)
            self.assertEqual((28, 28, 28, False), second_result)
            serialized = data_path.read_text(encoding="utf-8")
            self.assertIn('"period_start_date": "1999-01-01"', serialized)
            self.assertIn('"period_end_date": "2026-12-31"', serialized)
            self.assertIn('"exemption_amount_usd": 15000000', serialized)
            self.assertNotIn('"revenue_procedure"', serialized)
            self.assertNotIn('"source_url"', serialized)
            self.assertNotIn('"applies_to"', serialized)

    def test_legacy_year_json_migrates_to_period_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "gst-exemption.json"
            data_path.write_text(
                (
                    "["
                    '{"year":1999,'
                    '"exemption_amount_usd":1010000,'
                    '"applies_to":"generation_skipping_transfers_during_calendar_year",'
                    '"revenue_procedure":null}'
                    "]"
                ),
                encoding="utf-8",
            )

            result = self.updater.update_from_source_texts(
                [],
                data_path,
                write=True,
                include_static_history=True,
            )

            self.assertEqual((27, 1, 27, True), result)
            serialized = data_path.read_text(encoding="utf-8")
            self.assertIn('"period_start_date": "1999-01-01"', serialized)
            self.assertNotIn('"year"', serialized)
            self.assertNotIn('"revenue_procedure"', serialized)
            self.assertNotIn('"applies_to"', serialized)

    def test_empty_publication_poll_noops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "gst-exemption.json"

            result = self.updater.update_from_source_texts(
                [],
                data_path,
                write=True,
                include_static_history=False,
                target_year=2027,
            )

            self.assertEqual((0, 0, 0, False), result)
            self.assertFalse(data_path.exists())

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
                self.updater.GstExemptionRecord(
                    period_start_date="2027-01-01",
                    period_end_date="2027-12-31",
                    exemption_amount_usd=15_000_000,
                    applies_to=self.updater.APPLIES_TO,
                    revenue_procedure=None,
                    source_url="",
                )
            ],
        )

        self.assertEqual((), outside_window_urls)
        self.assertEqual((), existing_target_urls)

    def test_publication_poll_uses_irs_newsroom_revenue_procedure_discovery(
        self,
    ) -> None:
        observed_urls: list[str] = []

        def fetch_newsroom_html(source_url: str) -> str | None:
            observed_urls.append(source_url)
            return '<a href="/pub/irs-drop/rp-25-32.pdf">Revenue Procedure</a>'

        original_fetch = self.updater.irs_sources.fetch_news_html_if_available
        self.updater.irs_sources.fetch_news_html_if_available = fetch_newsroom_html
        try:
            urls = self.updater.source_urls_for_run(
                False,
                self.updater.dt.date(2026, 11, 1),
                existing_records=[],
            )
        finally:
            self.updater.irs_sources.fetch_news_html_if_available = original_fetch

        self.assertEqual(("https://www.irs.gov/pub/irs-drop/rp-25-32.pdf",), urls)
        self.assertTrue(
            all("/newsroom/" in source_url for source_url in observed_urls)
        )

    def test_conflicting_existing_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "gst-exemption.json"
            data_path.write_text(
                "["
                '{"year":2026,'
                '"exemption_amount_usd":13990000,'
                '"applies_to":"generation_skipping_transfers_during_calendar_year",'
                '"revenue_procedure":"Rev. Proc. 2025-32"'
                "}"
                "]",
                encoding="utf-8",
            )

            with self.assertRaises(self.updater.UpdateGstExemptionError) as context:
                self.updater.update_from_source_texts(
                    [(SOURCE_URL, self.fixture_text)],
                    data_path,
                    write=True,
                    include_static_history=False,
                )

            self.assertEqual(
                self.updater.UpdateErrorCode.CONFLICTING_RECORD,
                context.exception.code,
            )

    def test_missing_amount_fails_closed(self) -> None:
        with self.assertRaises(self.updater.UpdateGstExemptionError) as context:
            self.updater.parse_gst_exemption_record("Rev. Proc. 2025-32", SOURCE_URL)

        self.assertEqual(
            self.updater.UpdateErrorCode.INVALID_AMOUNT, context.exception.code
        )

    def test_input_text_backfill_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "gst-exemption.json"

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


if __name__ == "__main__":
    unittest.main()
