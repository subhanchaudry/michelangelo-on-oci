# Third-party notices

Original OCI deployment and integration code in this repository is licensed under Apache-2.0. Third-party material retains its original license; the repository license does not replace those terms.

## Michelangelo AI

This distribution integrates Michelangelo AI v0.13.0, upstream commit `22f282b7641ed88af97061c66040cdc18f04347d`, from [michelangelo-ai/michelangelo](https://github.com/michelangelo-ai/michelangelo). Michelangelo AI is licensed under Apache-2.0. A copy is included in [LICENSES/Michelangelo-Apache-2.0.txt](LICENSES/Michelangelo-Apache-2.0.txt). The OCI overlays modify authentication, runtime configuration, dependency selection, and model lifecycle integration. Upstream copyright notices remain applicable.

## Temporal and Dockerize source builds

The workflow images rebuild Temporal 1.24.2 (MIT) with dependency updates and use Dockerize 0.16.0 (MIT). Their pinned source commits are recorded by the installer. Original license files are retained in built images. The Temporal server and MySQL schema sources remain on 1.24.2.

## Caddy, KubeRay, Kyverno, and Envoy Gateway source builds

The installer rebuilds Caddy 2.11.9, KubeRay 1.7.2, Kyverno 1.19.2, and Envoy Gateway 1.9.2 with pinned source commits and dependency updates. These projects use Apache-2.0; original license and applicable notice files are retained in the built images. Kyverno 1.19.2 retains the legacy ClusterPolicy behavior required by this distribution.

## SQLite source build

The identity, artifact-delivery, and CPU workflow images build SQLite 3.54.1 from checksum-pinned upstream source. SQLite is dedicated to the public domain. The source copyright disclaimer is retained in the installed library package. Source and checksum records are included in `DEPENDENCIES.json`.

## Dependencies and images

Release ZIPs contain deployment source and dependency instructions; they do not bundle upstream container-image layers or downloaded Python, Go, or operating-system packages. Installation obtains third-party software from the configured upstream sources and builds images in the customer's registry.

Dependency inputs are recorded in `DEPENDENCIES.json`, the Terraform provider lock files, and the all-in-one installer dependency locks. The installer records the executable digests it deploys. Retain the licenses and notices supplied in those components. Redistribution of built images requires reviewing their own terms, including source-availability obligations where applicable.

The deployment uses components including Kubernetes, Temporal, Ray, KubeRay, Triton, Envoy, MySQL Router, Caddy, OAuth2 Proxy, Redis, and OCI Terraform providers. These projects have independent licenses and maintenance policies. MySQL Router is obtained from Oracle Container Registry; its upstream license and source-distribution terms apply to any redistribution of that image.

Project and product names identify compatibility and do not imply sponsorship or certification.
