locals {
  service_name_receiver = "viteoh-interactions"
  service_name_worker   = "viteoh-worker"
  service_name_web      = "viteoh-web"
  worker_url            = "https://${local.service_name_worker}-${data.google_project.current.number}.${var.region}.run.app"
  workspace_url         = "https://${local.service_name_web}-${data.google_project.current.number}.${var.region}.run.app"
}

data "google_project" "current" {}

data "google_secret_manager_secret" "discord_bot_token" {
  secret_id = "viteoh-discord-bot-token"
}

data "google_secret_manager_secret" "workspace_signing_key" {
  secret_id = "viteoh-workspace-signing-key"
}

data "google_secret_manager_secret" "proposal_ownership_key" {
  secret_id = "viteoh-proposal-ownership-key"
}

resource "google_firestore_database" "app" {
  project                     = var.project_id
  name                        = "(default)"
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "PESSIMISTIC"
  app_engine_integration_mode = "DISABLED"
  delete_protection_state     = "DELETE_PROTECTION_ENABLED"
}

resource "google_service_account" "receiver" {
  account_id   = "viteoh-receiver"
  display_name = "Vite-oh public interaction receiver"
}

resource "google_service_account" "worker" {
  account_id   = "viteoh-worker"
  display_name = "Vite-oh private worker"
}

resource "google_service_account" "web" {
  account_id   = "viteoh-web"
  display_name = "Vite-oh public web workspace"
}

resource "google_service_account" "task_invoker" {
  account_id   = "viteoh-task-invoker"
  display_name = "Vite-oh Cloud Tasks and Scheduler invoker"
}

resource "google_project_iam_member" "receiver_task_manager" {
  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"
  member  = "serviceAccount:${google_service_account.receiver.email}"
}

resource "google_project_iam_member" "receiver_firestore_reader" {
  project = var.project_id
  role    = "roles/datastore.viewer"
  member  = "serviceAccount:${google_service_account.receiver.email}"
}

resource "google_project_iam_member" "web_roles" {
  for_each = toset([
    "roles/cloudtasks.enqueuer",
    "roles/datastore.viewer",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.web.email}"
}

resource "google_project_iam_member" "worker_roles" {
  for_each = toset([
    "roles/cloudtasks.enqueuer",
    "roles/cloudtasks.taskDeleter",
    "roles/cloudtasks.viewer",
    "roles/datastore.user",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_service_account_iam_member" "task_act_as" {
  for_each = {
    receiver = google_service_account.receiver.email
    worker   = google_service_account.worker.email
    web      = google_service_account.web.email
  }
  service_account_id = google_service_account.task_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${each.value}"
}

resource "google_secret_manager_secret_iam_member" "worker_token" {
  secret_id = data.google_secret_manager_secret.discord_bot_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_proposal_ownership_key" {
  secret_id = data.google_secret_manager_secret.proposal_ownership_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_secret_manager_secret_iam_member" "workspace_signing_key" {
  for_each = {
    web    = google_service_account.web.email
    worker = google_service_account.worker.email
  }
  secret_id = data.google_secret_manager_secret.workspace_signing_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

resource "google_cloud_tasks_queue" "interactions" {
  name     = "viteoh-interactions"
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 20
    max_dispatches_per_second = 20
  }
  retry_config {
    max_attempts       = 12
    max_retry_duration = "900s"
    min_backoff        = "1s"
    max_backoff        = "60s"
    max_doublings      = 5
  }
}

resource "google_cloud_tasks_queue" "deadlines" {
  name     = "viteoh-deadlines"
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 10
    max_dispatches_per_second = 10
  }
  retry_config {
    max_attempts       = -1
    max_retry_duration = "86400s"
    min_backoff        = "5s"
    max_backoff        = "3600s"
    max_doublings      = 8
  }
}

resource "google_cloud_tasks_queue" "workspace" {
  name     = "viteoh-workspace"
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 20
    max_dispatches_per_second = 20
  }
  retry_config {
    max_attempts       = 12
    max_retry_duration = "900s"
    min_backoff        = "1s"
    max_backoff        = "60s"
    max_doublings      = 5
  }
}

resource "google_cloud_run_v2_service" "worker" {
  name                = local.service_name_worker
  location            = var.region
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.worker.email
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }
    containers {
      image = var.container_image
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
      ports {
        container_port = 8080
      }
      env {
        name  = "SERVICE_ROLE"
        value = "worker"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "WORKER_URL"
        value = local.worker_url
      }
      env {
        name  = "WORKSPACE_URL"
        value = local.workspace_url
      }
      env {
        name  = "DISCORD_APPLICATION_ID"
        value = var.discord_application_id
      }
      env {
        name  = "DISCORD_OWNER_USER_ID"
        value = var.discord_owner_user_id
      }
      env {
        name  = "TASK_INVOKER_SERVICE_ACCOUNT"
        value = google_service_account.task_invoker.email
      }
      env {
        name = "DISCORD_BOT_TOKEN"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.discord_bot_token.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "WORKSPACE_SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.workspace_signing_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "PROPOSAL_OWNERSHIP_SECRET"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.proposal_ownership_key.secret_id
            version = "latest"
          }
        }
      }
      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 3
        failure_threshold     = 10
        http_get {
          path = "/health"
          port = 8080
        }
      }
    }
  }
  depends_on = [
    google_project_iam_member.worker_roles,
    google_secret_manager_secret_iam_member.worker_token,
    google_secret_manager_secret_iam_member.worker_proposal_ownership_key,
    google_secret_manager_secret_iam_member.workspace_signing_key,
    google_service_account_iam_member.task_act_as,
  ]
}

resource "google_cloud_run_v2_service" "receiver" {
  name                = local.service_name_receiver
  location            = var.region
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.receiver.email
    timeout         = "10s"
    scaling {
      min_instance_count = 1
      max_instance_count = 3
    }
    containers {
      image = var.container_image
      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
        cpu_idle = true
      }
      ports {
        container_port = 8080
      }
      env {
        name  = "SERVICE_ROLE"
        value = "receiver"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "WORKER_URL"
        value = local.worker_url
      }
      env {
        name  = "WORKSPACE_URL"
        value = local.workspace_url
      }
      env {
        name  = "TASK_INVOKER_SERVICE_ACCOUNT"
        value = google_service_account.task_invoker.email
      }
      env {
        name  = "DISCORD_PUBLIC_KEY"
        value = var.discord_public_key
      }
      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 3
        failure_threshold     = 10
        http_get {
          path = "/health"
          port = 8080
        }
      }
    }
  }
  depends_on = [
    google_project_iam_member.receiver_firestore_reader,
    google_project_iam_member.receiver_task_manager,
    google_service_account_iam_member.task_act_as,
  ]
}

resource "google_cloud_run_v2_service" "web" {
  name                = local.service_name_web
  location            = var.region
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.web.email
    timeout         = "30s"
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
    containers {
      image = var.container_image
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
      ports {
        container_port = 8080
      }
      env {
        name  = "SERVICE_ROLE"
        value = "web"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "WORKER_URL"
        value = local.worker_url
      }
      env {
        name  = "WORKSPACE_URL"
        value = local.workspace_url
      }
      env {
        name  = "TASK_INVOKER_SERVICE_ACCOUNT"
        value = google_service_account.task_invoker.email
      }
      env {
        name  = "DISCORD_APPLICATION_ID"
        value = var.discord_application_id
      }
      env {
        name  = "DISCORD_OWNER_USER_ID"
        value = var.discord_owner_user_id
      }
      env {
        name = "WORKSPACE_SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.workspace_signing_key.secret_id
            version = "latest"
          }
        }
      }
      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 3
        failure_threshold     = 10
        http_get {
          path = "/health"
          port = 8080
        }
      }
    }
  }
  depends_on = [
    google_project_iam_member.web_roles,
    google_secret_manager_secret_iam_member.workspace_signing_key,
    google_service_account_iam_member.task_act_as,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "receiver_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.receiver.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.web.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.task_invoker.email}"
}

resource "google_cloud_run_v2_service_iam_member" "web_worker_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.web.email}"
}

resource "google_firestore_index" "proposal_history" {
  project    = var.project_id
  database   = google_firestore_database.app.name
  collection = "proposals"
  fields {
    field_path = "guild_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "archived"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

resource "google_firestore_field" "workspace_job_ttl" {
  project    = var.project_id
  database   = google_firestore_database.app.name
  collection = "workspace_jobs"
  field      = "expires_at"
  ttl_config {}
}

resource "google_firestore_field" "workspace_launch_ttl" {
  project    = var.project_id
  database   = google_firestore_database.app.name
  collection = "workspace_launches"
  field      = "expires_at"
  ttl_config {}
}

resource "google_cloud_scheduler_job" "reconcile" {
  name             = "viteoh-reconcile"
  description      = "Repair missing deadline tasks and finalize overdue proposals."
  schedule         = "*/5 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"
  region           = var.region
  retry_config {
    retry_count          = 5
    min_backoff_duration = "5s"
    max_backoff_duration = "300s"
    max_doublings        = 4
  }
  http_target {
    uri         = "${local.worker_url}/tasks/reconcile"
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.task_invoker.email
      audience              = local.worker_url
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.worker_invoker]
}

resource "google_cloud_run_v2_job" "register_commands" {
  name                = "viteoh-register-commands"
  location            = var.region
  deletion_protection = true
  template {
    template {
      service_account = google_service_account.worker.email
      timeout         = "300s"
      containers {
        image   = var.container_image
        command = ["python", "-m", "viteoh.register_commands"]
        env {
          name  = "DISCORD_APPLICATION_ID"
          value = var.discord_application_id
        }
        env {
          name = "DISCORD_BOT_TOKEN"
          value_source {
            secret_key_ref {
              secret  = data.google_secret_manager_secret.discord_bot_token.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.worker_token]
}

resource "google_logging_metric" "invalid_signatures" {
  name   = "viteoh/invalid_discord_signatures"
  filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${local.service_name_receiver}"
    textPayload:"Rejected invalid Discord request signature"
  EOT
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "invalid_signatures" {
  display_name          = "Vite-oh invalid Discord signature spike"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "More than 20 invalid signatures in five minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.invalid_signatures.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 20
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "worker_errors" {
  display_name          = "Vite-oh worker 5xx errors"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "Worker returned a 5xx response"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${local.service_name_worker}\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "web_errors" {
  display_name          = "Vite-oh workspace 5xx errors"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "Workspace returned a 5xx response"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${local.service_name_web}\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_logging_metric" "workspace_authorization_denials" {
  name   = "viteoh/workspace_authorization_denials"
  filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${local.service_name_web}"
    textPayload:"Workspace authorization denied"
  EOT
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "workspace_authorization_denials" {
  display_name          = "Vite-oh workspace authorization denial spike"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "More than 20 workspace denials in five minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.workspace_authorization_denials.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 20
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "task_attempt_errors" {
  display_name          = "Vite-oh Cloud Tasks delivery errors"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "A task attempt returned a non-OK response"
    condition_threshold {
      filter          = "resource.type=\"cloud_tasks_queue\" AND metric.type=\"cloudtasks.googleapis.com/queue/task_attempt_count\" AND metric.labels.response_code!=\"ok\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "task_delay" {
  display_name          = "Vite-oh Cloud Tasks delivery delay"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "99th percentile task delay exceeds five minutes"
    condition_threshold {
      filter          = "resource.type=\"cloud_tasks_queue\" AND metric.type=\"cloudtasks.googleapis.com/queue/task_attempt_delays\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 300000
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }
}

resource "google_logging_metric" "reconciliation_repairs" {
  name   = "viteoh/reconciliation_repairs"
  filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${local.service_name_worker}"
    textPayload:"Reconciliation repaired proposal state"
  EOT
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "reconciliation_repairs" {
  display_name          = "Vite-oh deadline reconciliation repair"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "A missing task, overdue proposal, or side effect required repair"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.reconciliation_repairs.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}
