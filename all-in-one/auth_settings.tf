# Public signing keys are required for external OIDC signature verification.
# Settings is a shared domain singleton; update only its public-key boolean with
# atomic SCIM PATCH. The helper reuses this Terraform job's existing identity.
variable "enable_public_signing_certificate_access" {
  description = "Enable public identity-domain signing certificates/JWKS for OIDC. False preserves the current flag. Other Settings and private signing keys are never replaced."
  type        = bool
  default     = true
}

resource "terraform_data" "public_signing_certificates" {
  input = {
    domain_url = trimsuffix(var.auth_identity_domain_url, "/")
    enable     = var.enable_public_signing_certificate_access
  }
  # Recheck on every Apply, including after external changes. Replacement has no
  # domain teardown action; its destroy only discards local Terraform data state.
  triggers_replace = {
    apply_run     = timestamp()
    helper_sha256 = filesha256("${path.module}/scripts/enable-public-signing-keys.py")
  }
  provisioner "local-exec" {
    working_dir = path.module
    command     = "python3 scripts/enable-public-signing-keys.py"
    environment = {
      MA_IDENTITY_DOMAIN_URL = self.input.domain_url
      MA_ENABLE_SIGNING_KEYS = tostring(self.input.enable)
    }
  }
}
