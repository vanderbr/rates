#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Update IRS actuarial table JSON from official IRS source workbooks."""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

IRS_ACTUARIAL_PAGE_URL = "https://www.irs.gov/retirement-plans/actuarial-tables"
IRS_TEGE_PREFIX = "/pub/irs-tege/"
REQUEST_TIMEOUT_SECONDS = 30
MAX_WORKBOOK_BYTES = 10_000_000
RADIX = 100_000

DATASET_SPECS = {
    "mortality-table-2010cm": {
        "path": Path("actuarial/mortality-table-2010cm"),
        "source_file": "table-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-2010cm-final.xlsx",
        "name": "Table 2010CM Mortality Table",
        "description": "IRS current mortality table derived from mortality experience around 2010.",
    },
    "life-expectancy-by-age": {
        "path": Path("actuarial/life-expectancy-by-age"),
        "source_file": "table-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-2010cm-final.xlsx",
        "name": "Life Expectancy by Age from Table 2010CM",
        "description": (
            "Complete future lifetime approximation computed from IRS Table "
            "2010CM survivors by age."
        ),
    },
    "table-2001": {
        "path": Path("table-2001"),
        "source_file": "table-2000cm.xls",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-2000cm.xls",
        "name": "Table 2000CM Mortality Table",
        "description": (
            "IRS prior mortality table derived from mortality experience around "
            "2000. The source folder name is retained as table-2001 for repository "
            "layout compatibility."
        ),
    },
    "table-b": {
        "path": Path("actuarial/table-b"),
        "source_file": "table-b-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-b-final.xlsx",
        "name": "Table B",
        "description": "Term-certain annuity, income-interest, and remainder factors.",
    },
    "table-d": {
        "path": Path("actuarial/table-d"),
        "source_file": "table-d.xls",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-d.xls",
        "name": "Table D",
        "description": "Term unitrust remainder factors.",
    },
    "table-h": {
        "path": Path("actuarial/table-h"),
        "source_file": "table-h-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-h-2010cm-final.xlsx",
        "name": "Table H",
        "description": "Commutation factors based on IRS Table 2010CM.",
    },
    "table-r2": {
        "path": Path("actuarial/table-r2"),
        "source_file": "table-r2-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-r2-2010cm-final.xlsx",
        "name": "Table R(2)",
        "description": "Two-life remainder factors based on IRS Table 2010CM.",
    },
    "table-s": {
        "path": Path("actuarial/table-s"),
        "source_file": "table-s-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-s-2010cm-final.xlsx",
        "name": "Table S",
        "description": "One-life annuity, life-estate, and remainder factors.",
    },
    "table-u1": {
        "path": Path("actuarial/table-u1"),
        "source_file": "table-u1-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-u1-2010cm-final.xlsx",
        "name": "Table U(1)",
        "description": "One-life unitrust remainder factors based on IRS Table 2010CM.",
    },
    "table-u2": {
        "path": Path("actuarial/table-u2"),
        "source_file": "table-u2-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-u2-2010cm-final.xlsx",
        "name": "Table U(2)",
        "description": "Two-life unitrust remainder factors based on IRS Table 2010CM.",
    },
    "table-z": {
        "path": Path("actuarial/table-z"),
        "source_file": "table-z-2010cm-final.xlsx",
        "source_url": "https://www.irs.gov/pub/irs-tege/table-z-2010cm-final.xlsx",
        "name": "Table Z",
        "description": "Unitrust commutation factors based on IRS Table 2010CM.",
    },
}


class UpdateErrorCode(Enum):
    BAD_URL = "bad_url"
    DUPLICATE_RECORD = "duplicate_record"
    FETCH_FAILED = "fetch_failed"
    FETCH_TOO_LARGE = "fetch_too_large"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_SOURCE = "invalid_source"
    WRITE_FAILED = "write_failed"


class UpdateActuarialTablesError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: UpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class ShardedDataset:
    dataset_id: str
    dataset_dir: Path
    rate_field: str
    record_sort_keys: tuple[str, ...]
    records_by_rate: dict[int, list[dict[str, object]]]


def validate_source_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.irs.gov"
        or not parsed.path.startswith(IRS_TEGE_PREFIX)
        or not (parsed.path.endswith(".xlsx") or parsed.path.endswith(".xls"))
    ):
        raise UpdateActuarialTablesError(UpdateErrorCode.BAD_URL)


def fetch_bytes(source_url: str) -> bytes:
    validate_source_url(source_url)
    request = Request(
        source_url,
        headers={
            "User-Agent": "vanderbr-rates-actuarial-updater/1.0 (+https://github.com/vanderbr/rates)"
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_WORKBOOK_BYTES:
                raise UpdateActuarialTablesError(UpdateErrorCode.FETCH_TOO_LARGE)
            body = response.read(MAX_WORKBOOK_BYTES + 1)
    except UpdateActuarialTablesError:
        raise
    except (HTTPError, OSError, URLError, ValueError):
        raise UpdateActuarialTablesError(UpdateErrorCode.FETCH_FAILED) from None
    if len(body) > MAX_WORKBOOK_BYTES:
        raise UpdateActuarialTablesError(UpdateErrorCode.FETCH_TOO_LARGE)
    return body


def ensure_source_files(source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    for spec in DATASET_SPECS.values():
        source_path = source_dir / str(spec["source_file"])
        if source_path.exists():
            continue
        source_path.write_bytes(fetch_bytes(str(spec["source_url"])))


def load_rows(source_dir: Path, dataset_id: str) -> list[list[object]]:
    spec = DATASET_SPECS[dataset_id]
    source_path = source_dir / str(spec["source_file"])
    return load_xlsx_sheet_rows(source_path)[0]


def load_all_sheet_rows(source_dir: Path, dataset_id: str) -> list[list[list[object]]]:
    spec = DATASET_SPECS[dataset_id]
    source_path = source_dir / str(spec["source_file"])
    return load_xlsx_sheet_rows(source_path)


def load_xlsx_sheet_rows(source_path: Path) -> list[list[list[object]]]:
    try:
        with zipfile.ZipFile(source_path) as workbook_zip:
            shared_strings = parse_shared_strings(workbook_zip)
            sheet_names = sorted(
                (
                    name
                    for name in workbook_zip.namelist()
                    if re.fullmatch(r"xl/worksheets/sheet[0-9]+[.]xml", name)
                    is not None
                ),
                key=sheet_sort_key,
            )
            if not sheet_names:
                raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
            return [
                parse_sheet_xml(workbook_zip.read(sheet_name), shared_strings)
                for sheet_name in sheet_names
            ]
    except (KeyError, OSError, zipfile.BadZipFile):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE) from None


def load_biff_sheet_rows(source_path: Path) -> list[list[object]]:
    try:
        workbook = read_compound_document_stream(source_path.read_bytes(), "Workbook")
    except OSError:
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE) from None
    cells = parse_biff_cells(workbook)
    if not cells:
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)

    max_row = max(row for row, _column in cells)
    max_column = max(column for _row, column in cells)
    rows: list[list[object]] = []
    for row_index in range(max_row + 1):
        row_values: list[object] = []
        for column_index in range(max_column + 1):
            row_values.append(cells.get((row_index, column_index)))
        rows.append(row_values)
    return rows


def read_compound_document_stream(document: bytes, stream_name: str) -> bytes:
    if document[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)

    sector_size = 1 << read_uint16(document, 30)
    first_directory_sector = read_uint32(document, 48)
    mini_stream_cutoff_size = read_uint32(document, 56)
    fat_sector_count = read_uint32(document, 44)
    difat_entries = [
        entry
        for entry in struct_unpack_uint32(document, 76, 109)
        if entry not in (0xFFFFFFFE, 0xFFFFFFFF)
    ]
    if fat_sector_count > len(difat_entries):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)

    fat_entries: list[int] = []
    for sector_index in difat_entries[:fat_sector_count]:
        sector = read_sector(document, sector_size, sector_index)
        fat_entries.extend(struct_unpack_uint32(sector, 0, sector_size // 4))

    directory_stream = read_regular_stream(
        document,
        sector_size,
        fat_entries,
        first_directory_sector,
        sector_size * len(sector_chain(fat_entries, first_directory_sector)),
    )
    for offset in range(0, len(directory_stream), 128):
        entry = directory_stream[offset : offset + 128]
        if len(entry) != 128:
            continue
        name_byte_length = read_uint16(entry, 64)
        if name_byte_length < 2:
            continue
        name = entry[: name_byte_length - 2].decode("utf-16le", errors="ignore")
        object_type = entry[66]
        if name != stream_name or object_type != 2:
            continue
        first_sector = read_uint32(entry, 116)
        stream_size = read_uint64(entry, 120)
        if stream_size < mini_stream_cutoff_size:
            raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
        return read_regular_stream(
            document,
            sector_size,
            fat_entries,
            first_sector,
            stream_size,
        )

    raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)


def read_uint16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
    return int.from_bytes(data[offset : offset + 2], "little")


def read_uint32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
    return int.from_bytes(data[offset : offset + 4], "little")


def read_uint64(data: bytes, offset: int) -> int:
    if offset + 8 > len(data):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
    return int.from_bytes(data[offset : offset + 8], "little")


def struct_unpack_uint32(data: bytes, offset: int, count: int) -> tuple[int, ...]:
    byte_length = count * 4
    if offset + byte_length > len(data):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
    return tuple(
        int.from_bytes(data[index : index + 4], "little")
        for index in range(offset, offset + byte_length, 4)
    )


def read_sector(document: bytes, sector_size: int, sector_index: int) -> bytes:
    offset = (sector_index + 1) * sector_size
    sector = document[offset : offset + sector_size]
    if len(sector) != sector_size:
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
    return sector


def sector_chain(fat_entries: list[int], first_sector: int) -> list[int]:
    chain: list[int] = []
    current = first_sector
    seen: set[int] = set()
    while current not in (0xFFFFFFFE, 0xFFFFFFFF):
        if current in seen or current >= len(fat_entries):
            raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
        seen.add(current)
        chain.append(current)
        current = fat_entries[current]
    return chain


def read_regular_stream(
    document: bytes,
    sector_size: int,
    fat_entries: list[int],
    first_sector: int,
    stream_size: int,
) -> bytes:
    data = b"".join(
        read_sector(document, sector_size, sector_index)
        for sector_index in sector_chain(fat_entries, first_sector)
    )
    return data[:stream_size]


def parse_biff_cells(workbook: bytes) -> dict[tuple[int, int], object]:
    cells: dict[tuple[int, int], object] = {}
    offset = 0
    while offset + 4 <= len(workbook):
        record_type = read_uint16(workbook, offset)
        record_length = read_uint16(workbook, offset + 2)
        payload_start = offset + 4
        payload_end = payload_start + record_length
        if payload_end > len(workbook):
            raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
        payload = workbook[payload_start:payload_end]
        parse_biff_cell_record(record_type, payload, cells)
        offset = payload_end
    return cells


def parse_biff_cell_record(
    record_type: int,
    payload: bytes,
    cells: dict[tuple[int, int], object],
) -> None:
    if record_type == 0x0203 and len(payload) >= 14:
        row = read_uint16(payload, 0)
        column = read_uint16(payload, 2)
        cells[(row, column)] = struct_unpack_float64(payload, 6)
    elif record_type == 0x027E and len(payload) >= 10:
        row = read_uint16(payload, 0)
        column = read_uint16(payload, 2)
        cells[(row, column)] = decode_rk(read_uint32(payload, 6))
    elif record_type == 0x00BD and len(payload) >= 8:
        row = read_uint16(payload, 0)
        first_column = read_uint16(payload, 2)
        last_column = read_uint16(payload, len(payload) - 2)
        offset = 4
        for column in range(first_column, last_column + 1):
            if offset + 6 > len(payload):
                raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
            cells[(row, column)] = decode_rk(read_uint32(payload, offset + 2))
            offset += 6
    elif record_type == 0x0204 and len(payload) >= 8:
        row = read_uint16(payload, 0)
        column = read_uint16(payload, 2)
        text_length = read_uint16(payload, 6)
        text = payload[8 : 8 + text_length].decode("latin1", errors="ignore")
        cells[(row, column)] = text


def struct_unpack_float64(data: bytes, offset: int) -> float:
    if offset + 8 > len(data):
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
    return struct.unpack_from("<d", data, offset)[0]


def decode_rk(value: int) -> float:
    multiplier = 100.0 if value & 0x01 else 1.0
    if value & 0x02:
        signed_value = int.from_bytes(
            (value & 0xFFFFFFFC).to_bytes(4, "little"),
            "little",
            signed=True,
        )
        return float(signed_value >> 2) / multiplier
    raw = (value & 0xFFFFFFFC) << 32
    return struct.unpack("<d", raw.to_bytes(8, "little"))[0] / multiplier


def sheet_sort_key(path: str) -> int:
    match = re.search(r"sheet([0-9]+)[.]xml$", path)
    if match is None:
        return 0
    return int(match.group(1))


def parse_shared_strings(workbook_zip: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook_zip.namelist():
        return []
    root = ElementTree.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
    namespace = xml_namespace(root.tag)
    strings: list[str] = []
    for item in root.findall(f"{namespace}si"):
        text_parts = [node.text or "" for node in item.iter(f"{namespace}t")]
        strings.append("".join(text_parts))
    return strings


def parse_sheet_xml(sheet_xml: bytes, shared_strings: list[str]) -> list[list[object]]:
    root = ElementTree.fromstring(sheet_xml)
    namespace = xml_namespace(root.tag)
    rows: list[list[object]] = []
    for row_node in root.iter(f"{namespace}row"):
        row_values: list[object] = []
        for cell in row_node.findall(f"{namespace}c"):
            reference = cell.attrib.get("r", "")
            column_index = column_index_from_cell_reference(reference)
            while len(row_values) <= column_index:
                row_values.append(None)
            row_values[column_index] = parse_cell_value(cell, namespace, shared_strings)
        rows.append(row_values)
    return rows


def parse_cell_value(
    cell: ElementTree.Element, namespace: str, shared_strings: list[str]
) -> object:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text_node = cell.find(f"{namespace}is/{namespace}t")
        return text_node.text if text_node is not None else None

    value_node = cell.find(f"{namespace}v")
    if value_node is None or value_node.text is None:
        return None

    if cell_type == "s":
        try:
            return shared_strings[int(value_node.text)]
        except (IndexError, ValueError):
            raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE) from None

    text = value_node.text
    try:
        decimal_value = Decimal(text)
    except ArithmeticError:
        return text
    if decimal_value == decimal_value.to_integral_value():
        return int(decimal_value)
    return float(decimal_value)


def xml_namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[: tag.index("}") + 1]
    return ""


def column_index_from_cell_reference(reference: str) -> int:
    column_text = ""
    for character in reference:
        if character.isalpha():
            column_text += character.upper()
        else:
            break
    if column_text == "":
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)

    column_index = 0
    for character in column_text:
        column_index = column_index * 26 + (ord(character) - ord("A") + 1)
    return column_index - 1


def value_at(row: list[object], index: int) -> object:
    if index >= len(row):
        return None
    return row[index]


def is_blank(value: object) -> bool:
    return value is None or value == "" or value == " "


def parse_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_factor(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        if math.isfinite(float(value)):
            return float(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        if text.startswith("."):
            text = "0" + text
        try:
            return float(text)
        except ValueError:
            return None
    return None


def scale_decimal_factor(value: float) -> int:
    scaled = (Decimal(str(value)) * Decimal(1_000_000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    if scaled < 0:
        raise UpdateActuarialTablesError(UpdateErrorCode.INVALID_SOURCE)
    return int(scaled)


def parse_rate_basis_points(value: object) -> int | None:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            try:
                return int((Decimal(text[:-1]) * Decimal(100)).to_integral_value())
            except ArithmeticError:
                return None
        match = re.search(r"([0-9]+(?:[.][0-9]+)?) Percent", text)
        if match is not None:
            return int((Decimal(match.group(1)) * Decimal(100)).to_integral_value())
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int((Decimal(str(value)) * Decimal(10_000)).to_integral_value())
    return None


def json_dumps(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False) + "\n"


def write_if_changed(path: Path, data: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == data:
            return False
        path.write_text(data, encoding="utf-8")
    except OSError:
        raise UpdateActuarialTablesError(UpdateErrorCode.WRITE_FAILED) from None
    return True


def write_records_file(dataset_dir: Path, records: list[dict[str, object]]) -> bool:
    return write_if_changed(dataset_dir / "rates.json", json_dumps(records))


def shard_filename(rate_basis_points: int) -> str:
    return f"{rate_basis_points:05d}-basis-points.json"


def sort_records(
    records: Iterable[dict[str, object]], sort_keys: tuple[str, ...]
) -> list[dict[str, object]]:
    return sorted(
        records,
        key=lambda record: tuple(record[key] for key in sort_keys),
    )


def write_sharded_dataset(dataset: ShardedDataset) -> bool:
    changed = False
    total_records = 0
    shards = []
    by_rate_dir = dataset.dataset_dir / "by-interest-rate"
    for rate in sorted(dataset.records_by_rate):
        records = sort_records(dataset.records_by_rate[rate], dataset.record_sort_keys)
        records_for_storage = [
            {
                key: value
                for key, value in record.items()
                if key != dataset.rate_field
            }
            for record in records
        ]
        total_records += len(records)
        filename = shard_filename(rate)
        changed = (
            write_if_changed(by_rate_dir / filename, json_dumps(records_for_storage))
            or changed
        )
        shards.append(
            {
                dataset.rate_field: rate,
                "path": f"by-interest-rate/{filename}",
                "record_count": len(records),
            }
        )

    manifest = {
        "dataset_id": dataset.dataset_id,
        "record_storage": "by_interest_rate",
        "record_count": total_records,
        "rate_field": dataset.rate_field,
        "shards": shards,
    }
    changed = (
        write_if_changed(dataset.dataset_dir / "manifest.json", json_dumps(manifest))
        or changed
    )
    return changed


def metadata(
    dataset_id: str,
    storage: dict[str, object],
    value_fields: dict[str, object],
) -> dict[str, object]:
    spec = DATASET_SPECS[dataset_id]
    return {
        "name": spec["name"],
        "source": "Internal Revenue Service",
        "source_page_url": IRS_ACTUARIAL_PAGE_URL,
        "source_url": spec["source_url"],
        "mortality_basis": mortality_basis(dataset_id),
        "effective_for_valuations_on_or_after": effective_start_date(dataset_id),
        "effective_for_valuations_on_or_before": effective_end_date(dataset_id),
        "description": spec["description"],
        "value_fields": value_fields,
        "storage": storage,
    }


def mortality_basis(dataset_id: str) -> str | None:
    if dataset_id == "table-d":
        return None
    if dataset_id == "table-2001":
        return "2000CM"
    return "2010CM"


def effective_start_date(dataset_id: str) -> str | None:
    if dataset_id == "table-d":
        return None
    if dataset_id == "table-2001":
        return "2009-05-01"
    return "2023-06-01"


def effective_end_date(dataset_id: str) -> str | None:
    if dataset_id == "table-2001":
        return "2023-05-31"
    return None


def clean_metadata(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if item is not None}


def write_metadata(
    dataset_id: str,
    storage: dict[str, object],
    value_fields: dict[str, object],
) -> bool:
    spec = DATASET_SPECS[dataset_id]
    return write_if_changed(
        Path(spec["path"]) / "metadata.json",
        json_dumps(clean_metadata(metadata(dataset_id, storage, value_fields))),
    )


def parse_mortality_records(rows: list[list[object]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    seen: set[int] = set()
    for row in rows:
        for age_index, lives_index in ((1, 2), (5, 6), (9, 10)):
            age = parse_int(value_at(row, age_index))
            lives = parse_factor(value_at(row, lives_index))
            if age is None or lives is None:
                continue
            if age in seen:
                raise UpdateActuarialTablesError(UpdateErrorCode.DUPLICATE_RECORD)
            seen.add(age)
            records.append(
                {
                    "age": age,
                    "survivors_per_100000_scaled_1e6": scale_decimal_factor(lives),
                }
            )
    return sort_records(records, ("age",))


def parse_life_expectancy_records(
    mortality_records: list[dict[str, object]],
) -> list[dict[str, object]]:
    survivors_by_age = {
        int(record["age"]): float(record["survivors_per_100000_scaled_1e6"]) / 1_000_000
        for record in mortality_records
    }
    ages = sorted(survivors_by_age)
    records: list[dict[str, object]] = []
    for age in ages:
        lives = survivors_by_age[age]
        if lives <= 0:
            curtate = 0.0
            complete = 0.0
        else:
            curtate = (
                sum(
                    survivors_by_age[future_age]
                    for future_age in ages
                    if future_age > age
                )
                / lives
            )
            complete = curtate + 0.5
        records.append(
            {
                "age": age,
                "curtate_life_expectancy_years_scaled_1e6": scale_decimal_factor(
                    round(curtate, 6)
                ),
                "complete_life_expectancy_years_scaled_1e6": scale_decimal_factor(
                    round(complete, 6)
                ),
            }
        )
    return records


def append_record_by_rate(
    records_by_rate: dict[int, list[dict[str, object]]],
    rate: int,
    record: dict[str, object],
) -> None:
    record_with_rate = {**record}
    records_by_rate.setdefault(rate, []).append(record_with_rate)


def parse_table_s(rows: list[list[object]]) -> dict[int, list[dict[str, object]]]:
    records_by_rate: dict[int, list[dict[str, object]]] = {}
    current_rate: int | None = None
    for row in rows:
        for cell in row:
            rate = parse_rate_basis_points(cell)
            if isinstance(cell, str) and "Interest at" in cell and rate is not None:
                current_rate = rate
        if current_rate is None:
            continue
        for offset in (0, 4):
            age = parse_int(value_at(row, offset))
            annuity = parse_factor(value_at(row, offset + 1))
            life_estate = parse_factor(value_at(row, offset + 2))
            remainder = parse_factor(value_at(row, offset + 3))
            if age is None or annuity is None or life_estate is None or remainder is None:
                continue
            append_record_by_rate(
                records_by_rate,
                current_rate,
                {
                    "interest_rate_basis_points": current_rate,
                    "age": age,
                    "annuity_factor_scaled_1e6": scale_decimal_factor(annuity),
                    "life_estate_factor_scaled_1e6": scale_decimal_factor(life_estate),
                    "remainder_factor_scaled_1e6": scale_decimal_factor(remainder),
                },
            )
    return records_by_rate


def parse_commutation_table(
    rows: list[list[object]], rate_phrase: str, rate_field: str, field_prefix: str
) -> dict[int, list[dict[str, object]]]:
    records_by_rate: dict[int, list[dict[str, object]]] = {}
    current_rate: int | None = None
    for row in rows:
        for cell in row:
            rate = parse_rate_basis_points(cell)
            if isinstance(cell, str) and rate_phrase in cell and rate is not None:
                current_rate = rate
            if isinstance(cell, str):
                title_match = re.search(r"Table [A-Z]\(([0-9]+(?:[.][0-9]+)?)\)", cell)
                if title_match is not None:
                    current_rate = int(
                        (Decimal(title_match.group(1)) * Decimal(100)).to_integral_value()
                    )
        if current_rate is None:
            continue
        for offset in (0, 8):
            age = parse_int(value_at(row, offset))
            dx = parse_factor(value_at(row, offset + 2))
            nx = parse_factor(value_at(row, offset + 4))
            mx = parse_factor(value_at(row, offset + 6))
            if age is None or dx is None or nx is None or mx is None:
                continue
            append_record_by_rate(
                records_by_rate,
                current_rate,
                {
                    rate_field: current_rate,
                    "age": age,
                    f"{field_prefix}dx_scaled_1e6": scale_decimal_factor(dx),
                    f"{field_prefix}nx_scaled_1e6": scale_decimal_factor(nx),
                    f"{field_prefix}mx_scaled_1e6": scale_decimal_factor(mx),
                },
            )
    return records_by_rate


def parse_table_b(rows: list[list[object]]) -> dict[int, list[dict[str, object]]]:
    records_by_rate: dict[int, list[dict[str, object]]] = {}
    row_count = len(rows)
    for index, row in enumerate(rows):
        if value_at(row, 0) != "Table B":
            continue
        rate_row = rows[index + 2] if index + 2 < row_count else []
        for offset, rate_index in ((0, 4), (8, 12)):
            rate = parse_rate_basis_points(value_at(rate_row, rate_index))
            if rate is None:
                continue
            for data_row in rows[index + 4 :]:
                if value_at(data_row, 0) == "Table B":
                    break
                years = parse_int(value_at(data_row, offset))
                annuity = parse_factor(value_at(data_row, offset + 2))
                income = parse_factor(value_at(data_row, offset + 4))
                remainder = parse_factor(value_at(data_row, offset + 6))
                if years is None or annuity is None or income is None or remainder is None:
                    continue
                append_record_by_rate(
                    records_by_rate,
                    rate,
                    {
                        "interest_rate_basis_points": rate,
                        "term_years": years,
                        "annuity_factor_scaled_1e6": scale_decimal_factor(annuity),
                        "income_interest_factor_scaled_1e6": scale_decimal_factor(income),
                        "remainder_factor_scaled_1e6": scale_decimal_factor(remainder),
                    },
                )
    return records_by_rate


def parse_one_life_unitrust(rows: list[list[object]]) -> dict[int, list[dict[str, object]]]:
    records_by_rate: dict[int, list[dict[str, object]]] = {}
    current_rates: list[tuple[int, int]] = []
    for row in rows:
        if value_at(row, 0) == "Age":
            current_rates = []
            for column_index in range(1, len(row)):
                rate = parse_rate_basis_points(value_at(row, column_index))
                if rate is not None:
                    current_rates.append((column_index, rate))
            continue
        age = parse_int(value_at(row, 0))
        if age is None:
            continue
        for column_index, rate in current_rates:
            factor = parse_factor(value_at(row, column_index))
            if factor is None:
                continue
            append_record_by_rate(
                records_by_rate,
                rate,
                {
                    "adjusted_payout_rate_basis_points": rate,
                    "age": age,
                    "unitrust_remainder_factor_scaled_1e6": scale_decimal_factor(factor),
                },
            )
    return records_by_rate


def parse_two_life_factor_sheets(
    sheets: list[list[list[object]]],
    rate_field: str,
    factor_field: str,
) -> dict[int, list[dict[str, object]]]:
    records_by_rate: dict[int, list[dict[str, object]]] = {}
    for rows in sheets:
        current_rates: list[tuple[int, int]] = []
        for row in rows:
            if str(value_at(row, 0)).strip() == "O" and str(value_at(row, 1)).strip() == "Y":
                current_rates = []
                for column_index in range(2, len(row)):
                    rate = parse_rate_basis_points(value_at(row, column_index))
                    if rate is not None:
                        current_rates.append((column_index, rate))
                continue
            older_age = parse_int(value_at(row, 0))
            younger_age = parse_int(value_at(row, 1))
            if older_age is None or younger_age is None:
                continue
            for column_index, rate in current_rates:
                factor = parse_factor(value_at(row, column_index))
                if factor is None:
                    continue
                append_record_by_rate(
                    records_by_rate,
                    rate,
                    {
                        rate_field: rate,
                        "older_age": older_age,
                        "younger_age": younger_age,
                        f"{factor_field}_scaled_1e6": scale_decimal_factor(factor),
                    },
                )
    return records_by_rate


def generate_table_d() -> dict[int, list[dict[str, object]]]:
    records_by_rate: dict[int, list[dict[str, object]]] = {}
    for rate in range(20, 2001, 20):
        adjusted_payout_rate = Decimal(rate) / Decimal(10_000)
        for years in range(1, 21):
            factor = (Decimal(1) - adjusted_payout_rate) ** years
            rounded = factor.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            append_record_by_rate(
                records_by_rate,
                rate,
                {
                    "adjusted_payout_rate_basis_points": rate,
                    "term_years": years,
                    "unitrust_remainder_factor_scaled_1e6": int(
                        rounded * Decimal(1_000_000)
                    ),
                },
            )
    return records_by_rate


def assert_unique_sharded_records(
    records_by_rate: dict[int, list[dict[str, object]]], key_fields: tuple[str, ...]
) -> None:
    for records in records_by_rate.values():
        seen: set[tuple[object, ...]] = set()
        for record in records:
            key = tuple(record[field] for field in key_fields)
            if key in seen:
                raise UpdateActuarialTablesError(UpdateErrorCode.DUPLICATE_RECORD)
            seen.add(key)


def update(source_dir: Path, write: bool) -> tuple[int, bool]:
    mortality_records = parse_mortality_records(load_rows(source_dir, "mortality-table-2010cm"))
    life_expectancy_records = parse_life_expectancy_records(mortality_records)
    table_2001_records = parse_mortality_records(
        load_biff_sheet_rows(source_dir / str(DATASET_SPECS["table-2001"]["source_file"]))
    )

    changed = False
    file_count = 0
    if write:
        changed = write_records_file(Path(DATASET_SPECS["table-2001"]["path"]), table_2001_records) or changed
        changed = write_metadata(
            "table-2001",
            {"primary_records": "rates.json", "ordering": "ascending_age", "dedupe_key": "age"},
            {
                "survivors_per_100000_scaled_1e6": {
                    "unit": "survivors_scaled_1e6",
                    "scale": "stored value / 1000000",
                    "radix": RADIX,
                }
            },
        ) or changed
        changed = write_records_file(Path(DATASET_SPECS["mortality-table-2010cm"]["path"]), mortality_records) or changed
        changed = write_metadata(
            "mortality-table-2010cm",
            {"primary_records": "rates.json", "ordering": "ascending_age", "dedupe_key": "age"},
            {
                "survivors_per_100000_scaled_1e6": {
                    "unit": "survivors_scaled_1e6",
                    "scale": "stored value / 1000000",
                    "radix": RADIX,
                }
            },
        ) or changed
        changed = write_records_file(Path(DATASET_SPECS["life-expectancy-by-age"]["path"]), life_expectancy_records) or changed
        changed = write_metadata(
            "life-expectancy-by-age",
            {"primary_records": "rates.json", "ordering": "ascending_age", "dedupe_key": "age"},
            {
                "curtate_life_expectancy_years_scaled_1e6": {
                    "unit": "years_scaled_1e6",
                    "scale": "stored value / 1000000",
                },
                "complete_life_expectancy_years_scaled_1e6": {
                    "unit": "years_scaled_1e6",
                    "scale": "stored value / 1000000",
                    "calculation_note": "curtate expectation plus 0.5 year uniform-distribution approximation",
                },
            },
        ) or changed
        file_count += 6

    sharded_configs = [
        (
            "table-b",
            parse_table_b(load_rows(source_dir, "table-b")),
            "interest_rate_basis_points",
            ("interest_rate_basis_points", "term_years"),
            {
                "annuity_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"},
                "income_interest_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"},
                "remainder_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"},
            },
        ),
        (
            "table-d",
            generate_table_d(),
            "adjusted_payout_rate_basis_points",
            ("adjusted_payout_rate_basis_points", "term_years"),
            {"unitrust_remainder_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000", "formula": "(1 - adjusted_payout_rate) ^ term_years"}},
        ),
        (
            "table-h",
            parse_commutation_table(
                load_rows(source_dir, "table-h"),
                "Interest Rate of",
                "interest_rate_basis_points",
                "",
            ),
            "interest_rate_basis_points",
            ("interest_rate_basis_points", "age"),
            {"dx_scaled_1e6": {"unit": "commutation_factor_scaled_1e6", "scale": "stored value / 1000000"}, "nx_scaled_1e6": {"unit": "commutation_factor_scaled_1e6", "scale": "stored value / 1000000"}, "mx_scaled_1e6": {"unit": "commutation_factor_scaled_1e6", "scale": "stored value / 1000000"}},
        ),
        (
            "table-r2",
            parse_two_life_factor_sheets(
                load_all_sheet_rows(source_dir, "table-r2"),
                "interest_rate_basis_points",
                "remainder_factor",
            ),
            "interest_rate_basis_points",
            ("interest_rate_basis_points", "older_age", "younger_age"),
            {"remainder_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"}},
        ),
        (
            "table-s",
            parse_table_s(load_rows(source_dir, "table-s")),
            "interest_rate_basis_points",
            ("interest_rate_basis_points", "age"),
            {"annuity_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"}, "life_estate_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"}, "remainder_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"}},
        ),
        (
            "table-u1",
            parse_one_life_unitrust(load_rows(source_dir, "table-u1")),
            "adjusted_payout_rate_basis_points",
            ("adjusted_payout_rate_basis_points", "age"),
            {"unitrust_remainder_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"}},
        ),
        (
            "table-u2",
            parse_two_life_factor_sheets(
                load_all_sheet_rows(source_dir, "table-u2"),
                "adjusted_payout_rate_basis_points",
                "unitrust_remainder_factor",
            ),
            "adjusted_payout_rate_basis_points",
            ("adjusted_payout_rate_basis_points", "older_age", "younger_age"),
            {"unitrust_remainder_factor_scaled_1e6": {"unit": "factor_scaled_1e6", "scale": "stored value / 1000000"}},
        ),
        (
            "table-z",
            parse_commutation_table(
                load_rows(source_dir, "table-z"),
                "Adjusted Payout Rate",
                "adjusted_payout_rate_basis_points",
                "u",
            ),
            "adjusted_payout_rate_basis_points",
            ("adjusted_payout_rate_basis_points", "age"),
            {"udx_scaled_1e6": {"unit": "commutation_factor_scaled_1e6", "scale": "stored value / 1000000"}, "unx_scaled_1e6": {"unit": "commutation_factor_scaled_1e6", "scale": "stored value / 1000000"}, "umx_scaled_1e6": {"unit": "commutation_factor_scaled_1e6", "scale": "stored value / 1000000"}},
        ),
    ]

    for dataset_id, records_by_rate, rate_field, sort_keys, value_fields in sharded_configs:
        assert_unique_sharded_records(records_by_rate, sort_keys)
        if not write:
            continue
        dataset_dir = Path(DATASET_SPECS[dataset_id]["path"])
        changed = write_sharded_dataset(
            ShardedDataset(
                dataset_id=dataset_id,
                dataset_dir=dataset_dir,
                rate_field=rate_field,
                record_sort_keys=sort_keys,
                records_by_rate=records_by_rate,
            )
        ) or changed
        changed = write_metadata(
            dataset_id,
            {
                "manifest_file": "manifest.json",
                "primary_records": "by-interest-rate/NNNNN-basis-points.json",
                "shards": "by-interest-rate/NNNNN-basis-points.json",
                "shard_rate_field": rate_field,
                "ordering": "ascending_time_or_age_key",
                "dedupe_key": [
                    sort_key for sort_key in sort_keys if sort_key != rate_field
                ],
            },
            value_fields,
        ) or changed
        file_count += len(records_by_rate) + 2

    return file_count, changed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Directory containing IRS source workbooks. Missing files are fetched.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.source_dir is None:
            with tempfile.TemporaryDirectory() as directory:
                source_dir = Path(directory)
                ensure_source_files(source_dir)
                file_count, changed = update(source_dir, args.write)
        else:
            ensure_source_files(args.source_dir)
            file_count, changed = update(args.source_dir, args.write)
    except UpdateActuarialTablesError as error:
        print(error.code.value, file=sys.stderr)
        return 1

    if args.write:
        print(f"actuarial_files={file_count} changed={str(changed).lower()}")
    else:
        print(f"actuarial_files={file_count} changed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
