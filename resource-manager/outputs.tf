output "cluster_id" {
  value       = oci_containerengine_cluster.this.id
  description = "OKE cluster OCID."
}

output "mysql_endpoint" {
  value       = oci_mysql_mysql_db_system.this.ip_address
  description = "Private MySQL endpoint for Helm values and Vault secret creation."
}

output "platform_compartment_id" {
  value       = oci_identity_compartment.platform.id
  description = "Dedicated Michelangelo AI compartment OCID."
}

output "vault_id" {
  value       = oci_kms_vault.this.id
  description = "Vault OCID for post-provisioning platform secrets."
}

output "vault_key_id" {
  value       = oci_kms_key.platform.id
  description = "Customer-managed encryption-key OCID used for platform secrets."
}

output "artifact_bucket" {
  value       = oci_objectstorage_bucket.artifacts.name
  description = "Michelangelo artifact bucket name."
}

output "team_artifact_buckets" {
  value       = { for name, bucket in oci_objectstorage_bucket.team_artifacts : name => bucket.name }
  description = "Per-team private artifact buckets; empty until ml_team_names is configured."
}

output "s3_compatibility_endpoint" {
  value       = "https://${var.object_storage_namespace}.compat.objectstorage.${var.region}.oci.oraclecloud.com"
  description = "Path-style OCI Object Storage S3 Compatibility API endpoint."
}

output "ocir_repository_url" {
  value       = oci_artifacts_container_repository.platform.id
  description = "Legacy name: this is a repository OCID, not an image URL. Prefer ocir_repository_id; resolve the regional registry host separately."
}

output "ocir_repository_id" {
  value       = oci_artifacts_container_repository.platform.id
  description = "OCIR repository OCID; not a Docker registry URL."
}

output "ocir_repository_name" {
  value       = oci_artifacts_container_repository.platform.display_name
  description = "Repository path to append to the site's verified regional registry host and namespace."
}

output "log_bucket" {
  value       = oci_objectstorage_bucket.logs.name
  description = "Private platform log bucket name for reviewed workflow log configuration."
}

output "bastion_id" {
  value       = oci_bastion_bastion.this.id
  description = "Bastion OCID for separately created temporary operator sessions."
}

output "s3_compatibility_host" {
  value       = "${var.object_storage_namespace}.compat.objectstorage.${var.region}.oci.oraclecloud.com"
  description = "S3 compatibility hostname without a scheme, for Helm objectStorage.endpoint with secure=true."
}

output "ocir_release_repositories" {
  value       = { for name, repository in oci_artifacts_container_repository.release_images : name => repository.id }
  description = "Optional OCIR component repositories. Immutability requires regional service support."
}
