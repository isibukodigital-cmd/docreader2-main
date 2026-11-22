variable "aws_region" {
  description = "AWS region for the remote Terraform state bucket."
  type        = string
  default     = "eu-north-1"
}

variable "state_bucket_prefix" {
  description = "Globally unique prefix for the versioned Terraform state bucket."
  type        = string
  default     = "docreader-tfstate"
}
