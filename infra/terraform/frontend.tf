# Next.js SSR on Amplify Hosting (WEB_COMPUTE). Enabled once the repository is on GitHub.
resource "aws_amplify_app" "web" {
  count        = var.enable_amplify ? 1 : 0
  name         = local.name
  repository   = var.github_repository_url
  access_token = var.github_access_token == "" ? null : var.github_access_token
  platform     = "WEB_COMPUTE"

  lifecycle {
    ignore_changes = [access_token]
  }

  # Server-side env vars for Next.js SSR must be written to .env.production at build time;
  # Amplify does not expose app environment variables to the SSR runtime otherwise.
  build_spec = <<-YAML
    version: 1
    applications:
      - appRoot: frontend
        frontend:
          phases:
            preBuild:
              commands:
                - npm ci
            build:
              commands:
                - env | grep -E '^(APP_ENV|API_BASE_URL|AUTH_PROVIDER|COGNITO_REGION|COGNITO_APP_CLIENT_ID)=' > .env.production
                - printf 'BUILD_COMMIT_ID=%s\n' "$(git rev-parse HEAD)" >> .env.production
                - npm run build
          artifacts:
            baseDirectory: .next
            files:
              - '**/*'
          cache:
            paths:
              - node_modules/**/*
              - .next/cache/**/*
  YAML

  environment_variables = {
    AMPLIFY_MONOREPO_APP_ROOT = "frontend"
    APP_ENV                   = "production"
    API_BASE_URL              = aws_apigatewayv2_stage.default.invoke_url
    AUTH_PROVIDER             = "cognito"
    COGNITO_REGION            = var.region
    COGNITO_APP_CLIENT_ID     = aws_cognito_user_pool_client.web.id
  }
}

resource "aws_amplify_branch" "main" {
  count             = var.enable_amplify ? 1 : 0
  app_id            = aws_amplify_app.web[0].id
  branch_name       = var.frontend_branch
  framework         = "Next.js - SSR"
  stage             = "PRODUCTION"
  enable_auto_build = false # The GitHub quality gate triggers the incoming webhook.
}


resource "aws_amplify_webhook" "release" {
  count       = var.enable_amplify ? 1 : 0
  app_id      = aws_amplify_app.web[0].id
  branch_name = aws_amplify_branch.main[0].branch_name
  description = "GitHub gated release pipeline"
}
