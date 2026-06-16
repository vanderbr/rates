#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Update the IRS generation-skipping transfer exemption history."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import update_annual_gift_exclusion as irs_sources
except ModuleNotFoundError:
    from scripts import update_annual_gift_exclusion as irs_sources


DEFAULT_DATA_PATH = Path("gst-exemption/gst-exemption.json")
REVENUE_PROCEDURE_PATTERN = irs_sources.REVENUE_PROCEDURE_PATTERN
IRS_REVENUE_PROCEDURE_URLS = irs_sources.IRS_REVENUE_PROCEDURE_URLS
YEAR_PATTERN = re.compile(r"^[0-9]{4}$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
APPLIES_TO = "generation_skipping_transfers_during_calendar_year"

# Deterministic legacy history is kept in code so backfill remains stable while
# newer annual values are extracted from IRS Revenue Procedure PDFs.
STATIC_GST_EXEMPTION_TABLE = (
    (1986, "1986-10-22", "1986-12-31", 1_000_000),
    (1987, "1987-01-01", "1987-12-31", 1_000_000),
    (1988, "1988-01-01", "1988-12-31", 1_000_000),
    (1989, "1989-01-01", "1989-12-31", 1_000_000),
    (1990, "1990-01-01", "1990-12-31", 1_000_000),
    (1991, "1991-01-01", "1991-12-31", 1_000_000),
    (1992, "1992-01-01", "1992-12-31", 1_000_000),
    (1993, "1993-01-01", "1993-12-31", 1_000_000),
    (1994, "1994-01-01", "1994-12-31", 1_000_000),
    (1995, "1995-01-01", "1995-12-31", 1_000_000),
    (1996, "1996-01-01", "1996-12-31", 1_000_000),
    (1997, "1997-01-01", "1997-12-31", 1_000_000),
    (1998, "1998-01-01", "1998-12-31", 1_000_000),
    (1999, 1_010_000),
    (2000, 1_030_000),
    (2001, 1_060_000),
    (2002, 1_100_000),
    (2003, 1_120_000),
    (2004, 1_500_000),
    (2005, 1_500_000),
    (2006, 2_000_000),
    (2007, 2_000_000),
    (2008, 2_000_000),
    (2009, 3_500_000),
    (2010, 5_000_000),
    (2011, 5_000_000),
    (2012, 5_120_000),
    (2013, 5_250_000),
    (2014, 5_340_000),
    (2015, 5_430_000),
    (2016, 5_450_000),
    (2017, 5_490_000),
    (2018, 11_180_000),
    (2019, 11_400_000),
    (2020, 11_580_000),
    (2021, 11_700_000),
    (2022, 12_060_000),
    (2023, 12_920_000),
    (2024, 13_610_000),
    (2025, 13_990_000),
)


class UpdateErrorCode(Enum):
    BAD_URL = "bad_url"
    CONFLICTING_RECORD = "conflicting_record"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    DUPLICATE_SOURCE_RECORD = "duplicate_source_record"
    FETCH_FAILED = "fetch_failed"
    FETCH_TOO_LARGE = "fetch_too_large"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_JSON = "invalid_json"
    INVALID_REVENUE_PROCEDURE = "invalid_revenue_procedure"
    NO_SOURCE_RECORDS = "no_source_records"
    PDF_TEXT_EXTRACTION_FAILED = "pdf_text_extraction_failed"
    WRITE_FAILED = "write_failed"


class UpdateGstExemptionError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: UpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, order=True)
class GstExemptionRecord:
    """One IRS-published generation-skipping transfer exemption observation."""

    period_start_date: str
    period_end_date: str
    exemption_amount_usd: int
    applies_to: str
    revenue_procedure: str | None
    source_url: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "period_start_date": self.period_start_date,
            "period_end_date": self.period_end_date,
            "exemption_amount_usd": self.exemption_amount_usd,
        }

    def has_same_published_values(self, other: "GstExemptionRecord") -> bool:
        return (
            self.period_start_date == other.period_start_date
            and self.period_end_date == other.period_end_date
            and self.exemption_amount_usd == other.exemption_amount_usd
            and self.applies_to == other.applies_to
        )


def map_source_error(
    error: irs_sources.UpdateAnnualGiftExclusionError,
) -> UpdateGstExemptionError:
    try:
        code = UpdateErrorCode(error.code.value)
    except ValueError:
        code = UpdateErrorCode.FETCH_FAILED
    return UpdateGstExemptionError(code)


def static_form_709_records() -> list[GstExemptionRecord]:
    records: list[GstExemptionRecord] = []
    for entry in STATIC_GST_EXEMPTION_TABLE:
        if len(entry) == 2:
            year, amount = entry
            period_start_date = f"{year:04d}-01-01"
            period_end_date = f"{year:04d}-12-31"
        else:
            _year, period_start_date, period_end_date, amount = entry

        records.append(
            GstExemptionRecord(
                period_start_date=period_start_date,
                period_end_date=period_end_date,
                exemption_amount_usd=amount,
                applies_to=APPLIES_TO,
                revenue_procedure=None,
                source_url="",
            )
        )
    return records


def validate_pdf_url(source_url: str) -> None:
    try:
        irs_sources.validate_pdf_url(source_url)
    except irs_sources.UpdateAnnualGiftExclusionError as error:
        raise map_source_error(error) from None


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def parse_revenue_procedure(normalized_text: str) -> str:
    match = REVENUE_PROCEDURE_PATTERN.search(normalized_text)
    if match is None:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_REVENUE_PROCEDURE)
    return f"Rev. Proc. {match.group('procedure')}"


def parse_gst_exemption_record(source_text: str, source_url: str) -> GstExemptionRecord:
    validate_pdf_url(source_url)
    normalized_text = normalize_text(source_text)
    revenue_procedure = parse_revenue_procedure(normalized_text)
    amount, year = parse_gst_exemption_amount(normalized_text)

    return GstExemptionRecord(
        period_start_date=f"{year:04d}-01-01",
        period_end_date=f"{year:04d}-12-31",
        exemption_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def parse_gst_exemption_amount(normalized_text: str) -> tuple[int, int]:
    pattern = re.compile(
        r"For calendar year (?P<year>[0-9]{4}), the generation-skipping "
        r"transfer exemption amount under § 2631\(c\) is equal to "
        r"\$(?P<amount>[0-9,]+)",
        re.IGNORECASE,
    )
    match = pattern.search(normalized_text)
    if match is None:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_AMOUNT)

    return (parse_amount(match.group("amount")), parse_year(match.group("year")))


def parse_amount(value: str) -> int:
    amount_text = value.replace(",", "")
    if not amount_text.isdecimal():
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_AMOUNT)

    amount = int(amount_text)
    if amount <= 0:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_AMOUNT)

    return amount


def parse_year(value: str) -> int:
    if YEAR_PATTERN.fullmatch(value) is None:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_AMOUNT)
    year = int(value)
    if year < 1900 or year > 2500:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_AMOUNT)
    return year


def validate_iso_date(value: str) -> None:
    if DATE_PATTERN.fullmatch(value) is None:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON) from None


def load_existing_records(data_path: Path) -> list[GstExemptionRecord]:
    if not data_path.exists():
        return []

    try:
        raw_data = data_path.read_text(encoding="utf-8")
        json_value = json.loads(raw_data, object_pairs_hook=no_duplicate_keys)
    except UpdateGstExemptionError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON) from None

    if not isinstance(json_value, list):
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)

    return sorted(parse_json_record(item) for item in json_value)


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateGstExemptionError(UpdateErrorCode.DUPLICATE_JSON_KEY)
        result[key] = value
    return result


def parse_json_record(value: object) -> GstExemptionRecord:
    if not isinstance(value, dict):
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)

    expected_keys = {
        "period_start_date",
        "period_end_date",
        "exemption_amount_usd",
    }
    expected_keys_with_applies_to = {*expected_keys, "applies_to"}
    legacy_keys = {*expected_keys_with_applies_to, "source_url"}
    legacy_keys_with_revenue_procedure = {
        *expected_keys_with_applies_to,
        "revenue_procedure",
    }
    legacy_keys_with_both = {
        *expected_keys_with_applies_to,
        "revenue_procedure",
        "source_url",
    }
    legacy_year_keys = {
        "year",
        "exemption_amount_usd",
    }
    legacy_year_keys_with_applies_to = {
        *legacy_year_keys,
        "applies_to",
        "revenue_procedure",
    }
    legacy_year_keys_with_source_url = {
        *legacy_year_keys_with_applies_to,
        "source_url",
    }
    actual_keys = set(value.keys())
    if actual_keys not in (
        expected_keys,
        expected_keys_with_applies_to,
        legacy_keys,
        legacy_keys_with_revenue_procedure,
        legacy_keys_with_both,
        legacy_year_keys,
        legacy_year_keys_with_applies_to,
        legacy_year_keys_with_source_url,
    ):
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)

    if "year" in value:
        year = value["year"]
        if not isinstance(year, int) or year < 1900 or year > 2500:
            raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)
        period_start_date = f"{year:04d}-01-01"
        period_end_date = f"{year:04d}-12-31"
    else:
        period_start_date = value["period_start_date"]
        period_end_date = value["period_end_date"]

    amount = value["exemption_amount_usd"]
    applies_to = value.get("applies_to", APPLIES_TO)
    revenue_procedure = value.get("revenue_procedure")
    source_url = value.get("source_url", "")

    if (
        not isinstance(period_start_date, str)
        or not isinstance(period_end_date, str)
        or not isinstance(amount, int)
        or amount <= 0
        or not isinstance(applies_to, str)
        or applies_to != APPLIES_TO
    ):
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)
    validate_iso_date(period_start_date)
    validate_iso_date(period_end_date)
    if period_start_date > period_end_date:
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)

    if not isinstance(source_url, str):
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)
    if revenue_procedure is not None and (
        not isinstance(revenue_procedure, str)
        or REVENUE_PROCEDURE_PATTERN.fullmatch(revenue_procedure) is None
    ):
        raise UpdateGstExemptionError(UpdateErrorCode.INVALID_JSON)

    return GstExemptionRecord(
        period_start_date=period_start_date,
        period_end_date=period_end_date,
        exemption_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def merge_records(
    existing_records: list[GstExemptionRecord],
    source_records: list[GstExemptionRecord],
) -> tuple[list[GstExemptionRecord], bool]:
    merged_by_period: dict[tuple[str, str], GstExemptionRecord] = {}

    for record in existing_records:
        key = (record.period_start_date, record.period_end_date)
        existing = merged_by_period.get(key)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateGstExemptionError(UpdateErrorCode.CONFLICTING_RECORD)
        merged_by_period[key] = record

    changed = False
    for record in source_records:
        key = (record.period_start_date, record.period_end_date)
        existing = merged_by_period.get(key)
        if existing is None:
            merged_by_period[key] = record
            changed = True
        elif not existing.has_same_published_values(record):
            raise UpdateGstExemptionError(UpdateErrorCode.CONFLICTING_RECORD)

    return sorted(merged_by_period.values()), changed


def write_records(data_path: Path, records: list[GstExemptionRecord]) -> None:
    data_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = serialize_records(records)
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=data_path.parent,
            delete=False,
        ) as temp_file:
            temp_file.write(serialized)
            temp_name = temp_file.name
        os.replace(temp_name, data_path)
    except OSError:
        raise UpdateGstExemptionError(UpdateErrorCode.WRITE_FAILED) from None


def serialize_records(records: list[GstExemptionRecord]) -> str:
    return json.dumps(
        [record.to_json_object() for record in sorted(records)], indent=2
    ) + "\n"


def canonical_json_changed(data_path: Path, records: list[GstExemptionRecord]) -> bool:
    if not data_path.exists():
        return True
    try:
        return data_path.read_text(encoding="utf-8") != serialize_records(records)
    except OSError:
        raise UpdateGstExemptionError(UpdateErrorCode.WRITE_FAILED) from None


def annual_update_target_year(today: dt.date) -> int | None:
    return irs_sources.annual_update_target_year(today)


def period_for_year(year: int) -> tuple[str, str]:
    return (f"{year:04d}-01-01", f"{year:04d}-12-31")


def has_record_for_year(records: list[GstExemptionRecord], target_year: int) -> bool:
    target_start, target_end = period_for_year(target_year)
    return any(
        record.period_start_date <= target_start and record.period_end_date >= target_end
        for record in records
    )


def source_urls_for_run(
    backfill: bool,
    today: dt.date,
    existing_records: list[GstExemptionRecord] | None = None,
) -> tuple[str, ...]:
    if not backfill:
        target_year = annual_update_target_year(today)
        if target_year is None:
            return ()
        if existing_records is not None and has_record_for_year(
            existing_records, target_year
        ):
            return ()

    try:
        return irs_sources.source_urls_for_run(backfill, today)
    except irs_sources.UpdateAnnualGiftExclusionError as error:
        raise map_source_error(error) from None


def discover_pdf_urls_from_news_html(html: str) -> list[str]:
    return irs_sources.discover_pdf_urls_from_news_html(html)


def update_from_source_texts(
    source_texts: list[tuple[str, str]],
    data_path: Path,
    write: bool,
    include_static_history: bool,
    target_year: int | None = None,
) -> tuple[int, int, int, bool]:
    existing_records = load_existing_records(data_path)
    records: list[GstExemptionRecord] = []
    if include_static_history:
        records.extend(static_form_709_records())

    seen_periods = {
        (record.period_start_date, record.period_end_date) for record in records
    }
    for source_url, source_text in source_texts:
        try:
            record = parse_gst_exemption_record(source_text, source_url)
        except UpdateGstExemptionError as error:
            if error.code == UpdateErrorCode.INVALID_AMOUNT:
                continue
            raise
        if target_year is not None:
            target_start, target_end = period_for_year(target_year)
            if (
                record.period_start_date != target_start
                or record.period_end_date != target_end
            ):
                continue
        record_key = (record.period_start_date, record.period_end_date)
        if record_key in seen_periods:
            raise UpdateGstExemptionError(UpdateErrorCode.DUPLICATE_SOURCE_RECORD)
        records.append(record)
        seen_periods.add(record_key)

    if len(records) == 0:
        return (0, len(existing_records), len(existing_records), False)

    merged_records, changed = merge_records(existing_records, records)
    changed = changed or canonical_json_changed(data_path, merged_records)
    if write and changed:
        write_records(data_path, merged_records)
    return (len(records), len(existing_records), len(merged_records), changed)


def update_from_urls(
    source_urls: tuple[str, ...],
    data_path: Path,
    write: bool,
    include_static_history: bool,
    target_year: int | None = None,
) -> tuple[int, int, int, bool]:
    source_texts: list[tuple[str, str]] = []
    for source_url in source_urls:
        try:
            pdf_bytes = irs_sources.fetch_pdf(source_url)
            source_texts.append((source_url, irs_sources.extract_pdf_text(pdf_bytes)))
        except irs_sources.UpdateAnnualGiftExclusionError as error:
            raise map_source_error(error) from None

    return update_from_source_texts(
        source_texts,
        data_path,
        write,
        include_static_history,
        target_year=target_year,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update IRS GST exemption JSON.")
    parser.add_argument("--write", action="store_true", help="write updated JSON")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="include deterministic legacy history and all configured Revenue Procedures",
    )
    parser.add_argument(
        "--input-text",
        type=Path,
        help="parse one local extracted source text file instead of fetching IRS PDFs",
    )
    parser.add_argument(
        "--source-url",
        default=IRS_REVENUE_PROCEDURE_URLS[-1],
        help="IRS PDF URL represented by --input-text",
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="path to the local rates JSON file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        if args.input_text is not None and args.backfill:
            raise UpdateGstExemptionError(UpdateErrorCode.INVALID_ARGUMENTS)

        if args.input_text is not None:
            source_text = args.input_text.read_text(encoding="utf-8")
            result = update_from_source_texts(
                [(args.source_url, source_text)],
                args.data_path,
                args.write,
                include_static_history=False,
            )
        else:
            today = dt.date.today()
            existing_records = load_existing_records(args.data_path)
            target_year = None if args.backfill else annual_update_target_year(today)
            source_urls = (
                source_urls_for_run(True, today)
                if args.backfill
                else source_urls_for_run(
                    False,
                    today,
                    existing_records=existing_records,
                )
            )
            result = update_from_urls(
                source_urls,
                args.data_path,
                args.write,
                include_static_history=args.backfill,
                target_year=target_year,
            )
    except (OSError, UnicodeDecodeError):
        print(
            f"gst_exemption_update failed={UpdateErrorCode.INVALID_ARGUMENTS.value}",
            file=sys.stderr,
        )
        return 1
    except UpdateGstExemptionError as error:
        print(f"gst_exemption_update failed={error.code.value}", file=sys.stderr)
        return 1

    source_count, existing_count, final_count, changed = result
    print(
        "gst_exemption_update "
        f"source_records={source_count} "
        f"existing_records={existing_count} "
        f"final_records={final_count} "
        f"changed={str(changed).lower()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
