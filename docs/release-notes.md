# Michelangelo on OCI 0.13.0-oci.1-rc.2

Release candidate for evaluation. Local application validation does not establish fresh-site acceptance or upgrade readiness.

## Changes from the 0.12 distribution

- Rebased the OCI compatibility layer on upstream 0.13, retaining package checksum enforcement, serving suspension, revision readiness and private CPU placement.
- Reconciled the browser integration with upstream proto3 JSON RPC and regenerated API descriptors.
- Kept OCI IAM membership and session enforcement in explicitly named OCI identity types alongside upstream authentication contracts. Upstream optional authentication modes are not a replacement for the OCI identity integration.
- Retained upstream per-replica rollout readiness and added OCI template reconciliation coverage.
- Updated the Go networking dependency to `golang.org/x/net v0.62.0` and the UI image's Alpine package fixes; rebuilt and scanned the four application images.
- Expanded source checks to authentication subpackages, deployment and inference packages, worker activity serialization, application builds, chart rendering and TLS transformation. Added a separate Studio CI job.

## Rc.2 support-image changes

- Build a MySQL client-only setup image and a patched Envoy image.
- Rebuild Temporal 1.24.2 and its SQL tool with updated dependencies, preserving the existing server and schema versions.
- Use the supported Temporal namespace CLI and reject deployment if reviewed support-image builds are missing.
- Build compatible KubeRay and Kyverno images with updated dependencies; preserve Envoy Gateway 1.9.2 while updating its Go dependencies.
- Correct model finalization across the HTTP gateway and protobuf JSON serialization while retaining immutable specification and provenance enforcement.
- Live acceptance and the complete deployed-image inventory remain required before publication.

## Live-acceptance corrections

- Default to two CPU workers so a lifecycle workflow and Triton model delivery have room to run concurrently. Upgrading the former default adds one worker and its boot volume.
- Add public HTTPS checks of the existing OAuth2 readiness endpoint, with failure and missing-measurement alarms. Node-level load-balancer health alone does not establish application-edge availability.
- Preserve upstream model-aware Triton readiness and configured probe timings in the OCI CPU overlay, so a restarted replica remains unready until its serving models are loaded.
- These changes remain under live acceptance; they do not close the recovery, image-review, or publication gates.

## Distribution

- Complete OCI deployment through local Terraform or OCI Resource Manager.
- Separate Terraform and Resource Manager infrastructure foundations.
- Michelangelo v0.13.0 pinned to upstream commit `22f282b7641ed88af97061c66040cdc18f04347d` with OCI integration overlays.
- OCI IAM sign-in, persistent application membership, private OKE workloads, database TLS, Object Storage artifacts, and private OCIR images.
- Eight CPU lifecycle examples spanning training, evaluation, registration, batch prediction, and serving.
- Versioned packages, SHA-256 checksums, source manifests, and dependency records.

## Compatibility

Kubernetes client 0.35 requires the `kubernetes_protomessage_one_more_release` build tag for this distribution's existing gogo protobuf workflow serialization. The packaged builder and source tests set it explicitly. Future Kubernetes upgrades must reassess this temporary upstream compatibility mechanism; omitting it breaks activity-result serialization.

Upstream 0.13 rejects unknown trainer column dtypes and supports string columns. Kueue scheduler implementation was reverted upstream; retained protobuf fields do not imply scheduler support. Optional SeaweedFS, GCS and LiteLLM integrations are not enabled by the default OCI profile.

The `installer/release/platform/releases/v0.11.0/` directory contains inherited compatibility helpers and runtime assets. It does not select the installed application version. Selection is controlled by the v0.13.0 source manifest.

Existing sites must follow the maintenance and recovery procedure before an upgrade. Historical database migration is not automatic. Runtime checkpoints predating the containment boundary cannot be restored by the installer.

## Operating boundaries

Browser sessions require a new sign-in after 20 minutes. The default CPU topology does not provide high availability, GPU execution, or project-level team isolation. Upstream **Coming soon** features retain their upstream status. See [security and operating boundaries](security.md) and [operations](operations.md) for deployment responsibilities.
