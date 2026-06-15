#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Update the local IRS unified estate and gift tax exemption history."""

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


DEFAULT_DATA_PATH = Path("estate-gift-tax-exemption/estate-gift-tax-exemption.json")
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
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
APPLIES_TO = "estates_of_decedents_dying_during_period"
LEGACY_APPLIES_TO = "estates_of_decedents_dying_during_calendar_year"
STATIC_BASIC_EXCLUSION_RANGES = (
    ("1977-01-01", "1977-06-30", 30_000),
    ("1977-07-01", "1977-12-31", 120_667),
    ("1978-01-01", "1978-12-31", 134_000),
    ("1979-01-01", "1979-12-31", 147_333),
    ("1980-01-01", "1980-12-31", 161_563),
    ("1981-01-01", "1981-12-31", 175_625),
    ("1982-01-01", "1982-12-31", 225_000),
    ("1983-01-01", "1983-12-31", 275_000),
    ("1984-01-01", "1984-12-31", 325_000),
    ("1985-01-01", "1985-12-31", 400_000),
    ("1986-01-01", "1986-12-31", 500_000),
    ("1987-01-01", "1987-12-31", 600_000),
    ("1988-01-01", "1988-12-31", 600_000),
    ("1989-01-01", "1989-12-31", 600_000),
    ("1990-01-01", "1990-12-31", 600_000),
    ("1991-01-01", "1991-12-31", 600_000),
    ("1992-01-01", "1992-12-31", 600_000),
    ("1993-01-01", "1993-12-31", 600_000),
    ("1994-01-01", "1994-12-31", 600_000),
    ("1995-01-01", "1995-12-31", 600_000),
    ("1996-01-01", "1996-12-31", 600_000),
    ("1997-01-01", "1997-12-31", 600_000),
    ("1998-01-01", "1998-12-31", 625_000),
    ("1999-01-01", "1999-12-31", 650_000),
    ("2000-01-01", "2000-12-31", 675_000),
    ("2001-01-01", "2001-12-31", 675_000),
    ("2002-01-01", "2002-12-31", 1_000_000),
    ("2003-01-01", "2003-12-31", 1_000_000),
    ("2004-01-01", "2004-12-31", 1_000_000),
    ("2005-01-01", "2005-12-31", 1_000_000),
    ("2006-01-01", "2006-12-31", 1_000_000),
    ("2007-01-01", "2007-12-31", 1_000_000),
    ("2008-01-01", "2008-12-31", 1_000_000),
    ("2009-01-01", "2009-12-31", 1_000_000),
    ("2010-01-01", "2010-12-31", 1_000_000),
    ("2011-01-01", "2011-12-31", 5_000_000),
    ("2012-01-01", "2012-12-31", 5_120_000),
    ("2013-01-01", "2013-12-31", 5_250_000),
    ("2014-01-01", "2014-12-31", 5_340_000),
    ("2015-01-01", "2015-12-31", 5_430_000),
    ("2016-01-01", "2016-12-31", 5_450_000),
    ("2017-01-01", "2017-12-31", 5_490_000),
    ("2018-01-01", "2018-12-31", 11_180_000),
    ("2019-01-01", "2019-12-31", 11_400_000),
    ("2020-01-01", "2020-12-31", 11_580_000),
    ("2021-01-01", "2021-12-31", 11_700_000),
    ("2022-01-01", "2022-12-31", 12_060_000),
    ("2023-01-01", "2023-12-31", 12_920_000),
    ("2024-01-01", "2024-12-31", 13_610_000),
    ("2025-01-01", "2025-12-31", 13_990_000),
    ("2026-01-01", "2026-12-31", 15_000_000),
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


class UpdateUnifiedEstateGiftTaxExemptionError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: UpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, order=True)
class UnifiedEstateGiftTaxExemptionRecord:
    """One IRS-published annual basic exclusion amount observation."""

    period_start_date: str
    period_end_date: str
    basic_exclusion_amount_usd: int
    applies_to: str
    revenue_procedure: str | None
    source_url: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "period_start_date": self.period_start_date,
            "period_end_date": self.period_end_date,
            "basic_exclusion_amount_usd": self.basic_exclusion_amount_usd,
        }

    def has_same_published_values(
        self, other: "UnifiedEstateGiftTaxExemptionRecord"
    ) -> bool:
        return (
            self.period_start_date == other.period_start_date
            and self.period_end_date == other.period_end_date
            and self.basic_exclusion_amount_usd == other.basic_exclusion_amount_usd
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
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.BAD_URL)


def validate_news_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.irs.gov"
        or not parsed.path.startswith(IRS_NEWS_PREFIX)
    ):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.BAD_URL)


def fetch_pdf(source_url: str) -> bytes:
    validate_pdf_url(source_url)
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "vanderbr-rates-unified-estate-gift-tax-exemption-updater/"
                "1.0 (+https://github.com/vanderbr/rates)"
            )
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_PDF_BYTES:
                raise UpdateUnifiedEstateGiftTaxExemptionError(
                    UpdateErrorCode.FETCH_TOO_LARGE
                )
            body = response.read(MAX_PDF_BYTES + 1)
    except (HTTPError, OSError, URLError, ValueError):
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.FETCH_FAILED
        ) from None

    if len(body) > MAX_PDF_BYTES:
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.FETCH_TOO_LARGE)

    return body


def fetch_news_html_if_available(source_url: str) -> str | None:
    validate_news_url(source_url)
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "vanderbr-rates-unified-estate-gift-tax-exemption-updater/"
                "1.0 (+https://github.com/vanderbr/rates)"
            )
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_HTML_BYTES:
                raise UpdateUnifiedEstateGiftTaxExemptionError(
                    UpdateErrorCode.FETCH_TOO_LARGE
                )
            body = response.read(MAX_HTML_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.FETCH_FAILED
        ) from None
    except (OSError, URLError, ValueError):
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.FETCH_FAILED
        ) from None

    if len(body) > MAX_HTML_BYTES:
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.FETCH_TOO_LARGE)

    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.FETCH_FAILED
        ) from None


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
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.PDF_TEXT_EXTRACTION_FAILED
        ) from None

    if result.returncode != 0:
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.PDF_TEXT_EXTRACTION_FAILED
        )

    return result.stdout


def parse_exemption_record(
    source_text: str, source_url: str
) -> UnifiedEstateGiftTaxExemptionRecord:
    validate_pdf_url(source_url)
    normalized_text = normalize_text(source_text)
    revenue_procedure = parse_revenue_procedure(normalized_text)
    amount, year = parse_basic_exclusion_amount(normalized_text)

    return UnifiedEstateGiftTaxExemptionRecord(
        period_start_date=f"{year:04d}-01-01",
        period_end_date=f"{year:04d}-12-31",
        basic_exclusion_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def parse_revenue_procedure(normalized_text: str) -> str:
    match = REVENUE_PROCEDURE_PATTERN.search(normalized_text)
    if match is None:
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.INVALID_REVENUE_PROCEDURE
        )
    return f"Rev. Proc. {match.group('procedure')}"


def parse_basic_exclusion_amount(normalized_text: str) -> tuple[int, int]:
    patterns = (
        re.compile(
            r"estate of any decedent dying in calendar year (?P<year>[0-9]{4}), "
            r"the basic exclusion amount is \$(?P<amount>[0-9,]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"estates of decedents who die during (?P<year>[0-9]{4}) have a "
            r"basic exclusion amount of \$(?P<amount>[0-9,]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"basic exclusion amount to \$(?P<amount>[0-9,]+) for calendar year "
            r"(?P<year>[0-9]{4})",
            re.IGNORECASE,
        ),
    )

    for pattern in patterns:
        match = pattern.search(normalized_text)
        if match is not None:
            return (parse_amount(match.group("amount")), parse_year(match.group("year")))

    raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_AMOUNT)


def parse_amount(value: str) -> int:
    amount_text = value.replace(",", "")
    if not amount_text.isdecimal():
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_AMOUNT)

    amount = int(amount_text)
    if amount <= 0:
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_AMOUNT)

    return amount


def parse_year(value: str) -> int:
    if YEAR_PATTERN.fullmatch(value) is None:
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_AMOUNT)
    year = int(value)
    if year < 1900 or year > 2500:
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_AMOUNT)
    return year


def validate_iso_date(value: str) -> None:
    if DATE_PATTERN.fullmatch(value) is None:
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.INVALID_JSON
        ) from None


def period_for_year(year: int) -> tuple[str, str]:
    return (f"{year:04d}-01-01", f"{year:04d}-12-31")


def load_existing_records(
    data_path: Path,
) -> list[UnifiedEstateGiftTaxExemptionRecord]:
    if not data_path.exists():
        return []

    try:
        raw_data = data_path.read_text(encoding="utf-8")
        json_value = json.loads(raw_data, object_pairs_hook=no_duplicate_keys)
    except UpdateUnifiedEstateGiftTaxExemptionError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.INVALID_JSON
        ) from None

    if not isinstance(json_value, list):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)

    return sorted(parse_json_record(item) for item in json_value)


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateUnifiedEstateGiftTaxExemptionError(
                UpdateErrorCode.DUPLICATE_JSON_KEY
            )
        result[key] = value
    return result


def parse_json_record(value: object) -> UnifiedEstateGiftTaxExemptionRecord:
    if not isinstance(value, dict):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)

    expected_keys = {
        "period_start_date",
        "period_end_date",
        "basic_exclusion_amount_usd",
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
        "basic_exclusion_amount_usd",
    }
    legacy_year_keys_with_applies_to = {
        *legacy_year_keys,
        "applies_to",
    }
    legacy_year_keys_with_revenue_procedure = {
        *legacy_year_keys_with_applies_to,
        "revenue_procedure",
    }
    legacy_year_keys_with_source_url = {*legacy_year_keys_with_applies_to, "source_url"}
    actual_keys = set(value.keys())
    if actual_keys not in (
        expected_keys,
        expected_keys_with_applies_to,
        legacy_keys_with_revenue_procedure,
        legacy_keys_with_source_url,
        legacy_keys_with_both,
        legacy_year_keys,
        legacy_year_keys_with_applies_to,
        legacy_year_keys_with_revenue_procedure,
        legacy_year_keys_with_source_url,
    ):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)

    if "year" in value:
        year = value["year"]
        if not isinstance(year, int) or year < 1900 or year > 2500:
            raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)
        period_start_date, period_end_date = period_for_year(year)
    else:
        period_start_date = value["period_start_date"]
        period_end_date = value["period_end_date"]

    amount = value["basic_exclusion_amount_usd"]
    applies_to = value.get("applies_to", APPLIES_TO)
    revenue_procedure = value.get("revenue_procedure")
    source_url = value.get("source_url", "")

    if not isinstance(period_start_date, str) or not isinstance(period_end_date, str):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)
    validate_iso_date(period_start_date)
    validate_iso_date(period_end_date)
    if period_start_date > period_end_date:
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)

    if (
        not isinstance(amount, int)
        or amount <= 0
        or not isinstance(applies_to, str)
        or applies_to not in (APPLIES_TO, LEGACY_APPLIES_TO)
    ):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)

    if revenue_procedure is not None and (
        not isinstance(revenue_procedure, str)
        or REVENUE_PROCEDURE_PATTERN.fullmatch(revenue_procedure) is None
    ):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)

    if not isinstance(source_url, str):
        raise UpdateUnifiedEstateGiftTaxExemptionError(UpdateErrorCode.INVALID_JSON)
    if source_url != "":
        validate_pdf_url(source_url)

    return UnifiedEstateGiftTaxExemptionRecord(
        period_start_date=period_start_date,
        period_end_date=period_end_date,
        basic_exclusion_amount_usd=amount,
        applies_to=APPLIES_TO,
        revenue_procedure=revenue_procedure,
        source_url=source_url,
    )


def static_form_709_records() -> list[UnifiedEstateGiftTaxExemptionRecord]:
    return [
        UnifiedEstateGiftTaxExemptionRecord(
            period_start_date=period_start_date,
            period_end_date=period_end_date,
            basic_exclusion_amount_usd=amount,
            applies_to=APPLIES_TO,
            revenue_procedure=None,
            source_url="",
        )
        for period_start_date, period_end_date, amount in STATIC_BASIC_EXCLUSION_RANGES
    ]


def merge_records(
    existing_records: list[UnifiedEstateGiftTaxExemptionRecord],
    source_records: list[UnifiedEstateGiftTaxExemptionRecord],
) -> tuple[list[UnifiedEstateGiftTaxExemptionRecord], bool]:
    merged_by_period: dict[
        tuple[str, str], UnifiedEstateGiftTaxExemptionRecord
    ] = {}

    for record in existing_records:
        key = (record.period_start_date, record.period_end_date)
        existing = merged_by_period.get(key)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateUnifiedEstateGiftTaxExemptionError(
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
            raise UpdateUnifiedEstateGiftTaxExemptionError(
                UpdateErrorCode.CONFLICTING_RECORD
            )

    return sorted(merged_by_period.values()), changed


def remove_records_overlapped_by_source_records(
    existing_records: list[UnifiedEstateGiftTaxExemptionRecord],
    source_records: list[UnifiedEstateGiftTaxExemptionRecord],
) -> tuple[list[UnifiedEstateGiftTaxExemptionRecord], bool]:
    source_keys = {
        (record.period_start_date, record.period_end_date) for record in source_records
    }
    filtered_records: list[UnifiedEstateGiftTaxExemptionRecord] = []
    changed = False

    for existing_record in existing_records:
        existing_key = (
            existing_record.period_start_date,
            existing_record.period_end_date,
        )
        if existing_key in source_keys:
            filtered_records.append(existing_record)
            continue
        if any(
            periods_overlap(existing_record, source_record)
            for source_record in source_records
        ):
            changed = True
            continue
        filtered_records.append(existing_record)

    return (filtered_records, changed)


def periods_overlap(
    first: UnifiedEstateGiftTaxExemptionRecord,
    second: UnifiedEstateGiftTaxExemptionRecord,
) -> bool:
    first_start = dt.date.fromisoformat(first.period_start_date)
    first_end = dt.date.fromisoformat(first.period_end_date)
    second_start = dt.date.fromisoformat(second.period_start_date)
    second_end = dt.date.fromisoformat(second.period_end_date)
    return first_start <= second_end and second_start <= first_end


def write_records(
    data_path: Path, records: list[UnifiedEstateGiftTaxExemptionRecord]
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
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.WRITE_FAILED
        ) from None


def serialize_records(records: list[UnifiedEstateGiftTaxExemptionRecord]) -> str:
    return json.dumps(
        [record.to_json_object() for record in sorted(records)], indent=2
    ) + "\n"


def records_file_needs_write(
    data_path: Path, records: list[UnifiedEstateGiftTaxExemptionRecord]
) -> bool:
    if not data_path.exists():
        return True
    try:
        return data_path.read_text(encoding="utf-8") != serialize_records(records)
    except OSError:
        raise UpdateUnifiedEstateGiftTaxExemptionError(
            UpdateErrorCode.INVALID_JSON
        ) from None


def annual_update_target_year(today: dt.date) -> int | None:
    if today.month >= 11:
        return today.year + 1
    if today.month == 1:
        return today.year
    return None


def has_record_for_year(
    records: list[UnifiedEstateGiftTaxExemptionRecord], target_year: int
) -> bool:
    target_start, target_end = period_for_year(target_year)
    return any(
        record.period_start_date == target_start and record.period_end_date == target_end
        for record in records
    )


def source_urls_for_run(
    backfill: bool,
    today: dt.date,
    existing_records: list[UnifiedEstateGiftTaxExemptionRecord] | None = None,
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
        except UpdateUnifiedEstateGiftTaxExemptionError as error:
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
    original_existing_count = len(existing_records)
    records = static_form_709_records() if include_static_history else []
    for source_url, source_text in source_texts:
        record = parse_exemption_record(source_text, source_url)
        if target_year is not None:
            target_start, target_end = period_for_year(target_year)
            if (
                record.period_start_date != target_start
                or record.period_end_date != target_end
            ):
                continue
        records.append(record)

    if len(records) == 0:
        return (0, original_existing_count, original_existing_count, False)

    records_by_period: dict[
        tuple[str, str], UnifiedEstateGiftTaxExemptionRecord
    ] = {}
    for record in records:
        key = (record.period_start_date, record.period_end_date)
        existing = records_by_period.get(key)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateUnifiedEstateGiftTaxExemptionError(
                UpdateErrorCode.DUPLICATE_SOURCE_RECORD
            )
        records_by_period[key] = record

    source_records = sorted(records_by_period.values())
    if include_static_history:
        existing_records, removed_grouped_records = (
            remove_records_overlapped_by_source_records(
                existing_records, source_records
            )
        )
    else:
        removed_grouped_records = False

    merged_records, changed = merge_records(existing_records, source_records)
    changed = changed or removed_grouped_records
    needs_write = records_file_needs_write(data_path, merged_records)
    if write and (changed or needs_write):
        write_records(data_path, merged_records)
    return (
        len(records_by_period),
        original_existing_count,
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
        description="Update IRS unified estate and gift tax exemption JSON."
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
            raise UpdateUnifiedEstateGiftTaxExemptionError(
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
            "unified_estate_gift_tax_exemption_update "
            f"failed={UpdateErrorCode.INVALID_ARGUMENTS.value}",
            file=sys.stderr,
        )
        return 1
    except UpdateUnifiedEstateGiftTaxExemptionError as error:
        print(
            f"unified_estate_gift_tax_exemption_update failed={error.code.value}",
            file=sys.stderr,
        )
        return 1

    source_count, existing_count, final_count, changed = result
    print(
        "unified_estate_gift_tax_exemption_update "
        f"source_records={source_count} "
        f"existing_records={existing_count} "
        f"final_records={final_count} "
        f"changed={str(changed).lower()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
