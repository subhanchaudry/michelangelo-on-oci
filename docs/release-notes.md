# Michelangelo on OCI 0.12.0-oci.1

## Distribution

- Complete OCI deployment through local Terraform or OCI Resource Manager.
- Separate Terraform and Resource Manager infrastructure foundations.
- Michelangelo v0.12.0 pinned to upstream commit `ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3` with OCI integration overlays.
- OCI IAM sign-in, persistent application membership, private OKE workloads, database TLS, Object Storage artifacts, and private OCIR images.
- Eight CPU lifecycle examples spanning training, evaluation, registration, batch prediction, and serving.
- Versioned packages, SHA-256 checksums, source manifests, and dependency records.

## Compatibility

The `installer/release/platform/releases/v0.11.0/` directory contains inherited compatibility helpers and runtime assets. It does not select the installed application version. Selection is controlled by the v0.12.0 source manifest.

Existing sites must follow the maintenance and recovery procedure before an upgrade. Historical database migration is not automatic. Runtime checkpoints predating the containment boundary cannot be restored by the installer.

## Operating boundaries

Browser sessions require a new sign-in after 20 minutes. The default CPU topology does not provide high availability, GPU execution, or project-level team isolation. Upstream **Coming soon** features retain their upstream status. See [security and operating boundaries](security.md) and [operations](operations.md) for deployment responsibilities.
