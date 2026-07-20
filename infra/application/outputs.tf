output "interaction_endpoint_url" {
  value = "${google_cloud_run_v2_service.receiver.uri}/interactions"
}

output "worker_url" {
  value = local.worker_url
}
