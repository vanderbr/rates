# SPDX-License-Identifier: Apache-2.0

"""Network and PDF extraction helpers for IRS AFR updates."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .constants import (
    KNOWN_BACKFILL_INDEX_OMISSION_URLS,
    KNOWN_CURRENT_INDEX_OMISSION_URLS,
    MAX_HTML_BYTES,
    MAX_INDEX_PAGES,
    MAX_PDF_BYTES,
    REQUEST_TIMEOUT_SECONDS,
)
from .errors import AfrUpdateError, AfrUpdateErrorCode


class AfrIndexLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_by_name = dict(attrs)
        href = attrs_by_name.get("href")
        if href is None:
            return
        self._active_href = href
        self._active_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._active_href is None:
            return
        text = " ".join(" ".join(self._active_text).split()).upper()
        if "APPLICABLE FEDERAL RATES" in text and self._active_href.endswith(".pdf"):
            self.links.append(self._active_href)
        self._active_href = None
        self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)


def validate_index_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.netloc != "www.irs.gov":
        raise AfrUpdateError(AfrUpdateErrorCode.BAD_SOURCE_URL)
    if parsed.path != "/applicable-federal-rates":
        raise AfrUpdateError(AfrUpdateErrorCode.BAD_SOURCE_URL)


def validate_pdf_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.irs.gov"
        or not parsed.path.startswith("/pub/irs-drop/")
        or not parsed.path.endswith(".pdf")
    ):
        raise AfrUpdateError(AfrUpdateErrorCode.BAD_PDF_URL)


def fetch_bytes(source_url: str, max_bytes: int) -> bytes:
    request = Request(
        source_url,
        headers={"User-Agent": "vanderbr-tax-afr-updater/1.0"},
        method="GET",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            if length_header is not None and int(length_header) > max_bytes:
                raise AfrUpdateError(AfrUpdateErrorCode.FETCH_TOO_LARGE)
            body = response.read(max_bytes + 1)
    except (OSError, URLError, ValueError):
        raise AfrUpdateError(AfrUpdateErrorCode.FETCH_FAILED) from None

    if len(body) > max_bytes:
        raise AfrUpdateError(AfrUpdateErrorCode.FETCH_TOO_LARGE)

    return body


def fetch_text(source_url: str) -> str:
    validate_index_url(source_url)
    body = fetch_bytes(source_url, MAX_HTML_BYTES)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise AfrUpdateError(AfrUpdateErrorCode.FETCH_FAILED) from None


def fetch_pdf_bytes(source_url: str) -> bytes:
    validate_pdf_url(source_url)
    return fetch_bytes(source_url, MAX_PDF_BYTES)


def fetch_pdf_text(source_url: str) -> str:
    return extract_pdf_text(fetch_pdf_bytes(source_url))


def extract_pdf_text(pdf_bytes: bytes) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        raise AfrUpdateError(AfrUpdateErrorCode.PDF_TEXT_EXTRACTOR_MISSING)

    try:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "source.pdf"
            output_path = Path(directory) / "source.txt"
            input_path.write_bytes(pdf_bytes)
            subprocess.run(
                [pdftotext, "-layout", str(input_path), str(output_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return output_path.read_text(encoding="utf-8")
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        raise AfrUpdateError(AfrUpdateErrorCode.PDF_TEXT_EXTRACTION_FAILED) from None


def discover_pdf_urls(index_url: str, backfill: bool) -> list[str]:
    urls: list[str] = []
    pages = range(MAX_INDEX_PAGES) if backfill else range(1)

    for page_number in pages:
        page_url = index_url if page_number == 0 else f"{index_url}?page={page_number}"
        html = fetch_text(page_url)
        parser = AfrIndexLinkParser()
        parser.feed(html)
        page_urls = [normalize_irs_pdf_url(urljoin(index_url, link)) for link in parser.links]
        if len(page_urls) == 0:
            if page_number == 0:
                raise AfrUpdateError(AfrUpdateErrorCode.NO_PDF_LINKS)
            break
        urls.extend(page_urls)
        if not backfill:
            break

    urls.extend(KNOWN_CURRENT_INDEX_OMISSION_URLS)
    if backfill:
        urls.extend(KNOWN_BACKFILL_INDEX_OMISSION_URLS)

    return dedupe_valid_pdf_urls(urls)


def dedupe_valid_pdf_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        validate_pdf_url(url)
        if url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped


def normalize_irs_pdf_url(source_url: str) -> str:
    # The IRS historical index has at least one double-encoded space in a PDF
    # href. Decode only that exact transport artifact so published file names
    # remain otherwise byte-for-byte stable.
    return source_url.replace("%2520", "%20")
