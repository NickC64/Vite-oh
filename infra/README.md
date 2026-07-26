# Terraform stacks

Infrastructure is split into two independently managed states:

- `bootstrap/` owns project APIs, Artifact Registry, Discord and workspace
  secret containers, GitHub Workload Identity Federation, the deployer service
  account, and the deployer's state-bucket access.
- `application/` owns Firestore, runtime service accounts and IAM, Cloud Tasks,
  Cloud Run, Cloud Scheduler, command registration, logging metrics, and
  alerts.

Only a local project administrator applies `bootstrap`. GitHub Actions applies
`application`, so the deployer never needs permission to modify the identity
provider it uses to authenticate.

## First-time bootstrap

The backend bucket must exist before either stack. Enable the two APIs needed
to let Terraform manage the remaining APIs:

```bash
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project=mail-in-votes
```

Create and version a regional state bucket if one does not already exist:

```bash
gcloud storage buckets create gs://YOUR_STATE_BUCKET \
  --project=mail-in-votes \
  --location=northamerica-northeast1 \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update gs://YOUR_STATE_BUCKET --versioning
```

Apply the bootstrap stack using local Application Default Credentials:

```bash
gcloud auth application-default login

export TF_VAR_project_id=mail-in-votes
export TF_VAR_github_repository=NickC64/Vite-oh
export TF_VAR_terraform_state_bucket=YOUR_STATE_BUCKET

terraform -chdir=infra/bootstrap init \
  -backend-config="bucket=${TF_VAR_terraform_state_bucket}" \
  -backend-config="prefix=viteoh/bootstrap"
terraform -chdir=infra/bootstrap apply
```

Add the bot token directly to Secret Manager:

```bash
printf '%s' "$DISCORD_BOT_TOKEN" |
  gcloud secrets versions add viteoh-discord-bot-token \
    --project=mail-in-votes \
    --data-file=-
```

Add an independent, high-entropy workspace signing key:

```bash
openssl rand -base64 48 |
  gcloud secrets versions add viteoh-workspace-signing-key \
    --project=mail-in-votes \
    --data-file=-
```

The key signs browser sessions and hashes requester identifiers. Do not reuse
the Discord token or place this key in GitHub or tfvars.

Use the bootstrap outputs for the GitHub `production` environment:

```bash
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER \
  --env production \
  --body "$(terraform -chdir=infra/bootstrap output -raw workload_identity_provider)"
gh variable set GCP_DEPLOYER_SERVICE_ACCOUNT \
  --env production \
  --body "$(terraform -chdir=infra/bootstrap output -raw deployer_service_account)"
```

Application configuration lives only in the GitHub `production` environment.
Do not maintain a parallel application `terraform.tfvars` locally.

## Migrating the original combined state

Do this once for installations created before the stack split:

1. Stop or wait for all Terraform and deployment workflows.
2. Confirm the original combined backend is still configured without a
   `prefix`.
3. Authenticate locally with an account that can read and write the state
   bucket.
4. Run:

   ```bash
   gcloud auth application-default login
   CONFIRM_SPLIT_STATE=yes DRY_RUN=yes \
     ./infra/migrate-state.sh YOUR_STATE_BUCKET
   CONFIRM_SPLIT_STATE=yes ./infra/migrate-state.sh YOUR_STATE_BUCKET
   ```

The script:

- pulls the original state without modifying it;
- creates independent lineages for `viteoh/bootstrap` and
  `viteoh/application`;
- preserves all existing resource addresses;
- refuses to overwrite either destination if it is non-empty; and
- prints the temporary directory containing local state backups.

The original backend object remains unchanged as a rollback archive. After the
split, review both plans before applying:

```bash
export TF_VAR_project_id=mail-in-votes
export TF_VAR_github_repository=NickC64/Vite-oh
export TF_VAR_terraform_state_bucket=YOUR_STATE_BUCKET

terraform -chdir=infra/bootstrap plan
```

Run the application plan through the production workflow so it receives the
GitHub environment configuration and immutable image reference.

Once both stacks are healthy, the manually granted
`roles/iam.workloadIdentityPoolAdmin` role can be removed from the deployer;
the application stack no longer reads or manages the federation pool:

```bash
gcloud projects remove-iam-policy-binding mail-in-votes \
  --member="serviceAccount:viteoh-github-deployer@mail-in-votes.iam.gserviceaccount.com" \
  --role="roles/iam.workloadIdentityPoolAdmin"
```
