# Maintainer release procedure

Keep raw verification evidence, deployment inputs, state, image archives, and vulnerability reports outside the Git repository. Run from a clean checkout and bind results to the final commit and exact packaged bytes.

## Offline checks

Use Python 3.12 or later with PyYAML 6.0.3. Run the portable suite, `all-in-one/scripts/check-source.py`, and all three extracted Terraform profiles as configured in `workflows/validation.yml`. The source check requires Git and Go with automatic toolchain selection. Run the publication guard on the complete Git index and each archive; independently scan the entire history for secrets.

Build each profile using `all-in-one/scripts/package-stack.py --profile PROFILE --output PATH`. The three profile names are `all-in-one`, `terraform`, and `resource-manager`. Packaging rejects pre-existing output paths and emits a SHA-256 sidecar. Verify every manifest entry and local documentation link after extraction.

## Public-release evidence

The public-release profile uses schema version 2. Existing commands without `--profile` retain the schema-1 migration contract. Do not rewrite older evidence records as schema 2.

From an extracted all-in-one package, use both tools with the same inputs:

```sh
python3 -B scripts/site-acceptance.py init \
  --profile public-release --source-revision FULL_GIT_COMMIT \
  --package /protected/release.zip --source-manifest SOURCE-MANIFEST.json \
  --images /protected/images.json --cluster-uid CLUSTER_UID \
  --record /protected/site-acceptance.json
python3 -B scripts/release-gate.py init \
  --profile public-release --source-revision FULL_GIT_COMMIT \
  --package /protected/release.zip --source-manifest SOURCE-MANIFEST.json \
  --images /protected/images.json --cluster-uid CLUSTER_UID \
  --acceptance /protected/site-acceptance.json --record /protected/release-gate.json
```

Initialization creates pending records. Complete entries only from observed, reviewed results and hash-bound evidence. Use `check` with identical arguments to evaluate the records. Evidence paths must remain inside their respective record directory.

The public-release acceptance profile records its explicit access-check exclusions. Report exclusions separately from completed checks. Automated authorization and revocation regressions remain required. Studio logs and outputs are verified separately from upstream resource panels labeled Coming soon.

The public-release profile requires complete fresh installation, rerun, lifecycle, failure/recovery, independent full-site restoration, notifications, source review, and inventory evidence. It does not require migration of an existing installation, and never authorizes cutover or resource deletion.

## Security and provenance

Collect all running and job/init-container executable digests, including supporting and managed components. Scan each digest with current vulnerability data and package enumeration enabled. Public-release scans must be no older than seven days. Empty or unsupported package inventory is incomplete coverage, not a clean scan.

Resolve applicable High/Critical findings. Remaining scanner false positives require digest-bound analysis, a reviewer, and supporting evidence. Risk exceptions cannot satisfy this profile. A finding still present in the scanned digest cannot be cleared merely by marking it fixed.

The `package_source_provenance` record must show the final Git revision, clean checkout, exact packaging command, artifact checksum, and source-manifest comparison. Record actual resource and image inventories, not only intended manifests. `release_eligible` evaluates the supplied evidence; it does not certify arbitrary customer environments or set `production_ready` to true.

## Handoff

Prepare the release pull request, downloadable ZIPs and checksums, dependency inventory, and protected verification report. Retain upstream licenses and notices. Do not publish raw evidence or private deployment values. Merge, publish the release, and change repository visibility only after the release owner's final review.
