# Third-party notices

Original OCI deployment and integration code in this repository is licensed under Apache-2.0. Third-party material retains its original license; the repository license does not replace those terms.

## Michelangelo AI

This distribution integrates Michelangelo AI v0.12.0, upstream commit `ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3`, from [michelangelo-ai/michelangelo](https://github.com/michelangelo-ai/michelangelo). Michelangelo AI is licensed under Apache-2.0. A copy is included in [LICENSES/Michelangelo-Apache-2.0.txt](LICENSES/Michelangelo-Apache-2.0.txt). The OCI overlays modify authentication, runtime configuration, dependency selection, and model lifecycle integration. Upstream copyright notices remain applicable.

## Dependencies and images

Release ZIPs contain deployment source and dependency instructions; they do not bundle upstream container-image layers or downloaded Python, Go, or operating-system packages. Installation obtains third-party software from the configured upstream sources and builds images in the customer's registry.

Dependency inputs are recorded in `DEPENDENCIES.json`, the Terraform provider lock files, and the all-in-one installer dependency locks. The installer records the executable digests it deploys. Retain the licenses and notices supplied in those components. Redistribution of built images requires reviewing their own terms, including source-availability obligations where applicable.

The deployment uses components including Kubernetes, Temporal, Ray, KubeRay, Triton, Envoy, MySQL Router, Caddy, OAuth2 Proxy, Redis, and OCI Terraform providers. These projects have independent licenses and maintenance policies. MySQL Router is obtained from Oracle Container Registry; its upstream license and source-distribution terms apply to any redistribution of that image.

Project and product names identify compatibility and do not imply sponsorship or certification.
