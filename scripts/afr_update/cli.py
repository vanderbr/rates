# SPDX-License-Identifier: Apache-2.0

"""Command-line interface for the IRS AFR updater."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .constants import AFR_INDEX_URL, DEFAULT_DATASET_DIR, DEFAULT_LEGACY_DATA_PATH
from .errors import AfrUpdateError, AfrUpdateErrorCode
from .store import update_from_index, update_from_pdf_texts


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch IRS AFR revenue rulings and merge them into local JSON."
    )
    parser.add_argument("--index-url", default=AFR_INDEX_URL)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument(
        "--data-path",
        type=Path,
        default=DEFAULT_LEGACY_DATA_PATH,
        help="Legacy all-history JSON file to migrate from when present.",
    )
    parser.add_argument("--input-text", type=Path)
    parser.add_argument("--input-source-url")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--archive-sources",
        action="store_true",
        help="Archive newly discovered monthly IRS revenue ruling PDFs.",
    )
    parser.add_argument(
        "--source-archive-dir",
        type=Path,
        default=Path("sources/irs-revenue-rulings"),
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    try:
        if args.input_text is None:
            if args.input_source_url is not None:
                raise AfrUpdateError(AfrUpdateErrorCode.INVALID_ARGUMENTS)
            source_count, existing_count, final_count, changed = update_from_index(
                index_url=args.index_url,
                dataset_dir=args.dataset_dir,
                write=args.write,
                backfill=args.backfill,
                legacy_data_path=args.data_path,
                archive_sources=args.archive_sources,
                source_archive_dir=args.source_archive_dir,
            )
        else:
            if args.input_source_url is None or args.backfill:
                raise AfrUpdateError(AfrUpdateErrorCode.INVALID_ARGUMENTS)
            text = args.input_text.read_text(encoding="utf-8")
            source_count, existing_count, final_count, changed = update_from_pdf_texts(
                [(args.input_source_url, text)],
                args.dataset_dir,
                args.write,
                args.data_path,
            )
    except (OSError, AfrUpdateError) as error:
        if isinstance(error, AfrUpdateError):
            print(f"afr_update_error={error.code.value}", file=sys.stderr)
        else:
            print("afr_update_error=io_failed", file=sys.stderr)
        return 1

    print(
        "afr_update "
        f"source_records={source_count} "
        f"existing_records={existing_count} "
        f"final_records={final_count} "
        f"changed={str(changed).lower()}"
    )
    return 0
