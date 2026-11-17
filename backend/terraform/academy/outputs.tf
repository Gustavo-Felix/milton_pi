output "rds_endpoint" {
  value = aws_db_instance.this.address
}

output "ec2_instance_id" {
  value = aws_instance.pipeline.id
}

output "database_url" {
  description = "terraform output -raw database_url  (contém a senha; use com sslmode=require)"
  value       = "postgresql://${aws_db_instance.this.username}:${random_password.db.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}"
  sensitive   = true
}
