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

When `notification_email` is set, the package also checks `https://<public-hostname>/oauth2/ready` every 60 seconds using OCI Health Checks. Separate alarms cover failed readiness checks and missing measurements. This tests DNS, TLS, the public edge and OAuth2 Proxy readiness; it does not prove that an authenticated Studio session, model inference, or database operation succeeds. Load-balancer backend alarms remain separate because their node-level health checks can stay healthy while the application edge is down. Validate a controlled outage, recovery, and notification receipt before launch. Health Checks adds a managed monitoring resource and may incur charges.

The upstream bundled monitoring stack is disabled in this configuration. OCI monitoring does not imply that every upstream application metric is collected.

## Backups and restoration

MySQL is configured with seven-day backup retention and point-in-time recovery. Object Storage artifact versioning is enabled. Protect Terraform state, application and orchestration databases, artifact versions, configuration, encryption material, and the exact release/image inventory.

Before a consistent recovery checkpoint, pause submissions and schedules, drain active work, and suspend serving. Capture application and orchestration state together with referenced artifact versions. Restore into a separate environment, start the full application, and confirm historical workflow access and model execution before relying on the recovery procedure. A database restore alone does not establish application recovery.

Preserve new writes before reverting to an older database. This distribution does not automatically migrate an existing site's history or delete its resources.

## Upgrades and recovery

Retain the previous package, configuration, image digests, protected state, and compatible recovery checkpoint. Start the build host before rerunning installation from the new package.

For a builder created before SSH host-key pinning, obtain its public host key through authenticated OCI console access and set `bootstrap_ssh_host_key` before applying this package. For example, read `/etc/ssh/ssh_host_ed25519_key.pub` from that console. Do not establish trust using an unauthenticated SSH key scan. An absent or incorrect pin fails closed; the package does not replace an existing builder automatically. Leave this setting empty for new builders.

The installer enters maintenance and checks for active work before reconciling runtime resources. It preserves installer-owned pipeline and serving identities. If upgrade verification fails, it attempts to restore compatible runtime configuration; interrupted provisioning is not atomic.

Pre-existing workloads that fail template validation keep the site in maintenance. Drain and review them through authorized operator procedures. Rollback to a checkpoint predating the runtime-containment boundary is refused; use forward recovery for that case. Missing serving targets are restored by the installer, not recreated through caller-supplied connection settings.

## Cost and teardown

After installation, stop the build host between installer runs if it is not needed. Stopped instances still retain chargeable storage. Database storage, load balancers, volumes, and other retained resources can continue to incur charges.

Review a destroy plan and retention requirements before teardown. Nonempty buckets, persistent data, retained database backups, and shared IAM settings require explicit handling. Remove only resources belonging to the intended stack; do not reuse another deployment's Terraform state.
