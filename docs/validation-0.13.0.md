# Michelangelo 0.13 validation

`0.13.0-oci.1-rc.2` is undergoing support-image remediation and live acceptance. It is not an approved public or production release. It selects upstream commit `22f282b7641ed88af97061c66040cdc18f04347d` with the reviewed OCI patch and dependency overlays.

## Application baseline verification

The following application results were established for rc.1. The application source patch is unchanged in rc.2; the changed installer and support-image builds require their own validation.

| Check | Observed result |
| --- | --- |
| Portable installer/package suite | 102 tests: 101 passed; one native-admission wrapper skipped in the portable run and exercised separately |
| Clean source reconstruction | Pinned source and patch verified; all six overlays applied twice; targeted Go suites and application compilation passed |
| Terraform packages | All three extracted profiles initialized and validated successfully |
| Python deployment readiness | Four deployment readiness tests passed |
| Native workload admission | 81 positive/negative cases passed |
| Studio | Type checking, 1,757 tests in 177 files, and production build passed |
| Chart and TLS post-renderer | 36 resources rendered; four application digest references retained; default services private |
| Linux application build | API server, worker and controller compiled for linux/amd64; all three service images and the UI image built locally |
| Application image scans | Zero High and Critical findings in each of those four rebuilt images using November 16, 2026 vulnerability data |

The Studio test wrapper now keeps a stable styling engine and uses unique class prefixes, preventing leaked style rules from hiding controls across tests. A Linux stress reproduction failed before this correction and passed all 50 repeated deployment-update submissions afterward. The production UI behavior was unchanged by this test-only correction.

Regression coverage includes resuming a suspended serving deployment with an unchanged template hash; replicas return to one and the private CPU deployment strategy remains Recreate.

The Go scope includes `auth/...`, API handler and header utilities, Kubernetes job mapping, `components/deployment/...`, `components/inferenceserver/...`, TriggerRun, worker configuration and deployment activities. The activity checks exercise both Cadence and Temporal serialization. The broader upstream worker-activity suite depends on Bazel-generated mocks and was not completed with plain `go test`.

The image scan result covers the four application images only. It does not cover every training/serving, identity, Router, workflow, gateway, bootstrap or OCI-managed image, nor establish exploitability or production security. Local results do not establish a fresh installation, migration or full-site recovery.

## Support-image remediation

The rc.1 pinned database setup and workflow images had critical/high findings in the November 16 scan. Rc.2 builds a client-only MySQL setup image, updates Envoy, and rebuilds Temporal 1.24.2 with reviewed dependency locks while retaining its server and schema versions. The namespace job uses the supported Temporal CLI. Source commits and executable image digests remain pinned.

Local prototype scans have zero critical/high findings for these four replacements. The installer suite passes 110 checks with one native-admission wrapper skipped, and the 36-resource chart check passes. Full reproducible-image and live acceptance results remain pending; these observations do not close the complete runtime-image gate.

## Remaining release gates

- Rebuild and inventory every supporting executable image and resolve applicable findings or coverage gaps.
- Install the exact candidate archive on an isolated OCI site; complete HTTPS, IAM access, database TLS, all eight workflows and negative artifact checks.
- Verify upgrade interruption, credential and certificate rotation, independent full application restore and authorized notification receipt.
- Verify secret-safe operational logging and exact-build evidence binding; preserve second-human checks as excluded rather than passed under the existing scope.

Existing-site cutover, public publication and repository visibility changes are outside this candidate push. GitHub CI independently validates the pushed revision and produces the three review archives only after its required checks pass.

Additional rc.2 verification found and corrected publication-path compatibility issues: the HTTP transcoder omitted EvaluationReportService, Go model immutability treated absent and empty repeated fields as different, and Python provenance comparison differed on emitted protobuf defaults. The complete Go API-handler suite and 19 publication-policy tests pass after correction; the live rerun remains required.

Compatible KubeRay 1.7.2 and Kyverno 1.19.2 images have local zero high/critical scans. The exact reproducible Kyverno image also passes, and the actual ClusterPolicy mutates both Ray pod templates with the required restricted service account. Helm rendering verifies immutable private image references and pull credentials. Kyverno 1.20 was rejected because it no longer enforces this policy format.

The rebuilt Caddy image retains a high-severity legacy JOSE finding. The rendered profile disables its administrative API and enables external ACME issuance without a local PKI or ACME-server handler. A digest-bound reachability disposition and live configuration verification remain required; this is not a zero-finding image.
