# A stack-specific private CA verifies only this private DB; no public trust is implied.
locals {
  mysql_expected_hostname = "mysql.endpoints.${replace(local.name, "-", "")}.oraclevcn.com"
}
resource "tls_private_key" "mysql_ca" {
  algorithm = "RSA"
  rsa_bits  = 4096
}
resource "tls_self_signed_cert" "mysql_ca" {
  private_key_pem = tls_private_key.mysql_ca.private_key_pem
  subject {
    common_name = "${local.name} private database CA"
  }
  is_ca_certificate     = true
  validity_period_hours = 8760
  allowed_uses          = ["cert_signing", "crl_signing"]
}
resource "tls_private_key" "mysql" {
  algorithm = "RSA"
  rsa_bits  = 4096
}
resource "tls_cert_request" "mysql" {
  private_key_pem = tls_private_key.mysql.private_key_pem
  subject {
    common_name = local.mysql_expected_hostname
  }
  dns_names = [local.mysql_expected_hostname]
}
resource "tls_locally_signed_cert" "mysql" {
  cert_request_pem      = tls_cert_request.mysql.cert_request_pem
  ca_private_key_pem    = tls_private_key.mysql_ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.mysql_ca.cert_pem
  validity_period_hours = 8760
  allowed_uses          = ["digital_signature", "key_encipherment", "server_auth"]
}
resource "oci_certificates_management_certificate" "mysql" {
  compartment_id = var.compartment_id
  name           = "${local.name}-mysql"
  certificate_config {
    # Defer IMPORTED until managed PEM exists: OCI provider rejects unknown required PEM at Plan.
    config_type     = startswith(tls_self_signed_cert.mysql_ca.cert_pem, "-----BEGIN CERTIFICATE-----") ? "IMPORTED" : ""
    certificate_pem = tls_locally_signed_cert.mysql.cert_pem
    cert_chain_pem  = tls_self_signed_cert.mysql_ca.cert_pem
    private_key_pem = tls_private_key.mysql.private_key_pem

  }
  freeform_tags = local.tags
}
resource "oci_identity_policy" "mysql_certificate" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "${local.name}-mysql-certificate"
  description    = "Only this compartment's MySQL principals may read this certificate"
  statements     = ["Allow any-user to read leaf-certificate-family in compartment id ${var.compartment_id} where all {request.principal.type='mysqldbsystem', request.resource.compartment.id='${var.compartment_id}', target.leaf-certificate.id='${oci_certificates_management_certificate.mysql.id}'}"]
}
data "oci_mysql_mysql_configurations" "base" {
  compartment_id = var.compartment_id
  shape_name     = var.mysql_shape
  display_name   = "${var.mysql_shape}.Standalone"
  type           = ["DEFAULT"]
  state          = "ACTIVE"
}
resource "oci_mysql_mysql_configuration" "secure" {
  compartment_id          = var.compartment_id
  shape_name              = var.mysql_shape
  parent_configuration_id = one(data.oci_mysql_mysql_configurations.base.configurations).id
  display_name            = "${local.name}-mysql-tls"
  dynamic "options" {
    for_each = { for option in one(data.oci_mysql_mysql_configurations.base.configurations).options : option.name => option.value if option.name != "require_secure_transport"
    }
    content {
      name  = options.key
      value = options.value
    }

  }
  options {
    name  = "require_secure_transport"
    value = "ON"
  }
}
