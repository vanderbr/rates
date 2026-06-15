#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Update the IRS annual gift exclusion for a noncitizen spouse."""

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


DEFAULT_DATA_PATH = Path("noncitizen-spouse-gift-exclusion/noncitizen-spouse-gift-exclusion.json")
REVENUE_PROCEDURE_PATTERN = irs_sources.REVENUE_PROCEDURE_PATTERN
IRS_REVENUE_PROCEDURE_URLS = irs_sources.IRS_REVENUE_PROCEDURE_URLS
IRS_DROP_LINK_PATTERN = irs_sources.IRS_DROP_LINK_PATTERN
YEAR_PATTERN = re.compile(r"^[0-9]{4}$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
APPLIES_TO = "gifts_to_spouse_who_is_not_united_states_citizen_made_during_period"
LEGACY_APPLIES_TO = "gifts_to_spouse_who_is_not_united_states_citizen_made_during_calendar_year"
STATIC_NONCITIZEN_SPOUSE_EXCLUSION_RANGES = (
    ("1988-07-14", "1998-12-31", 100_000),
    ("1999-01-01", "1999-12-31", 101_000),
    ("2000-01-01", "2000-12-31", 103_000),
    ("2001-01-01", "2001-12-31", 106_000),
    ("2002-01-01", "2002-12-31", 110_000),
    ("2003-01-01", "2003-12-31", 112_000),
    ("2004-01-01", "2004-12-31", 114_000),
    ("2005-01-01", "2005-12-31", 117_000),
    ("2006-01-01", "2006-12-31", 120_000),
    ("2007-01-01", "2007-12-31", 125_000),
    ("2008-01-01", "2008-12-31", 128_000),
    ("2009-01-01", "2009-12-31", 133_000),
    ("2010-01-01", "2010-12-31", 134_000),
    ("2011-01-01", "2011-12-31", 136_000),
    ("2012-01-01", "2012-12-31", 139_000),
    ("2013-01-01", "2013-12-31", 143_000),
    ("2014-01-01", "2014-12-31", 145_000),
    ("2015-01-01", "2015-12-31", 147_000),
    ("2016-01-01", "2016-12-31", 148_000),
    ("2017-01-01", "2017-12-31", 149_000),
    ("2018-01-01", "2018-12-31", 152_000),
    ("2019-01-01", "2019-12-31", 155_000),
    ("2020-01-01", "2020-12-31", 157_000),
    ("2021-01-01", "2021-12-31", 159_000),
    ("2022-01-01", "2022-12-31", 164_000),
    ("2023-01-01", "2023-12-31", 175_000),
    ("2024-01-01", "2024-12-31", 185_000),
    ("2025-01-01", "2025-12-31", 190_000),
    ("2026-01-01", "2026-12-31", 194_000),
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


class UpdateNoncitizenSpouseGiftExclusionError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: UpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, order=True)
class NoncitizenSpouseGiftExclusionRecord:
    """One IRS-published annual exclusion observation for a noncitizen spouse."""

    period_start_date: str
    period_end_date: str
    annual_exclusion_amount_usd: int
    applies_to: str
    revenue_procedure: str | None
    source_url: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "period_start_date": self.period_start_date,
            "period_end_date": self.period_end_date,
            "annual_exclusion_amount_usd": self.annual_exclusion_amount_usd,
        }

    def has_same_published_values(
        self, other: "NoncitizenSpouseGiftExclusionRecord"
    ) -> bool:
        return (
            self.period_start_date == other.period_start_date
            and self.period_end_date == other.period_end_date
            and self.annual_exclusion_amount_usd
            == other.annual_exclusion_amount_usd
            and self.applies_to == other.applies_to
        )


def map_source_error(
    error: irs_sources.UpdateAnnualGiftExclusionError,
) -> UpdateNoncitizenSpouseGiftExclusionError:
    try:
        code = UpdateErrorCode(error.code.value)
    except ValueError:
        code = UpdateErrorCode.FETCH_FAILED
    return UpdateNoncitizenSpouseGiftExclusionError(code)


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
        raise UpdateNoncitizenSpouseGiftExclusionError(
            UpdateErrorCode.INVALID_REVENUE_PROCEDURE
        )
    return f"Rev. Proc. {match.group('procedure')}"


def parse_noncitizen_spouse_gift_exclusion_record(
    source_text: str, source_url: str
) -> NoncitizenSpouseGiftExclusionRecord:
    validate_pdf_url(source_url)
    normalized_text = normalize_text(source_text)
    revenue_procedure = parse_revenue_procedure(normalized_text)
    amount, year = parse_annual_exclusion_amount(normalized_text)

    return NoncitizenSpouseGiftExclusionRecord(
        period_start_date=f"{year:04d}-01-01",
        period_end_date=f"{year:04d}-12-31",
        annual_exclusion_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def parse_annual_exclusion_amount(normalized_text: str) -> tuple[int, int]:
    pattern = re.compile(
        r"For calendar year (?P<year>[0-9]{4}), the first "
        r"\$(?P<amount>[0-9,]+)(?: \(instead of the amount provided in "
        r"paragraph \(1\) of this section [0-9.]+\))? of gifts to a spouse who "
        r"is not a citizen of the United States",
        re.IGNORECASE,
    )
    match = pattern.search(normalized_text)
    if match is None:
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)

    return (parse_amount(match.group("amount")), parse_year(match.group("year")))


def parse_amount(value: str) -> int:
    amount_text = value.replace(",", "")
    if not amount_text.isdecimal():
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)

    amount = int(amount_text)
    if amount <= 0:
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)

    return amount


def parse_year(value: str) -> int:
    if YEAR_PATTERN.fullmatch(value) is None:
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)
    year = int(value)
    if year < 1900 or year > 2500:
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)
    return year


def validate_iso_date(value: str) -> None:
    if DATE_PATTERN.fullmatch(value) is None:
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise UpdateNoncitizenSpouseGiftExclusionError(
            UpdateErrorCode.INVALID_JSON
        ) from None


def period_for_year(year: int) -> tuple[str, str]:
    return (f"{year:04d}-01-01", f"{year:04d}-12-31")


def load_existing_records(
    data_path: Path,
) -> list[NoncitizenSpouseGiftExclusionRecord]:
    if not data_path.exists():
        return []

    try:
        raw_data = data_path.read_text(encoding="utf-8")
        json_value = json.loads(raw_data, object_pairs_hook=no_duplicate_keys)
    except UpdateNoncitizenSpouseGiftExclusionError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise UpdateNoncitizenSpouseGiftExclusionError(
            UpdateErrorCode.INVALID_JSON
        ) from None

    if not isinstance(json_value, list):
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    return sorted(parse_json_record(item) for item in json_value)


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateNoncitizenSpouseGiftExclusionError(
                UpdateErrorCode.DUPLICATE_JSON_KEY
            )
        result[key] = value
    return result


def parse_json_record(value: object) -> NoncitizenSpouseGiftExclusionRecord:
    if not isinstance(value, dict):
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    expected_keys = {
        "period_start_date",
        "period_end_date",
        "annual_exclusion_amount_usd",
    }
    expected_keys_with_applies_to = {*expected_keys, "applies_to"}
    legacy_keys_with_revenue_procedure = {
        *expected_keys_with_applies_to,
        "revenue_procedure",
    }
    legacy_keys_with_source_url = {*expected_keys_with_applies_to, "source_url"}
    legacy_keys_with_both = {
        *expected_keys_with_applies_to,
        "revenue_procedure",
        "source_url",
    }
    legacy_year_keys = {
        "year",
        "annual_exclusion_amount_usd",
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
        legacy_keys_with_revenue_procedure,
        legacy_keys_with_source_url,
        legacy_keys_with_both,
        legacy_year_keys,
        legacy_year_keys_with_applies_to,
        legacy_year_keys_with_source_url,
    ):
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    if "year" in value:
        year = value["year"]
        if not isinstance(year, int) or year < 1900 or year > 2500:
            raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)
        period_start_date, period_end_date = period_for_year(year)
    else:
        period_start_date = value["period_start_date"]
        period_end_date = value["period_end_date"]

    amount = value["annual_exclusion_amount_usd"]
    applies_to = value.get("applies_to", APPLIES_TO)
    revenue_procedure = value.get("revenue_procedure")
    source_url = value.get("source_url", "")

    if not isinstance(period_start_date, str) or not isinstance(period_end_date, str):
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)
    validate_iso_date(period_start_date)
    validate_iso_date(period_end_date)
    if period_start_date > period_end_date:
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    if (
        not isinstance(amount, int)
        or amount <= 0
        or not isinstance(applies_to, str)
        or applies_to not in (APPLIES_TO, LEGACY_APPLIES_TO)
    ):
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    if revenue_procedure is not None and (
        not isinstance(revenue_procedure, str)
        or REVENUE_PROCEDURE_PATTERN.fullmatch(revenue_procedure) is None
    ):
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    if not isinstance(source_url, str):
        raise UpdateNoncitizenSpouseGiftExclusionError(UpdateErrorCode.INVALID_JSON)
    if source_url != "":
        validate_pdf_url(source_url)

    return NoncitizenSpouseGiftExclusionRecord(
        period_start_date=period_start_date,
        period_end_date=period_end_date,
        annual_exclusion_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def static_historical_records() -> list[NoncitizenSpouseGiftExclusionRecord]:
    return [
        NoncitizenSpouseGiftExclusionRecord(
            period_start_date=period_start_date,
            period_end_date=period_end_date,
            annual_exclusion_amount_usd=amount,
            applies_to=APPLIES_TO,
            revenue_procedure=None,
            source_url="",
        )
        for period_start_date, period_end_date, amount in STATIC_NONCITIZEN_SPOUSE_EXCLUSION_RANGES
    ]


def merge_records(
    existing_records: list[NoncitizenSpouseGiftExclusionRecord],
    source_records: list[NoncitizenSpouseGiftExclusionRecord],
) -> tuple[list[NoncitizenSpouseGiftExclusionRecord], bool]:
    merged_by_period: dict[
        tuple[str, str], NoncitizenSpouseGiftExclusionRecord
    ] = {}

    for record in existing_records:
        key = (record.period_start_date, record.period_end_date)
        existing = merged_by_period.get(key)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateNoncitizenSpouseGiftExclusionError(
                UpdateErrorCode.CONFLICTING_RECORD
            )
        merged_by_period[key] = record

    changed = False
    for record in source_records:
        key = (record.period_start_date, record.period_end_date)
        existing = merged_by_period.get(key)
        if existing is None:
            merged_by_period[key] = record
            changed = True
        elif not existing.has_same_published_values(record):
            raise UpdateNoncitizenSpouseGiftExclusionError(
                UpdateErrorCode.CONFLICTING_RECORD
            )

    return sorted(merged_by_period.values()), changed


def write_records(
    data_path: Path, records: list[NoncitizenSpouseGiftExclusionRecord]
) -> None:
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
        raise UpdateNoncitizenSpouseGiftExclusionError(
            UpdateErrorCode.WRITE_FAILED
        ) from None


def serialize_records(records: list[NoncitizenSpouseGiftExclusionRecord]) -> str:
    return json.dumps(
        [record.to_json_object() for record in sorted(records)], indent=2
    ) + "\n"


def records_file_needs_write(
    data_path: Path, records: list[NoncitizenSpouseGiftExclusionRecord]
) -> bool:
    if not data_path.exists():
        return True
    try:
        return data_path.read_text(encoding="utf-8") != serialize_records(records)
    except OSError:
        raise UpdateNoncitizenSpouseGiftExclusionError(
            UpdateErrorCode.INVALID_JSON
        ) from None


def annual_update_target_year(today: dt.date) -> int | None:
    return irs_sources.annual_update_target_year(today)


def has_record_for_year(
    records: list[NoncitizenSpouseGiftExclusionRecord], target_year: int
) -> bool:
    target_start, target_end = period_for_year(target_year)
    return any(
        record.period_start_date <= target_start and record.period_end_date >= target_end
        for record in records
    )


def source_urls_for_run(
    backfill: bool,
    today: dt.date,
    existing_records: list[NoncitizenSpouseGiftExclusionRecord] | None = None,
) -> tuple[str, ...]:
    if backfill:
        return IRS_REVENUE_PROCEDURE_URLS

    target_year = annual_update_target_year(today)
    if target_year is None:
        return ()

    if existing_records is not None and has_record_for_year(
        existing_records, target_year
    ):
        return ()

    discovered_urls: list[str] = []
    for news_url in irs_sources.prospective_news_urls(today):
        try:
            html = irs_sources.fetch_news_html_if_available(news_url)
        except irs_sources.UpdateAnnualGiftExclusionError as error:
            if error.code in (
                irs_sources.UpdateErrorCode.FETCH_FAILED,
                irs_sources.UpdateErrorCode.FETCH_TOO_LARGE,
            ):
                continue
            raise map_source_error(error) from None
        if html is None:
            continue
        discovered_urls.extend(discover_pdf_urls_from_news_html(html))

    return tuple(irs_sources.unique_preserving_order(discovered_urls))


def discover_pdf_urls_from_news_html(html: str) -> list[str]:
    return irs_sources.discover_pdf_urls_from_news_html(html)


def update_from_source_texts(
    source_texts: list[tuple[str, str]],
    data_path: Path,
    write: bool,
    include_static_history: bool = False,
    target_year: int | None = None,
) -> tuple[int, int, int, bool]:
    existing_records = load_existing_records(data_path)
    records = static_historical_records() if include_static_history else []
    for source_url, source_text in source_texts:
        record = parse_noncitizen_spouse_gift_exclusion_record(
            source_text, source_url
        )
        if target_year is not None:
            target_start, target_end = period_for_year(target_year)
            if (
                record.period_start_date != target_start
                or record.period_end_date != target_end
            ):
                continue
        records.append(record)

    if len(records) == 0:
        return (0, len(existing_records), len(existing_records), False)

    records_by_period: dict[
        tuple[str, str], NoncitizenSpouseGiftExclusionRecord
    ] = {}
    for record in records:
        key = (record.period_start_date, record.period_end_date)
        existing = records_by_period.get(key)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateNoncitizenSpouseGiftExclusionError(
                UpdateErrorCode.DUPLICATE_SOURCE_RECORD
            )
        records_by_period[key] = record

    merged_records, changed = merge_records(
        existing_records, sorted(records_by_period.values())
    )
    needs_write = records_file_needs_write(data_path, merged_records)
    if write and (changed or needs_write):
        write_records(data_path, merged_records)
    return (
        len(records_by_period),
        len(existing_records),
        len(merged_records),
        changed or needs_write,
    )


def update_from_urls(
    source_urls: tuple[str, ...],
    data_path: Path,
    write: bool,
    include_static_history: bool = False,
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
        include_static_history=include_static_history,
        target_year=target_year,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update IRS annual gift exclusion for a noncitizen spouse JSON."
    )
    parser.add_argument("--write", action="store_true", help="write updated JSON")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="merge deterministic legacy history and configured IRS Revenue Procedures",
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
            raise UpdateNoncitizenSpouseGiftExclusionError(
                UpdateErrorCode.INVALID_ARGUMENTS
            )

        if args.input_text is not None:
            source_text = args.input_text.read_text(encoding="utf-8")
            result = update_from_source_texts(
                [(args.source_url, source_text)], args.data_path, args.write
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
            "noncitizen_spouse_gift_exclusion_update "
            f"failed={UpdateErrorCode.INVALID_ARGUMENTS.value}",
            file=sys.stderr,
        )
        return 1
    except UpdateNoncitizenSpouseGiftExclusionError as error:
        print(
            f"noncitizen_spouse_gift_exclusion_update failed={error.code.value}",
            file=sys.stderr,
        )
        return 1

    source_count, existing_count, final_count, changed = result
    print(
        "noncitizen_spouse_gift_exclusion_update "
        f"source_records={source_count} "
        f"existing_records={existing_count} "
        f"final_records={final_count} "
        f"changed={str(changed).lower()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
