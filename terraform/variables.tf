variable "tenancy_ocid" {
  description = "OCI tenancy OCID."
  type        = string
}

variable "compartment_id" {
  description = "Parent compartment for the accelerator environment."
  type        = string
}

variable "platform_compartment_name" {
  description = "Name of the child compartment created for this accelerator environment."
  type        = string
  default     = "MichelangeloAI"
}

variable "environment" {
  description = "Environment identifier, for example dev, nonprod, or prod."
  type        = string
  default     = "nonprod"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,16}$", var.environment))
    error_message = "environment must contain only lowercase letters, digits, and hyphens."
  }

  validation {
    condition     = length(replace(var.environment, "-", "")) <= 13
    error_message = "environment must have at most 13 non-hyphen characters so the prefixed VCN DNS label fits OCI's 15-character limit."
  }
}

variable "region" {
  description = "OCI workload region used by the provider and Object Storage endpoints."
  type        = string
}

variable "home_region" {
  description = "OCI tenancy home region, required for compartment and tenancy IAM changes."
  type        = string
}

variable "operator_group_name" {
  description = "Existing tenancy IAM group granted platform-operator access. Leave null to skip the optional policy."
  type        = string
  default     = null
}

variable "availability_domain" {
  description = "Availability domain name used for OKE node pools and MySQL."
  type        = string
}

variable "gpu_availability_domain" {
  description = "Availability domain for the optional GPU pool. Set it to an AD where the selected GPU shape is available."
  type        = string
  default     = null
}

variable "kubernetes_version" {
  description = "An OKE-supported Kubernetes version that is >= the upstream Michelangelo minimum."
  type        = string
}

variable "oke_node_image_id" {
  description = "Image OCID for the selected CPU/system OKE worker-node image. Resolve it per region and Kubernetes version."
  type        = string
}

variable "gpu_oke_node_image_id" {
  description = "Image OCID for the selected GPU-enabled OKE worker-node image. Resolve it per region and Kubernetes version."
  type        = string
  default     = null
}

variable "system_node_shape" {
  description = "CPU shape for Michelangelo, Temporal, KubeRay, ingress, and observability."
  type        = string
  default     = "VM.Standard.E5.Flex"
}

variable "system_node_ocpus" {
  description = "OCPU count for each flexible-shape system node."
  type        = number
  default     = 2
}

variable "system_node_memory_in_gbs" {
  description = "Memory in GB for each flexible-shape system node."
  type        = number
  default     = 16
}

variable "cpu_node_shape" {
  description = "CPU shape for Ray execution workloads."
  type        = string
  default     = "VM.Standard.E5.Flex"
}

variable "cpu_node_ocpus" {
  description = "OCPU count for each flexible-shape CPU execution node."
  type        = number
  default     = 2
}

variable "cpu_node_memory_in_gbs" {
  description = "Memory in GB for each flexible-shape CPU execution node."
  type        = number
  default     = 16
}

variable "gpu_node_shape" {
  description = "GPU shape for Ray execution workloads. Must be available in the selected region."
  type        = string
  default     = "VM.GPU.A10.1"
}

variable "enable_gpu_pool" {
  description = "Whether to create the optional GPU Ray execution node pool."
  type        = bool
  default     = false
}

variable "system_node_count" {
  description = "Initial system pool node count."
  type        = number
  default     = 3
  validation {
    condition     = var.system_node_count >= 1 && floor(var.system_node_count) == var.system_node_count
    error_message = "system_node_count must be a positive whole number."
  }
}

variable "system_node_boot_volume_size_in_gbs" {
  description = "Optional boot size for system nodes. Null preserves the image default used by the existing pool."
  type        = number
  default     = null
}

variable "cpu_node_count" {
  description = "Initial CPU execution pool node count."
  type        = number
  default     = 1
  validation {
    condition     = var.cpu_node_count >= 0 && floor(var.cpu_node_count) == var.cpu_node_count
    error_message = "cpu_node_count must be a nonnegative whole number."
  }
}

variable "cpu_node_boot_volume_size_in_gbs" {
  description = "Boot volume size for CPU Ray/Spark execution nodes. Use at least 500 GiB for the full upstream examples image."
  type        = number
  default     = 500
}

variable "gpu_node_count" {
  description = "Initial GPU execution pool node count when enable_gpu_pool is true."
  type        = number
  default     = 0
  validation {
    condition     = var.gpu_node_count >= 0 && floor(var.gpu_node_count) == var.gpu_node_count
    error_message = "gpu_node_count must be a nonnegative whole number."
  }
}

variable "mysql_shape_name" {
  description = "OCI MySQL DB System shape. Confirm capacity and HA requirements before production use."
  type        = string
  default     = "MySQL.2"
}

variable "mysql_admin_username" {
  description = "Bootstrap MySQL administrator. Store the paired password in a secure CI secret, never in tfvars."
  type        = string
  default     = "madmin"
}

variable "mysql_admin_password" {
  description = "Bootstrap MySQL administrator password. Pass from a secure environment variable such as TF_VAR_mysql_admin_password."
  type        = string
  sensitive   = true
}

variable "object_storage_namespace" {
  description = "OCI Object Storage namespace. Retrieve with `oci os ns get`."
  type        = string
}

variable "ml_team_names" {
  description = "Optional lowercase team identifiers. Creates private per-team artifact buckets; identity policies and isolation acceptance are separate requirements."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for name in var.ml_team_names : can(regex("^[a-z0-9-]{2,24}$", name))])
    error_message = "ml_team_names values must contain only lowercase letters, digits, and hyphens."
  }
}

variable "admin_cidr" {
  description = "Permanent IPv4 client CIDR for Bastion. Operator sessions temporarily add and later remove their own /32."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && try(tonumber(split("/", var.admin_cidr)[1]) >= 24, false)
    error_message = "Use an approved IPv4 client range /24 or narrower; unrestricted Bastion access is not allowed."
  }
}

variable "release_repository_names" {
  description = "Optional separate release repositories. The current pilot uses only the existing shared repository."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for name in var.release_repository_names : contains(["apiserver", "controllermgr", "worker", "ui", "envoy", "kuberay-collector", "examples"], name)])
    error_message = "Select supported Michelangelo release components only."
  }
}

variable "tags" {
  description = "Free-form tags applied to all supported resources."
  type        = map(string)
  default     = {}
}

variable "registry_immutability_supported" {
  description = "Enable only after regional OCIR accepts isImmutable. Digest pins do not substitute for registry enforcement."
  type        = bool
  default     = false
}
