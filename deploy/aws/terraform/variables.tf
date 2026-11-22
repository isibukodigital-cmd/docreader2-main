variable "aws_region" {
  description = "AWS region for the deployment."
  type        = string
  default     = "eu-north-1"
}

variable "project_name" {
  description = "Lowercase resource-name prefix."
  type        = string
  default     = "docreader"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "project_name must be 3-20 lowercase letters, numbers, or hyphens and start with a letter."
  }
}

variable "vpc_cidr" {
  description = "IPv4 range for the new VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "app_domain_name" {
  description = "Optional DNS name for the application; requires a Route 53 hosted-zone ID for managed HTTPS."
  type        = string
  default     = ""

  validation {
    condition     = var.app_domain_name == "" || can(regex("^[A-Za-z0-9.-]+$", var.app_domain_name))
    error_message = "app_domain_name must be a DNS hostname without a scheme, path, or trailing slash."
  }
}

variable "route53_zone_id" {
  description = "Hosted-zone ID for app_domain_name. Leave empty when no domain is available."
  type        = string
  default     = ""
}

variable "cognito_domain_prefix" {
  description = "Optional Cognito Hosted UI prefix. A random suffix is appended for regional uniqueness."
  type        = string
  default     = "docreader"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,48}$", var.cognito_domain_prefix))
    error_message = "cognito_domain_prefix must contain only lowercase letters, numbers, and hyphens."
  }
}

variable "google_oauth_client_id" {
  description = "Google OAuth client ID for Cognito federation; set together with its secret."
  type        = string
  default     = ""
}

variable "google_oauth_client_secret" {
  description = "Google OAuth client secret. Terraform state must be stored securely."
  type        = string
  sensitive   = true
  default     = ""
}

variable "microsoft_oauth_client_id" {
  description = "Microsoft Entra OIDC client ID for Cognito federation; set together with its secret."
  type        = string
  default     = ""
}

variable "microsoft_oauth_client_secret" {
  description = "Microsoft Entra OIDC client secret. Terraform state must be stored securely."
  type        = string
  sensitive   = true
  default     = ""
}

variable "microsoft_graph_client_id" {
  description = "Client ID of the separate Entra SPA application used to obtain Graph tokens."
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "EC2 instance type. The app uses CPU embeddings and needs more than 1 GiB RAM."
  type        = string
  default     = "t3.small"
}

variable "app_image_tag" {
  description = "Immutable ECR image tag to run on EC2."
  type        = string
  default     = "bootstrap"
}

variable "app_desired_capacity" {
  description = "Start at zero until an image has been pushed and the database schema installed."
  type        = number
  default     = 0

  validation {
    condition     = var.app_desired_capacity >= 0
    error_message = "app_desired_capacity must be zero or greater."
  }
}

variable "app_max_capacity" {
  description = "Maximum number of EC2 instances in the private application Auto Scaling group."
  type        = number
  default     = 2

  validation {
    condition     = var.app_max_capacity >= 1
    error_message = "app_max_capacity must be at least one."
  }
}

variable "postgres_major_version" {
  description = "PostgreSQL major version selected from the latest available minor release."
  type        = string
  default     = "16"
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class."
  type        = string
  default     = "db.t4g.small"
}

variable "db_multi_az" {
  description = "Enable RDS Multi-AZ for database availability; incurs additional charges."
  type        = bool
  default     = false
}

variable "db_deletion_protection" {
  description = "Protect the database from accidental Terraform deletion."
  type        = bool
  default     = true
}

variable "cognito_deletion_protection" {
  description = "Protect the user pool from accidental deletion."
  type        = bool
  default     = true
}

variable "db_backup_retention_days" {
  description = "RDS automated backup retention period in days."
  type        = number
  default     = 7
}

variable "db_allocated_storage_gib" {
  description = "Initial encrypted gp3 RDS storage size in GiB."
  type        = number
  default     = 20
}

variable "alb_deletion_protection" {
  description = "Protect the application load balancer from accidental deletion."
  type        = bool
  default     = false
}
