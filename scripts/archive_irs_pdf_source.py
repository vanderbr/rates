#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Archive IRS PDF source evidence with deterministic manifest metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


DEFAULT_ARCHIVE_DIR = Path("sources/irs-revenue-rulings")
MANIFEST_FILENAME = "manifest.json"
INDEX_FILENAME = "INDEX.md"
MAX_PDF_BYTES = 20_000_000
REQUEST_TIMEOUT_SECONDS = 30
PDF_HEADER_SCAN_BYTES = 1024
YEAR_PATTERN = re.compile(r"^[0-9]{4}$")
SLUG_WORD_PATTERN = re.compile(r"[^a-z0-9]+")


class ArchiveErrorCode(Enum):
    BAD_ARGUMENTS = "bad_arguments"
    BAD_MANIFEST = "bad_manifest"
    BAD_PDF_URL = "bad_pdf_url"
    FETCH_FAILED = "fetch_failed"
    FETCH_TOO_LARGE = "fetch_too_large"
    READ_FAILED = "read_failed"
    WRITE_FAILED = "write_failed"


class ArchiveError(Exception):
    def __init__(self, code: ArchiveErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, order=True)
class ArchiveEntry:
    year: str
    periods: tuple[str, ...]
    subjects: tuple[str, ...]
    path: str
    source_url: str
    title: str
    retrieved_date: str
    bytes: int
    sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "year": self.year,
            "periods": list(self.periods),
            "subjects": list(self.subjects),
            "path": self.path,
            "source_url": self.source_url,
            "title": self.title,
            "retrieved_date": self.retrieved_date,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


def validate_year(year: str) -> None:
    if YEAR_PATTERN.match(year) is None:
        raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)


def validate_pdf_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.netloc != "www.irs.gov":
        raise ArchiveError(ArchiveErrorCode.BAD_PDF_URL)
    if not parsed.path.endswith(".pdf"):
        raise ArchiveError(ArchiveErrorCode.BAD_PDF_URL)
    if not (
        parsed.path.startswith("/pub/irs-drop/")
        or parsed.path.startswith("/pub/irs-irbs/")
    ):
        raise ArchiveError(ArchiveErrorCode.BAD_PDF_URL)


def filename_from_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    filename = unquote(Path(parsed.path).name)
    if not filename.lower().endswith(".pdf") or "/" in filename or "\x00" in filename:
        raise ArchiveError(ArchiveErrorCode.BAD_PDF_URL)
    return filename


def slug_text(value: str) -> str:
    slug = SLUG_WORD_PATTERN.sub("-", value.lower()).strip("-")
    if len(slug) == 0:
        raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)
    return slug


def subject_slug(subjects: tuple[str, ...]) -> str:
    ordered_subjects = sorted(subjects)
    if ordered_subjects == ["afr", "section-7520-rates"]:
        return "afr-7520"
    if ordered_subjects == ["section-7520-rates"]:
        return "7520"
    return "afr"


def period_slug(periods: tuple[str, ...]) -> str:
    ordered_periods = sorted(periods)
    if len(ordered_periods) == 1:
        return ordered_periods[0]
    return f"{ordered_periods[0]}_to_{ordered_periods[-1]}"


def archive_filename(
    periods: tuple[str, ...],
    subjects: tuple[str, ...],
    title: str,
    source_url: str,
) -> str:
    original_filename = filename_from_url(source_url)
    original_stem = Path(original_filename).stem
    return "_".join(
        [
            period_slug(periods),
            subject_slug(subjects),
            slug_text(title),
            slug_text(original_stem),
        ]
    ) + ".pdf"


def read_pdf_from_path(path: Path) -> bytes:
    try:
        body = path.read_bytes()
    except OSError:
        raise ArchiveError(ArchiveErrorCode.READ_FAILED) from None
    if len(body) > MAX_PDF_BYTES:
        raise ArchiveError(ArchiveErrorCode.FETCH_TOO_LARGE)
    if not has_pdf_header(body):
        raise ArchiveError(ArchiveErrorCode.READ_FAILED)
    return body


def has_pdf_header(body: bytes) -> bool:
    # A few older IRS PDFs include a short binary preamble before the PDF header.
    # Preserve the original bytes, but still require a real PDF marker near the
    # beginning so an HTML error page or unrelated file cannot enter the archive.
    return body[:PDF_HEADER_SCAN_BYTES].find(b"%PDF-") >= 0


def fetch_pdf(source_url: str) -> bytes:
    validate_pdf_url(source_url)
    request = Request(
        source_url,
        headers={"User-Agent": "vanderbr-rates-source-archiver/1.0"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > MAX_PDF_BYTES:
                raise ArchiveError(ArchiveErrorCode.FETCH_TOO_LARGE)
            body = response.read(MAX_PDF_BYTES + 1)
    except ArchiveError:
        raise
    except (OSError, URLError, ValueError):
        raise ArchiveError(ArchiveErrorCode.FETCH_FAILED) from None

    if len(body) > MAX_PDF_BYTES:
        raise ArchiveError(ArchiveErrorCode.FETCH_TOO_LARGE)
    if not has_pdf_header(body):
        raise ArchiveError(ArchiveErrorCode.FETCH_FAILED)
    return body


def load_manifest(archive_dir: Path) -> list[ArchiveEntry]:
    manifest_path = archive_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return []
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ArchiveError(ArchiveErrorCode.BAD_MANIFEST) from None
    if not isinstance(value, dict) or value.get("archive_id") != "irs-revenue-rulings":
        raise ArchiveError(ArchiveErrorCode.BAD_MANIFEST)
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise ArchiveError(ArchiveErrorCode.BAD_MANIFEST)
    return sorted(parse_manifest_entry(entry) for entry in entries)


def archive_contains_entry(
    archive_dir: Path,
    periods: tuple[str, ...],
    subjects: tuple[str, ...],
    source_url: str,
) -> bool:
    validate_periods(periods)
    validate_subjects(subjects)
    validate_pdf_url(source_url)
    required_periods = set(periods)
    required_subjects = set(subjects)
    for entry in load_manifest(archive_dir):
        if entry.source_url != source_url:
            continue
        if required_periods.issubset(set(entry.periods)) and required_subjects.issubset(
            set(entry.subjects)
        ):
            return True
    return False


def archived_periods_for_source(
    archive_dir: Path,
    subjects: tuple[str, ...],
    source_url: str,
) -> tuple[str, ...]:
    validate_subjects(subjects)
    validate_pdf_url(source_url)
    required_subjects = set(subjects)
    periods: set[str] = set()
    for entry in load_manifest(archive_dir):
        if entry.source_url == source_url and required_subjects.issubset(
            set(entry.subjects)
        ):
            periods.update(entry.periods)
    return tuple(sorted(periods))


def parse_manifest_entry(value: object) -> ArchiveEntry:
    if not isinstance(value, dict):
        raise ArchiveError(ArchiveErrorCode.BAD_MANIFEST)
    expected_keys = {
        "year",
        "periods",
        "subjects",
        "path",
        "source_url",
        "title",
        "retrieved_date",
        "bytes",
        "sha256",
    }
    if set(value.keys()) != expected_keys:
        raise ArchiveError(ArchiveErrorCode.BAD_MANIFEST)
    year = value["year"]
    periods = value["periods"]
    subjects = value["subjects"]
    path = value["path"]
    source_url = value["source_url"]
    title = value["title"]
    retrieved_date = value["retrieved_date"]
    byte_count = value["bytes"]
    sha256 = value["sha256"]
    if (
        not isinstance(year, str)
        or not isinstance(periods, list)
        or not all(isinstance(period, str) for period in periods)
        or not isinstance(subjects, list)
        or not all(isinstance(subject, str) for subject in subjects)
        or not isinstance(path, str)
        or not isinstance(source_url, str)
        or not isinstance(title, str)
        or not isinstance(retrieved_date, str)
        or not isinstance(byte_count, int)
        or not isinstance(sha256, str)
        or len(title.strip()) == 0
        or re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", retrieved_date) is None
        or re.match(r"^[0-9a-f]{64}$", sha256) is None
    ):
        raise ArchiveError(ArchiveErrorCode.BAD_MANIFEST)
    validate_year(year)
    validate_pdf_url(source_url)
    validate_periods(tuple(periods))
    validate_subjects(tuple(subjects))
    return ArchiveEntry(
        year=year,
        periods=tuple(periods),
        subjects=tuple(subjects),
        path=path,
        source_url=source_url,
        title=title,
        retrieved_date=retrieved_date,
        bytes=byte_count,
        sha256=sha256,
    )


def merge_entries(existing: list[ArchiveEntry], new_entry: ArchiveEntry) -> list[ArchiveEntry]:
    merged_by_path: dict[str, ArchiveEntry] = {}
    for entry in existing:
        merged_by_path[entry.path] = entry
    current = merged_by_path.get(new_entry.path)
    if current is not None and (
        current.source_url != new_entry.source_url
        or current.bytes != new_entry.bytes
        or current.sha256 != new_entry.sha256
    ):
        raise ArchiveError(ArchiveErrorCode.BAD_MANIFEST)
    merged_by_path[new_entry.path] = new_entry
    return sorted(merged_by_path.values())


def write_manifest(archive_dir: Path, entries: list[ArchiveEntry]) -> None:
    payload = {
        "archive_id": "irs-revenue-rulings",
        "entries": [entry.to_json_object() for entry in sorted(entries)],
    }
    write_text_atomic(
        archive_dir / MANIFEST_FILENAME,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    write_text_atomic(archive_dir / INDEX_FILENAME, render_index(entries))


def render_index(entries: list[ArchiveEntry]) -> str:
    lines = [
        "# IRS Revenue Ruling Source Index",
        "",
        "This index lists the IRS PDFs preserved here and the rate month or months",
        "each file supports. It is meant to make the source materials easy to find",
        "when checking AFR and Section 7520 history.",
        "",
        "| Rate month | Rates covered | IRS publication | Archived PDF | IRS URL | Retrieved |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in sorted(entries):
        lines.append(
            "| "
            + " | ".join(
                [
                    ", ".join(entry.periods),
                    ", ".join(format_subject(subject) for subject in entry.subjects),
                    entry.title,
                    f"[{entry.path}]({entry.path})",
                    entry.source_url,
                    entry.retrieved_date,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def format_subject(subject: str) -> str:
    if subject == "section-7520-rates":
        return "Section 7520"
    return "AFR"


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            temp_file.write(text)
            temp_name = temp_file.name
        os.replace(temp_name, path)
    except OSError:
        raise ArchiveError(ArchiveErrorCode.WRITE_FAILED) from None


def write_bytes_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temp_file:
            temp_file.write(body)
            temp_name = temp_file.name
        os.replace(temp_name, path)
    except OSError:
        raise ArchiveError(ArchiveErrorCode.WRITE_FAILED) from None


def archive_pdf(
    archive_dir: Path,
    year: str,
    periods: tuple[str, ...],
    subjects: tuple[str, ...],
    source_url: str,
    title: str,
    retrieved_date: str,
    body: bytes,
) -> ArchiveEntry:
    validate_year(year)
    validate_periods(periods)
    validate_subjects(subjects)
    validate_pdf_url(source_url)
    if len(title.strip()) == 0:
        raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)
    if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", retrieved_date) is None:
        raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)

    filename = archive_filename(periods, subjects, title, source_url)
    relative_path = f"by-year/{year}/{filename}"
    sha256 = hashlib.sha256(body).hexdigest()
    entry = ArchiveEntry(
        year=year,
        periods=tuple(sorted(periods)),
        subjects=tuple(sorted(subjects)),
        path=relative_path,
        source_url=source_url,
        title=" ".join(title.split()),
        retrieved_date=retrieved_date,
        bytes=len(body),
        sha256=sha256,
    )
    entries = merge_entries(load_manifest(archive_dir), entry)
    write_bytes_atomic(archive_dir / relative_path, body)
    write_manifest(archive_dir, entries)
    return entry


def validate_periods(periods: tuple[str, ...]) -> None:
    if len(periods) == 0:
        raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)
    for period in periods:
        if re.match(r"^[0-9]{4}-[0-9]{2}$", period) is None:
            raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)


def validate_subjects(subjects: tuple[str, ...]) -> None:
    allowed = {"afr", "section-7520-rates"}
    if len(subjects) == 0:
        raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)
    if any(subject not in allowed for subject in subjects):
        raise ArchiveError(ArchiveErrorCode.BAD_ARGUMENTS)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive an IRS PDF source file under sources/irs-revenue-rulings."
    )
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--year", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--period",
        action="append",
        required=True,
        help="Rate period supported by the PDF, formatted YYYY-MM. Repeat as needed.",
    )
    parser.add_argument(
        "--subject",
        action="append",
        required=True,
        choices=("afr", "section-7520-rates"),
        help="Dataset subject supported by the PDF. Repeat as needed.",
    )
    parser.add_argument("--retrieved-date", default=date.today().isoformat())
    parser.add_argument(
        "--input-pdf",
        type=Path,
        help="Use an already downloaded IRS PDF instead of fetching the URL.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        body = read_pdf_from_path(args.input_pdf) if args.input_pdf else fetch_pdf(args.url)
        entry = archive_pdf(
            archive_dir=args.archive_dir,
            year=args.year,
            periods=tuple(args.period),
            subjects=tuple(args.subject),
            source_url=args.url,
            title=args.title,
            retrieved_date=args.retrieved_date,
            body=body,
        )
    except ArchiveError as error:
        print(f"archive_irs_pdf_source_error={error.code.value}", file=sys.stderr)
        return 1

    print(f"archived_irs_pdf_source path={entry.path} sha256={entry.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
