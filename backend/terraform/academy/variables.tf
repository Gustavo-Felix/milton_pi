variable "aws_region" {
  description = "A sandbox só permite us-east-1 e us-west-2."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = contains(["us-east-1", "us-west-2"], var.aws_region)
    error_message = "Use us-east-1 ou us-west-2."
  }
}

variable "project" {
  type    = string
  default = "milton-pi"
}

variable "my_ip_cidr" {
  description = "Seu IP público com /32 (checkip.amazonaws.com). Libera o RDS para DBeaver/psql de casa."
  type        = string

  validation {
    condition     = can(cidrhost(var.my_ip_cidr, 0))
    error_message = "Use o formato 200.100.50.25/32."
  }
}

variable "pipeline_dir" {
  description = "Pasta da pipeline, relativa a este diretório (backend/terraform/academy -> backend/pipeline)."
  type        = string
  default     = "../../pipeline"
}

# ---------- RDS ----------
variable "db_instance_class" {
  description = "A sandbox aceita nano/micro/small com capacidade de intermitência. Se db.t4g.micro for recusada, use db.t3.micro."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_encrypted" {
  description = "KMS não consta na lista de serviços da sandbox; por isso o padrão é false. Dados da ANS/IBGE são públicos."
  type        = bool
  default     = false
}

# ---------- EC2 que roda a pipeline ----------
variable "instance_type" {
  description = "A sandbox só aceita t2/t3 nano, micro e small."
  type        = string
  default     = "t3.small"
}

variable "ebs_size_gb" {
  description = "Máximo da sandbox: 35 GB."
  type        = number
  default     = 30

  validation {
    condition     = var.ebs_size_gb <= 35
    error_message = "A sandbox limita volumes EBS a 35 GB."
  }
}

variable "instance_profile_name" {
  description = "Perfil de instância pré-criado da sandbox."
  type        = string
  default     = "LabInstanceProfile"
}

variable "etl_args" {
  description = "Argumentos do orchestrator. Vazio = competência atual e todos os datasets. Ex.: \"2026 11 geografia operadora_ativa\" (teste rápido)."
  type        = string
  default     = ""
}

variable "run_etl_on_boot" {
  description = "true = a pipeline roda assim que a instância liga (primeiro boot e todo start-instances)."
  type        = bool
  default     = true
}

variable "stop_after_etl" {
  description = "true = a instância desliga sozinha quando a carga termina com sucesso (substitui a Lambda de liga/desliga)."
  type        = bool
  default     = true
}

variable "pipeline_chunk_size" {
  description = "Linhas por chunk. t3.small tem 2 GB de RAM: mantenha baixo."
  type        = string
  default     = "20000"
}

variable "pipeline_batch_size" {
  type    = string
  default = "10000"
}
