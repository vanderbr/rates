# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import hashlib
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_FILE_NAMES = {".DS_Store"}
GENERATED_DIR_NAMES = {"__pycache__"}
SINGLE_FILE_DATASETS = {
    "actuarial/life-expectancy-by-age": {
        "life-expectancy-by-age.json",
        "manifest.json",
        "metadata.json",
    },
    "actuarial/mortality-table-2000cm": {
        "manifest.json",
        "metadata.json",
        "mortality-table-2000cm.json",
    },
    "actuarial/mortality-table-2010cm": {
        "manifest.json",
        "metadata.json",
        "mortality-table-2010cm.json",
    },
    "annual-gift-exclusion": {
        "annual-gift-exclusion.json",
        "manifest.json",
        "metadata.json",
    },
    "estate-gift-tax-exemption": {
        "estate-gift-tax-exemption.json",
        "manifest.json",
        "metadata.json",
    },
    "gst-exemption": {"gst-exemption.json", "manifest.json", "metadata.json"},
    "noncitizen-spouse-gift-exclusion": {
        "manifest.json",
        "metadata.json",
        "noncitizen-spouse-gift-exclusion.json",
    },
}
INTEREST_RATE_SHARDED_DATASETS = {
    "actuarial/table-b",
    "actuarial/table-d",
    "actuarial/table-h",
    "actuarial/table-r2",
    "actuarial/table-s",
    "actuarial/table-u1",
    "actuarial/table-u2",
    "actuarial/table-z",
}
YEAR_SHARDED_DATASETS = {
    "7520",
    "afr",
    "fed-funds",
    "treasury/treasury-yield-curve",
    "sofr",
    "sofr/sofr-30d-average",
    "sofr/sofr-90d-average",
    "sofr/sofr-180d-average",
}
DATASET_METADATA_FILES = {
    "7520": "metadata.json",
    "afr": "metadata.json",
    "fed-funds": "metadata.json",
    "treasury/treasury-yield-curve": "metadata.json",
    "sofr": "metadata.json",
    "sofr/sofr-30d-average": "metadata.json",
    "sofr/sofr-90d-average": "metadata.json",
    "sofr/sofr-180d-average": "metadata.json",
}
YEAR_SHARDED_MANIFEST = "manifest.json"
DISALLOWED_LEGACY_PATHS = {
    "federal-funds",
    "irs",
    "market",
    "section-7520",
    "table-2001",
}
ANNUAL_IRS_DATASETS = {
    "annual-gift-exclusion",
    "estate-gift-tax-exemption",
    "gst-exemption",
    "noncitizen-spouse-gift-exclusion",
}
TREASURY_SOURCE_URL_TEMPLATE = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/daily-treasury-rates.csv/{year}/all?"
    "field_tdr_date_value={year}&type=daily_treasury_yield_curve&page=&"
    "_format=csv"
)
DISALLOWED_SOURCE_PATTERNS = (
    re.compile(
        r"https://home\.treasury\.gov/resource-center/data-chart-center/"
        r"interest-rates(?!/)"
    ),
    re.compile(r"https://uscode\.house\.gov/"),
    re.compile(r"https://www\.irs\.gov/instructions/i709"),
    re.compile(r"https://www\.newyorkfed\.org/markets/reference-rates/sofr"),
)


class RepositoryLayoutTests(unittest.TestCase):
    def test_expected_dataset_directories_exist(self) -> None:
        for dataset_path in SINGLE_FILE_DATASETS:
            self.assertTrue((REPO_ROOT / dataset_path).is_dir(), dataset_path)
        for dataset_path in INTEREST_RATE_SHARDED_DATASETS:
            self.assertTrue((REPO_ROOT / dataset_path).is_dir(), dataset_path)
            self.assertTrue(
                (REPO_ROOT / dataset_path / "by-interest-rate").is_dir(),
                dataset_path,
            )
            self.assertTrue((REPO_ROOT / dataset_path / "protobuf").is_dir(), dataset_path)
            self.assertTrue((REPO_ROOT / dataset_path / "manifest.json").is_file(), dataset_path)
            self.assertTrue((REPO_ROOT / dataset_path / "metadata.json").is_file(), dataset_path)
            self.assertFalse((REPO_ROOT / dataset_path / "rates.json").exists(), dataset_path)
        for dataset_path in YEAR_SHARDED_DATASETS:
            self.assertTrue((REPO_ROOT / dataset_path).is_dir(), dataset_path)
            self.assertTrue((REPO_ROOT / dataset_path / "by-year").is_dir(), dataset_path)
            self.assertTrue((REPO_ROOT / dataset_path / "protobuf").is_dir(), dataset_path)
            self.assertTrue(
                (REPO_ROOT / dataset_path / YEAR_SHARDED_MANIFEST).is_file(),
                dataset_path,
            )
            self.assertFalse((REPO_ROOT / dataset_path / "rates.json").exists(), dataset_path)
            metadata_file = DATASET_METADATA_FILES.get(dataset_path)
            if metadata_file is not None:
                self.assertTrue(
                    (REPO_ROOT / dataset_path / metadata_file).is_file(),
                    dataset_path,
                )

    def test_year_sharded_dataset_roots_only_contain_contract_files(self) -> None:
        for dataset_path in INTEREST_RATE_SHARDED_DATASETS:
            dataset_dir = REPO_ROOT / dataset_path
            self.assertEqual(
                {"by-interest-rate", "manifest.json", "metadata.json", "protobuf"},
                {path.name for path in dataset_dir.iterdir()},
                dataset_path,
            )
        for dataset_path in YEAR_SHARDED_DATASETS:
            dataset_dir = REPO_ROOT / dataset_path
            expected_entries = {"by-year", YEAR_SHARDED_MANIFEST, "protobuf"}
            metadata_file = DATASET_METADATA_FILES.get(dataset_path)
            if metadata_file is not None:
                expected_entries.add(metadata_file)
            for child_dataset_path in YEAR_SHARDED_DATASETS:
                child_parent = str(Path(child_dataset_path).parent)
                if child_parent == dataset_path:
                    expected_entries.add(Path(child_dataset_path).name)
            actual_entries = {
                path.name
                for path in dataset_dir.iterdir()
                if path.name not in GENERATED_FILE_NAMES
            }
            self.assertEqual(expected_entries, actual_entries, dataset_path)

    def test_generated_local_files_are_not_present(self) -> None:
        for path in REPO_ROOT.rglob("*"):
            if ".git" in path.parts:
                continue
            if path.name in GENERATED_FILE_NAMES:
                raise AssertionError(str(path))
            if path.is_dir():
                self.assertNotIn(path.name, GENERATED_DIR_NAMES, str(path))

    def test_no_legacy_dataset_directories_remain(self) -> None:
        for dataset_path in DISALLOWED_LEGACY_PATHS:
            self.assertFalse((REPO_ROOT / dataset_path).exists(), dataset_path)

    def test_annual_irs_metadata_uses_revenue_procedure_sources(self) -> None:
        for dataset_path in ANNUAL_IRS_DATASETS:
            metadata = self.load_json_object(REPO_ROOT / dataset_path / "metadata.json")
            applies_to = metadata.get("applies_to")
            if not isinstance(applies_to, str) or applies_to == "":
                raise AssertionError(dataset_path)
            source_urls = metadata.get("source_urls")
            if not isinstance(source_urls, list) or len(source_urls) == 0:
                raise AssertionError(dataset_path)
            for source_url in source_urls:
                if not isinstance(source_url, str):
                    raise AssertionError(dataset_path)
                self.assertTrue(
                    source_url.startswith("https://www.irs.gov/pub/irs-drop/rp-"),
                    dataset_path,
                )
                self.assertTrue(source_url.endswith(".pdf"), dataset_path)

    def test_published_shape_contract_is_versioned_per_dataset(self) -> None:
        index = self.load_json_object(REPO_ROOT / "index.json")
        datasets = index.get("datasets")
        if not isinstance(datasets, list):
            raise AssertionError("index.json")

        for dataset in datasets:
            if not isinstance(dataset, dict):
                raise AssertionError("index.json")
            dataset_path = dataset.get("dataset_path")
            dataset_id = dataset.get("dataset_id")
            manifest_path = dataset.get("manifest_path")
            if (
                not isinstance(dataset_path, str)
                or not isinstance(dataset_id, str)
                or not isinstance(manifest_path, str)
            ):
                raise AssertionError("index.json")

            manifest = self.load_json_object(REPO_ROOT / manifest_path)
            metadata = self.load_json_object(REPO_ROOT / dataset_path / "metadata.json")
            schema_path = REPO_ROOT / "schemas" / "v1" / f"{dataset_id}.schema.json"

            self.assertEqual(dataset_id, manifest.get("dataset_id"), dataset_path)
            self.assertEqual(dataset_id, metadata.get("dataset_id"), dataset_path)
            self.assertEqual(1, manifest.get("schema_version"), dataset_path)
            self.assertEqual(1, metadata.get("schema_version"), dataset_path)
            schema_id = manifest.get("schema_id")
            self.assertEqual(schema_id, metadata.get("schema_id"), dataset_path)
            if not isinstance(schema_id, str):
                raise AssertionError(dataset_path)
            self.assertTrue(schema_id.startswith("rates."), dataset_path)
            self.assertTrue(schema_id.endswith(".v1"), dataset_path)
            self.assertTrue(schema_path.is_file(), dataset_path)
            proto = manifest.get("proto")
            if not isinstance(proto, dict):
                raise AssertionError(dataset_path)
            proto_file = proto.get("file")
            if not isinstance(proto_file, str):
                raise AssertionError(dataset_path)
            self.assertTrue(proto_file.startswith("proto/rates/v1/"), dataset_path)

    def test_treasury_metadata_uses_csv_source_template(self) -> None:
        metadata = self.load_json_object(
            REPO_ROOT / "treasury" / "treasury-yield-curve" / "metadata.json"
        )

        self.assertNotIn("source_url", metadata)
        self.assertEqual(
            TREASURY_SOURCE_URL_TEMPLATE,
            metadata.get("source_url_template"),
        )

    def test_public_source_citations_do_not_use_context_pages(self) -> None:
        paths = [REPO_ROOT / "README.md"]
        paths.extend((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        paths.extend(REPO_ROOT.glob("*/metadata.json"))
        paths.extend(REPO_ROOT.glob("*/*/metadata.json"))

        for path in paths:
            text = path.read_text(encoding="utf-8")
            for pattern in DISALLOWED_SOURCE_PATTERNS:
                self.assertIsNone(pattern.search(text), str(path))

    def test_single_file_datasets_only_contain_expected_json_files(self) -> None:
        for dataset_path, expected_files in SINGLE_FILE_DATASETS.items():
            actual_files = {
                path.name for path in (REPO_ROOT / dataset_path).iterdir() if path.is_file()
            }
            self.assertEqual(expected_files, actual_files, dataset_path)
            manifest = self.load_json_object(REPO_ROOT / dataset_path / "manifest.json")
            self.assertEqual("single_file", manifest.get("record_storage"), dataset_path)
            self.assert_proto_reference(manifest, dataset_path)
            records_entry = manifest.get("records")
            if not isinstance(records_entry, dict):
                raise AssertionError(dataset_path)
            records_path = records_entry.get("path")
            if not isinstance(records_path, str):
                raise AssertionError(dataset_path)
            self.assertNotEqual("rates.json", records_path, dataset_path)
            self.assert_records_do_not_have_source_url(
                self.load_json_list(REPO_ROOT / dataset_path / records_path),
                dataset_path,
            )
            if dataset_path in ANNUAL_IRS_DATASETS:
                self.assert_records_do_not_have_applies_to(
                    self.load_json_list(REPO_ROOT / dataset_path / records_path),
                    dataset_path,
                )
            self.assert_artifact_entry(REPO_ROOT / dataset_path, records_entry, dataset_path)
            protobuf_path = records_entry.get("protobuf_path")
            if not isinstance(protobuf_path, str):
                raise AssertionError(dataset_path)
            self.assertEqual(
                {Path(protobuf_path).name},
                {
                    path.name
                    for path in (REPO_ROOT / dataset_path / "protobuf").glob("*.pb")
                },
                dataset_path,
            )

    def test_year_shards_match_canonical_json_records(self) -> None:
        for dataset_path in YEAR_SHARDED_DATASETS:
            dataset_dir = REPO_ROOT / dataset_path
            manifest = self.load_json_object(dataset_dir / YEAR_SHARDED_MANIFEST)
            self.assert_proto_reference(manifest, dataset_path)
            manifest_years = manifest.get("years")
            if not isinstance(manifest_years, list):
                raise AssertionError(dataset_path)

            canonical_records: list[object] = []
            expected_by_year: dict[str, list[object]] = {}
            for year_entry in manifest_years:
                if not isinstance(year_entry, dict):
                    raise AssertionError(dataset_path)
                year = year_entry.get("year")
                if not isinstance(year, str):
                    raise AssertionError(dataset_path)
                shard_path = year_entry.get("path")
                expected_filename = self.year_shard_filename(dataset_path, year)
                if shard_path != f"by-year/{expected_filename}":
                    raise AssertionError(dataset_path)
                self.assert_artifact_entry(dataset_dir, year_entry, dataset_path)
                year_records = self.load_json_list(dataset_dir / str(shard_path))
                self.assert_records_do_not_have_source_url(
                    year_records, f"{dataset_path}/{year}"
                )
                self.assert_sorted_unique_dates(year_records, f"{dataset_path}/{year}")
                expected_by_year[year] = year_records
                canonical_records.extend(year_records)

            self.assert_sorted_unique_dates(canonical_records, dataset_path)

            actual_year_files = sorted(path.name for path in (dataset_dir / "by-year").glob("*.json"))
            self.assertEqual(
                [
                    self.year_shard_filename(dataset_path, year)
                    for year in sorted(expected_by_year.keys())
                ],
                actual_year_files,
                dataset_path,
            )
            for year, expected_records in expected_by_year.items():
                actual_records = self.load_json_list(
                    dataset_dir / "by-year" / self.year_shard_filename(dataset_path, year)
                )
                self.assert_records_do_not_have_source_url(
                    actual_records, f"{dataset_path}/{year}"
                )
                self.assert_sorted_unique_dates(actual_records, f"{dataset_path}/{year}")
                self.assertEqual(expected_records, actual_records, f"{dataset_path}/{year}")

            expected_protobuf_files = sorted(
                Path(str(year_entry.get("protobuf_path"))).name
                for year_entry in manifest_years
                if isinstance(year_entry, dict)
            )
            actual_protobuf_files = sorted(
                path.name for path in (dataset_dir / "protobuf").glob("*.pb")
            )
            self.assertEqual(expected_protobuf_files, actual_protobuf_files, dataset_path)
            self.assertEqual(len(canonical_records), manifest.get("record_count"), dataset_path)
            if canonical_records:
                first_record = canonical_records[0]
                last_record = canonical_records[-1]
                if not isinstance(first_record, dict) or not isinstance(last_record, dict):
                    raise AssertionError(dataset_path)
                first_key = self.record_sort_key(first_record)
                last_key = self.record_sort_key(last_record)
                self.assert_manifest_boundary(manifest, first_key, "first", dataset_path)
                self.assert_manifest_boundary(manifest, last_key, "last", dataset_path)

    def test_interest_rate_shards_match_manifests(self) -> None:
        for dataset_path in INTEREST_RATE_SHARDED_DATASETS:
            dataset_dir = REPO_ROOT / dataset_path
            manifest = self.load_json_object(dataset_dir / "manifest.json")
            self.assert_proto_reference(manifest, dataset_path)
            shards = manifest.get("shards")
            rate_field = manifest.get("rate_field")
            if not isinstance(shards, list) or not isinstance(rate_field, str):
                raise AssertionError(dataset_path)

            expected_files: list[str] = []
            record_count = 0
            for shard in shards:
                if not isinstance(shard, dict):
                    raise AssertionError(dataset_path)
                rate_basis_points = shard.get(rate_field)
                path = shard.get("path")
                shard_count = shard.get("record_count")
                if not isinstance(rate_basis_points, int) or not isinstance(path, str):
                    raise AssertionError(dataset_path)
                if not isinstance(shard_count, int):
                    raise AssertionError(dataset_path)
                expected_path = f"by-interest-rate/{rate_basis_points:05d}-basis-points.json"
                self.assertEqual(expected_path, path, dataset_path)
                self.assert_artifact_entry(dataset_dir, shard, dataset_path)
                expected_files.append(Path(path).name)

                records = self.load_json_list(dataset_dir / path)
                self.assert_records_do_not_have_source_url(records, f"{dataset_path}/{path}")
                self.assertEqual(shard_count, len(records), f"{dataset_path}/{path}")
                record_count += len(records)
                for record in records:
                    if not isinstance(record, dict):
                        raise AssertionError(dataset_path)
                    if rate_field in record:
                        self.assertEqual(
                            rate_basis_points,
                            record.get(rate_field),
                            dataset_path,
                        )

            self.assertEqual(record_count, manifest.get("record_count"), dataset_path)
            actual_files = sorted(
                path.name for path in (dataset_dir / "by-interest-rate").glob("*.json")
            )
            self.assertEqual(sorted(expected_files), actual_files, dataset_path)
            expected_protobuf_files = sorted(
                Path(str(shard.get("protobuf_path"))).name
                for shard in shards
                if isinstance(shard, dict)
            )
            actual_protobuf_files = sorted(
                path.name for path in (dataset_dir / "protobuf").glob("*.pb")
            )
            self.assertEqual(expected_protobuf_files, actual_protobuf_files, dataset_path)

    def load_json_list(self, path: Path) -> list[object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise AssertionError(str(path))
        return value

    def load_json_object(self, path: Path) -> dict[str, object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AssertionError(str(path))
        return value

    def assert_sorted_unique_dates(self, records: list[object], label: str) -> None:
        dates: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                raise AssertionError(label)
            record_date = self.record_sort_key(record)
            if record_date is None:
                raise AssertionError(label)
            dates.append(record_date)
        self.assertEqual(sorted(dates), dates, label)
        self.assertEqual(len(set(dates)), len(dates), label)

    def record_sort_key(self, record: dict[object, object]) -> str | None:
        date = record.get("date")
        if isinstance(date, str):
            return date
        effective_month = record.get("effective_month")
        if isinstance(effective_month, str):
            return effective_month
        return None

    def assert_records_do_not_have_source_url(
        self, records: list[object], label: str
    ) -> None:
        for record in records:
            if not isinstance(record, dict):
                raise AssertionError(label)
            self.assertNotIn("source_url", record, label)

    def assert_records_do_not_have_applies_to(
        self, records: list[object], label: str
    ) -> None:
        for record in records:
            if not isinstance(record, dict):
                raise AssertionError(label)
            self.assertNotIn("applies_to", record, label)

    def assert_artifact_entry(
        self, dataset_dir: Path, entry: dict[object, object], label: str
    ) -> None:
        path = entry.get("path")
        protobuf_path = entry.get("protobuf_path")
        if not isinstance(path, str) or not isinstance(protobuf_path, str):
            raise AssertionError(label)
        json_path = dataset_dir / path
        pb_path = dataset_dir / protobuf_path
        self.assertTrue(json_path.is_file(), label)
        self.assertTrue(pb_path.is_file(), label)
        self.assertEqual(json_path.stat().st_size, entry.get("bytes"), label)
        self.assertEqual(pb_path.stat().st_size, entry.get("protobuf_bytes"), label)
        self.assertEqual(self.sha256(json_path), entry.get("sha256"), label)
        self.assertEqual(self.sha256(pb_path), entry.get("protobuf_sha256"), label)

    def sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def assert_proto_reference(self, manifest: dict[str, object], label: str) -> None:
        proto = manifest.get("proto")
        if not isinstance(proto, dict):
            raise AssertionError(label)
        proto_file = proto.get("file")
        proto_message = proto.get("message")
        if not isinstance(proto_file, str) or not isinstance(proto_message, str):
            raise AssertionError(label)
        self.assertTrue((REPO_ROOT / proto_file).is_file(), label)
        self.assertTrue(proto_message.startswith("rates.v1."), label)

    def year_shard_filename(self, dataset_path: str, year: str) -> str:
        if dataset_path == "7520":
            return f"{year}-section-7520-rates.json"
        return f"{year}-{Path(dataset_path).name}.json"

    def assert_manifest_boundary(
        self,
        manifest: dict[str, object],
        expected: str | None,
        boundary: str,
        dataset_path: str,
    ) -> None:
        date_key = f"{boundary}_date"
        month_key = f"{boundary}_effective_month"
        actual = manifest.get(date_key)
        if actual is None:
            actual = manifest.get(month_key)
        self.assertEqual(expected, actual, dataset_path)


if __name__ == "__main__":
    unittest.main()
