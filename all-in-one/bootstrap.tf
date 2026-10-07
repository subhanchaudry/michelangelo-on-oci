resource "tls_private_key" "bootstrap" {
  algorithm = "RSA"
  rsa_bits  = 4096
}
resource "oci_core_instance" "bootstrap" {
  compartment_id      = var.compartment_id
  availability_domain = local.availability_domain
  display_name        = "${local.name}-bootstrap"
  shape               = var.bootstrap_shape
  shape_config {
    ocpus         = var.bootstrap_ocpus
    memory_in_gbs = var.bootstrap_memory_gbs
  }
  source_details {
    source_type             = "image"
    source_id               = local.bootstrap_image
    boot_volume_size_in_gbs = 250
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.edge.id
    assign_public_ip = true
    hostname_label   = "bootstrap"
  }
  metadata = {
    ssh_authorized_keys = join("\n", compact([for key in [tls_private_key.bootstrap.public_key_openssh, var.operator_ssh_public_key] : trimspace(key)]))
    user_data = base64encode(<<-CLOUDINIT
 #cloud-config
 # Package installation runs after VCN DNS convergence via installer/bootstrap.sh.
 runcmd:
   - [mkdir, -p, /opt/michelangelo]
 CLOUDINIT
    )

  }
  # Cloud-init is initial-boot configuration. Dependency repairs are maintained
  # in installer/bootstrap.sh so source updates do not replace this builder.
  lifecycle {
    ignore_changes = [metadata["user_data"]]
  }
  freeform_tags = local.tags
}
resource "oci_resourcemanager_private_endpoint" "bootstrap" {
  count          = var.use_resource_manager_private_endpoint ? 1 : 0
  compartment_id = var.compartment_id
  display_name   = "${local.name}-orm"
  subnet_id      = oci_core_subnet.endpoints.id
  vcn_id         = oci_core_vcn.platform.id
}
# Resolve reachable IPs in the executing Apply job instead of reusing a value
# frozen into a saved Plan. Request the endpoint mapping immediately before SSH
# so the running job does not depend on an earlier job's connection setup.
resource "terraform_data" "orm_connection_refresh" {
  count = var.use_resource_manager_private_endpoint ? 1 : 0
  input = timestamp()
}
data "oci_resourcemanager_private_endpoint_reachable_ip" "bootstrap" {
  depends_on          = [terraform_data.orm_connection_refresh]
  count               = var.use_resource_manager_private_endpoint ? 1 : 0
  private_endpoint_id = oci_resourcemanager_private_endpoint.bootstrap[0].id
  private_ip          = oci_core_instance.bootstrap.private_ip
}
locals {
  install_config = merge({
    cluster_id                  = oci_containerengine_cluster.platform.id
    kubernetes_version          = local.kubernetes_version
    bootstrap_dynamic_group_id  = oci_identity_dynamic_group.bootstrap.id
    region                      = var.region
    compartment_id              = var.compartment_id
    namespace                   = "ma-system"
    object_storage_namespace    = local.object_namespace
    artifact_bucket             = oci_objectstorage_bucket.artifacts.name
    log_bucket                  = oci_objectstorage_bucket.logs.name
    mysql_host                  = oci_mysql_mysql_db_system.platform.ip_address
    mysql_expected_hostname     = local.mysql_expected_hostname
    mysql_ca_pem                = tls_self_signed_cert.mysql_ca.cert_pem
    mysql_username              = var.mysql_username
    mysql_password              = random_password.mysql.result
    registry                    = local.registry
    registry_host               = local.registry_host
    registry_username           = "${local.object_namespace}/${oci_identity_domains_user.automation.user_name}"
    registry_password           = oci_identity_domains_auth_token.registry.token
    artifact_access_key         = oci_identity_domains_customer_secret_key.artifacts.access_key
    artifact_secret_key         = oci_identity_domains_customer_secret_key.artifacts.secret_key
    runtime_registry_username   = "${local.object_namespace}/${oci_identity_domains_user.runtime_reader.user_name}"
    runtime_registry_password   = oci_identity_domains_auth_token.runtime_reader.token
    runtime_artifact_access_key = oci_identity_domains_customer_secret_key.runtime_reader.access_key
    runtime_artifact_secret_key = oci_identity_domains_customer_secret_key.runtime_reader.secret_key
    public_hostname             = local.public_hostname
    reserved_public_ip          = oci_core_public_ip.application.ip_address
    reserved_public_ip_id       = oci_core_public_ip.application.id
    lb_subnet_id                = oci_core_subnet.edge.id
    lb_subnet_ids               = [oci_core_subnet.edge.id]
  }, local.auth_config)
  installer_checksum = sha256(join("", [for f in sort(tolist(fileset("${path.module}/installer", "**"))) : filesha256("${path.module}/installer/${f}")]))
}
resource "null_resource" "install" {
  triggers = {
    instance_id              = oci_core_instance.bootstrap.id
    source_manifest_checksum = filesha256("${path.module}/SOURCE-MANIFEST.json")
    installer_checksum       = local.installer_checksum
    configuration_checksum   = sha256(jsonencode(local.install_config))

  }
  depends_on = [oci_containerengine_node_pool.platform, oci_mysql_mysql_db_system.platform, oci_identity_policy.bootstrap, oci_identity_policy.automation, oci_identity_policy.cluster_load_balancer, oci_identity_policy.runtime_reader, terraform_data.public_signing_certificates]
  connection {
    type        = "ssh"
    host        = var.use_resource_manager_private_endpoint ? data.oci_resourcemanager_private_endpoint_reachable_ip.bootstrap[0].ip_address : oci_core_instance.bootstrap.public_ip
    user        = "ubuntu"
    private_key = tls_private_key.bootstrap.private_key_pem
    timeout     = "5m"
    agent       = false

  }
  provisioner "remote-exec" {
    inline = ["sudo cloud-init status --wait > /dev/null || { test -f /var/lib/cloud/instance/boot-finished && echo 'Cloud-init recorded errors; repairing and verifying package dependencies'; }", "mkdir -p /home/ubuntu/installer", "touch /home/ubuntu/config.json && chmod 600 /home/ubuntu/config.json"]
  }
  provisioner "file" {
    source      = "${path.module}/installer/"
    destination = "/home/ubuntu/installer"
  }
  provisioner "file" {
    source      = "${path.module}/SOURCE-MANIFEST.json"
    destination = "/home/ubuntu/SOURCE-MANIFEST.json"
  }
  provisioner "file" {
    content     = jsonencode(local.install_config)
    destination = "/home/ubuntu/config.json"
  }
  provisioner "remote-exec" {
    inline = [
      "chmod 600 /home/ubuntu/config.json",
      "sudo install -d -m 700 /opt/michelangelo",
      "sudo cp -a /home/ubuntu/installer /opt/michelangelo/",
      "sudo install -m 644 /home/ubuntu/SOURCE-MANIFEST.json /opt/michelangelo/SOURCE-MANIFEST.json",
      "sudo install -m 600 /home/ubuntu/config.json /opt/michelangelo/config.json",
      "rm -f /home/ubuntu/config.json",
      "sudo bash /opt/michelangelo/installer/bootstrap.sh",
      "sudo python3 /opt/michelangelo/installer/install.py --config /opt/michelangelo/config.json",
    ]

  }
}
