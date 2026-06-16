#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Import locally normalized SOA 2015 VBT JSON into the public data contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any


DATASET_DIR = Path("actuarial/2015-vbt")
SOURCE_PAGE_URL = "https://www.soa.org/resources/experience-studies/2015/2015-valuation-basic-tables/"
EXPECTED_TABLE_COUNT = 68
RATE_SCALE = 100_000


class Import2015VbtErrorCode(Enum):
    DUPLICATE_TABLE = "duplicate_table"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_SOURCE = "invalid_source"
    WRITE_FAILED = "write_failed"


class Import2015VbtError(Exception):
    """Domain-specific importer failure for deterministic command exits."""

    def __init__(self, code: Import2015VbtErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False) + "\n"


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE) from None
    if not isinstance(value, dict):
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
    return value


def write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        path.write_text(text, encoding="utf-8")
    except OSError:
        raise Import2015VbtError(Import2015VbtErrorCode.WRITE_FAILED) from None
    return True


def parse_rate_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
    records: dict[str, int] = {}
    for raw_age, raw_rate in value.items():
        if not isinstance(raw_age, str) or not raw_age.isdecimal():
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        if isinstance(raw_rate, bool) or not isinstance(raw_rate, int):
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        if raw_rate < 0 or raw_rate > RATE_SCALE:
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        records[raw_age] = raw_rate
    return dict(sorted(records.items(), key=lambda item: int(item[0])))


def parse_select_rates(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
    records: dict[str, dict[str, int]] = {}
    for raw_issue_age, raw_duration_rates in value.items():
        if not isinstance(raw_issue_age, str) or not raw_issue_age.isdecimal():
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        duration_rates = parse_rate_map(raw_duration_rates)
        if len(duration_rates) == 0:
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        records[raw_issue_age] = duration_rates
    return dict(sorted(records.items(), key=lambda item: int(item[0])))


def classify_table(table_id: str, source: str) -> dict[str, object]:
    age_basis_match = re.search(r"_(alb|anb)$", table_id)
    if age_basis_match is None:
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
    age_basis = age_basis_match.group(1)

    if source == "2015 VBT Smoker-Distinct Tables":
        match = re.fullmatch(r"2015_vbt_smoker_distinct_tables__2015_([fm])(ns|sm)_(alb|anb)", table_id)
        if match is None:
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        sex = "female" if match.group(1) == "f" else "male"
        smoker = "non_smoker" if match.group(2) == "ns" else "smoker"
        return {
            "structure": "smoker_distinct",
            "sex": sex,
            "smoker": smoker,
            "age_basis": age_basis,
            "relative_risk_percent": 0,
        }

    if source == "2015 VBT Unismoke Tables":
        match = re.fullmatch(r"2015_vbt_unismoke_tables__2015_(female|male)_composite_(alb|anb)", table_id)
        if match is None:
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        return {
            "structure": "unismoke",
            "sex": match.group(1),
            "smoker": "composite",
            "age_basis": age_basis,
            "relative_risk_percent": 0,
        }

    if source == "2015 VBT Relative Risk Tables":
        match = re.fullmatch(
            r"2015_vbt_relative_risk_tables__2015_(female|male)_(non_smoker|smoker)_rr([0-9]+)_(alb|anb)",
            table_id,
        )
        if match is None:
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        return {
            "structure": "relative_risk",
            "sex": match.group(1),
            "smoker": match.group(2),
            "age_basis": age_basis,
            "relative_risk_percent": int(match.group(3)),
        }

    raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)


def normalize_table(path: Path) -> dict[str, object]:
    source = read_json_object(path)
    table_source = source.get("source")
    table_name = source.get("table")
    table_identity = source.get("table_identity")
    if (
        not isinstance(table_source, str)
        or not isinstance(table_name, str)
        or isinstance(table_identity, bool)
        or not isinstance(table_identity, int)
    ):
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)

    table_id = path.stem
    classification = classify_table(table_id, table_source)
    return {
        "table_id": table_id,
        "table_identity": table_identity,
        "source": table_source,
        "table": table_name,
        "family": "2015 vbt",
        "table_set": "vbt",
        "basis": "valuation_basic",
        **classification,
        "shape": "select_and_ultimate",
        "year": 2015,
        "select_rates": parse_select_rates(source.get("select_rates")),
        "ultimate_rates": parse_rate_map(source.get("ultimate_rates")),
    }


def metadata() -> dict[str, object]:
    return {
        "name": "2015 Valuation Basic Tables",
        "source": "Society of Actuaries",
        "source_page_url": SOURCE_PAGE_URL,
        "mortality_basis": "2015 VBT",
        "description": (
            "SOA 2015 Valuation Basic Tables covering relative-risk, "
            "smoker-distinct, and unismoke ALB/ANB select-and-ultimate tables."
        ),
        "value_fields": {
            "select_rates": {
                "unit": "mortality_probability_scaled_1e5",
                "scale": "stored value / 100000",
                "rate_key": "issue_age/duration",
            },
            "ultimate_rates": {
                "unit": "mortality_probability_scaled_1e5",
                "scale": "stored value / 100000",
                "rate_key": "attained_age",
            },
        },
        "storage": {
            "manifest_file": "manifest.json",
            "primary_records": "by-table/<table-id>.json",
            "ordering": "ascending_table_id",
            "dedupe_key": "table_id",
        },
    }


def import_tables(repo_root: Path, source_json_dir: Path, write: bool) -> tuple[int, bool]:
    if not source_json_dir.is_dir():
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_ARGUMENTS)

    tables = [normalize_table(path) for path in sorted(source_json_dir.glob("*.json"))]
    if len(tables) != EXPECTED_TABLE_COUNT:
        raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)

    seen: set[str] = set()
    for table in tables:
        table_id = table.get("table_id")
        if not isinstance(table_id, str) or table_id in seen:
            raise Import2015VbtError(Import2015VbtErrorCode.DUPLICATE_TABLE)
        seen.add(table_id)

    if not write:
        return len(tables), False

    changed = False
    dataset_dir = repo_root / DATASET_DIR
    changed = write_text(dataset_dir / "metadata.json", canonical_json(metadata())) or changed
    for table in tables:
        table_id = table.get("table_id")
        if not isinstance(table_id, str):
            raise Import2015VbtError(Import2015VbtErrorCode.INVALID_SOURCE)
        changed = write_text(
            dataset_dir / "by-table" / f"{table_id}.json",
            canonical_json(table),
        ) or changed
    return len(tables), changed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-json-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        count, changed = import_tables(args.repo_root, args.source_json_dir, args.write)
    except Import2015VbtError as error:
        print(f"import_2015_vbt_error={error.code.value}", file=sys.stderr)
        return 1
    print(f"import_2015_vbt tables={count} changed={str(changed).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
