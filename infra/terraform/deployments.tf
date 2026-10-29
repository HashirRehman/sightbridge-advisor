# GitHub receives short-lived credentials restricted to publishing this API's code.
# Infrastructure and runtime secrets remain managed separately by Terraform and SSM.
variable "enable_github_deployments" {
  type    = bool
  default = false
}

variable "github_deployment_subjects" {
  description = "Exact GitHub OIDC subjects allowed to deploy main (including immutable IDs if enabled)."
  type        = list(string)
  default     = []
  validation {
    condition = alltrue([
      for subject in var.github_deployment_subjects :
      startswith(subject, "repo:") && endswith(subject, ":ref:refs/heads/main") && !strcontains(subject, "*")
    ])
    error_message = "Deployment subjects must identify an exact repository's main branch."
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.enable_github_deployments ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_deploy_assume" {
  count = var.enable_github_deployments ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = var.github_deployment_subjects
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count              = var.enable_github_deployments ? 1 : 0
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume[0].json
  lifecycle {
    precondition {
      condition     = length(var.github_deployment_subjects) > 0
      error_message = "Configure the repository's exact OIDC subject before enabling deployments."
    }
  }
}

data "aws_iam_policy_document" "github_deploy" {
  count = var.enable_github_deployments ? 1 : 0
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/releases/*"]
  }
  statement {
    actions   = ["lambda:UpdateFunctionCode", "lambda:GetFunctionConfiguration"]
    resources = [aws_lambda_function.api.arn]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = var.enable_github_deployments ? 1 : 0
  name   = "deploy-api-code"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}

output "github_deployment_variables" {
  value = var.enable_github_deployments ? {
    AWS_DEPLOY_ROLE_ARN  = aws_iam_role.github_deploy[0].arn
    AWS_REGION           = var.region
    LAMBDA_FUNCTION_NAME = aws_lambda_function.api.function_name
    ARTIFACTS_BUCKET     = aws_s3_bucket.artifacts.id
    API_BASE_URL         = aws_apigatewayv2_stage.default.invoke_url
  } : {}
}
