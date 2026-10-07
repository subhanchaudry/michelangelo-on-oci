resource "random_password" "mysql" {
  length           = 24
  special          = true
  override_special = "!#%+-_"
  min_special      = 2
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
}
resource "oci_mysql_mysql_db_system" "platform" {
  compartment_id          = var.compartment_id
  availability_domain     = local.availability_domain
  subnet_id               = oci_core_subnet.endpoints.id
  display_name            = "${local.name}-mysql"
  hostname_label          = "mysql"
  shape_name              = var.mysql_shape
  configuration_id        = oci_mysql_mysql_configuration.secure.id
  admin_username          = var.mysql_username
  admin_password          = random_password.mysql.result
  data_storage_size_in_gb = 50
  port                    = 3306
  port_x                  = 33060
  secure_connections {
    certificate_generation_type = "BYOC"
    certificate_id              = oci_certificates_management_certificate.mysql.id
  }
  depends_on = [oci_identity_policy.mysql_certificate]
  deletion_policy {
    is_delete_protected        = false
    automatic_backup_retention = "RETAIN"
    final_backup               = "REQUIRE_FINAL_BACKUP"
  }
  backup_policy {
    is_enabled        = true
    retention_in_days = 7
    pitr_policy {
      is_enabled = true
    }
  }
  freeform_tags = local.tags
}
resource "oci_objectstorage_bucket" "artifacts" {
  compartment_id = var.compartment_id
  namespace      = local.object_namespace
  name           = "${local.name}-artifacts"
  access_type    = "NoPublicAccess"
  # OCI provider has no force_destroy; nonempty buckets require explicit retention/cleanup.
  versioning    = "Enabled"
  freeform_tags = local.tags
}
resource "oci_objectstorage_bucket" "logs" {
  compartment_id = var.compartment_id
  namespace      = local.object_namespace
  name           = "${local.name}-logs"
  access_type    = "NoPublicAccess"
  # OCI provider has no force_destroy; nonempty buckets require explicit retention/cleanup.
  freeform_tags = local.tags
}
resource "oci_artifacts_container_repository" "platform" {
  compartment_id = var.compartment_id
  display_name   = "michelangelo/${var.environment}"
  is_public      = false
  freeform_tags  = local.tags
}
