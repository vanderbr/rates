# IRS and Financial Rates

[![Validate Data Contract](https://github.com/vanderbr/rates/actions/workflows/validate.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/validate.yml)
[![Update Market Rates](https://github.com/vanderbr/rates/actions/workflows/update-market-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-market-rates.yml)
[![Update IRS Rates](https://github.com/vanderbr/rates/actions/workflows/update-irs-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-irs-rates.yml)
[![Update Annual IRS Rates](https://github.com/vanderbr/rates/actions/workflows/update-annual-irs-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-annual-irs-rates.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Data: JSON + Protobuf](https://img.shields.io/badge/Data-JSON%20%2B%20Protobuf-2f6f4e.svg)](#data-contract)

Published IRS, Treasury, and New York Fed rate data.

The repository provides JSON and generated protobuf files of source
observations and statutory tables.

## Use

Start at [`index.json`](index.json) for a catalog of the available datasets.
Each dataset also has a `manifest.json` with file paths, sizes, and checksums
for users who want to verify exact file contents.

Live coverage, record counts, first/last dates, schema ids, proto messages, and
artifact hashes are generated into `index.json` and each dataset
`manifest.json`. 

Audit reliance use immutable commit SHAs or signed release tags. See
[`AUDIT.md`](AUDIT.md).

## Static API

For simple on-the-fly lookups, generated static API files live under
[`api/v1/`](api/v1/). They contain the same records arranged as direct lookup
files.

```sh
BASE="https://raw.githubusercontent.com/vanderbr/rates/main"

# Discover datasets and latest generated API paths.
curl -fsSL "$BASE/api/v1/index.json"

# Latest generated records.
curl -fsSL "$BASE/api/v1/datasets/sofr/latest.json"
curl -fsSL "$BASE/api/v1/datasets/section-7520-rates/latest.json"
curl -fsSL "$BASE/api/v1/datasets/applicable-federal-rates/latest.json"
curl -fsSL "$BASE/api/v1/datasets/federal-funds/latest.json"
curl -fsSL "$BASE/api/v1/datasets/treasury-yield-curve/latest.json"

# Look up IRS monthly records by YYYY-MM.
curl -fsSL "$BASE/api/v1/datasets/section-7520-rates/by-month/2026-07.json"
curl -fsSL "$BASE/api/v1/datasets/applicable-federal-rates/by-month/2026-07.json"

# Look up annual IRS legal amounts by YYYY.
curl -fsSL "$BASE/api/v1/datasets/annual-gift-exclusion/by-year/2026.json"
curl -fsSL "$BASE/api/v1/datasets/estate-gift-tax-exemption/by-year/2026.json"
curl -fsSL "$BASE/api/v1/datasets/gst-exemption/by-year/1987.json"
curl -fsSL "$BASE/api/v1/datasets/noncitizen-spouse-gift-exclusion/by-year/2026.json"

# Look up market records by YYYY-MM-DD.
curl -fsSL "$BASE/api/v1/datasets/sofr/by-date/2026-06-12.json"
curl -fsSL "$BASE/api/v1/datasets/sofr-index/by-date/2026-06-15.json"
curl -fsSL "$BASE/api/v1/datasets/federal-funds/by-date/2026-06-12.json"
curl -fsSL "$BASE/api/v1/datasets/treasury-yield-curve/by-date/2026-06-15.json"
```

Each static API record includes the record, the dataset path, the manifest path,
and the natural record key. For audit-sensitive use, replace `main` with a
commit SHA or signed release tag.

Annual `by-year` files contain a `records` array because a year can have more
than one statutory period.

## Data

| Family | Datasets |
| --- | --- |
| IRS monthly rates | [`7520/`](7520/), [`afr/`](afr/) |
| IRS annual exclusions and exemptions | [`annual-gift-exclusion/`](annual-gift-exclusion/), [`estate-gift-tax-exemption/`](estate-gift-tax-exemption/), [`gst-exemption/`](gst-exemption/), [`noncitizen-spouse-gift-exclusion/`](noncitizen-spouse-gift-exclusion/) |
| IRS actuarial tables | [`actuarial/`](actuarial/) |
| Market rates | [`treasury/treasury-yield-curve/`](treasury/treasury-yield-curve/), [`fed-funds/`](fed-funds/), [`sofr/`](sofr/) |

Each dataset directory contains a `metadata.json` file with source attribution
and field semantics.

Monthly IRS rate history is stored in year shards and annual legal amount
history is stored as inclusive statutory periods. Coverage is intentionally
limited to records whose published fields can be represented by the dataset's
schema.

IRS revenue ruling PDFs retained for source review live under
[`sources/irs-revenue-rulings/`](sources/irs-revenue-rulings/), with a
reader-friendly source index.

## Data Contract

Primary records are deterministic JSON arrays sorted by their natural key.
Generated protobuf shards are produced from the same records. JSON Schemas live
under [`schemas/v1/`](schemas/v1/), and proto definitions live under
[`proto/rates/v1/`](proto/rates/v1/).

Each dataset family carries its own shape contract through `schema_id`,
`schema_version`, its JSON Schema, and its proto definition. Current data paths
are unversioned; consumers should treat the v1 schema/proto references in the
manifest as the authoritative record shape.

Single-file datasets use dataset-named records:

```text
<dataset>/<dataset-id>.json
<dataset>/metadata.json
<dataset>/manifest.json
<dataset>/protobuf/<dataset-id>.pb
```

Year-sharded datasets use:

```text
<dataset>/metadata.json
<dataset>/manifest.json
<dataset>/by-year/YYYY-<shard-id>.json
<dataset>/protobuf/YYYY-<shard-id>.pb
```

Use the manifest `years[].path` values as the source of truth. Examples include
`7520/by-year/YYYY-section-7520-rates.json` and `afr/by-year/YYYY-afr.json`.

Actuarial factor datasets are sharded by valuation rate:

```text
<dataset>/metadata.json
<dataset>/manifest.json
<dataset>/by-interest-rate/NNNNN-basis-points.json
<dataset>/protobuf/NNNNN-basis-points.pb
```

Static table collections, such as the SOA 2015 VBT, are sharded by table id:

```text
<dataset>/metadata.json
<dataset>/manifest.json
<dataset>/by-table/<table-id>.json
<dataset>/protobuf/<table-id>.pb
```

Conventions:

- Rates are integer basis points.
- Dollar amounts are integer U.S. dollars.
- Business-day datasets include only source-published observations; weekends,
  holidays, and unpublished dates are not represented by placeholder records.
- `sofr/` contains overnight SOFR observations only. SOFR averages and the SOFR
  Index are separate datasets under `sofr/`.
- SOFR averages use basis points scaled by `1000`.
- The SOFR Index is scaled by `100000000`.
- VBT mortality probabilities are scaled by `100000`.
- Actuarial decimal quantities use fixed-scale integer fields ending in
  `_scaled_1e6`.
- Annual legal amounts use inclusive `period_start_date` and
  `period_end_date`.
- Invariant legal applicability, such as what an annual exclusion applies to,
  is stored once in `metadata.json`, not repeated in every record.

## License

Licensed under the [Apache License 2.0](LICENSE).
