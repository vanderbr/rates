# vanderbr/rates

[![Validate Data Contract](https://github.com/vanderbr/rates/actions/workflows/validate.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/validate.yml)
[![Update Market Rates](https://github.com/vanderbr/rates/actions/workflows/update-market-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-market-rates.yml)
[![Update IRS Rates](https://github.com/vanderbr/rates/actions/workflows/update-irs-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-irs-rates.yml)
[![Update Annual IRS Rates](https://github.com/vanderbr/rates/actions/workflows/update-annual-irs-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-annual-irs-rates.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Data: JSON + Protobuf](https://img.shields.io/badge/Data-JSON%20%2B%20Protobuf-2f6f4e.svg)](#storage-contract)

Machine-readable rate and exemption source data for financial, tax, and
estate-planning calculations.

This repository stores official-source observations as deterministic JSON plus
generated protobuf shards for fast typed ingestion from Rust and other
production code. It is source data, not advisory calculation logic: consumers
are responsible for the compounding, day-count, interpolation, legal
interpretation, and projection assumptions appropriate to their use case.

## Consumer Quick Start

Production consumers should start at [`index.json`](index.json), select a
dataset manifest, verify the manifest digest from the index, then verify each
JSON or protobuf shard before decoding it. JSON is the canonical audit format;
protobuf is the canonical fast-ingest format generated from the same records.

For example, a consumer that needs the 2026 Treasury curve should read:

```text
index.json
treasury/treasury-yield-curve/manifest.json
treasury/treasury-yield-curve/by-year/2026-treasury-yield-curve.json
treasury/treasury-yield-curve/protobuf/2026-treasury-yield-curve.pb
```

The manifest entry for the shard contains both JSON and protobuf byte lengths
and SHA-256 digests. Treat a digest mismatch as a hard ingest failure.

## Audit Reliance

Audit reliance should be based on immutable commits or signed release tags for
which the `Validate Data Contract` workflow has passed. The repository-level
controls, required GitHub branch-protection settings, and consumer verification
steps are documented in [`AUDIT.md`](AUDIT.md).

## What Is Included

The repository currently covers IRS statutory/tax datasets, IRS actuarial
tables, and daily market rate datasets used as inputs to discount-rate and
financial-projection calculators.

### IRS And Tax Datasets

| Dataset | Data | Metadata | Source | Frequency |
| --- | --- | --- | --- | --- |
| Section 7520 rates | [`7520/manifest.json`](7520/manifest.json), [`7520/rates.json`](7520/rates.json), [`7520/protobuf/rates.pb`](7520/protobuf/rates.pb) | [`7520/metadata.json`](7520/metadata.json) | [IRS Section 7520 interest rates](https://www.irs.gov/businesses/small-businesses-self-employed/section-7520-interest-rates) | Monthly |
| Applicable Federal Rates | [`afr/manifest.json`](afr/manifest.json), [`afr/by-year/`](afr/by-year/) | [`afr/metadata.json`](afr/metadata.json) | [IRS Applicable Federal Rates](https://www.irs.gov/applicable-federal-rates) | Monthly |
| Annual gift tax exclusion | [`annual-gift-exclusion/manifest.json`](annual-gift-exclusion/manifest.json), [`annual-gift-exclusion/rates.json`](annual-gift-exclusion/rates.json), [`annual-gift-exclusion/protobuf/rates.pb`](annual-gift-exclusion/protobuf/rates.pb) | [`annual-gift-exclusion/metadata.json`](annual-gift-exclusion/metadata.json) | [26 USC 2503(b)](https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section2503&num=0&edition=prelim), [IRS Form 709 instructions](https://www.irs.gov/instructions/i709) | Annual |
| Unified estate and gift tax exemption | [`estate-gift-tax-exemption/manifest.json`](estate-gift-tax-exemption/manifest.json), [`estate-gift-tax-exemption/rates.json`](estate-gift-tax-exemption/rates.json), [`estate-gift-tax-exemption/protobuf/rates.pb`](estate-gift-tax-exemption/protobuf/rates.pb) | [`estate-gift-tax-exemption/metadata.json`](estate-gift-tax-exemption/metadata.json) | [IRS Form 709 instructions](https://www.irs.gov/instructions/i709), [IRS Revenue Procedure 2025-32](https://www.irs.gov/pub/irs-drop/rp-25-32.pdf) | Annual |
| GST exemption | [`gst-exemption/manifest.json`](gst-exemption/manifest.json), [`gst-exemption/rates.json`](gst-exemption/rates.json), [`gst-exemption/protobuf/rates.pb`](gst-exemption/protobuf/rates.pb) | [`gst-exemption/metadata.json`](gst-exemption/metadata.json) | [IRS Form 709 instructions](https://www.irs.gov/instructions/i709), [IRS Revenue Procedure 2025-32](https://www.irs.gov/pub/irs-drop/rp-25-32.pdf) | Annual |
| Noncitizen spouse gift exclusion | [`noncitizen-spouse-gift-exclusion/manifest.json`](noncitizen-spouse-gift-exclusion/manifest.json), [`noncitizen-spouse-gift-exclusion/rates.json`](noncitizen-spouse-gift-exclusion/rates.json), [`noncitizen-spouse-gift-exclusion/protobuf/rates.pb`](noncitizen-spouse-gift-exclusion/protobuf/rates.pb) | [`noncitizen-spouse-gift-exclusion/metadata.json`](noncitizen-spouse-gift-exclusion/metadata.json) | [26 USC 2523(i)](https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section2523&num=0&edition=prelim), [IRS Form 709 instructions](https://www.irs.gov/instructions/i709) | Annual |

### IRS Actuarial Datasets

These datasets come from the [IRS actuarial tables](https://www.irs.gov/retirement-plans/actuarial-tables) used to value annuities, life estates, remainders, and reversions under section 7520.

| Dataset | Data | Metadata | Official source | Storage |
| --- | --- | --- | --- | --- |
| Prior Mortality Table 2000CM | [`table-2001/manifest.json`](table-2001/manifest.json), [`table-2001/rates.json`](table-2001/rates.json), [`table-2001/protobuf/rates.pb`](table-2001/protobuf/rates.pb) | [`table-2001/metadata.json`](table-2001/metadata.json) | [Table 2000CM](https://www.irs.gov/pub/irs-tege/table-2000cm.xls) | Single file |
| Life expectancy by age | [`actuarial/life-expectancy-by-age/manifest.json`](actuarial/life-expectancy-by-age/manifest.json), [`actuarial/life-expectancy-by-age/rates.json`](actuarial/life-expectancy-by-age/rates.json), [`actuarial/life-expectancy-by-age/protobuf/rates.pb`](actuarial/life-expectancy-by-age/protobuf/rates.pb) | [`actuarial/life-expectancy-by-age/metadata.json`](actuarial/life-expectancy-by-age/metadata.json) | [Table 2010CM](https://www.irs.gov/pub/irs-tege/table-2010cm-final.xlsx) | Single file |
| Mortality Table 2010CM | [`actuarial/mortality-table-2010cm/manifest.json`](actuarial/mortality-table-2010cm/manifest.json), [`actuarial/mortality-table-2010cm/rates.json`](actuarial/mortality-table-2010cm/rates.json), [`actuarial/mortality-table-2010cm/protobuf/rates.pb`](actuarial/mortality-table-2010cm/protobuf/rates.pb) | [`actuarial/mortality-table-2010cm/metadata.json`](actuarial/mortality-table-2010cm/metadata.json) | [Table 2010CM](https://www.irs.gov/pub/irs-tege/table-2010cm-final.xlsx) | Single file |
| Table B | [`actuarial/table-b/manifest.json`](actuarial/table-b/manifest.json), [`actuarial/table-b/by-interest-rate/`](actuarial/table-b/by-interest-rate/), [`actuarial/table-b/protobuf/`](actuarial/table-b/protobuf/) | [`actuarial/table-b/metadata.json`](actuarial/table-b/metadata.json) | [Table B](https://www.irs.gov/pub/irs-tege/table-b-final.xlsx) | By interest rate |
| Table D | [`actuarial/table-d/manifest.json`](actuarial/table-d/manifest.json), [`actuarial/table-d/by-interest-rate/`](actuarial/table-d/by-interest-rate/), [`actuarial/table-d/protobuf/`](actuarial/table-d/protobuf/) | [`actuarial/table-d/metadata.json`](actuarial/table-d/metadata.json) | [Table D](https://www.irs.gov/pub/irs-tege/table-d.xls) | By adjusted payout rate |
| Table H | [`actuarial/table-h/manifest.json`](actuarial/table-h/manifest.json), [`actuarial/table-h/by-interest-rate/`](actuarial/table-h/by-interest-rate/), [`actuarial/table-h/protobuf/`](actuarial/table-h/protobuf/) | [`actuarial/table-h/metadata.json`](actuarial/table-h/metadata.json) | [Table H](https://www.irs.gov/pub/irs-tege/table-h-2010cm-final.xlsx) | By interest rate |
| Table R(2) | [`actuarial/table-r2/manifest.json`](actuarial/table-r2/manifest.json), [`actuarial/table-r2/by-interest-rate/`](actuarial/table-r2/by-interest-rate/), [`actuarial/table-r2/protobuf/`](actuarial/table-r2/protobuf/) | [`actuarial/table-r2/metadata.json`](actuarial/table-r2/metadata.json) | [Table R(2)](https://www.irs.gov/pub/irs-tege/table-r2-2010cm-final.xlsx) | By interest rate |
| Table S | [`actuarial/table-s/manifest.json`](actuarial/table-s/manifest.json), [`actuarial/table-s/by-interest-rate/`](actuarial/table-s/by-interest-rate/), [`actuarial/table-s/protobuf/`](actuarial/table-s/protobuf/) | [`actuarial/table-s/metadata.json`](actuarial/table-s/metadata.json) | [Table S](https://www.irs.gov/pub/irs-tege/table-s-2010cm-final.xlsx) | By interest rate |
| Table U(1) | [`actuarial/table-u1/manifest.json`](actuarial/table-u1/manifest.json), [`actuarial/table-u1/by-interest-rate/`](actuarial/table-u1/by-interest-rate/), [`actuarial/table-u1/protobuf/`](actuarial/table-u1/protobuf/) | [`actuarial/table-u1/metadata.json`](actuarial/table-u1/metadata.json) | [Table U(1)](https://www.irs.gov/pub/irs-tege/table-u1-2010cm-final.xlsx) | By adjusted payout rate |
| Table U(2) | [`actuarial/table-u2/manifest.json`](actuarial/table-u2/manifest.json), [`actuarial/table-u2/by-interest-rate/`](actuarial/table-u2/by-interest-rate/), [`actuarial/table-u2/protobuf/`](actuarial/table-u2/protobuf/) | [`actuarial/table-u2/metadata.json`](actuarial/table-u2/metadata.json) | [Table U(2)](https://www.irs.gov/pub/irs-tege/table-u2-2010cm-final.xlsx) | By adjusted payout rate |
| Table Z | [`actuarial/table-z/manifest.json`](actuarial/table-z/manifest.json), [`actuarial/table-z/by-interest-rate/`](actuarial/table-z/by-interest-rate/), [`actuarial/table-z/protobuf/`](actuarial/table-z/protobuf/) | [`actuarial/table-z/metadata.json`](actuarial/table-z/metadata.json) | [Table Z](https://www.irs.gov/pub/irs-tege/table-z-2010cm-final.xlsx) | By adjusted payout rate |

### Market Rate Datasets

| Dataset | Data | Metadata | Official source | Frequency |
| --- | --- | --- | --- | --- |
| Daily Treasury par yield curve | [`treasury/treasury-yield-curve/manifest.json`](treasury/treasury-yield-curve/manifest.json), [`treasury/treasury-yield-curve/by-year/`](treasury/treasury-yield-curve/by-year/), [`treasury/treasury-yield-curve/protobuf/`](treasury/treasury-yield-curve/protobuf/) | [`treasury/treasury-yield-curve/metadata.json`](treasury/treasury-yield-curve/metadata.json) | [U.S. Treasury interest rate data](https://home.treasury.gov/resource-center/data-chart-center/interest-rates) | Business day |
| Effective Federal Funds Rate | [`fed-funds/manifest.json`](fed-funds/manifest.json), [`fed-funds/by-year/`](fed-funds/by-year/), [`fed-funds/protobuf/`](fed-funds/protobuf/) | [`fed-funds/metadata.json`](fed-funds/metadata.json) | [New York Fed rates](https://markets.newyorkfed.org/rates) | Business day |
| SOFR | [`sofr/manifest.json`](sofr/manifest.json), [`sofr/by-year/`](sofr/by-year/), [`sofr/protobuf/`](sofr/protobuf/) | [`sofr/metadata.json`](sofr/metadata.json) | [New York Fed SOFR](https://www.newyorkfed.org/markets/reference-rates/sofr) | Business day |
| 30-Day Average SOFR | [`sofr/sofr-30d-average/manifest.json`](sofr/sofr-30d-average/manifest.json), [`sofr/sofr-30d-average/by-year/`](sofr/sofr-30d-average/by-year/), [`sofr/sofr-30d-average/protobuf/`](sofr/sofr-30d-average/protobuf/) | [`sofr/sofr-30d-average/metadata.json`](sofr/sofr-30d-average/metadata.json) | [New York Fed SOFR](https://www.newyorkfed.org/markets/reference-rates/sofr) | Business day |
| 90-Day Average SOFR | [`sofr/sofr-90d-average/manifest.json`](sofr/sofr-90d-average/manifest.json), [`sofr/sofr-90d-average/by-year/`](sofr/sofr-90d-average/by-year/), [`sofr/sofr-90d-average/protobuf/`](sofr/sofr-90d-average/protobuf/) | [`sofr/sofr-90d-average/metadata.json`](sofr/sofr-90d-average/metadata.json) | [New York Fed SOFR](https://www.newyorkfed.org/markets/reference-rates/sofr) | Business day |
| 180-Day Average SOFR | [`sofr/sofr-180d-average/manifest.json`](sofr/sofr-180d-average/manifest.json), [`sofr/sofr-180d-average/by-year/`](sofr/sofr-180d-average/by-year/), [`sofr/sofr-180d-average/protobuf/`](sofr/sofr-180d-average/protobuf/) | [`sofr/sofr-180d-average/metadata.json`](sofr/sofr-180d-average/metadata.json) | [New York Fed SOFR](https://www.newyorkfed.org/markets/reference-rates/sofr) | Business day |

## Current Coverage

| Dataset | Records | First | Last |
| --- | ---: | --- | --- |
| Section 7520 rates | 354 | 1997-01 | 2026-06 |
| Applicable Federal Rates | 318 | 2000-01 | 2026-07 |
| Annual gift tax exclusion | 72 | 1955-01-01 | 2026-12-31 |
| Unified estate and gift tax exemption | 51 | 1977-01-01 | 2026-12-31 |
| GST exemption | 28 | 1999-01-01 | 2026-12-31 |
| Noncitizen spouse gift exclusion | 29 | 1988-07-14 | 2026-12-31 |
| Mortality Table 2010CM | 111 | Age 0 | Age 110 |
| Prior Mortality Table 2000CM | 111 | Age 0 | Age 110 |
| Life expectancy by age | 111 | Age 0 | Age 110 |
| Table B | 6,000 | 0.2% | 20.0% |
| Table D | 2,000 | 0.2% | 20.0% |
| Table H | 11,000 | 0.2% | 20.0% |
| Table R(2) | 610,500 | 0.2% | 20.0% |
| Table S | 11,000 | 0.2% | 20.0% |
| Table U(1) | 11,000 | 0.2% | 20.0% |
| Table U(2) | 610,500 | 0.2% | 20.0% |
| Table Z | 11,000 | 0.2% | 20.0% |
| Daily Treasury par yield curve | 9,120 | 1990-01-02 | 2026-06-15 |
| Effective Federal Funds Rate | 6,520 | 2000-07-03 | 2026-06-12 |
| SOFR | 2,049 | 2018-04-02 | 2026-06-15 |
| 30-Day Average SOFR | 1,571 | 2020-03-02 | 2026-06-15 |
| 90-Day Average SOFR | 1,571 | 2020-03-02 | 2026-06-15 |
| 180-Day Average SOFR | 1,571 | 2020-03-02 | 2026-06-15 |

## Storage Contract

All primary records are deterministic JSON arrays sorted by their natural time
key. Every dataset also has generated protobuf shards, a manifest with
byte-length and SHA-256 integrity metadata, a schema id, and a protobuf message
reference. The root [`index.json`](index.json) lists every dataset manifest and
its digest.

Small annual or monthly datasets use one file:

```text
<dataset>/rates.json
<dataset>/metadata.json
<dataset>/manifest.json
<dataset>/protobuf/rates.pb
```

Large daily or wide datasets are sharded by calendar year:

```text
<dataset>/manifest.json
<dataset>/metadata.json
<dataset>/by-year/YYYY-<dataset>.json
<dataset>/protobuf/YYYY-<dataset>.pb
```

Large actuarial factor datasets are sharded by valuation rate:

```text
<dataset>/manifest.json
<dataset>/metadata.json
<dataset>/by-interest-rate/NNNNN-basis-points.json
<dataset>/protobuf/NNNNN-basis-points.pb
```

The actuarial shard rate is stored once in `manifest.json` under
`shards[].interest_rate_basis_points` or
`shards[].adjusted_payout_rate_basis_points`. Rows inside a shard omit that
repeated rate field so consumers can deserialize smaller homogeneous records
after selecting the desired valuation-rate file.

Record objects intentionally do not contain per-record source URLs or fetch
URLs. Source attribution, units, date semantics, and storage rules live in the
dataset-level `metadata.json` file. Manifests are integrity indexes: they list
shard paths, record counts, first/last observation dates where applicable, JSON
byte lengths and SHA-256 hashes, protobuf paths, protobuf byte lengths,
protobuf SHA-256 hashes, schema ids, schema versions, and protobuf message
names.

Rates are stored as integer basis points. For example, `4.97%` is stored as
`497`. Missing or not-yet-published market observations are stored as `null`.
Dollar-denominated tax amounts are stored as integer U.S. dollars.
Annual exclusion and exemption datasets use inclusive effective date ranges so
period lookup code can be shared across annual, partial-year, and multi-year
unchanged amounts.
Actuarial decimal quantities are canonical fixed-scale integers with
`_scaled_1e6` suffixes. For example, `0.97801` is stored as `978010`.
Table D factors are generated from the IRS workbook's term unitrust formula
`(1 - adjusted_payout_rate) ^ term_years`, rounded to six decimal places before
scaling.

JSON Schemas live under [`schemas/v1/`](schemas/v1/). Protobuf source files live
under [`proto/rates/v1/`](proto/rates/v1/). Each dataset
family has its own proto file, and
[`proto/rates/v1/rates.proto`](proto/rates/v1/rates.proto)
imports them into an aggregate module.

## Update Automation

Market rates are updated by
[`update-market-rates.yml`](.github/workflows/update-market-rates.yml), which
runs on a daily GitHub Actions schedule after the prior U.S. business day would
normally be available from Treasury and the New York Fed.

IRS monthly rates are updated by
[`update-irs-rates.yml`](.github/workflows/update-irs-rates.yml). AFR and
Section 7520 data are checked after mid-month on the 15th, 22nd, and 28th.
Annual inflation-adjustment datasets run weekly during the
November-through-January publication window; schedule-aware updaters no-op
after the target year is present and defer again until the next November
window. Backfill commands merge the longest supported official-source history
for each dataset.

Run the updater suite locally:

```sh
make update
```

Backfill all supported histories:

```sh
make update-backfill
```

Run the repository tests:

```sh
make test
```

Regenerate manifests, schemas, and protobuf shards from the current canonical
JSON:

```sh
make artifacts
```

The AFR and annual IRS updaters require `pdftotext` from Poppler for live PDF
extraction. The GitHub Actions workflow installs `poppler-utils` before running
those updaters.

The actuarial updater is intended for manual refreshes when IRS actuarial tables
change. It uses only the Python standard library for the current IRS workbook
formats.

GitHub Actions run the artifact generator after each scheduled data update so
JSON, protobuf, schemas, manifests, and the root index stay in lockstep. The
validation workflow runs `protoc`, `buf lint`,
`buf generate --template buf.gen.yaml`, `python scripts/artifact_contract.py`,
`python scripts/audit_contract.py`, and `make test`.

Run the full local audit gate:

```sh
make audit
```

## Consumer Notes

Market-rate files preserve source-published observations only. They are not
discount curves by themselves. Present-value, GRAT, CLAT, and other
estate-planning calculators should choose their own curve construction,
compounding, day-count, interpolation, and date-window rules.

The Treasury yield curve dataset stores `par_yields_basis_points` as a fixed
array ordered by `treasury/treasury-yield-curve/metadata.json` so consumers can
map directly to compact typed arrays. The array preserves every
Treasury-published tenor currently present in the official feed, including bill
tenors that are not available for older years. This canonical curve is the only
Treasury source of truth in the repository; single-tenor Treasury directories
are intentionally absent until generated derived datasets are needed. New York
Fed datasets currently include EFFR, SOFR, SOFR percentiles and volume, SOFR
averages, and the SOFR Index. SOFR averages are stored as integer basis points
scaled by 1000, and the SOFR Index is stored as an integer scaled by 100000000.

The actuarial datasets include the IRS prior 2000CM mortality table for
valuation dates from May 1, 2009 through May 31, 2023, and the current 2010CM
tables for valuations on or after June 1, 2023. Table D is not mortality based
and applies before and after the regulatory mortality-table change.

## Known Source Gap

The IRS AFR index and likely direct PDF URLs did not provide a May 2001 AFR
revenue ruling during backfill. The AFR dataset is otherwise continuous from
January 2000 through July 2026.

## License

Licensed under the [Apache License 2.0](LICENSE).
