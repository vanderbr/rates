# SPDX-License-Identifier: Apache-2.0

"""Constants and recognizers for IRS AFR revenue rulings."""

from __future__ import annotations

import re
from pathlib import Path


AFR_INDEX_URL = "https://www.irs.gov/applicable-federal-rates"
DEFAULT_DATASET_DIR = Path("afr")
DEFAULT_LEGACY_DATA_PATH = Path("afr/rates.json")
KNOWN_CURRENT_INDEX_OMISSION_URLS = [
    "https://www.irs.gov/pub/irs-drop/rr-26-09.pdf",
]
KNOWN_BACKFILL_INDEX_OMISSION_URLS = [
    "https://www.irs.gov/pub/irs-drop/rr-13-09.pdf",
    "https://www.irs.gov/pub/irs-drop/rr-13-11.pdf",
]
MAX_INDEX_PAGES = 20
MAX_HTML_BYTES = 2_000_000
MAX_PDF_BYTES = 4_000_000
REQUEST_TIMEOUT_SECONDS = 30
COMPOUNDING_KEYS = ["annual", "semiannual", "quarterly", "monthly"]
TERM_LABELS = {
    "short-term": "short_term",
    "mid-term": "mid_term",
    "long-term": "long_term",
}
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
REVENUE_RULING_PATTERN = re.compile(r"Rev\. Rul\. ([0-9]{4})-([0-9]{1,3})")
MONTH_PATTERN = re.compile(
    r"Applicable Federal Rates \(AFR\) for "
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December) ([0-9]{4})"
)
ADJUSTED_MONTH_PATTERN = re.compile(
    r"Adjusted AFR for "
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December) ([0-9]{4})"
)
TABLE_ROW_PATTERN = re.compile(
    r"^(?P<label>(?:[0-9]{3}[%$] )?AFR|adjusted AFR)\s+"
    r"(?P<annual>[0-9.]+%)\s+"
    r"(?P<semiannual>[0-9.]+%)\s+"
    r"(?P<quarterly>[0-9.]+%)\s+"
    r"(?P<monthly>[0-9.]+%)$",
    re.IGNORECASE,
)
ADJUSTED_TERM_ROW_PATTERN = re.compile(
    r"^(?P<term>Short-term|Mid-term|Long-term)\s+"
    r"(?P<annual>[0-9.]+%)\s+"
    r"(?P<semiannual>[0-9.]+%)\s+"
    r"(?P<quarterly>[0-9.]+%)\s+"
    r"(?P<monthly>[0-9.]+%)$",
    re.IGNORECASE,
)
