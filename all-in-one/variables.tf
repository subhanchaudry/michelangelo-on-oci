variable "tenancy_ocid" {
  type = string
}
variable "compartment_id" {
  type        = string
  description = "Existing workload compartment; all workload resources are confined here."
}
variable "region" {
  type = string
}
variable "home_region" {
  type = string
}
variable "environment" {
  type    = string
  default = "oneclick"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,12}$", var.environment))
    error_message = "Use 3-13 lowercase characters, starting with a letter."
  }
}
variable "availability_domain" {
  type    = string
  default = null
}
variable "kubernetes_version" {
  description = "OKE Kubernetes version. The release default is fixed; select upgrades explicitly."
  type        = string
  default     = "v1.37.0"
}
variable "oke_node_image_id" {
  type    = string
  default = null
}
variable "node_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}
variable "cpu_node_count" {
  description = "CPU workers for concurrent lifecycle workflows and model serving. Each worker also requires a boot volume."
  type        = number
  default     = 2
  validation {
    condition     = var.cpu_node_count >= 2 && floor(var.cpu_node_count) == var.cpu_node_count
    error_message = "cpu_node_count must be a whole number of at least 2 for concurrent workflow and serving capacity."
  }
}
variable "node_ocpus" {
  type    = number
  default = 2
}
variable "node_memory_gbs" {
  type    = number
  default = 16
}
variable "node_boot_gbs" {
  type    = number
  default = 200
}
variable "cpu_boot_gbs" {
  type    = number
  default = 500
}
variable "bootstrap_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}
variable "bootstrap_ocpus" {
  type    = number
  default = 4
}
variable "bootstrap_memory_gbs" {
  type    = number
  default = 32
}
variable "bootstrap_image_id" {
  type    = string
  default = null
}
variable "admin_cidr" {
  type        = string
  description = "Operator public IPv4 /32 for bootstrap SSH; Resource Manager uses its private endpoint."
  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && try(tonumber(split("/", var.admin_cidr)[1]) == 32, false)
    error_message = "Use the single authorized operator IPv4 address as /32."
  }
}
variable "bootstrap_ssh_host_key" {
  type        = string
  default     = ""
  description = "Existing builder's SSH public host key, verified through authenticated OCI console access. Leave empty for a new builder with a generated pinned key."
  validation {
    condition     = var.bootstrap_ssh_host_key == "" || can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/]+={0,3}( [^\\r\\n]*)?$", trimspace(var.bootstrap_ssh_host_key)))
    error_message = "Supply one OpenSSH public host key, or leave empty for a new builder."
  }
}
variable "operator_ssh_public_key" {
  type    = string
  default = ""
}
variable "use_resource_manager_private_endpoint" {
  type    = bool
  default = true
}
variable "public_hostname" {
  type        = string
  default     = null
  description = "Optional DNS name pointing at the reserved IP. Default is IP.sslip.io."
}
variable "mysql_shape" {
  type    = string
  default = "MySQL.2"
}
variable "mysql_username" {
  type    = string
  default = "maadmin"
}
variable "tags" {
  type    = map(string)
  default = {}
}
