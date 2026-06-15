# IRS and Financial Rates

[![Validate Data Contract](https://github.com/vanderbr/rates/actions/workflows/validate.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/validate.yml)
[![Update Market Rates](https://github.com/vanderbr/rates/actions/workflows/update-market-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-market-rates.yml)
[![Update IRS Rates](https://github.com/vanderbr/rates/actions/workflows/update-irs-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-irs-rates.yml)
[![Update Annual IRS Rates](https://github.com/vanderbr/rates/actions/workflows/update-annual-irs-rates.yml/badge.svg)](https://github.com/vanderbr/rates/actions/workflows/update-annual-irs-rates.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Data: JSON + Protobuf](https://img.shields.io/badge/Data-JSON%20%2B%20Protobuf-2f6f4e.svg)](#data-contract)

Deterministic IRS, Treasury, and New York Fed source data for financial,
tax, and estate-planning software.

The repository publishes canonical JSON for auditability and generated
protobuf shards for fast ingestion. It publishes source observations and
statutory tables, not calculation advice.

## Use

Start at [`index.json`](index.json). For each dataset, verify the manifest hash
from the index, then verify the selected JSON or protobuf shard against the
manifest byte length and SHA-256 hash before decoding it.

Live coverage, record counts, first/last dates, schema ids, proto messages, and
artifact hashes are generated into `index.json` and each dataset
`manifest.json`. The README intentionally does not duplicate those volatile
values.

Audit reliance should use immutable commit SHAs or signed release tags whose
`Validate Data Contract` workflow passed. See [`AUDIT.md`](AUDIT.md).

## Data

| Family | Datasets |
| --- | --- |
| IRS monthly rates | [`7520/`](7520/), [`afr/`](afr/) |
| IRS annual exclusions and exemptions | [`annual-gift-exclusion/`](annual-gift-exclusion/), [`estate-gift-tax-exemption/`](estate-gift-tax-exemption/), [`gst-exemption/`](gst-exemption/), [`noncitizen-spouse-gift-exclusion/`](noncitizen-spouse-gift-exclusion/) |
| IRS actuarial tables | [`actuarial/`](actuarial/) |
| Market rates | [`treasury/treasury-yield-curve/`](treasury/treasury-yield-curve/), [`fed-funds/`](fed-funds/), [`sofr/`](sofr/) |

Each dataset directory contains a `metadata.json` file with source attribution
and field semantics.

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

Conventions:

- Rates are integer basis points.
- Dollar amounts are integer U.S. dollars.
- SOFR averages use basis points scaled by `1000`.
- The SOFR Index is scaled by `100000000`.
- Actuarial decimal quantities use fixed-scale integer fields ending in
  `_scaled_1e6`.
- Annual legal amounts use inclusive `period_start_date` and
  `period_end_date`.
- Invariant legal applicability, such as what an annual exclusion applies to,
  is stored once in `metadata.json`, not repeated in every record.

## Automation

Scheduled workflows update source data and open pull requests:

- [`update-market-rates.yml`](.github/workflows/update-market-rates.yml)
- [`update-irs-rates.yml`](.github/workflows/update-irs-rates.yml)
- [`update-annual-irs-rates.yml`](.github/workflows/update-annual-irs-rates.yml)

Validate locally:

```sh
make audit
```

Common maintenance commands:

```sh
make update
make update-backfill
make update-actuarial
make artifacts
make test
```

The validation workflow runs `protoc`, `buf lint`,
`buf generate --template buf.gen.yaml`, `python scripts/artifact_contract.py`,
`python scripts/audit_contract.py`, and `make test`.

The AFR and annual IRS updaters require `pdftotext` from Poppler for live PDF
extraction.

## License

Licensed under the [Apache License 2.0](LICENSE).
