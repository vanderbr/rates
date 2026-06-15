#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Compatibility entry point for the IRS AFR updater."""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from afr_update import (  # noqa: E402
    AfrRateRecord,
    AfrUpdateError,
    AfrUpdateErrorCode,
    fetch_pdf_text,
    load_existing_records,
    main,
    merge_records,
    normalize_irs_pdf_url,
    parse_afr_record,
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
    "parse_afr_record",
    "update_from_index",
    "update_from_pdf_texts",
    "write_dataset_files",
    "write_records",
]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
