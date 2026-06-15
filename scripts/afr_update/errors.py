# SPDX-License-Identifier: Apache-2.0

"""Typed updater errors suitable for deterministic command exits."""

from __future__ import annotations

from enum import Enum


class AfrUpdateErrorCode(Enum):
    BAD_PDF_URL = "bad_pdf_url"
    BAD_SOURCE_URL = "bad_source_url"
    CONFLICTING_RECORD = "conflicting_record"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    DUPLICATE_JSON_RECORD = "duplicate_json_record"
    DUPLICATE_SOURCE_RECORD = "duplicate_source_record"
    FETCH_FAILED = "fetch_failed"
    FETCH_TOO_LARGE = "fetch_too_large"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_JSON = "invalid_json"
    INVALID_PERCENT = "invalid_percent"
    MISSING_FIELD = "missing_field"
    NO_PDF_LINKS = "no_pdf_links"
    PDF_TEXT_EXTRACTION_FAILED = "pdf_text_extraction_failed"
    PDF_TEXT_EXTRACTOR_MISSING = "pdf_text_extractor_missing"
    WRITE_FAILED = "write_failed"


class AfrUpdateError(Exception):
    """Domain-specific failure for deterministic updater exits."""

    def __init__(self, code: AfrUpdateErrorCode) -> None:
        super().__init__(code.value)
        self.code = code
