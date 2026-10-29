output "api_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "documents_bucket" {
  value = aws_s3_bucket.documents.bucket
}

output "db_endpoint" {
  value = aws_db_instance.postgres.address
}

output "ssm_parameters" {
  description = "SecureStrings (values never printed): admin URL, app URL, app password."
  value = {
    admin_url    = aws_ssm_parameter.db_admin_url.name
    app_url      = aws_ssm_parameter.db_app_url.name
    app_password = aws_ssm_parameter.db_app_password.name
  }
}

output "amplify_default_domain" {
  value = var.enable_amplify ? "https://${var.frontend_branch}.${aws_amplify_app.web[0].default_domain}" : null
}
