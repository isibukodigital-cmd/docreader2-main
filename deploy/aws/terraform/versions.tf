terraform {
  required_version = ">= 1.10.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Application = var.project_name
      ManagedBy   = "Terraform"
    }
  }
}

resource "random_id" "suffix" {
  byte_length = 3
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_rds_engine_version" "postgres" {
  engine  = "postgres"
  version = var.postgres_major_version
  latest  = true
}

data "aws_ssm_parameter" "amazon_linux_2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
