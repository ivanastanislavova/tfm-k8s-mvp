output "master_public_ip" {
  value = oci_core_instance.master.public_ip
}

output "worker_public_ips" {
  value = [for w in oci_core_instance.worker : w.public_ip]
}

output "ssh_user" {
  value = "ubuntu"
}