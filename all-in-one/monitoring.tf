variable "notification_email" {
  type        = string
  default     = ""
  description = "Owner email for OCI alarms. Empty leaves optional alarms disabled. Confirm the OCI subscription email before relying on delivery."
  validation {
    condition     = var.notification_email == "" || can(regex("^[^@ ,;]+@[^@ ,;]+\\.[^@ ,;]+$", var.notification_email))
    error_message = "Supply one owner email address or an empty string."
  }
}

variable "monthly_budget_amount" {
  type        = number
  default     = 0
  description = "Optional whole-number monthly budget in rate-card currency for the entire workload compartment. Zero disables the budget."
  validation {
    condition     = var.monthly_budget_amount >= 0 && floor(var.monthly_budget_amount) == var.monthly_budget_amount
    error_message = "Budget must be zero or a positive whole number."
  }
}

resource "oci_ons_notification_topic" "operations" {
  count          = var.notification_email == "" ? 0 : 1
  compartment_id = var.compartment_id
  name           = "${local.name}-operations"
  description    = "Michelangelo database and availability notifications"
  freeform_tags  = local.tags
}

resource "oci_ons_subscription" "owner" {
  count          = var.notification_email == "" ? 0 : 1
  compartment_id = var.compartment_id
  topic_id       = oci_ons_notification_topic.operations[0].id
  protocol       = "EMAIL"
  endpoint       = var.notification_email
}

locals {
  database_alarms = {
    backup_failure = {
      query    = "BackupFailure[5m]{dbSystemId = \"${oci_mysql_mysql_db_system.platform.id}\"}.max() > 0"
      duration = "PT1M"
      body     = "A MySQL backup failed. Inspect backup lifecycle details and restore coverage."
    }
    storage_usage = {
      query    = "DbVolumeUtilization[5m]{resourceId = \"${oci_mysql_mysql_db_system.platform.id}\"}.max() > 85"
      duration = "PT5M"
      body     = "MySQL storage utilization exceeds 85 percent. Review capacity before writes fail."
    }
    metrics_absent = {
      query    = "CPUUtilization[5m]{resourceId = \"${oci_mysql_mysql_db_system.platform.id}\"}.absent()"
      duration = "PT15M"
      body     = "MySQL metrics are absent. Check service state and scheduled maintenance."
    }
  }
}

resource "oci_monitoring_alarm" "database" {
  for_each              = var.notification_email == "" ? {} : local.database_alarms
  compartment_id        = var.compartment_id
  metric_compartment_id = var.compartment_id
  display_name          = "${local.name}-${each.key}"
  namespace             = "oci_mysql_database"
  query                 = each.value.query
  severity              = "CRITICAL"
  is_enabled            = true
  pending_duration      = each.value.duration
  destinations          = [oci_ons_notification_topic.operations[0].id]
  body                  = each.value.body
  message_format        = "ONS_OPTIMIZED"
  freeform_tags         = local.tags
}

# The Kubernetes cloud controller owns the load balancer. Its name is not a
# Terraform resource identity; select it by the reserved public address.
data "oci_load_balancer_load_balancers" "application" {
  count          = var.notification_email == "" ? 0 : 1
  compartment_id = var.compartment_id
  depends_on     = [null_resource.install]
}

locals {
  application_load_balancers = var.notification_email == "" ? [] : [
    for lb in data.oci_load_balancer_load_balancers.application[0].load_balancers : lb.id
    if contains([for address in lb.ip_address_details : address.ip_address], oci_core_public_ip.application.ip_address)
  ]
}

resource "oci_monitoring_alarm" "application" {
  count                 = var.notification_email == "" ? 0 : 1
  compartment_id        = var.compartment_id
  metric_compartment_id = var.compartment_id
  display_name          = "${local.name}-unhealthy-backends"
  namespace             = "oci_lbaas"
  query                 = "unhealthyBackendServers[5m]{resourceId = \"${one(local.application_load_balancers)}\"}.max() > 0"
  severity              = "CRITICAL"
  is_enabled            = true
  pending_duration      = "PT5M"
  destinations          = [oci_ons_notification_topic.operations[0].id]
  body                  = "The application load balancer reports unhealthy backends. Inspect the OKE identity edge and node readiness."
  message_format        = "ONS_OPTIMIZED"
  freeform_tags         = local.tags
  lifecycle {
    precondition {
      condition     = length(local.application_load_balancers) == 1
      error_message = "Exactly one load balancer must own the application's reserved public IP."
    }
  }
}

resource "oci_budget_budget" "operations" {
  provider       = oci.home
  count          = var.monthly_budget_amount > 0 ? 1 : 0
  compartment_id = var.tenancy_ocid
  amount         = var.monthly_budget_amount
  reset_period   = "MONTHLY"
  display_name   = "${local.name}-monthly"
  target_type    = "COMPARTMENT"
  targets        = [var.compartment_id]
  freeform_tags  = local.tags
  lifecycle {
    precondition {
      condition     = var.notification_email != ""
      error_message = "A budget requires notification_email."
    }
  }
}

resource "oci_budget_alert_rule" "operations" {
  provider       = oci.home
  count          = var.monthly_budget_amount > 0 ? 1 : 0
  budget_id      = oci_budget_budget.operations[0].id
  type           = "ACTUAL"
  threshold      = 80
  threshold_type = "PERCENTAGE"
  recipients     = var.notification_email
  display_name   = "${local.name}-budget-80-percent"
  message        = "The workload compartment reached 80 percent of its monthly budget. This includes all resources in that compartment."
}

output "notification_topic_id" {
  value = try(oci_ons_notification_topic.operations[0].id, null)
}

# Node-level LB checks can remain healthy when the identity edge is absent.
# Probe the existing OAuth2 readiness route over the public TLS path as well.
resource "oci_health_checks_http_monitor" "application_edge" {
  count               = var.notification_email == "" ? 0 : 1
  compartment_id      = var.compartment_id
  display_name        = "${local.name}-application-edge"
  interval_in_seconds = 60
  protocol            = "HTTPS"
  targets             = [local.public_hostname]
  port                = 443
  path                = "/oauth2/ready"
  method              = "GET"
  timeout_in_seconds  = 10
  is_enabled          = true
  freeform_tags       = local.tags
  depends_on          = [null_resource.install]
}

resource "oci_monitoring_alarm" "application_edge" {
  for_each = var.notification_email == "" ? {} : {
    unavailable = {
      expression = "grouping().min() < 1"
      duration   = "PT5M"
      body       = "Public HTTPS identity-edge readiness checks are failing. Inspect DNS, TLS, the load balancer, Caddy, and OAuth2 Proxy. This probe does not validate authenticated application workflows."
    }
    absent = {
      expression = "grouping().absent()"
      duration   = "PT10M"
      body       = "Public HTTPS identity-edge readiness measurements are absent. Inspect the Health Checks monitor before relying on availability coverage."
    }
  }
  compartment_id        = var.compartment_id
  metric_compartment_id = var.compartment_id
  display_name          = "${local.name}-edge-${each.key}"
  namespace             = "oci_healthchecks"
  query                 = "HTTP.isHealthy[1m]{resourceId = \"${oci_health_checks_http_monitor.application_edge[0].id}\"}.${each.value.expression}"
  severity              = "CRITICAL"
  is_enabled            = true
  pending_duration      = each.value.duration
  destinations          = [oci_ons_notification_topic.operations[0].id]
  body                  = each.value.body
  message_format        = "ONS_OPTIMIZED"
  freeform_tags         = local.tags
}
