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
  type    = string
  default = null
}
variable "oke_node_image_id" {
  type    = string
  default = null
}
variable "node_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
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
