# Deployment

The application is deployed in **Asia Pacific (Sydney), `ap-southeast-2`**, using Terraform.
Terraform manages RDS, Cognito, Lambda, API Gateway, document/artifact buckets, runtime IAM,
SSM database parameters, Amplify and a $10 monthly budget. The state bucket is bootstrapped
separately. Frontend builds use the `main` branch and `frontend/` directory.

## Runtime

| Component | Configuration |
|---|---|
| Frontend | Amplify Hosting, Next.js 15 SSR (`WEB_COMPUTE`) |
| API | API Gateway HTTP API → Python 3.12 arm64 Lambda, 1 GB, 29-second timeout |
| Database | PostgreSQL 16, `db.t4g.micro`, 20 GB gp2, single AZ, pgvector, TLS required |
| Authentication | Invite-only Cognito user pool; advisor and admin reviewer accounts |
| Secrets | SSM SecureString, fetched server-side; secret values are excluded from source and Lambda configuration |
| Models | Amazon Nova Lite primary, DeepSeek V3.2 fallback, Titan V2 embeddings (1024 dimensions) |
| Retrieval | Six passages, similarity floor 0.40; abbreviation expansion for 1031, 721 and DST |
| Logs | Structured JSON with request IDs, 14-day CloudWatch retention |
| Budget | $10 monthly threshold; notifications at 50/80/100% actual and 100% forecast |

The database has a public endpoint to avoid NAT Gateway/interface-endpoint costs. TLS,
generated passwords and a least-privilege application role protect access. Private networking
is the next production hardening step. Database changes go through ORM models and Alembic.

## Provision or update

Use Terraform **1.10+** (S3 native state locking), AWS CLI, Python 3.12/uv and Node 22.
Configure the operator's AWS profile without committing credentials.

1. Set `AWS_PROFILE` and `AWS_REGION=ap-southeast-2` in the operator shell.
2. Bootstrap a private, encrypted, versioned S3 state bucket in Sydney. Copy
   `infra/terraform/backend.hcl.example` to the ignored `backend.hcl` and fill its bucket name.
3. Configure ignored `terraform.tfvars` from its example, including the approved alert email,
   calibrated retrieval floor and repository URL. Runtime secrets must already exist as
   SecureStrings. Terraform receives parameter names, never the Bedrock secret value.
4. Build the Lambda artifact with `cd backend && ./scripts/build_lambda.sh`.
5. From `infra/terraform`, run `terraform init -backend-config=backend.hcl`,
   `terraform plan -out=deployment.tfplan`, inspect the plan, and `terraform apply deployment.tfplan`.
6. Apply `alembic upgrade head` using the admin database URL from SSM. Run
   `python -m scripts.create_app_role` with `APP_DB_PASSWORD` supplied privately from SSM.
7. Ingest the corpus with Titan and the document bucket configured. Existing embeddings may
   be reused only when their model, input text and dimensions match exactly.
8. Create advisor/admin Cognito users with notifications suppressed; assign the admin group.
   Store reviewer passwords privately. No credentials belong in the repository.
9. Connect Amplify to the repository using a private operator credential. If the app is
   bootstrapped through the AWS API, import it as `aws_amplify_app.web[0]` before applying.
   GitHub credentials are omitted from Terraform state; `access_token` changes are ignored.
10. Start the Amplify `main` release build and run the verification below.

The state contains generated database passwords and must stay private and encrypted. The
reviewer credentials are kept in an ignored local file. Neither file is a submission artifact.

## Time budget

Model calls share a 20-second deadline. Query embedding waits at most four seconds, the
primary generation attempt at most eight, and fallback may use the remaining shared budget.
SDK retries default to zero. The socket read timeout is 20 seconds; worker deadlines bound
caller wait time independently of the SDK. Authentication and database I/O are outside this
budget, so this is not a guarantee that every request completes within Lambda's limit.

In-flight HTTP requests cannot be cancelled in Python. A late result is discarded, but the
attempt may still finish and be billed. Rotate expired runtime secrets through SSM and recycle
Lambda instances so cached clients load the replacement.

## Verify

- Health returns 200 with the database available; chat without a token returns 401.
- Both reviewer logins succeed. Advisors receive 403 from the audit API; admins receive 200.
- Ask about 1031 exchanges, DSTs and 721 exchanges. Check that citations open and quotes match.
- Confirm advice/injection refusals and disclaimers, history ownership, and admin audit records.
- Check the browser experience at desktop and mobile sizes.
- Run the real-model eval and inspect saved answers, not just the automated score.

The budget is an alert, **not a spending cap**. Eligible usage consumes credits/free allowances;
resources such as RDS still have metered costs. Stop the stack when the demonstration is over.
For teardown, review `terraform plan -destroy` before using `terraform destroy`. Nonempty S3
buckets require an explicit retention or cleanup decision; the state bucket is retained.

## Continuous deployment

CI checks Python, TypeScript, browser behavior and Terraform on pull requests and main pushes.
After a main push passes CI, `Deploy backend` checks out that exact commit, builds the arm64
Lambda package, uploads it to an encrypted `releases/<commit>.zip` object, updates the API and
checks health and unauthenticated access. Superseded commits are skipped. Amplify separately
builds the frontend from main.

Enable `enable_github_deployments` and configure `github_deployment_subjects` with the exact
repository/main OIDC subject. Repositories using immutable GitHub subject claims must include
the owner and repository IDs. Apply Terraform, then copy the public metadata from
`terraform output -json github_deployment_variables` into GitHub Actions repository variables.
The OIDC role can only write release objects and update/read this Lambda's code configuration;
it cannot read runtime secrets, modify the database or apply infrastructure changes. AWS access
keys and the Bedrock token are not GitHub secrets. Infrastructure and migrations remain operator
Terraform/Alembic operations.

For a rollback, revert the offending commit on main and let CI and CD deploy the tested revert.

Current account limitation: creating the GitHub IAM OIDC provider is explicitly denied by an
organization service control policy. The deployment workflow stays inactive while
`AWS_DEPLOY_ROLE_ARN` is unset; the application still deploys through the operator's Terraform
profile, and Amplify frontend deployment remains active. An account administrator must allow
OIDC provider creation before backend CD can be enabled. No long-lived AWS credentials were
added to GitHub as a workaround.
