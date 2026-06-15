#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Generate canonical JSON metadata, manifests, schemas, and protobuf shards.

The source updaters own extraction and conflict detection. This module owns the
distribution contract: deterministic JSON, fixed-scale actuarial decimals,
hash-addressable manifests, and dependency-free protobuf encoding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
PROTO_AGGREGATE_FILE = "proto/rates/v1/rates.proto"
PROTO_FILE_BY_MESSAGE = {
    "ApplicableFederalRatesFile": "proto/rates/v1/applicable_federal_rates.proto",
    "Section7520RateFile": "proto/rates/v1/section_7520_rates.proto",
    "AnnualGiftExclusionFile": "proto/rates/v1/annual_gift_exclusion.proto",
    "EstateGiftTaxExemptionFile": "proto/rates/v1/estate_gift_tax_exemption.proto",
    "GstExemptionFile": "proto/rates/v1/gst_exemption.proto",
    "NoncitizenSpouseGiftExclusionFile": "proto/rates/v1/noncitizen_spouse_gift_exclusion.proto",
    "TreasuryYieldCurveFile": "proto/rates/v1/treasury_yield_curve.proto",
    "FederalFundsRateFile": "proto/rates/v1/federal_funds.proto",
    "SofrFile": "proto/rates/v1/sofr.proto",
    "SofrAverageFile": "proto/rates/v1/sofr_average.proto",
    "SofrIndexFile": "proto/rates/v1/sofr_index.proto",
    "MortalityFile": "proto/rates/v1/mortality_table.proto",
    "LifeExpectancyFile": "proto/rates/v1/life_expectancy_by_age.proto",
    "TableBFile": "proto/rates/v1/actuarial_table_b.proto",
    "TableDFile": "proto/rates/v1/actuarial_table_d.proto",
    "TableHFile": "proto/rates/v1/actuarial_table_h.proto",
    "TableR2File": "proto/rates/v1/actuarial_table_r2.proto",
    "TableSFile": "proto/rates/v1/actuarial_table_s.proto",
    "TableU1File": "proto/rates/v1/actuarial_table_u1.proto",
    "TableU2File": "proto/rates/v1/actuarial_table_u2.proto",
    "TableZFile": "proto/rates/v1/actuarial_table_z.proto",
}
FACTOR_SCALE = Decimal(1_000_000)

ACTUARIAL_SCALED_FIELDS = {
    "annuity_factor",
    "complete_life_expectancy_years",
    "curtate_life_expectancy_years",
    "dx",
    "income_interest_factor",
    "life_estate_factor",
    "mx",
    "nx",
    "remainder_factor",
    "survivors_per_100000",
    "udx",
    "umx",
    "unitrust_remainder_factor",
    "unx",
}


class ArtifactErrorCode:
    INVALID_DATASET = "invalid_dataset"
    INVALID_JSON = "invalid_json"
    INVALID_NUMBER = "invalid_number"
    WRITE_FAILED = "write_failed"


class ArtifactError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DatasetSpec:
    dataset_path: str
    dataset_id: str
    schema_id: str
    proto_file_message: str
    storage: str
    record_encoder: Callable[[dict[str, object]], bytes]
    record_field_names: tuple[str, ...]
    protobuf_record_field_alias: str | None = None
    rate_field: str | None = None
    record_filename: str | None = None


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ArtifactError(ArtifactErrorCode.INVALID_NUMBER)
    chunks = bytearray()
    current = value
    while current >= 0x80:
        chunks.append((current & 0x7F) | 0x80)
        current >>= 7
    chunks.append(current)
    return bytes(chunks)


def encode_key(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def field_varint(field_number: int, value: int | bool | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bool):
        numeric = 1 if value else 0
    elif isinstance(value, int):
        numeric = value
    else:
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    return encode_key(field_number, 0) + encode_varint(numeric)


def field_int32(field_number: int, value: int | None) -> bytes:
    if value is None:
        return b""
    if not isinstance(value, int):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    if value < -(2**31) or value > 2**31 - 1:
        raise ArtifactError(ArtifactErrorCode.INVALID_NUMBER)
    encoded_value = value if value >= 0 else (1 << 64) + value
    return encode_key(field_number, 0) + encode_varint(encoded_value)


def field_string(field_number: int, value: str | None) -> bytes:
    if value is None:
        return b""
    encoded = value.encode("utf-8")
    return encode_key(field_number, 2) + encode_varint(len(encoded)) + encoded


def field_message(field_number: int, value: bytes) -> bytes:
    return encode_key(field_number, 2) + encode_varint(len(value)) + value


def encode_file(schema_id: str, records: Iterable[dict[str, object]], encoder: Callable[[dict[str, object]], bytes]) -> bytes:
    out = bytearray()
    out.extend(field_string(1, schema_id))
    for record in records:
        out.extend(field_message(2, encoder(record)))
    return bytes(out)


def proto_file_for_spec(spec: DatasetSpec) -> str:
    proto_file = PROTO_FILE_BY_MESSAGE.get(spec.proto_file_message)
    if proto_file is None:
        raise ArtifactError(ArtifactErrorCode.INVALID_DATASET)
    return proto_file


def required_str(record: dict[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    return value


def optional_int(record: dict[str, object], key: str) -> int | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    return value


def required_int(record: dict[str, object], key: str) -> int:
    value = optional_int(record, key)
    if value is None:
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    return value


def encode_scalar_record(record: dict[str, object], fields: tuple[tuple[str, int, str], ...]) -> bytes:
    out = bytearray()
    for key, tag, kind in fields:
        if kind == "string":
            out.extend(field_string(tag, required_str(record, key)))
        elif kind == "optional_uint":
            out.extend(field_varint(tag, optional_int(record, key)))
        elif kind == "optional_int":
            out.extend(field_int32(tag, optional_int(record, key)))
        elif kind == "uint":
            out.extend(field_varint(tag, required_int(record, key)))
        else:
            raise ArtifactError(ArtifactErrorCode.INVALID_DATASET)
    return bytes(out)


def encode_compounding(value: object) -> bytes:
    if not isinstance(value, dict):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    return encode_scalar_record(
        value,
        (
            ("annual", 1, "uint"),
            ("semiannual", 2, "uint"),
            ("quarterly", 3, "uint"),
            ("monthly", 4, "uint"),
        ),
    )


def encode_afr_term(value: object) -> bytes:
    if not isinstance(value, dict):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    out = bytearray()
    for key, tag in (
        ("afr", 1),
        ("afr_110", 2),
        ("afr_120", 3),
        ("afr_130", 4),
        ("afr_150", 5),
        ("afr_175", 6),
    ):
        if key in value:
            out.extend(field_message(tag, encode_compounding(value[key])))
    return bytes(out)


def encode_afr_record(record: dict[str, object]) -> bytes:
    rates = record.get("applicable_federal_rates")
    adjusted = record.get("adjusted_applicable_federal_rates")
    if not isinstance(rates, dict) or not isinstance(adjusted, dict):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    out = bytearray()
    out.extend(field_string(1, required_str(record, "effective_month")))
    out.extend(field_message(2, encode_afr_term(rates.get("short_term"))))
    out.extend(field_message(3, encode_afr_term(rates.get("mid_term"))))
    out.extend(field_message(4, encode_afr_term(rates.get("long_term"))))
    adjusted_out = bytearray()
    adjusted_out.extend(field_message(1, encode_compounding(adjusted.get("short_term"))))
    adjusted_out.extend(field_message(2, encode_compounding(adjusted.get("mid_term"))))
    adjusted_out.extend(field_message(3, encode_compounding(adjusted.get("long_term"))))
    out.extend(field_message(5, bytes(adjusted_out)))
    return bytes(out)


def encode_nullable_int(value: object) -> bytes:
    out = bytearray()
    if value is None:
        out.extend(field_varint(1, False))
        return bytes(out)
    if not isinstance(value, int):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    out.extend(field_varint(1, True))
    out.extend(field_varint(2, value))
    return bytes(out)


def encode_treasury_record(record: dict[str, object]) -> bytes:
    rates = record.get("par_yields_basis_points")
    if not isinstance(rates, list):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    out = bytearray()
    out.extend(field_string(1, required_str(record, "date")))
    for rate in rates:
        out.extend(field_message(2, encode_nullable_int(rate)))
    return bytes(out)


def encode_sofr_record(record: dict[str, object]) -> bytes:
    return encode_scalar_record(
        record,
        (
            ("date", 1, "string"),
            ("rate_basis_points", 2, "optional_uint"),
            ("percentile_1_basis_points", 3, "optional_int"),
            ("percentile_25_basis_points", 4, "optional_int"),
            ("percentile_75_basis_points", 5, "optional_int"),
            ("percentile_99_basis_points", 6, "optional_int"),
            ("volume_billions", 7, "optional_uint"),
        ),
    )


def encode_sofr_average_record(field_name: str) -> Callable[[dict[str, object]], bytes]:
    def encode(record: dict[str, object]) -> bytes:
        out = bytearray()
        out.extend(field_string(1, required_str(record, "date")))
        out.extend(field_varint(2, required_int(record, field_name)))
        return bytes(out)
    return encode


def encode_sofr_index_record(record: dict[str, object]) -> bytes:
    out = bytearray()
    out.extend(field_string(1, required_str(record, "date")))
    out.extend(field_varint(2, required_int(record, "sofr_index_scaled_100000000")))
    return bytes(out)


SECTION_7520_FIELDS = (
    ("effective_month", 1, "string"),
    ("midterm_afr_120_basis_points", 2, "uint"),
    ("section_7520_rate_basis_points", 3, "uint"),
)
ANNUAL_GIFT_FIELDS = (
    ("period_start_date", 1, "string"),
    ("period_end_date", 2, "string"),
    ("annual_exclusion_amount_usd", 3, "uint"),
)
ESTATE_GIFT_FIELDS = (
    ("period_start_date", 1, "string"),
    ("period_end_date", 2, "string"),
    ("basic_exclusion_amount_usd", 3, "uint"),
)
GST_FIELDS = (
    ("period_start_date", 1, "string"),
    ("period_end_date", 2, "string"),
    ("exemption_amount_usd", 3, "uint"),
)
FED_FUNDS_FIELDS = (("date", 1, "string"), ("rate_basis_points", 2, "optional_uint"))
MORTALITY_FIELDS = (("age", 1, "uint"), ("survivors_per_100000_scaled_1e6", 2, "uint"))
LIFE_EXPECTANCY_FIELDS = (
    ("age", 1, "uint"),
    ("curtate_life_expectancy_years_scaled_1e6", 2, "uint"),
    ("complete_life_expectancy_years_scaled_1e6", 3, "uint"),
)
TABLE_B_FIELDS = (
    ("term_years", 1, "uint"),
    ("annuity_factor_scaled_1e6", 2, "uint"),
    ("income_interest_factor_scaled_1e6", 3, "uint"),
    ("remainder_factor_scaled_1e6", 4, "uint"),
)
TABLE_D_FIELDS = (("term_years", 1, "uint"), ("unitrust_remainder_factor_scaled_1e6", 2, "uint"))
TABLE_H_FIELDS = (
    ("age", 1, "uint"),
    ("dx_scaled_1e6", 2, "uint"),
    ("nx_scaled_1e6", 3, "uint"),
    ("mx_scaled_1e6", 4, "uint"),
)
TABLE_R2_FIELDS = (
    ("older_age", 1, "uint"),
    ("younger_age", 2, "uint"),
    ("remainder_factor_scaled_1e6", 3, "uint"),
)
TABLE_S_FIELDS = (
    ("age", 1, "uint"),
    ("annuity_factor_scaled_1e6", 2, "uint"),
    ("life_estate_factor_scaled_1e6", 3, "uint"),
    ("remainder_factor_scaled_1e6", 4, "uint"),
)
TABLE_U1_FIELDS = (("age", 1, "uint"), ("unitrust_remainder_factor_scaled_1e6", 2, "uint"))
TABLE_U2_FIELDS = (
    ("older_age", 1, "uint"),
    ("younger_age", 2, "uint"),
    ("unitrust_remainder_factor_scaled_1e6", 3, "uint"),
)
TABLE_Z_FIELDS = (
    ("age", 1, "uint"),
    ("udx_scaled_1e6", 2, "uint"),
    ("unx_scaled_1e6", 3, "uint"),
    ("umx_scaled_1e6", 4, "uint"),
)


def scalar_encoder(fields: tuple[tuple[str, int, str], ...]) -> Callable[[dict[str, object]], bytes]:
    return lambda record: encode_scalar_record(record, fields)


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec("7520", "section-7520-rates", "rates.section_7520_rates.v1", "Section7520RateFile", "by_year", scalar_encoder(SECTION_7520_FIELDS), tuple(field[0] for field in SECTION_7520_FIELDS)),
    DatasetSpec("afr", "applicable-federal-rates", "rates.applicable_federal_rates.v1", "ApplicableFederalRatesFile", "by_year", encode_afr_record, ("effective_month", "applicable_federal_rates", "adjusted_applicable_federal_rates")),
    DatasetSpec("annual-gift-exclusion", "annual-gift-exclusion", "rates.annual_gift_exclusion.v1", "AnnualGiftExclusionFile", "single", scalar_encoder(ANNUAL_GIFT_FIELDS), tuple(field[0] for field in ANNUAL_GIFT_FIELDS), record_filename="annual-gift-exclusion.json"),
    DatasetSpec("estate-gift-tax-exemption", "estate-gift-tax-exemption", "rates.estate_gift_tax_exemption.v1", "EstateGiftTaxExemptionFile", "single", scalar_encoder(ESTATE_GIFT_FIELDS), tuple(field[0] for field in ESTATE_GIFT_FIELDS), record_filename="estate-gift-tax-exemption.json"),
    DatasetSpec("gst-exemption", "gst-exemption", "rates.gst_exemption.v1", "GstExemptionFile", "single", scalar_encoder(GST_FIELDS), tuple(field[0] for field in GST_FIELDS), record_filename="gst-exemption.json"),
    DatasetSpec("noncitizen-spouse-gift-exclusion", "noncitizen-spouse-gift-exclusion", "rates.noncitizen_spouse_gift_exclusion.v1", "NoncitizenSpouseGiftExclusionFile", "single", scalar_encoder(ANNUAL_GIFT_FIELDS), tuple(field[0] for field in ANNUAL_GIFT_FIELDS), record_filename="noncitizen-spouse-gift-exclusion.json"),
    DatasetSpec("treasury/treasury-yield-curve", "treasury-yield-curve", "rates.treasury_yield_curve.v1", "TreasuryYieldCurveFile", "by_year", encode_treasury_record, ("date", "par_yields_basis_points")),
    DatasetSpec("fed-funds", "federal-funds", "rates.federal_funds.v1", "FederalFundsRateFile", "by_year", scalar_encoder(FED_FUNDS_FIELDS), tuple(field[0] for field in FED_FUNDS_FIELDS)),
    DatasetSpec("sofr", "sofr", "rates.sofr.v1", "SofrFile", "by_year", encode_sofr_record, ("date", "rate_basis_points", "percentile_1_basis_points", "percentile_25_basis_points", "percentile_75_basis_points", "percentile_99_basis_points", "volume_billions")),
    DatasetSpec("sofr/sofr-30d-average", "sofr-30d-average", "rates.sofr_30d_average.v1", "SofrAverageFile", "by_year", encode_sofr_average_record("average_30_day_basis_points_scaled_1000"), ("date", "average_30_day_basis_points_scaled_1000"), "average_basis_points_scaled_1000"),
    DatasetSpec("sofr/sofr-90d-average", "sofr-90d-average", "rates.sofr_90d_average.v1", "SofrAverageFile", "by_year", encode_sofr_average_record("average_90_day_basis_points_scaled_1000"), ("date", "average_90_day_basis_points_scaled_1000"), "average_basis_points_scaled_1000"),
    DatasetSpec("sofr/sofr-180d-average", "sofr-180d-average", "rates.sofr_180d_average.v1", "SofrAverageFile", "by_year", encode_sofr_average_record("average_180_day_basis_points_scaled_1000"), ("date", "average_180_day_basis_points_scaled_1000"), "average_basis_points_scaled_1000"),
    DatasetSpec("sofr/sofr-index", "sofr-index", "rates.sofr_index.v1", "SofrIndexFile", "by_year", encode_sofr_index_record, ("date", "sofr_index_scaled_100000000")),
    DatasetSpec("actuarial/mortality-table-2000cm", "mortality-table-2000cm", "rates.mortality_table_2000cm.v1", "MortalityFile", "single", scalar_encoder(MORTALITY_FIELDS), tuple(field[0] for field in MORTALITY_FIELDS), record_filename="mortality-table-2000cm.json"),
    DatasetSpec("actuarial/mortality-table-2010cm", "mortality-table-2010cm", "rates.mortality_table_2010cm.v1", "MortalityFile", "single", scalar_encoder(MORTALITY_FIELDS), tuple(field[0] for field in MORTALITY_FIELDS), record_filename="mortality-table-2010cm.json"),
    DatasetSpec("actuarial/life-expectancy-by-age", "life-expectancy-by-age", "rates.life_expectancy_by_age.v1", "LifeExpectancyFile", "single", scalar_encoder(LIFE_EXPECTANCY_FIELDS), tuple(field[0] for field in LIFE_EXPECTANCY_FIELDS), record_filename="life-expectancy-by-age.json"),
    DatasetSpec("actuarial/table-b", "actuarial-table-b", "rates.actuarial_table_b.v1", "TableBFile", "by_interest_rate", scalar_encoder(TABLE_B_FIELDS), tuple(field[0] for field in TABLE_B_FIELDS), rate_field="interest_rate_basis_points"),
    DatasetSpec("actuarial/table-d", "actuarial-table-d", "rates.actuarial_table_d.v1", "TableDFile", "by_interest_rate", scalar_encoder(TABLE_D_FIELDS), tuple(field[0] for field in TABLE_D_FIELDS), rate_field="adjusted_payout_rate_basis_points"),
    DatasetSpec("actuarial/table-h", "actuarial-table-h", "rates.actuarial_table_h.v1", "TableHFile", "by_interest_rate", scalar_encoder(TABLE_H_FIELDS), tuple(field[0] for field in TABLE_H_FIELDS), rate_field="interest_rate_basis_points"),
    DatasetSpec("actuarial/table-r2", "actuarial-table-r2", "rates.actuarial_table_r2.v1", "TableR2File", "by_interest_rate", scalar_encoder(TABLE_R2_FIELDS), tuple(field[0] for field in TABLE_R2_FIELDS), rate_field="interest_rate_basis_points"),
    DatasetSpec("actuarial/table-s", "actuarial-table-s", "rates.actuarial_table_s.v1", "TableSFile", "by_interest_rate", scalar_encoder(TABLE_S_FIELDS), tuple(field[0] for field in TABLE_S_FIELDS), rate_field="interest_rate_basis_points"),
    DatasetSpec("actuarial/table-u1", "actuarial-table-u1", "rates.actuarial_table_u1.v1", "TableU1File", "by_interest_rate", scalar_encoder(TABLE_U1_FIELDS), tuple(field[0] for field in TABLE_U1_FIELDS), rate_field="adjusted_payout_rate_basis_points"),
    DatasetSpec("actuarial/table-u2", "actuarial-table-u2", "rates.actuarial_table_u2.v1", "TableU2File", "by_interest_rate", scalar_encoder(TABLE_U2_FIELDS), tuple(field[0] for field in TABLE_U2_FIELDS), rate_field="adjusted_payout_rate_basis_points"),
    DatasetSpec("actuarial/table-z", "actuarial-table-z", "rates.actuarial_table_z.v1", "TableZFile", "by_interest_rate", scalar_encoder(TABLE_Z_FIELDS), tuple(field[0] for field in TABLE_Z_FIELDS), rate_field="adjusted_payout_rate_basis_points"),
)


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON) from None


def write_if_changed(path: Path, data: bytes) -> bool:
    try:
        if path.exists() and path.read_bytes() == data:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temp_file:
            temp_file.write(data)
            temp_name = temp_file.name
        os.replace(temp_name, path)
    except OSError:
        raise ArtifactError(ArtifactErrorCode.WRITE_FAILED) from None
    return True


def write_text_if_changed(path: Path, text: str) -> bool:
    return write_if_changed(path, text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON) from None


def file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON) from None


def decimal_to_scaled(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ArtifactError(ArtifactErrorCode.INVALID_NUMBER)
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ArtifactError(ArtifactErrorCode.INVALID_NUMBER) from None
    scaled = (decimal_value * FACTOR_SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if scaled < 0:
        raise ArtifactError(ArtifactErrorCode.INVALID_NUMBER)
    return int(scaled)


def normalize_record_numbers(record: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in record.items():
        if key in ACTUARIAL_SCALED_FIELDS:
            normalized[f"{key}_scaled_1e6"] = decimal_to_scaled(value)
        else:
            normalized[key] = value
    return normalized


def normalize_records(spec: DatasetSpec, records: list[object]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    is_actuarial = spec.dataset_path.startswith("actuarial/")
    for record in records:
        if not isinstance(record, dict):
            raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
        next_record = dict(record)
        if is_actuarial:
            next_record = normalize_record_numbers(next_record)
        if set(next_record.keys()) != set(spec.record_field_names):
            raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
        normalized.append(next_record)
    return normalized


def normalize_value_fields(value_fields: object) -> object:
    if not isinstance(value_fields, dict):
        return value_fields
    normalized: dict[str, object] = {}
    for key, value in value_fields.items():
        if key in ACTUARIAL_SCALED_FIELDS:
            next_key = f"{key}_scaled_1e6"
            if isinstance(value, dict):
                next_value = dict(value)
                next_value["unit"] = f"{value.get('unit', 'decimal')}_scaled_1e6"
                next_value["scale"] = "stored value / 1000000"
            else:
                next_value = {"unit": "decimal_scaled_1e6", "scale": "stored value / 1000000"}
            normalized[next_key] = next_value
        else:
            normalized[key] = value
    return normalized


def augment_metadata(spec: DatasetSpec, dataset_dir: Path) -> bool:
    metadata_path = dataset_dir / "metadata.json"
    metadata = read_json(metadata_path)
    if not isinstance(metadata, dict):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    metadata["dataset_id"] = spec.dataset_id
    metadata["schema_id"] = spec.schema_id
    metadata["schema_version"] = SCHEMA_VERSION
    metadata["proto"] = {
        "file": proto_file_for_spec(spec),
        "message": f"rates.v1.{spec.proto_file_message}",
    }
    metadata["value_fields"] = normalize_value_fields(metadata.get("value_fields"))
    return write_text_if_changed(metadata_path, canonical_json(metadata))


def protobuf_path_for_json(dataset_dir: Path, json_path: Path) -> Path:
    relative = json_path.relative_to(dataset_dir)
    return dataset_dir / "protobuf" / relative.with_suffix(".pb").name


def write_protobuf(spec: DatasetSpec, dataset_dir: Path, json_path: Path, records: list[dict[str, object]]) -> Path:
    pb_path = protobuf_path_for_json(dataset_dir, json_path)
    payload = encode_file(spec.schema_id, records, spec.record_encoder)
    write_if_changed(pb_path, payload)
    return pb_path


def artifact_entry(dataset_dir: Path, json_path: Path, pb_path: Path) -> dict[str, object]:
    return {
        "path": json_path.relative_to(dataset_dir).as_posix(),
        "bytes": file_bytes(json_path),
        "sha256": sha256_file(json_path),
        "protobuf_path": pb_path.relative_to(dataset_dir).as_posix(),
        "protobuf_bytes": file_bytes(pb_path),
        "protobuf_sha256": sha256_file(pb_path),
    }


def process_single_dataset(spec: DatasetSpec, dataset_dir: Path) -> dict[str, object]:
    data_path = dataset_dir / (spec.record_filename or "records.json")
    raw = read_json(data_path)
    if not isinstance(raw, list):
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    records = normalize_records(spec, raw)
    write_text_if_changed(data_path, canonical_json(records))
    pb_path = write_protobuf(spec, dataset_dir, data_path, records)
    entry = artifact_entry(dataset_dir, data_path, pb_path)
    manifest = {
        "dataset_id": spec.dataset_id,
        "schema_id": spec.schema_id,
        "schema_version": SCHEMA_VERSION,
        "record_storage": "single_file",
        "record_count": len(records),
        "proto": {
            "file": proto_file_for_spec(spec),
            "message": f"rates.v1.{spec.proto_file_message}",
        },
        "records": entry,
    }
    write_text_if_changed(dataset_dir / "manifest.json", canonical_json(manifest))
    return {"dataset_path": spec.dataset_path, **manifest}


def year_from_record(record: dict[str, object]) -> str:
    value = record.get("date") or record.get("effective_month")
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}(-[0-9]{2})?(-[0-9]{2})?", value) is None:
        raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
    return value[:4]


def process_year_dataset(spec: DatasetSpec, dataset_dir: Path) -> dict[str, object]:
    year_dir = dataset_dir / "by-year"
    year_entries = []
    record_count = 0
    first_key: str | None = None
    last_key: str | None = None
    for json_path in sorted(year_dir.glob("*.json")):
        raw = read_json(json_path)
        if not isinstance(raw, list):
            raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
        records = normalize_records(spec, raw)
        for record in records:
            if year_from_record(record) != json_path.name[:4]:
                raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
        write_text_if_changed(json_path, canonical_json(records))
        pb_path = write_protobuf(spec, dataset_dir, json_path, records)
        if records:
            keys = [str(record.get("date") or record.get("effective_month")) for record in records]
            first_key = keys[0] if first_key is None else min(first_key, keys[0])
            last_key = keys[-1] if last_key is None else max(last_key, keys[-1])
        entry = artifact_entry(dataset_dir, json_path, pb_path)
        entry["year"] = json_path.name[:4]
        entry["record_count"] = len(records)
        if records and "date" in records[0]:
            entry["first_date"] = records[0]["date"]
            entry["last_date"] = records[-1]["date"]
        elif records:
            entry["first_effective_month"] = records[0]["effective_month"]
            entry["last_effective_month"] = records[-1]["effective_month"]
        year_entries.append(entry)
        record_count += len(records)
    manifest: dict[str, object] = {
        "dataset_id": spec.dataset_id,
        "schema_id": spec.schema_id,
        "schema_version": SCHEMA_VERSION,
        "record_storage": "by_year",
        "record_count": record_count,
        "proto": {
            "file": proto_file_for_spec(spec),
            "message": f"rates.v1.{spec.proto_file_message}",
        },
        "years": year_entries,
    }
    if first_key is not None and last_key is not None:
        if len(first_key) == 7:
            manifest["first_effective_month"] = first_key
            manifest["last_effective_month"] = last_key
        else:
            manifest["first_date"] = first_key
            manifest["last_date"] = last_key
    write_text_if_changed(dataset_dir / "manifest.json", canonical_json(manifest))
    return {"dataset_path": spec.dataset_path, **manifest}


def process_rate_dataset(spec: DatasetSpec, dataset_dir: Path) -> dict[str, object]:
    if spec.rate_field is None:
        raise ArtifactError(ArtifactErrorCode.INVALID_DATASET)
    shard_dir = dataset_dir / "by-interest-rate"
    shard_entries = []
    record_count = 0
    for json_path in sorted(shard_dir.glob("*.json")):
        raw = read_json(json_path)
        if not isinstance(raw, list):
            raise ArtifactError(ArtifactErrorCode.INVALID_JSON)
        records = normalize_records(spec, raw)
        write_text_if_changed(json_path, canonical_json(records))
        pb_path = write_protobuf(spec, dataset_dir, json_path, records)
        rate = int(json_path.name.removesuffix("-basis-points.json"))
        entry = artifact_entry(dataset_dir, json_path, pb_path)
        entry[spec.rate_field] = rate
        entry["record_count"] = len(records)
        shard_entries.append(entry)
        record_count += len(records)
    manifest = {
        "dataset_id": spec.dataset_id,
        "schema_id": spec.schema_id,
        "schema_version": SCHEMA_VERSION,
        "record_storage": "by_interest_rate",
        "record_count": record_count,
        "rate_field": spec.rate_field,
        "proto": {
            "file": proto_file_for_spec(spec),
            "message": f"rates.v1.{spec.proto_file_message}",
        },
        "shards": shard_entries,
    }
    write_text_if_changed(dataset_dir / "manifest.json", canonical_json(manifest))
    return {"dataset_path": spec.dataset_path, **manifest}


def schema_for_spec(spec: DatasetSpec) -> dict[str, object]:
    properties: dict[str, object] = {}
    required: list[str] = []
    for field in spec.record_field_names:
        required.append(field)
        if field.endswith("_date") or field in {"date", "effective_month", "applies_to"}:
            properties[field] = {"type": "string"}
        elif field == "par_yields_basis_points":
            properties[field] = {
                "type": "array",
                "items": {"type": ["integer", "null"]},
            }
        elif field in {"applicable_federal_rates", "adjusted_applicable_federal_rates"}:
            properties[field] = {"type": "object"}
        else:
            properties[field] = {"type": "integer", "minimum": 0}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://raw.githubusercontent.com/vanderbr/rates/main/schemas/v1/{spec.dataset_id}.schema.json",
        "title": spec.dataset_id,
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": properties,
        },
    }


def write_schemas(repo_root: Path) -> None:
    for spec in DATASETS:
        schema_path = repo_root / "schemas" / "v1" / f"{spec.dataset_id}.schema.json"
        write_text_if_changed(schema_path, canonical_json(schema_for_spec(spec)))


def process_dataset(repo_root: Path, spec: DatasetSpec) -> dict[str, object]:
    dataset_dir = repo_root / spec.dataset_path
    augment_metadata(spec, dataset_dir)
    if spec.storage == "single":
        return process_single_dataset(spec, dataset_dir)
    if spec.storage == "by_year":
        return process_year_dataset(spec, dataset_dir)
    if spec.storage == "by_interest_rate":
        return process_rate_dataset(spec, dataset_dir)
    raise ArtifactError(ArtifactErrorCode.INVALID_DATASET)


def write_root_index(repo_root: Path, datasets: list[dict[str, object]]) -> None:
    entries = []
    for dataset in datasets:
        dataset_path = dataset["dataset_path"]
        manifest_path = Path(str(dataset_path)) / "manifest.json"
        entries.append(
            {
                "dataset_path": dataset_path,
                "dataset_id": dataset["dataset_id"],
                "schema_id": dataset["schema_id"],
                "schema_version": dataset["schema_version"],
                "record_storage": dataset["record_storage"],
                "record_count": dataset["record_count"],
                "manifest_path": manifest_path.as_posix(),
                "manifest_bytes": file_bytes(repo_root / manifest_path),
                "manifest_sha256": sha256_file(repo_root / manifest_path),
            }
        )
    index = {
        "schema_id": "rates.index.v1",
        "schema_version": SCHEMA_VERSION,
        "proto": {"file": PROTO_AGGREGATE_FILE},
        "datasets": entries,
    }
    write_text_if_changed(repo_root / "index.json", canonical_json(index))


def generate(repo_root: Path) -> None:
    write_schemas(repo_root)
    datasets = [process_dataset(repo_root, spec) for spec in DATASETS]
    write_root_index(repo_root, datasets)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate vanderbr/rates distribution artifacts.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        generate(args.repo_root)
    except ArtifactError as error:
        print(f"artifact_contract_error={error.code}", file=sys.stderr)
        return 1
    print("artifact_contract generated=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
