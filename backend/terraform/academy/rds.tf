resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = var.project
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_db_instance" "this" {
  identifier = var.project

  engine         = "postgres"
  engine_version = "17"
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp2" # a sandbox só aceita gp2 no RDS
  storage_encrypted = var.db_encrypted

  db_name  = "milton_pi"
  username = "milton"
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = true
  multi_az               = false

  # Ambiente efêmero: sem backup automático (criação mais rápida) e sem snapshot final.
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false

  monitoring_interval          = 0 # monitoramento aprimorado não é suportado
  performance_insights_enabled = false
}
