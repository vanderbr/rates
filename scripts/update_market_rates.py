#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Update local daily Treasury and New York Fed market-rate JSON histories."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


TREASURY_START_YEAR = 1990
DEFAULT_TREASURY_MANIFEST_PATH = Path("treasury/treasury-yield-curve/manifest.json")
DEFAULT_TREASURY_YEAR_DIR = Path("treasury/treasury-yield-curve/by-year")
MAX_RESPONSE_BYTES = 20_000_000
REQUEST_TIMEOUT_SECONDS = 60
ISO_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
NYFED_DATE_PATTERN = re.compile(r"^[0-9]{2}/[0-9]{2}/[0-9]{4}$")

TREASURY_TENOR_COLUMNS = (
    ("1_month", "1 Mo"),
    ("6_week", "1.5 Month"),
    ("2_month", "2 Mo"),
    ("3_month", "3 Mo"),
    ("4_month", "4 Mo"),
    ("6_month", "6 Mo"),
    ("1_year", "1 Yr"),
    ("2_year", "2 Yr"),
    ("3_year", "3 Yr"),
    ("5_year", "5 Yr"),
    ("7_year", "7 Yr"),
    ("10_year", "10 Yr"),
    ("20_year", "20 Yr"),
    ("30_year", "30 Yr"),
)
TREASURY_TENOR_KEYS = tuple(key for key, _header in TREASURY_TENOR_COLUMNS)
TREASURY_HEADER_TO_KEY = {header: key for key, header in TREASURY_TENOR_COLUMNS}

NYFED_HEADERS = (
    "Effective Date",
    "Rate Type",
    "Rate (%)",
    "1st Percentile (%)",
    "25th Percentile (%)",
    "75th Percentile (%)",
    "99th Percentile (%)",
    "Volume ($Billions)",
    "Target Rate From (%)",
    "Target Rate To (%)",
    "Intra Day - Low (%)",
    "Intra Day - High (%)",
    "Standard Deviation (%)",
    "30-Day Average SOFR",
    "90-Day Average SOFR",
    "180-Day Average SOFR",
    "SOFR Index",
    "Revision Indicator (Y/N)",
    "Footnote ID",
)


class MarketRateUpdateErrorCode(Enum):
    BAD_SOURCE_URL = "bad_source_url"
    CONFLICTING_RECORD = "conflicting_record"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    DUPLICATE_JSON_RECORD = "duplicate_json_record"
    DUPLICATE_SOURCE_RECORD = "duplicate_source_record"
    FETCH_FAILED = "fetch_failed"
    FETCH_TOO_LARGE = "fetch_too_large"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_CSV = "invalid_csv"
    INVALID_DATE = "invalid_date"
    INVALID_JSON = "invalid_json"
    INVALID_PERCENT = "invalid_percent"
    UNKNOWN_DATASET = "unknown_dataset"
    WRITE_FAILED = "write_failed"


class MarketRateUpdateError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: MarketRateUpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class JsonRecord(Protocol):
    date: str

    def to_json_object(self) -> dict[str, object]:
        """Return a stable JSON-compatible representation."""


@dataclass(frozen=True)
class NyFedRateDataset:
    """Static metadata for one New York Fed reference-rate feed."""

    dataset_id: str
    path_slug: str
    rate_type: str
    api_group: str
    api_name: str
    start_date: date
    year_sharded: bool
    api_path: str | None = None

    @property
    def source_path(self) -> str:
        if self.api_path is not None:
            return self.api_path
        return f"/api/rates/{self.api_group}/{self.api_name}/search.csv"

    @property
    def legacy_data_path(self) -> Path:
        return Path(self.path_slug) / "rates.json"

    @property
    def manifest_path(self) -> Path:
        return Path(self.path_slug) / "manifest.json"

    @property
    def year_dir(self) -> Path:
        return Path(self.path_slug) / "by-year"

    @property
    def shard_slug(self) -> str:
        return Path(self.path_slug).name


NYFED_DATASETS = (
    NyFedRateDataset(
        dataset_id="federal-funds",
        path_slug="fed-funds",
        rate_type="EFFR",
        api_group="unsecured",
        api_name="effr",
        start_date=date(2000, 7, 3),
        year_sharded=True,
    ),
    NyFedRateDataset(
        dataset_id="sofr",
        path_slug="sofr",
        rate_type="SOFR",
        api_group="secured",
        api_name="sofr",
        start_date=date(2018, 4, 2),
        year_sharded=True,
        api_path="/api/rates/all/search.csv",
    ),
)
NYFED_DATASET_BY_ID = {dataset.dataset_id: dataset for dataset in NYFED_DATASETS}
NYFED_SOURCE_BY_PATH = {dataset.source_path: dataset for dataset in NYFED_DATASETS}
DATASET_CHOICES = (
    "all",
    "treasury-yield-curve",
    "ny-fed-reference-rates",
    *(dataset.dataset_id for dataset in NYFED_DATASETS),
)


@dataclass(frozen=True, order=True)
class TreasuryYieldCurveRecord:
    """One official Treasury par yield curve observation."""

    date: str
    par_yields_basis_points: dict[str, int | None]
    source_url: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "date": self.date,
            "par_yields_basis_points": [
                self.par_yields_basis_points[key] for key in TREASURY_TENOR_KEYS
            ],
        }

    def has_same_published_values(self, other: "TreasuryYieldCurveRecord") -> bool:
        return (
            self.date == other.date
            and self.par_yields_basis_points == other.par_yields_basis_points
        )


@dataclass(frozen=True, order=True)
class NyFedReferenceRateRecord:
    """One New York Fed overnight reference-rate observation."""

    date: str
    rate_type: str
    rate_basis_points: int | None
    source_url: str
    percentile_1_basis_points: int | None = None
    percentile_25_basis_points: int | None = None
    percentile_75_basis_points: int | None = None
    percentile_99_basis_points: int | None = None
    volume_billions: int | None = None
    average_30_day_basis_points_scaled_1000: int | None = None
    average_90_day_basis_points_scaled_1000: int | None = None
    average_180_day_basis_points_scaled_1000: int | None = None
    sofr_index_scaled_100000000: int | None = None
    revision_indicator: str | None = None
    footnote_id: str | None = None

    def to_json_object(self) -> dict[str, object]:
        result: dict[str, object] = {
            "date": self.date,
            "rate_basis_points": self.rate_basis_points,
        }
        if self.has_sofr_detail_fields():
            result.update(
                {
                    "percentile_1_basis_points": self.percentile_1_basis_points,
                    "percentile_25_basis_points": self.percentile_25_basis_points,
                    "percentile_75_basis_points": self.percentile_75_basis_points,
                    "percentile_99_basis_points": self.percentile_99_basis_points,
                    "volume_billions": self.volume_billions,
                    "average_30_day_basis_points_scaled_1000": (
                        self.average_30_day_basis_points_scaled_1000
                    ),
                    "average_90_day_basis_points_scaled_1000": (
                        self.average_90_day_basis_points_scaled_1000
                    ),
                    "average_180_day_basis_points_scaled_1000": (
                        self.average_180_day_basis_points_scaled_1000
                    ),
                    "sofr_index_scaled_100000000": self.sofr_index_scaled_100000000,
                }
            )
        return result

    def has_same_published_values(self, other: "NyFedReferenceRateRecord") -> bool:
        return (
            self.date == other.date
            and self.rate_type == other.rate_type
            and self.rate_basis_points == other.rate_basis_points
            and self.percentile_1_basis_points == other.percentile_1_basis_points
            and self.percentile_25_basis_points == other.percentile_25_basis_points
            and self.percentile_75_basis_points == other.percentile_75_basis_points
            and self.percentile_99_basis_points == other.percentile_99_basis_points
            and self.volume_billions == other.volume_billions
            and self.average_30_day_basis_points_scaled_1000
            == other.average_30_day_basis_points_scaled_1000
            and self.average_90_day_basis_points_scaled_1000
            == other.average_90_day_basis_points_scaled_1000
            and self.average_180_day_basis_points_scaled_1000
            == other.average_180_day_basis_points_scaled_1000
            and self.sofr_index_scaled_100000000
            == other.sofr_index_scaled_100000000
        )

    def can_be_enriched_by(self, other: "NyFedReferenceRateRecord") -> bool:
        if self.date != other.date or self.rate_type != other.rate_type:
            return False

        found_new_value = False
        for existing_value, source_value in (
            (self.rate_basis_points, other.rate_basis_points),
            (self.percentile_1_basis_points, other.percentile_1_basis_points),
            (self.percentile_25_basis_points, other.percentile_25_basis_points),
            (self.percentile_75_basis_points, other.percentile_75_basis_points),
            (self.percentile_99_basis_points, other.percentile_99_basis_points),
            (self.volume_billions, other.volume_billions),
            (
                self.average_30_day_basis_points_scaled_1000,
                other.average_30_day_basis_points_scaled_1000,
            ),
            (
                self.average_90_day_basis_points_scaled_1000,
                other.average_90_day_basis_points_scaled_1000,
            ),
            (
                self.average_180_day_basis_points_scaled_1000,
                other.average_180_day_basis_points_scaled_1000,
            ),
            (self.sofr_index_scaled_100000000, other.sofr_index_scaled_100000000),
        ):
            if existing_value is None and source_value is not None:
                found_new_value = True
            elif existing_value is not None and existing_value != source_value:
                return False

        return found_new_value

    def has_sofr_detail_fields(self) -> bool:
        return any(
            value is not None
            for value in (
                self.percentile_1_basis_points,
                self.percentile_25_basis_points,
                self.percentile_75_basis_points,
                self.percentile_99_basis_points,
                self.volume_billions,
                self.average_30_day_basis_points_scaled_1000,
                self.average_90_day_basis_points_scaled_1000,
                self.average_180_day_basis_points_scaled_1000,
                self.sofr_index_scaled_100000000,
            )
        )


@dataclass(frozen=True, order=True)
class DerivedMetricRecord:
    """One generated single-metric record derived from a canonical dataset."""

    date: str
    metric_field: str
    metric_value: int

    def to_json_object(self) -> dict[str, object]:
        return {"date": self.date, self.metric_field: self.metric_value}


@dataclass(frozen=True)
class SofrDerivedDataset:
    """Generated SOFR metric view for consumers that need direct lookup."""

    dataset_id: str
    path_slug: str
    metric_field: str

    @property
    def manifest_path(self) -> Path:
        return Path(self.path_slug) / "manifest.json"

    @property
    def metadata_path(self) -> Path:
        return Path(self.path_slug) / "metadata.json"

    @property
    def year_dir(self) -> Path:
        return Path(self.path_slug) / "by-year"

    @property
    def shard_slug(self) -> str:
        return Path(self.path_slug).name


SOFR_DERIVED_DATASETS = (
    SofrDerivedDataset(
        dataset_id="sofr-30d-average",
        path_slug="sofr/sofr-30d-average",
        metric_field="average_30_day_basis_points_scaled_1000",
    ),
    SofrDerivedDataset(
        dataset_id="sofr-90d-average",
        path_slug="sofr/sofr-90d-average",
        metric_field="average_90_day_basis_points_scaled_1000",
    ),
    SofrDerivedDataset(
        dataset_id="sofr-180d-average",
        path_slug="sofr/sofr-180d-average",
        metric_field="average_180_day_basis_points_scaled_1000",
    ),
)


def build_treasury_source_url(year: int) -> str:
    if year < TREASURY_START_YEAR or year > current_year():
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_ARGUMENTS)

    query = urlencode(
        {
            "field_tdr_date_value": str(year),
            "type": "daily_treasury_yield_curve",
            "page": "",
            "_format": "csv",
        }
    )
    return (
        "https://home.treasury.gov/resource-center/data-chart-center/"
        f"interest-rates/daily-treasury-rates.csv/{year}/all?{query}"
    )


def build_nyfed_source_url(dataset: NyFedRateDataset, end_date: date) -> str:
    if end_date < dataset.start_date:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_ARGUMENTS)

    query = urlencode(
        {
            "startDate": dataset.start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }
    )
    return (
        "https://markets.newyorkfed.org"
        f"{dataset.source_path}?{query}"
    )


def current_year() -> int:
    return datetime.now(UTC).year


def current_utc_date() -> date:
    return datetime.now(UTC).date()


def validate_treasury_source_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "home.treasury.gov"
        or not parsed.path.startswith(
            "/resource-center/data-chart-center/interest-rates/"
            "daily-treasury-rates.csv/"
        )
        or query.get("type") != ["daily_treasury_yield_curve"]
        or query.get("_format") != ["csv"]
    ):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.BAD_SOURCE_URL)


def validate_nyfed_source_url(source_url: str, dataset: NyFedRateDataset) -> None:
    parsed = urlparse(source_url)
    query = parse_qs(parsed.query)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "markets.newyorkfed.org"
        or parsed.path != dataset.source_path
        or "startDate" not in query
        or "endDate" not in query
    ):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.BAD_SOURCE_URL)

    if len(query["startDate"]) != 1 or len(query["endDate"]) != 1:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.BAD_SOURCE_URL)

    start_date = date.fromisoformat(parse_iso_date(query["startDate"][0]))
    end_date = date.fromisoformat(parse_iso_date(query["endDate"][0]))
    if start_date < dataset.start_date or start_date > end_date:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.BAD_SOURCE_URL)


def infer_nyfed_dataset_from_source_url(source_url: str) -> NyFedRateDataset:
    parsed = urlparse(source_url)
    dataset = NYFED_SOURCE_BY_PATH.get(parsed.path)
    if dataset is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.BAD_SOURCE_URL)
    return dataset


def fetch_text(source_url: str) -> str:
    request = Request(
        source_url,
        headers={"User-Agent": "vanderbr-rates-updater/1.0"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_RESPONSE_BYTES:
                raise MarketRateUpdateError(MarketRateUpdateErrorCode.FETCH_TOO_LARGE)
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, OSError, URLError, ValueError):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.FETCH_FAILED) from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.FETCH_TOO_LARGE)

    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.FETCH_FAILED) from None


def parse_treasury_csv(
    csv_text: str, source_url: str
) -> list[TreasuryYieldCurveRecord]:
    validate_treasury_source_url(source_url)
    reader = csv.DictReader(StringIO(csv_text))
    fieldnames = normalize_csv_fieldnames(reader.fieldnames)
    validate_treasury_headers(fieldnames)
    reader.fieldnames = fieldnames

    records: list[TreasuryYieldCurveRecord] = []
    seen_dates: set[str] = set()
    for row in reader:
        if None in row:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
        if is_blank_row(row):
            continue

        record_date = parse_treasury_date(get_required_csv_value(row, "Date"))
        if record_date in seen_dates:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_SOURCE_RECORD)

        rates: dict[str, int | None] = {}
        for key, header in TREASURY_TENOR_COLUMNS:
            rates[key] = parse_percent_basis_points(row.get(header, ""))

        records.append(
            TreasuryYieldCurveRecord(
                date=record_date,
                par_yields_basis_points=rates,
                source_url=source_url,
            )
        )
        seen_dates.add(record_date)

    if len(records) == 0:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return sorted(records)


def parse_nyfed_csv(
    csv_text: str,
    source_url: str,
    dataset: NyFedRateDataset,
) -> list[NyFedReferenceRateRecord]:
    validate_nyfed_source_url(source_url, dataset)
    reader = csv.DictReader(StringIO(csv_text))
    fieldnames = normalize_csv_fieldnames(reader.fieldnames)
    validate_nyfed_headers(fieldnames)
    reader.fieldnames = fieldnames

    if dataset.dataset_id == "sofr":
        return parse_sofr_csv_rows(reader, source_url)

    records: list[NyFedReferenceRateRecord] = []
    seen_dates: set[str] = set()
    for row in reader:
        if None in row:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
        if is_blank_row(row):
            continue

        record_date = parse_nyfed_date(get_required_csv_value(row, "Effective Date"))
        if record_date in seen_dates:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_SOURCE_RECORD)

        rate_type = get_required_csv_value(row, "Rate Type")
        if rate_type != dataset.rate_type:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)

        records.append(
            NyFedReferenceRateRecord(
                date=record_date,
                rate_type=rate_type,
                rate_basis_points=parse_percent_basis_points(
                    get_required_csv_value(row, "Rate (%)")
                ),
                source_url=source_url,
            )
        )
        seen_dates.add(record_date)

    if len(records) == 0:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return sorted(records)


def parse_sofr_csv_rows(
    rows: csv.DictReader[str],
    source_url: str,
) -> list[NyFedReferenceRateRecord]:
    records_by_date: dict[str, NyFedReferenceRateRecord] = {}
    seen_source_rows: set[tuple[str, str]] = set()

    for row in rows:
        if None in row:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
        if is_blank_row(row):
            continue

        record_date = parse_nyfed_date(get_required_csv_value(row, "Effective Date"))
        rate_type = get_required_csv_value(row, "Rate Type")
        if rate_type not in {"SOFR", "SOFRAI"}:
            continue

        source_key = (record_date, rate_type)
        if source_key in seen_source_rows:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_SOURCE_RECORD)
        seen_source_rows.add(source_key)

        if rate_type == "SOFR":
            partial_record = parse_sofr_observation_row(row, record_date, source_url)
        else:
            partial_record = parse_sofr_average_index_row(row, record_date, source_url)

        existing_record = records_by_date.get(record_date)
        if existing_record is None:
            records_by_date[record_date] = partial_record
        else:
            records_by_date[record_date] = combine_sofr_records(
                existing_record, partial_record
            )

    if len(records_by_date) == 0:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return sorted(records_by_date.values())


def parse_sofr_observation_row(
    row: dict[str | None, str | None],
    record_date: str,
    source_url: str,
) -> NyFedReferenceRateRecord:
    return NyFedReferenceRateRecord(
        date=record_date,
        rate_type="SOFR",
        rate_basis_points=parse_percent_basis_points(
            get_required_csv_value(row, "Rate (%)")
        ),
        source_url=source_url,
        percentile_1_basis_points=parse_percent_basis_points_signed(
            get_required_csv_value(row, "1st Percentile (%)")
        ),
        percentile_25_basis_points=parse_percent_basis_points_signed(
            get_required_csv_value(row, "25th Percentile (%)")
        ),
        percentile_75_basis_points=parse_percent_basis_points_signed(
            get_required_csv_value(row, "75th Percentile (%)")
        ),
        percentile_99_basis_points=parse_percent_basis_points_signed(
            get_required_csv_value(row, "99th Percentile (%)")
        ),
        volume_billions=parse_nonnegative_integer(
            get_required_csv_value(row, "Volume ($Billions)")
        ),
        revision_indicator=parse_revision_indicator(
            get_required_csv_value(row, "Revision Indicator (Y/N)")
        ),
        footnote_id=parse_optional_identifier(get_required_csv_value(row, "Footnote ID")),
    )


def parse_sofr_average_index_row(
    row: dict[str | None, str | None],
    record_date: str,
    source_url: str,
) -> NyFedReferenceRateRecord:
    return NyFedReferenceRateRecord(
        date=record_date,
        rate_type="SOFR",
        rate_basis_points=None,
        source_url=source_url,
        average_30_day_basis_points_scaled_1000=parse_percent_basis_points_scaled(
            get_required_csv_value(row, "30-Day Average SOFR"),
            1000,
        ),
        average_90_day_basis_points_scaled_1000=parse_percent_basis_points_scaled(
            get_required_csv_value(row, "90-Day Average SOFR"),
            1000,
        ),
        average_180_day_basis_points_scaled_1000=parse_percent_basis_points_scaled(
            get_required_csv_value(row, "180-Day Average SOFR"),
            1000,
        ),
        sofr_index_scaled_100000000=parse_decimal_scaled(
            get_required_csv_value(row, "SOFR Index"),
            100_000_000,
        ),
        revision_indicator=parse_revision_indicator(
            get_required_csv_value(row, "Revision Indicator (Y/N)")
        ),
        footnote_id=parse_optional_identifier(get_required_csv_value(row, "Footnote ID")),
    )


def combine_sofr_records(
    first: NyFedReferenceRateRecord,
    second: NyFedReferenceRateRecord,
) -> NyFedReferenceRateRecord:
    if first.date != second.date or first.rate_type != "SOFR" or second.rate_type != "SOFR":
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)

    return NyFedReferenceRateRecord(
        date=first.date,
        rate_type="SOFR",
        rate_basis_points=combine_optional_values(
            first.rate_basis_points, second.rate_basis_points
        ),
        source_url=first.source_url,
        percentile_1_basis_points=combine_optional_values(
            first.percentile_1_basis_points, second.percentile_1_basis_points
        ),
        percentile_25_basis_points=combine_optional_values(
            first.percentile_25_basis_points, second.percentile_25_basis_points
        ),
        percentile_75_basis_points=combine_optional_values(
            first.percentile_75_basis_points, second.percentile_75_basis_points
        ),
        percentile_99_basis_points=combine_optional_values(
            first.percentile_99_basis_points, second.percentile_99_basis_points
        ),
        volume_billions=combine_optional_values(
            first.volume_billions, second.volume_billions
        ),
        average_30_day_basis_points_scaled_1000=combine_optional_values(
            first.average_30_day_basis_points_scaled_1000,
            second.average_30_day_basis_points_scaled_1000,
        ),
        average_90_day_basis_points_scaled_1000=combine_optional_values(
            first.average_90_day_basis_points_scaled_1000,
            second.average_90_day_basis_points_scaled_1000,
        ),
        average_180_day_basis_points_scaled_1000=combine_optional_values(
            first.average_180_day_basis_points_scaled_1000,
            second.average_180_day_basis_points_scaled_1000,
        ),
        sofr_index_scaled_100000000=combine_optional_values(
            first.sofr_index_scaled_100000000,
            second.sofr_index_scaled_100000000,
        ),
        revision_indicator=combine_optional_values(
            first.revision_indicator, second.revision_indicator
        ),
        footnote_id=combine_optional_values(first.footnote_id, second.footnote_id),
    )


def combine_optional_values[ValueT](
    first: ValueT | None,
    second: ValueT | None,
) -> ValueT | None:
    if first is None:
        return second
    if second is None or first == second:
        return first
    raise MarketRateUpdateError(MarketRateUpdateErrorCode.CONFLICTING_RECORD)


def normalize_csv_fieldnames(fieldnames: list[str] | None) -> list[str]:
    if fieldnames is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return [fieldname.strip().removeprefix("\ufeff") for fieldname in fieldnames]


def validate_treasury_headers(fieldnames: list[str]) -> None:
    allowed = {"Date", *TREASURY_HEADER_TO_KEY.keys()}
    if "Date" not in fieldnames or len(set(fieldnames)) != len(fieldnames):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    if any(fieldname not in allowed for fieldname in fieldnames):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)


def validate_nyfed_headers(fieldnames: list[str]) -> None:
    if tuple(fieldnames) != NYFED_HEADERS:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)


def is_blank_row(row: dict[str | None, str | None]) -> bool:
    return all(value is None or value.strip() == "" for value in row.values())


def get_required_csv_value(row: dict[str | None, str | None], key: str) -> str:
    value = row.get(key)
    if value is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return value


def parse_treasury_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except ValueError:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_DATE) from None
    return parsed.isoformat()


def parse_iso_date(value: str) -> str:
    candidate = value.strip()
    if ISO_DATE_PATTERN.fullmatch(candidate) is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_DATE)
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_DATE) from None


def parse_nyfed_date(value: str) -> str:
    candidate = value.strip()
    if NYFED_DATE_PATTERN.fullmatch(candidate) is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_DATE)
    try:
        return datetime.strptime(candidate, "%m/%d/%Y").date().isoformat()
    except ValueError:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_DATE) from None


def parse_percent_basis_points(value: str | None) -> int | None:
    if value is None:
        return None

    normalized = value.strip()
    if normalized in {"", ".", "N/A", "NA"}:
        return None

    try:
        percent = Decimal(normalized)
    except InvalidOperation:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT) from None

    basis_points = percent * Decimal(100)
    if basis_points != basis_points.to_integral_value():
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)
    if basis_points < 0 or basis_points > Decimal(100_000):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)

    return int(basis_points)


def parse_percent_basis_points_signed(value: str | None) -> int | None:
    normalized = normalize_optional_decimal(value)
    if normalized is None:
        return None

    try:
        percent = Decimal(normalized)
    except InvalidOperation:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT) from None

    basis_points = percent * Decimal(100)
    if basis_points != basis_points.to_integral_value():
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)
    if basis_points < Decimal(-100_000) or basis_points > Decimal(100_000):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)

    return int(basis_points)


def parse_percent_basis_points_scaled(value: str | None, scale: int) -> int | None:
    normalized = normalize_optional_decimal(value)
    if normalized is None:
        return None

    try:
        percent = Decimal(normalized)
    except InvalidOperation:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT) from None

    scaled_basis_points = percent * Decimal(100) * Decimal(scale)
    if scaled_basis_points != scaled_basis_points.to_integral_value():
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)
    if scaled_basis_points < 0 or scaled_basis_points > Decimal(100_000 * scale):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)

    return int(scaled_basis_points)


def parse_decimal_scaled(value: str | None, scale: int) -> int | None:
    normalized = normalize_optional_decimal(value)
    if normalized is None:
        return None

    try:
        decimal_value = Decimal(normalized)
    except InvalidOperation:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT) from None

    scaled_value = decimal_value * Decimal(scale)
    if scaled_value != scaled_value.to_integral_value():
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)
    if scaled_value < 0 or scaled_value > Decimal(100_000 * scale):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_PERCENT)

    return int(scaled_value)


def normalize_optional_decimal(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized in {"", ".", "N/A", "NA"}:
        return None
    return normalized


def parse_nonnegative_integer(value: str | None) -> int | None:
    normalized = normalize_optional_decimal(value)
    if normalized is None:
        return None
    if re.fullmatch(r"[0-9]+", normalized) is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    parsed = int(normalized)
    if parsed > 10_000_000:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return parsed


def parse_revision_indicator(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    if normalized not in {"Y", "N"}:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return normalized


def parse_optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", normalized) is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_CSV)
    return normalized


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_JSON_KEY)
        result[key] = value
    return result


def load_treasury_records(
    year_dir: Path, legacy_data_path: Path
) -> list[TreasuryYieldCurveRecord]:
    records = load_year_sharded_records(
        year_dir, "treasury-yield-curve", parse_treasury_json_record, ()
    )
    if len(records) > 0 or not legacy_data_path.exists():
        return records

    json_value = load_json_array(legacy_data_path)
    return sorted(parse_treasury_json_record(item) for item in json_value)


def load_nyfed_records(
    year_dir: Path,
    legacy_data_path: Path,
    dataset: NyFedRateDataset,
) -> list[NyFedReferenceRateRecord]:
    records = load_year_sharded_records(
        year_dir,
        dataset.shard_slug,
        lambda item: parse_nyfed_json_record(item, dataset),
        (dataset.dataset_id,),
    )
    if len(records) > 0 or not legacy_data_path.exists():
        return records

    json_value = load_json_array(legacy_data_path)
    return sorted(parse_nyfed_json_record(item, dataset) for item in json_value)


def load_year_sharded_records[RecordT: JsonRecord](
    year_dir: Path,
    dataset_slug: str,
    parse_record: Callable[[object], RecordT],
    legacy_dataset_slugs: tuple[str, ...],
) -> list[RecordT]:
    if not year_dir.exists():
        return []

    records: list[RecordT] = []
    for shard_path in sorted(year_dir.glob("*.json")):
        shard_year = year_from_shard_path(
            shard_path, dataset_slug, legacy_dataset_slugs
        )
        if shard_year is None:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        json_value = load_json_array(shard_path)
        for item in json_value:
            record = parse_record(item)
            if record.date[:4] != shard_year:
                raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
            records.append(record)
    return sorted(records)


def load_json_array(data_path: Path) -> list[object]:
    if not data_path.exists():
        return []

    try:
        raw_data = data_path.read_text(encoding="utf-8")
        json_value = json.loads(raw_data, object_pairs_hook=no_duplicate_keys)
    except MarketRateUpdateError:
        raise
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON) from None

    if not isinstance(json_value, list):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
    return json_value


def parse_treasury_json_record(value: object) -> TreasuryYieldCurveRecord:
    if not isinstance(value, dict):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)

    expected_keys = {"date", "par_yields_basis_points"}
    legacy_keys = {*expected_keys, "source_url"}
    actual_keys = set(value.keys())
    if actual_keys != expected_keys and actual_keys != legacy_keys:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)

    record_date = value["date"]
    rates = value["par_yields_basis_points"]
    source_url = value.get("source_url", "")
    if (
        not isinstance(record_date, str)
        or not isinstance(rates, (dict, list))
        or not isinstance(source_url, str)
    ):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)

    parse_iso_date(record_date)
    if source_url != "":
        validate_treasury_source_url(source_url)
    parsed_rates: dict[str, int | None] = {}
    if isinstance(rates, list):
        if len(rates) != len(TREASURY_TENOR_KEYS):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        iterable_rates = zip(TREASURY_TENOR_KEYS, rates, strict=True)
    else:
        if set(rates.keys()) != set(TREASURY_TENOR_KEYS):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        iterable_rates = ((key, rates[key]) for key in TREASURY_TENOR_KEYS)

    for key, rate in iterable_rates:
        if rate is not None and (not isinstance(rate, int) or rate < 0 or rate > 100_000):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        parsed_rates[key] = rate

    return TreasuryYieldCurveRecord(
        date=record_date,
        par_yields_basis_points=parsed_rates,
        source_url=source_url,
    )


def parse_nyfed_json_record(
    value: object,
    dataset: NyFedRateDataset,
) -> NyFedReferenceRateRecord:
    if not isinstance(value, dict):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)

    # Older generated federal-funds files used a dataset-specific key. Accept it
    # here so the updater can migrate existing JSON into the shared NY Fed shape.
    legacy_fed_funds_keys = {
        "date",
        "effective_federal_funds_rate_basis_points",
    }
    legacy_fed_funds_keys_with_source = {*legacy_fed_funds_keys, "source_url"}
    actual_keys = set(value.keys())
    if (
        dataset.dataset_id == "federal-funds"
        and (
            actual_keys == legacy_fed_funds_keys
            or actual_keys == legacy_fed_funds_keys_with_source
        )
    ):
        record_date = value["date"]
        rate = value["effective_federal_funds_rate_basis_points"]
        source_url = value.get("source_url", "")
        if (
            not isinstance(record_date, str)
            or (rate is not None and not isinstance(rate, int))
            or not isinstance(source_url, str)
        ):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        parse_iso_date(record_date)
        if rate is not None and (rate < 0 or rate > 100_000):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        return NyFedReferenceRateRecord(
            date=record_date,
            rate_type=dataset.rate_type,
            rate_basis_points=rate,
            source_url=source_url,
        )

    expected_keys = {"date", "rate_basis_points"}
    legacy_expected_keys = {*expected_keys, "rate_type"}
    canonical_sofr_detail_keys = {
        *expected_keys,
        "percentile_1_basis_points",
        "percentile_25_basis_points",
        "percentile_75_basis_points",
        "percentile_99_basis_points",
        "volume_billions",
        "average_30_day_basis_points_scaled_1000",
        "average_90_day_basis_points_scaled_1000",
        "average_180_day_basis_points_scaled_1000",
        "sofr_index_scaled_100000000",
    }
    legacy_sofr_detail_keys = {
        *legacy_expected_keys,
        "percentiles_basis_points",
        "volume_billions",
        "average_30_day_basis_points_scaled_1000",
        "average_90_day_basis_points_scaled_1000",
        "average_180_day_basis_points_scaled_1000",
        "sofr_index_scaled_100000000",
        "revision_indicator",
        "footnote_id",
    }
    legacy_keys = {*legacy_expected_keys, "source_url"}
    canonical_keys_with_source = {*expected_keys, "source_url"}
    canonical_sofr_detail_keys_with_source = {
        *canonical_sofr_detail_keys,
        "source_url",
    }
    legacy_sofr_detail_keys_with_source = {*legacy_sofr_detail_keys, "source_url"}
    actual_keys = set(value.keys())
    if (
        actual_keys != expected_keys
        and actual_keys != legacy_expected_keys
        and actual_keys != legacy_keys
        and actual_keys != canonical_keys_with_source
        and actual_keys != canonical_sofr_detail_keys
        and actual_keys != canonical_sofr_detail_keys_with_source
        and actual_keys != legacy_sofr_detail_keys
        and actual_keys != legacy_sofr_detail_keys_with_source
    ):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)

    record_date = value["date"]
    rate_type = value.get("rate_type", dataset.rate_type)
    rate = value["rate_basis_points"]
    source_url = value.get("source_url", "")
    if (
        not isinstance(record_date, str)
        or not isinstance(rate_type, str)
        or (rate is not None and not isinstance(rate, int))
        or not isinstance(source_url, str)
    ):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)

    parse_iso_date(record_date)
    if source_url != "":
        validate_nyfed_source_url(source_url, infer_nyfed_dataset_from_source_url(source_url))
    if rate_type != dataset.rate_type:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
    if rate is not None and (rate < 0 or rate > 100_000):
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)

    base_key_sets = (
        expected_keys,
        legacy_expected_keys,
        legacy_keys,
        canonical_keys_with_source,
    )
    if actual_keys in base_key_sets:
        return NyFedReferenceRateRecord(
            date=record_date,
            rate_type=rate_type,
            rate_basis_points=rate,
            source_url=source_url,
        )

    if dataset.dataset_id != "sofr":
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
    if "percentiles_basis_points" in value:
        percentiles = value["percentiles_basis_points"]
        if not isinstance(percentiles, dict) or set(percentiles.keys()) != {
            "1",
            "25",
            "75",
            "99",
        }:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        percentile_1 = percentiles["1"]
        percentile_25 = percentiles["25"]
        percentile_75 = percentiles["75"]
        percentile_99 = percentiles["99"]
        revision_indicator = value["revision_indicator"]
        footnote_id = value["footnote_id"]
        if revision_indicator is not None and revision_indicator not in {"Y", "N"}:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        if footnote_id is not None and not isinstance(footnote_id, str):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        if isinstance(footnote_id, str):
            parse_optional_identifier(footnote_id)
    else:
        percentile_1 = value["percentile_1_basis_points"]
        percentile_25 = value["percentile_25_basis_points"]
        percentile_75 = value["percentile_75_basis_points"]
        percentile_99 = value["percentile_99_basis_points"]
        revision_indicator = None
        footnote_id = None

    return NyFedReferenceRateRecord(
        date=record_date,
        rate_type=rate_type,
        rate_basis_points=rate,
        source_url=source_url,
        percentile_1_basis_points=parse_json_optional_int(percentile_1, -100_000, 100_000),
        percentile_25_basis_points=parse_json_optional_int(percentile_25, -100_000, 100_000),
        percentile_75_basis_points=parse_json_optional_int(percentile_75, -100_000, 100_000),
        percentile_99_basis_points=parse_json_optional_int(percentile_99, -100_000, 100_000),
        volume_billions=parse_json_optional_int(value["volume_billions"], 0, 10_000_000),
        average_30_day_basis_points_scaled_1000=parse_json_optional_int(
            value["average_30_day_basis_points_scaled_1000"],
            0,
            100_000_000,
        ),
        average_90_day_basis_points_scaled_1000=parse_json_optional_int(
            value["average_90_day_basis_points_scaled_1000"],
            0,
            100_000_000,
        ),
        average_180_day_basis_points_scaled_1000=parse_json_optional_int(
            value["average_180_day_basis_points_scaled_1000"],
            0,
            100_000_000,
        ),
        sofr_index_scaled_100000000=parse_json_optional_int(
            value["sofr_index_scaled_100000000"],
            0,
            100_000_000_000_000,
        ),
        revision_indicator=revision_indicator,
        footnote_id=footnote_id,
    )


def parse_json_optional_int(value: object, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < minimum or value > maximum:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
    return value


def merge_treasury_records(
    existing_records: list[TreasuryYieldCurveRecord],
    source_records: list[TreasuryYieldCurveRecord],
) -> tuple[list[TreasuryYieldCurveRecord], bool]:
    return merge_records(existing_records, source_records)


def merge_nyfed_records(
    existing_records: list[NyFedReferenceRateRecord],
    source_records: list[NyFedReferenceRateRecord],
) -> tuple[list[NyFedReferenceRateRecord], bool]:
    merged_by_date: dict[str, NyFedReferenceRateRecord] = {}

    for record in existing_records:
        existing = merged_by_date.get(record.date)
        if existing is not None:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_JSON_RECORD)
        merged_by_date[record.date] = record

    source_dates: set[str] = set()
    changed = False
    for record in source_records:
        if record.date in source_dates:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_SOURCE_RECORD)
        source_dates.add(record.date)
        existing = merged_by_date.get(record.date)
        if existing is None:
            merged_by_date[record.date] = record
            changed = True
        elif existing.has_same_published_values(record):
            continue
        elif existing.can_be_enriched_by(record):
            merged_by_date[record.date] = record
            changed = True
        else:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.CONFLICTING_RECORD)

    return sorted(merged_by_date.values()), changed


def merge_records[
    RecordT: TreasuryYieldCurveRecord | NyFedReferenceRateRecord
](
    existing_records: list[RecordT],
    source_records: list[RecordT],
) -> tuple[list[RecordT], bool]:
    merged_by_date: dict[str, RecordT] = {}

    for record in existing_records:
        existing = merged_by_date.get(record.date)
        if existing is not None:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_JSON_RECORD)
        merged_by_date[record.date] = record

    source_dates: set[str] = set()
    changed = False
    for record in source_records:
        if record.date in source_dates:
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.DUPLICATE_SOURCE_RECORD)
        source_dates.add(record.date)
        existing = merged_by_date.get(record.date)
        if existing is None:
            merged_by_date[record.date] = record
            changed = True
        elif not existing.has_same_published_values(record):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.CONFLICTING_RECORD)

    return sorted(merged_by_date.values()), changed


def serialize_records(records: list[JsonRecord]) -> str:
    return json.dumps(
        [record.to_json_object() for record in sorted(records)], indent=2
    ) + "\n"


def serialize_records_by_year(records: list[JsonRecord]) -> dict[str, str]:
    grouped: dict[str, list[JsonRecord]] = {}
    for record in sorted(records):
        year = record.date[:4]
        grouped.setdefault(year, []).append(record)
    return {year: serialize_records(year_records) for year, year_records in grouped.items()}


def write_year_sharded_dataset_files(
    dataset_id: str,
    shard_slug: str,
    manifest_path: Path,
    year_dir: Path,
    records: list[JsonRecord],
) -> bool:
    by_year = serialize_records_by_year(records)
    changed = write_if_changed(
        manifest_path, serialize_year_sharded_manifest(dataset_id, shard_slug, records)
    )
    for year, serialized in by_year.items():
        year_path = year_dir / year_shard_filename(shard_slug, year)
        changed = write_if_changed(year_path, serialized) or changed
    expected_filenames = {
        year_shard_filename(shard_slug, year) for year in by_year.keys()
    }
    changed = remove_stale_year_shards(year_dir, expected_filenames) or changed
    return changed


def write_sofr_derived_datasets(
    records: list[NyFedReferenceRateRecord],
    sofr_root: Path,
) -> bool:
    changed = False
    for dataset in SOFR_DERIVED_DATASETS:
        dataset_dir = sofr_root / dataset.shard_slug
        metric_records = derive_sofr_metric_records(records, dataset.metric_field)
        changed = write_if_changed(
            dataset_dir / "metadata.json",
            serialize_sofr_derived_metadata(dataset),
        ) or changed
        changed = write_year_sharded_dataset_files(
            dataset.dataset_id,
            dataset.shard_slug,
            dataset_dir / "manifest.json",
            dataset_dir / "by-year",
            metric_records,
        ) or changed
    return changed


def derive_sofr_metric_records(
    records: list[NyFedReferenceRateRecord],
    metric_field: str,
) -> list[DerivedMetricRecord]:
    metric_records: list[DerivedMetricRecord] = []
    for record in records:
        metric_value = getattr(record, metric_field)
        if metric_value is None:
            continue
        if not isinstance(metric_value, int):
            raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_JSON)
        metric_records.append(
            DerivedMetricRecord(
                date=record.date,
                metric_field=metric_field,
                metric_value=metric_value,
            )
        )
    return sorted(metric_records)


def serialize_sofr_derived_metadata(dataset: SofrDerivedDataset) -> str:
    metadata = {
        "name": sofr_derived_dataset_name(dataset.dataset_id),
        "source": "Federal Reserve Bank of New York",
        "source_url": "https://markets.newyorkfed.org/api/rates/all/search.csv",
        "derived_from": "../by-year/YYYY-sofr.json",
        "record_frequency": "business_day",
        "date_key": {
            "field": "date",
            "format": "YYYY-MM-DD",
            "meaning": "Effective date of the New York Fed-published SOFR average",
        },
        "value_fields": {
            dataset.metric_field: {
                "unit": "basis_points_scaled_1000",
                "scale": "stored value / 1000 = basis points",
                "source_rate_expression": "annualized_percent",
            }
        },
        "storage": {
            "manifest_file": "manifest.json",
            "primary_records": f"by-year/YYYY-{dataset.shard_slug}.json",
            "year_shards": f"by-year/YYYY-{dataset.shard_slug}.json",
            "ordering": "ascending_date",
            "dedupe_key": "date",
        },
    }
    return json.dumps(metadata, indent=2) + "\n"


def serialize_treasury_metadata() -> str:
    metadata = {
        "name": "Daily Treasury Yield Curve Rates",
        "source": "United States Department of the Treasury",
        "source_url_template": (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/daily-treasury-rates.csv/{year}/all?"
            "field_tdr_date_value={year}&type=daily_treasury_yield_curve&"
            "page=&_format=csv"
        ),
        "record_frequency": "business_day",
        "date_key": {
            "field": "date",
            "format": "YYYY-MM-DD",
            "meaning": "Treasury-published rate date",
        },
        "value_fields": {
            "par_yields_basis_points": {
                "unit": "basis_points",
                "shape": "fixed array ordered by metadata.tenors",
                "tenors": list(TREASURY_TENOR_KEYS),
                "null_meaning": "Tenor was not published for that date",
            }
        },
        "storage": {
            "manifest_file": "manifest.json",
            "primary_records": "by-year/YYYY-treasury-yield-curve.json",
            "year_shards": "by-year/YYYY-treasury-yield-curve.json",
            "ordering": "ascending_date",
            "dedupe_key": "date",
        },
    }
    return json.dumps(metadata, indent=2) + "\n"


def serialize_nyfed_metadata(dataset: NyFedRateDataset) -> str:
    if dataset.dataset_id == "federal-funds":
        name = "Effective Federal Funds Rate"
        value_fields: dict[str, object] = {
            "rate_basis_points": {
                "unit": "basis_points",
                "source_rate_type": dataset.rate_type,
                "null_meaning": "No rate was published for that effective date",
            }
        }
    elif dataset.dataset_id == "sofr":
        name = "Secured Overnight Financing Rate"
        value_fields = {
            "rate_basis_points": {
                "unit": "basis_points",
                "source_rate_type": dataset.rate_type,
                "null_meaning": "SOFRAI may publish before the daily SOFR observation",
            },
            "percentile_1_basis_points": {"unit": "basis_points"},
            "percentile_25_basis_points": {"unit": "basis_points"},
            "percentile_75_basis_points": {"unit": "basis_points"},
            "percentile_99_basis_points": {"unit": "basis_points"},
            "volume_billions": {"unit": "billions_usd"},
            "average_30_day_basis_points_scaled_1000": {
                "unit": "basis_points_scaled_1000",
                "scale": "stored value / 1000 = basis points",
            },
            "average_90_day_basis_points_scaled_1000": {
                "unit": "basis_points_scaled_1000",
                "scale": "stored value / 1000 = basis points",
            },
            "average_180_day_basis_points_scaled_1000": {
                "unit": "basis_points_scaled_1000",
                "scale": "stored value / 1000 = basis points",
            },
            "sofr_index_scaled_100000000": {
                "unit": "index_scaled_100000000",
                "scale": "stored value / 100000000 = published index",
            },
        }
    else:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.UNKNOWN_DATASET)

    metadata = {
        "name": name,
        "source": "Federal Reserve Bank of New York",
        "source_url": f"https://markets.newyorkfed.org{dataset.source_path}",
        "record_frequency": "business_day",
        "date_key": {
            "field": "date",
            "format": "YYYY-MM-DD",
            "meaning": "Effective date of the published reference rate",
        },
        "value_fields": value_fields,
        "storage": {
            "manifest_file": "manifest.json",
            "primary_records": f"by-year/YYYY-{dataset.shard_slug}.json",
            "year_shards": f"by-year/YYYY-{dataset.shard_slug}.json",
            "ordering": "ascending_date",
            "dedupe_key": "date",
        },
    }
    return json.dumps(metadata, indent=2) + "\n"


def sofr_derived_dataset_name(dataset_id: str) -> str:
    if dataset_id == "sofr-30d-average":
        return "30-Day Average SOFR"
    if dataset_id == "sofr-90d-average":
        return "90-Day Average SOFR"
    if dataset_id == "sofr-180d-average":
        return "180-Day Average SOFR"
    raise MarketRateUpdateError(MarketRateUpdateErrorCode.UNKNOWN_DATASET)


def serialize_year_sharded_manifest(
    dataset_id: str, shard_slug: str, records: list[JsonRecord]
) -> str:
    sorted_records = sorted(records)
    by_year: dict[str, list[JsonRecord]] = {}
    for record in sorted_records:
        by_year.setdefault(record.date[:4], []).append(record)

    years = []
    for year, year_records in by_year.items():
        years.append(
            {
                "year": year,
                "path": f"by-year/{year_shard_filename(shard_slug, year)}",
                "record_count": len(year_records),
                "first_date": year_records[0].date,
                "last_date": year_records[-1].date,
            }
        )

    manifest = {
        "dataset_id": dataset_id,
        "record_storage": "by_year",
        "record_count": len(sorted_records),
        "first_date": sorted_records[0].date if sorted_records else None,
        "last_date": sorted_records[-1].date if sorted_records else None,
        "years": years,
    }
    return json.dumps(manifest, indent=2) + "\n"


def year_shard_filename(dataset_id: str, year: str) -> str:
    return f"{year}-{dataset_id}.json"


def year_from_shard_path(
    shard_path: Path, dataset_slug: str, legacy_dataset_slugs: tuple[str, ...]
) -> str | None:
    name = shard_path.name
    for slug in (dataset_slug, *legacy_dataset_slugs):
        suffix = f"-{slug}.json"
        if name.endswith(suffix):
            year = name.removesuffix(suffix)
            if re.fullmatch(r"[0-9]{4}", year) is not None:
                return year
            return None
    if re.fullmatch(r"[0-9]{4}[.]json", name) is not None:
        return name[:4]
    return None


def remove_stale_year_shards(year_dir: Path, expected_filenames: set[str]) -> bool:
    if not year_dir.exists():
        return False

    changed = False
    try:
        for shard_path in year_dir.glob("*.json"):
            if shard_path.name not in expected_filenames:
                shard_path.unlink()
                changed = True
    except OSError:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.WRITE_FAILED) from None
    return changed


def write_if_changed(path: Path, content: str) -> bool:
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp_file:
            temp_file.write(content)
            temp_name = temp_file.name
        os.replace(temp_name, path)
    except OSError:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.WRITE_FAILED) from None
    return True


def update_treasury_from_csv_texts(
    csv_texts: list[tuple[str, str]],
    manifest_path: Path,
    year_dir: Path,
    write: bool,
) -> tuple[int, int, int, bool]:
    legacy_data_path = manifest_path.parent / "rates.json"
    existing_records = load_treasury_records(year_dir, legacy_data_path)
    source_records: list[TreasuryYieldCurveRecord] = []
    for source_url, csv_text in csv_texts:
        source_records.extend(parse_treasury_csv(csv_text, source_url))
    merged_records, changed = merge_treasury_records(existing_records, source_records)
    if write:
        changed = write_year_sharded_dataset_files(
            "treasury-yield-curve",
            "treasury-yield-curve",
            manifest_path,
            year_dir,
            merged_records,
        ) or changed
        changed = (
            write_if_changed(
                manifest_path.parent / "metadata.json",
                serialize_treasury_metadata(),
            )
            or changed
        )
    return (len(source_records), len(existing_records), len(merged_records), changed)


def update_nyfed_from_csv_text(
    csv_text: str,
    source_url: str,
    dataset: NyFedRateDataset,
    write: bool,
) -> tuple[int, int, int, bool]:
    existing_records = load_nyfed_records(
        dataset.year_dir, dataset.legacy_data_path, dataset
    )
    source_records = parse_nyfed_csv(csv_text, source_url, dataset)
    merged_records, changed = merge_nyfed_records(existing_records, source_records)
    if write:
        changed = write_year_sharded_dataset_files(
            dataset.dataset_id,
            dataset.shard_slug,
            dataset.manifest_path,
            dataset.year_dir,
            merged_records,
        ) or changed
        changed = (
            write_if_changed(
                Path(dataset.path_slug) / "metadata.json",
                serialize_nyfed_metadata(dataset),
            )
            or changed
        )
        if dataset.dataset_id == "sofr":
            changed = write_sofr_derived_datasets(
                merged_records, Path(dataset.path_slug)
            ) or changed
    return (len(source_records), len(existing_records), len(merged_records), changed)


def update_treasury_from_years(
    years: list[int], manifest_path: Path, year_dir: Path, write: bool
) -> tuple[int, int, int, bool]:
    csv_texts = []
    for year in years:
        source_url = build_treasury_source_url(year)
        csv_texts.append((source_url, fetch_text(source_url)))
    return update_treasury_from_csv_texts(csv_texts, manifest_path, year_dir, write)


def update_nyfed_from_source(
    dataset: NyFedRateDataset, end_date: date, write: bool
) -> tuple[int, int, int, bool]:
    source_url = build_nyfed_source_url(dataset, end_date)
    return update_nyfed_from_csv_text(fetch_text(source_url), source_url, dataset, write)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch daily market rates and merge them into local JSON."
    )
    parser.add_argument("--dataset", choices=DATASET_CHOICES, default="all")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--start-year", type=int, default=TREASURY_START_YEAR)
    parser.add_argument("--end-year", type=int, default=current_year())
    parser.add_argument(
        "--treasury-manifest-path", type=Path, default=DEFAULT_TREASURY_MANIFEST_PATH
    )
    parser.add_argument("--treasury-year-dir", type=Path, default=DEFAULT_TREASURY_YEAR_DIR)
    parser.add_argument("--nyfed-end-date", type=parse_cli_date, default=current_utc_date())
    return parser.parse_args(argv)


def parse_cli_date(value: str) -> date:
    try:
        return date.fromisoformat(parse_iso_date(value))
    except MarketRateUpdateError as error:
        raise argparse.ArgumentTypeError(error.code.value) from None


def treasury_years_for_args(args: argparse.Namespace) -> list[int]:
    if args.start_year < TREASURY_START_YEAR or args.end_year > current_year():
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_ARGUMENTS)
    if args.start_year > args.end_year:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.INVALID_ARGUMENTS)
    if args.backfill:
        return list(range(args.start_year, args.end_year + 1))
    first_year = max(args.start_year, args.end_year - 1)
    return list(range(first_year, args.end_year + 1))


def nyfed_datasets_for_name(dataset_name: str) -> list[NyFedRateDataset]:
    if dataset_name in {"all", "ny-fed-reference-rates"}:
        return list(NYFED_DATASETS)
    dataset = NYFED_DATASET_BY_ID.get(dataset_name)
    if dataset is None:
        raise MarketRateUpdateError(MarketRateUpdateErrorCode.UNKNOWN_DATASET)
    return [dataset]


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    try:
        if args.dataset in {"all", "treasury-yield-curve"}:
            treasury_result = update_treasury_from_years(
                years=treasury_years_for_args(args),
                manifest_path=args.treasury_manifest_path,
                year_dir=args.treasury_year_dir,
                write=args.write,
            )
            print_update_result("treasury_yield_curve_update", treasury_result)

        if args.dataset != "treasury-yield-curve":
            for dataset in nyfed_datasets_for_name(args.dataset):
                result = update_nyfed_from_source(
                    dataset=dataset,
                    end_date=args.nyfed_end_date,
                    write=args.write,
                )
                print_update_result(f"{dataset.dataset_id}_update", result)
    except (OSError, MarketRateUpdateError) as error:
        if isinstance(error, MarketRateUpdateError):
            print(f"market_rate_update_error={error.code.value}", file=sys.stderr)
        else:
            print("market_rate_update_error=io_failed", file=sys.stderr)
        return 1

    return 0


def print_update_result(label: str, result: tuple[int, int, int, bool]) -> None:
    source_count, existing_count, final_count, changed = result
    print(
        f"{label} "
        f"source_records={source_count} "
        f"existing_records={existing_count} "
        f"final_records={final_count} "
        f"changed={str(changed).lower()}"
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
