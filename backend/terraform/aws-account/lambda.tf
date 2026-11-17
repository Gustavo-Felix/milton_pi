# ---------------------------------------------------------------------------
# Economia: RDS ligado só na janela da carga.
#   Scheduler (db_start_expression)  -> Lambda start        -> RDS liga
#   Scheduler (schedule_expression)  -> ECS RunTask (ETL)   (já existe em ecs.tf)
#   ECS Task State Change = STOPPED  -> Lambda stop_if_idle -> RDS desliga
#   Scheduler diário (rede de segurança) -> Lambda stop_if_idle
# ---------------------------------------------------------------------------

data "archive_file" "rds_power" {
  type        = "zip"
  source_file = "${path.module}/lambda/rds_power.py"
  output_path = "${path.module}/.build/rds_power.zip"
}

resource "aws_cloudwatch_log_group" "rds_power" {
  name              = "/aws/lambda/${var.project}-rds-power"
  retention_in_days = 7
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rds_power" {
  name               = "${var.project}-rds-power"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "rds_power" {
  name = "ligar-desligar-rds"
  role = aws_iam_role.rds_power.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:StartDBInstance",
          "rds:StopDBInstance",
          "rds:ListTagsForResource",
        ]
        Resource = aws_db_instance.this.arn
      },
      {
        Effect    = "Allow"
        Action    = ["ecs:ListTasks"]
        Resource  = "*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.rds_power.arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "rds_power" {
  function_name    = "${var.project}-rds-power"
  role             = aws_iam_role.rds_power.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "rds_power.handler"
  filename         = data.archive_file.rds_power.output_path
  source_code_hash = data.archive_file.rds_power.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      DB_INSTANCE_ID  = aws_db_instance.this.identifier
      ECS_CLUSTER_ARN = aws_ecs_cluster.this.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.rds_power, aws_iam_role_policy.rds_power]
}

# ---------- 1) Ligar o RDS antes da carga ----------
resource "aws_iam_role_policy" "scheduler_invoke_lambda" {
  name = "invocar-lambda-rds-power"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.rds_power.arn
    }]
  })
}

resource "aws_scheduler_schedule" "db_start" {
  name                = "${var.project}-db-start"
  description         = "Liga o RDS antes da carga"
  schedule_expression = var.db_start_expression
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.rds_power.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = "start" })
  }
}

# ---------- 2) Desligar quando a tarefa ECS terminar (sucesso ou falha) ----------
resource "aws_cloudwatch_event_rule" "etl_stopped" {
  name        = "${var.project}-etl-stopped"
  description = "Tarefa do cluster terminou -> tenta desligar o RDS"
  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.this.arn]
      lastStatus = ["STOPPED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "etl_stopped" {
  rule  = aws_cloudwatch_event_rule.etl_stopped.name
  arn   = aws_lambda_function.rds_power.arn
  input = jsonencode({ action = "stop_if_idle" })
}

resource "aws_lambda_permission" "from_eventbridge" {
  statement_id  = "AllowEventBridgeEtlStopped"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rds_power.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.etl_stopped.arn
}

# ---------- 3) Rede de segurança diária (RDS parado religa sozinho após 7 dias) ----------
resource "aws_scheduler_schedule" "db_safety_stop" {
  name                = "${var.project}-db-safety-stop"
  description         = "Desliga o RDS se ele tiver religado sozinho"
  schedule_expression = var.db_safety_stop_expression
  state               = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.rds_power.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = "stop_if_idle" })
  }
}
