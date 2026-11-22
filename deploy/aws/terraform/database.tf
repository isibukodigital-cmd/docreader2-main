resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db"
  subnet_ids = [for subnet in aws_subnet.private : subnet.id]
}

resource "aws_db_instance" "postgres" {
  identifier                  = "${var.project_name}-postgres"
  engine                      = data.aws_rds_engine_version.postgres.engine
  engine_version              = data.aws_rds_engine_version.postgres.version
  instance_class              = var.db_instance_class
  db_name                     = "docqa"
  username                    = "docqa_admin"
  manage_master_user_password = true
  port                        = 5432

  allocated_storage     = var.db_allocated_storage_gib
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period = var.db_backup_retention_days
  copy_tags_to_snapshot   = true
  deletion_protection     = var.db_deletion_protection
  skip_final_snapshot     = false
  final_snapshot_identifier = format(
    "%s-final-%s",
    var.project_name,
    random_id.suffix.hex,
  )

  auto_minor_version_upgrade = true
  apply_immediately          = false
}
