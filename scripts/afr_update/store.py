# SPDX-License-Identifier: Apache-2.0

"""JSON validation and merge logic for local AFR rate history."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

try:
    import archive_irs_pdf_source as irs_archive
except ModuleNotFoundError:
    from scripts import archive_irs_pdf_source as irs_archive

from .constants import COMPOUNDING_KEYS, REVENUE_RULING_PATTERN
from .errors import AfrUpdateError, AfrUpdateErrorCode
from .fetch import (
    discover_pdf_urls,
    extract_pdf_text,
    fetch_pdf_bytes,
    validate_index_url,
    validate_pdf_url,
)
from .models import AfrRateRecord
from .parser import is_afr_ruling_text, parse_afr_record


DEFAULT_SOURCE_ARCHIVE_DIR = Path("sources/irs-revenue-rulings")
MONTHLY_RULING_ARCHIVE_SUBJECTS = ("afr", "section-7520-rates")


def load_existing_records(
    dataset_dir: Path, legacy_data_path: Path | None = None
) -> list[AfrRateRecord]:
    records = load_year_sharded_records(dataset_dir / "by-year")
    if len(records) > 0:
        return records

    if legacy_data_path is None:
        legacy_data_path = dataset_dir / "rates.json"
    return load_legacy_records(legacy_data_path)


def load_legacy_records(data_path: Path) -> list[AfrRateRecord]:
    if not data_path.exists():
        return []

    try:
        raw_data = data_path.read_text(encoding="utf-8")
        json_value = json.loads(raw_data, object_pairs_hook=no_duplicate_keys)
    except AfrUpdateError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON) from None

    if not isinstance(json_value, list):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)

    return sorted(parse_json_record(item) for item in json_value)


def load_year_sharded_records(year_dir: Path) -> list[AfrRateRecord]:
    if not year_dir.exists():
        return []

    records: list[AfrRateRecord] = []
    try:
        shard_paths = sorted(year_dir.glob("*.json"))
    except OSError:
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON) from None

    for shard_path in shard_paths:
        shard_year = year_from_shard_path(shard_path)
        if shard_year is None:
            raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)
        for record in load_legacy_records(shard_path):
            if record.effective_month[:4] != shard_year:
                raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)
            records.append(record)

    return sorted(records)


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AfrUpdateError(AfrUpdateErrorCode.DUPLICATE_JSON_KEY)
        result[key] = value
    return result


def parse_json_record(value: object) -> AfrRateRecord:
    if not isinstance(value, dict):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)

    expected_keys = {
        "effective_month",
        "applicable_federal_rates",
        "adjusted_applicable_federal_rates",
    }
    legacy_keys = {*expected_keys, "revenue_ruling"}
    legacy_keys_with_source = {*legacy_keys, "source_url"}
    actual_keys = set(value.keys())
    if (
        actual_keys != expected_keys
        and actual_keys != legacy_keys
        and actual_keys != legacy_keys_with_source
    ):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)

    effective_month = value["effective_month"]
    revenue_ruling = value.get("revenue_ruling")
    source_url = value.get("source_url", "")
    applicable_federal_rates = value["applicable_federal_rates"]
    adjusted_applicable_federal_rates = value["adjusted_applicable_federal_rates"]

    if (
        not isinstance(effective_month, str)
        or re.match(r"^[0-9]{4}-[0-9]{2}$", effective_month) is None
        or not isinstance(applicable_federal_rates, dict)
        or not isinstance(adjusted_applicable_federal_rates, dict)
        or not isinstance(source_url, str)
    ):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)

    if revenue_ruling is not None and (
        not isinstance(revenue_ruling, str)
        or REVENUE_RULING_PATTERN.fullmatch(revenue_ruling) is None
    ):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)

    if source_url != "":
        validate_pdf_url(source_url)
    validate_json_rate_map(applicable_federal_rates)
    validate_json_adjusted_map(adjusted_applicable_federal_rates)

    return AfrRateRecord(
        effective_month=effective_month,
        revenue_ruling=revenue_ruling,
        source_url=source_url,
        applicable_federal_rates=applicable_federal_rates,
        adjusted_applicable_federal_rates=adjusted_applicable_federal_rates,
    )


def validate_json_rate_map(value: dict[str, Any]) -> None:
    expected = {
        "short_term": {"afr", "afr_110", "afr_120", "afr_130"},
        "mid_term": {"afr", "afr_110", "afr_120", "afr_130", "afr_150", "afr_175"},
        "long_term": {"afr", "afr_110", "afr_120", "afr_130"},
    }
    if set(value.keys()) != set(expected.keys()):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)
    for term, rate_keys in expected.items():
        term_value = value[term]
        if not isinstance(term_value, dict) or set(term_value.keys()) != rate_keys:
            raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)
        for rate_key in rate_keys:
            compounding_value = term_value[rate_key]
            validate_compounding_json_value(compounding_value)


def validate_json_adjusted_map(value: dict[str, Any]) -> None:
    if set(value.keys()) != {"short_term", "mid_term", "long_term"}:
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)
    for term in value.values():
        validate_compounding_json_value(term)


def validate_compounding_json_value(value: object) -> None:
    if not isinstance(value, dict) or set(value.keys()) != set(COMPOUNDING_KEYS):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)
    for rate in value.values():
        if not isinstance(rate, int) or rate < 0 or rate > 100_000:
            raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON)


def merge_records(
    existing_records: list[AfrRateRecord],
    source_records: list[AfrRateRecord],
) -> tuple[list[AfrRateRecord], bool]:
    merged_by_month: dict[str, AfrRateRecord] = {}

    for record in existing_records:
        existing = merged_by_month.get(record.effective_month)
        if existing is not None:
            raise AfrUpdateError(AfrUpdateErrorCode.DUPLICATE_JSON_RECORD)
        merged_by_month[record.effective_month] = record

    source_months: set[str] = set()
    changed = False
    for record in source_records:
        if record.effective_month in source_months:
            raise AfrUpdateError(AfrUpdateErrorCode.DUPLICATE_SOURCE_RECORD)
        source_months.add(record.effective_month)
        existing = merged_by_month.get(record.effective_month)
        if existing is None:
            merged_by_month[record.effective_month] = record
            changed = True
        elif not existing.has_same_published_values(record):
            raise AfrUpdateError(AfrUpdateErrorCode.CONFLICTING_RECORD)

    return sorted(merged_by_month.values()), changed


def write_records(data_path: Path, records: list[AfrRateRecord]) -> None:
    data_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        [record.to_json_object() for record in sorted(records)], indent=2
    )
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=data_path.parent,
            delete=False,
        ) as temp_file:
            temp_file.write(serialized)
            temp_file.write("\n")
            temp_name = temp_file.name
        os.replace(temp_name, data_path)
    except OSError:
        raise AfrUpdateError(AfrUpdateErrorCode.WRITE_FAILED) from None


def write_dataset_files(
    dataset_dir: Path,
    records: list[AfrRateRecord],
    legacy_data_path: Path | None = None,
) -> None:
    by_year = group_records_by_year(records)
    write_text_file(dataset_dir / "metadata.json", serialize_metadata())
    write_text_file(
        dataset_dir / "manifest.json",
        serialize_manifest(records, by_year),
    )
    year_dir = dataset_dir / "by-year"
    for year in sorted(by_year):
        year_records = by_year[year]
        write_text_file(
            year_dir / year_shard_filename(year), serialize_records(year_records)
        )
    expected_filenames = {year_shard_filename(year) for year in by_year.keys()}
    remove_stale_year_shards(year_dir, expected_filenames)

    if legacy_data_path is None:
        legacy_data_path = dataset_dir / "rates.json"
    remove_legacy_file(legacy_data_path)


def group_records_by_year(
    records: list[AfrRateRecord],
) -> dict[str, list[AfrRateRecord]]:
    by_year: dict[str, list[AfrRateRecord]] = {}
    for record in sorted(records):
        by_year.setdefault(record.effective_month[:4], []).append(record)
    return by_year


def serialize_records(records: list[AfrRateRecord]) -> str:
    return json.dumps(
        [record.to_json_object() for record in sorted(records)], indent=2
    ) + "\n"


def serialize_manifest(
    records: list[AfrRateRecord], by_year: dict[str, list[AfrRateRecord]]
) -> str:
    sorted_records = sorted(records)
    years = []
    for year in sorted(by_year):
        year_records = by_year[year]
        years.append(
            {
                "year": year,
                "path": f"by-year/{year_shard_filename(year)}",
                "record_count": len(year_records),
                "first_effective_month": year_records[0].effective_month,
                "last_effective_month": year_records[-1].effective_month,
            }
        )

    manifest = {
        "dataset_id": "afr",
        "record_storage": "by_year",
        "record_count": len(sorted_records),
        "first_effective_month": (
            sorted_records[0].effective_month if sorted_records else None
        ),
        "last_effective_month": (
            sorted_records[-1].effective_month if sorted_records else None
        ),
        "years": years,
    }
    return json.dumps(manifest, indent=2) + "\n"


def serialize_metadata() -> str:
    metadata = {
        "name": "Applicable Federal Rates",
        "short_name": "AFR",
        "source": "Internal Revenue Service",
        "source_url": "https://www.irs.gov/applicable-federal-rates",
        "record_frequency": "month",
        "date_key": {
            "field": "effective_month",
            "format": "YYYY-MM",
            "meaning": "Calendar month for which the IRS-published AFR ruling applies",
        },
        "value_fields": {
            "applicable_federal_rates": {
                "unit": "basis_points",
                "scale": "1 percent = 100 basis points",
                "shape": "term -> rate_family -> compounding_period",
            },
            "adjusted_applicable_federal_rates": {
                "unit": "basis_points",
                "scale": "1 percent = 100 basis points",
                "shape": "term -> compounding_period",
            },
        },
        "calculator_semantics": {
            "curve_role": "irs_monthly_statutory_rate_table",
            "source_rate_expression": "annualized_percent",
            "compounding": "source_published_compounding_columns",
            "day_count": "consumer_supplied",
            "discount_curve_ready": False,
            "notes": (
                "Use the compounding column required by the governing calculation. "
                "These records preserve IRS-published AFR tables and do not infer "
                "a present-value convention."
            ),
        },
        "storage": {
            "manifest_file": "manifest.json",
            "primary_records": "by-year/YYYY-afr.json",
            "year_shards": "by-year/YYYY-afr.json",
            "ordering": "ascending_effective_month",
            "dedupe_key": "effective_month",
        },
    }
    return json.dumps(metadata, indent=2) + "\n"


def year_shard_filename(year: str) -> str:
    return f"{year}-afr.json"


def year_from_shard_path(shard_path: Path) -> str | None:
    name = shard_path.name
    if name.endswith("-afr.json"):
        year = name.removesuffix("-afr.json")
        if re.fullmatch(r"[0-9]{4}", year) is not None:
            return year
        return None
    if re.fullmatch(r"[0-9]{4}[.]json", name) is not None:
        return name[:4]
    return None


def write_text_file(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_name = temp_file.name
        os.replace(temp_name, path)
    except OSError:
        raise AfrUpdateError(AfrUpdateErrorCode.WRITE_FAILED) from None


def remove_stale_year_shards(year_dir: Path, expected_filenames: set[str]) -> None:
    if not year_dir.exists():
        return

    try:
        for shard_path in year_dir.glob("*.json"):
            if shard_path.name not in expected_filenames:
                shard_path.unlink()
    except OSError:
        raise AfrUpdateError(AfrUpdateErrorCode.WRITE_FAILED) from None


def remove_legacy_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        raise AfrUpdateError(AfrUpdateErrorCode.WRITE_FAILED) from None


def dataset_needs_write(
    dataset_dir: Path,
    records: list[AfrRateRecord],
    legacy_data_path: Path | None = None,
) -> bool:
    if legacy_data_path is None:
        legacy_data_path = dataset_dir / "rates.json"
    if legacy_data_path.exists():
        return True
    if not (dataset_dir / "manifest.json").exists():
        return True

    expected_metadata = serialize_metadata()
    try:
        if (dataset_dir / "metadata.json").read_text(encoding="utf-8") != expected_metadata:
            return True
    except OSError:
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON) from None

    by_year = group_records_by_year(records)
    expected_manifest = serialize_manifest(records, by_year)
    try:
        if (dataset_dir / "manifest.json").read_text(encoding="utf-8") != expected_manifest:
            return True
    except OSError:
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON) from None

    expected_years = set(by_year.keys())
    for year in expected_years:
        year_path = dataset_dir / "by-year" / year_shard_filename(year)
        if not year_path.exists():
            return True
        try:
            if year_path.read_text(encoding="utf-8") != serialize_records(by_year[year]):
                return True
        except OSError:
            raise AfrUpdateError(AfrUpdateErrorCode.INVALID_JSON) from None
    return False


def update_from_pdf_texts(
    pdf_texts: list[tuple[str, str]],
    dataset_dir: Path,
    write: bool,
    legacy_data_path: Path | None = None,
) -> tuple[int, int, int, bool]:
    existing_records = load_existing_records(dataset_dir, legacy_data_path)
    records = [parse_afr_record(text, source_url) for source_url, text in pdf_texts]
    merged_records, changed = merge_records(existing_records, records)
    needs_write = dataset_needs_write(dataset_dir, merged_records, legacy_data_path)
    if write and (changed or needs_write):
        write_dataset_files(dataset_dir, merged_records, legacy_data_path)
    return (len(records), len(existing_records), len(merged_records), changed or needs_write)


def update_from_index(
    index_url: str,
    dataset_dir: Path,
    write: bool,
    backfill: bool,
    legacy_data_path: Path | None = None,
    archive_sources: bool = False,
    source_archive_dir: Path = DEFAULT_SOURCE_ARCHIVE_DIR,
) -> tuple[int, int, int, bool]:
    validate_index_url(index_url)
    pdf_urls = discover_pdf_urls(index_url, backfill)
    existing_records = load_existing_records(dataset_dir, legacy_data_path)
    existing_months = {record.effective_month for record in existing_records}
    records: list[AfrRateRecord] = []
    archive_candidates: list[tuple[AfrRateRecord, bytes]] = []
    seen_months: set[str] = set()

    for pdf_url in pdf_urls:
        if archive_sources and write and source_archive_is_complete(
            pdf_url, existing_months, source_archive_dir
        ):
            continue
        pdf_bytes = fetch_pdf_bytes(pdf_url)
        pdf_text = extract_pdf_text(pdf_bytes)
        if not is_afr_ruling_text(pdf_text):
            continue
        record = parse_afr_record(pdf_text, pdf_url)
        if record.effective_month in seen_months:
            raise AfrUpdateError(AfrUpdateErrorCode.DUPLICATE_SOURCE_RECORD)
        records.append(record)
        archive_candidates.append((record, pdf_bytes))
        seen_months.add(record.effective_month)

    merged_records, changed = merge_records(existing_records, records)
    needs_write = dataset_needs_write(dataset_dir, merged_records, legacy_data_path)
    data_changed = changed or needs_write
    if write and data_changed:
        write_dataset_files(dataset_dir, merged_records, legacy_data_path)
    archive_changed = False
    if archive_sources and write:
        for record, pdf_bytes in archive_candidates:
            archive_changed = (
                archive_monthly_ruling_source(record, pdf_bytes, source_archive_dir)
                or archive_changed
            )
    return (
        len(records),
        len(existing_records),
        len(merged_records),
        data_changed or archive_changed,
    )


def archive_monthly_ruling_source(
    record: AfrRateRecord, pdf_bytes: bytes, source_archive_dir: Path
) -> bool:
    if record.revenue_ruling is None or record.source_url == "":
        raise AfrUpdateError(AfrUpdateErrorCode.SOURCE_ARCHIVE_FAILED)
    periods = (record.effective_month,)
    try:
        if irs_archive.archive_contains_entry(
            source_archive_dir,
            periods,
            MONTHLY_RULING_ARCHIVE_SUBJECTS,
            record.source_url,
        ):
            return False
        irs_archive.archive_pdf(
            archive_dir=source_archive_dir,
            year=record.effective_month[:4],
            periods=periods,
            subjects=MONTHLY_RULING_ARCHIVE_SUBJECTS,
            source_url=record.source_url,
            title=record.revenue_ruling,
            retrieved_date=date.today().isoformat(),
            body=pdf_bytes,
        )
        return True
    except irs_archive.ArchiveError:
        raise AfrUpdateError(AfrUpdateErrorCode.SOURCE_ARCHIVE_FAILED) from None


def source_archive_is_complete(
    source_url: str, existing_months: set[str], source_archive_dir: Path
) -> bool:
    try:
        archived_periods = irs_archive.archived_periods_for_source(
            source_archive_dir,
            MONTHLY_RULING_ARCHIVE_SUBJECTS,
            source_url,
        )
    except irs_archive.ArchiveError:
        raise AfrUpdateError(AfrUpdateErrorCode.SOURCE_ARCHIVE_FAILED) from None
    if len(archived_periods) == 0:
        return False
    return all(period in existing_months for period in archived_periods)
