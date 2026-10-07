resource "oci_containerengine_cluster" "this" {
  lifecycle {
    prevent_destroy = true
  }
  compartment_id     = local.workload_compartment_id
  kubernetes_version = var.kubernetes_version
  name               = "${local.name_prefix}-oke"
  vcn_id             = oci_core_vcn.this.id

  endpoint_config {
    is_public_ip_enabled = false
    subnet_id            = oci_core_subnet.endpoints.id
  }

  options {
    service_lb_subnet_ids = [oci_core_subnet.load_balancer.id]
  }

  freeform_tags = local.common_tags
}

resource "oci_containerengine_node_pool" "system" {
  cluster_id         = oci_containerengine_cluster.this.id
  compartment_id     = local.workload_compartment_id
  kubernetes_version = var.kubernetes_version
  name               = "${local.name_prefix}-system"
  node_shape         = var.system_node_shape

  node_shape_config {
    ocpus         = var.system_node_ocpus
    memory_in_gbs = var.system_node_memory_in_gbs
  }

  node_config_details {
    placement_configs {
      availability_domain = var.availability_domain
      subnet_id           = oci_core_subnet.nodes.id
    }
    size = var.system_node_count
  }

  node_source_details {
    boot_volume_size_in_gbs = var.system_node_boot_volume_size_in_gbs
    image_id                = var.oke_node_image_id
    source_type             = "IMAGE"
  }

  initial_node_labels {
    key   = "workload.oci.oracle.com/pool"
    value = "system"
  }

  freeform_tags = local.common_tags
}

resource "oci_containerengine_node_pool" "cpu" {
  cluster_id         = oci_containerengine_cluster.this.id
  compartment_id     = local.workload_compartment_id
  kubernetes_version = var.kubernetes_version
  name               = "${local.name_prefix}-cpu"
  node_shape         = var.cpu_node_shape

  node_shape_config {
    ocpus         = var.cpu_node_ocpus
    memory_in_gbs = var.cpu_node_memory_in_gbs
  }

  node_config_details {
    placement_configs {
      availability_domain = var.availability_domain
      subnet_id           = oci_core_subnet.nodes.id
    }
    size = var.cpu_node_count
  }

  node_source_details {
    # The upstream examples image includes Spark and CUDA layers even for a
    # CPU-only workflow. Keep enough unpacking headroom for its first pull.
    boot_volume_size_in_gbs = var.cpu_node_boot_volume_size_in_gbs
    image_id                = var.oke_node_image_id
    source_type             = "IMAGE"
  }

  initial_node_labels {
    key   = "workload.oci.oracle.com/pool"
    value = "cpu"
  }

  freeform_tags = local.common_tags
}

resource "oci_containerengine_node_pool" "gpu" {
  lifecycle {
    precondition {
      condition     = try(length(trimspace(var.gpu_availability_domain)) > 0 && length(trimspace(var.gpu_oke_node_image_id)) > 0, false)
      error_message = "An enabled GPU pool requires gpu_availability_domain and gpu_oke_node_image_id, even when its node count is zero."
    }
  }
  count              = var.enable_gpu_pool ? 1 : 0
  cluster_id         = oci_containerengine_cluster.this.id
  compartment_id     = local.workload_compartment_id
  kubernetes_version = var.kubernetes_version
  name               = "${local.name_prefix}-gpu"
  node_shape         = var.gpu_node_shape

  node_config_details {
    placement_configs {
      availability_domain = var.gpu_availability_domain
      subnet_id           = oci_core_subnet.nodes.id
    }
    size = var.gpu_node_count
  }

  node_source_details {
    image_id    = var.gpu_oke_node_image_id
    source_type = "IMAGE"
  }

  initial_node_labels {
    key   = "workload.oci.oracle.com/pool"
    value = "gpu"
  }

  freeform_tags = local.common_tags
}
