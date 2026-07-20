output "artifact_registry_repository" {
  value = google_artifact_registry_repository.app.name
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "discord_bot_token_secret" {
  value = google_secret_manager_secret.discord_bot_token.id
}
