# Michelangelo 0.13 validation

`0.13.0-oci.1-rc.1` is a tested source candidate, not an approved public or production release. It selects upstream commit `22f282b7641ed88af97061c66040cdc18f04347d` with the reviewed OCI patch and dependency overlays.

## Local verification

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

## Remaining release gates

- Rebuild and inventory every supporting executable image and resolve applicable findings or coverage gaps.
- Install the exact candidate archive on an isolated OCI site; complete HTTPS, IAM access, database TLS, all eight workflows and negative artifact checks.
- Verify upgrade interruption, credential and certificate rotation, independent full application restore and authorized notification receipt.
- Verify secret-safe operational logging and exact-build evidence binding; preserve second-human checks as excluded rather than passed under the existing scope.

Existing-site cutover, public publication and repository visibility changes are outside this candidate push. GitHub CI independently validates the pushed revision and produces the three review archives only after its required checks pass.
