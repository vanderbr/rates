# Audit Controls

This repository publishes deterministic source-data artifacts for estate and
financial planning systems. Audit use should rely on immutable commits or
signed tags with passing validation.

## Boundary

The repository publishes source observations and generated artifacts. It does
not provide legal, tax, actuarial, or investment advice.

Treat a revision as reliable only when:

- It is pinned to a commit SHA or signed tag.
- The `Validate Data Contract` workflow passed.
- `index.json`, selected manifests, and selected JSON or protobuf files match
  their byte lengths and SHA-256 hashes.
- Protobuf files are decoded with `proto/rates/v1` from the same revision.

## Repository Checks

The validation workflow:

- Rejects local OS files such as `.DS_Store`.
- Validates proto syntax and Buf lint rules.
- Regenerates manifests, schemas, index metadata, and protobuf shards.
- Runs repository invariant checks and the test suite.
- Fails when generated artifacts differ from committed files.

The audit script verifies:

- No `.DS_Store` or `__pycache__` files are present.
- The only proto source tree is `proto/rates/v1`.
- Proto files use the `rates.v1` package.
- Old proto namespace markers are absent.
- JSON files use deterministic canonical formatting.
- Manifests reference existing proto files and `rates.v1.*` messages.
- Each protobuf shard is referenced by one dataset manifest.

Run the same gate locally:

```sh
make audit
```

## GitHub Settings

For audited use of the default branch:

- Protect `main`.
- Require pull requests.
- Require the `Validate Data Contract` status check.
- Require branches to be current before merging.
- Restrict direct pushes.
- Require signed commits or signed release tags for audited releases.
- Use least-privilege GitHub Actions permissions.
- Require review for workflow-file changes.

Scheduled data updates should open pull requests. Publish generated changes
only after validation passes.

## Consumer Verification

Consumers should:

1. Fetch a pinned commit or signed tag.
2. Read `index.json`.
3. Verify the selected dataset manifest hash from `index.json`.
4. Read the selected dataset `manifest.json`.
5. Verify the selected file hash and byte length from the manifest.
6. Decode JSON with the matching schema, or protobuf with the matching
   `proto/rates/v1` message from the same revision.

Digest mismatches, missing files, unexpected schema ids, and unexpected proto
message names should be hard ingest failures.
