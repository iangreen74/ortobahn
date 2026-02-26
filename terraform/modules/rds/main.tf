resource "aws_db_subnet_group" "main" {
  name       = "ortobahn-db-v2"
  subnet_ids = var.subnet_ids

  tags = { Name = "ortobahn-db-v2" }
}

resource "aws_db_instance" "main" {
  identifier     = "ortobahn-pg-v2"
  engine         = "postgres"
  engine_version = "16.10"

  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage
  storage_type      = "gp3"

  db_name  = "ortobahn"
  username = "ortobahn_app"
  password = var.db_master_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.security_group_id]

  multi_az            = false
  publicly_accessible = false
  skip_final_snapshot = true

  backup_retention_period = 7
  backup_window           = "09:00-09:30"
  maintenance_window      = "mon:10:00-mon:10:30"

  tags = { Name = "ortobahn-pg-v2" }

  lifecycle {
    ignore_changes = [password, engine_version]
  }
}
