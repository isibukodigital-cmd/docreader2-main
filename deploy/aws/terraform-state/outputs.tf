output "state_bucket_name" {
  description = "Use this bucket for the application Terraform S3 backend."
  value       = aws_s3_bucket.terraform_state.bucket
}
