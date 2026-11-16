# Installation

## Prerequisites

- An OCI tenancy, its home region, and an existing workload compartment for all-in-one. Foundation packages instead create a child compartment under the supplied parent.
- Capacity for OKE nodes, a build host, MySQL, storage, public IP, and a load balancer. The complete profile uses CPU instances; GPU capacity is not required.
- An OCI IAM identity domain and existing invited users identified by exact email address. The applying identity needs permissions for the infrastructure, IAM application, machine accounts, groups, policies, and dynamic group described by the source.
- Owner contact addresses for the separate publisher and runtime-reader machine accounts. These can use the same owned contact mailbox if your domain permits it.
- Permission to enable public signing-verification keys on the identity domain, or confirmation they are already accessible. `enable_public_signing_certificate_access=true` changes a shared domain setting; destroy does not revert it. Private signing keys remain private.
- An operator public IPv4 address supplied as a `/32`. SSH to the public build host is restricted to this address. OCI Resource Manager can use its private endpoint.
- Outbound access to upstream Git, image registries, package repositories, and certificate issuance services.

For local all-in-one deployment, use Terraform `>=1.5.7, <2.0.0`, Python 3.9 or later, the OCI Python SDK, and your configured OCI credentials. The provider lock files select dependency versions. Resource Manager supplies its job identity.

The default hostname uses the reserved IP with `sslip.io`. For an owned `public_hostname`, point its DNS record to the reserved address before certificate issuance. Permit certificate-authority access for renewal.

## Verify and extract

Keep extracted deployment workspaces and sensitive state outside the source repository.

```sh
shasum -a 256 -c michelangelo-all-in-one-0.13.0-oci.1-rc.1.zip.sha256
mkdir my-site
unzip michelangelo-all-in-one-0.13.0-oci.1-rc.1.zip -d my-site
cd my-site
```

Obtain the ZIP and checksum from the same trusted release. A checksum detects changed bytes; it is not a publisher signature.

## Complete deployment with local Terraform

Run from the extracted all-in-one package:

```sh
cp terraform.tfvars.example terraform.tfvars
# Replace every placeholder and review IAM, networking, capacity, and monitoring.
terraform init
terraform plan -out=deployment.tfplan
terraform apply deployment.tfplan
terraform output application_url
```

Select the destination region and your tenancy's actual home region. Supply an existing compartment, the IAM domain URL, invited users, machine-account contact addresses, and the operator IPv4 `/32`. Set `use_resource_manager_private_endpoint=false` for local execution. An optional SSH public key enables later builder access; never supply a private key in variables.

Apply provisions infrastructure, builds and pushes images to your private registry, installs the platform, configures authentication, and registers lifecycle examples. Keep the applying job running through installer completion. If it fails, retain its protected logs and correct the reported error before rerunning.

## Complete deployment with Resource Manager

1. Create an OCI Resource Manager stack from the all-in-one ZIP.
2. Select a Terraform version satisfying the package's `versions.tf`.
3. Complete the Console fields, including the destination region and existing workload compartment. Keep `use_resource_manager_private_endpoint=true` for private job-to-builder connectivity.
4. Review Plan, including IAM/domain changes and resource consumption, then run Apply.
5. Review installer completion and open `application_url` as an invited IAM user.

Do not upload populated variables, credentials, saved plans, or Terraform state with the source ZIP. Protect sensitive deployment inputs through your Resource Manager and state-storage controls.

## Infrastructure foundations

The Terraform foundation requires Terraform `>=1.6.0, <2.0.0`. Extract its ZIP into a separate directory, populate `terraform.tfvars.example`, and provide the database password through `TF_VAR_mysql_admin_password` using your approved secret mechanism. Run init, plan, and apply there.

The Resource Manager foundation uses Terraform `>=1.5.7, <1.6.0` and OCI provider 7.32.0. Upload that ZIP and complete its Console form.

Both foundations create infrastructure and a child compartment. Application images, installation, authentication, and Studio configuration remain separate operator responsibilities. Use all-in-one for the complete installation path.

## First access

Open `application_url` and sign in with an invited IAM account. Confirm that Studio, project navigation, and workflow submission are available. Follow [operations](operations.md) to run the included examples and establish your site's backup and alerting procedures.
