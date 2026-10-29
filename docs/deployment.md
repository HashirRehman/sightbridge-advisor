# Deployment (AWS, Terraform)

Status: **not yet applied.** The Terraform in `infra/terraform` validates and lints cleanly, and the Lambda package was tested in AWS's official `python:3.12-arm64` Lambda image. Deployment is waiting on an AWS account where Bedrock can be invoked (see [Blocker](#blocker)).

## Architecture (free-tier shaped)

| Component | Service | Notes |
|---|---|---|
| Web app | Amplify Hosting (Next.js SSR, `WEB_COMPUTE`) | Built from `frontend/`. Server-side env vars written to `.env.production` at build time |
| API | API Gateway HTTP API → Lambda (Python 3.12, arm64, 1 GB, 29 s) | Throttling: 5 rps, burst 10. **Outside a VPC**, so no NAT or VPC-endpoint cost |
| Auth | Cognito user pool | Invite-only, `admin` group, public client with USER_PASSWORD_AUTH |
| Database | RDS PostgreSQL 16 `db.t4g.micro`, 20 GB gp2, single-AZ, pgvector | `rds.force_ssl=1`. Public endpoint protected by TLS and a 40-character generated password |
| Secrets | SSM Parameter Store SecureString | Admin URL, app URL, app password. The API reads its URL at cold start |
| Documents | Private S3 bucket (SSE, versioned, TLS-only policy) | Originals referenced by `documents.storage_key` |
| Models | Bedrock: Nova Lite (primary), DeepSeek V3.2 (fallback), Titan Text Embeddings V2 | IAM limits invocation to exactly these models |
| Logs | CloudWatch, 14-day retention | JSON lines with request IDs |
| Cost guard | AWS Budgets at $10/month: 50/80/100% actual + 100% forecast | Emails `alarm_email` |

**Accepted tradeoff:** to avoid NAT ($32+/month) or interface endpoints (about $7/month each), the database endpoint is internet-reachable. It still requires TLS, the generated password and a least-privilege role. The production path is private subnets for RDS and Lambda, with endpoints for Bedrock, SSM and S3.

**Expected cost:**
- Worst case about **$18/month**, mostly RDS (instance, storage, public IPv4), if no RDS free allowance applies.
- Bedrock usage is fractions of a cent per question with Nova Lite. Embedding the full corpus once is under $0.01.
- Lambda, API Gateway, Cognito, CloudWatch and S3 stay within free allowances at demo volume.

## Runbook

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-east-1

# 0. Prove Bedrock works on this account (listing models is not enough)
cd backend && uv run python -m scripts.bedrock_smoke

# 1. Terraform state bucket (once)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3api create-bucket --bucket sightbridge-tfstate-$ACCOUNT
aws s3api put-bucket-versioning --bucket sightbridge-tfstate-$ACCOUNT --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket sightbridge-tfstate-$ACCOUNT \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# 2. Build the Lambda package
./scripts/build_lambda.sh                      # → backend/dist/lambda.zip

# 3. Provision
cd ../infra/terraform
cp backend.hcl.example backend.hcl && cp terraform.tfvars.example terraform.tfvars   # edit both
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan && terraform apply tfplan

# 4. Database: migrations → least-privilege app role → corpus ingestion (operator machine)
cd ../../backend
export DATABASE_URL=$(aws ssm get-parameter --name /sightbridge-advisor/production/database-admin-url --with-decryption --query Parameter.Value --output text)
uv run alembic upgrade head
APP_DB_PASSWORD=$(aws ssm get-parameter --name /sightbridge-advisor/production/database-app-password --with-decryption --query Parameter.Value --output text) \
  uv run python -m scripts.create_app_role
DOCUMENTS_BUCKET=$(terraform -chdir=../infra/terraform output -raw documents_bucket) \
  EMBEDDING_PROVIDER=bedrock uv run python -m scripts.ingest
EMBEDDING_PROVIDER=bedrock LLM_PROVIDER=bedrock uv run python -m scripts.run_eval --label titan__nova-lite

# 5. Reviewer accounts (passwords shared out of band, never committed)
POOL=$(terraform -chdir=../infra/terraform output -raw cognito_user_pool_id)
for u in advisor.reviewer@example.com admin.reviewer@example.com; do
  aws cognito-idp admin-create-user --user-pool-id $POOL --username $u \
    --user-attributes Name=email,Value=$u Name=email_verified,Value=true --message-action SUPPRESS
  aws cognito-idp admin-set-user-password --user-pool-id $POOL --username $u --password '<strong password>' --permanent
done
aws cognito-idp admin-add-user-to-group --user-pool-id $POOL --username admin.reviewer@example.com --group-name admin

# 6. Frontend: push the repo, then set enable_amplify=true (+ repository URL, token via
#    TF_VAR_github_access_token) and apply again; Amplify builds main.

# 7. Verify: GET <api_url>/api/health; sign in as both users; ask a question; check
#    citations open; check the audit log as admin and the 403 as advisor.

# Teardown
terraform destroy
```

## Blocker

The first account checked (`write-hashir`, Free plan) returns `ValidationException: Operation not allowed` for **every** Bedrock model, including Amazon's own Titan and Nova. IAM was confirmed as AdministratorAccess, so this is an account-level restriction that AWS applies to some new or Free-plan accounts. Fixes are an AWS Support case or an account without the restriction. The smoke test in step 0 detects it before anything is provisioned.
