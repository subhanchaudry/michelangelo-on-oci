variable "automation_user_email" {
  description = "Owner contact email for the installer registry and artifact machine account."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", var.automation_user_email))
    error_message = "Supply an owned email address for this machine identity."
  }
}

variable "runtime_reader_user_email" {
  description = "Owner contact email for the read-only serving and delivery machine account."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", var.runtime_reader_user_email))
    error_message = "Supply an owned email address for this machine identity."
  }
}

# Credentials are generated in managed state and never placed in the distributable ZIP.
resource "oci_identity_domains_user" "automation" {
  provider      = oci.home
  idcs_endpoint = trimsuffix(var.auth_identity_domain_url, "/")
  schemas       = ["urn:ietf:params:scim:schemas:core:2.0:User"]
  user_name     = "${local.name}-automation"
  description   = "Michelangelo Automation machine identity"
  active        = true
  name {
    given_name  = "Michelangelo"
    family_name = "Automation"
  }
  emails {
    value   = var.automation_user_email
    type    = "work"
    primary = true
  }
  emails {
    value   = var.automation_user_email
    type    = "recovery"
    primary = false
  }
  urnietfparamsscimschemasoracleidcsextensionuser_user {
    bypass_notification = true
  }
  urnietfparamsscimschemasoracleidcsextensioncapabilities_user {
    can_use_console                  = false
    can_use_console_password         = false
    can_use_api_keys                 = false
    can_use_auth_tokens              = true
    can_use_customer_secret_keys     = true
    can_use_smtp_credentials         = false
    can_use_oauth2client_credentials = false
    can_use_db_credentials           = false
  }
  lifecycle {
    ignore_changes = [schemas, urnietfparamsscimschemasoracleidcsextensionuser_user[0].bypass_notification]
  }
}

resource "oci_identity_group" "automation" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${local.name}-automation"
  description    = "Michelangelo registry and artifact automation"
}
resource "oci_identity_user_group_membership" "automation" {
  provider = oci.home
  user_id  = oci_identity_domains_user.automation.ocid
  group_id = oci_identity_group.automation.id
}
resource "oci_identity_domains_auth_token" "registry" {
  provider      = oci.home
  idcs_endpoint = trimsuffix(var.auth_identity_domain_url, "/")
  schemas       = ["urn:ietf:params:scim:schemas:oracle:idcs:authToken"]
  description   = "Michelangelo automation AuthToken"
  user {
    value = oci_identity_domains_user.automation.id
  }
}
resource "oci_identity_domains_customer_secret_key" "artifacts" {
  provider      = oci.home
  idcs_endpoint = trimsuffix(var.auth_identity_domain_url, "/")
  schemas       = ["urn:ietf:params:scim:schemas:oracle:idcs:customerSecretKey"]
  display_name  = "Michelangelo automation CustomerSecretKey"
  user {
    value = oci_identity_domains_user.automation.id
  }
}
resource "oci_identity_policy" "automation" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "${local.name}-automation"
  description    = "Workload-compartment-only image and artifact access"
  statements = [
    "Allow group id ${oci_identity_group.automation.id} to read repos in compartment id ${var.compartment_id} where target.repo.name='${oci_artifacts_container_repository.platform.display_name}'",
    "Allow group id ${oci_identity_group.automation.id} to manage repos in compartment id ${var.compartment_id} where target.repo.name='${oci_artifacts_container_repository.platform.display_name}'",
    "Allow group id ${oci_identity_group.automation.id} to manage objects in compartment id ${var.compartment_id} where any {target.bucket.name='${oci_objectstorage_bucket.artifacts.name}', target.bucket.name='${oci_objectstorage_bucket.logs.name}'}",
    "Allow group id ${oci_identity_group.automation.id} to read buckets in compartment id ${var.compartment_id} where any {target.bucket.name='${oci_objectstorage_bucket.artifacts.name}', target.bucket.name='${oci_objectstorage_bucket.logs.name}'}",
  ]
}
resource "oci_identity_dynamic_group" "bootstrap" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${local.name}-bootstrap"
  description    = "Only this stack's bootstrap instance"
  matching_rule  = "ALL {instance.id = '${oci_core_instance.bootstrap.id}'}"
}
resource "oci_identity_policy" "bootstrap" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "${local.name}-bootstrap"
  description    = "Bootstrap access limited to workload compartment"
  statements = [
    "Allow dynamic-group id ${oci_identity_dynamic_group.bootstrap.id} to manage cluster-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.bootstrap.id} to read repos in compartment id ${var.compartment_id} where target.repo.name='${oci_artifacts_container_repository.platform.display_name}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.bootstrap.id} to read buckets in compartment id ${var.compartment_id}",
  ]
}
# The OKE cluster identity creates its public ingress load balancer and attaches its reserved IP.
resource "oci_identity_policy" "cluster_load_balancer" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "${local.name}-oke-edge"
  description    = "Load balancer access for this stack's OKE cluster"
  # OKE CSI uses the cluster resource principal (Oracle OKE key-delegate docs).
  # Provisioning and attaching the edge PVC stays within this workload compartment.
  statements = [
    "Allow any-user to manage volume-family in compartment id ${var.compartment_id} where all {request.principal.type='cluster', request.principal.id='${oci_containerengine_cluster.platform.id}'}",
    "Allow any-user to use instances in compartment id ${var.compartment_id} where all {request.principal.type='cluster', request.principal.id='${oci_containerengine_cluster.platform.id}'}",
    "Allow any-user to manage load-balancers in compartment id ${var.compartment_id} where all {request.principal.type='cluster', request.principal.id='${oci_containerengine_cluster.platform.id}'}",
    "Allow any-user to manage virtual-network-family in compartment id ${var.compartment_id} where all {request.principal.type='cluster', request.principal.id='${oci_containerengine_cluster.platform.id}'}",
  ]
}

# Serving and delivery may pull images/read artifacts, with no registry or object writes.
resource "oci_identity_domains_user" "runtime_reader" {
  provider      = oci.home
  idcs_endpoint = trimsuffix(var.auth_identity_domain_url, "/")
  schemas       = ["urn:ietf:params:scim:schemas:core:2.0:User"]
  user_name     = "${local.name}-runtime-reader"
  description   = "Michelangelo Runtime reader machine identity"
  active        = true
  name {
    given_name  = "Michelangelo"
    family_name = "Runtime reader"
  }
  emails {
    value   = var.runtime_reader_user_email
    type    = "work"
    primary = true
  }
  emails {
    value   = var.runtime_reader_user_email
    type    = "recovery"
    primary = false
  }
  urnietfparamsscimschemasoracleidcsextensionuser_user {
    bypass_notification = true
  }
  urnietfparamsscimschemasoracleidcsextensioncapabilities_user {
    can_use_console                  = false
    can_use_console_password         = false
    can_use_api_keys                 = false
    can_use_auth_tokens              = true
    can_use_customer_secret_keys     = true
    can_use_smtp_credentials         = false
    can_use_oauth2client_credentials = false
    can_use_db_credentials           = false
  }
  lifecycle {
    ignore_changes = [schemas, urnietfparamsscimschemasoracleidcsextensionuser_user[0].bypass_notification]
  }
}

resource "oci_identity_group" "runtime_reader" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${local.name}-runtime-reader"
  description    = "Read-only serving runtime credentials"
}
resource "oci_identity_user_group_membership" "runtime_reader" {
  provider = oci.home
  user_id  = oci_identity_domains_user.runtime_reader.ocid
  group_id = oci_identity_group.runtime_reader.id
}
resource "oci_identity_domains_auth_token" "runtime_reader" {
  provider      = oci.home
  idcs_endpoint = trimsuffix(var.auth_identity_domain_url, "/")
  schemas       = ["urn:ietf:params:scim:schemas:oracle:idcs:authToken"]
  description   = "Michelangelo runtime_reader AuthToken"
  user {
    value = oci_identity_domains_user.runtime_reader.id
  }
}
resource "oci_identity_domains_customer_secret_key" "runtime_reader" {
  provider      = oci.home
  idcs_endpoint = trimsuffix(var.auth_identity_domain_url, "/")
  schemas       = ["urn:ietf:params:scim:schemas:oracle:idcs:customerSecretKey"]
  display_name  = "Michelangelo runtime_reader CustomerSecretKey"
  user {
    value = oci_identity_domains_user.runtime_reader.id
  }
}
resource "oci_identity_policy" "runtime_reader" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "${local.name}-runtime-reader"
  description    = "Read-only registry, artifacts, and logs in the workload compartment"
  statements = [
    "Allow group id ${oci_identity_group.runtime_reader.id} to read repos in compartment id ${var.compartment_id} where target.repo.name='${oci_artifacts_container_repository.platform.display_name}'",
    "Allow group id ${oci_identity_group.runtime_reader.id} to read objects in compartment id ${var.compartment_id} where any {target.bucket.name='${oci_objectstorage_bucket.artifacts.name}', target.bucket.name='${oci_objectstorage_bucket.logs.name}'}",
    "Allow group id ${oci_identity_group.runtime_reader.id} to read buckets in compartment id ${var.compartment_id} where any {target.bucket.name='${oci_objectstorage_bucket.artifacts.name}', target.bucket.name='${oci_objectstorage_bucket.logs.name}'}",
  ]
}
