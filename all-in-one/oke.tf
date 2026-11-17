resource "oci_containerengine_cluster" "platform" {
  compartment_id     = var.compartment_id
  name               = "${local.name}-oke"
  type               = "BASIC_CLUSTER"
  kubernetes_version = local.kubernetes_version
  vcn_id             = oci_core_vcn.platform.id
  endpoint_config {
    is_public_ip_enabled = false
    subnet_id            = oci_core_subnet.endpoints.id
  }
  options {
    service_lb_subnet_ids = [oci_core_subnet.edge.id]
    kubernetes_network_config {
      pods_cidr     = "10.244.0.0/16"
      services_cidr = "10.96.0.0/16"
    }
  }
  freeform_tags = local.tags
}
resource "oci_containerengine_node_pool" "platform" {
  for_each           = toset(["system", "cpu"])
  compartment_id     = var.compartment_id
  cluster_id         = oci_containerengine_cluster.platform.id
  name               = "${local.name}-${each.key}"
  kubernetes_version = local.kubernetes_version
  node_shape         = var.node_shape
  node_shape_config {
    ocpus         = var.node_ocpus
    memory_in_gbs = var.node_memory_gbs
  }
  node_config_details {
    size = each.key == "cpu" ? var.cpu_node_count : 1
    placement_configs {
      availability_domain = local.availability_domain
      subnet_id           = oci_core_subnet.nodes.id
    }
  }
  node_source_details {
    source_type             = "IMAGE"
    image_id                = local.node_image
    boot_volume_size_in_gbs = each.key == "cpu" ? var.cpu_boot_gbs : var.node_boot_gbs
  }
  initial_node_labels {
    key   = "workload.oci.oracle.com/pool"
    value = each.key
  }
  # OKE image partitions retain their original size unless explicitly grown.
  # Preserve OCI's initialization script and expand the root before joining.
  node_metadata = {
    user_data = base64encode(<<-NODEINIT
      #!/bin/bash
      set -euo pipefail
      curl --fail --retry 10 --retry-delay 3 -H "Authorization: Bearer Oracle" -L0 http://169.254.169.254/opc/v2/instance/metadata/oke_init_script | base64 --decode > /var/run/oke-init.sh
      bash /usr/libexec/oci-growfs -y
      bash /var/run/oke-init.sh
    NODEINIT
    )
  }
  freeform_tags = local.tags
}
