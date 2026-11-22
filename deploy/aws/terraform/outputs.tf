output "application_url" {
  description = "Application URL. HTTPS is enabled only when app_domain_name is configured."
  value       = local.tls_enabled ? "https://${var.app_domain_name}" : "http://${aws_lb.app.dns_name}"
}

output "application_load_balancer_dns_name" {
  description = "Public ALB hostname."
  value       = aws_lb.app.dns_name
}

output "ecr_repository_url" {
  description = "Push the built DocReader container image to this ECR repository."
  value       = aws_ecr_repository.app.repository_url
}

output "rds_endpoint" {
  description = "Private PostgreSQL endpoint; reachable only from the VPC."
  value       = aws_db_instance.postgres.address
}

output "rds_master_secret_arn" {
  description = "Secrets Manager ARN containing the RDS-managed master credentials."
  value       = aws_db_instance.postgres.master_user_secret[0].secret_arn
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID for DOCQA_COGNITO_USER_POOL_ID."
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Public Cognito app client ID for DOCQA_COGNITO_CLIENT_ID."
  value       = aws_cognito_user_pool_client.web.id
}

output "cognito_domain" {
  description = "Cognito Hosted UI domain for DOCQA_COGNITO_DOMAIN."
  value       = local.cognito_hosted_ui_url
}

output "cognito_callback_urls" {
  description = "URLs configured on the Cognito app client."
  value       = local.auth_callback_urls
}
