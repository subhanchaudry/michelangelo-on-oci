resource "oci_core_vcn" "platform" {
  compartment_id = var.compartment_id
  display_name   = "${local.name}-vcn"
  cidr_blocks    = ["10.84.0.0/16"]
  dns_label      = replace(local.name, "-", "")
  freeform_tags  = local.tags
}
resource "oci_core_internet_gateway" "platform" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.platform.id
  display_name   = "${local.name}-igw"
  enabled        = true
}
resource "oci_core_nat_gateway" "platform" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.platform.id
  display_name   = "${local.name}-nat"
}
resource "oci_core_service_gateway" "platform" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.platform.id
  services {
    service_id = local.service.id
  }
}
resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.platform.id
  route_rules {
    network_entity_id = oci_core_internet_gateway.platform.id
    destination       = "0.0.0.0/0"
  }
}
resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.platform.id
  route_rules {
    network_entity_id = oci_core_nat_gateway.platform.id
    destination       = "0.0.0.0/0"
  }
  route_rules {
    network_entity_id = oci_core_service_gateway.platform.id
    destination       = local.service.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
  }
}
resource "oci_core_security_list" "private" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.platform.id
  display_name   = "${local.name}-private"
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
  ingress_security_rules {
    protocol = "all"
    source   = "10.84.0.0/16"
  }
  ingress_security_rules {
    protocol = "all"
    source   = "10.244.0.0/16"
  }
}
resource "oci_core_security_list" "public" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.platform.id
  display_name   = "${local.name}-edge"
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
  ingress_security_rules {
    protocol = "all"
    source   = "10.84.0.0/16"
  }
  ingress_security_rules {
    protocol = "6"
    source   = var.admin_cidr
    tcp_options {
      min = 22
      max = 22
    }
  }
  dynamic "ingress_security_rules" {
    for_each = [80, 443]
    content {
      protocol = "6"
      source   = "0.0.0.0/0"
      tcp_options {
        min = ingress_security_rules.value
        max = ingress_security_rules.value
      }
    }

  }
}
resource "oci_core_subnet" "nodes" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.platform.id
  cidr_block                 = "10.84.16.0/20"
  display_name               = "${local.name}-nodes"
  dns_label                  = "nodes"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
}
resource "oci_core_subnet" "endpoints" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.platform.id
  cidr_block                 = "10.84.32.0/24"
  display_name               = "${local.name}-endpoints"
  dns_label                  = "endpoints"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
}
resource "oci_core_subnet" "edge" {
  compartment_id    = var.compartment_id
  vcn_id            = oci_core_vcn.platform.id
  cidr_block        = "10.84.48.0/24"
  display_name      = "${local.name}-edge"
  dns_label         = "edge"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.public.id]
}
resource "oci_core_public_ip" "application" {
  compartment_id = var.compartment_id
  display_name   = "${local.name}-application"
  lifetime       = "RESERVED"
  freeform_tags  = local.tags
  # Kubernetes CCM owns attachment to its load balancer.
  lifecycle {
    ignore_changes = [private_ip_id]
  }
}
