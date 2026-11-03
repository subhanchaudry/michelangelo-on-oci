terraform {
  required_version = ">= 1.6.0, < 2.0.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 7.0"
    }
  }
}

provider "oci" {
  tenancy_ocid = var.tenancy_ocid
  region       = var.region
}

# OCI tenancy IAM resources are managed through the tenancy home region. Workload
# resources continue to use the default provider, configured for var.region.
provider "oci" {
  alias        = "home"
  region       = var.home_region
  tenancy_ocid = var.tenancy_ocid
}
