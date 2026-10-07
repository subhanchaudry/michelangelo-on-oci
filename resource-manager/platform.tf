resource "oci_kms_vault" "this" {
  lifecycle {
    prevent_destroy = true
  }
  compartment_id = local.workload_compartment_id
  display_name   = "${local.name_prefix}-vault"
  vault_type     = "DEFAULT"
  freeform_tags  = local.common_tags
}

resource "oci_kms_key" "platform" {
  lifecycle {
    prevent_destroy = true
  }
  compartment_id      = local.workload_compartment_id
  display_name        = "${local.name_prefix}-platform-key"
  management_endpoint = oci_kms_vault.this.management_endpoint
  key_shape {
    algorithm = "AES"
    length    = 32
  }
  freeform_tags = local.common_tags
}

resource "oci_objectstorage_bucket" "artifacts" {
  lifecycle {
    prevent_destroy = true
  }
  compartment_id = local.workload_compartment_id
  namespace      = var.object_storage_namespace
  name           = "${local.name_prefix}-ml-artifacts"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Enabled"
  kms_key_id     = oci_kms_key.platform.id
  depends_on     = [oci_identity_policy.object_storage_kms]
  freeform_tags  = local.common_tags
}

resource "oci_objectstorage_bucket" "logs" {
  lifecycle {
    prevent_destroy = true
  }
  compartment_id = local.workload_compartment_id
  namespace      = var.object_storage_namespace
  name           = "${local.name_prefix}-platform-logs"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  kms_key_id     = oci_kms_key.platform.id
  depends_on     = [oci_identity_policy.object_storage_kms]
  freeform_tags  = local.common_tags
}

# A project should not share an artifact bucket or Customer Secret Key with a
# different project. The Kubernetes team template consumes the corresponding
# per-team secret from Vault after it has been created through the OCI console
# or CLI under the approved least-privilege identity.
resource "oci_objectstorage_bucket" "team_artifacts" {
  lifecycle {
    prevent_destroy = true
  }
  for_each       = var.ml_team_names
  compartment_id = local.workload_compartment_id
  namespace      = var.object_storage_namespace
  name           = "${local.name_prefix}-${each.value}-artifacts"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Enabled"
  kms_key_id     = oci_kms_key.platform.id
  depends_on     = [oci_identity_policy.object_storage_kms]
  freeform_tags  = merge(local.common_tags, { "ml-team" = each.value })
}

resource "oci_mysql_mysql_db_system" "this" {
  lifecycle {
    prevent_destroy = true
  }
  compartment_id          = local.workload_compartment_id
  availability_domain     = var.availability_domain
  subnet_id               = oci_core_subnet.endpoints.id
  display_name            = "${local.name_prefix}-mysql"
  hostname_label          = "${replace(local.name_prefix, "-", "")}-mysql"
  shape_name              = var.mysql_shape_name
  admin_username          = var.mysql_admin_username
  admin_password          = var.mysql_admin_password
  data_storage_size_in_gb = 50
  port                    = 3306
  port_x                  = 33060
  freeform_tags           = local.common_tags

  # Preserve the accepted pilot policy and make it reproducible at new sites.
  # Backup availability is not evidence of a successful restore rehearsal.
  backup_policy {
    is_enabled        = true
    retention_in_days = 7
    pitr_policy {
      is_enabled = true
    }
  }
}

resource "oci_bastion_bastion" "this" {
  bastion_type   = "STANDARD"
  compartment_id = local.workload_compartment_id
  # The managed Bastion service targets a private subnet that can reach OKE
  # worker nodes and the MySQL endpoint; it does not require a customer jump host.
  target_subnet_id             = oci_core_subnet.endpoints.id
  name                         = "${local.name_prefix}-bastion"
  client_cidr_block_allow_list = [var.admin_cidr]
  freeform_tags                = local.common_tags
}

resource "oci_artifacts_container_repository" "platform" {
  compartment_id = local.workload_compartment_id
  display_name   = "michelangelo/${var.environment}"
  # Registry immutability availability must be checked per region. Keep digest pins mandatory;
  # registry-level overwrite prevention remains a separate service gate.
  is_immutable  = var.registry_immutability_supported
  is_public     = false
  freeform_tags = local.common_tags
}

# Separate OCIR repositories make promotion auditable and prevent a workload
# image from overwriting a Michelangelo control-plane image with the same tag.
resource "oci_artifacts_container_repository" "release_images" {
  for_each       = var.release_repository_names
  compartment_id = local.workload_compartment_id
  display_name   = "michelangelo/${var.environment}/${each.value}"
  is_immutable   = var.registry_immutability_supported
  is_public      = false
  freeform_tags  = local.common_tags
}
