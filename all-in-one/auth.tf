variable "auth_identity_domain_url" {
  description = "Existing OCI IAM identity-domain HTTPS URL whose users sign in to Studio."
  type        = string
  validation {
    condition     = can(regex("^https://[a-zA-Z0-9.-]+(:443)?/?$", var.auth_identity_domain_url))
    error_message = "Supply an identity-domain HTTPS origin, without a path or query."
  }
}

variable "auth_authorized_emails" {
  description = "Initial exact IAM emails invited into the persistent application directory. Administrators manage later access in Studio."
  type        = list(string)
  validation {
    condition     = length(var.auth_authorized_emails) > 0 && alltrue([for email in var.auth_authorized_emails : can(regex("^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", email))])
    error_message = "Authorize at least one exact email address."
  }
}

variable "auth_admin_emails" {
  description = "Initial application administrator emails. Empty selects the first initial invited email. Reapply preserves existing directory roles and removals."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for email in var.auth_admin_emails : can(regex("^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", email))])
    error_message = "Administrator invitations require exact OCI IAM email addresses."
  }
}

variable "auth_acme_email" {
  description = "Email receiving certificate authority expiry or account notices."
  type        = string
}

# API-key identity needs application management permissions in this existing
# domain. This application grants no OCI API administrator role to end users.
resource "oci_identity_domains_app" "studio" {
  idcs_endpoint = trimsuffix(var.auth_identity_domain_url, "/")
  schemas       = ["urn:ietf:params:scim:schemas:oracle:idcs:App"]
  based_on_template {
    value = "CustomWebAppTemplateId"
  }
  display_name              = "Michelangelo Studio ${var.environment}"
  description               = "Michelangelo browser login with persistent per-user application membership."
  active                    = true
  is_oauth_client           = true
  client_type               = "confidential"
  allowed_grants            = ["authorization_code", "refresh_token"]
  allow_offline             = true
  allow_access_control      = false
  all_url_schemes_allowed   = false
  redirect_uris             = ["https://${local.public_hostname}/oauth2/callback"]
  post_logout_redirect_uris = ["https://${local.public_hostname}/"]
  home_page_url             = "https://${local.public_hostname}/"
  # OCI augments this descriptive schema list with server-managed extensions.
  lifecycle {
    ignore_changes = [schemas]
  }
}

locals {
  auth_config = {
    oidc_issuer            = trimsuffix(var.auth_identity_domain_url, "/")
    oidc_client_id         = oci_identity_domains_app.studio.name
    oidc_client_secret     = oci_identity_domains_app.studio.client_secret
    authorized_emails      = var.auth_authorized_emails
    bootstrap_admin_emails = length(var.auth_admin_emails) > 0 ? var.auth_admin_emails : slice(var.auth_authorized_emails, 0, 1)
    acme_email             = var.auth_acme_email
  }
}

output "identity_domain_application_id" {
  value       = oci_identity_domains_app.studio.id
  description = "Identity-domain application ID for browser login."
}
