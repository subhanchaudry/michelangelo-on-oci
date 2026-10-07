# michelangelo-on-oci

Deploy Michelangelo AI on Oracle Cloud Infrastructure using Terraform, OCI Resource Manager, or an all-in-one installer.

This repository contains deployment source for an internal pilot or customer evaluation. The complete installer selects Michelangelo **v0.12.0**, pinned to upstream commit `ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3`, and applies the included OCI integration overlays. The customer package version is **0.12.0-oci.1-rc.1**.

## Choose one deployment path

| Directory | What it deploys | How to run it |
| --- | --- | --- |
| `all-in-one/` | OKE, networking, MySQL, storage, registry, IAM integration, a build host, and the installed platform with lifecycle examples | Local Terraform or an OCI Resource Manager ZIP |
| `terraform/` | Infrastructure foundation only; creates a child compartment and infrastructure | Local Terraform; application installation is separate |
| `resource-manager/` | The same infrastructure foundation with Console input forms | OCI Resource Manager; application installation is separate |

**Use all-in-one for a complete new deployment.** It uses an existing workload compartment. The foundation alternatives use `compartment_id` as the parent for a new platform compartment. These are independent stacks: do not apply multiple paths to the same resources or share their Terraform state. The foundation stacks do not hand off automatically to the all-in-one installer.

The `all-in-one/installer/release/platform/releases/v0.11.0/` directory supplies compatibility build helpers and example runtime assets. It does not select the active application version; that is pinned in `all-in-one/installer/sources/v0.12.0/manifest.json`.

## Reference architecture

![Michelangelo on OCI: public load balancer and IAM sign-in, private OKE application and ML workloads, MySQL, Object Storage, OCIR, and installer host](docs/architecture.svg)

Open the [architecture guide](docs/architecture.md) for request and deployment flows, source references and scope. The [PNG](docs/architecture.png) supports presentations; the [SVG](docs/architecture.svg) preserves readable labels at full size. The diagram describes the **all-in-one** package, including its public build host and private OKE API. Foundation-only stacks install infrastructure separately from the application.

## About upstream Michelangelo AI

[Michelangelo AI](https://github.com/michelangelo-ai/michelangelo) is Uber's open-source ML platform. Its README describes training, evaluation, deployment, and monitoring capabilities, and marks the project as **beta**, with APIs and features subject to change. Python pipelines use `@task` and `@workflow` decorators.

This OCI repository adds deployment infrastructure and integration overlays. Its installed profile uses Temporal, Ray, Triton, OCI IAM, MySQL, Object Storage and OCIR. The bundled monitoring stack is disabled; upstream capabilities are not an OCI acceptance guarantee. Use the pinned [v0.12.0 source](https://github.com/michelangelo-ai/michelangelo/tree/ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3) when assessing compatibility; current upstream documentation may describe newer behavior.

Useful upstream references, reviewed October 7, 2026:

- [Project overview and local sandbox instructions](https://github.com/michelangelo-ai/michelangelo#readme), [documentation](https://michelangelo-ai.org/) and [Python examples](https://github.com/michelangelo-ai/michelangelo/tree/ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3/python/examples).
- [Contributing guidelines](https://github.com/michelangelo-ai/michelangelo/blob/main/CONTRIBUTING.md), [discussions](https://github.com/michelangelo-ai/michelangelo/discussions) and [security reporting](https://github.com/michelangelo-ai/michelangelo/blob/main/SECURITY.md).
- [Upstream Apache-2.0 license](https://github.com/michelangelo-ai/michelangelo/blob/ed2a2b09ed9fab57e1479f45e84330a78f3ff8d3/LICENSE); dependencies retain their own licenses.

## Prerequisites

- An OCI tenancy, its home region, an approved workload compartment, and capacity for the configured OKE nodes, build host, MySQL, volumes, public IP and load balancer. CPU defaults are in the Terraform files; GPU capacity is not required by the all-in-one profile.
- An OCI IAM identity domain and existing human users with the exact email addresses to invite. The applying identity needs permission to create the described infrastructure and to manage the stack's IAM application, machine accounts, groups, policies and dynamic group. Review `auth.tf`, `automation_identity.tf` and the other Terraform resources with your tenancy administrator.
- Owner contact email addresses for the two noninteractive accounts: one publishes images and artifacts; the other reads runtime images and artifacts. These are machine accounts, not two extra people signing into Studio. They may use the same owned contact mailbox if your IAM domain policy permits it. Human access tests still require distinct human users.
- Permission to enable public signing-verification keys on the existing identity domain, or confirmation that they are already enabled. `enable_public_signing_certificate_access=true` changes this **shared domain setting**; private keys remain private. Terraform destroy does not revert the setting. Set it to `false` only when the required public keys are already accessible.
- Outbound connectivity for upstream Git, image registries, package repositories, and certificate issuance. Inbound HTTPS serves Studio; operator SSH is restricted to the supplied IPv4 `/32`. MySQL, orchestration and inference stay behind cluster/private-network controls.
- Python 3.9+ for packaging. For local all-in-one execution, install the OCI Python SDK for the signing-key helper, configure your OCI credentials locally, and use Terraform 1.5.7 or later, below 2.0. Resource Manager supplies its job identity. Provider versions and checksums are pinned in each directory.

All-in-one defaults to an IP-based `sslip.io` hostname and public certificate issuance. For an owned `public_hostname`, create its DNS record pointing at the reserved public IP before certificate issuance can succeed. Keep access to the certificate authority available for renewal.

## Package the source

Run from the repository root. Keep release ZIPs and deployment workspaces outside this Git repository. The packager uses explicit file lists, verifies nested source hashes, checks for common private files/secrets, refuses to overwrite an existing release, and writes a SHA-256 checksum alongside each ZIP.

```sh
python3 all-in-one/scripts/check-publication.py
python3 all-in-one/scripts/package-stack.py \
  --profile all-in-one --output ../releases/michelangelo-all-in-one-0.12.0-oci.1-rc.1.zip
python3 all-in-one/scripts/package-stack.py \
  --profile terraform --output ../releases/michelangelo-terraform-0.12.0-oci.1-rc.1.zip
python3 all-in-one/scripts/package-stack.py \
  --profile resource-manager --output ../releases/michelangelo-resource-manager-0.12.0-oci.1-rc.1.zip
```

Verify a downloaded archive with `shasum -a 256 -c <archive>.sha256` from its containing directory. A checksum detects changed bytes; it is not a publisher signature. Use a trusted source for both archive and checksum.

## Complete platform: local Terraform

**Deploy from an extracted all-in-one ZIP**, not the source directory. Packaging supplies the source manifest required by the installer.

```sh
mkdir -p ../private-deployments/my-site
unzip ../releases/michelangelo-all-in-one-0.12.0-oci.1-rc.1.zip -d ../private-deployments/my-site
cd ../private-deployments/my-site
cp terraform.tfvars.example terraform.tfvars
# Edit every placeholder and review capacity, IAM and network settings.
terraform init
terraform plan -out=deployment.tfplan
terraform apply deployment.tfplan
terraform output application_url
```

The example selects Chicago (`us-chicago-1`); choose a supported destination region and your tenancy's actual home region. Supply an **existing** compartment OCID, your IAM domain URL, invited users, contact emails and your operator public IPv4 `/32`. For local Terraform keep `use_resource_manager_private_endpoint=false`. An optional operator SSH public key permits later builder access; never put a private key in the variables.

Apply creates infrastructure, builds and pushes images into your registry, installs the platform, configures authentication and seeds lifecycle examples. Builds and dependency downloads take time. Keep the applying job running and review its result; the existence of Terraform resources alone does not prove application readiness.

## Complete platform: OCI Resource Manager

1. Create a Resource Manager stack from the generated **all-in-one ZIP**. Use a Terraform version compatible with `versions.tf` (minimum 1.5.7).
2. Complete the Console fields from `schema.yaml`. Select the destination region and existing workload compartment. Keep `use_resource_manager_private_endpoint=true` so the job reaches the builder through the private endpoint.
3. Review the Plan, including IAM/domain changes and estimated resource consumption, then run Apply. Review installer/job logs and the `application_url` output.
4. Sign in as an invited IAM user and complete the checks below.

Do not upload credentials, a populated variable file, a saved plan, or Terraform state in the source ZIP. Manage sensitive deployment data through your approved Resource Manager/state controls.

## Infrastructure-only alternatives

For local Terraform, extract the **terraform ZIP** into its own private directory, copy `terraform.tfvars.example` to `terraform.tfvars`, and replace every placeholder. Supply the database password through `TF_VAR_mysql_admin_password` using your approved secret mechanism. Run `terraform init`, `terraform plan`, and `terraform apply` as above. This path requires Terraform 1.6 or later; see `versions.tf` for the exact bounds.

For Resource Manager, upload the **resource-manager ZIP** and complete its form. This profile pins Terraform to the 1.5 series (`>=1.5.7, <1.6.0`) and OCI provider 7.32.0. Both foundation paths create a child compartment and provision infrastructure; they do **not** install or expose a ready-to-use Studio. Application configuration, images, authentication and runtime installation remain your responsibility. Use all-in-one instead when you need the complete experience.

## Verify a new site

- Open `application_url`, sign in with an invited account, and verify an uninvited user is denied. Test a second user's role, removal, session revocation and restoration before sharing the site.
- Submit a training run through Studio. Verify the source revision, successful workflow, persisted logs/artifacts and checksums. A registered model must stay pending until successful outputs and evaluation permit publication; failed candidates must remain ineligible.
- Run batch prediction, inspect persisted output, deploy an eligible model, test prediction and invalid input, then suspend it. Verify ineligible models cannot be deployed. Start with small CPU examples and run larger model checks sequentially within available capacity.
- Exercise a bounded schedule, pause it, and verify that no new work launches. Suspend all serving targets when idle.
- Test backup restoration, certificate renewal, credential rotation, user revocation and upgrade recovery in your environment. Record cluster identity, package checksum and executable image digests with each result.

The reference implementation exercised eight real training flows and offline lifecycle flows, plus representative serving, Studio and restoration checks. **This curated customer package has not yet been applied to a fresh customer site.** Those results do not certify a new tenancy or constitute full acceptance. Image dependency findings remain to be reviewed and remediated; a private pilot's security exceptions do not transfer to customers. Scan the built application and supporting images and resolve or explicitly accept findings under your own policy before use.

Browser sessions expire after 20 minutes and require a fresh sign-in. Automatic token refresh is disabled while OCI refresh interoperability is being qualified. Token signature and binding checks remain enabled. The application directory preserves roles and removals across installer reruns; initial invitation variables do not continuously overwrite it. Project-level team isolation and high availability are outside this pilot profile.

## Updates, recovery and cost

Use a new versioned package and retain the prior package, image digests, configuration and protected state. The installer includes a maintenance gate and drain checks before runtime reconciliation, preserves installer-owned pipeline/target identities, and restores compatible runtime configuration when its upgrade verification fails. Rehearse upgrades and failure recovery; do not assume all interrupted Terraform/provisioner steps are atomic. Keep the build host available for installation, reruns and upgrades.

Before replacing an existing site, pause submissions and schedules, drain jobs, suspend serving targets, and capture consistent application **and orchestration** database backups plus artifact/object versions. Rehearse restoration separately. This repository does not automatically migrate old history or delete an older pilot. Preserve new writes before any rollback to an older database.

After successful installation and verification, the build host can be stopped to reduce compute cost; start it before the next installer run. Stopped instances still incur storage costs, and other resources such as database storage, load balancers and volumes continue to cost money. Use budgets and alerts, pause schedules and suspend serving when idle. Review a destroy plan and retained backups before teardown; bucket objects, persistent volumes and shared IAM settings need explicit retention decisions.

## Source changes and publication

Only deployment source, dependency locks, packaging helpers, reference documentation and this README belong in this repository. Generated state, plans, credentials, site variables, Kubernetes configuration, logs, archives and acceptance evidence belong in protected storage outside it. Terraform state contains generated private keys and passwords even when outputs are marked sensitive. Restrict and encrypt state and backups; treat saved plans and installer configuration as secrets.

Before committing or releasing:

```sh
python3 all-in-one/scripts/check-publication.py
# After staging, inspect the actual Git index as well:
python3 all-in-one/scripts/check-publication.py --staged
# Check each generated ZIP before uploading it:
python3 all-in-one/scripts/check-publication.py --archive ../releases/your-release.zip
```

Review the staged diff and run an independent secret scanner. The guard detects common patterns and private paths; it cannot prove that every confidential value has been removed. Changes to nested runtime inputs require reviewed updates to `installer/release/SOURCE-MANIFEST.json`; changes to all-in-one package membership require updating `all-in-one/PACKAGE-FILES.json`. Do not bypass mismatched hashes to ship unreviewed content.

Once your GitHub repository exists, configure its remote from this directory and use branches and pull requests. Do not initialize or push the parent project directory. Upstream software and dependencies retain their respective license obligations; confirm redistribution terms before publishing artifacts.
