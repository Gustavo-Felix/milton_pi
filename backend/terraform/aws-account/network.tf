# Usa a VPC padrão da conta (sem NAT Gateway, que custaria ~US$ 32/mês).
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Security group da tarefa Fargate: só saída (baixar ANS/IBGE e falar com o RDS).
resource "aws_security_group" "pipeline" {
  name        = "${var.project}-pipeline"
  description = "Tarefa Fargate da pipeline ETL"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "Saida livre (ANS, IBGE, ECR, RDS)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Security group do banco.
resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "Postgres do Milton PI"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_pipeline" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.pipeline.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "Pipeline Fargate"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_my_ip" {
  count             = var.db_publicly_accessible ? 1 : 0
  security_group_id = aws_security_group.db.id
  cidr_ipv4         = var.my_ip_cidr
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "Meu IP (carga inicial / DBeaver)"
}
