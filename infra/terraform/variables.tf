variable "project" {
  type    = string
  default = "sightbridge-advisor"
}

variable "environment" {
  type    = string
  default = "production"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alarm_email" {
  description = "Receives budget alerts."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Budget alerts fire at 50%, 80% and 100% (actual) of this amount."
  type        = number
  default     = 10
}

# --- Database (free-tier eligible defaults) ---
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "PostgreSQL major version with pgvector support on RDS."
  type        = string
  default     = "16"
}

variable "db_ingress_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach PostgreSQL. Lambda runs outside a VPC (no NAT / VPC-endpoint cost),
    so it has no fixed egress IP and the default is open. Access still requires TLS and a
    generated password. Narrow this, or move to private subnets, for production use.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# --- Models ---
variable "chat_model_id" {
  type    = string
  default = "us.amazon.nova-lite-v1:0"
}

variable "fallback_chat_model_id" {
  type    = string
  default = "deepseek.v3.2"
}

variable "embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

# --- Backend ---
variable "lambda_zip_path" {
  description = "Built by backend/scripts/build_lambda.sh."
  type        = string
  default     = "../../backend/dist/lambda.zip"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

# --- Frontend (optional until the repository is on GitHub) ---
variable "enable_amplify" {
  type    = bool
  default = false
}

variable "github_repository_url" {
  type    = string
  default = ""
}

variable "github_access_token" {
  description = "Fine-grained token with read access to the repository (Amplify webhook)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "frontend_branch" {
  type    = string
  default = "main"
}
