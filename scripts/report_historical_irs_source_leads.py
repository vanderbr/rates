#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Report official source leads for older IRS AFR and Section 7520 rulings.

The IRS website exposes monthly revenue ruling PDFs for later years, but older
AFR and Section 7520 rulings are often bound into Internal Revenue Cumulative
Bulletins. This report keeps the known official catalog records and access
notes close to the data archive while avoiding unverifiable numeric backfill.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


DEFAULT_OUTPUT_PATH = Path("sources/irs-revenue-rulings/historical-source-leads.json")
RETRIEVED_DATE = "2026-06-17"
GOVINFO_DETAIL_BASE_URL = "https://www.govinfo.gov/app/details/"
GOVINFO_METADATA_BASE_URL = "https://www.govinfo.gov/metadata/pkg/"
ARCHIVE_DETAIL_BASE_URL = "https://archive.org/details/"


class SourceLeadErrorCode(Enum):
    WRITE_FAILED = "write_failed"


class SourceLeadError(Exception):
    def __init__(self, code: SourceLeadErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class GovInfoLead:
    year: int
    package_id: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "detail_url": f"{GOVINFO_DETAIL_BASE_URL}{self.package_id}",
            "metadata_url": f"{GOVINFO_METADATA_BASE_URL}{self.package_id}/mods.xml",
            "title": f"Internal Revenue Cumulative Bulletin {self.year}",
            "status": "official_catalog_record",
        }


@dataclass(frozen=True)
class InternetArchiveLead:
    index_identifier: str | None
    notes: tuple[str, ...]

    def to_json_object(self) -> dict[str, object]:
        value: dict[str, object] = {
            "status": "discovery_lead_only",
            "notes": list(self.notes),
        }
        if self.index_identifier is not None:
            value["index_identifier"] = self.index_identifier
            value["index_url"] = f"{ARCHIVE_DETAIL_BASE_URL}{self.index_identifier}"
        return value


@dataclass(frozen=True)
class HistoricalSourceLead:
    year: int
    rate_coverage_note: str
    govinfo: GovInfoLead
    internet_archive: InternetArchiveLead

    def to_json_object(self) -> dict[str, object]:
        return {
            "year": self.year,
            "subjects": ["afr", "section-7520-rates"],
            "rate_coverage_note": self.rate_coverage_note,
            "govinfo": self.govinfo.to_json_object(),
            "internet_archive": self.internet_archive.to_json_object(),
        }


def historical_source_leads() -> tuple[HistoricalSourceLead, ...]:
    return (
        HistoricalSourceLead(
            year=1989,
            rate_coverage_note=(
                "AFR rulings may cover the full calendar year; Section 7520 "
                "rates begin with May 1989 under the current dataset scope."
            ),
            govinfo=GovInfoLead(
                year=1989,
                package_id="GOVPUB-T22-aaf296b1f844da19743e7a36ca791ec6",
            ),
            internet_archive=InternetArchiveLead(
                index_identifier=(
                    "sim_united-states-internal-revenue-cumulative-bulletin_1989_index"
                ),
                notes=(
                    "Index record discovered by Archive.org metadata search.",
                    "Bound-volume derivatives may be access-restricted.",
                ),
            ),
        ),
        HistoricalSourceLead(
            year=1990,
            rate_coverage_note="AFR and Section 7520 rulings may cover the full calendar year.",
            govinfo=GovInfoLead(
                year=1990,
                package_id="GOVPUB-T22-cd76c958c56200689d4a52922f58eb43",
            ),
            internet_archive=InternetArchiveLead(
                index_identifier=(
                    "sim_united-states-internal-revenue-cumulative-bulletin_1990_index"
                ),
                notes=(
                    "Index record discovered by Archive.org metadata search.",
                    "Bound-volume derivatives may be access-restricted.",
                ),
            ),
        ),
        HistoricalSourceLead(
            year=1991,
            rate_coverage_note="AFR and Section 7520 rulings may cover the full calendar year.",
            govinfo=GovInfoLead(
                year=1991,
                package_id="GOVPUB-T22-230e9ab8cec1063f9dc7bb07bfd4740b",
            ),
            internet_archive=InternetArchiveLead(
                index_identifier=(
                    "sim_united-states-internal-revenue-cumulative-bulletin_1991_index"
                ),
                notes=(
                    "Index record discovered by Archive.org metadata search.",
                    "Bound-volume derivatives may be access-restricted.",
                ),
            ),
        ),
        HistoricalSourceLead(
            year=1992,
            rate_coverage_note="AFR and Section 7520 rulings may cover the full calendar year.",
            govinfo=GovInfoLead(
                year=1992,
                package_id="GOVPUB-T22-ef1df1a16d70d7731bd1a17696f722e7",
            ),
            internet_archive=InternetArchiveLead(
                index_identifier=(
                    "sim_united-states-internal-revenue-cumulative-bulletin_1992_index"
                ),
                notes=(
                    "Index record discovered by Archive.org metadata search.",
                    "Bound-volume derivatives may be access-restricted.",
                ),
            ),
        ),
        HistoricalSourceLead(
            year=1993,
            rate_coverage_note="AFR and Section 7520 rulings may cover the full calendar year.",
            govinfo=GovInfoLead(
                year=1993,
                package_id="GOVPUB-T22-ead415109da2009feda00f43f5903456",
            ),
            internet_archive=InternetArchiveLead(
                index_identifier=None,
                notes=(
                    "No matching Archive.org index identifier was found by predictable ID or metadata search.",
                    "Use the official GovInfo record first for continued discovery.",
                ),
            ),
        ),
        HistoricalSourceLead(
            year=1994,
            rate_coverage_note="AFR and Section 7520 rulings may cover the full calendar year.",
            govinfo=GovInfoLead(
                year=1994,
                package_id="GOVPUB-T22-fa6021c1f704388d418436ab95604a13",
            ),
            internet_archive=InternetArchiveLead(
                index_identifier=(
                    "sim_united-states-internal-revenue-cumulative-bulletin_1994_index"
                ),
                notes=(
                    "Index and bound-volume records were discovered.",
                    "Observed July-December 1994 text PDF derivative is about 400 MB, above GitHub's normal file limit.",
                    "Derivative downloads may be access-restricted.",
                ),
            ),
        ),
        HistoricalSourceLead(
            year=1995,
            rate_coverage_note="AFR and Section 7520 rulings may cover the full calendar year.",
            govinfo=GovInfoLead(
                year=1995,
                package_id="GOVPUB-T22-c1f3e2322722ef17ff04ef9a47a3b6da",
            ),
            internet_archive=InternetArchiveLead(
                index_identifier=(
                    "sim_united-states-internal-revenue-cumulative-bulletin_1995_index"
                ),
                notes=(
                    "Index and bound-volume records were discovered.",
                    "Observed January-June 1995 text PDF derivative is about 430 MB.",
                    "Observed July-December 1995 text PDF derivative is about 227 MB.",
                    "Derivative downloads may be access-restricted.",
                ),
            ),
        ),
    )


def report_json() -> str:
    report = {
        "report_id": "historical-irs-revenue-ruling-source-leads",
        "retrieved_date": RETRIEVED_DATE,
        "scope": "Older AFR and Section 7520 source discovery for 1989-1995.",
        "notes": [
            "GovInfo records are official Treasury Department / IRS catalog records.",
            "Do not backfill rate values from OCR or secondary tables without verifying the IRS-published ruling values.",
            "Some bound cumulative bulletin PDFs exceed GitHub's normal per-file size limit; prefer verified ruling-page extracts when accessible.",
        ],
        "leads": [lead.to_json_object() for lead in historical_source_leads()],
    }
    return json.dumps(report, indent=2) + "\n"


def write_report(output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json(), encoding="utf-8")
    except OSError:
        raise SourceLeadError(SourceLeadErrorCode.WRITE_FAILED) from None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write known source leads for older IRS revenue rulings."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path for the generated JSON report.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        write_report(args.output)
    except SourceLeadError as error:
        print(f"historical_source_leads_error={error.code.value}", file=sys.stderr)
        return 1
    print(f"historical_source_leads_written={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
