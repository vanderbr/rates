#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Update the local IRS annual gift tax exclusion history."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_DATA_PATH = Path("annual-gift-exclusion/annual-gift-exclusion.json")
MAX_PDF_BYTES = 2_000_000
MAX_HTML_BYTES = 2_000_000
REQUEST_TIMEOUT_SECONDS = 30
IRS_DROP_PREFIX = "/pub/irs-drop/"
IRS_NEWS_PREFIX = "/newsroom/"
IRS_REVENUE_PROCEDURE_URLS = (
    "https://www.irs.gov/pub/irs-drop/rp-18-57.pdf",
    "https://www.irs.gov/pub/irs-drop/rp-19-44.pdf",
    "https://www.irs.gov/pub/irs-drop/rp-20-45.pdf",
    "https://www.irs.gov/pub/irs-drop/rp-21-45.pdf",
    "https://www.irs.gov/pub/irs-drop/rp-22-38.pdf",
    "https://www.irs.gov/pub/irs-drop/rp-23-34.pdf",
    "https://www.irs.gov/pub/irs-drop/rp-24-40.pdf",
    "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf",
)
REVENUE_PROCEDURE_PATTERN = re.compile(
    r"Rev\. Proc\. (?P<procedure>[0-9]{4}-[0-9]{1,3})"
)
IRS_DROP_LINK_PATTERN = re.compile(
    r"(?:https://www\.irs\.gov)?/pub/irs-drop/rp-[0-9]{2}-[0-9]{1,3}\.pdf"
)
YEAR_PATTERN = re.compile(r"^[0-9]{4}$")
APPLIES_TO = "gifts_of_present_interests_made_during_calendar_year"
STATIC_ANNUAL_EXCLUSION_RANGES = (
    (1955, 1981, 3_000),
    (1982, 2001, 10_000),
    (2002, 2005, 11_000),
    (2006, 2008, 12_000),
    (2009, 2012, 13_000),
    (2013, 2017, 14_000),
    (2018, 2021, 15_000),
    (2022, 2022, 16_000),
    (2023, 2023, 17_000),
    (2024, 2024, 18_000),
    (2025, 2026, 19_000),
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


class UpdateAnnualGiftExclusionError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: UpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, order=True)
class AnnualGiftExclusionRecord:
    """One IRS-published annual gift tax exclusion observation."""

    year: int
    annual_exclusion_amount_usd: int
    applies_to: str
    revenue_procedure: str | None
    source_url: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "period_start_date": f"{self.year:04d}-01-01",
            "period_end_date": f"{self.year:04d}-12-31",
            "annual_exclusion_amount_usd": self.annual_exclusion_amount_usd,
        }

    def has_same_published_values(self, other: "AnnualGiftExclusionRecord") -> bool:
        return (
            self.year == other.year
            and self.annual_exclusion_amount_usd
            == other.annual_exclusion_amount_usd
            and self.applies_to == other.applies_to
        )


def validate_pdf_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.irs.gov"
        or not parsed.path.startswith(IRS_DROP_PREFIX)
        or not parsed.path.endswith(".pdf")
    ):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.BAD_URL)


def validate_news_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.irs.gov"
        or not parsed.path.startswith(IRS_NEWS_PREFIX)
    ):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.BAD_URL)


def fetch_pdf(source_url: str) -> bytes:
    validate_pdf_url(source_url)
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "vanderbr-rates-annual-gift-exclusion-updater/"
                "1.0 (+https://github.com/vanderbr/rates)"
            )
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_PDF_BYTES:
                raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_TOO_LARGE)
            body = response.read(MAX_PDF_BYTES + 1)
    except (HTTPError, OSError, URLError, ValueError):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_FAILED) from None

    if len(body) > MAX_PDF_BYTES:
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_TOO_LARGE)

    return body


def fetch_news_html_if_available(source_url: str) -> str | None:
    validate_news_url(source_url)
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "vanderbr-rates-annual-gift-exclusion-updater/"
                "1.0 (+https://github.com/vanderbr/rates)"
            )
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_HTML_BYTES:
                raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_TOO_LARGE)
            body = response.read(MAX_HTML_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_FAILED) from None
    except (OSError, URLError, ValueError):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_FAILED) from None

    if len(body) > MAX_HTML_BYTES:
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_TOO_LARGE)

    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.FETCH_FAILED) from None


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf_file:
            pdf_file.write(pdf_bytes)
            pdf_file.flush()
            result = subprocess.run(
                ["pdftotext", "-layout", pdf_file.name, "-"],
                check=False,
                capture_output=True,
                text=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        raise UpdateAnnualGiftExclusionError(
            UpdateErrorCode.PDF_TEXT_EXTRACTION_FAILED
        ) from None

    if result.returncode != 0:
        raise UpdateAnnualGiftExclusionError(
            UpdateErrorCode.PDF_TEXT_EXTRACTION_FAILED
        )

    return result.stdout


def parse_annual_gift_exclusion_record(
    source_text: str, source_url: str
) -> AnnualGiftExclusionRecord:
    validate_pdf_url(source_url)
    normalized_text = normalize_text(source_text)
    revenue_procedure = parse_revenue_procedure(normalized_text)
    amount, year = parse_annual_exclusion_amount(normalized_text)

    return AnnualGiftExclusionRecord(
        year=year,
        annual_exclusion_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def parse_revenue_procedure(normalized_text: str) -> str:
    match = REVENUE_PROCEDURE_PATTERN.search(normalized_text)
    if match is None:
        raise UpdateAnnualGiftExclusionError(
            UpdateErrorCode.INVALID_REVENUE_PROCEDURE
        )
    return f"Rev. Proc. {match.group('procedure')}"


def parse_annual_exclusion_amount(normalized_text: str) -> tuple[int, int]:
    patterns = (
        re.compile(
            r"For calendar year (?P<year>[0-9]{4}), the first "
            r"\$(?P<amount>[0-9,]+) of gifts to any person",
            re.IGNORECASE,
        ),
        re.compile(
            r"annual exclusion for gifts is \$(?P<amount>[0-9,]+) for calendar "
            r"year (?P<year>[0-9]{4})",
            re.IGNORECASE,
        ),
    )

    for pattern in patterns:
        match = pattern.search(normalized_text)
        if match is not None:
            return (parse_amount(match.group("amount")), parse_year(match.group("year")))

    raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)


def parse_amount(value: str) -> int:
    amount_text = value.replace(",", "")
    if not amount_text.isdecimal():
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)

    amount = int(amount_text)
    if amount <= 0:
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)

    return amount


def parse_year(value: str) -> int:
    if YEAR_PATTERN.fullmatch(value) is None:
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)
    year = int(value)
    if year < 1900 or year > 2500:
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_AMOUNT)
    return year


def load_existing_records(data_path: Path) -> list[AnnualGiftExclusionRecord]:
    if not data_path.exists():
        return []

    try:
        raw_data = data_path.read_text(encoding="utf-8")
        json_value = json.loads(raw_data, object_pairs_hook=no_duplicate_keys)
    except UpdateAnnualGiftExclusionError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON) from None

    if not isinstance(json_value, list):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    return sorted(parse_json_record(item) for item in json_value)


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateAnnualGiftExclusionError(UpdateErrorCode.DUPLICATE_JSON_KEY)
        result[key] = value
    return result


def parse_json_record(value: object) -> AnnualGiftExclusionRecord:
    if not isinstance(value, dict):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    canonical_keys = {
        "period_start_date",
        "period_end_date",
        "annual_exclusion_amount_usd",
    }
    canonical_keys_with_applies_to = {*canonical_keys, "applies_to"}
    legacy_keys = {
        "year",
        "annual_exclusion_amount_usd",
    }
    legacy_keys_with_applies_to = {
        *legacy_keys,
        "applies_to",
    }
    legacy_keys_with_revenue_procedure = {
        *legacy_keys_with_applies_to,
        "revenue_procedure",
    }
    legacy_keys_with_source_url = {*legacy_keys_with_applies_to, "source_url"}
    legacy_keys_with_both = {
        *legacy_keys_with_applies_to,
        "revenue_procedure",
        "source_url",
    }
    actual_keys = set(value.keys())
    if actual_keys not in (
        canonical_keys,
        canonical_keys_with_applies_to,
        legacy_keys_with_revenue_procedure,
        legacy_keys,
        legacy_keys_with_applies_to,
        legacy_keys_with_source_url,
        legacy_keys_with_both,
    ):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    year = parse_json_record_year(value)
    amount = value["annual_exclusion_amount_usd"]
    applies_to = value.get("applies_to", APPLIES_TO)
    revenue_procedure = value.get("revenue_procedure")
    source_url = value.get("source_url", "")

    if (
        not isinstance(amount, int)
        or amount <= 0
        or not isinstance(applies_to, str)
        or applies_to != APPLIES_TO
    ):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    if revenue_procedure is not None and (
        not isinstance(revenue_procedure, str)
        or REVENUE_PROCEDURE_PATTERN.fullmatch(revenue_procedure) is None
    ):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)

    if not isinstance(source_url, str):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)
    if source_url != "":
        validate_pdf_url(source_url)

    return AnnualGiftExclusionRecord(
        year=year,
        annual_exclusion_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def parse_json_record_year(value: dict[str, object]) -> int:
    legacy_year = value.get("year")
    if isinstance(legacy_year, int):
        if legacy_year < 1900 or legacy_year > 2500:
            raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)
        return legacy_year

    period_start_date = value.get("period_start_date")
    period_end_date = value.get("period_end_date")
    if not isinstance(period_start_date, str) or not isinstance(period_end_date, str):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)
    if (
        not period_start_date.endswith("-01-01")
        or not period_end_date.endswith("-12-31")
        or period_start_date[:4] != period_end_date[:4]
    ):
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON)
    return parse_year(period_start_date[:4])


def static_historical_records() -> list[AnnualGiftExclusionRecord]:
    records: list[AnnualGiftExclusionRecord] = []
    for start_year, end_year, amount in STATIC_ANNUAL_EXCLUSION_RANGES:
        for year in range(start_year, end_year + 1):
            records.append(
                AnnualGiftExclusionRecord(
                    year=year,
                    annual_exclusion_amount_usd=amount,
                    applies_to=APPLIES_TO,
                    revenue_procedure=None,
                    source_url="",
                )
            )
    return records


def merge_records(
    existing_records: list[AnnualGiftExclusionRecord],
    source_records: list[AnnualGiftExclusionRecord],
) -> tuple[list[AnnualGiftExclusionRecord], bool]:
    merged_by_year: dict[int, AnnualGiftExclusionRecord] = {}

    for record in existing_records:
        existing = merged_by_year.get(record.year)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateAnnualGiftExclusionError(UpdateErrorCode.CONFLICTING_RECORD)
        merged_by_year[record.year] = record

    changed = False
    for record in source_records:
        existing = merged_by_year.get(record.year)
        if existing is None:
            merged_by_year[record.year] = record
            changed = True
        elif not existing.has_same_published_values(record):
            raise UpdateAnnualGiftExclusionError(UpdateErrorCode.CONFLICTING_RECORD)

    return sorted(merged_by_year.values()), changed


def write_records(data_path: Path, records: list[AnnualGiftExclusionRecord]) -> None:
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
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.WRITE_FAILED) from None


def annual_update_target_year(today: dt.date) -> int | None:
    if today.month >= 11:
        return today.year + 1
    if today.month == 1:
        return today.year
    return None


def has_record_for_year(
    records: list[AnnualGiftExclusionRecord], target_year: int
) -> bool:
    return any(record.year == target_year for record in records)


def source_urls_for_run(
    backfill: bool,
    today: dt.date,
    existing_records: list[AnnualGiftExclusionRecord] | None = None,
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
    for news_url in prospective_news_urls(today):
        try:
            html = fetch_news_html_if_available(news_url)
        except UpdateAnnualGiftExclusionError as error:
            if error.code in (
                UpdateErrorCode.FETCH_FAILED,
                UpdateErrorCode.FETCH_TOO_LARGE,
            ):
                continue
            raise
        if html is None:
            continue
        discovered_urls.extend(discover_pdf_urls_from_news_html(html))
    return tuple(unique_preserving_order(discovered_urls))


def prospective_news_urls(today: dt.date) -> tuple[str, ...]:
    urls: list[str] = []
    for year in (today.year + 1, today.year, today.year - 1):
        urls.extend(
            [
                (
                    "https://www.irs.gov/newsroom/"
                    f"irs-releases-tax-inflation-adjustments-for-tax-year-{year}"
                ),
                (
                    "https://www.irs.gov/newsroom/"
                    f"irs-provides-tax-inflation-adjustments-for-tax-year-{year}"
                ),
                (
                    "https://www.irs.gov/newsroom/"
                    f"irs-releases-tax-inflation-adjustments-for-tax-year-{year}-"
                    "including-amendments-from-the-one-big-beautiful-bill"
                ),
            ]
        )
    return tuple(urls)


def discover_pdf_urls_from_news_html(html: str) -> list[str]:
    urls: list[str] = []
    for match in IRS_DROP_LINK_PATTERN.finditer(html):
        value = match.group(0)
        if value.startswith("https://"):
            urls.append(value)
        else:
            urls.append(f"https://www.irs.gov{value}")
    return unique_preserving_order(urls)


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        unique_values.append(value)
        seen.add(value)
    return unique_values


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
        record = parse_annual_gift_exclusion_record(source_text, source_url)
        if target_year is not None and record.year != target_year:
            continue
        records.append(record)

    if len(records) == 0:
        return (0, len(existing_records), len(existing_records), False)

    records_by_year: dict[int, AnnualGiftExclusionRecord] = {}
    for record in records:
        existing = records_by_year.get(record.year)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateAnnualGiftExclusionError(UpdateErrorCode.DUPLICATE_SOURCE_RECORD)
        records_by_year[record.year] = record

    merged_records, changed = merge_records(
        existing_records, sorted(records_by_year.values())
    )
    needs_write = records_file_needs_write(data_path, merged_records)
    if write and (changed or needs_write):
        write_records(data_path, merged_records)
    return (
        len(records_by_year),
        len(existing_records),
        len(merged_records),
        changed or needs_write,
    )


def records_file_needs_write(
    data_path: Path, records: list[AnnualGiftExclusionRecord]
) -> bool:
    if not data_path.exists():
        return True
    expected = (
        json.dumps([record.to_json_object() for record in sorted(records)], indent=2)
        + "\n"
    )
    try:
        return data_path.read_text(encoding="utf-8") != expected
    except OSError:
        raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_JSON) from None


def update_from_urls(
    source_urls: tuple[str, ...],
    data_path: Path,
    write: bool,
    include_static_history: bool = False,
    target_year: int | None = None,
) -> tuple[int, int, int, bool]:
    source_texts: list[tuple[str, str]] = []
    for source_url in source_urls:
        pdf_bytes = fetch_pdf(source_url)
        source_texts.append((source_url, extract_pdf_text(pdf_bytes)))

    return update_from_source_texts(
        source_texts,
        data_path,
        write,
        include_static_history=include_static_history,
        target_year=target_year,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update IRS annual gift tax exclusion JSON."
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
            raise UpdateAnnualGiftExclusionError(UpdateErrorCode.INVALID_ARGUMENTS)

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
            f"annual_gift_exclusion_update failed={UpdateErrorCode.INVALID_ARGUMENTS.value}",
            file=sys.stderr,
        )
        return 1
    except UpdateAnnualGiftExclusionError as error:
        print(
            f"annual_gift_exclusion_update failed={error.code.value}",
            file=sys.stderr,
        )
        return 1

    source_count, existing_count, final_count, changed = result
    print(
        "annual_gift_exclusion_update "
        f"source_records={source_count} "
        f"existing_records={existing_count} "
        f"final_records={final_count} "
        f"changed={str(changed).lower()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
