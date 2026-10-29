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
  default = "ap-southeast-2"
}

variable "alarm_email" {
  description = "Optional email recipient for budget alerts."
  type        = string
  default     = ""
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
  default = "deepseek.v3.2"
}

variable "fallback_chat_model_id" {
  type    = string
  default = "apac.amazon.nova-lite-v1:0"
}

variable "generation_timeout_seconds" {
  description = "Primary model allowance within the shared 20-second answer budget."
  type        = number
  default     = 14
  validation {
    condition     = var.generation_timeout_seconds > 0 && var.generation_timeout_seconds < 20
    error_message = "Leave time within the shared answer budget for retrieval and fallback."
  }
}

variable "embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "safety_model_id" {
  type    = string
  default = "apac.amazon.nova-lite-v1:0"
}

# --- Backend ---
variable "bedrock_api_key_ssm_parameter" {
  description = "Pre-existing SecureString parameter name; Terraform never reads its value."
  type        = string
  default     = ""
}

variable "retrieval_min_similarity" {
  description = "Similarity floor calibrated against the Titan evaluation."
  type        = number
  default     = 0.40
  validation {
    condition     = var.retrieval_min_similarity >= 0 && var.retrieval_min_similarity <= 1
    error_message = "retrieval_min_similarity must be between 0 and 1."
  }
}

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
