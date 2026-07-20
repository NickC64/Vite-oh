#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: CONFIRM_SPLIT_STATE=yes $0 STATE_BUCKET" >&2
  exit 2
fi

if [[ "${CONFIRM_SPLIT_STATE:-}" != "yes" ]]; then
  echo "Set CONFIRM_SPLIT_STATE=yes after stopping all Terraform runs." >&2
  exit 2
fi

for command in terraform jq uuidgen; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Required command not found: ${command}" >&2
    exit 2
  fi
done

state_bucket="$1"
infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/viteoh-state-split.XXXXXX")"
full_state="${work_dir}/full.tfstate"
bootstrap_state="${work_dir}/bootstrap.tfstate"
application_state="${work_dir}/application.tfstate"

echo "State backup and split files: ${work_dir}"

terraform -chdir="${infra_dir}/migration" init -reconfigure \
  -backend-config="bucket=${state_bucket}"
terraform -chdir="${infra_dir}/migration" state pull >"${full_state}"

cp "${full_state}" "${bootstrap_state}"
cp "${full_state}" "${application_state}"

remove_resource() {
  local state_file="$1"
  local address="$2"

  if terraform state list -state="${state_file}" | grep -F "${address}" >/dev/null; then
    terraform state rm -state="${state_file}" "${address}" >/dev/null
  fi
}

application_resources=(
  "data.google_project.current"
  "google_firestore_database.app"
  "google_service_account.receiver"
  "google_service_account.worker"
  "google_service_account.task_invoker"
  "google_project_iam_member.receiver_task_manager"
  "google_project_iam_member.worker_roles"
  "google_service_account_iam_member.task_act_as"
  "google_secret_manager_secret_iam_member.worker_token"
  "google_cloud_tasks_queue.interactions"
  "google_cloud_tasks_queue.deadlines"
  "google_cloud_run_v2_service.worker"
  "google_cloud_run_v2_service.receiver"
  "google_cloud_run_v2_service_iam_member.receiver_public"
  "google_cloud_run_v2_service_iam_member.worker_invoker"
  "google_cloud_scheduler_job.reconcile"
  "google_cloud_run_v2_job.register_commands"
  "google_logging_metric.invalid_signatures"
  "google_logging_metric.reconciliation_repairs"
  "google_monitoring_alert_policy.invalid_signatures"
  "google_monitoring_alert_policy.worker_errors"
  "google_monitoring_alert_policy.task_attempt_errors"
  "google_monitoring_alert_policy.task_delay"
  "google_monitoring_alert_policy.reconciliation_repairs"
)

bootstrap_resources=(
  "google_project_service.apis"
  "google_artifact_registry_repository.app"
  "google_secret_manager_secret.discord_bot_token"
  "google_iam_workload_identity_pool.github"
  "google_iam_workload_identity_pool_provider.github"
  "google_service_account.deployer"
  "google_service_account_iam_member.github_wif"
  "google_project_iam_member.deployer_roles"
  "google_project_iam_member.deployer_service_account_user"
)

for address in "${application_resources[@]}"; do
  remove_resource "${bootstrap_state}" "${address}"
done

for address in "${bootstrap_resources[@]}"; do
  remove_resource "${application_state}" "${address}"
done

jq --arg lineage "$(uuidgen)" \
  '.lineage = $lineage | .outputs = {}' \
  "${bootstrap_state}" >"${bootstrap_state}.new"
mv "${bootstrap_state}.new" "${bootstrap_state}"

jq --arg lineage "$(uuidgen)" \
  '.lineage = $lineage | .outputs = {}' \
  "${application_state}" >"${application_state}.new"
mv "${application_state}.new" "${application_state}"

echo "Bootstrap resources:"
terraform state list -state="${bootstrap_state}"
echo "Application resources:"
terraform state list -state="${application_state}"

if [[ "${DRY_RUN:-}" == "yes" ]]; then
  echo "Dry run complete; no destination state was written."
  exit 0
fi

terraform -chdir="${infra_dir}/bootstrap" init -reconfigure \
  -backend-config="bucket=${state_bucket}" \
  -backend-config="prefix=viteoh/bootstrap"
terraform -chdir="${infra_dir}/application" init -reconfigure \
  -backend-config="bucket=${state_bucket}" \
  -backend-config="prefix=viteoh/application"

if terraform -chdir="${infra_dir}/bootstrap" state list 2>/dev/null | grep . >/dev/null; then
  echo "Bootstrap destination state is not empty; refusing to overwrite it." >&2
  exit 1
fi

if terraform -chdir="${infra_dir}/application" state list 2>/dev/null | grep . >/dev/null; then
  echo "Application destination state is not empty; refusing to overwrite it." >&2
  exit 1
fi

terraform -chdir="${infra_dir}/bootstrap" state push "${bootstrap_state}"
terraform -chdir="${infra_dir}/application" state push "${application_state}"

echo "State split complete. The original backend state was left unchanged."
echo "Run plans for infra/bootstrap and infra/application before applying."
