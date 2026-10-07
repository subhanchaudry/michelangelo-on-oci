terraform {
  required_version = ">= 1.5.7, < 2.0.0"
  required_providers {
    oci = { source = "oracle/oci", version = "= 9.8.0"
    }
    tls = { source = "hashicorp/tls", version = "~> 4.0"
    }
    random = { source = "hashicorp/random", version = "~> 3.6"
    }
    null = { source = "hashicorp/null", version = "~> 3.2"
    }

  }
}
# Local execution uses the default OCI profile; Resource Manager injects its job identity.
provider "oci" {
  tenancy_ocid = var.tenancy_ocid
  region       = var.region
}
provider "oci" {
  alias        = "home"
  tenancy_ocid = var.tenancy_ocid
  region       = var.home_region
}
