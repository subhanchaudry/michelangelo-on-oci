locals {
  name_prefix = "ma-${var.environment}"
  common_tags = merge(var.tags, {
    managed-by  = "terraform"
    workload    = "michelangelo-ai"
    environment = var.environment
  })
  workload_compartment_id = oci_identity_compartment.platform.id
}
