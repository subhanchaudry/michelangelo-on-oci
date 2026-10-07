output "application_url" {
  value = "https://${local.public_hostname}"
}
output "reserved_public_ip" {
  value = oci_core_public_ip.application.ip_address
}
output "cluster_id" {
  value = oci_containerengine_cluster.platform.id
}
output "bootstrap_public_ip" {
  value = oci_core_instance.bootstrap.public_ip
}
output "bootstrap_instance_id" {
  value = oci_core_instance.bootstrap.id
}
output "bootstrap_ssh_private_key" {
  value     = tls_private_key.bootstrap.private_key_pem
  sensitive = true
}
output "artifact_bucket" {
  value = oci_objectstorage_bucket.artifacts.name
}
output "log_bucket" {
  value = oci_objectstorage_bucket.logs.name
}
output "mysql_endpoint" {
  value = oci_mysql_mysql_db_system.platform.ip_address
}
output "registry" {
  value = local.registry
}
output "kubernetes_version" {
  value = local.kubernetes_version
}

output "oke_node_image_id" {
  value       = local.node_image
  description = "Selected OKE image; pin this input with the version before future upgrades."
}
