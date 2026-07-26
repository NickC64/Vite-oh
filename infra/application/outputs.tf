output "interaction_endpoint_url" {
  value = "${google_cloud_run_v2_service.receiver.uri}/interactions"
}

output "worker_url" {
  value = local.worker_url
}

output "workspace_url" {
  value = local.workspace_url
}

output "workspace_health_url" {
  value = "${local.workspace_url}/health"
}
