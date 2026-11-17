# Michelangelo on OCI 0.13.0-oci.1

Michelangelo on OCI 0.13.0-oci.1 integrates Michelangelo AI v0.13.0 with OCI infrastructure, identity, storage, and private model serving. Deploy the platform in your own tenancy through Terraform or OCI Resource Manager.

## What's included

- **Model lifecycle:** Studio workflows for training, evaluation, model registration, batch prediction, and private serving, with immutable model provenance and artifact checksum enforcement.
- **OCI identity:** IAM sign-in, persistent application membership, separate publisher and runtime-reader accounts, and restricted workflow permissions.
- **Private application services:** OKE workloads, managed MySQL with remote TLS verification, Object Storage artifacts, and private OCIR images.
- **Eight CPU examples:** ranking, collaborative filtering, fraud/anomaly detection, housing regression, and BERT classification workflows.
- **Deployment choices:** a complete all-in-one package and separate Terraform and Resource Manager infrastructure foundations, each with SHA-256 checksums and a source manifest.

## Changes in this release

The OCI integration now uses pinned Michelangelo v0.13.0 source. Studio communication follows upstream proto3 JSON contracts. Model publication preserves immutable specifications and provenance across the HTTP gateway and native APIs.

Model serving retains upstream per-replica, model-aware readiness. The OCI CPU configuration preserves probe settings so replicas wait for their serving models to load before becoming ready. Canary deployment and rollback carry the expected artifact checksums through model delivery. Deployment workflows track the requested model revision through controller reconciliation, so an earlier rollout’s outcome cannot be reported as the result of a new deployment.

CPU workflows use the system DNS resolver for private Kubernetes service names. The installer recompiles lifecycle packages when their configuration or compiler changes, preserving prior package output while applying the updated workflow environment.

The default topology includes one system node and two CPU workers, providing scheduling room for lifecycle workflows alongside model serving. Upgrading from the previous single-worker default adds a CPU worker and boot volume, with corresponding charges. Configure `cpu_node_count` for your workload concurrency.

Optional owner notifications include external HTTPS monitoring of the OAuth2 readiness endpoint, plus alarms for failed checks and missing measurements. Application-edge monitoring complements the existing node-level load-balancer alarms.

The all-in-one Kubernetes default is fixed at `v1.37.0`. Newer regional versions require an explicit selection, preventing an ordinary application maintenance plan from silently selecting a cluster upgrade. Existing sites should retain their deployed version and node image in their configuration.

The Temporal readiness helper uses a pinned Alpine image with an explicit operating-system package inventory. Python runtime images include SQLite 3.54.1, built from pinned upstream source with FTS5, session APIs, and limited-update/delete support.

Supporting components use pinned dependencies and OCI integration builds, including the MySQL setup client, Temporal and its SQL tooling, Envoy, Envoy Gateway, KubeRay, and Kyverno. See [DEPENDENCIES.json](../DEPENDENCIES.json) for component versions and source records.

## Installation and upgrades

Follow the [installation guide](installation.md) to select a package, verify its checksum, and deploy. The all-in-one package installs the application; the foundation packages provision infrastructure only.

Before upgrading, retain the previous package, configuration, image inventory, protected state, and a consistent recovery checkpoint. Follow the maintenance and recovery procedure in [operations](operations.md). The installer preserves application membership and installer-owned pipeline and serving identities. Historical database migration is not automatic; runtime checkpoints predating the containment boundary require forward recovery.

## Compatibility and operating scope

The distribution pins upstream commit `22f282b7641ed88af97061c66040cdc18f04347d`. Upstream 0.13 supports string trainer columns and rejects unknown column types. Kueue scheduling is not enabled. Optional SeaweedFS, GCS, and LiteLLM integrations are outside the default OCI profile.

The packaged Go builder includes the Kubernetes protobuf compatibility flag required by this upstream version. The inherited `installer/release/platform/releases/v0.11.0/` directory supplies compatibility helpers; the v0.13.0 source manifest selects the installed application version.

Browser sessions require sign-in after 20 minutes. The default CPU topology does not provide high availability, GPU execution, or project-level team isolation. Example workflows illustrate platform operation; customers assess model quality with their own data and evaluation criteria. Upstream features marked **Coming soon** retain that status.

See [architecture](architecture.md), [operations](operations.md), and [security](security.md) for deployment responsibilities and configuration boundaries.
