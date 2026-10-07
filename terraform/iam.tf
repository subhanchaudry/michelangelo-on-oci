resource "oci_identity_compartment" "platform" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = var.platform_compartment_name
  description    = "Michelangelo AI ${var.environment} platform resources"
  freeform_tags  = local.common_tags
}

resource "oci_identity_policy" "platform_operators" {
  count          = var.operator_group_name == null ? 0 : 1
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "${replace(local.name_prefix, "-", "_")}_operators"
  description    = "Platform operator access for the approved tenancy IAM group."
  statements = [
    "Allow group ${var.operator_group_name} to manage cluster-family in compartment id ${oci_identity_compartment.platform.id}",
    "Allow group ${var.operator_group_name} to manage object-family in compartment id ${oci_identity_compartment.platform.id}",
    "Allow group ${var.operator_group_name} to manage vaults in compartment id ${oci_identity_compartment.platform.id}",
    "Allow group ${var.operator_group_name} to manage secret-family in compartment id ${oci_identity_compartment.platform.id}",
  ]
  freeform_tags = local.common_tags
}

# Object Storage needs this service policy before it can use the customer-managed
# key to encrypt either platform bucket.
resource "oci_identity_policy" "object_storage_kms" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "${replace(local.name_prefix, "-", "_")}_objectstorage_kms"
  description    = "Allows Object Storage in the workload region to use the platform encryption key."
  statements = [
    "Allow service objectstorage-${var.region} to use keys in compartment id ${oci_identity_compartment.platform.id} where target.key.id = '${oci_kms_key.platform.id}'",
  ]
  freeform_tags = local.common_tags
}
