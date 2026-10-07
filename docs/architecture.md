# Michelangelo on OCI reference architecture

![Michelangelo on OCI reference architecture](architecture.svg)

This view documents the source of the all-in-one **0.12.0-oci.1-rc.1** package, using upstream Michelangelo **v0.12.0** with OCI overlays. Reviewed October 7, 2026. It is a deployment reference, not a live tenancy inventory or a customer acceptance record. The `terraform/` and `resource-manager/` foundation alternatives do not install these application services.

## Read the diagram

1. **Browser access:** DNS resolves to the reserved public IP on an OCI flexible load balancer in the public edge subnet. TCP ports 80 and 443 pass to the OKE identity edge. Caddy terminates TLS and manages ACME certificates; OAuth2 Proxy uses OCI IAM OIDC, then the application identity service checks application access before forwarding to Studio. Studio proxies API requests through Envoy. Certificates persist on an OKE PVC. No inference service is exposed as a separate public load balancer.
2. **Application and ML execution:** private OKE nodes run Studio, API, controllers, workers, Temporal and the lifecycle log reader. Controllers coordinate Ray training/batch jobs through KubeRay and model delivery to Triton. An internal Envoy Gateway uses a ClusterIP service. The default pools each have one node: system and CPU. GPU nodes are not configured. Component boxes are logical groupings, not dedicated node pools or enforced network isolation boundaries.
3. **Data:** OCI MySQL in the private endpoint subnet persists application metadata, Temporal state and application identities. MySQL connections use TLS with certificate checks; the application metadata path includes a MySQL Router sidecar. MySQL has seven-day backups and PITR configured. Private Object Storage holds models, run outputs and lifecycle logs through its HTTPS S3-compatible endpoint. A separate logs bucket is provisioned but the installer does not bind it to the lifecycle log runtime; do not infer a separate active logging pipeline from that resource.
4. **Deployment:** local Terraform or OCI Resource Manager provisions resources and invokes the public build host over SSH. Local access is restricted to the operator IPv4 `/32`; Resource Manager can use its optional private endpoint, omitted from this overview. The host builds from pinned source plus overlays, publishes images to private OCIR, and installs through the private OKE API. Runtime workloads pull images using the configured reader credentials. Publisher and reader machine accounts are separate from human Studio users. Keep the host available for reruns and upgrades; it may be stopped between installer runs.
5. **Routing:** the public edge uses an Internet Gateway. Private subnets route OCI service-network traffic through a Service Gateway and other outbound Internet traffic through a NAT Gateway. The build host also downloads upstream source and dependencies; DNS and ACME certificate issuance are external prerequisites. Gateway routing is shown as a note to keep the component flows readable. Arrows summarize primary dependencies, not every network connection or port.

## Scope and operating limits

This is a customer-operated evaluation profile. The configured deployment does not establish high availability, project-level team isolation, production scale or recovery readiness. The bundled monitoring stack is disabled. New-site acceptance, image security review, credential rotation, certificate renewal and backup restoration still require the checks in the repository README. The diagram contains no site identifiers, live addresses, credentials or measured capacity claims.

## Evidence in the deployment source

These links identify the files in the GitHub source repository; extracted release ZIPs retain this guide and its images.

| Area | Source |
| --- | --- |
| VCN, subnet and gateway placement | [network.tf](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/network.tf) |
| OKE private endpoint and node pools | [oke.tf](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/oke.tf) |
| MySQL, buckets and OCIR | [platform.tf](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/platform.tf), [mysql_tls.tf](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/mysql_tls.tf) |
| Build host and optional Resource Manager endpoint | [bootstrap.tf](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/bootstrap.tf) |
| IAM application and machine identities | [auth.tf](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/auth.tf), [automation_identity.tf](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/automation_identity.tf) |
| TCP load balancer, Caddy and OAuth2 Proxy | [installer/auth.py](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/installer/auth.py) |
| Persistent application identities | [installer/identity.py](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/installer/identity.py) |
| Temporal, KubeRay, internal Envoy Gateway, images and Helm | [installer/install.py](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/installer/install.py) |
| Runtime artifact and log configuration | [installer/runtime.py](https://github.com/subhanchaudry/michelangelo-on-oci/blob/main/all-in-one/installer/runtime.py) |

## Edit and export

The [SVG](architecture.svg) contains editable vector shapes and text. The [PNG](architecture.png) is a portable presentation export. Use the [Python source](render-architecture.py) to update the labeled components, boundaries and routed arrows, then regenerate from the repository root:

```sh
python3 docs/render-architecture.py
```

To refresh the PNG with the `sharp` Node package available in your environment:

```sh
node -e "require('sharp')('docs/architecture.svg').png().toFile('docs/architecture.png')"
```

Review the rendered output and source evidence after each edit. No deployment runs as part of diagram generation. On narrow displays, open the full-size image; the numbered flows above provide a text alternative.
