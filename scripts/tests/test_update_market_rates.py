# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_market_rates.py"
TREASURY_2026_SOURCE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/2026/all?field_tdr_date_value=2026&"
    "type=daily_treasury_yield_curve&page=&_format=csv"
)
TREASURY_1990_SOURCE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/1990/all?field_tdr_date_value=1990&"
    "type=daily_treasury_yield_curve&page=&_format=csv"
)
FED_FUNDS_SOURCE_URL = (
    "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.csv?"
    "startDate=2000-07-03&endDate=2026-06-12"
)
SOFR_SOURCE_URL = (
    "https://markets.newyorkfed.org/api/rates/all/search.csv?"
    "startDate=2018-04-02&endDate=2026-06-12"
)
NYFED_HEADER = (
    "Effective Date,Rate Type,Rate (%),1st Percentile (%),25th Percentile (%),"
    "75th Percentile (%),99th Percentile (%),Volume ($Billions),"
    "Target Rate From (%),Target Rate To (%),Intra Day - Low (%),"
    "Intra Day - High (%),Standard Deviation (%),30-Day Average SOFR,"
    "90-Day Average SOFR,180-Day Average SOFR,SOFR Index,"
    "Revision Indicator (Y/N),Footnote ID\n"
)
TREASURY_2026_CSV = (
    'Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr",'
    '"3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
    "06/12/2026,3.69,3.70,3.70,3.78,3.79,3.82,3.86,4.09,4.12,4.21,4.34,4.48,4.98,4.97\n"
    "06/11/2026,3.69,3.69,3.70,3.78,3.79,3.81,3.85,4.05,4.09,4.18,4.31,4.45,4.96,4.95\n"
    "06/10/2026,3.69,3.70,3.72,3.79,3.80,3.82,3.90,4.13,4.17,4.27,4.40,4.55,,5.03\n"
)
TREASURY_1990_CSV = (
    'Date,"3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","30 Yr"\n'
    "12/31/1990,6.63,6.73,6.82,7.15,7.40,7.68,8.00,8.08,8.26\n"
    "12/28/1990,6.64,6.85,6.91,7.25,7.48,7.78,8.08,8.14,8.31\n"
)
FED_FUNDS_CSV = (
    NYFED_HEADER
    +
    "07/05/2000,EFFR,6.52,,,,,,6.50,,2.00,6.94,0.50,,,,,,\n"
    "07/03/2000,EFFR,7.03,,,,,,6.50,,5.50,7.50,0.28,,,,,,\n"
    "07/04/2000,EFFR,,,,,,,,,,,,,,,,,\n"
)
SOFR_CSV = (
    NYFED_HEADER
    +
    "04/03/2018,SOFRAI,,,,,,,,,,,,1.81000,1.80500,1.80250,1.00012345,,\n"
    +
    "04/03/2018,SOFR,1.83,1.62,1.81,1.91,2.00,825,,,,,,,,,,,\n"
    "04/02/2018,SOFRAI,,,,,,,,,,,,1.80000,1.80000,1.80000,1.00000000,,\n"
    "04/02/2018,SOFR,1.80,1.25,1.77,1.89,2.25,849,,,,,,,,,,,\n"
)
SOFR_AVERAGE_INDEX_ONLY_CSV = (
    NYFED_HEADER
    +
    "06/15/2026,SOFRAI,,,,,,,,,,,,3.60136,3.63561,3.67923,1.24721652,,\n"
)


def load_updater_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_market_rates", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("updater module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MarketRateUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updater = load_updater_module()

    def test_parses_current_treasury_yield_curve_into_basis_points(self) -> None:
        records = self.updater.parse_treasury_csv(
            TREASURY_2026_CSV, TREASURY_2026_SOURCE_URL
        )

        self.assertEqual(3, len(records))
        self.assertEqual("2026-06-10", records[0].date)
        self.assertEqual(369, records[0].par_yields_basis_points["1_month"])
        self.assertEqual(370, records[0].par_yields_basis_points["6_week"])
        self.assertEqual(455, records[0].par_yields_basis_points["10_year"])
        self.assertIsNone(records[0].par_yields_basis_points["20_year"])
        self.assertEqual(503, records[0].par_yields_basis_points["30_year"])

    def test_parses_legacy_treasury_headers_with_missing_newer_tenors(self) -> None:
        records = self.updater.parse_treasury_csv(
            TREASURY_1990_CSV, TREASURY_1990_SOURCE_URL
        )

        self.assertEqual(2, len(records))
        self.assertEqual("1990-12-28", records[0].date)
        self.assertIsNone(records[0].par_yields_basis_points["1_month"])
        self.assertIsNone(records[0].par_yields_basis_points["20_year"])
        self.assertEqual(664, records[0].par_yields_basis_points["3_month"])
        self.assertEqual(831, records[0].par_yields_basis_points["30_year"])

    def test_treasury_update_writes_manifest_and_year_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "treasury" / "treasury-yield-curve" / "manifest.json"
            year_dir = root / "treasury" / "treasury-yield-curve" / "by-year"

            first_result = self.updater.update_treasury_from_csv_texts(
                [(TREASURY_2026_SOURCE_URL, TREASURY_2026_CSV)],
                manifest_path,
                year_dir,
                True,
            )
            second_result = self.updater.update_treasury_from_csv_texts(
                [(TREASURY_2026_SOURCE_URL, TREASURY_2026_CSV)],
                manifest_path,
                year_dir,
                True,
            )

            self.assertEqual((3, 0, 3, True), first_result)
            self.assertEqual((3, 3, 3, False), second_result)
            self.assertIn('"record_storage": "by_year"', manifest_path.read_text(encoding="utf-8"))
            self.assertFalse((manifest_path.parent / "rates.json").exists())
            self.assertIn(
                '"date": "2026-06-12"',
                (year_dir / "2026-treasury-yield-curve.json").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertIn(
                '"par_yields_basis_points": [',
                (year_dir / "2026-treasury-yield-curve.json").read_text(
                    encoding="utf-8"
                ),
            )

    def test_parses_nyfed_effr_and_missing_observation(self) -> None:
        records = self.updater.parse_nyfed_csv(
            FED_FUNDS_CSV,
            FED_FUNDS_SOURCE_URL,
            self.updater.NYFED_DATASET_BY_ID["federal-funds"],
        )

        self.assertEqual(3, len(records))
        self.assertEqual("2000-07-03", records[0].date)
        self.assertEqual("EFFR", records[0].rate_type)
        self.assertEqual(703, records[0].rate_basis_points)
        self.assertIsNone(records[1].rate_basis_points)

    def test_parses_sofr_from_secured_endpoint(self) -> None:
        records = self.updater.parse_nyfed_csv(
            SOFR_CSV,
            SOFR_SOURCE_URL,
            self.updater.NYFED_DATASET_BY_ID["sofr"],
        )

        self.assertEqual(2, len(records))
        self.assertEqual("2018-04-02", records[0].date)
        self.assertEqual("SOFR", records[0].rate_type)
        self.assertEqual(180, records[0].rate_basis_points)
        self.assertEqual(125, records[0].percentile_1_basis_points)
        self.assertEqual(177, records[0].percentile_25_basis_points)
        self.assertEqual(189, records[0].percentile_75_basis_points)
        self.assertEqual(225, records[0].percentile_99_basis_points)
        self.assertEqual(849, records[0].volume_billions)
        self.assertEqual(180000, records[0].average_30_day_basis_points_scaled_1000)
        self.assertEqual(180000, records[0].average_90_day_basis_points_scaled_1000)
        self.assertEqual(180000, records[0].average_180_day_basis_points_scaled_1000)
        self.assertEqual(100000000, records[0].sofr_index_scaled_100000000)

    def test_sofr_update_writes_complete_canonical_and_derived_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = self.updater.NyFedRateDataset(
                dataset_id="sofr",
                path_slug=f"{directory}/sofr",
                rate_type="SOFR",
                api_group="secured",
                api_name="sofr",
                start_date=self.updater.date(2018, 4, 2),
                year_sharded=True,
                api_path="/api/rates/all/search.csv",
            )

            result = self.updater.update_nyfed_from_csv_text(
                SOFR_CSV,
                SOFR_SOURCE_URL,
                dataset,
                True,
            )

            self.assertEqual((2, 0, 2, True), result)
            canonical_json = (dataset.year_dir / "2018-sofr.json").read_text(
                encoding="utf-8"
            )
            self.assertIn('"volume_billions": 849', canonical_json)
            self.assertIn('"percentile_1_basis_points": 125', canonical_json)
            self.assertNotIn("percentiles_basis_points", canonical_json)
            self.assertNotIn("rate_type", canonical_json)
            self.assertNotIn("average_30_day_basis_points_scaled_1000", canonical_json)
            self.assertNotIn("sofr_index_scaled_100000000", canonical_json)
            derived_path = (
                Path(directory)
                / "sofr"
                / "sofr-30d-average"
                / "by-year"
                / "2018-sofr-30d-average.json"
            )
            self.assertIn(
                '"average_30_day_basis_points_scaled_1000": 180000',
                derived_path.read_text(encoding="utf-8"),
            )
            index_path = (
                Path(directory)
                / "sofr"
                / "sofr-index"
                / "by-year"
                / "2018-sofr-index.json"
            )
            self.assertIn(
                '"sofr_index_scaled_100000000": 100000000',
                index_path.read_text(encoding="utf-8"),
            )

    def test_sofr_average_index_only_row_does_not_create_sofr_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = self.updater.NyFedRateDataset(
                dataset_id="sofr",
                path_slug=f"{directory}/sofr",
                rate_type="SOFR",
                api_group="secured",
                api_name="sofr",
                start_date=self.updater.date(2018, 4, 2),
                year_sharded=True,
                api_path="/api/rates/all/search.csv",
            )

            result = self.updater.update_nyfed_from_csv_text(
                SOFR_AVERAGE_INDEX_ONLY_CSV,
                SOFR_SOURCE_URL,
                dataset,
                True,
            )

            self.assertEqual((1, 0, 0, True), result)
            self.assertFalse((dataset.year_dir / "2026-sofr.json").exists())
            average_path = (
                Path(directory)
                / "sofr"
                / "sofr-30d-average"
                / "by-year"
                / "2026-sofr-30d-average.json"
            )
            index_path = (
                Path(directory)
                / "sofr"
                / "sofr-index"
                / "by-year"
                / "2026-sofr-index.json"
            )
            self.assertIn(
                '"average_30_day_basis_points_scaled_1000": 360136',
                average_path.read_text(encoding="utf-8"),
            )
            self.assertIn(
                '"sofr_index_scaled_100000000": 124721652',
                index_path.read_text(encoding="utf-8"),
            )

    def test_sofr_update_reads_current_observation_only_json_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = self.updater.NyFedRateDataset(
                dataset_id="sofr",
                path_slug=f"{directory}/sofr",
                rate_type="SOFR",
                api_group="secured",
                api_name="sofr",
                start_date=self.updater.date(2018, 4, 2),
                year_sharded=True,
                api_path="/api/rates/all/search.csv",
            )
            dataset.year_dir.mkdir(parents=True)
            (dataset.year_dir / "2018-sofr.json").write_text(
                (
                    "[\n"
                    "  {\n"
                    '    "date": "2018-04-02",\n'
                    '    "rate_basis_points": 180,\n'
                    '    "percentile_1_basis_points": 125,\n'
                    '    "percentile_25_basis_points": 177,\n'
                    '    "percentile_75_basis_points": 189,\n'
                    '    "percentile_99_basis_points": 225,\n'
                    '    "volume_billions": 849\n'
                    "  }\n"
                    "]\n"
                ),
                encoding="utf-8",
            )

            result = self.updater.update_nyfed_from_csv_text(
                SOFR_CSV,
                SOFR_SOURCE_URL,
                dataset,
                True,
            )

            self.assertEqual((2, 1, 2, True), result)
            canonical_json = (dataset.year_dir / "2018-sofr.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("average_30_day_basis_points_scaled_1000", canonical_json)
            self.assertNotIn("sofr_index_scaled_100000000", canonical_json)

    def test_sofr_merge_enriches_publication_lag_record(self) -> None:
        partial = self.updater.NyFedReferenceRateRecord(
            date="2026-06-15",
            rate_type="SOFR",
            rate_basis_points=None,
            source_url=SOFR_SOURCE_URL,
            average_30_day_basis_points_scaled_1000=360136,
            average_90_day_basis_points_scaled_1000=363561,
            average_180_day_basis_points_scaled_1000=367923,
            sofr_index_scaled_100000000=124721652,
        )
        complete = self.updater.NyFedReferenceRateRecord(
            date="2026-06-15",
            rate_type="SOFR",
            rate_basis_points=365,
            source_url=SOFR_SOURCE_URL,
            percentile_1_basis_points=359,
            percentile_25_basis_points=363,
            percentile_75_basis_points=370,
            percentile_99_basis_points=374,
            volume_billions=3059,
            average_30_day_basis_points_scaled_1000=360136,
            average_90_day_basis_points_scaled_1000=363561,
            average_180_day_basis_points_scaled_1000=367923,
            sofr_index_scaled_100000000=124721652,
        )

        merged, changed = self.updater.merge_nyfed_records([partial], [complete])

        self.assertTrue(changed)
        self.assertEqual([complete], merged)

    def test_nyfed_update_writes_json_canonical_and_year_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = self.updater.NyFedRateDataset(
                dataset_id="federal-funds",
                path_slug=f"{directory}/fed-funds",
                rate_type="EFFR",
                api_group="unsecured",
                api_name="effr",
                start_date=self.updater.date(2000, 7, 3),
                year_sharded=True,
            )

            result = self.updater.update_nyfed_from_csv_text(
                FED_FUNDS_CSV,
                FED_FUNDS_SOURCE_URL,
                dataset,
                True,
            )

            self.assertEqual((3, 0, 3, True), result)
            self.assertIn(
                '"record_storage": "by_year"',
                dataset.manifest_path.read_text(encoding="utf-8"),
            )
            self.assertFalse(dataset.legacy_data_path.exists())
            self.assertIn(
                '"date": "2000-07-05"',
                (dataset.year_dir / "2000-fed-funds.json").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertNotIn(
                "rate_type",
                (dataset.year_dir / "2000-fed-funds.json").read_text(
                    encoding="utf-8"
                ),
            )

    def test_non_backfill_treasury_update_fetches_prior_year_boundary(self) -> None:
        args = self.updater.argparse.Namespace(
            start_year=1990,
            end_year=2026,
            backfill=False,
        )

        self.assertEqual([2025, 2026], self.updater.treasury_years_for_args(args))

    def test_conflicting_treasury_record_fails_closed(self) -> None:
        good_record = self.updater.parse_treasury_csv(
            TREASURY_2026_CSV, TREASURY_2026_SOURCE_URL
        )[0]
        bad_rates = dict(good_record.par_yields_basis_points)
        bad_rates["10_year"] = 999
        bad_record = self.updater.TreasuryYieldCurveRecord(
            date=good_record.date,
            par_yields_basis_points=bad_rates,
            source_url=good_record.source_url,
        )

        with self.assertRaises(self.updater.MarketRateUpdateError) as context:
            self.updater.merge_treasury_records([good_record], [bad_record])

        self.assertEqual(
            self.updater.MarketRateUpdateErrorCode.CONFLICTING_RECORD,
            context.exception.code,
        )

    def test_duplicate_existing_json_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shard_path = (
                Path(directory) / "fed-funds" / "by-year" / "2000-fed-funds.json"
            )
            shard_path.parent.mkdir(parents=True)
            shard_path.write_text(
                (
                    "[\n"
                    "  {\n"
                    '    "date": "2000-07-03",\n'
                    '    "rate_basis_points": 703\n'
                    "  },\n"
                    "  {\n"
                    '    "date": "2000-07-03",\n'
                    '    "rate_basis_points": 703\n'
                    "  }\n"
                    "]\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaises(self.updater.MarketRateUpdateError) as context:
                self.updater.update_nyfed_from_csv_text(
                    FED_FUNDS_CSV,
                    FED_FUNDS_SOURCE_URL,
                    self.updater.NyFedRateDataset(
                        dataset_id="federal-funds",
                        path_slug=str(shard_path.parents[1]),
                        rate_type="EFFR",
                        api_group="unsecured",
                        api_name="effr",
                        start_date=self.updater.date(2000, 7, 3),
                        year_sharded=True,
                    ),
                    True,
                )

            self.assertEqual(
                self.updater.MarketRateUpdateErrorCode.DUPLICATE_JSON_RECORD,
                context.exception.code,
            )

    def test_duplicate_source_date_fails_closed(self) -> None:
        duplicate_csv = (
            'Date,"3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","30 Yr"\n'
            "12/31/1990,6.63,6.73,6.82,7.15,7.40,7.68,8.00,8.08,8.26\n"
            "12/31/1990,6.63,6.73,6.82,7.15,7.40,7.68,8.00,8.08,8.26\n"
        )

        with self.assertRaises(self.updater.MarketRateUpdateError) as context:
            self.updater.parse_treasury_csv(duplicate_csv, TREASURY_1990_SOURCE_URL)

        self.assertEqual(
            self.updater.MarketRateUpdateErrorCode.DUPLICATE_SOURCE_RECORD,
            context.exception.code,
        )

    def test_rejects_non_treasury_source_url(self) -> None:
        with self.assertRaises(self.updater.MarketRateUpdateError) as context:
            self.updater.parse_treasury_csv(
                TREASURY_2026_CSV,
                "https://example.com/daily-treasury-rates.csv/2026/all",
            )

        self.assertEqual(
            self.updater.MarketRateUpdateErrorCode.BAD_SOURCE_URL,
            context.exception.code,
        )

    def test_rejects_wrong_nyfed_rate_type_for_dataset(self) -> None:
        with self.assertRaises(self.updater.MarketRateUpdateError) as context:
            self.updater.parse_nyfed_csv(
                FED_FUNDS_CSV,
                FED_FUNDS_SOURCE_URL,
                self.updater.NYFED_DATASET_BY_ID["sofr"],
            )

        self.assertEqual(
            self.updater.MarketRateUpdateErrorCode.BAD_SOURCE_URL,
            context.exception.code,
        )

    def test_rejects_unexpected_nyfed_header(self) -> None:
        with self.assertRaises(self.updater.MarketRateUpdateError) as context:
            self.updater.parse_nyfed_csv(
                "observation_date,DFF\n1954-07-01,1.13\n",
                FED_FUNDS_SOURCE_URL,
                self.updater.NYFED_DATASET_BY_ID["federal-funds"],
            )

        self.assertEqual(
            self.updater.MarketRateUpdateErrorCode.INVALID_CSV,
            context.exception.code,
        )


if __name__ == "__main__":
    unittest.main()
