output "rds_endpoint" {
  value = aws_db_instance.this.address
}

output "ecr_repository_url" {
  value = aws_ecr_repository.pipeline.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.pipeline.family
}

output "pipeline_subnet_ids" {
  value = data.aws_subnets.default.ids
}

output "pipeline_security_group_id" {
  value = aws_security_group.pipeline.id
}

output "database_url" {
  description = "terraform output -raw database_url  (contém a senha)"
  value       = aws_ssm_parameter.database_url.value
  sensitive   = true
}

output "log_group" {
  value = aws_cloudwatch_log_group.pipeline.name
}
