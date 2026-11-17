variable "aws_region" {
  description = "Região. us-east-1 é a mais barata; sa-east-1 custa cerca do dobro (README da pipeline)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  type    = string
  default = "milton-pi"
}

variable "my_ip_cidr" {
  description = "Seu IP público com /32 (veja em checkip.amazonaws.com). Libera a porta 5432 do RDS para você."
  type        = string

  validation {
    condition     = can(cidrhost(var.my_ip_cidr, 0))
    error_message = "Use o formato 200.100.50.25/32."
  }
}

variable "alert_email" {
  description = "E-mail que recebe os alertas de orçamento."
  type        = string
}

# ---------- RDS ----------
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "GB iniciais (o banco tem ~4 GB; 20 é o mínimo do gp3)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "Teto do autoscaling de disco."
  type        = number
  default     = 50
}

variable "db_publicly_accessible" {
  description = "true = você acessa de casa (db_setup.sh, DBeaver). Custa o IPv4 público (~US$ 3,65/mês). false = só de dentro da VPC."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "true em produção. Em teste deixe false para o terraform destroy funcionar."
  type        = bool
  default     = false
}

variable "skip_final_snapshot" {
  description = "true em teste. Em produção use false (gera snapshot ao destruir)."
  type        = bool
  default     = true
}

# ---------- Pipeline (ECS Fargate) ----------
variable "task_cpu" {
  description = "Unidades de CPU do Fargate (1024 = 1 vCPU)."
  type        = number
  default     = 2048
}

variable "task_memory" {
  description = "MB de memória do Fargate (2 vCPU aceita 4096 a 16384)."
  type        = number
  default     = 4096
}

variable "task_architecture" {
  description = "X86_64 (build simples em qualquer PC) ou ARM64 (~20% mais barato; exige build arm64)."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.task_architecture)
    error_message = "Use X86_64 ou ARM64."
  }
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "pipeline_chunk_size" {
  type    = string
  default = "50000"
}

variable "pipeline_batch_size" {
  type    = string
  default = "10000"
}

variable "schedule_enabled" {
  type    = bool
  default = true
}

variable "schedule_expression" {
  description = <<-EOT
    Agendamento do EventBridge Scheduler (UTC).
    Semanal (domingo 03:00 UTC): cron(0 3 ? * SUN *)   <- padrão, cabe no orçamento com folga
    Diário (meia-noite Brasília): cron(0 3 * * ? *)    <- RNF03 do projeto, custa ~US$ 8 a mais até 31/12
  EOT
  type        = string
  default     = "cron(0 3 ? * SUN *)"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

# ---------- Orçamento ----------
variable "monthly_budget_usd" {
  description = "Limite mensal. Novembro (22 dias) + dezembro ~ 2 x 25 = 50."
  type        = number
  default     = 25
}

# ---------- Liga/desliga do RDS (lambda.tf) ----------
variable "db_start_expression" {
  description = <<-EOT
    Quando ligar o RDS (UTC). Deve ser pelo menos ~20 min ANTES de schedule_expression:
    o RDS leva de 5 a 10 min para ficar disponível. Padrão: domingo 02:30 (ETL às 03:00).
    Se usar carga diária, troque por cron(30 2 * * ? *).
  EOT
  type        = string
  default     = "cron(30 2 ? * SUN *)"
}

variable "db_safety_stop_expression" {
  description = "Rede de segurança: desliga o RDS se ele estiver ligado sem motivo (UTC). Padrão: todo dia 08:00 UTC (05:00 Brasília)."
  type        = string
  default     = "cron(0 8 * * ? *)"
}
