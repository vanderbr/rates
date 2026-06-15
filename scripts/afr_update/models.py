# SPDX-License-Identifier: Apache-2.0

"""Data models for published IRS AFR records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class AfrRateRecord:
    effective_month: str
    revenue_ruling: str | None
    source_url: str
    applicable_federal_rates: dict[str, dict[str, dict[str, int]]]
    adjusted_applicable_federal_rates: dict[str, dict[str, int]]

    def to_json_object(self) -> dict[str, object]:
        return {
            "effective_month": self.effective_month,
            "applicable_federal_rates": self.applicable_federal_rates,
            "adjusted_applicable_federal_rates": self.adjusted_applicable_federal_rates,
        }

    def has_same_published_values(self, other: "AfrRateRecord") -> bool:
        return (
            self.effective_month == other.effective_month
            and self.applicable_federal_rates == other.applicable_federal_rates
            and self.adjusted_applicable_federal_rates
            == other.adjusted_applicable_federal_rates
        )
