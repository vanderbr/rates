# SPDX-License-Identifier: Apache-2.0

"""IRS AFR updater package."""

from .cli import main
from .errors import AfrUpdateError, AfrUpdateErrorCode
from .fetch import fetch_pdf_text, normalize_irs_pdf_url
from .models import AfrRateRecord
from .parser import is_afr_ruling_text, parse_afr_record
from .store import (
    load_existing_records,
    merge_records,
    update_from_index,
    update_from_pdf_texts,
    write_dataset_files,
    write_records,
)

__all__ = [
    "AfrRateRecord",
    "AfrUpdateError",
    "AfrUpdateErrorCode",
    "fetch_pdf_text",
    "load_existing_records",
    "main",
    "merge_records",
    "normalize_irs_pdf_url",
    "is_afr_ruling_text",
    "parse_afr_record",
    "update_from_index",
    "update_from_pdf_texts",
    "write_dataset_files",
    "write_records",
]
