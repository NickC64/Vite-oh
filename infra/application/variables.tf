variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Region shared by Cloud Run, Firestore, Cloud Tasks, and Scheduler."
  type        = string
  default     = "northamerica-northeast1"
}

variable "container_image" {
  description = "Immutable container image reference, preferably by commit SHA."
  type        = string
}

variable "discord_application_id" {
  type = string
}

variable "discord_public_key" {
  type      = string
  sensitive = true
}

variable "discord_guild_id" {
  type = string
}

variable "discord_output_channel_id" {
  type = string
}

variable "discord_owner_user_id" {
  type = string
}

variable "proposal_timeout_seconds" {
  type    = number
  default = 172800
}

variable "notification_channels" {
  description = "Optional pre-existing Cloud Monitoring notification-channel resource names."
  type        = list(string)
  default     = []
}
