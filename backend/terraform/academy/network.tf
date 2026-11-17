data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "ec2" {
  name        = "${var.project}-ec2"
  description = "EC2 da pipeline (somente saida; acesso por SSM Session Manager)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "Saida livre (ANS, IBGE, Docker Hub, S3, RDS)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "Postgres do Milton PI"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_ec2" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "EC2 da pipeline"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_my_ip" {
  security_group_id = aws_security_group.db.id
  cidr_ipv4         = var.my_ip_cidr
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "Meu IP"
}
