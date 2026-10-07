data "oci_identity_availability_domains" "available" {
  compartment_id = var.tenancy_ocid
}
data "oci_identity_regions" "available" {
}
data "oci_objectstorage_namespace" "this" {
  compartment_id = var.tenancy_ocid
}
data "oci_containerengine_cluster_option" "available" {
  cluster_option_id = "all"
  compartment_id    = var.compartment_id
}
data "oci_containerengine_node_pool_option" "available" {
  node_pool_option_id = "all"
  compartment_id      = var.compartment_id
}
data "oci_core_images" "bootstrap" {
  compartment_id           = var.compartment_id
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.bootstrap_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}
data "oci_core_services" "available" {
}
locals {
  name                = "ma-${var.environment}"
  tags                = merge(var.tags, { Application = "Michelangelo", Environment = var.environment })
  availability_domain = coalesce(var.availability_domain, data.oci_identity_availability_domains.available.availability_domains[0].name)
  # Version strings are padded numerically to avoid lexicographic 1.9 > 1.34 mistakes.
  version_map = { for v in data.oci_containerengine_cluster_option.available.kubernetes_versions : format("%03d.%03d.%03d", tonumber(split(".", trimprefix(v, "v"))[0]), tonumber(split(".", trimprefix(v, "v"))[1]), tonumber(split(".", trimprefix(v, "v"))[2])) => v
  }
  kubernetes_version = coalesce(var.kubernetes_version, local.version_map[reverse(sort(keys(local.version_map)))[0]])
  node_images = { for image in data.oci_containerengine_node_pool_option.available.sources : image.source_name => image.image_id if image.source_type == "IMAGE" && strcontains(image.source_name, "OKE") && !strcontains(lower(image.source_name), "aarch64") && !strcontains(lower(image.source_name), "gpu") && strcontains(image.source_name, trimprefix(local.kubernetes_version, "v"))
  }
  node_image       = try(length(trimspace(var.oke_node_image_id)) > 0, false) ? var.oke_node_image_id : local.node_images[reverse(sort(keys(local.node_images)))[0]]
  bootstrap_image  = try(length(trimspace(var.bootstrap_image_id)) > 0, false) ? var.bootstrap_image_id : data.oci_core_images.bootstrap.images[0].id
  object_namespace = data.oci_objectstorage_namespace.this.namespace
  registry_host    = "${lower(one([for r in data.oci_identity_regions.available.regions : r.key if r.name == var.region]))}.ocir.io"
  registry         = "${local.registry_host}/${local.object_namespace}/${oci_artifacts_container_repository.platform.display_name}"
  public_hostname  = coalesce(var.public_hostname, "${oci_core_public_ip.application.ip_address}.sslip.io")
  service          = one([for s in data.oci_core_services.available.services : s if can(regex("^All .* Services In Oracle Services Network$", s.name))])
}
