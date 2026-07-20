variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Region for the Artifact Registry repository."
  type        = string
  default     = "northamerica-northeast1"
}

variable "github_repository" {
  description = "GitHub repository in owner/name form."
  type        = string
}

variable "terraform_state_bucket" {
  description = "Pre-existing GCS bucket used by both Terraform backends."
  type        = string
}
