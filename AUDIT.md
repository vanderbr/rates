# Audit Controls

This repository is designed to publish deterministic source-data artifacts for
estate-planning and financial-planning systems. Audit reliance should be based
on immutable commits or tags whose GitHub Actions validation completed
successfully.

## Reliance Boundary

The repository publishes official-source observations and deterministic derived
artifacts. It does not publish legal, tax, actuarial, or investment advice.
Consumers remain responsible for their own calculation assumptions, legal
interpretation, and suitability controls.

A consuming system should treat a repository revision as reliable only when all
of the following are true:

- The revision is a specific commit SHA or signed release tag, not a floating
  branch name fetched without verification.
- The `Validate Data Contract` workflow passed for that revision.
- The consumer verifies `index.json`, each selected dataset manifest, and each
  JSON or protobuf shard against the published byte lengths and SHA-256 hashes.
- The consumer decodes protobuf shards using the proto source under
  `proto/rates/v1` from the same revision.

## Enforced Repository Controls

The validation workflow enforces the published data contract with these checks:

- Rejects local OS metadata such as `.DS_Store`.
- Validates proto syntax with `protoc`.
- Validates Buf lint rules and the no-output generation template.
- Regenerates manifests, schemas, index metadata, and protobuf shards.
- Runs `scripts/audit_contract.py` to verify repository-level invariants.
- Runs the full updater and repository-layout test suite.
- Fails if regenerated artifacts differ from committed files.

The audit script verifies:

- No `.DS_Store` or `__pycache__` artifacts are present.
- The only proto source tree is `proto/rates/v1`.
- Proto files use the `rates.v1` package.
- Old proto namespace markers are absent from text artifacts.
- Every JSON file is deterministic canonical JSON.
- Every manifest references an existing proto file and `rates.v1.*` message.
- Every published protobuf shard is referenced by exactly one dataset manifest.

Run the same gate locally:

```sh
make audit
```

## Required GitHub Settings

These settings are not stored in repository files, but they are required before
downstream systems should rely on the default branch in an audit:

- Protect `main`.
- Require pull requests before merging.
- Require the `Validate Data Contract` status check.
- Require branches to be up to date before merging.
- Restrict who can push directly to protected branches.
- Require signed commits or signed release tags for audited releases.
- Keep GitHub Actions permissions at least-privilege for each workflow.
- Require review for workflow-file changes.

Scheduled data-update workflows should open pull requests rather than pushing
directly to `main`. A data-update pull request is publishable only after the
validation workflow passes on the generated artifact changes.

## Consumer Verification

Consumers should verify artifacts in this order:

1. Fetch a pinned commit or signed tag.
2. Read `index.json`.
3. Verify the selected dataset manifest hash from `index.json`.
4. Read the selected dataset `manifest.json`.
5. Verify the selected JSON or protobuf shard hash and byte length from the
   manifest.
6. Decode JSON with the matching JSON Schema or protobuf with the matching
   `proto/rates/v1` message from the same revision.

Digest mismatches, missing files, unexpected schema ids, or unexpected proto
message names should be hard ingest failures.
