data "oci_core_services" "all" {}

# Do not rely on the API's ordering of Object Storage and All Services entries.
locals {
  regional_services = one([for service in data.oci_core_services.all.services : service
    if can(regex("^All .* Services In Oracle Services Network$", service.name))
  ])
}

resource "oci_core_vcn" "this" {
  compartment_id = local.workload_compartment_id
  display_name   = "${local.name_prefix}-vcn"
  cidr_blocks    = ["10.80.0.0/16"]
  dns_label      = "ma${replace(var.environment, "-", "")}"
  freeform_tags  = local.common_tags
}

resource "oci_core_internet_gateway" "this" {
  compartment_id = local.workload_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${local.name_prefix}-igw"
  enabled        = true
  freeform_tags  = local.common_tags
}

resource "oci_core_nat_gateway" "this" {
  compartment_id = local.workload_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${local.name_prefix}-nat"
  freeform_tags  = local.common_tags
}

resource "oci_core_service_gateway" "this" {
  compartment_id = local.workload_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${local.name_prefix}-sgw"
  services {
    service_id = local.regional_services.id
  }
  freeform_tags = local.common_tags
}

resource "oci_core_route_table" "public" {
  compartment_id = local.workload_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${local.name_prefix}-public-rt"
  route_rules {
    network_entity_id = oci_core_internet_gateway.this.id
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
  }
  freeform_tags = local.common_tags
}

resource "oci_core_route_table" "private" {
  compartment_id = local.workload_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${local.name_prefix}-private-rt"
  route_rules {
    network_entity_id = oci_core_nat_gateway.this.id
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
  }
  route_rules {
    network_entity_id = oci_core_service_gateway.this.id
    destination       = local.regional_services.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
  }
  freeform_tags = local.common_tags
}

resource "oci_core_security_list" "private" {
  compartment_id = local.workload_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${local.name_prefix}-private-sl"

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }

  ingress_security_rules {
    protocol = "6"
    source   = "10.80.0.0/16"
  }

  # OKE's Flannel configuration carries cross-node pod traffic over VXLAN UDP
  # port 14789 (verify this value after every CNI migration).
  ingress_security_rules {
    protocol = "17"
    source   = "10.80.0.0/16"
    udp_options {
      min = 14789
      max = 14789
    }
  }

  # OKE pods use the Flannel pod CIDR. Nodes receive inter-pod traffic with
  # the pod IP as its source, so allow it between private cluster subnets.
  ingress_security_rules {
    protocol = "all"
    source   = "10.244.0.0/16"
  }
  freeform_tags = local.common_tags
}

resource "oci_core_subnet" "nodes" {
  compartment_id             = local.workload_compartment_id
  vcn_id                     = oci_core_vcn.this.id
  display_name               = "${local.name_prefix}-nodes-subnet"
  cidr_block                 = "10.80.16.0/20"
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
  prohibit_public_ip_on_vnic = true
  dns_label                  = "nodes"
  freeform_tags              = local.common_tags
}

resource "oci_core_subnet" "endpoints" {
  compartment_id             = local.workload_compartment_id
  vcn_id                     = oci_core_vcn.this.id
  display_name               = "${local.name_prefix}-endpoints-subnet"
  cidr_block                 = "10.80.32.0/24"
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
  prohibit_public_ip_on_vnic = true
  dns_label                  = "endpoints"
  freeform_tags              = local.common_tags
}

resource "oci_core_subnet" "load_balancer" {
  compartment_id             = local.workload_compartment_id
  vcn_id                     = oci_core_vcn.this.id
  display_name               = "${local.name_prefix}-lb-subnet"
  cidr_block                 = "10.80.48.0/24"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.private.id]
  prohibit_public_ip_on_vnic = false
  dns_label                  = "lb"
  freeform_tags              = local.common_tags
}
