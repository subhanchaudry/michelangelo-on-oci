# Resource Manager profile only. Local operator Terraform remains unchanged.
terraform {
  required_version = ">= 1.5.7, < 1.6.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 7.32.0"
    }
  }
}

# Resource Manager supplies provider authentication. No API keys belong here.
provider "oci" {
  tenancy_ocid = var.tenancy_ocid
  region       = var.region
}

provider "oci" {
  alias        = "home"
  tenancy_ocid = var.tenancy_ocid
  region       = var.home_region
}
