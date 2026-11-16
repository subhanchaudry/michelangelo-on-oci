# Michelangelo on OCI 0.13 release preparation

Preparation target: `0.13.0-oci.1-rc.1`. The installer now selects pinned 0.13 source after local reconstruction and compatibility checks. This remains a release candidate; the [validation record](validation-0.13.0.md) separates completed local checks from remaining release gates.

## Source baseline

- Upstream: [v0.13.0](https://github.com/michelangelo-ai/michelangelo/releases/tag/v0.13.0), published November 5, 2026 at 01:32:11 UTC (November 4 Eastern).
- Exact upstream commit: `22f282b7641ed88af97061c66040cdc18f04347d`.
- OCI preparation base: `ce4a63fce18fe253ccebfc4cf8c91c244812a3c7`.
- Previous upstream: `ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3` (`v0.12.0`).
- Working branch: `codex/release-0.13.0-oci.1`.
- Deliverables: all-in-one deployment, Terraform foundation, and Resource Manager foundation. Foundation packages do not install the application.

## Sequence and completion criteria

| Stage | Work | Completion evidence |
| --- | --- | --- |
| 1. Intake | Verify tag and private baseline; retain release metadata; calculate full endpoint tree diff rather than relying on the GitHub compare API's 300-file limit. Run baseline portable checks. | Exact revisions, complete changed-path inventory, baseline test receipt. |
| 2. Compatibility | Check every legacy patch section against 0.13. Classify retained, upstream-superseded, regenerated, and conflicting changes. Exercise each overlay on a disposable reconstruction. | Per-path disposition and a source reconstruction with no rejected hunks. |
| 3. Authentication | Reconcile OCI IAM sessions and application membership with upstream authenticators, OIDC, Kubernetes RBAC, worker middleware and runner tokens. Preserve denial defaults, actor attribution, rotating credentials and controller internal calls. | Positive and negative API tests; missing, expired, wrong-audience and revoked credentials; project boundaries; token rotation; log redaction. |
| 4. Application compatibility | Reconcile UI proto3 JSON with Envoy descriptors, model deployment readiness, revision lineage, cascade cancellation, scheduling windows/catchup, and trainer dtype validation. | Targeted Go, Python and UI checks, matching generated descriptors, rendered chart review and all eight example definitions. |
| 5. Dependencies | Rebase existing dependency corrections onto the new module/lock inputs. Do not copy the 0.12 Go module over the new upstream module or undo upstream security fixes. Rebuild application and supporting images. | Resolved dependency locks, build logs, SBOM/package coverage, immutable image digests and current vulnerability dispositions. |
| 6. Package integration | Add a reviewed 0.13 source manifest and patch; update installer commit, distribution records, CPU runtime version, allowlists, notices, docs and CI. Recompute nested source manifests. | Reconstructed-source tests, portable suite, native admission tests, all three extracted Terraform validations, publication/secret checks and deterministic archive checksums. |
| 7. Isolated qualification | Fresh install and rerun; test administrator access, TLS, workflows, serving, schedules, artifact denials, recovery, rotation and observability on the exact candidate. | Exact package/source/image binding for every result; failed and untested checks remain distinct. |
| 8. Release decision | Reconcile evidence, documentation, compatibility limits, image findings and cleanup; assemble review artifacts. | Readiness report, release notes, upgrade/rollback guide, package hashes and explicit open gates. |

Stages 1–2 precede version selection. Stages 3–5 feed stage 6. Live qualification uses the exact stage 6 archives; any subsequent runtime-affecting change invalidates affected results. Public publication, merging and existing-site cutover are separate decisions.

## Changes requiring special attention

| Upstream change | OCI integration response |
| --- | --- |
| Pluggable bearer authentication, OIDC, Kubernetes SubjectAccessReview and per-project runner tokens | Evaluate upstream contracts before replacing custom behavior. Upstream feature availability alone does not establish OCI IAM compatibility or team isolation. |
| API sensitive-header redaction | Retain upstream protection and audit custom logging; update overlays that expect the former log statement. |
| UI uses proto3 JSON with Envoy; service list generated from descriptors | Rebase RPC/UI patches and regenerate protocol assets with the reviewed toolchain. Do not carry an old binary descriptor into 0.13 without comparison. |
| Per-replica rollout readiness | Compare the OCI deployment readiness patch with upstream and retain only required differences; test stalled and partial rollouts. |
| TriggerRun cascade cancellation, delete guards, windows and catchup | Exercise active child cancellation, paused triggers, window boundaries, retries and deletion; preserve lineage and revision pinning. |
| String dtype support and rejection of unknown trainer dtypes | Audit example schemas, ordered column lists, validation and negative inputs. |
| Kueue implementation reverted while proto remains | Do not advertise scheduler functionality based on retained protobuf fields. Keep the existing supported scheduling path explicit. |
| Go SDK 1.26.9 and Kubernetes client library changes | Reconcile local build tooling, reviewed Go modules, controller behavior and OKE compatibility. |
| SeaweedFS, GCS and LiteLLM gateway options | Keep outside the default OCI Object Storage/CPU profile; do not enable new optional services as part of this upgrade. |

## Isolated acceptance matrix

1. Install from an extracted archive into an isolated site, rerun safely and verify private workload placement and image provenance.
2. Verify HTTPS issuance and renewal, IAM browser login, persistent membership, session expiry/revocation, API authorization and TLS rejection of untrusted database certificates.
3. Run the eight included workflows through training, evaluation, eligible publication, lineage, persisted logs, independent inference, batch prediction, A/B/A serving and suspension/reactivation. Retain quality thresholds.
4. Reject missing, corrupted, incompatible and ineligible artifacts; restore exact known-good artifacts and retest. Check runner publication denial and bounded serving permissions.
5. Exercise trigger windows/catchup, pause/resume, cancellation with active children, retries and token renewal.
6. Rotate credentials and certificates, check cold starts, interrupt installation/upgrade at documented boundaries, and prove rollback only across explicitly supported versions.
7. Restore databases, objects and application configuration into an independent site. Start the full application and run recovered workflows; database hash agreement alone is insufficient.
8. Verify failure, backup and budget notification receipt only within the authorized recipient scope. Audit secret handling in plan/apply/destroy logs without exporting credentials into diagnostic records.
9. Verify all image inventory coverage and applicable High/Critical findings against the final executable digests. Preserve unresolved vendor-managed findings as open gates.
10. Record cleanup and retained resources separately from functional acceptance.

## Existing acceptance limits

The prior release-preparation report did not establish release readiness. Its final dependency patches lacked rebuilt-image verification; live HTTPS, full recovery and notification delivery were incomplete, and operational logging needed remediation. These are carry-forward verification requirements, not current 0.13 findings or automatically closed issues.

Second-human checks remain excluded under the existing scope; exclusions must not be reported as passes. Existing pilot migration and cross-version rollback require separate qualification. No cloud deployment, public release or production certification follows from local checks.
