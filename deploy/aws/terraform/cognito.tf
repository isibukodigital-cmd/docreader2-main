locals {
  google_idp_enabled     = var.google_oauth_client_id != "" && var.google_oauth_client_secret != ""
  microsoft_idp_enabled  = var.microsoft_oauth_client_id != "" && var.microsoft_oauth_client_secret != ""
  cognito_domain_name    = "${var.cognito_domain_prefix}-${random_id.suffix.hex}"
  cognito_hosted_ui_url  = "https://${local.cognito_domain_name}.auth.${var.aws_region}.amazoncognito.com"
  auth_callback_urls     = var.app_domain_name != "" ? ["https://${var.app_domain_name}/"] : ["http://localhost:8000/"]
  supported_cognito_idps = concat(["COGNITO"], local.google_idp_enabled ? ["Google"] : [], local.microsoft_idp_enabled ? ["Microsoft"] : [])
}

resource "aws_cognito_user_pool" "main" {
  name                = "${var.project_name}-users"
  deletion_protection = var.cognito_deletion_protection ? "ACTIVE" : "INACTIVE"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  schema {
    attribute_data_type      = "String"
    name                     = "tenant_id"
    developer_only_attribute = false
    mutable                  = true
    required                 = false

    string_attribute_constraints {
      min_length = "1"
      max_length = "64"
    }
  }

  schema {
    attribute_data_type      = "String"
    name                     = "microsoft_user_id"
    developer_only_attribute = false
    mutable                  = true
    required                 = false

    string_attribute_constraints {
      min_length = "1"
      max_length = "64"
    }
  }
}

resource "aws_cognito_identity_provider" "google" {
  count = local.google_idp_enabled ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    authorize_scopes = "openid email profile"
    client_id        = var.google_oauth_client_id
    client_secret    = var.google_oauth_client_secret
  }

  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    name           = "name"
    username       = "sub"
  }
}

resource "aws_cognito_identity_provider" "microsoft" {
  count = local.microsoft_idp_enabled ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Microsoft"
  provider_type = "OIDC"

  provider_details = {
    client_id                     = var.microsoft_oauth_client_id
    client_secret                 = var.microsoft_oauth_client_secret
    authorize_scopes              = "openid email profile"
    oidc_issuer                   = "https://login.microsoftonline.com/common/v2.0"
    authorize_url                 = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    token_url                     = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    attributes_url                = "https://graph.microsoft.com/oidc/userinfo"
    attributes_request_method     = "GET"
    jwks_uri                      = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
    attributes_url_add_attributes = "false"
  }

  attribute_mapping = {
    email                      = "email"
    email_verified             = "email_verified"
    name                       = "name"
    username                   = "sub"
    "custom:tenant_id"         = "tid"
    "custom:microsoft_user_id" = "oid"
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = local.cognito_domain_name
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.project_name}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret                      = false
  enable_token_revocation              = true
  prevent_user_existence_errors        = "ENABLED"
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = local.supported_cognito_idps
  callback_urls                        = local.auth_callback_urls
  logout_urls                          = local.auth_callback_urls
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 10
  }

  depends_on = [
    aws_cognito_identity_provider.google,
    aws_cognito_identity_provider.microsoft,
  ]

  lifecycle {
    precondition {
      condition = (
        (var.google_oauth_client_id == "") == (var.google_oauth_client_secret == "") &&
        (var.microsoft_oauth_client_id == "") == (var.microsoft_oauth_client_secret == "")
      )
      error_message = "Set both client ID and client secret, or neither, for each Cognito identity provider."
    }

    precondition {
      condition = (
        local.google_idp_enabled &&
        local.microsoft_idp_enabled &&
        var.microsoft_graph_client_id != ""
      )
      error_message = "Google, Microsoft OIDC, and Microsoft Graph client IDs must be configured for the supported sign-in options."
    }
  }
}
