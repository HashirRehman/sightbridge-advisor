resource "random_password" "db_master" {
  length  = 40
  special = false # avoids URL-encoding issues in the connection string
}

resource "aws_db_parameter_group" "postgres" {
  name   = "${local.name}-pg${var.db_engine_version}"
  family = "postgres${var.db_engine_version}"

  parameter {
    name         = "rds.force_ssl"
    value        = "1" # reject any non-TLS connection
    apply_method = "pending-reboot"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "PostgreSQL access (TLS enforced by parameter group)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "PostgreSQL"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.db_ingress_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "default" {
  name       = "${local.name}-db"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_db_instance" "postgres" {
  identifier     = "${local.name}-db"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  # Free-tier shaped: single AZ, 20 GB gp2, 1 day of automated backups.
  allocated_storage       = 20
  max_allocated_storage   = 0
  storage_type            = "gp2"
  storage_encrypted       = true
  multi_az                = false
  backup_retention_period = 1

  db_name  = "sightbridge"
  username = "sightbridge_admin"
  password = random_password.db_master.result

  db_subnet_group_name   = aws_db_subnet_group.default.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.postgres.name
  publicly_accessible    = true

  auto_minor_version_upgrade = true
  deletion_protection        = false
  skip_final_snapshot        = true
  apply_immediately          = true
}

# Admin URL: used only by the operator for migrations, ingestion and creating the app role.
resource "aws_ssm_parameter" "db_admin_url" {
  name  = "${local.ssm}/database-admin-url"
  type  = "SecureString"
  value = "postgresql+psycopg://${aws_db_instance.postgres.username}:${random_password.db_master.result}@${aws_db_instance.postgres.address}:5432/${aws_db_instance.postgres.db_name}?sslmode=require"
}

# App URL: least-privilege role created by backend/scripts/create_app_role.py.
resource "random_password" "db_app" {
  length  = 40
  special = false
}

resource "aws_ssm_parameter" "db_app_password" {
  name  = "${local.ssm}/database-app-password"
  type  = "SecureString"
  value = random_password.db_app.result
}

resource "aws_ssm_parameter" "db_app_url" {
  name  = "${local.ssm}/database-url"
  type  = "SecureString"
  value = "postgresql+psycopg://sightbridge_app:${random_password.db_app.result}@${aws_db_instance.postgres.address}:5432/${aws_db_instance.postgres.db_name}?sslmode=require"
}
