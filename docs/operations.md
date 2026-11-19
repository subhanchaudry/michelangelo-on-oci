# Operations

## Access and sessions

Studio uses OCI IAM sign-in and the persistent application user directory. Browser sessions last 20 minutes and then require a fresh sign-in. Automatic browser-token refresh is disabled; signature and token-binding checks remain enabled. Application roles and removals survive installer reruns. Initial invitation variables do not continuously overwrite the directory.

Use the administrative interface for membership changes. Maintain an authorized administrator and protect the publisher and reader machine-account credentials separately from human sign-in.

## Model workflows

The distribution includes ranking, collaborative filtering, fraud/anomaly, housing regression, and BERT classification examples. Example datasets illustrate workflow behavior and do not establish customer-model quality.

Submit training through Studio and inspect the resulting run, source revision, logs, evaluation, and persisted artifacts. A model remains pending until successful outputs and evaluation permit publication. Failed candidates remain ineligible for deployment.

For an eligible model, run batch prediction or activate a private serving target, then inspect the output. Run larger examples sequentially within available capacity. Pause schedules and suspend serving targets when idle. Upstream Studio resource panels marked **Coming soon** remain upstream limitations; use OCI Console and authorized Kubernetes tooling for infrastructure inspection.

## Monitoring and notifications

Configure the package's OCI notification destination and budget settings for the site's owner. Confirm subscription delivery before relying on alerts. Monitor application availability, failed workloads, MySQL backup status, certificate expiry, and resource consumption. Investigate both alarm firing and resolution; notification configuration alone does not prove receipt.

When `notification_email` is set, the package also checks `https://<public-hostname>/oauth2/ready` every 60 seconds using OCI Health Checks. Separate alarms cover failed readiness checks and missing measurements. The check covers DNS, TLS, the public edge, and OAuth2 Proxy readiness. Monitor authenticated Studio access, model inference, and database operations separately. Load-balancer backend alarms remain separate because their node-level health checks can stay healthy while the application edge is down. Include alarm delivery and recovery notifications in your operational readiness procedure. Health Checks adds a managed monitoring resource and may incur charges.

The upstream bundled monitoring stack is disabled in this configuration. OCI monitoring does not imply that every upstream application metric is collected.

## Backups and restoration

MySQL is configured with seven-day backup retention and point-in-time recovery. Object Storage artifact versioning is enabled. Protect Terraform state, application and orchestration databases, artifact versions, configuration, encryption material, and the exact release/image inventory.

Before a consistent recovery checkpoint, pause submissions and schedules, drain active work, and suspend serving. Capture application and orchestration state together with referenced artifact versions. Restore into a separate environment, start the full application, and confirm historical workflow access and model execution before relying on the recovery procedure. A database restore alone does not establish application recovery.

Preserve new writes before reverting to an older database. This distribution does not automatically migrate an existing site's history or delete its resources.

### Resource metadata reconciliation

Preserve the destination's original database, configuration, resource inventory, and deployment replica settings before changing recovered connections. Keep submissions, schedules, workflow execution, and serving paused during reconciliation.

Compare source and destination catalog entries by resource kind, namespace, name, persisted metadata identity, and ownership references. Independent workflow runs can produce different catalogs. Review shared entries, source-only entries, and destination-only entries explicitly; similar generated names do not establish a mapping. Preserve the source history and the destination's rollback material. Stop for review when a mapping, finalizer, or dependent workload is unresolved.

After reconciliation, start the application through the normal installer and confirm historical workflow access, artifact integrity, and model execution. Rerun installation and confirm that recovered connections and data persist before completing the recovery cutover.

### Retaining restored connections

After restoring and verifying the database and artifact inventory, retain the recovered connections in `/opt/michelangelo/.runtime/recovery-connections.json` on the destination build host. The installer reads this protected profile on every installation, including when Terraform supplies the original site configuration again. It writes the effective configuration back to the protected installer configuration file. The destination keeps its cluster, OCI IAM client, registry access, network settings, and region.

The profile is an operator-managed JSON object with exactly three fields:

- `format`: `michelangelo-recovery-connections-v1`.
- `recipient`: the destination's exact `cluster_id`, `region`, `object_storage_namespace`, `oidc_issuer`, and `oidc_client_id` from its protected configuration.
- `connections`: `mysql_host`, `mysql_expected_hostname`, `mysql_ca_pem`, `mysql_username`, `mysql_password`, `artifact_bucket`, `artifact_access_key`, `artifact_secret_key`, `runtime_artifact_access_key`, and `runtime_artifact_secret_key` for the recovered data.

Use the restored database's private IPv4 address, its certificate hostname, and the matching trusted CA. Preserve artifact URI names and versions. The profile must be a root-owned regular file with mode `0600`, in a directory that only root can write. Symlinks, shared hard links, incomplete profiles, and recipient mismatches stop installation. Transfer credentials through protected recovery channels; never place this file in Git, an installation archive, shell arguments, or logs.

Before activation, reconcile restored resource metadata and retain the application and identity database passwords in their protected runtime files. Track the restored database and retained artifact services in the recovery environment's infrastructure state, with their retention and access policies. This profile changes application connections; it does not create resources, import Terraform ownership, replay workflow history, or establish backup consistency. Keep the original environment quiescent while verifying the restored application.

Back up this profile with the destination's protected recovery material. Update it when rotating recovered connection credentials. Removing it permits the next Terraform installation to use the original connections again; treat removal as an explicit data cutover and preserve any new writes first.

## Upgrades and recovery

Retain the previous package, configuration, image digests, protected state, and compatible recovery checkpoint. Start the build host before rerunning installation from the new package.

Keep `kubernetes_version` and `oke_node_image_id` set to the deployed values during routine application maintenance. The all-in-one release defaults to Kubernetes `v1.37.0`; earlier packages could select the newest regional version when this input was empty. Preserve an existing site's version explicitly before upgrading the package. Node images remain region-specific and are selected from the matching OKE catalog when no image is supplied. Review any cluster-version or node-image change in the plan as a separate infrastructure upgrade.

For a builder created before SSH host-key pinning, obtain its public host key through authenticated OCI console access and set `bootstrap_ssh_host_key` before applying this package. For example, read `/etc/ssh/ssh_host_ed25519_key.pub` from that console. Do not establish trust using an unauthenticated SSH key scan. An absent or incorrect pin fails closed; the package does not replace an existing builder automatically. Leave this setting empty for new builders.

The installer enters maintenance and checks for active work before reconciling runtime resources. It preserves installer-owned pipeline and serving identities. If upgrade verification fails, it attempts to restore compatible runtime configuration; interrupted provisioning is not atomic.

The installer binds external database, artifact-access, and TLS Secret revisions to the consuming application pod templates. When those credentials change, the affected deployments restart with the new values; unchanged credentials retain the same template revision. Keep credential recovery material protected and complete rotation through the installer while the site is in maintenance.

Pre-existing workloads that fail template validation keep the site in maintenance. Drain and review them through authorized operator procedures. Rollback to a checkpoint predating the runtime-containment boundary is refused; use forward recovery for that case. Missing serving targets are restored by the installer, not recreated through caller-supplied connection settings.

## Cost and teardown

After installation, stop the build host between installer runs if it is not needed. Stopped instances still retain chargeable storage. Database storage, load balancers, volumes, and other retained resources can continue to incur charges.

Review a destroy plan and retention requirements before teardown. Nonempty buckets, persistent data, retained database backups, and shared IAM settings require explicit handling. Remove only resources belonging to the intended stack; do not reuse another deployment's Terraform state.

## Application API connections

Application SDK calls wait for a connection during a transient DNS or API connection outage, within the existing 60-second deadline or a shorter caller-supplied deadline. Serving operations retain their 30-second request deadline. Authentication and application errors remain visible; requests already delivered to the server are not automatically replayed. If the deadline expires, inspect cluster DNS and API availability before resubmitting a mutating operation.
