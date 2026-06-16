# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_afr_rates.py"
FIXTURE_PATH = REPO_ROOT / "scripts" / "fixtures" / "afr_2026_06.txt"
LEGACY_TABLE_2_FIXTURE_PATH = (
    REPO_ROOT / "scripts" / "fixtures" / "afr_2022_10_table2_legacy.txt"
)
LEGACY_TABLE_2_TITLE_FIXTURE_PATH = (
    REPO_ROOT / "scripts" / "fixtures" / "afr_2004_08_table2_legacy_title.txt"
)
BOXED_TABLE_FIXTURE_PATH = (
    REPO_ROOT / "scripts" / "fixtures" / "afr_2001_12_boxed_table.txt"
)
MULTIPLIER_TYPO_FIXTURE_PATH = (
    REPO_ROOT / "scripts" / "fixtures" / "afr_2001_08_multiplier_typo.txt"
)
NO_TABLE_1_MARKER_FIXTURE_PATH = (
    REPO_ROOT / "scripts" / "fixtures" / "afr_2001_05_no_table_1_marker.txt"
)
SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rr-26-11.pdf"
LEGACY_SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rr-22-18.pdf"
LEGACY_TITLE_SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rr-04-84.pdf"
BOXED_TABLE_SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rr-01-58.pdf"
MULTIPLIER_TYPO_SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rr-01-36.pdf"
NO_TABLE_1_MARKER_SOURCE_URL = "https://www.irs.gov/pub/irs-drop/rr-01-22.pdf"
PDF_BYTES = b"%PDF-1.7\nsource bytes\n%%EOF\n"


def load_updater_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_afr_rates", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("updater module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AfrRateUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updater = load_updater_module()
        self.fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")
        self.legacy_table_2_text = LEGACY_TABLE_2_FIXTURE_PATH.read_text(
            encoding="utf-8"
        )
        self.legacy_table_2_title_text = LEGACY_TABLE_2_TITLE_FIXTURE_PATH.read_text(
            encoding="utf-8"
        )
        self.boxed_table_text = BOXED_TABLE_FIXTURE_PATH.read_text(encoding="utf-8")
        self.multiplier_typo_text = MULTIPLIER_TYPO_FIXTURE_PATH.read_text(
            encoding="utf-8"
        )
        self.no_table_1_marker_text = NO_TABLE_1_MARKER_FIXTURE_PATH.read_text(
            encoding="utf-8"
        )

    def test_parses_table_1_and_table_2_into_basis_points(self) -> None:
        record = self.updater.parse_afr_record(self.fixture_text, SOURCE_URL)

        self.assertEqual("2026-06", record.effective_month)
        self.assertEqual("Rev. Rul. 2026-11", record.revenue_ruling)
        self.assertEqual(
            385,
            record.applicable_federal_rates["short_term"]["afr"]["annual"],
        )
        self.assertEqual(
            453,
            record.applicable_federal_rates["short_term"]["afr_120"]["monthly"],
        )
        self.assertEqual(
            706,
            record.applicable_federal_rates["mid_term"]["afr_175"]["monthly"],
        )
        self.assertEqual(
            570,
            record.applicable_federal_rates["long_term"]["afr_120"]["monthly"],
        )
        self.assertEqual(
            368,
            record.adjusted_applicable_federal_rates["long_term"]["annual"],
        )

    def test_parses_legacy_two_digit_revenue_ruling_with_typographic_dash(
        self,
    ) -> None:
        text = self.fixture_text.replace("Rev. Rul. 2026-11", "REV. RUL. 26–11")

        record = self.updater.parse_afr_record(text, SOURCE_URL)

        self.assertEqual("2026-06", record.effective_month)
        self.assertEqual("Rev. Rul. 26-11", record.revenue_ruling)

    def test_parses_legacy_adjusted_afr_table_layout(self) -> None:
        record = self.updater.parse_afr_record(
            self.legacy_table_2_text, LEGACY_SOURCE_URL
        )

        self.assertEqual("2022-10", record.effective_month)
        self.assertEqual(
            258,
            record.adjusted_applicable_federal_rates["short_term"]["annual"],
        )
        self.assertEqual(
            257,
            record.adjusted_applicable_federal_rates["long_term"]["quarterly"],
        )

    def test_parses_legacy_adjusted_afr_table_title(self) -> None:
        record = self.updater.parse_afr_record(
            self.legacy_table_2_title_text, LEGACY_TITLE_SOURCE_URL
        )

        self.assertEqual("2004-08", record.effective_month)
        self.assertEqual(
            171,
            record.adjusted_applicable_federal_rates["short_term"]["annual"],
        )
        self.assertEqual(
            455,
            record.adjusted_applicable_federal_rates["long_term"]["monthly"],
        )

    def test_parses_boxed_legacy_table(self) -> None:
        record = self.updater.parse_afr_record(
            self.boxed_table_text, BOXED_TABLE_SOURCE_URL
        )

        self.assertEqual("2001-12", record.effective_month)
        self.assertEqual(
            248,
            record.applicable_federal_rates["short_term"]["afr"]["annual"],
        )
        self.assertEqual(
            592,
            record.applicable_federal_rates["long_term"]["afr_120"]["monthly"],
        )

    def test_parses_legacy_multiplier_marker_typo(self) -> None:
        record = self.updater.parse_afr_record(
            self.multiplier_typo_text, MULTIPLIER_TYPO_SOURCE_URL
        )

        self.assertEqual("2001-08", record.effective_month)
        self.assertEqual(
            746,
            record.applicable_federal_rates["long_term"]["afr_130"]["annual"],
        )

    def test_parses_legacy_table_without_literal_table_1_marker(self) -> None:
        record = self.updater.parse_afr_record(
            self.no_table_1_marker_text, NO_TABLE_1_MARKER_SOURCE_URL
        )

        self.assertEqual("2001-05", record.effective_month)
        self.assertEqual(
            425,
            record.applicable_federal_rates["short_term"]["afr"]["annual"],
        )
        self.assertEqual(
            687,
            record.applicable_federal_rates["long_term"]["afr_130"]["monthly"],
        )
        self.assertEqual(
            489,
            record.adjusted_applicable_federal_rates["long_term"]["annual"],
        )

    def test_effective_month_comes_from_table_1_not_prior_references(self) -> None:
        text = (
            "Rev. Rul. 2002-61\n"
            "This ruling references Applicable Federal Rates (AFR) for October 2001.\n"
            f"{self.fixture_text.replace('Rev. Rul. 2026-11', '')}"
        )

        record = self.updater.parse_afr_record(text, SOURCE_URL)

        self.assertEqual("2026-06", record.effective_month)

    def test_effective_month_uses_table_2_when_table_1_has_irs_year_typo(self) -> None:
        text = self.fixture_text.replace(
            "Applicable Federal Rates (AFR) for June 2026",
            "Applicable Federal Rates (AFR) for June 2025",
        ).replace(
            "Adjusted AFR for June 2026",
            "Adjusted AFR for June 2026",
        )

        record = self.updater.parse_afr_record(text, SOURCE_URL)

        self.assertEqual("2026-06", record.effective_month)

    def test_update_writes_manifest_and_year_shard_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset_dir = Path(directory) / "afr"

            first_result = self.updater.update_from_pdf_texts(
                [(SOURCE_URL, self.fixture_text)], dataset_dir, True
            )
            second_result = self.updater.update_from_pdf_texts(
                [(SOURCE_URL, self.fixture_text)], dataset_dir, True
            )

            self.assertEqual((1, 0, 1, True), first_result)
            self.assertEqual((1, 1, 1, False), second_result)
            manifest = (dataset_dir / "manifest.json").read_text(encoding="utf-8")
            metadata = (dataset_dir / "metadata.json").read_text(encoding="utf-8")
            serialized = (dataset_dir / "by-year" / "2026-afr.json").read_text(
                encoding="utf-8"
            )
            self.assertIn('"record_storage": "by_year"', manifest)
            self.assertIn('"primary_records": "by-year/YYYY-afr.json"', metadata)
            self.assertIn('"effective_month": "2026-06"', serialized)
            self.assertIn('"afr_175"', serialized)
            self.assertFalse((dataset_dir / "rates.json").exists())

    def test_loads_written_year_shards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset_dir = Path(directory) / "afr"

            self.updater.update_from_pdf_texts(
                [(SOURCE_URL, self.fixture_text)], dataset_dir, True
            )
            records = self.updater.load_existing_records(dataset_dir)

            self.assertEqual(1, len(records))
            self.assertEqual("2026-06", records[0].effective_month)

    def test_update_creates_missing_folder_for_new_year(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset_dir = Path(directory) / "missing" / "afr"

            result = self.updater.update_from_pdf_texts(
                [(SOURCE_URL, self.fixture_text)], dataset_dir, True
            )

            self.assertEqual((1, 0, 1, True), result)
            self.assertTrue((dataset_dir / "manifest.json").is_file())
            self.assertTrue((dataset_dir / "by-year" / "2026-afr.json").is_file())

    def test_update_from_index_archives_monthly_revenue_ruling_once(self) -> None:
        store = sys.modules["afr_update.store"]
        original_discover_pdf_urls = store.discover_pdf_urls
        original_fetch_pdf_bytes = store.fetch_pdf_bytes
        original_extract_pdf_text = store.extract_pdf_text
        try:
            store.discover_pdf_urls = lambda index_url, backfill: [SOURCE_URL]
            store.fetch_pdf_bytes = lambda source_url: PDF_BYTES
            store.extract_pdf_text = lambda pdf_bytes: self.fixture_text

            with tempfile.TemporaryDirectory() as directory:
                dataset_dir = Path(directory) / "afr"
                archive_dir = Path(directory) / "sources" / "irs-revenue-rulings"

                first_result = self.updater.update_from_index(
                    index_url="https://www.irs.gov/applicable-federal-rates",
                    dataset_dir=dataset_dir,
                    write=True,
                    backfill=False,
                    source_archive_dir=archive_dir,
                    archive_sources=True,
                )
                second_result = self.updater.update_from_index(
                    index_url="https://www.irs.gov/applicable-federal-rates",
                    dataset_dir=dataset_dir,
                    write=True,
                    backfill=False,
                    source_archive_dir=archive_dir,
                    archive_sources=True,
                )

                manifest = json.loads(
                    (archive_dir / "manifest.json").read_text(encoding="utf-8")
                )

            self.assertEqual((1, 0, 1, True), first_result)
            self.assertEqual((0, 1, 1, False), second_result)
            self.assertEqual(1, len(manifest["entries"]))
            entry = manifest["entries"][0]
            self.assertEqual(["2026-06"], entry["periods"])
            self.assertEqual(["afr", "section-7520-rates"], entry["subjects"])
            self.assertEqual(SOURCE_URL, entry["source_url"])
            self.assertEqual("Rev. Rul. 2026-11", entry["title"])
        finally:
            store.discover_pdf_urls = original_discover_pdf_urls
            store.fetch_pdf_bytes = original_fetch_pdf_bytes
            store.extract_pdf_text = original_extract_pdf_text

    def test_update_from_index_reports_archive_only_change(self) -> None:
        store = sys.modules["afr_update.store"]
        original_discover_pdf_urls = store.discover_pdf_urls
        original_fetch_pdf_bytes = store.fetch_pdf_bytes
        original_extract_pdf_text = store.extract_pdf_text
        try:
            store.discover_pdf_urls = lambda index_url, backfill: [SOURCE_URL]
            store.fetch_pdf_bytes = lambda source_url: PDF_BYTES
            store.extract_pdf_text = lambda pdf_bytes: self.fixture_text

            with tempfile.TemporaryDirectory() as directory:
                dataset_dir = Path(directory) / "afr"
                archive_dir = Path(directory) / "sources" / "irs-revenue-rulings"
                self.updater.update_from_pdf_texts(
                    [(SOURCE_URL, self.fixture_text)], dataset_dir, True
                )

                result = self.updater.update_from_index(
                    index_url="https://www.irs.gov/applicable-federal-rates",
                    dataset_dir=dataset_dir,
                    write=True,
                    backfill=False,
                    source_archive_dir=archive_dir,
                    archive_sources=True,
                )

                manifest = json.loads(
                    (archive_dir / "manifest.json").read_text(encoding="utf-8")
                )

            self.assertEqual((1, 1, 1, True), result)
            self.assertEqual(1, len(manifest["entries"]))
        finally:
            store.discover_pdf_urls = original_discover_pdf_urls
            store.fetch_pdf_bytes = original_fetch_pdf_bytes
            store.extract_pdf_text = original_extract_pdf_text

    def test_conflicting_record_fails_closed(self) -> None:
        good_record = self.updater.parse_afr_record(self.fixture_text, SOURCE_URL)
        bad_record = self.updater.AfrRateRecord(
            effective_month=good_record.effective_month,
            revenue_ruling=good_record.revenue_ruling,
            source_url=good_record.source_url,
            applicable_federal_rates={
                **good_record.applicable_federal_rates,
                "short_term": {
                    **good_record.applicable_federal_rates["short_term"],
                    "afr": {
                        **good_record.applicable_federal_rates["short_term"]["afr"],
                        "annual": 999,
                    },
                },
            },
            adjusted_applicable_federal_rates=good_record.adjusted_applicable_federal_rates,
        )

        with self.assertRaises(self.updater.AfrUpdateError) as context:
            self.updater.merge_records([good_record], [bad_record])

        self.assertEqual(
            self.updater.AfrUpdateErrorCode.CONFLICTING_RECORD,
            context.exception.code,
        )

    def test_rejects_non_irs_pdf_url(self) -> None:
        with self.assertRaises(self.updater.AfrUpdateError) as context:
            self.updater.parse_afr_record(
                self.fixture_text,
                "https://example.com/pub/irs-drop/rr-26-11.pdf",
            )

        self.assertEqual(self.updater.AfrUpdateErrorCode.BAD_PDF_URL, context.exception.code)

    def test_normalizes_irs_double_encoded_space_pdf_url(self) -> None:
        self.assertEqual(
            "https://www.irs.gov/pub/irs-drop/rr%20-13-18.pdf",
            self.updater.normalize_irs_pdf_url(
                "https://www.irs.gov/pub/irs-drop/rr%2520-13-18.pdf"
            ),
        )


if __name__ == "__main__":
    unittest.main()
