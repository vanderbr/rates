#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Update the local IRS section 7520 rate history from the IRS website."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_SOURCE_URL = (
    "https://www.irs.gov/businesses/small-businesses-self-employed/"
    "section-7520-interest-rates"
)
PRIOR_YEARS_SOURCE_URL = (
    "https://www.irs.gov/businesses/small-businesses-self-employed/"
    "section-7520-interest-rates-for-prior-years"
)
DEFAULT_DATA_PATH = Path("7520/by-year")
MAX_RESPONSE_BYTES = 2_000_000
REQUEST_TIMEOUT_SECONDS = 30
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
MONTH_PATTERN = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)"
    r" ([0-9]{4})$"
)
REVENUE_RULING_PATTERN = re.compile(r"^Rev\. Rul\.? ([0-9]{4}|[0-9]{2})-[0-9]{1,3}$")
STATIC_IRB_SOURCE_URL = "https://www.irs.gov/pub/irs-irbs/"
STATIC_1996_SECTION_7520_TABLE = (
    ("1996-01", 689, 680, "Rev. Rul. 96-6", "irb96-02.pdf"),
    ("1996-02", 675, 680, "Rev. Rul. 96-14", "irb96-06.pdf"),
    ("1996-03", 656, 660, "Rev. Rul. 96-15", "irb96-11.pdf"),
    ("1996-04", 708, 700, "Rev. Rul. 96-19", "irb96-15.pdf"),
    ("1996-05", 765, 760, "Rev. Rul. 96-24", "irb96-19.pdf"),
    ("1996-06", 793, 800, "Rev. Rul. 96-27", "irb96-24.pdf"),
    ("1996-07", 812, 820, "Rev. Rul. 96-34", "irb96-28.pdf"),
    ("1996-08", 824, 820, "Rev. Rul. 96-37", "irb96-32.pdf"),
    ("1996-09", 799, 800, "Rev. Rul. 96-43", "irb96-37.pdf"),
    ("1996-10", 809, 800, "Rev. Rul. 96-49", "irb96-41.pdf"),
    ("1996-11", 794, 800, "Rev. Rul. 96-52", "irb96-45.pdf"),
    ("1996-12", 759, 760, "Rev. Rul. 96-57", "irb96-50.pdf"),
)
PRIOR_YEARS_HTML_CORRECTIONS = {
    ("2014-10", 222, 222, "Rev. Rul. 2014-26"): 220,
}
EXISTING_RECORD_CORRECTIONS = {
    ("2014-10", 222, 222): 220,
}


class UpdateErrorCode(Enum):
    BAD_URL = "bad_url"
    CONFLICTING_RECORD = "conflicting_record"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    DUPLICATE_SOURCE_RECORD = "duplicate_source_record"
    FETCH_FAILED = "fetch_failed"
    FETCH_TOO_LARGE = "fetch_too_large"
    HTML_TABLE_NOT_FOUND = "html_table_not_found"
    INVALID_JSON = "invalid_json"
    INVALID_MONTH = "invalid_month"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_RATE = "invalid_rate"
    INVALID_REVENUE_RULING = "invalid_revenue_ruling"
    NO_SOURCE_RECORDS = "no_source_records"
    WRITE_FAILED = "write_failed"


class UpdateSection7520RatesError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: UpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, order=True)
class Section7520RateRecord:
    """One IRS-published monthly section 7520 rate observation."""

    effective_month: str
    midterm_afr_120_basis_points: int
    section_7520_rate_basis_points: int
    revenue_ruling: str
    source_url: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "effective_month": self.effective_month,
            "midterm_afr_120_basis_points": self.midterm_afr_120_basis_points,
            "section_7520_rate_basis_points": self.section_7520_rate_basis_points,
        }

    def has_same_published_values(self, other: "Section7520RateRecord") -> bool:
        return (
            self.effective_month == other.effective_month
            and self.midterm_afr_120_basis_points
            == other.midterm_afr_120_basis_points
            and self.section_7520_rate_basis_points
            == other.section_7520_rate_basis_points
        )


class VisibleTextParser(HTMLParser):
    """Extract visible table text without depending on IRS presentation markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []
        self._ignored_depth = 0
        self._text_bytes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return

        normalized = " ".join(data.split())
        if normalized == "":
            return

        self._text_bytes += len(normalized.encode("utf-8"))
        if self._text_bytes > MAX_RESPONSE_BYTES:
            raise UpdateSection7520RatesError(UpdateErrorCode.FETCH_TOO_LARGE)

        self.tokens.append(normalized)


def validate_source_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.irs.gov"
        or not parsed.path.startswith("/businesses/")
    ):
        raise UpdateSection7520RatesError(UpdateErrorCode.BAD_URL)


def fetch_source_html(source_url: str) -> str:
    validate_source_url(source_url)
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "vanderbr-tax-section-7520-rate-updater/"
                "1.0 (+https://github.com/vanderbr/rates)"
            )
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_RESPONSE_BYTES:
                raise UpdateSection7520RatesError(UpdateErrorCode.FETCH_TOO_LARGE)

            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, URLError, ValueError):
        raise UpdateSection7520RatesError(UpdateErrorCode.FETCH_FAILED) from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise UpdateSection7520RatesError(UpdateErrorCode.FETCH_TOO_LARGE)

    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise UpdateSection7520RatesError(UpdateErrorCode.FETCH_FAILED) from None


def parse_section_7520_records(
    html: str, source_url: str
) -> list[Section7520RateRecord]:
    validate_source_url(source_url)
    parser = VisibleTextParser()
    parser.feed(html)
    tokens = parser.tokens

    table_start = find_table_start(tokens)
    records: list[Section7520RateRecord] = []
    seen_months: set[str] = set()
    index = table_start

    while index < len(tokens):
        token = tokens[index]
        if token.startswith("For prior years"):
            break

        if MONTH_PATTERN.match(token) is None:
            index += 1
            continue

        if index + 3 >= len(tokens):
            raise UpdateSection7520RatesError(UpdateErrorCode.HTML_TABLE_NOT_FOUND)

        record = apply_prior_years_html_correction(
            source_url,
            Section7520RateRecord(
                effective_month=parse_valuation_month(token),
                midterm_afr_120_basis_points=parse_percent_basis_points(
                    tokens[index + 1]
                ),
                section_7520_rate_basis_points=parse_percent_basis_points(
                    tokens[index + 2]
                ),
                revenue_ruling=parse_revenue_ruling(tokens[index + 3]),
                source_url=source_url,
            ),
        )

        if record.effective_month in seen_months:
            raise UpdateSection7520RatesError(UpdateErrorCode.DUPLICATE_SOURCE_RECORD)

        records.append(record)
        seen_months.add(record.effective_month)
        index += 4

    if len(records) == 0:
        raise UpdateSection7520RatesError(UpdateErrorCode.NO_SOURCE_RECORDS)

    return sorted(records)


def apply_prior_years_html_correction(
    source_url: str, record: Section7520RateRecord
) -> Section7520RateRecord:
    if source_url != PRIOR_YEARS_SOURCE_URL:
        return record

    corrected_section_rate = PRIOR_YEARS_HTML_CORRECTIONS.get(
        (
            record.effective_month,
            record.midterm_afr_120_basis_points,
            record.section_7520_rate_basis_points,
            record.revenue_ruling,
        )
    )
    if corrected_section_rate is None:
        return record

    # The IRS prior-years HTML transcribes Rev. Rul. 2014-26 Table 5 as 2.22%.
    # The direct ruling PDF publishes 2.2%, so the PDF controls the stored value.
    return Section7520RateRecord(
        effective_month=record.effective_month,
        midterm_afr_120_basis_points=record.midterm_afr_120_basis_points,
        section_7520_rate_basis_points=corrected_section_rate,
        revenue_ruling=record.revenue_ruling,
        source_url=record.source_url,
    )


def find_table_start(tokens: list[str]) -> int:
    header_positions = [
        find_token(tokens, "Valuation month"),
        find_token(tokens, "120% of applicable federal midterm rate"),
        find_token(tokens, "Section 7520 interest rate"),
        find_token(tokens, "Revenue ruling"),
    ]
    if any(position is None for position in header_positions):
        raise UpdateSection7520RatesError(UpdateErrorCode.HTML_TABLE_NOT_FOUND)

    return max(position for position in header_positions if position is not None) + 1


def find_token(tokens: list[str], expected: str) -> int | None:
    for index, token in enumerate(tokens):
        if token == expected:
            return index
    return None


def parse_valuation_month(value: str) -> str:
    match = MONTH_PATTERN.match(value)
    if match is None:
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_MONTH)

    month_number = MONTHS.get(match.group(1).lower())
    if month_number is None:
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_MONTH)

    return f"{int(match.group(2)):04d}-{month_number:02d}"


def parse_percent_basis_points(value: str) -> int:
    normalized = value.strip().rstrip("`").removesuffix("%")
    try:
        percent = Decimal(normalized)
    except InvalidOperation:
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_RATE) from None

    basis_points = percent * Decimal(100)
    if basis_points != basis_points.to_integral_value():
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_RATE)
    if basis_points < 0 or basis_points > Decimal(100_000):
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_RATE)

    return int(basis_points)


def parse_revenue_ruling(value: str) -> str:
    if REVENUE_RULING_PATTERN.match(value) is None:
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_REVENUE_RULING)
    return value.replace("Rev. Rul ", "Rev. Rul. ", 1)


def static_1996_section_7520_records() -> list[Section7520RateRecord]:
    return [
        Section7520RateRecord(
            effective_month=effective_month,
            midterm_afr_120_basis_points=midterm_afr_120_basis_points,
            section_7520_rate_basis_points=section_7520_rate_basis_points,
            revenue_ruling=revenue_ruling,
            source_url=f"{STATIC_IRB_SOURCE_URL}{irb_filename}",
        )
        for (
            effective_month,
            midterm_afr_120_basis_points,
            section_7520_rate_basis_points,
            revenue_ruling,
            irb_filename,
        ) in STATIC_1996_SECTION_7520_TABLE
    ]


def uses_year_shards(data_path: Path) -> bool:
    return data_path.suffix == ""


def record_year(record: Section7520RateRecord) -> str:
    return record.effective_month[:4]


def year_shard_path(data_path: Path, year: str) -> Path:
    return data_path / f"{year}-section-7520-rates.json"


def load_existing_records(data_path: Path) -> list[Section7520RateRecord]:
    if uses_year_shards(data_path):
        if not data_path.exists():
            return []
        records: list[Section7520RateRecord] = []
        try:
            for json_path in sorted(data_path.glob("*.json")):
                records.extend(load_existing_records(json_path))
        except OSError:
            raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_JSON) from None
        return sorted(records)

    if not data_path.exists():
        return []

    try:
        raw_data = data_path.read_text(encoding="utf-8")
        json_value = json.loads(raw_data, object_pairs_hook=no_duplicate_keys)
    except UpdateSection7520RatesError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_JSON) from None

    if not isinstance(json_value, list):
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_JSON)

    return sorted(parse_json_record(item) for item in json_value)


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateSection7520RatesError(UpdateErrorCode.DUPLICATE_JSON_KEY)
        result[key] = value
    return result


def parse_json_record(value: object) -> Section7520RateRecord:
    if not isinstance(value, dict):
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_JSON)

    expected_keys = {
        "effective_month",
        "midterm_afr_120_basis_points",
        "section_7520_rate_basis_points",
    }
    legacy_keys = {*expected_keys, "source_url"}
    legacy_keys_with_revenue_ruling = {*expected_keys, "revenue_ruling"}
    legacy_keys_with_both = {*expected_keys, "revenue_ruling", "source_url"}
    legacy_long_keys = {
        "valuation_month",
        "applicable_federal_midterm_120_percent_basis_points",
        "section_7520_rate_basis_points",
        "revenue_ruling",
    }
    legacy_long_keys_with_source_url = {*legacy_long_keys, "source_url"}
    actual_keys = set(value.keys())
    if actual_keys not in (
        expected_keys,
        legacy_keys,
        legacy_keys_with_revenue_ruling,
        legacy_keys_with_both,
        legacy_long_keys,
        legacy_long_keys_with_source_url,
    ):
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_JSON)

    if "valuation_month" in value:
        effective_month = value["valuation_month"]
        midterm_rate = value["applicable_federal_midterm_120_percent_basis_points"]
    else:
        effective_month = value["effective_month"]
        midterm_rate = value["midterm_afr_120_basis_points"]
    section_rate = value["section_7520_rate_basis_points"]
    revenue_ruling = value.get("revenue_ruling", "")
    source_url = value.get("source_url", DEFAULT_SOURCE_URL)

    if (
        not isinstance(effective_month, str)
        or not re.match(r"^[0-9]{4}-[0-9]{2}$", effective_month)
        or not isinstance(midterm_rate, int)
        or not isinstance(section_rate, int)
        or not isinstance(revenue_ruling, str)
    ):
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_JSON)

    if not isinstance(source_url, str):
        raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_JSON)
    validate_source_url(source_url)
    if revenue_ruling != "":
        parse_revenue_ruling(revenue_ruling)

    return apply_existing_record_correction(
        Section7520RateRecord(
            effective_month=effective_month,
            midterm_afr_120_basis_points=midterm_rate,
            section_7520_rate_basis_points=section_rate,
            revenue_ruling=revenue_ruling,
            source_url=source_url,
        )
    )


def apply_existing_record_correction(
    record: Section7520RateRecord,
) -> Section7520RateRecord:
    corrected_section_rate = EXISTING_RECORD_CORRECTIONS.get(
        (
            record.effective_month,
            record.midterm_afr_120_basis_points,
            record.section_7520_rate_basis_points,
        )
    )
    if corrected_section_rate is None:
        return record

    return Section7520RateRecord(
        effective_month=record.effective_month,
        midterm_afr_120_basis_points=record.midterm_afr_120_basis_points,
        section_7520_rate_basis_points=corrected_section_rate,
        revenue_ruling=record.revenue_ruling,
        source_url=record.source_url,
    )


def merge_records(
    existing_records: list[Section7520RateRecord],
    source_records: list[Section7520RateRecord],
) -> tuple[list[Section7520RateRecord], bool]:
    merged_by_month: dict[str, Section7520RateRecord] = {}

    for record in existing_records:
        existing = merged_by_month.get(record.effective_month)
        if existing is not None and not existing.has_same_published_values(record):
            raise UpdateSection7520RatesError(UpdateErrorCode.CONFLICTING_RECORD)
        merged_by_month[record.effective_month] = record

    changed = False
    for record in source_records:
        existing = merged_by_month.get(record.effective_month)
        if existing is None:
            merged_by_month[record.effective_month] = record
            changed = True
        elif not existing.has_same_published_values(record):
            raise UpdateSection7520RatesError(UpdateErrorCode.CONFLICTING_RECORD)

    return sorted(merged_by_month.values()), changed


def serialize_records(records: list[Section7520RateRecord]) -> str:
    json_ready = [record.to_json_object() for record in sorted(records)]
    return json.dumps(json_ready, indent=2) + "\n"


def write_single_records_file(
    data_path: Path, records: list[Section7520RateRecord]
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
        raise UpdateSection7520RatesError(UpdateErrorCode.WRITE_FAILED) from None


def write_records(data_path: Path, records: list[Section7520RateRecord]) -> None:
    if not uses_year_shards(data_path):
        write_single_records_file(data_path, records)
        return

    data_path.mkdir(parents=True, exist_ok=True)
    records_by_year: dict[str, list[Section7520RateRecord]] = {}
    for record in sorted(records):
        records_by_year.setdefault(record_year(record), []).append(record)

    expected_names = {
        f"{year}-section-7520-rates.json" for year in records_by_year
    }
    try:
        for existing_path in data_path.glob("*.json"):
            if existing_path.name not in expected_names:
                existing_path.unlink()
    except OSError:
        raise UpdateSection7520RatesError(UpdateErrorCode.WRITE_FAILED) from None

    for year, year_records in records_by_year.items():
        write_single_records_file(year_shard_path(data_path, year), year_records)


def canonical_single_json_changed(
    data_path: Path, records: list[Section7520RateRecord]
) -> bool:
    if not data_path.exists():
        return True
    try:
        return data_path.read_text(encoding="utf-8") != serialize_records(records)
    except OSError:
        raise UpdateSection7520RatesError(UpdateErrorCode.WRITE_FAILED) from None


def canonical_json_changed(
    data_path: Path, records: list[Section7520RateRecord]
) -> bool:
    if not uses_year_shards(data_path):
        return canonical_single_json_changed(data_path, records)

    records_by_year: dict[str, list[Section7520RateRecord]] = {}
    for record in sorted(records):
        records_by_year.setdefault(record_year(record), []).append(record)

    expected_names = {
        f"{year}-section-7520-rates.json" for year in records_by_year
    }
    if data_path.exists():
        try:
            actual_names = {path.name for path in data_path.glob("*.json")}
        except OSError:
            raise UpdateSection7520RatesError(UpdateErrorCode.WRITE_FAILED) from None
        if actual_names != expected_names:
            return True
    elif expected_names:
        return True

    return any(
        canonical_single_json_changed(year_shard_path(data_path, year), year_records)
        for year, year_records in records_by_year.items()
    )


def update_from_html(
    html: str, source_url: str, data_path: Path, write: bool
) -> tuple[int, int, int, bool]:
    existing_records = load_existing_records(data_path)
    source_records = parse_section_7520_records(html, source_url)
    merged_records, changed = merge_records(existing_records, source_records)
    changed = changed or canonical_json_changed(data_path, merged_records)
    if write and changed:
        write_records(data_path, merged_records)
    return (len(source_records), len(existing_records), len(merged_records), changed)


def update_from_sources(
    source_urls: list[str], data_path: Path, write: bool
) -> tuple[int, int, int, bool]:
    existing_records = load_existing_records(data_path)
    source_records: list[Section7520RateRecord] = []

    for source_url in source_urls:
        html = fetch_source_html(source_url)
        source_records.extend(parse_section_7520_records(html, source_url))

    if PRIOR_YEARS_SOURCE_URL in source_urls:
        source_records.extend(static_1996_section_7520_records())

    merged_records, changed = merge_records(existing_records, sorted(source_records))
    changed = changed or canonical_json_changed(data_path, merged_records)
    if write and changed:
        write_records(data_path, merged_records)
    return (len(source_records), len(existing_records), len(merged_records), changed)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch IRS section 7520 rates and merge them into local JSON."
    )
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--input-html", type=Path)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Fetch prior-year IRS section 7520 rates in addition to the current year.",
    )
    parser.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    try:
        if args.input_html is None:
            source_urls = [args.source_url]
            if args.backfill:
                source_urls.append(PRIOR_YEARS_SOURCE_URL)
            source_count, existing_count, final_count, changed = update_from_sources(
                source_urls=source_urls,
                data_path=args.data_path,
                write=args.write,
            )
        else:
            if args.backfill:
                raise UpdateSection7520RatesError(UpdateErrorCode.INVALID_ARGUMENTS)
            html = args.input_html.read_text(encoding="utf-8")
            source_count, existing_count, final_count, changed = update_from_html(
                html=html,
                source_url=args.source_url,
                data_path=args.data_path,
                write=args.write,
            )
    except (OSError, UpdateSection7520RatesError) as error:
        if isinstance(error, UpdateSection7520RatesError):
            print(f"section7520_update_error={error.code.value}", file=sys.stderr)
        else:
            print("section7520_update_error=io_failed", file=sys.stderr)
        return 1

    print(
        "section7520_update "
        f"source_records={source_count} "
        f"existing_records={existing_count} "
        f"final_records={final_count} "
        f"changed={str(changed).lower()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
