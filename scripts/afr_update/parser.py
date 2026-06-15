# SPDX-License-Identifier: Apache-2.0

"""Parse IRS revenue ruling text into deterministic AFR records."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .constants import (
    ADJUSTED_MONTH_PATTERN,
    ADJUSTED_TERM_ROW_PATTERN,
    COMPOUNDING_KEYS,
    MONTH_PATTERN,
    MONTHS,
    REVENUE_RULING_PATTERN,
    TABLE_ROW_PATTERN,
    TERM_LABELS,
)
from .errors import AfrUpdateError, AfrUpdateErrorCode
from .fetch import validate_pdf_url
from .models import AfrRateRecord


def normalize_lines(text: str) -> list[str]:
    normalized = text.replace("\u0002", "-").replace("\u00a0", " ")
    lines: list[str] = []
    for line in normalized.splitlines():
        without_borders = line.strip(" │┌└┐┘─")
        compacted = " ".join(without_borders.split())
        if compacted != "":
            lines.append(compacted)
    return lines


def parse_afr_record(text: str, source_url: str) -> AfrRateRecord:
    validate_pdf_url(source_url)
    lines = normalize_lines(text)
    joined = "\n".join(lines)

    revenue_ruling_match = REVENUE_RULING_PATTERN.search(joined)
    if revenue_ruling_match is None:
        raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)

    effective_month = find_effective_month(lines, revenue_ruling_match.group(1))
    revenue_ruling = revenue_ruling_match.group(0)
    table_1 = parse_table_1(lines)
    table_2 = parse_table_2(lines)

    return AfrRateRecord(
        effective_month=effective_month,
        revenue_ruling=revenue_ruling,
        source_url=source_url,
        applicable_federal_rates=table_1,
        adjusted_applicable_federal_rates=table_2,
    )


def is_afr_ruling_text(text: str) -> bool:
    return "Applicable Federal Rates (AFR)" in text and "TABLE 1" in text


def format_effective_month(month_name: str, year: str) -> str:
    month = MONTHS.get(month_name.lower())
    if month is None:
        raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
    return f"{int(year):04d}-{month:02d}"


def find_effective_month(lines: list[str], revenue_ruling_year: str) -> str:
    table_1_month = find_table_1_month(lines)
    table_2_month = find_month_after_marker(lines, "TABLE 2")
    if (
        table_1_month is not None
        and table_1_month[:4] != revenue_ruling_year
        and table_2_month is not None
        and table_2_month[:4] == revenue_ruling_year
    ):
        return table_2_month
    if table_1_month is None:
        raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
    return table_1_month


def find_table_1_month(lines: list[str]) -> str | None:
    table_1_month = find_month_after_marker(lines, "TABLE 1")
    if table_1_month is not None:
        return table_1_month

    for line in lines:
        if "TABLE 2" in line:
            break
        match = MONTH_PATTERN.search(line)
        if match is not None:
            return format_effective_month(match.group(1), match.group(2))
    return None


def find_month_after_marker(lines: list[str], marker: str) -> str | None:
    for index, line in enumerate(lines):
        if marker not in line:
            continue
        for candidate in lines[index + 1 :]:
            match = MONTH_PATTERN.search(candidate) or ADJUSTED_MONTH_PATTERN.search(candidate)
            if match is not None:
                return format_effective_month(match.group(1), match.group(2))
            if marker == "TABLE 1" and "TABLE 2" in candidate:
                break
            if marker == "TABLE 2" and "TABLE 3" in candidate:
                break
    return None


def parse_table_1(lines: list[str]) -> dict[str, dict[str, dict[str, int]]]:
    table_lines = slice_table_1(lines)
    result: dict[str, dict[str, dict[str, int]]] = {}
    current_term: str | None = None

    for line in table_lines:
        adjusted_term_match = ADJUSTED_TERM_ROW_PATTERN.match(line)
        if adjusted_term_match is not None:
            term = normalize_term(adjusted_term_match.group("term"))
            if term is None:
                raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
            result[term] = parse_compounding_values(adjusted_term_match)
            current_term = term
            continue

        term = normalize_term(line)
        if term is not None:
            current_term = term
            result.setdefault(term, {})
            continue

        row_match = TABLE_ROW_PATTERN.match(line)
        if row_match is None or current_term is None:
            continue

        rate_key = normalize_rate_label(row_match.group("label"))
        result[current_term][rate_key] = parse_compounding_values(row_match)

    expected = {
        "short_term": {"afr", "afr_110", "afr_120", "afr_130"},
        "mid_term": {"afr", "afr_110", "afr_120", "afr_130", "afr_150", "afr_175"},
        "long_term": {"afr", "afr_110", "afr_120", "afr_130"},
    }
    validate_nested_keys(result, expected)
    return result


def slice_table_1(lines: list[str]) -> list[str]:
    try:
        return slice_between(lines, "TABLE 1", "TABLE 2")
    except AfrUpdateError:
        return slice_between(lines, "Applicable Federal Rates (AFR)", "TABLE 2")


def parse_table_2(lines: list[str]) -> dict[str, dict[str, int]]:
    table_lines = slice_between(lines, "TABLE 2", "TABLE 3")
    result: dict[str, dict[str, int]] = {}
    current_term: str | None = None

    for line in table_lines:
        adjusted_term_match = ADJUSTED_TERM_ROW_PATTERN.match(line)
        if adjusted_term_match is not None:
            term = normalize_term(adjusted_term_match.group("term"))
            if term is None:
                raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
            result[term] = parse_compounding_values(adjusted_term_match)
            current_term = term
            continue

        term = normalize_term(line)
        if term is not None:
            current_term = term
            continue

        row_match = TABLE_ROW_PATTERN.match(line)
        if row_match is None or current_term is None:
            continue
        if normalize_rate_label(row_match.group("label")) != "adjusted_afr":
            continue
        result[current_term] = parse_compounding_values(row_match)

    expected = {
        "short_term": set(COMPOUNDING_KEYS),
        "mid_term": set(COMPOUNDING_KEYS),
        "long_term": set(COMPOUNDING_KEYS),
    }
    if set(result.keys()) != set(expected.keys()):
        raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
    for term, keys in expected.items():
        if set(result[term].keys()) != keys:
            raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
    return result


def slice_between(lines: list[str], start_marker: str, end_marker: str) -> list[str]:
    start_index: int | None = None
    for index, line in enumerate(lines):
        if start_marker in line:
            start_index = index + 1
            break
    if start_index is None:
        raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)

    for index in range(start_index + 1, len(lines)):
        if end_marker in lines[index]:
            return lines[start_index:index]

    raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)


def normalize_term(line: str) -> str | None:
    key = line.lower().replace("\u2011", "-").replace("\u2010", "-")
    return TERM_LABELS.get(key)


def normalize_rate_label(label: str) -> str:
    normalized = " ".join(label.lower().split())
    if normalized == "afr":
        return "afr"
    if normalized == "adjusted afr":
        return "adjusted_afr"
    if normalized.endswith("% afr") or normalized.endswith("$ afr"):
        return f"afr_{normalized[:-5]}"
    raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)


def parse_compounding_values(row_match: re.Match[str]) -> dict[str, int]:
    return {
        key: parse_percent_basis_points(row_match.group(key))
        for key in COMPOUNDING_KEYS
    }


def parse_percent_basis_points(value: str) -> int:
    normalized = value.strip().removesuffix("%")
    try:
        percent = Decimal(normalized)
    except InvalidOperation:
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_PERCENT) from None

    basis_points = percent * Decimal(100)
    if basis_points != basis_points.to_integral_value():
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_PERCENT)
    if basis_points < 0 or basis_points > Decimal(100_000):
        raise AfrUpdateError(AfrUpdateErrorCode.INVALID_PERCENT)
    return int(basis_points)


def validate_nested_keys(
    result: dict[str, dict[str, dict[str, int]]],
    expected: dict[str, set[str]],
) -> None:
    if set(result.keys()) != set(expected.keys()):
        raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
    for term, rate_keys in expected.items():
        if set(result[term].keys()) != rate_keys:
            raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
        for rate_key in rate_keys:
            if set(result[term][rate_key].keys()) != set(COMPOUNDING_KEYS):
                raise AfrUpdateError(AfrUpdateErrorCode.MISSING_FIELD)
