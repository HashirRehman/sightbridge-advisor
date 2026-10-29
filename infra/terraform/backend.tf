locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  # "us.amazon.nova-lite-v1:0" (cross-Region profile) → "amazon.nova-lite-v1:0" (base model)
  chat_base_model     = replace(var.chat_model_id, "/^(us|eu|apac|global)\\./", "")
  is_profile          = local.chat_base_model != var.chat_model_id
  fallback_base_model = replace(var.fallback_chat_model_id, "/^(us|eu|apac|global)\\./", "")
  fallback_is_profile = local.fallback_base_model != var.fallback_chat_model_id
  safety_base_model   = replace(var.safety_model_id, "/^(us|eu|apac|global)\\./", "")
  safety_is_profile   = local.safety_base_model != var.safety_model_id
}

# --- Logs (short retention keeps CloudWatch inside the free allowance) ---
resource "aws_cloudwatch_log_group" "api_lambda" {
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = var.log_retention_days
}

# --- IAM: only what the API needs at runtime ---
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "DispatchOwnAnswerWorker"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.api.arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api_lambda.arn}:*"]
  }

  statement {
    sid     = "ReadDatabaseUrl"
    actions = ["ssm:GetParameter"]
    resources = concat(
      [aws_ssm_parameter.db_app_url.arn],
      var.bedrock_api_key_ssm_parameter == "" ? [] : [
        "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${var.bedrock_api_key_ssm_parameter}",
      ],
    )
  }

  statement {
    sid     = "DecryptSsmDefaultKey"
    actions = ["kms:Decrypt"]
    resources = ["arn:${local.partition}:kms:${var.region}:${local.account_id}:alias/aws/ssm",
    "arn:${local.partition}:kms:${var.region}:${local.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid     = "InvokeApprovedModelsOnly"
    actions = ["bedrock:InvokeModel"]
    resources = concat(
      [
        "arn:${local.partition}:bedrock:${var.region}::foundation-model/${var.embedding_model_id}",
        "arn:${local.partition}:bedrock:${split("-", var.region)[0]}-*::foundation-model/${local.chat_base_model}",
      ],
      local.is_profile ? ["arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/${var.chat_model_id}"] : [],
      var.fallback_chat_model_id == "" ? [] : [
        "arn:${local.partition}:bedrock:${split("-", var.region)[0]}-*::foundation-model/${local.fallback_base_model}",
      ],
      local.fallback_is_profile ? ["arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/${var.fallback_chat_model_id}"] : [],
      ["arn:${local.partition}:bedrock:${split("-", var.region)[0]}-*::foundation-model/${local.safety_base_model}"],
      local.safety_is_profile ? ["arn:${local.partition}:bedrock:${var.region}:${local.account_id}:inference-profile/${var.safety_model_id}"] : [],
    )
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "runtime"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# --- Deployment artifact (the zip is ~45 MB, close to the 50 MB direct-upload limit) ---
resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.name}-artifacts-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "lambda_zip" {
  bucket      = aws_s3_bucket.artifacts.id
  key         = "api/${filesha256(var.lambda_zip_path)}.zip"
  source      = var.lambda_zip_path
  source_hash = filesha256(var.lambda_zip_path)
}

# --- Lambda: FastAPI via Mangum ---
resource "aws_lambda_function" "api" {
  function_name    = "${local.name}-api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "app.lambda_handler.handler"
  s3_bucket        = aws_s3_object.lambda_zip.bucket
  s3_key           = aws_s3_object.lambda_zip.key
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 1024
  timeout          = 60 # Background worker; synchronous model calls retain their 20-second budget.

  logging_config {
    log_format = "Text" # the app already emits structured JSON lines
    log_group  = aws_cloudwatch_log_group.api_lambda.name
  }

  environment {
    variables = {
      ENVIRONMENT                    = "production"
      LOG_LEVEL                      = "INFO"
      DATABASE_URL_SSM_PARAMETER     = aws_ssm_parameter.db_app_url.name
      EMBEDDING_PROVIDER             = "bedrock"
      LLM_PROVIDER                   = "bedrock"
      BEDROCK_CHAT_MODEL_ID          = var.chat_model_id
      BEDROCK_FALLBACK_CHAT_MODEL_ID = var.fallback_chat_model_id
      BEDROCK_EMBEDDING_MODEL_ID     = var.embedding_model_id
      BEDROCK_API_KEY_SSM_PARAMETER  = var.bedrock_api_key_ssm_parameter
      GENERATION_TIMEOUT_SECONDS     = tostring(var.generation_timeout_seconds)
      SAFETY_MODEL_ID                = var.safety_model_id
      RETRIEVAL_MIN_SIMILARITY       = tostring(var.retrieval_min_similarity)
      AUTH_PROVIDER                  = "cognito"
      COGNITO_REGION                 = var.region
      COGNITO_USER_POOL_ID           = aws_cognito_user_pool.main.id
      COGNITO_APP_CLIENT_ID          = aws_cognito_user_pool_client.web.id
      DOCUMENTS_BUCKET               = aws_s3_bucket.documents.bucket
      CORS_ALLOWED_ORIGINS           = ""
    }
  }
}

resource "aws_lambda_function_event_invoke_config" "api" {
  function_name                = aws_lambda_function.api.function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
}

# --- HTTP API with stage-level throttling as a cost/abuse backstop ---
resource "aws_apigatewayv2_api" "http" {
  name          = "${local.name}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "chat" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /api/chat"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.chat.route_key
    throttling_burst_limit = 4
    throttling_rate_limit  = 2
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      method    = "$context.httpMethod"
      path      = "$context.path"
      status    = "$context.status"
      latencyMs = "$context.integrationLatency"
    })
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
