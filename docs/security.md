# Security and operating boundaries

## Identity and access

OCI IAM authenticates human users; the application directory controls application access. Publisher and runtime-reader machine accounts have separate responsibilities. The compute manager manages Ray in `default` and observes Pods and logs with read-only access. Native admission checks runtime templates and generated workloads. The shared lifecycle identity is restricted to catalog registration and existing serving targets.

This configuration does not provide project-level team isolation or a separate workload identity for each catalog workflow. Deploy workloads within an appropriate shared trust boundary. The default topology does not provide high availability or a customer capacity commitment.

## Network and data

The public load balancer exposes Studio HTTPS through the identity edge. A public build host accepts SSH from the supplied operator `/32`; the OKE API, application workers, database, orchestration, and inference services use private-network controls. Do not expose private model-serving endpoints without an additional reviewed access design.

Remote database connections enforce TLS and certificate validation. The application-to-Router leg stays on pod-local loopback and permits plaintext; Router verifies the remote MySQL server's certificate and identity. Object Storage and registry access use the site's scoped credentials. Keep certificate-authority connectivity available for renewal.

Terraform pins the build host's SSH identity before sending installation credentials. New builders receive a unique host key through OCI creation metadata. Restrict access to that metadata and to Terraform state, which contains generated keys and passwords even when outputs are marked sensitive. Encrypt and restrict state, saved plans, backups, installer configuration, and logs. Do not commit populated variables, credentials, state, or Kubernetes configuration to a source repository.

## Software maintenance

The distribution pins upstream source, integration inputs, and build dependencies. Installation records executable image digests. Retain those records so security findings can be associated with the images actually running at your site.

Review current vulnerabilities throughout the deployment lifetime. Assess vulnerabilities against your deployed image inventory, workload exposure, and access controls. Apply updates through the documented maintenance and recovery procedures.

## Component responsibilities

Customers own tenancy permissions, workload suitability, capacity, access administration, notifications, retention, and recovery objectives. This repository does not confer Oracle or Uber product certification, a managed-service SLA, or an external security approval.

Upstream functionality labeled **Coming soon** retains that status. The OCI distribution does not change upstream support commitments or guarantee features described by a newer upstream version.

The trusted serving-manager identity can POST through the Kubernetes pod proxy in the dedicated model project namespaces. Michelangelo 0.13 uses this to check model readiness on each replica before routing traffic. Kubernetes RBAC cannot restrict this permission to a particular HTTP path; keep the serving credential confined to the controller. Workflow runners and the compute-manager identity do not receive this permission.
