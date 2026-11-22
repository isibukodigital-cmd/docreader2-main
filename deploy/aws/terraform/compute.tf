locals {
  ecr_repository_name = "${var.project_name}-app"
  app_image           = "${aws_ecr_repository.app.repository_url}:${var.app_image_tag}"
  tls_enabled         = var.app_domain_name != ""
  public_subnet_ids   = [for subnet in aws_subnet.public : subnet.id]
  private_subnet_ids  = [for subnet in aws_subnet.private : subnet.id]
}

resource "aws_ecr_repository" "app" {
  name                 = local.ecr_repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the latest ten tagged application images."
      selection = {
        tagStatus     = "tagged"
        tagPrefixList = ["release-"]
        countType     = "imageCountMoreThan"
        countNumber   = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_lb" "app" {
  name                       = "${var.project_name}-alb"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = local.public_subnet_ids
  enable_deletion_protection = var.alb_deletion_protection
}

resource "aws_lb_target_group" "app" {
  name        = "${var.project_name}-app"
  port        = 8000
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = aws_vpc.main.id

  health_check {
    enabled             = true
    path                = "/health/ready"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_acm_certificate" "app" {
  count = local.tls_enabled ? 1 : 0

  domain_name       = var.app_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true

    precondition {
      condition     = var.route53_zone_id != ""
      error_message = "route53_zone_id is required when app_domain_name is set."
    }
  }
}

resource "aws_route53_record" "certificate_validation" {
  for_each = local.tls_enabled ? toset([var.app_domain_name]) : toset([])

  zone_id         = var.route53_zone_id
  name            = one(aws_acm_certificate.app[0].domain_validation_options).resource_record_name
  type            = one(aws_acm_certificate.app[0].domain_validation_options).resource_record_type
  records         = [one(aws_acm_certificate.app[0].domain_validation_options).resource_record_value]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "app" {
  count = local.tls_enabled ? 1 : 0

  certificate_arn         = aws_acm_certificate.app[0].arn
  validation_record_fqdns = [for record in aws_route53_record.certificate_validation : record.fqdn]
}

resource "aws_lb_listener" "http_redirect" {
  count = local.tls_enabled ? 1 : 0

  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "http_test" {
  count = local.tls_enabled ? 0 : 1

  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_lb_listener" "https" {
  count = local.tls_enabled ? 1 : 0

  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.app[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_lb_listener_rule" "hide_metrics_http" {
  count = local.tls_enabled ? 0 : 1

  listener_arn = aws_lb_listener.http_test[0].arn
  priority     = 1

  action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not Found"
      status_code  = "404"
    }
  }

  condition {
    path_pattern {
      values = ["/metrics"]
    }
  }
}

resource "aws_lb_listener_rule" "hide_metrics_https" {
  count = local.tls_enabled ? 1 : 0

  listener_arn = aws_lb_listener.https[0].arn
  priority     = 1

  action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not Found"
      status_code  = "404"
    }
  }

  condition {
    path_pattern {
      values = ["/metrics"]
    }
  }
}

resource "aws_route53_record" "app" {
  count = local.tls_enabled ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.app_domain_name
  type    = "A"

  alias {
    name                   = aws_lb.app.dns_name
    zone_id                = aws_lb.app.zone_id
    evaluate_target_health = true
  }
}

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.project_name}-app"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app_runtime" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.postgres.master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "app_runtime" {
  name   = "${var.project_name}-runtime"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_runtime.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.project_name}-app"
  role = aws_iam_role.app.name
}

resource "aws_launch_template" "app" {
  name_prefix   = "${var.project_name}-app-"
  image_id      = data.aws_ssm_parameter.amazon_linux_2023.value
  instance_type = var.instance_type
  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    app_image                 = local.app_image
    aws_region                = var.aws_region
    cognito_client_id         = aws_cognito_user_pool_client.web.id
    cognito_domain            = local.cognito_hosted_ui_url
    cognito_region            = var.aws_region
    cognito_user_pool_id      = aws_cognito_user_pool.main.id
    db_endpoint               = aws_db_instance.postgres.address
    db_name                   = aws_db_instance.postgres.db_name
    db_secret_arn             = aws_db_instance.postgres.master_user_secret[0].secret_arn
    ecr_repository_url        = aws_ecr_repository.app.repository_url
    microsoft_graph_client_id = var.microsoft_graph_client_id
  }))

  iam_instance_profile {
    name = aws_iam_instance_profile.app.name
  }

  vpc_security_group_ids = [aws_security_group.app.id]

  metadata_options {
    http_endpoint          = "enabled"
    http_tokens            = "required"
    instance_metadata_tags = "disabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.project_name}-app"
    }
  }

  update_default_version = true
}

resource "aws_autoscaling_group" "app" {
  name                = "${var.project_name}-app"
  min_size            = 0
  max_size            = var.app_max_capacity
  desired_capacity    = var.app_desired_capacity
  vpc_zone_identifier = local.private_subnet_ids
  target_group_arns   = [aws_lb_target_group.app.arn]
  health_check_type   = "EC2"

  launch_template {
    id      = aws_launch_template.app.id
    version = aws_launch_template.app.latest_version
  }

  instance_refresh {
    strategy = "Rolling"

    preferences {
      min_healthy_percentage = 50
      instance_warmup        = 600
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.project_name}-app"
    propagate_at_launch = true
  }

  lifecycle {
    precondition {
      condition     = var.app_desired_capacity <= var.app_max_capacity
      error_message = "app_desired_capacity cannot exceed app_max_capacity."
    }

    precondition {
      condition     = var.app_desired_capacity == 0 || local.tls_enabled
      error_message = "Configure app_domain_name and HTTPS before starting the application instances."
    }
  }
}
